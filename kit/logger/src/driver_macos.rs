// tap-callback: tap_callback
//! macOS driver of the standalone logger: a listen-only event tap on key presses and releases, the
//! tracking of whether a Japanese input method is open, and the conversion-session state machine.
//!
//! One thread creates the tap, attaches it to its run loop, and runs that loop in short slices so that
//! it notices the stop flag well within the stop deadline of the driver contract. Two helper threads
//! run next to it: one forwards key events from a bounded queue to the core, and one polls the input
//! source; see `driver_macos::ime_source`. The tap is created listen-only, so it can observe key
//! events but can neither alter nor swallow them.
//!
//! The tap callback performs atomic operations and a non-blocking channel send only. It does not
//! log, allocate for its own purposes, or wait. A full queue is not waited on; the event is dropped
//! and counted, and the count is reported once when the driver stops.
//!
//! Start-up is synchronous: `start` returns only after the tap is installed, and reports a failed
//! installation (usually a missing Input Monitoring permission) as an error. When the installation
//! fails, the driver asks the system once to show its Input Monitoring prompt, so that a user who has
//! not yet been asked can grant the permission and start the program again.
//!
//! Known limits. A tap that the system disables is re-enabled, both when the callback is told of the
//! disabling and by a periodic check on the driver thread; both kinds of re-enabling are counted and
//! reported when the driver stops. Keys typed while the tap was disabled are not observed, which is
//! indistinguishable from an idle period. The tap is not re-created if re-enabling does not restore
//! delivery; the callback gives up re-enabling after a long run of consecutive disabling notices,
//! and the periodic check continues. While Secure Event Input is active (for example in a password
//! field), the system delivers no key events to the tap, which is likewise indistinguishable from
//! an idle period. Auto-repeat events are not reported as key rows. Modifier keys are not observed,
//! because the tap listens to key presses and releases only.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, Sender, SyncSender};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use core_foundation::base::TCFType;
use core_foundation::mach_port::CFMachPortRef;
use core_foundation::runloop::{kCFRunLoopDefaultMode, CFRunLoop, CFRunLoopRunResult};
use core_graphics::event::{
    CGEvent, CGEventFlags, CGEventTap, CGEventTapLocation, CGEventTapOptions, CGEventTapPlacement,
    CGEventTapProxy, CGEventType, EventField,
};

use crate::driver_api::{DriverCtx, DriverHandle, KeyEvent};
use crate::input::ime_compose::{self, Chord};
use crate::input::ime_mode::{self, ImeOpenTransition};

mod ime_source;
mod keymap;
#[cfg(test)]
mod tests;

/// Upper bound on the wait for the driver thread to report the result of the installation.
const START_TIMEOUT: Duration = Duration::from_secs(5);
/// Length of one slice of the run loop, and the timeout the forwarding thread waits on an empty queue
/// before it checks the stop flags again.
const SLICE: Duration = Duration::from_millis(100);
/// Capacity of the bounded queue between the tap callback and the forwarding thread.
const KEY_QUEUE_CAPACITY: usize = 1024;
/// Number of consecutive disabling notices after which the callback stops re-enabling the tap. Any
/// delivered key event resets the count.
const MAX_CONSECUTIVE_REENABLES: u32 = 100;

/// Whether a Japanese input method is currently open, as far as the mode keys and the input-source
/// poll can tell.
static IME_OPEN: AtomicBool = AtomicBool::new(false);
/// Raised when a mode key changed [`IME_OPEN`], so that the poll trusts the flag once.
static IME_STATE_DIRTY: AtomicBool = AtomicBool::new(false);
/// Raised when a physical mode key of a JIS keyboard has been seen; from then on the poll trusts
/// [`IME_OPEN`].
static JIS_KEYBOARD_SEEN: AtomicBool = AtomicBool::new(false);

/// Queue to the forwarding thread. A tap callback is a bare function and cannot capture state, so the
/// sender is published once, before the tap is created. It is never dropped: the core detects a
/// terminated driver thread through the thread handle.
static KEY_TX: OnceLock<SyncSender<KeyEvent>> = OnceLock::new();
/// Wake-up channel to the input-source poll.
static WAKE_TX: OnceLock<SyncSender<()>> = OnceLock::new();
/// Number of key events dropped because the queue was full.
static DROPPED_KEY_EVENTS: AtomicU64 = AtomicU64::new(0);
/// Mach port of the installed tap, or null. It is written by the driver thread only, and read by the
/// callback and the periodic check, which run on that same thread.
static TAP_PORT: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
/// Disabling notices received since the last delivered key event.
static CONSECUTIVE_REENABLES: AtomicU32 = AtomicU32::new(0);
/// Number of times the callback re-enabled the tap after a disabling notice.
static CALLBACK_REENABLES: AtomicU32 = AtomicU32::new(0);
/// Number of times the periodic check found the tap disabled and re-enabled it.
static PERIODIC_REENABLES: AtomicU32 = AtomicU32::new(0);

