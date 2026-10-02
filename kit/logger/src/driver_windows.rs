//! Windows driver of the standalone logger: the low-level keyboard hook, the input-method window
//! events, the window cloak events, and the observation of the candidate window.
//!
//! One thread installs the three hooks and pumps its message queue, which the operating system requires
//! before it delivers low-level hook calls and out-of-context WinEvent notifications. The thread
//! polls the queue and the stop flag, so it ends well within the stop deadline of the driver
//! contract. No code is injected into another process, and no callback argument carries text.
//!
//! The cloak events are resolved on a further thread, next to the thread that forwards key events to
//! the core; the hook thread starts both and joins both, because the callback may not query the
//! compositor; see `driver_windows::cloak`. The observation closure that
//! `start` returns combines the state these paths write with two secondary signals; see
//! `driver_windows::candidate`.
//!
//! Start-up is synchronous: `start` returns only after the hooks are installed, and reports a failed
//! installation as an error instead of running silently without observing.
//!
//! Known limits. A hook that the operating system removes after installation is not re-installed:
//! the thread stays alive and the log then contains no key rows, which is indistinguishable from an
//! idle session. Timestamps are taken when a callback runs, and a callback runs when the thread next
//! polls its queue, so a timestamp can trail the physical event by up to the polling interval.
//! The observation of the focused element through UI Automation runs on the thread of the monitoring
//! loop as a synchronous cross-process call, so when the foreground application does not respond, the
//! writing of key rows and the end of a run at `--duration-s` are delayed until the UI Automation
//! timeout; no data is lost, because timestamps are taken when the events occur.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, Sender, SyncSender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use windows::Win32::Foundation::HINSTANCE;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::{
    DispatchMessageW, PeekMessageW, MSG, PM_NOREMOVE, PM_REMOVE, WM_QUIT,
};

use crate::driver_api::{DriverCtx, DriverHandle, KeyEvent};

mod candidate;
mod cloak;
mod ime_winevent;
mod keyboard;

/// Upper bound on the wait for the hook thread to report the result of the installation.
const START_TIMEOUT: Duration = Duration::from_secs(5);
/// Sleep between two empty polls of the message queue, and the timeout the forwarding thread waits on
/// an empty queue before it checks the stop flag again.
const IDLE_SLEEP: Duration = Duration::from_millis(1);
/// Capacity of the bounded channel between the keyboard callback and the forwarding thread. A callback
/// that cannot enqueue an event drops it and counts the drop rather than blocking; see
/// `driver_windows::keyboard`.
const KEY_QUEUE_CAPACITY: usize = 1024;

/// Wall-clock time in milliseconds since the Unix epoch.
fn wall_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub fn start(ctx: DriverCtx) -> Result<DriverHandle, String> {
    // The callback only ever tries a non-blocking send on this bounded channel; the forwarding thread
    // spawned inside `hook_thread` drains it and relays each event to `ctx.keys` with an ordinary,
    // possibly blocking, send, which is safe off the callback.
    let (key_tx, key_rx) = sync_channel::<KeyEvent>(KEY_QUEUE_CAPACITY);
    keyboard::set_sender(key_tx)?;
    let (cloak_tx, cloak_rx) = cloak::channel();
    cloak::set_sender(cloak_tx)?;
    let stop = Arc::clone(&ctx.stop);
    let (ack_tx, ack_rx) = sync_channel::<Result<(), String>>(1);
    let thread = thread::Builder::new()
        .name("ime-observation-driver".to_string())
        .spawn(move || hook_thread(&stop, &ack_tx, key_rx, cloak_rx, ctx.keys))
        .map_err(|e| format!("cannot spawn the hook thread: {e}"))?;
    match ack_rx.recv_timeout(START_TIMEOUT) {
        Ok(Ok(())) => Ok(DriverHandle {
            thread,
            candidate_window_observer: Some(candidate::observer()),
        }),
        Ok(Err(e)) => {
            let _ = thread.join();
            Err(e)
        }
        Err(RecvTimeoutError::Timeout) => {
            ctx.stop.store(true, Ordering::Release);
            Err("the hook thread did not report its start within 5 seconds".to_string())
        }
        Err(RecvTimeoutError::Disconnected) => {
            let _ = thread.join();
            Err("the hook thread ended before reporting its start".to_string())
        }
    }
}

/// Releases an installed hook when dropped, in place of a hand-written unwind on every early return.
/// `H` is a small, copyable handle (`HHOOK`, `HWINEVENTHOOK`), so releasing it does not consume
/// anything the rest of the function still needs.
struct HookGuard<H: Copy, F: FnMut(H)> {
    handle: H,
    release: F,
}

impl<H: Copy, F: FnMut(H)> HookGuard<H, F> {
    fn new(handle: H, release: F) -> Self {
        Self { handle, release }
    }
}

impl<H: Copy, F: FnMut(H)> Drop for HookGuard<H, F> {
    fn drop(&mut self) {
        (self.release)(self.handle);
    }
}

