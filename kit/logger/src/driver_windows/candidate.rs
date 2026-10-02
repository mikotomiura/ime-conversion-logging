//! Observation of whether a candidate window of the input method is open, handed to the core as the
//! `candidate_window_observer` of the driver.
//!
//! Three signals are combined, and the answer is their disjunction:
//!
//! 1. the shared active flag, which the two WinEvent paths of this driver drive (the input-method
//!    window events and the cloak events);
//! 2. a scan of the top-level windows for a visible window whose class names a candidate window;
//! 3. the class name of the element that has keyboard focus, read through UI Automation.
//!
//! The scan and the focus query are secondary. The class of the language bar is deliberately not
//! matched by the scan (see `is_ime_candidate_class_name`): that window is visible whenever a Japanese
//! input method is loaded, and matching it would report a conversion at all times.
//!
//! The active flag can be left raised if the notification that lowers it is missed. When the
//! input-method window events are driving the session state, and the flag is raised while neither
//! secondary signal confirms a candidate window, the flag is stale, and it is lowered here. When only
//! the cloak path is driving, the secondary signals cannot confirm a candidate window that is
//! composited (the scan finds nothing and the focused element is not the candidate window), so the
//! test would always call the flag stale and no session would ever be reported; it is therefore not
//! applied there, and the resolver's own time limit and the timeout of the monitoring loop bound a
//! flag that stays raised.
//!
//! Only class names are read. The accessible name of the focused element is not queried, because it
//! is arbitrary text of another application (a tab title, the body of a document) and none of it may
//! enter this process.
//!
//! The closure runs on the thread of the monitoring loop. The UI Automation object is created on the
//! first call, on that thread, and only that thread calls the closure afterwards.

use std::sync::atomic::Ordering;

use windows::Win32::Foundation::{BOOL, HWND, LPARAM};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED,
};
use windows::Win32::UI::Accessibility::{CUIAutomation, IUIAutomation};
use windows::Win32::UI::WindowsAndMessaging::{EnumWindows, GetClassNameW, IsWindowVisible};

use super::cloak::{legacy_path_is_driving, Cells};

// @excerpt-begin is_stale_winevent_active
fn is_stale_winevent_active(
    winevent_active: bool,
    enumwindows_active: bool,
    uia_active: bool,
    legacy_path: bool,
) -> bool {
    legacy_path && winevent_active && !enumwindows_active && !uia_active
}

// @excerpt-end is_stale_winevent_active

// @excerpt-begin is_ime_candidate_class_name
fn is_ime_candidate_class_name(class: &str) -> bool {
    class.contains("CandidateUI") || class.contains("Candidate")
}

// @excerpt-end is_ime_candidate_class_name

/// Whether a visible top-level window has a class that names a candidate window.
fn is_candidate_window_visible() -> bool {
    // The callback records a match through `lparam` and stops the enumeration.
    unsafe extern "system" fn callback(hwnd: HWND, lparam: LPARAM) -> BOOL {
        // SAFETY: `hwnd` is supplied by the enumeration.
        if !unsafe { IsWindowVisible(hwnd) }.as_bool() {
            return BOOL(1);
        }
        let mut buf = [0u16; 256];
        // SAFETY: `buf` is a local array; the call writes at most its length.
        let len = unsafe { GetClassNameW(hwnd, &mut buf) };
        if len > 0 {
            let class = String::from_utf16_lossy(&buf[..len as usize]);
            if is_ime_candidate_class_name(&class) {
                // SAFETY: `lparam` is the address of the caller's `bool`, alive for the whole call.
                unsafe { *(lparam.0 as *mut bool) = true };
                return BOOL(0);
            }
        }
        BOOL(1)
    }

    let mut found = false;
    // SAFETY: the callback matches the `WNDENUMPROC` signature and `found` outlives the call. The
    // result is ignored: stopping the enumeration early is reported as an error by the call.
    let _ = unsafe { EnumWindows(Some(callback), LPARAM(&mut found as *mut bool as isize)) };
    found
}

