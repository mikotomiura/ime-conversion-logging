//! Boundary types between a driver (per-operating-system key capture) and the core (monitoring loop and
//! log writing).
//!
//! A driver implementation must not need to modify `main.rs`; it conforms to this boundary instead.
//! Each driver exposes `pub fn start(ctx: DriverCtx) -> Result<DriverHandle, String>`.
//!
//! - A driver sends each observed key transition to `ctx.keys` as a `KeyEvent` (`vk` is mapped to the
//!   Windows virtual-key space; `t` is wall-clock time in milliseconds).
//! - Conversion-session state is exchanged through the atomics of the `input::hook` shim.
//! - **Stop contract**: once `ctx.stop` is raised, a driver must end its thread within
//!   [`STOP_DEADLINE_MS`] (for example by waiting on a timer, or on Windows by periodically checking
//!   `ctx.stop` around `MsgWaitForMultipleObjects` or similar). The core loop watches driver liveness
//!   with `JoinHandle::is_finished()` and treats an unexpected thread exit as an error. Dropping the
//!   `Sender` half of `ctx.keys` is also observable, as `mpsc::RecvTimeoutError::Disconnected`, but
//!   `is_finished()` detects a dead driver earlier and also covers the case where a driver thread ends
//!   before ever sending a key event.
//! - **Candidate-window observation hook**: a driver may return
//!   `Some(Box<dyn FnMut() -> bool + Send>)` in [`DriverHandle::candidate_window_observer`]. The core
//!   monitoring loop (`Monitor::poll` / `run_cycle` in `main.rs`) calls this closure once per cycle and
//!   treats its return value as "is a candidate window currently open". When the field is `None` (the
//!   default), the core instead reads `input::hook::IME_ACTIVE.load(Acquire)` directly.
//!
//!   **Calling thread**: this observation closure is called from the thread that runs `Monitor::poll`
//!   (the main loop thread started by `run()` in `main.rs`), which may be different from a driver's own
//!   key-capture thread. A driver that observes candidate windows through UI Automation initializes its
//!   COM apartment on the thread that first calls the accessibility API, because the resulting COM
//!   object is usable only from that thread; calling it from another thread hangs or fails. A driver
//!   that returns a UI-Automation-backed observer must therefore perform that initialization on its
//!   first call, on the thread that calls it (this thread need not be the one that constructed `ctx`).

use std::sync::atomic::AtomicBool;
use std::sync::mpsc::Sender;
use std::sync::Arc;

/// One observed key transition. Carries no character: the virtual-key code is folded into a category
/// before it is written out.
pub struct KeyEvent {
    /// Wall-clock time in milliseconds (Unix epoch).
    pub t: u64,
    /// Value mapped to the Windows virtual-key space.
    pub vk: u32,
    /// `true` for a press, `false` for a release.
    pub press: bool,
}

/// What is handed to a driver. Allowed to be unread while the driver slot is still a placeholder.
#[allow(dead_code)]
pub struct DriverCtx {
    pub keys: Sender<KeyEvent>,
    pub stop: Arc<AtomicBool>,
}

/// Result of starting a driver.
#[allow(dead_code)]
pub struct DriverHandle {
    /// The driver's thread. Must end within [`STOP_DEADLINE_MS`] of `ctx.stop` being raised.
    pub thread: std::thread::JoinHandle<()>,
    /// Observation closure for whether a candidate window is open (if absent, the core falls back to
    /// reading `IME_ACTIVE` directly). See the calling-thread note at the top of this file.
    pub candidate_window_observer: Option<Box<dyn FnMut() -> bool + Send>>,
}

impl DriverHandle {
    /// Convenience constructor for a driver that has no observation closure (for example, a
    /// placeholder implementation that only returns an error).
    #[allow(dead_code)]
    pub fn new(thread: std::thread::JoinHandle<()>) -> Self {
        Self {
            thread,
            candidate_window_observer: None,
        }
    }
}

/// Upper bound, in milliseconds, on how long a driver may take to end its thread after observing
/// `ctx.stop`. The core does not enforce this bound itself; it only detects a violation through
/// periodic checks of `JoinHandle::is_finished()`. This constant documents the contract that a driver
/// implementation must satisfy.
#[allow(dead_code)]
pub const STOP_DEADLINE_MS: u64 = 500;