/// Body of the hook thread: install, report, pump until stopped, uninstall.
///
/// `key_rx`, `cloak_rx` and `core_keys` are moved into the threads that use them rather than shared by
/// reference: `Receiver` and `Sender` are `Send` but not `Sync`, and nothing in this function needs
/// them again after those threads start draining their queues.
fn hook_thread(
    stop: &AtomicBool,
    ack: &SyncSender<Result<(), String>>,
    key_rx: Receiver<KeyEvent>,
    cloak_rx: Receiver<cloak::CloakEvent>,
    core_keys: Sender<KeyEvent>,
) {
    // SAFETY: `PeekMessageW` with a default `MSG` only creates the message queue of this thread,
    // which a low-level hook requires before it is installed.
    unsafe {
        let mut msg = MSG::default();
        let _ = PeekMessageW(&mut msg, None, 0, 0, PM_NOREMOVE);
    }
    let keyboard_hook = match keyboard::install(module_handle()) {
        Ok(h) => h,
        Err(e) => {
            let _ = ack.send(Err(e));
            return;
        }
    };
    {
        // The keyboard hook is released when this block ends, whether by falling off its end or by
        // an early `return` below (an installation failing). On an early return the guards are dropped
        // in the reverse order of their declaration; on the normal path they are dropped explicitly
        // in the same order. Either way the release order is cloak hook, input-method window hook,
        // keyboard hook.
        let keyboard_guard = HookGuard::new(keyboard_hook, keyboard::uninstall);
        let ime_hook = match ime_winevent::install() {
            Ok(h) => h,
            Err(e) => {
                let _ = ack.send(Err(e));
                return;
            }
        };
        let ime_guard = HookGuard::new(ime_hook, ime_winevent::uninstall);
        let cloak_hook = match cloak::install() {
            Ok(h) => h,
            Err(e) => {
                let _ = ack.send(Err(e));
                return;
            }
        };
        let cloak_guard = HookGuard::new(cloak_hook, cloak::uninstall);
        tracing::info!("keyboard, input-method window and cloak hooks installed");
        let _ = ack.send(Ok(()));

        // Raised when the pump returns for any reason, including a quit message that did not come
        // from the core. The shared stop flag is left alone in that case, so that the end of this
        // thread is still seen by the core as an unexpected end rather than a requested stop.
        let local_stop = AtomicBool::new(false);
        let local_stop = &local_stop;
        thread::scope(|scope| {
            let forwarder =
                scope.spawn(move || forward_events(key_rx, core_keys, stop, local_stop));
            let resolver = scope.spawn(move || cloak::resolve_events(cloak_rx, stop, local_stop));
            pump_until_stopped(stop, local_stop);
            // The pump no longer serves the hooks, and a hook whose thread does not serve its queue
            // delays the typing of the whole system, so the hooks are released before the two
            // threads are joined. The release order is cloak hook, input-method window hook,
            // keyboard hook.
            drop(cloak_guard);
            drop(ime_guard);
            drop(keyboard_guard);
            let _ = forwarder.join();
            let _ = resolver.join();
        });
    }
    tracing::info!(
        dropped_key_events = keyboard::dropped_count(),
        "hook thread stopped"
    );
}