/// The UI Automation client, created on first use.
struct FocusProbe {
    automation: Option<IUIAutomation>,
}

// SAFETY: a `FocusProbe` is constructed only inside the closure returned by `observer`, on the first
// call. The closure holds `None` until then, so no COM object exists before that call, and the core
// calls the closure from a single thread only (the calling-thread contract of the driver interface),
// so the object is never used from a second thread. The initialisation below asks for a
// multithreaded apartment, in which the object could be used from any thread; if the thread was
// already initialised with another apartment model (a single-threaded one), the object is bound to
// that thread, and the single-thread calling contract is what keeps its use sound.
unsafe impl Send for FocusProbe {}

impl FocusProbe {
    fn new() -> Self {
        // SAFETY: plain COM initialisation of the calling thread; a failure (for example a different
        // apartment model already set) is tolerated and the object creation below reports the outcome.
        // The calling-thread contract of the driver interface keeps the object on this thread.
        let automation = unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            CoCreateInstance::<_, IUIAutomation>(&CUIAutomation, None, CLSCTX_INPROC_SERVER).ok()
        };
        Self { automation }
    }

    /// Whether the focused element is a candidate window. "Candidate" is matched on the class name
    /// alone: a broader match such as "Ime" also matches the class names of ordinary web content, and
    /// the accessible name of the element is deliberately not read.
    fn focused_element_is_candidate(&self) -> bool {
        let Some(auto) = &self.automation else {
            return false;
        };
        // SAFETY: the interface pointers are used only on the thread that created them (see the
        // `Send` implementation above).
        unsafe {
            let Ok(element) = auto.GetFocusedElement() else {
                return false;
            };
            if let Ok(class) = element.CurrentClassName() {
                if class.to_string().contains("Candidate") {
                    return true;
                }
            }
        }
        false
    }
}

/// Combines the three signals. The two secondary probes are evaluated every time, in this order,
/// before the active flag is judged, because the staleness test needs both answers. The flag is
/// lowered only when the input-method window events are driving the session state.
fn observe(
    c: &Cells<'_>,
    now_ms: u64,
    scan: impl FnOnce() -> bool,
    focus: impl FnOnce() -> bool,
) -> bool {
    let winevent_active = c.active.load(Ordering::Acquire);
    let enumwindows_active = scan();
    let uia_active = focus();
    let legacy_path = legacy_path_is_driving(c.legacy_event_ms.load(Ordering::Acquire), now_ms);
    if is_stale_winevent_active(winevent_active, enumwindows_active, uia_active, legacy_path) {
        c.active.store(false, Ordering::Release);
        return false;
    }
    winevent_active || enumwindows_active || uia_active
}