#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn CGPreflightListenEventAccess() -> bool;
    fn CGRequestListenEventAccess() -> bool;
    fn AXIsProcessTrusted() -> bool;
}

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventTapEnable(tap: CFMachPortRef, enable: bool);
    fn CGEventTapIsEnabled(tap: CFMachPortRef) -> bool;
}

// The crate that provides the tap type does not invalidate the mach port when the tap is dropped, so
// a late delivery could reach a freed callback. The port is invalidated explicitly before the drop.
#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFMachPortInvalidate(port: CFMachPortRef);
}

/// Wall-clock time in milliseconds since the Unix epoch.
fn wall_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

/// The shared cells a mode key writes.
struct ModeCells<'a> {
    open: &'a AtomicBool,
    dirty: &'a AtomicBool,
    jis_seen: &'a AtomicBool,
}

const SHARED_MODE_CELLS: ModeCells<'static> = ModeCells {
    open: &IME_OPEN,
    dirty: &IME_STATE_DIRTY,
    jis_seen: &JIS_KEYBOARD_SEEN,
};

/// Applies a key transition to the open/closed flag when the key is an input-mode key.
///
/// A press sets or toggles the flag according to the transition table; a release of a mode key can
/// set it as well. The toggle is a single atomic exclusive-or, so that a write by the poll thread
/// between a load and a store is not lost.
fn apply_mode_key(vk: u32, press: bool, cells: &ModeCells<'_>) {
    if press {
        match ime_mode::ime_open_after_press(vk) {
            ImeOpenTransition::Set(v) => {
                cells.jis_seen.store(true, Ordering::Relaxed);
                cells.open.store(v, Ordering::Release);
                cells.dirty.store(true, Ordering::Release);
            }
            ImeOpenTransition::Toggle => {
                cells.jis_seen.store(true, Ordering::Relaxed);
                cells.open.fetch_xor(true, Ordering::AcqRel);
                cells.dirty.store(true, Ordering::Release);
            }
            ImeOpenTransition::None => {}
        }
    } else if let Some(v) = ime_mode::ime_open_after_release(vk) {
        cells.open.store(v, Ordering::Release);
        cells.dirty.store(true, Ordering::Release);
    }
}

/// A press together with the Command or Control modifier is a shortcut, not text entry; Shift and
/// Option are ordinary parts of typing.
fn chord_of(command: bool, control: bool) -> Chord {
    if command || control {
        Chord::Modified
    } else {
        Chord::Plain
    }
}

/// Sends one key event over the bounded queue, counting a drop instead of blocking when the queue is
/// full or its receiving end is gone.
fn send_or_count_drop(tx: &SyncSender<KeyEvent>, event: KeyEvent, dropped: &AtomicU64) {
    if tx.try_send(event).is_err() {
        dropped.fetch_add(1, Ordering::Relaxed);
    }
}

/// Whether a key row is queued for the core: auto-repeat events are dropped, and only keys of the
/// observed set are reported.
fn queue_row(repeat: bool, mac_vk: u64) -> bool {
    !repeat && keymap::observed(mac_vk)
}

/// Whether a key transition wakes the input-source poll: every press, and any transition of a mode
/// key.
fn wakes_poll(press: bool, vk: u32) -> bool {
    press || ime_mode::is_ime_mode_key(vk)
}

/// Whether a key transition is fed to the conversion-session state machine: presses only.
fn drives_compose(press: bool) -> bool {
    press
}

/// Handles one key transition: updates the input-mode flag, feeds the conversion-session state
/// machine, queues the key row for the core, and wakes the input-source poll.
fn handle_key(mac_vk: u64, press: bool, repeat: bool, command: bool, control: bool, t: u64) {
    let vk = keymap::to_vk(mac_vk);
    apply_mode_key(vk, press, &SHARED_MODE_CELLS);
    if drives_compose(press) {
        ime_compose::drive(vk, IME_OPEN.load(Ordering::Acquire), chord_of(command, control), t);
    }
    if queue_row(repeat, mac_vk) {
        if let Some(tx) = KEY_TX.get() {
            send_or_count_drop(tx, KeyEvent { t, vk, press }, &DROPPED_KEY_EVENTS);
        }
    }
    if wakes_poll(press, vk) {
        if let Some(tx) = WAKE_TX.get() {
            let _ = tx.try_send(());
        }
    }
}

