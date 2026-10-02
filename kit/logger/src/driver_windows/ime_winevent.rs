//! Out-of-context WinEvent hook for the input-method window events of the Windows driver.
//!
//! The operating system reports the display, change and dismissal of an input-method window as
//! `EVENT_OBJECT_IME_SHOW`, `EVENT_OBJECT_IME_CHANGE` and `EVENT_OBJECT_IME_HIDE`. The hook is
//! registered with `WINEVENT_OUTOFCONTEXT`: notifications are delivered to the registering thread
//! through its message queue, no code is injected into any other process, and the callback arguments
//! carry no text, so the observation is content-free by construction.
//!
//! The callback ignores the window and the process that raised an event, so the session summary is a
//! system-wide observation rather than one attributed to an application.
//!
//! A display event opens a conversion session and a dismissal closes it. The session state lives in
//! the shared atomics of the core: the start time, the dwell time and the ready flag written here are
//! read by the monitoring loop, and the navigation counter and abandonment flag are updated by the
//! keyboard hook. Candidate windows that are shown and hidden through window visibility raise these
//! events; windows that are cloaked by the compositor do not and are handled by a separate path.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};

use windows::Win32::Foundation::{HMODULE, HWND};
use windows::Win32::UI::Accessibility::{SetWinEventHook, UnhookWinEvent, HWINEVENTHOOK};
use windows::Win32::UI::WindowsAndMessaging::{
    EVENT_OBJECT_IME_CHANGE, EVENT_OBJECT_IME_HIDE, EVENT_OBJECT_IME_SHOW, WINEVENT_OUTOFCONTEXT,
    WINEVENT_SKIPOWNPROCESS,
};

use crate::input::hook::{
    IME_ACTIVE, IME_CANDIDATE_NAV_COUNT, IME_COMPOSITION_START_MS, IME_SHADOW_ABANDON,
    IME_SHADOW_DWELL_MS, IME_SHADOW_READY,
};

/// Wall-clock time in milliseconds of the most recent input-method window event, or 0 if none has
/// arrived. It records only when an event arrived, never a latch, so that a single stray event does not
/// silence a complementary path permanently.
pub(super) static LEGACY_IME_EVENT_MS: AtomicU64 = AtomicU64::new(0);

/// The cells a window event reads and writes. The production instance refers to the shared statics;
/// tests use local cells so that they do not race with tests of the copied state machine.
struct Session<'a> {
    legacy_event_ms: &'a AtomicU64,
    start_ms: &'a AtomicU64,
    nav: &'a AtomicU32,
    abandon: &'a AtomicBool,
    dwell_ms: &'a AtomicU64,
    ready: &'a AtomicBool,
    active: &'a AtomicBool,
}

impl Session<'static> {
    fn shared() -> Self {
        Self {
            legacy_event_ms: &LEGACY_IME_EVENT_MS,
            start_ms: &IME_COMPOSITION_START_MS,
            nav: &IME_CANDIDATE_NAV_COUNT,
            abandon: &IME_SHADOW_ABANDON,
            dwell_ms: &IME_SHADOW_DWELL_MS,
            ready: &IME_SHADOW_READY,
            active: &IME_ACTIVE,
        }
    }
}

/// Applies one window event to the session state.
///
/// A change event normally follows a display event, but some input methods raise a change event
/// without one; in that case the session is started by the change event.
///
/// The active flag is stored last with release ordering. A release store publishes only the writes that
/// precede it, so the reader that observes the flag with acquire ordering also observes the start time
/// and the reset counters.
fn apply_ime_event(event: u32, now_ms: u64, s: &Session<'_>) {
    s.legacy_event_ms.store(now_ms, Ordering::Release);
    match event {
        EVENT_OBJECT_IME_SHOW | EVENT_OBJECT_IME_CHANGE => {
            if event == EVENT_OBJECT_IME_SHOW || s.start_ms.load(Ordering::Relaxed) == 0 {
                s.start_ms.store(now_ms, Ordering::Relaxed);
                s.nav.store(0, Ordering::Relaxed);
                s.abandon.store(false, Ordering::Relaxed);
            }
            s.active.store(true, Ordering::Release);
        }
        EVENT_OBJECT_IME_HIDE => {
            let start = s.start_ms.load(Ordering::Relaxed);
            if start > 0 {
                s.dwell_ms
                    .store(now_ms.saturating_sub(start), Ordering::Relaxed);
                s.start_ms.store(0, Ordering::Relaxed);
                s.ready.store(true, Ordering::Release);
            }
            s.active.store(false, Ordering::Release);
        }
        _ => {}
    }
}

unsafe extern "system" fn ime_winevent_callback(
    _hook: HWINEVENTHOOK,
    event: u32,
    _hwnd: HWND,
    _id_object: i32,
    _id_child: i32,
    _id_event_thread: u32,
    _event_time: u32,
) {
    apply_ime_event(event, super::wall_ms(), &Session::shared());
}

/// Bounds of the registered event interval. A registration takes a single contiguous interval
/// `[min, max]`. With the correct values `EVENT_OBJECT_IME_SHOW < EVENT_OBJECT_IME_HIDE <
/// EVENT_OBJECT_IME_CHANGE`, so display is the minimum and change the maximum; swapping the bounds
/// yields an empty interval that delivers nothing.
const EVENT_MIN: u32 = EVENT_OBJECT_IME_SHOW;
const EVENT_MAX: u32 = EVENT_OBJECT_IME_CHANGE;
// Compile-time guard against an inverted, hence empty, interval.
const _: () = assert!(EVENT_MIN < EVENT_MAX);

