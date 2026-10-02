// winevent-callback: cloak_event_callback
//! Window cloak events of the Windows driver.
//!
//! Recent Windows versions show and hide the candidate window of the input method by cloaking and
//! uncloaking it in the desktop compositor rather than by showing and hiding it, so the events of
//! `driver_windows::ime_winevent` never arrive on such a system. This module registers the two
//! out-of-context WinEvent notifications for a window being cloaked and uncloaked, folds them into
//! conversion sessions with `CloakFold`, and writes the session state into the shared atomics.
//!
//! Work is split between two threads because the callback may not query the compositor. The callback
//! runs when the registering thread dispatches its message queue; it checks that the event concerns a
//! whole window, checks that the input-method window events are not already driving the session
//! state, and then makes a non-blocking send of the window handle to a resolver thread. The resolver
//! reads the window class, asks the compositor for the current cloak state and applies the fold. The
//! class name is used only to decide whether the window is a candidate window; it is never stored or
//! logged, and neither the window title nor the owning process is read.
//!
//! The resolver is the only writer of the session state on this path. When the input-method window
//! events have arrived recently (`legacy_path_is_driving`), the resolver leaves the atomics alone and
//! discards its fold. The check and the writes are not one atomic step: the class query and the
//! compositor query sit between them, so a show event of the other path can still arrive in that short
//! interval and the two paths can briefly write the same state. The timeout of the monitoring loop
//! bounds the effect of such an overlap.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::OnceLock;
use std::time::Duration;

use windows::Win32::Foundation::{HMODULE, HWND};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED};
use windows::Win32::UI::Accessibility::{SetWinEventHook, UnhookWinEvent, HWINEVENTHOOK};
use windows::Win32::UI::WindowsAndMessaging::{
    GetClassNameW, EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED, WINEVENT_OUTOFCONTEXT,
};

use super::ime_winevent::LEGACY_IME_EVENT_MS;
use crate::input::hook::{
    IME_ACTIVE, IME_CANDIDATE_NAV_COUNT, IME_COMPOSITION_START_MS, IME_SHADOW_ABANDON,
    IME_SHADOW_DWELL_MS, IME_SHADOW_READY,
};
use crate::input::ime_cloak::{
    candidate_shown_from_cloak, is_ime_cloak_class, CloakFold, FoldAction, FOLD_GAP_MS,
};

/// One cloak or uncloak notification, reduced to what the resolver needs. The window handle is carried
/// as an integer because a raw handle is not `Send`; it identifies the window and is not kept or
/// written anywhere.
pub(super) struct CloakEvent {
    hwnd: isize,
    cloaked: bool,
}

/// Capacity of the bounded channel between the callback and the resolver. A full channel makes the
/// callback drop the event rather than block.
const CLOAK_QUEUE_CAPACITY: usize = 256;
/// Wait of the resolver on an empty queue while no session is open. It bounds the delay with which the
/// resolver notices the stop flag.
const IDLE_WAIT: Duration = Duration::from_millis(50);

/// Sender used by the callback. A hook callback is a bare function and cannot capture state, so the
/// sender is published once, before the hook is installed, and is never dropped.
static CLOAK_TX: OnceLock<SyncSender<CloakEvent>> = OnceLock::new();

/// Number of notifications dropped because the channel was full when the callback tried to send.
static DROPPED_CLOAK_EVENTS: AtomicU64 = AtomicU64::new(0);

/// Publishes the sender of the channel the callback writes to. Fails when called a second time, because
/// a second resolver would never be fed.
pub(super) fn set_sender(tx: SyncSender<CloakEvent>) -> Result<(), String> {
    CLOAK_TX
        .set(tx)
        .map_err(|_| "the cloak hook can be started only once per process".to_string())
}

/// Creates the bounded channel between the callback and the resolver.
pub(super) fn channel() -> (SyncSender<CloakEvent>, Receiver<CloakEvent>) {
    std::sync::mpsc::sync_channel(CLOAK_QUEUE_CAPACITY)
}