/// Drains the bounded queue the keyboard callback writes to and relays each event on to the core.
/// Unlike the callback, this function is not run by the operating system and may block: it waits on
/// the queue with a timeout so that it also notices the stop flag while the queue is empty, and it
/// forwards with an ordinary send that blocks only as long as the core's own channel does. It ends on
/// the stop flag of the core or on the local stop flag of the hook thread.
fn forward_events(
    key_rx: Receiver<KeyEvent>,
    core_keys: Sender<KeyEvent>,
    stop: &AtomicBool,
    local_stop: &AtomicBool,
) {
    loop {
        match key_rx.recv_timeout(IDLE_SLEEP) {
            Ok(event) => {
                if core_keys.send(event).is_err() {
                    break; // The core dropped its receiver; nothing is left to forward to.
                }
            }
            Err(RecvTimeoutError::Timeout) => {
                if stop.load(Ordering::Acquire) || local_stop.load(Ordering::Acquire) {
                    break;
                }
            }
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Handle of the current executable module. A null instance is accepted by the call but is not used,
/// because some Windows 11 configurations then leave the hook without callbacks.
fn module_handle() -> HINSTANCE {
    // SAFETY: `GetModuleHandleW(None)` returns the handle of the calling process's executable.
    match unsafe { GetModuleHandleW(None) } {
        Ok(h) => HINSTANCE(h.0),
        Err(e) => {
            tracing::warn!("GetModuleHandleW failed ({e:?}); using a null instance");
            HINSTANCE::default()
        }
    }
}

/// Dispatches queued messages until the stop flag is raised or a quit message arrives. The queue is
/// polled rather than waited on, which keeps the hook callbacks and the stop flag served by one loop.
/// On return the local stop flag is raised whatever the reason, so that the threads that work for this
/// one end too; the shared stop flag is never written here.
fn pump_until_stopped(stop: &AtomicBool, local_stop: &AtomicBool) {
    pump(stop);
    local_stop.store(true, Ordering::Release);
}

fn pump(stop: &AtomicBool) {
    while !stop.load(Ordering::Acquire) {
        let mut msg = MSG::default();
        // SAFETY: `msg` is a valid, exclusively borrowed `MSG`; the calls run on the thread that owns
        // the queue.
        if unsafe { PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE) }.as_bool() {
            if msg.message == WM_QUIT {
                break;
            }
            let _ = unsafe { DispatchMessageW(&msg) };
        } else {
            thread::sleep(IDLE_SLEEP);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::sync::mpsc::channel;
    use windows::Win32::UI::WindowsAndMessaging::PostQuitMessage;

    #[test]
    fn a_hook_guard_releases_its_handle_on_drop() {
        let released = Cell::new(false);
        {
            let _guard = HookGuard::new(42u32, |_h| released.set(true));
        }
        assert!(released.get());
    }

    /// Mirrors the nesting in `hook_thread`: the keyboard guard is declared first, the input-method
    /// window guard second and the cloak guard third, so on drop the release order is cloak,
    /// input-method window, keyboard.
    #[test]
    fn guards_release_in_reverse_declaration_order() {
        let order: RefCell<Vec<&'static str>> = RefCell::new(Vec::new());
        {
            let _keyboard_guard = HookGuard::new(1u32, |_h| order.borrow_mut().push("keyboard"));
            let _ime_guard = HookGuard::new(2u32, |_h| order.borrow_mut().push("input-method"));
            let _cloak_guard = HookGuard::new(3u32, |_h| order.borrow_mut().push("cloak"));
        }
        assert_eq!(*order.borrow(), vec!["cloak", "input-method", "keyboard"]);
    }

    /// A quit message ends the pump and raises the local stop flag, and leaves the shared stop flag
    /// down, so that the core sees an unexpected end of the thread.
    #[test]
    fn a_quit_message_ends_the_pump_and_raises_only_the_local_flag() {
        let stop = AtomicBool::new(false);
        let local_stop = AtomicBool::new(false);
        thread::scope(|scope| {
            scope
                .spawn(|| {
                    // SAFETY: posts a quit message to the queue of this thread.
                    unsafe { PostQuitMessage(0) };
                    pump_until_stopped(&stop, &local_stop);
                })
                .join()
                .expect("the pump returns after a quit message");
        });
        assert!(local_stop.load(Ordering::Acquire));
        assert!(!stop.load(Ordering::Acquire));
    }

    #[test]
    fn the_stop_flag_ends_the_pump_and_raises_the_local_flag() {
        let stop = AtomicBool::new(true);
        let local_stop = AtomicBool::new(false);
        pump_until_stopped(&stop, &local_stop);
        assert!(local_stop.load(Ordering::Acquire));
    }

    #[test]
    fn forward_events_relays_queued_events_to_the_core_channel() {
        let (bounded_tx, bounded_rx) = sync_channel::<KeyEvent>(4);
        let (core_tx, core_rx) = channel::<KeyEvent>();
        let stop = AtomicBool::new(false);
        let local_stop = AtomicBool::new(false);
        bounded_tx
            .send(KeyEvent {
                t: 1,
                vk: 0x41,
                press: true,
            })
            .expect("room in the bounded channel");
        bounded_tx
            .send(KeyEvent {
                t: 2,
                vk: 0x42,
                press: false,
            })
            .expect("room in the bounded channel");
        let stop_ref = &stop; // Copy, so moving it into the closure below leaves `stop` itself usable.
        thread::scope(|scope| {
            let forwarder =
                scope.spawn(move || forward_events(bounded_rx, core_tx, stop_ref, &local_stop));
            let first = core_rx
                .recv_timeout(Duration::from_secs(1))
                .expect("first event relayed");
            assert_eq!((first.vk, first.press), (0x41, true));
            let second = core_rx
                .recv_timeout(Duration::from_secs(1))
                .expect("second event relayed");
            assert_eq!((second.vk, second.press), (0x42, false));
            stop.store(true, Ordering::Release);
            forwarder.join().expect("forwarder does not panic");
        });
    }

    #[test]
    fn forward_events_stops_once_the_stop_flag_is_set_and_the_queue_is_empty() {
        let (_bounded_tx, bounded_rx) = sync_channel::<KeyEvent>(4);
        let (core_tx, _core_rx) = channel::<KeyEvent>();
        let stop = AtomicBool::new(false);
        let local_stop = AtomicBool::new(false);
        let stop_ref = &stop;
        thread::scope(|scope| {
            let forwarder =
                scope.spawn(move || forward_events(bounded_rx, core_tx, stop_ref, &local_stop));
            // No event is ever sent; with the queue empty, the loop only exits once it observes the
            // stop flag through its own timeout, which also proves it never blocks forever on `recv`.
            stop.store(true, Ordering::Release);
            forwarder
                .join()
                .expect("forwarder returns once stopped with an empty queue");
        });
    }
}
