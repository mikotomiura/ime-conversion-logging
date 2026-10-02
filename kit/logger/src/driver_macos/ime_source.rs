//! Tracking of whether a Japanese input source is selected, for the macOS driver.
//!
//! Two sources feed the open/closed flag. A physical mode key on a JIS keyboard is applied by the tap
//! callback at once and is trusted whenever it has been seen. Otherwise (for example on an ANSI
//! keyboard, where the mode is switched from the menu bar or with a shortcut) the identifier of the
//! current keyboard input source is polled.
//!
//! The input-source API must be called on the main dispatch queue, which the main thread of the
//! process services by running its main run loop. A blocking call to that queue from a helper thread
//! would hang whenever the main thread does not service it, so the query is submitted asynchronously
//! and its latest completed result is read on the next poll. When the main queue is never serviced,
//! no result arrives and the flag written by the mode keys stays in force; a warning is logged once
//! if no query has completed within [`NO_RESULT_WARNING_AFTER`] of the first submission. The number
//! of completed queries is reported when the driver stops. As a consequence a switch of the input
//! source by other means than a JIS mode key is observed only if the process's main thread services
//! the main dispatch queue.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI8, AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use core_foundation::base::{CFRelease, TCFType};
use core_foundation::string::CFString;

use super::{IME_OPEN, IME_STATE_DIRTY, JIS_KEYBOARD_SEEN};

/// Longest wait on the wake channel before the sources are read again.
const POLL_INTERVAL: Duration = Duration::from_millis(100);
/// How long after the first submitted query the absence of any completed query is reported.
const NO_RESULT_WARNING_AFTER: Duration = Duration::from_secs(2);
/// Pause after a wake-up, so that the tap callback has finished updating the flags it shares.
const SETTLE: Duration = Duration::from_millis(5);

// @excerpt-begin select_ime_source
#[derive(Debug, PartialEq, Eq)]
enum ImeSource {
    HookFlag,
    TisQuery,
}

fn select_ime_source(dirty: bool, jis_keyboard_seen: bool) -> ImeSource {
    if dirty || jis_keyboard_seen {
        ImeSource::HookFlag
    } else {
        ImeSource::TisQuery
    }
}

// @excerpt-end select_ime_source

/// Substring of the input-source identifier that marks a Japanese input method.
const JAPANESE_SOURCE_MARK: &str = "inputmethod.Japanese";

/// Whether an input-source identifier denotes a Japanese input method.
pub(super) fn is_japanese_source_id(id: &str) -> bool {
    id.contains(JAPANESE_SOURCE_MARK)
}

/// The open/closed state implied by the sources: the flag maintained by the mode keys when it is
/// trusted, otherwise the latest completed query of the input source (the flag itself when no query
/// has completed).
pub(super) fn resolve_open(dirty: bool, jis_keyboard_seen: bool, hook_flag: bool, queried: Option<bool>) -> bool {
    match select_ime_source(dirty, jis_keyboard_seen) {
        ImeSource::HookFlag => hook_flag,
        ImeSource::TisQuery => queried.unwrap_or(hook_flag),
    }
}

/// Writes `open` to the shared flag only if it still holds `read`, the value the poll saw before it
/// asked the input source. Returns whether the write happened.
pub(super) fn publish_open(cell: &AtomicBool, read: bool, open: bool) -> bool {
    cell.compare_exchange(read, open, Ordering::AcqRel, Ordering::Acquire).is_ok()
}

// Text Input Sources (Carbon) and Grand Central Dispatch.
#[allow(non_camel_case_types)]
type TISInputSourceRef = *const c_void;
#[allow(non_camel_case_types)]
type CFStringRef = *const c_void;

#[link(name = "Carbon", kind = "framework")]
extern "C" {
    fn TISCopyCurrentKeyboardInputSource() -> TISInputSourceRef;
    fn TISGetInputSourceProperty(source: TISInputSourceRef, key: CFStringRef) -> *const c_void;
    #[allow(non_upper_case_globals)]
    static kTISPropertyInputSourceID: CFStringRef;
}

#[link(name = "System", kind = "dylib")]
extern "C" {
    #[allow(non_upper_case_globals)]
    static _dispatch_main_q: c_void;
    fn dispatch_async_f(
        queue: *const c_void,
        context: *mut c_void,
        work: unsafe extern "C" fn(*mut c_void),
    );
}