/// Number of notifications dropped so far because the channel was full.
pub(super) fn dropped_count() -> u64 {
    DROPPED_CLOAK_EVENTS.load(Ordering::Relaxed)
}

/// Callback for the cloak and uncloak notifications. It performs atomic operations and one
/// non-blocking send only: no locking, logging or query of the compositor or of the window.
///
/// Only notifications about a whole window are considered (object identifier zero, child identifier
/// zero); without this, the caret and the list items of the same window would inflate the count.
unsafe extern "system" fn cloak_event_callback(
    _hook: HWINEVENTHOOK,
    event: u32,
    hwnd: HWND,
    id_object: i32,
    id_child: i32,
    _id_event_thread: u32,
    _event_time: u32,
) {
    if id_object != 0 || id_child != 0 || hwnd.0.is_null() {
        return;
    }
    // When the input-method window events are driving the session state, send nothing. Only a time
    // comparison is done here; the resolver checks again on its side.
    let last_legacy = LEGACY_IME_EVENT_MS.load(Ordering::Acquire);
    if last_legacy != 0 && legacy_path_is_driving(last_legacy, super::wall_ms()) {
        return;
    }
    if let Some(tx) = CLOAK_TX.get() {
        let sent = tx.try_send(CloakEvent {
            hwnd: hwnd.0 as isize,
            cloaked: event == EVENT_OBJECT_CLOAKED,
        });
        if let Err(TrySendError::Full(_) | TrySendError::Disconnected(_)) = sent {
            DROPPED_CLOAK_EVENTS.fetch_add(1, Ordering::Relaxed);
        }
    }
}

/// Registers the notifications for a window being cloaked and uncloaked on the calling thread, which
/// must pump its message queue. The two event identifiers are adjacent, so the registered interval
/// contains nothing else. The events of the process's own windows are not skipped; this process owns
/// no window, so skipping them would not change what is observed.
pub(super) fn install() -> Result<HWINEVENTHOOK, String> {
    // SAFETY: `cloak_event_callback` matches the `WINEVENTPROC` signature and stays valid for the
    // lifetime of the process; the hook is removed by `uninstall` on the same thread.
    let hook = unsafe {
        SetWinEventHook(
            EVENT_OBJECT_CLOAKED,
            EVENT_OBJECT_UNCLOAKED,
            HMODULE::default(),
            Some(cloak_event_callback),
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
    };
    if hook.is_invalid() {
        return Err("SetWinEventHook (window cloak events) failed".to_string());
    }
    Ok(hook)
}

/// Removes the hook registered by [`install`]. Must run on the registering thread.
pub(super) fn uninstall(hook: HWINEVENTHOOK) {
    // SAFETY: `hook` was returned by `SetWinEventHook` on this thread and is removed exactly once.
    if !unsafe { UnhookWinEvent(hook) }.as_bool() {
        tracing::warn!("UnhookWinEvent (window cloak events) failed");
    }
}

// @excerpt-begin legacy_path_is_driving
pub const LEGACY_PATH_RECENCY_MS: u64 = 60_000;

#[cfg(target_os = "windows")]
pub fn legacy_path_is_driving(last_legacy_ms: u64, now_ms: u64) -> bool {
    if last_legacy_ms == 0 {
        return false;
    }
    now_ms.saturating_sub(last_legacy_ms) <= LEGACY_PATH_RECENCY_MS
}

// @excerpt-end legacy_path_is_driving

// @excerpt-begin resolved_cloak_state
fn resolved_cloak_state(dwm: Option<bool>, event_cloaked: bool) -> bool {
    dwm.unwrap_or(event_cloaked)
}

// @excerpt-end resolved_cloak_state

/// Class name of a window, or `None` when it cannot be read (for example because the window is gone
/// by the time an asynchronously delivered notification is handled). A failure is not turned into an
/// empty string. The name is used to classify the window and goes nowhere else.
fn class_name_of(hwnd: HWND) -> Option<String> {
    let mut buf = [0u16; 256];
    // SAFETY: `buf` is a local array; `GetClassNameW` writes at most its length and returns the number
    // of units written, or zero on failure.
    let len = unsafe { GetClassNameW(hwnd, &mut buf) };
    if len <= 0 {
        return None;
    }
    Some(String::from_utf16_lossy(&buf[..len as usize]))
}

/// Asks the compositor whether the window is cloaked now, or `None` when it cannot be determined. A
/// failure is not turned into "not cloaked".
fn dwm_is_cloaked(hwnd: HWND) -> Option<bool> {
    let mut v: u32 = 0;
    // SAFETY: the output is a local `u32` and the size passed is its size.
    let hr = unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            (&mut v as *mut u32).cast(),
            std::mem::size_of::<u32>() as u32,
        )
    };
    if hr.is_ok() {
        Some(v != 0)
    } else {
        None
    }
}