/// The observation closure for `DriverHandle::candidate_window_observer`.
pub(super) fn observer() -> Box<dyn FnMut() -> bool + Send> {
    let mut probe: Option<FocusProbe> = None;
    Box::new(move || {
        let probe = probe.get_or_insert_with(FocusProbe::new);
        observe(
            &Cells::shared(),
            super::wall_ms(),
            is_candidate_window_visible,
            || probe.focused_element_is_candidate(),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64};

    struct Local {
        legacy: AtomicU64,
        start: AtomicU64,
        nav: AtomicU32,
        abandon: AtomicBool,
        dwell: AtomicU64,
        ready: AtomicBool,
        active: AtomicBool,
    }

    impl Local {
        fn new(active: bool, legacy_ms: u64) -> Self {
            Self {
                legacy: AtomicU64::new(legacy_ms),
                start: AtomicU64::new(0),
                nav: AtomicU32::new(0),
                abandon: AtomicBool::new(false),
                dwell: AtomicU64::new(0),
                ready: AtomicBool::new(false),
                active: AtomicBool::new(active),
            }
        }

        fn cells(&self) -> Cells<'_> {
            Cells {
                legacy_event_ms: &self.legacy,
                start_ms: &self.start,
                nav: &self.nav,
                abandon: &self.abandon,
                dwell_ms: &self.dwell,
                ready: &self.ready,
                active: &self.active,
            }
        }
    }

    const NOW: u64 = 1_000_000;
    /// A legacy timestamp recent enough for the input-method window events to count as driving.
    const RECENT: u64 = NOW - 1_000;

    #[test]
    fn without_the_legacy_path_the_result_is_the_disjunction_and_the_flag_is_never_lowered() {
        for bits in 0..8u8 {
            let (active, scan, focus) = (bits & 1 != 0, bits & 2 != 0, bits & 4 != 0);
            for legacy_ms in [0, NOW - 100_000] {
                let l = Local::new(active, legacy_ms);
                let got = observe(&l.cells(), NOW, || scan, || focus);
                assert_eq!(got, active || scan || focus, "bits {bits:03b}");
                assert_eq!(l.active.load(Ordering::Acquire), active, "bits {bits:03b}");
            }
        }
    }

    #[test]
    fn a_raised_flag_that_nothing_confirms_is_lowered_while_the_legacy_path_drives() {
        let l = Local::new(true, RECENT);
        assert!(!observe(&l.cells(), NOW, || false, || false));
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn either_secondary_signal_keeps_the_flag_and_the_answer() {
        for (scan, focus) in [(true, false), (false, true), (true, true)] {
            let l = Local::new(true, RECENT);
            assert!(observe(&l.cells(), NOW, || scan, || focus));
            assert!(l.active.load(Ordering::Acquire));
        }
    }

    #[test]
    fn a_lowered_flag_stays_lowered_and_the_secondary_signals_still_count() {
        let l = Local::new(false, RECENT);
        assert!(!observe(&l.cells(), NOW, || false, || false));
        assert!(observe(&l.cells(), NOW, || true, || false));
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn the_cloak_path_alone_is_never_judged_stale() {
        // No input-method window event has arrived: the flag is trusted although no secondary signal
        // confirms it, which is the normal state on a system that composites the candidate window.
        let l = Local::new(true, 0);
        assert!(observe(&l.cells(), NOW, || false, || false));
        assert!(l.active.load(Ordering::Acquire));
    }

    #[test]
    fn both_probes_run_in_order_and_before_the_flag_is_lowered() {
        let l = Local::new(true, RECENT);
        let log: RefCell<Vec<String>> = RefCell::new(Vec::new());
        let got = observe(
            &l.cells(),
            NOW,
            || {
                log.borrow_mut()
                    .push(format!("scan(active={})", l.active.load(Ordering::Acquire)));
                false
            },
            || {
                log.borrow_mut().push(format!(
                    "focus(active={})",
                    l.active.load(Ordering::Acquire)
                ));
                false
            },
        );
        assert!(!got);
        assert_eq!(
            *log.borrow(),
            vec!["scan(active=true)", "focus(active=true)"]
        );
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn the_flag_is_read_before_the_probes_run() {
        // The probe raises the flag; the answer must still reflect the value read before it ran.
        let l = Local::new(false, 0);
        let got = observe(
            &l.cells(),
            NOW,
            || {
                l.active.store(true, Ordering::Release);
                false
            },
            || false,
        );
        assert!(!got);
    }

    #[test]
    fn the_legacy_timestamp_is_read_after_the_probes_run() {
        // The probe records an input-method window event; the staleness test must see it.
        let l = Local::new(true, 0);
        let got = observe(
            &l.cells(),
            NOW,
            || {
                l.legacy.store(RECENT, Ordering::Release);
                false
            },
            || false,
        );
        assert!(!got);
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn the_class_of_the_language_bar_is_not_a_candidate_window() {
        assert!(is_ime_candidate_class_name("Microsoft.IME.CandidateWindow"));
        assert!(is_ime_candidate_class_name("CandidateUI_UIElement"));
        assert!(!is_ime_candidate_class_name("MSCTFIME UI"));
        assert!(!is_ime_candidate_class_name("IME"));
    }
}