/// The tap callback. Returns `None`: a listen-only tap ignores the return value.
fn tap_callback(
    _proxy: CGEventTapProxy,
    event_type: CGEventType,
    event: &CGEvent,
) -> Option<CGEvent> {
    if matches!(
        event_type,
        CGEventType::TapDisabledByTimeout | CGEventType::TapDisabledByUserInput
    ) {
        if CONSECUTIVE_REENABLES.fetch_add(1, Ordering::Relaxed) < MAX_CONSECUTIVE_REENABLES {
            let port = TAP_PORT.load(Ordering::Acquire);
            if !port.is_null() {
                CALLBACK_REENABLES.fetch_add(1, Ordering::Relaxed);
                // SAFETY: the port is stored by the thread that runs this callback, after the tap
                // exists and before its run loop is entered, and cleared before the tap is
                // released; re-enabling a tap is a light call meant for this context.
                unsafe { CGEventTapEnable(port as CFMachPortRef, true) };
            }
        }
        return None;
    }
    CONSECUTIVE_REENABLES.store(0, Ordering::Relaxed);
    let press = matches!(event_type, CGEventType::KeyDown);
    if !press && !matches!(event_type, CGEventType::KeyUp) {
        return None;
    }
    let mac_vk = event.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE) as u64;
    let repeat = press && event.get_integer_value_field(EventField::KEYBOARD_EVENT_AUTOREPEAT) != 0;
    let flags = event.get_flags();
    handle_key(
        mac_vk,
        press,
        repeat,
        flags.contains(CGEventFlags::CGEventFlagCommand),
        flags.contains(CGEventFlags::CGEventFlagControl),
        wall_ms(),
    );
    None
}

pub fn start(ctx: DriverCtx) -> Result<DriverHandle, String> {
    let (key_tx, key_rx) = sync_channel::<KeyEvent>(KEY_QUEUE_CAPACITY);
    KEY_TX
        .set(key_tx)
        .map_err(|_| "the event tap can be started only once per process".to_string())?;
    let (wake_tx, wake_rx) = sync_channel::<()>(1);
    let _ = WAKE_TX.set(wake_tx);
    let stop = Arc::clone(&ctx.stop);
    let (ack_tx, ack_rx) = sync_channel::<Result<(), String>>(1);
    let thread = thread::Builder::new()
        .name("ime-observation-driver".to_string())
        .spawn(move || tap_thread(&stop, &ack_tx, key_rx, wake_rx, ctx.keys))
        .map_err(|e| format!("cannot spawn the driver thread: {e}"))?;
    match ack_rx.recv_timeout(START_TIMEOUT) {
        Ok(Ok(())) => Ok(DriverHandle::new(thread)),
        Ok(Err(e)) => {
            let _ = thread.join();
            Err(e)
        }
        Err(RecvTimeoutError::Timeout) => {
            ctx.stop.store(true, Ordering::Release);
            Err("the driver thread did not report its start within 5 seconds".to_string())
        }
        Err(RecvTimeoutError::Disconnected) => {
            let _ = thread.join();
            Err("the driver thread ended before reporting its start".to_string())
        }
    }
}

/// Explains a failed tap creation, asking the system to show its permission prompt once.
fn permission_message() -> String {
    // SAFETY: both calls only read the permission state of this process; the request shows the
    // system prompt and returns.
    let (listen, ax) = unsafe { (CGPreflightListenEventAccess(), AXIsProcessTrusted()) };
    if !listen {
        // SAFETY: as above.
        unsafe { CGRequestListenEventAccess() };
    }
    format!(
        "cannot create the keyboard event tap (Input Monitoring granted: {listen}; Accessibility \
         granted: {ax}); grant Input Monitoring to this program in System Settings, then start it again"
    )
}