/// Latest completed query: `-1` none yet, `0` not a Japanese source, `1` a Japanese source.
static QUERY_RESULT: AtomicI8 = AtomicI8::new(-1);
/// Whether a query is submitted and has not completed. At most one is outstanding, so a main queue
/// that is never serviced holds a single pending item rather than a growing backlog.
static QUERY_PENDING: AtomicBool = AtomicBool::new(false);
/// Number of queries that have completed on the main queue.
static COMPLETED_QUERIES: AtomicU64 = AtomicU64::new(0);

/// Number of input-source queries completed so far.
pub(super) fn completed_queries() -> u64 {
    COMPLETED_QUERIES.load(Ordering::Relaxed)
}

/// Runs on the main dispatch queue: reads the identifier of the current keyboard input source.
unsafe extern "C" fn query_on_main_queue(_context: *mut c_void) {
    // SAFETY: the Text Input Sources calls run on the main dispatch queue, as they require; the
    // returned source is released once, and the identifier string is only borrowed (get rule).
    let japanese = unsafe {
        let source = TISCopyCurrentKeyboardInputSource();
        if source.is_null() {
            false
        } else {
            let id_ref = TISGetInputSourceProperty(source, kTISPropertyInputSourceID);
            let japanese = !id_ref.is_null()
                && is_japanese_source_id(
                    &CFString::wrap_under_get_rule(id_ref as core_foundation::string::CFStringRef)
                        .to_string(),
                );
            CFRelease(source);
            japanese
        }
    };
    QUERY_RESULT.store(i8::from(japanese), Ordering::Release);
    COMPLETED_QUERIES.fetch_add(1, Ordering::Relaxed);
    QUERY_PENDING.store(false, Ordering::Release);
}

/// Submits a query to the main dispatch queue unless one is already outstanding. Never blocks.
fn submit_query() {
    if QUERY_PENDING.swap(true, Ordering::AcqRel) {
        return;
    }
    // SAFETY: `_dispatch_main_q` is the process-wide main queue; the work function ignores its
    // context, so a null context is valid.
    unsafe {
        dispatch_async_f(
            &raw const _dispatch_main_q,
            std::ptr::null_mut(),
            query_on_main_queue,
        );
    }
}

/// The latest completed query, or `None` when none has completed.
fn latest_query() -> Option<bool> {
    match QUERY_RESULT.load(Ordering::Acquire) {
        0 => Some(false),
        1 => Some(true),
        _ => None,
    }
}

/// Body of the polling thread. Reads the sources at least every [`POLL_INTERVAL`], and sooner when the
/// tap callback signals a key press, until the stop flag or the local stop flag is raised. It writes
/// the open/closed flag only from a completed query of the input source, and only if the flag still
/// holds the value read before the query, so a mode key applied in the meantime is never overwritten.
pub(super) fn poll_loop(wake_rx: Receiver<()>, stop: &AtomicBool, local_stop: &AtomicBool) {
    let mut first_submission: Option<Instant> = None;
    let mut warned = false;
    while !stop.load(Ordering::Acquire) && !local_stop.load(Ordering::Acquire) {
        match wake_rx.recv_timeout(POLL_INTERVAL) {
            Ok(()) => thread::sleep(SETTLE),
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => thread::sleep(POLL_INTERVAL),
        }
        let dirty = IME_STATE_DIRTY.swap(false, Ordering::AcqRel);
        let jis_keyboard_seen = JIS_KEYBOARD_SEEN.load(Ordering::Acquire);
        let hook_flag = IME_OPEN.load(Ordering::Acquire);
        if dirty || jis_keyboard_seen {
            continue;
        }
        submit_query();
        let submitted_at = *first_submission.get_or_insert_with(Instant::now);
        if !warned && submitted_at.elapsed() >= NO_RESULT_WARNING_AFTER && completed_queries() == 0 {
            warned = true;
            tracing::warn!(
                "no input-source query has completed; the main thread may not be servicing the main \
                 dispatch queue, so the open/closed state follows the mode keys only"
            );
        }
        let open = resolve_open(dirty, jis_keyboard_seen, hook_flag, latest_query());
        if open != hook_flag {
            // Give up when a mode key changed the flag since it was read above.
            publish_open(&IME_OPEN, hook_flag, open);
        }
    }
}
