// hook-callback: keyboard_hook_callback
//! Low-level keyboard hook (`WH_KEYBOARD_LL`) of the Windows driver.
//!
//! The callback places every observed key transition as `(t, vk, press)` on a bounded queue that a
//! separate thread drains toward the core, and, while a conversion session is open, counts candidate
//! navigation and records abandonment for the session summary. Two rules bind the callback: it performs
//! atomic operations and a non-blocking channel send only (no locking, allocation-heavy work or
//! logging), and it always passes the event on to the next hook. A full queue is not waited on; the
//! event is dropped and counted instead. Injected input is deliberately not discarded, so that keys
//! produced by a scripted input source are observed like physical ones.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::SyncSender;
use std::sync::OnceLock;

use windows::Win32::Foundation::{HINSTANCE, LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, SetWindowsHookExW, UnhookWindowsHookEx, HHOOK, KBDLLHOOKSTRUCT, WH_KEYBOARD_LL,
    WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

use crate::driver_api::KeyEvent;
use crate::input::hook::{IME_ACTIVE, IME_CANDIDATE_NAV_COUNT, IME_SHADOW_ABANDON};

/// Bounded channel to the forwarding thread that relays events on to the core. A hook callback is a
/// bare function and cannot capture state, so the sender is published once, before the hook is
/// installed. The sender is never dropped: the core detects a terminated driver thread through the
/// thread handle rather than through channel disconnection. The channel has a fixed capacity, so a full
/// channel makes the callback drop the event instead of blocking; `DROPPED_KEY_EVENTS` counts how many
/// events were dropped this way.
static KEY_TX: OnceLock<SyncSender<KeyEvent>> = OnceLock::new();

/// Number of key events dropped because the channel above was full when the callback tried to send.
/// The callback only adds to it; the driver reads it once, when it stops, to report a single summary
/// line (a count, never the events themselves).
static DROPPED_KEY_EVENTS: AtomicU64 = AtomicU64::new(0);

// @excerpt-begin is_observed_key
fn is_observed_key(vk_code: u32) -> bool {
    if crate::analysis::key_gate::is_feature_key(vk_code) {
        return true;
    }
    matches!(
        vk_code,
        0x10 | 0x11 | 0x12
        | 0x5B | 0x5C
        | 0xA0..=0xA5
        | 0x21..=0x28
        | 0x1B
        | 0x70..=0x7B
    )
}

// @excerpt-end is_observed_key

// @excerpt-begin classify_ime_nav_key
#[derive(Debug, PartialEq, Eq)]
enum ImeNavAction {
    Navigate,
    Abandon,
    Other,
}

fn classify_ime_nav_key(vk_code: u32) -> ImeNavAction {
    match vk_code {
        0x25..=0x28 => ImeNavAction::Navigate,
        0x1B => ImeNavAction::Abandon,
        _ => ImeNavAction::Other,
    }
}

// @excerpt-end classify_ime_nav_key

/// Maps a hook message to a key transition: `Some(true)` for a press, `Some(false)` for a release,
/// `None` for anything that is not a key message.
fn key_transition(message: u32) -> Option<bool> {
    match message {
        WM_KEYDOWN | WM_SYSKEYDOWN => Some(true),
        WM_KEYUP | WM_SYSKEYUP => Some(false),
        _ => None,
    }
}

/// Updates the navigation counter and the abandonment flag of the open conversion session for one key
/// transition. Only presses are recorded, and nothing is recorded while no session is open.
///
/// The hook is system-wide, so an arrow key or Escape pressed in an unrelated window while a session
/// is open is counted as well; the session summary is therefore an upper bound on candidate navigation.
fn record_ime_nav(
    vk_code: u32,
    press: bool,
    active: &AtomicBool,
    nav: &AtomicU32,
    abandon: &AtomicBool,
) {
    if !press || !active.load(Ordering::Acquire) {
        return;
    }
    match classify_ime_nav_key(vk_code) {
        ImeNavAction::Navigate => {
            nav.fetch_add(1, Ordering::Relaxed);
        }
        ImeNavAction::Abandon => abandon.store(true, Ordering::Relaxed),
        ImeNavAction::Other => {}
    }
}

/// Sends one key event over the bounded channel, counting a drop instead of blocking when the channel
/// is full or its receiving end is gone. Kept separate from the callback so that the drop-counting
/// behavior can be exercised without touching the process-wide sender above.
fn send_or_count_drop(tx: &SyncSender<KeyEvent>, event: KeyEvent, dropped: &AtomicU64) {
    if tx.try_send(event).is_err() {
        dropped.fetch_add(1, Ordering::Relaxed);
    }
}

/// Publishes the channel to the core. Fails if a sender was already published in this process.
pub(super) fn set_sender(tx: SyncSender<KeyEvent>) -> Result<(), String> {
    KEY_TX
        .set(tx)
        .map_err(|_| "the keyboard hook can be started only once per process".to_string())
}

/// Number of key events dropped so far because the bounded channel was full.
pub(super) fn dropped_count() -> u64 {
    DROPPED_KEY_EVENTS.load(Ordering::Relaxed)
}

/// Installs the low-level keyboard hook on the calling thread, which must pump its message queue.
pub(super) fn install(hinstance: HINSTANCE) -> Result<HHOOK, String> {
    // SAFETY: `keyboard_hook_callback` matches the `HOOKPROC` signature and stays valid for the
    // lifetime of the process; the hook is removed by `uninstall` on the same thread.
    unsafe { SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook_callback), hinstance, 0) }
        .map_err(|e| format!("SetWindowsHookExW(WH_KEYBOARD_LL) failed: {e}"))
}