/// The cells the resolver reads and writes. The production instance refers to the shared statics;
/// tests use local cells so that they do not race with the tests of the copied state machines.
pub(super) struct Cells<'a> {
    pub(super) legacy_event_ms: &'a AtomicU64,
    pub(super) start_ms: &'a AtomicU64,
    pub(super) nav: &'a AtomicU32,
    pub(super) abandon: &'a AtomicBool,
    pub(super) dwell_ms: &'a AtomicU64,
    pub(super) ready: &'a AtomicBool,
    pub(super) active: &'a AtomicBool,
}

impl Cells<'static> {
    pub(super) fn shared() -> Self {
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

/// Applies the outcome of the fold to the session state. This function is the only writer of the state
/// on the cloak path.
///
/// The order of the stores matches the input-method window events: the release store of the ready flag
/// publishes the preceding relaxed stores to the monitoring loop, which reads the flag with acquire
/// ordering. The active flag is lowered before the ready flag is raised, so that the monitoring loop,
/// which emits a row when the flag is lowered and the summary is ready, does not wait one more cycle.
fn apply_fold_action(action: FoldAction, c: &Cells<'_>) {
    match action {
        FoldAction::Begin(start_ms) => {
            c.start_ms.store(start_ms, Ordering::Relaxed);
            // Counters of the previous session are folded away. A session that begins before the
            // monitoring loop has read the summary of the previous one loses that summary; the
            // input-method window events have the same limit.
            c.nav.store(0, Ordering::Relaxed);
            c.abandon.store(false, Ordering::Relaxed);
            c.active.store(true, Ordering::Release);
        }
        FoldAction::End {
            dwell_ms,
            forced: true,
        } => {
            // A session cut off because its end was never observed. No row is produced: the ready
            // flag stays down and the start time is cleared, so that neither the normal path nor the
            // stale path of the monitoring loop reports the cap as a dwell time. The counters of the
            // cut-off interval are folded away here so that the invariant does not depend on the next
            // session beginning.
            c.start_ms.store(0, Ordering::Relaxed);
            c.nav.store(0, Ordering::Relaxed);
            c.abandon.store(false, Ordering::Relaxed);
            c.active.store(false, Ordering::Release);
            tracing::debug!(dwell_ms, "cloak session truncated; no row is produced");
        }
        FoldAction::End {
            dwell_ms,
            forced: false,
        } => {
            c.dwell_ms.store(dwell_ms, Ordering::Relaxed);
            c.start_ms.store(0, Ordering::Relaxed);
            c.active.store(false, Ordering::Release);
            c.ready.store(true, Ordering::Release);
        }
    }
}

/// Folds one notification of a candidate window into the session state. The compositor's answer takes
/// precedence over the edge the notification carried.
fn handle_event(
    fold: &mut CloakFold,
    ev: &CloakEvent,
    now_ms: u64,
    dwm: Option<bool>,
) -> Option<FoldAction> {
    let cloaked = resolved_cloak_state(dwm, ev.cloaked);
    fold.on_event(ev.hwnd, candidate_shown_from_cloak(cloaked), now_ms)
}

/// One turn of the resolver. Returns `true` when the input-method window events are driving the
/// session state and the turn therefore left every atomic alone.
///
/// The class and the compositor are queried through `class_of` and `dwm` so that the turn can be
/// tested without a window. A notification whose class cannot be read, or is not that of a candidate
/// window, is dropped before anything else happens: neither the compositor is asked nor the active
/// flag restated. For a candidate window, the compositor is asked for the current cloak state, the
/// fold is advanced, and, while the fold is open, the active flag is restated because the monitoring
/// loop may have lowered it and no further `Begin` follows in that case. Time is advanced on every
/// turn, with or without a notification; without this the last uncloak of a session would never
/// close it.
fn step(
    fold: &mut CloakFold,
    received: Option<CloakEvent>,
    now_ms: u64,
    c: &Cells<'_>,
    class_of: impl Fn(isize) -> Option<String>,
    dwm: impl Fn(isize) -> Option<bool>,
) -> bool {
    // The callback checks this too, but a session can be open when the input-method window events
    // start to arrive. The resolver then leaves every atomic alone: nothing records which path
    // wrote a flag, and lowering the active flag here could cancel a session of the other path.
    // A flag this path left raised is recovered by the timeout of the monitoring loop.
    if legacy_path_is_driving(c.legacy_event_ms.load(Ordering::Acquire), now_ms) {
        *fold = CloakFold::new();
        return true;
    }
    if let Some(ev) = received {
        let is_candidate = class_of(ev.hwnd).is_some_and(|class| is_ime_cloak_class(&class));
        if is_candidate {
            if let Some(action) = handle_event(fold, &ev, now_ms, dwm(ev.hwnd)) {
                apply_fold_action(action, c);
            }
            if fold.is_active() {
                c.active.store(true, Ordering::Release);
            }
        }
    }
    if let Some(action) = fold.on_tick(now_ms) {
        apply_fold_action(action, c);
    }
    false
}

/// Body of the resolver thread: receives the notifications, folds them, and advances the fold in time.
/// Returns once the stop flag or the local stop flag is raised, or the channel is disconnected.
pub(super) fn resolve_events(rx: Receiver<CloakEvent>, stop: &AtomicBool, local_stop: &AtomicBool) {
    tracing::info!("cloak resolver started");
    let c = Cells::shared();
    let mut fold = CloakFold::new();
    // Wake often enough not to miss the expiry of the debounce interval.
    let tick = Duration::from_millis(FOLD_GAP_MS.max(1) / 4 + 1);
    let mut warned_legacy = false;
    while !stop.load(Ordering::Acquire) && !local_stop.load(Ordering::Acquire) {
        let wait = if fold.is_active() { tick } else { IDLE_WAIT };
        let received = match rx.recv_timeout(wait) {
            Ok(ev) => Some(ev),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => break,
        };
        let stood_down = step(
            &mut fold,
            received,
            super::wall_ms(),
            &c,
            |h| class_name_of(HWND(h as *mut std::ffi::c_void)),
            |h| dwm_is_cloaked(HWND(h as *mut std::ffi::c_void)),
        );
        if stood_down {
            if !warned_legacy {
                warned_legacy = true;
                tracing::warn!(
                    "input-method window events are arriving; the cloak path stands down"
                );
            }
        } else {
            warned_legacy = false;
        }
    }
    tracing::info!(
        dropped_cloak_events = dropped_count(),
        "cloak resolver stopped"
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::input::ime_cloak::MAX_SESSION_MS;
    use std::cell::Cell;

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

    fn ev(hwnd: isize, cloaked: bool) -> CloakEvent {
        CloakEvent { hwnd, cloaked }
    }

    #[test]
    fn registered_events_are_adjacent() {
        assert_eq!(EVENT_OBJECT_CLOAKED, 0x8017);
        assert_eq!(EVENT_OBJECT_UNCLOAKED, 0x8018);
    }

    #[test]
    fn the_compositor_answer_takes_precedence_over_the_edge() {
        assert!(!resolved_cloak_state(Some(false), true));
        assert!(resolved_cloak_state(Some(true), false));
        assert!(resolved_cloak_state(None, true));
        assert!(!resolved_cloak_state(None, false));
    }

    #[test]
    fn the_legacy_path_counts_as_driving_only_after_an_event_and_only_for_a_while() {
        assert!(!legacy_path_is_driving(0, 1_000_000));
        assert!(legacy_path_is_driving(
            1_000,
            1_000 + LEGACY_PATH_RECENCY_MS
        ));
        assert!(!legacy_path_is_driving(
            1_000,
            1_000 + LEGACY_PATH_RECENCY_MS + 1
        ));
    }

    #[test]
    fn a_window_of_another_class_is_dropped_before_the_compositor_is_asked() {
        let l = Local::new();
        let mut fold = CloakFold::new();
        let asked = Cell::new(false);
        let stood_down = step(
            &mut fold,
            Some(ev(1, false)),
            100,
            &l.cells(),
            |_| Some("Chrome_WidgetWin_1".to_string()),
            |_| {
                asked.set(true);
                Some(false)
            },
        );
        assert!(!stood_down);
        assert!(!asked.get());
        assert!(!fold.is_active());
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn a_window_whose_class_cannot_be_read_is_dropped_before_the_compositor_is_asked() {
        let l = Local::new();
        let mut fold = CloakFold::new();
        let asked = Cell::new(false);
        step(
            &mut fold,
            Some(ev(1, false)),
            100,
            &l.cells(),
            |_| None,
            |_| {
                asked.set(true);
                Some(false)
            },
        );
        assert!(!asked.get());
        assert!(!fold.is_active());
        assert!(!l.active.load(Ordering::Acquire));
    }

    #[test]
    fn the_compositor_answer_wins_and_a_cloak_ends_the_session_after_the_gap() {
        let mut fold = CloakFold::new();
        // The notification says "cloaked" but the compositor says the window is visible: the
        // compositor wins and a session begins.
        let begin = handle_event(&mut fold, &ev(7, true), 1_000, Some(false));
        assert!(matches!(begin, Some(FoldAction::Begin(1_000))));
        // The window is cloaked again; the session ends only after the debounce interval.
        assert!(handle_event(&mut fold, &ev(7, false), 1_400, Some(true)).is_none());
        assert!(fold.on_tick(1_400 + FOLD_GAP_MS - 1).is_none());
        let end = fold.on_tick(1_400 + FOLD_GAP_MS);
        assert!(matches!(
            end,
            Some(FoldAction::End {
                dwell_ms: 400,
                forced: false
            })
        ));
    }

    fn ime_class(_: isize) -> Option<String> {
        Some("IME".to_string())
    }

    #[test]
    fn while_the_legacy_path_drives_the_atomics_are_untouched_and_the_fold_is_dropped() {
        let l = Local::new();
        let now = 1_000_000;
        l.legacy.store(now - 1_000, Ordering::Release);
        let mut fold = CloakFold::new();
        // A session opened before the other path started to arrive.
        fold.on_event(7, true, now - 500);
        assert!(fold.is_active());
        let stood_down = step(
            &mut fold,
            Some(ev(7, false)),
            now,
            &l.cells(),
            ime_class,
            |_| Some(false),
        );
        assert!(stood_down);
        assert!(!fold.is_active());
        assert!(!l.active.load(Ordering::Acquire));
        assert!(!l.ready.load(Ordering::Acquire));
        assert_eq!(l.start.load(Ordering::Relaxed), 0);
        assert_eq!(l.dwell.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn an_event_of_an_input_method_class_during_a_session_restates_the_active_flag() {
        let l = Local::new();
        let mut fold = CloakFold::new();
        step(
            &mut fold,
            Some(ev(7, false)),
            1_000,
            &l.cells(),
            ime_class,
            |_| Some(false),
        );
        assert!(l.active.load(Ordering::Acquire));
        // The monitoring loop lowers the flag while the session is still open.
        l.active.store(false, Ordering::Release);
        step(
            &mut fold,
            Some(ev(7, false)),
            1_100,
            &l.cells(),
            ime_class,
            |_| Some(false),
        );
        assert!(fold.is_active());
        assert!(l.active.load(Ordering::Acquire));
    }

    #[test]
    fn a_tick_without_a_notification_ends_the_session_and_publishes_it() {
        let l = Local::new();
        let mut fold = CloakFold::new();
        step(
            &mut fold,
            Some(ev(7, false)),
            1_000,
            &l.cells(),
            ime_class,
            |_| Some(false),
        );
        step(
            &mut fold,
            Some(ev(7, true)),
            1_400,
            &l.cells(),
            ime_class,
            |_| Some(true),
        );
        assert!(l.active.load(Ordering::Acquire));
        let stood_down = step(
            &mut fold,
            None,
            1_400 + FOLD_GAP_MS,
            &l.cells(),
            |_| panic!("no notification, no class query"),
            |_| panic!("no notification, no compositor query"),
        );
        assert!(!stood_down);
        assert!(!l.active.load(Ordering::Acquire));
        assert!(l.ready.load(Ordering::Acquire));
        assert_eq!(l.dwell.load(Ordering::Relaxed), 400);
    }

    #[test]
    fn begin_opens_the_session_and_resets_the_previous_counters() {
        let l = Local::new();
        l.nav.store(5, Ordering::Relaxed);
        l.abandon.store(true, Ordering::Relaxed);
        apply_fold_action(FoldAction::Begin(2_000), &l.cells());
        assert_eq!(l.start.load(Ordering::Relaxed), 2_000);
        assert_eq!(l.nav.load(Ordering::Relaxed), 0);
        assert!(!l.abandon.load(Ordering::Relaxed));
        assert!(l.active.load(Ordering::Acquire));
        assert!(!l.ready.load(Ordering::Acquire));
    }

    #[test]
    fn a_normal_end_publishes_the_dwell_time_and_lowers_the_active_flag() {
        let l = Local::new();
        apply_fold_action(FoldAction::Begin(2_000), &l.cells());
        apply_fold_action(
            FoldAction::End {
                dwell_ms: 900,
                forced: false,
            },
            &l.cells(),
        );
        assert_eq!(l.dwell.load(Ordering::Relaxed), 900);
        assert_eq!(l.start.load(Ordering::Relaxed), 0);
        assert!(!l.active.load(Ordering::Acquire));
        assert!(l.ready.load(Ordering::Acquire));
    }

    #[test]
    fn a_truncated_end_produces_no_summary_and_clears_the_counters() {
        let l = Local::new();
        apply_fold_action(FoldAction::Begin(2_000), &l.cells());
        l.nav.store(3, Ordering::Relaxed);
        l.abandon.store(true, Ordering::Relaxed);
        apply_fold_action(
            FoldAction::End {
                dwell_ms: MAX_SESSION_MS,
                forced: true,
            },
            &l.cells(),
        );
        assert!(!l.ready.load(Ordering::Acquire));
        assert_eq!(l.dwell.load(Ordering::Relaxed), 0);
        assert_eq!(l.start.load(Ordering::Relaxed), 0);
        assert_eq!(l.nav.load(Ordering::Relaxed), 0);
        assert!(!l.abandon.load(Ordering::Relaxed));
        assert!(!l.active.load(Ordering::Acquire));
    }
}