/// Body of the driver thread: create the tap, report, run the loop until stopped, tear down.
fn tap_thread(
    stop: &AtomicBool,
    ack: &SyncSender<Result<(), String>>,
    key_rx: Receiver<KeyEvent>,
    wake_rx: Receiver<()>,
    core_keys: Sender<KeyEvent>,
) {
    let tap = match CGEventTap::new(
        CGEventTapLocation::Session,
        CGEventTapPlacement::HeadInsertEventTap,
        CGEventTapOptions::ListenOnly,
        vec![CGEventType::KeyDown, CGEventType::KeyUp],
        tap_callback,
    ) {
        Ok(tap) => tap,
        Err(()) => {
            let _ = ack.send(Err(permission_message()));
            return;
        }
    };
    let port = tap.mach_port.as_concrete_TypeRef() as CFMachPortRef;
    let source = match tap.mach_port.create_runloop_source(0) {
        Ok(source) => source,
        Err(()) => {
            // SAFETY: the tap is alive and owned by this thread.
            unsafe { CFMachPortInvalidate(port) };
            let _ = ack.send(Err("cannot create a run-loop source for the event tap".to_string()));
            return;
        }
    };
    if stop.load(Ordering::Acquire) {
        // The caller gave up waiting for the start; nobody uses the tap any more.
        // SAFETY: the tap is alive and owned by this thread; the port is invalidated once.
        unsafe { CFMachPortInvalidate(port) };
        return;
    }
    let run_loop = CFRunLoop::get_current();
    // SAFETY: `kCFRunLoopDefaultMode` is a constant of the CoreFoundation framework.
    run_loop.add_source(&source, unsafe { kCFRunLoopDefaultMode });
    TAP_PORT.store(port as *mut c_void, Ordering::Release);
    tap.enable();
    tracing::info!("listen-only keyboard event tap installed");
    let _ = ack.send(Ok(()));

    // Raised when the run loop ends for any reason. The shared stop flag is left alone in that case, so
    // that the end of this thread is still seen by the core as an unexpected end rather than a stop.
    let local_stop = AtomicBool::new(false);
    let local_stop = &local_stop;
    thread::scope(|scope| {
        let forwarder = scope.spawn(move || forward_events(key_rx, core_keys, stop, local_stop));
        let poller = scope.spawn(move || ime_source::poll_loop(wake_rx, stop, local_stop));
        run_until_stopped(stop, port);
        local_stop.store(true, Ordering::Release);
        // The tap no longer serves the callback: the port is withdrawn and invalidated before the
        // helper threads are joined and before the tap itself is released.
        TAP_PORT.store(std::ptr::null_mut(), Ordering::Release);
        // SAFETY: the tap is alive and owned by this thread; the port is invalidated once.
        unsafe { CFMachPortInvalidate(port) };
        let _ = forwarder.join();
        let _ = poller.join();
    });
    drop(source);
    drop(tap);
    tracing::info!(
        dropped_key_events = DROPPED_KEY_EVENTS.load(Ordering::Relaxed),
        callback_reenables = CALLBACK_REENABLES.load(Ordering::Relaxed),
        periodic_reenables = PERIODIC_REENABLES.load(Ordering::Relaxed),
        input_source_queries_completed = ime_source::completed_queries(),
        "event tap thread stopped"
    );
}

/// Runs the run loop in slices until the stop flag is raised, re-enabling the tap when the system
/// has disabled it without telling the callback. Returns early if the run loop has nothing left to
/// serve.
fn run_until_stopped(stop: &AtomicBool, port: CFMachPortRef) {
    while !stop.load(Ordering::Acquire) {
        // SAFETY: `kCFRunLoopDefaultMode` is a constant of the CoreFoundation framework.
        let result = CFRunLoop::run_in_mode(unsafe { kCFRunLoopDefaultMode }, SLICE, false);
        if matches!(result, CFRunLoopRunResult::Finished) {
            break;
        }
        // SAFETY: the port belongs to the tap that this thread owns and has not yet invalidated.
        unsafe {
            if !CGEventTapIsEnabled(port) {
                PERIODIC_REENABLES.fetch_add(1, Ordering::Relaxed);
                CGEventTapEnable(port, true);
            }
        }
    }
}

/// Drains the bounded queue the tap callback writes to and relays each event on to the core. It may
/// block, unlike the callback: it waits on the queue with a timeout so that it also notices the stop
/// flags while the queue is empty.
fn forward_events(
    key_rx: Receiver<KeyEvent>,
    core_keys: Sender<KeyEvent>,
    stop: &AtomicBool,
    local_stop: &AtomicBool,
) {
    loop {
        match key_rx.recv_timeout(SLICE) {
            Ok(event) => {
                if core_keys.send(event).is_err() {
                    break;
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