/// Removes the hook installed by [`install`]. Must run on the installing thread.
pub(super) fn uninstall(hook: HHOOK) {
    // SAFETY: `hook` was returned by `SetWindowsHookExW` on this thread and is removed exactly once.
    if let Err(e) = unsafe { UnhookWindowsHookEx(hook) } {
        tracing::warn!("UnhookWindowsHookEx failed: {e:?}");
    }
}

unsafe extern "system" fn keyboard_hook_callback(
    code: i32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if code >= 0 {
        if let Some(press) = key_transition(wparam.0 as u32) {
            // SAFETY: `code >= 0` together with a WM_KEY*/WM_SYSKEY* message means the operating
            // system passes a valid pointer to a `KBDLLHOOKSTRUCT` for the duration of this call.
            let vk_code = (*(lparam.0 as *const KBDLLHOOKSTRUCT)).vkCode;
            let t = super::wall_ms();
            record_ime_nav(
                vk_code,
                press,
                &IME_ACTIVE,
                &IME_CANDIDATE_NAV_COUNT,
                &IME_SHADOW_ABANDON,
            );
            if is_observed_key(vk_code) {
                if let Some(tx) = KEY_TX.get() {
                    send_or_count_drop(
                        tx,
                        KeyEvent {
                            t,
                            vk: vk_code,
                            press,
                        },
                        &DROPPED_KEY_EVENTS,
                    );
                }
            }
        }
    }
    CallNextHookEx(None, code, wparam, lparam)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observed_keys_include_the_feature_set() {
        for vk in [
            0x41, 0x5A, 0x30, 0x39, 0x08, 0x0D, 0x20, 0x09, 0x2E, 0xBA, 0x60,
        ] {
            assert!(is_observed_key(vk), "{vk:#x}");
        }
    }

    #[test]
    fn observed_keys_add_modifiers_function_keys_escape_and_navigation() {
        for vk in [
            0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x21, 0x22, 0x23,
            0x24, 0x25, 0x26, 0x27, 0x28, 0x1B, 0x70, 0x7B,
        ] {
            assert!(is_observed_key(vk), "{vk:#x}");
        }
    }

    #[test]
    fn keys_outside_both_sets_are_not_observed() {
        for vk in [0x00, 0x07, 0x14, 0x2C, 0x7C, 0x90, 0xFF] {
            assert!(!is_observed_key(vk), "{vk:#x}");
        }
    }

    #[test]
    fn nav_classification_separates_arrows_escape_and_the_rest() {
        for vk in 0x25..=0x28 {
            assert_eq!(classify_ime_nav_key(vk), ImeNavAction::Navigate, "{vk:#x}");
        }
        assert_eq!(classify_ime_nav_key(0x1B), ImeNavAction::Abandon);
        for vk in [0x24, 0x29, 0x41, 0x20, 0x0D] {
            assert_eq!(classify_ime_nav_key(vk), ImeNavAction::Other, "{vk:#x}");
        }
    }

    #[test]
    fn key_messages_map_to_press_and_release() {
        assert_eq!(key_transition(WM_KEYDOWN), Some(true));
        assert_eq!(key_transition(WM_SYSKEYDOWN), Some(true));
        assert_eq!(key_transition(WM_KEYUP), Some(false));
        assert_eq!(key_transition(WM_SYSKEYUP), Some(false));
        assert_eq!(key_transition(0x0200), None); // WM_MOUSEMOVE
    }

    /// The session cells are local to each test; the shared statics are never touched, so these tests
    /// do not interfere with the tests of the copied state machine that run in parallel.
    fn cells(active: bool) -> (AtomicBool, AtomicU32, AtomicBool) {
        (
            AtomicBool::new(active),
            AtomicU32::new(0),
            AtomicBool::new(false),
        )
    }

    #[test]
    fn nothing_is_recorded_while_no_session_is_open() {
        let (active, nav, abandon) = cells(false);
        for vk in [0x25, 0x26, 0x1B] {
            record_ime_nav(vk, true, &active, &nav, &abandon);
        }
        assert_eq!(nav.load(Ordering::Relaxed), 0);
        assert!(!abandon.load(Ordering::Relaxed));
    }

    #[test]
    fn arrows_are_counted_and_escape_marks_abandonment_while_a_session_is_open() {
        let (active, nav, abandon) = cells(true);
        for vk in [0x25, 0x26, 0x27, 0x28, 0x28] {
            record_ime_nav(vk, true, &active, &nav, &abandon);
        }
        assert_eq!(nav.load(Ordering::Relaxed), 5);
        assert!(!abandon.load(Ordering::Relaxed));
        record_ime_nav(0x1B, true, &active, &nav, &abandon);
        assert!(abandon.load(Ordering::Relaxed));
    }

    #[test]
    fn releases_are_never_recorded() {
        // A key release must not be counted a second time, nor mark abandonment.
        let (active, nav, abandon) = cells(true);
        for vk in [0x25, 0x26, 0x27, 0x28, 0x1B] {
            record_ime_nav(vk, false, &active, &nav, &abandon);
        }
        assert_eq!(nav.load(Ordering::Relaxed), 0);
        assert!(!abandon.load(Ordering::Relaxed));
    }

    #[test]
    fn other_keys_do_not_touch_the_session_summary() {
        let (active, nav, abandon) = cells(true);
        for vk in [0x41, 0x20, 0x0D, 0x08, 0x24] {
            record_ime_nav(vk, true, &active, &nav, &abandon);
        }
        assert_eq!(nav.load(Ordering::Relaxed), 0);
        assert!(!abandon.load(Ordering::Relaxed));
    }

    /// `send_or_count_drop` is exercised on a local channel, never on the process-wide `KEY_TX`, so
    /// this test does not interfere with any other test that might publish a sender.
    #[test]
    fn a_key_event_is_relayed_while_the_channel_has_room() {
        let (tx, rx) = std::sync::mpsc::sync_channel::<KeyEvent>(1);
        let dropped = AtomicU64::new(0);
        send_or_count_drop(
            &tx,
            KeyEvent {
                t: 1,
                vk: 0x41,
                press: true,
            },
            &dropped,
        );
        assert_eq!(dropped.load(Ordering::Relaxed), 0);
        assert_eq!(rx.try_recv().expect("event was relayed").vk, 0x41);
    }

    #[test]
    fn a_key_event_is_dropped_and_counted_once_the_channel_is_full() {
        let (tx, _rx) = std::sync::mpsc::sync_channel::<KeyEvent>(1);
        let dropped = AtomicU64::new(0);
        // Fill the one slot; nothing drains it, so the next event has nowhere to go.
        tx.try_send(KeyEvent {
            t: 1,
            vk: 0x41,
            press: true,
        })
        .expect("the first event fits in the one-slot channel");
        send_or_count_drop(
            &tx,
            KeyEvent {
                t: 2,
                vk: 0x42,
                press: true,
            },
            &dropped,
        );
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
        send_or_count_drop(
            &tx,
            KeyEvent {
                t: 3,
                vk: 0x43,
                press: true,
            },
            &dropped,
        );
        assert_eq!(dropped.load(Ordering::Relaxed), 2);
    }
}