/// Registers the hook on the calling thread, which must pump its message queue. The events of the
/// process's own windows are skipped.
pub(super) fn install() -> Result<HWINEVENTHOOK, String> {
    // SAFETY: `ime_winevent_callback` matches the `WINEVENTPROC` signature and stays valid for the
    // lifetime of the process; the hook is removed by `uninstall` on the same thread.
    let hook = unsafe {
        SetWinEventHook(
            EVENT_MIN,
            EVENT_MAX,
            HMODULE::default(),
            Some(ime_winevent_callback),
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
    };
    if hook.is_invalid() {
        return Err("SetWinEventHook (input-method window events) failed".to_string());
    }
    Ok(hook)
}

/// Removes the hook registered by [`install`]. Must run on the registering thread.
pub(super) fn uninstall(hook: HWINEVENTHOOK) {
    // SAFETY: `hook` was returned by `SetWinEventHook` on this thread and is removed exactly once.
    if !unsafe { UnhookWinEvent(hook) }.as_bool() {
        tracing::warn!("UnhookWinEvent (input-method window events) failed");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Cells {
        legacy: AtomicU64,
        start: AtomicU64,
        nav: AtomicU32,
        abandon: AtomicBool,
        dwell: AtomicU64,
        ready: AtomicBool,
        active: AtomicBool,
    }

    impl Cells {
        fn new() -> Self {
            Self {
                legacy: AtomicU64::new(0),
                start: AtomicU64::new(0),
                nav: AtomicU32::new(0),
                abandon: AtomicBool::new(false),
                dwell: AtomicU64::new(0),
                ready: AtomicBool::new(false),
                active: AtomicBool::new(false),
            }
        }

        fn session(&self) -> Session<'_> {
            Session {
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

    #[test]
    fn event_range_is_show_then_hide_then_change() {
        assert_eq!(EVENT_OBJECT_IME_SHOW, 0x8027);
        assert_eq!(EVENT_OBJECT_IME_HIDE, 0x8028);
        assert_eq!(EVENT_OBJECT_IME_CHANGE, 0x8029);
    }

    #[test]
    fn registered_interval_covers_all_three_events() {
        // The bounds are the values `install` passes to the registration.
        for event in [
            EVENT_OBJECT_IME_SHOW,
            EVENT_OBJECT_IME_HIDE,
            EVENT_OBJECT_IME_CHANGE,
        ] {
            assert!((EVENT_MIN..=EVENT_MAX).contains(&event), "{event:#x}");
        }
    }

    #[test]
    fn show_opens_a_session_and_resets_the_previous_summary() {
        let c = Cells::new();
        c.nav.store(4, Ordering::Relaxed);
        c.abandon.store(true, Ordering::Relaxed);
        apply_ime_event(EVENT_OBJECT_IME_SHOW, 1_000, &c.session());
        assert_eq!(c.start.load(Ordering::Relaxed), 1_000);
        assert_eq!(c.nav.load(Ordering::Relaxed), 0);
        assert!(!c.abandon.load(Ordering::Relaxed));
        assert!(c.active.load(Ordering::Acquire));
        assert_eq!(c.legacy.load(Ordering::Acquire), 1_000);
    }

    #[test]
    fn hide_closes_the_session_with_the_dwell_time() {
        let c = Cells::new();
        apply_ime_event(EVENT_OBJECT_IME_SHOW, 1_000, &c.session());
        apply_ime_event(EVENT_OBJECT_IME_HIDE, 2_500, &c.session());
        assert_eq!(c.dwell.load(Ordering::Relaxed), 1_500);
        assert_eq!(c.start.load(Ordering::Relaxed), 0);
        assert!(c.ready.load(Ordering::Acquire));
        assert!(!c.active.load(Ordering::Acquire));
    }

    #[test]
    fn hide_without_an_open_session_publishes_no_summary() {
        let c = Cells::new();
        c.active.store(true, Ordering::Release);
        apply_ime_event(EVENT_OBJECT_IME_HIDE, 2_500, &c.session());
        assert!(!c.ready.load(Ordering::Acquire));
        assert_eq!(c.dwell.load(Ordering::Relaxed), 0);
        assert!(!c.active.load(Ordering::Acquire));
    }

    #[test]
    fn change_without_a_display_event_starts_the_session() {
        let c = Cells::new();
        c.nav.store(3, Ordering::Relaxed);
        apply_ime_event(EVENT_OBJECT_IME_CHANGE, 700, &c.session());
        assert_eq!(c.start.load(Ordering::Relaxed), 700);
        assert_eq!(c.nav.load(Ordering::Relaxed), 0);
        assert!(c.active.load(Ordering::Acquire));
    }

    #[test]
    fn change_inside_a_session_keeps_the_start_time_and_the_counters() {
        let c = Cells::new();
        apply_ime_event(EVENT_OBJECT_IME_SHOW, 1_000, &c.session());
        c.nav.store(2, Ordering::Relaxed);
        c.abandon.store(true, Ordering::Relaxed);
        apply_ime_event(EVENT_OBJECT_IME_CHANGE, 1_400, &c.session());
        assert_eq!(c.start.load(Ordering::Relaxed), 1_000);
        assert_eq!(c.nav.load(Ordering::Relaxed), 2);
        assert!(c.abandon.load(Ordering::Relaxed));
        assert!(c.active.load(Ordering::Acquire));
        assert_eq!(c.legacy.load(Ordering::Acquire), 1_400);
    }

    #[test]
    fn unrelated_events_only_stamp_the_arrival_time() {
        let c = Cells::new();
        apply_ime_event(0x8017, 900, &c.session());
        assert_eq!(c.legacy.load(Ordering::Acquire), 900);
        assert_eq!(c.start.load(Ordering::Relaxed), 0);
        assert!(!c.active.load(Ordering::Acquire));
        assert!(!c.ready.load(Ordering::Acquire));
    }
}
