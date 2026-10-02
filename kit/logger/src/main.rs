//! ime-observation-logger — the core of the standalone input-method observation logger.
//!
//! Reads the atomics exposed by the conversion-session state machine (`input/ime_compose.rs`,
//! `ime_cloak.rs`, `ime_mode.rs`) to write `ime_shadow` lines, and writes `key` lines from the key
//! transitions a driver sends. Output follows format `obs/1`. The observation is content-free: a
//! `key` line carries only one of fourteen categories, and an `ime_shadow` line carries only counts
//! (`dwell_ms`, `nav`, `abandon`).
//!
//! Usage: `ime-observation-logger --out <path> [--duration-s <n>]` or
//! `ime-observation-logger --selftest [--out <path>]`. The operating-system driver that captures key
//! events lives in `driver_macos.rs` / `driver_windows.rs`.

// The Windows driver's low-level keyboard hook calls into the feature-key classifier at the top of its
// callback. `key_gate.rs` is carried as a whole file so that the driver can call it from its own
// excerpted range. It is unused on macOS, so dead code is allowed here.
#[allow(dead_code)]
mod analysis;
mod driver_api;
#[cfg(target_os = "macos")]
mod driver_macos;
#[cfg(windows)]
mod driver_windows;
mod input;
#[allow(dead_code)]
mod key_category;

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use driver_api::DriverCtx;
use input::hook::{
    IME_ACTIVE, IME_CANDIDATE_NAV_COUNT, IME_COMPOSITION_START_MS, IME_SHADOW_ABANDON,
    IME_SHADOW_DWELL_MS, IME_SHADOW_READY,
};
use input::ime_cloak::MONITOR_SAFETY_VALVE_MS;
use input::ime_compose::{self, Chord};
use key_category::KeyCategory;

const LOGGER_VERSION: &str = env!("CARGO_PKG_VERSION");
/// The `os` field of `obs/1` takes only `macos` or `windows`; the core does not run on any other
/// platform (`start_driver` returns an error there instead of silently claiming "windows").
const OS_NAME: &str = if cfg!(target_os = "macos") {
    "macos"
} else if cfg!(windows) {
    "windows"
} else {
    "unsupported"
};
/// Source of the `manifest_sha256` field of the `meta` line. Covers only the core manifest, not a
/// driver's own manifest.
const MANIFEST: &[u8] = include_bytes!("../manifest.json");
/// Polling interval of the monitoring loop: 100 ms.
const POLL_MS: u64 = 100;

// ---------------------------------------------------------------------------
// Line writers (format obs/1). No spaces around separators, no escaping (see FORMATS.md).
// ---------------------------------------------------------------------------

fn meta_line() -> String {
    let mut hex = String::with_capacity(64);
    for b in Sha256::digest(MANIFEST) {
        hex.push_str(&format!("{b:02x}"));
    }
    // `key` is unconditional and `ime_shadow` is config-gated.
    format!(
        r#"{{"type":"meta","format":"obs/1","os":"{OS_NAME}","logger_version":"{LOGGER_VERSION}","manifest_sha256":"{hex}","capabilities":{{"unconditional":["key"],"threshold_gated":[],"config_gated":["ime_shadow"]}}}}"#
    )
}

fn key_line(t: u64, category: KeyCategory, press: bool) -> String {
    format!(
        r#"{{"type":"key","t":{t},"category":"{}","press":{press}}}"#,
        category.as_str()
    )
}

fn shadow_line(t: u64, dwell_ms: u64, nav: u32, abandon: bool) -> String {
    format!(
        r#"{{"type":"ime_shadow","t":{t},"dwell_ms":{dwell_ms},"nav":{nav},"abandon":{abandon}}}"#
    )
}

/// One write plus one flush per line, so a line already written survives an abrupt termination.
fn write_line<W: Write>(out: &mut W, line: &str) -> Result<(), String> {
    writeln!(out, "{line}")
        .and_then(|()| out.flush())
        .map_err(|e| format!("failed to write a line: {e}"))
}

fn wall_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

// ---------------------------------------------------------------------------
// Atomics read and written by the monitoring loop.
//
// In production these refer to the shim's own statics ([`ShadowAtomics::real`]). Tests instead use
// independent atomics allocated with `Box::leak` ([`ShadowAtomics::fresh_for_test`]), so that they do
// not race with the copied state machine's own unit tests, which read and write the real statics in
// parallel.
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
struct ShadowAtomics {
    /// Whether a candidate window (conversion session) is currently open. This is the flag the safety
    /// valve clears.
    active: &'static AtomicBool,
    ready: &'static AtomicBool,
    dwell_ms: &'static AtomicU64,
    nav: &'static AtomicU32,
    abandon: &'static AtomicBool,
    composition_start_ms: &'static AtomicU64,
}

impl ShadowAtomics {
    /// The production instance, referring to the shim's own statics.
    fn real() -> Self {
        Self {
            active: &IME_ACTIVE,
            ready: &IME_SHADOW_READY,
            dwell_ms: &IME_SHADOW_DWELL_MS,
            nav: &IME_CANDIDATE_NAV_COUNT,
            abandon: &IME_SHADOW_ABANDON,
            composition_start_ms: &IME_COMPOSITION_START_MS,
        }
    }

    /// Test instance: atomics unrelated to the real statics (`Box::leak` alone makes them `'static`,
    /// and they occupy separate memory from the shim's own statics). Does not share state with other
    /// tests that run in parallel.
    #[cfg(test)]
    fn fresh_for_test() -> Self {
        Self {
            active: Box::leak(Box::new(AtomicBool::new(false))),
            ready: Box::leak(Box::new(AtomicBool::new(false))),
            dwell_ms: Box::leak(Box::new(AtomicU64::new(0))),
            nav: Box::leak(Box::new(AtomicU32::new(0))),
            abandon: Box::leak(Box::new(AtomicBool::new(false))),
            composition_start_ms: Box::leak(Box::new(AtomicU64::new(0))),
        }
    }
}

fn default_candidate_observer() -> Box<dyn FnMut() -> bool + Send> {
    // Default: read `IME_ACTIVE` directly. Used when a driver returns no observation closure, and by
    // `--selftest`, which runs without a driver. A driver may instead plug in a stronger observer (for
    // example, one combining several window-visibility signals with a staleness reset) through the
    // contract documented in `driver_api.rs`.
    Box::new(|| IME_ACTIVE.load(Ordering::Acquire))
}

// ---------------------------------------------------------------------------
// Monitoring loop (`ime_shadow` lines).
//
// Summary emission and the monitoring thread, omitting an unrelated experiment-mode branch (including
// the ordering of the drain condition used). `t` is the time the summary was drained. The stale path
// and the active-to-false transition path are Windows-only.
//
// Whether a candidate window is open is judged by reading `IME_ACTIVE` directly by default, but a
// driver may plug in a stronger judgment through `DriverHandle.candidate_window_observer` (see the
// contract in `driver_api.rs`).
// ---------------------------------------------------------------------------

fn emit_ime_shadow<W: Write>(
    atomics: &ShadowAtomics,
    out: &mut W,
    t: u64,
    is_windows: bool,
) -> Result<(), String> {
    if atomics.ready.swap(false, Ordering::AcqRel) {
        // Normal path: the summary was already computed by the writer side.
        let dwell_ms = atomics.dwell_ms.swap(0, Ordering::Relaxed);
        let nav = atomics.nav.swap(0, Ordering::Relaxed);
        let abandon = atomics.abandon.swap(false, Ordering::Relaxed);
        return write_line(out, &shadow_line(t, dwell_ms, nav, abandon));
    }
    // The stale path is Windows-only: on macOS it would emit a spurious line partway through a session.
    if is_windows {
        let start = atomics.composition_start_ms.swap(0, Ordering::Relaxed);
        if start > 0 {
            let dwell_ms = t.saturating_sub(start);
            let nav = atomics.nav.swap(0, Ordering::Relaxed);
            let abandon = atomics.abandon.swap(false, Ordering::Relaxed);
            return write_line(out, &shadow_line(t, dwell_ms, nav, abandon));
        }
    }
    Ok(())
}

struct Monitor {
    is_windows: bool,
    last_active: Option<bool>,
    active_since: Option<Instant>,
    next_poll: Instant,
    /// Observation closure for whether a candidate window is open (default: read `IME_ACTIVE`; see
    /// `driver_api.rs`).
    observe_candidate: Box<dyn FnMut() -> bool + Send>,
    atomics: ShadowAtomics,
}

impl Monitor {
    fn new(
        now: Instant,
        is_windows: bool,
        observe_candidate: Box<dyn FnMut() -> bool + Send>,
    ) -> Self {
        Self {
            is_windows,
            last_active: None,
            active_since: None,
            next_poll: now,
            observe_candidate,
            atomics: ShadowAtomics::real(),
        }
    }

    /// Test constructor that also injects the atomics, so it does not race with the real statics.
    #[cfg(test)]
    fn new_for_test(
        now: Instant,
        is_windows: bool,
        observe_candidate: Box<dyn FnMut() -> bool + Send>,
        atomics: ShadowAtomics,
    ) -> Self {
        Self {
            is_windows,
            last_active: None,
            active_since: None,
            next_poll: now,
            observe_candidate,
            atomics,
        }
    }

    /// One cycle of the monitoring loop. Runs at most once per `POLL_MS`. `now` measures elapsed time;
    /// `ts_ms` is the wall-clock time of the drain, used as the `t` of an `ime_shadow` line.
    fn poll<W: Write>(&mut self, now: Instant, ts_ms: u64, out: &mut W) -> Result<(), String> {
        if now < self.next_poll {
            return Ok(());
        }
        self.next_poll = now + Duration::from_millis(POLL_MS);
        self.run_cycle(now, ts_ms, out)
    }

    /// Forces exactly one cycle regardless of the schedule. Called just before the run loop ends, so
    /// that a summary that became ready after the last scheduled `poll` is not lost.
    fn force_poll<W: Write>(
        &mut self,
        now: Instant,
        ts_ms: u64,
        out: &mut W,
    ) -> Result<(), String> {
        self.run_cycle(now, ts_ms, out)
    }

    fn run_cycle<W: Write>(&mut self, now: Instant, ts_ms: u64, out: &mut W) -> Result<(), String> {
        let active = (self.observe_candidate)();

        // Safety valve: if a missed close notification leaves the candidate window open indefinitely,
        // treat it as stale and clear it.
        if active {
            let since = *self.active_since.get_or_insert(now);
            if now.duration_since(since) > Duration::from_millis(MONITOR_SAFETY_VALVE_MS) {
                tracing::warn!(
                    valve_ms = MONITOR_SAFETY_VALVE_MS,
                    "candidate window stale — auto-resetting"
                );
                self.atomics.active.store(false, Ordering::Release);
                self.active_since = None;
                self.last_active = Some(false);
                if self.is_windows {
                    emit_ime_shadow(&self.atomics, out, ts_ms, true)?;
                }
                return Ok(());
            }
        } else {
            self.active_since = None;
        }

        // Active-to-false transition (Windows-only).
        if self.last_active.is_none_or(|prev| prev != active) {
            let was_active = self.last_active.unwrap_or(false);
            self.last_active = Some(active);
            if self.is_windows && was_active && !active {
                emit_ime_shadow(&self.atomics, out, ts_ms, true)?;
            }
        }

        // Direct polling of `IME_SHADOW_READY` (on macOS this is the only path an `ime_shadow` line
        // comes from).
        let ready = self.atomics.ready.load(Ordering::Acquire);
        if ime_compose::should_drain_ready(ready, active, self.is_windows) {
            emit_ime_shadow(&self.atomics, out, ts_ms, self.is_windows)?;
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Driver selection.
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn start_driver(ctx: DriverCtx) -> Result<driver_api::DriverHandle, String> {
    driver_macos::start(ctx)
}
#[cfg(windows)]
fn start_driver(ctx: DriverCtx) -> Result<driver_api::DriverHandle, String> {
    driver_windows::start(ctx)
}
#[cfg(not(any(target_os = "macos", windows)))]
fn start_driver(_ctx: DriverCtx) -> Result<driver_api::DriverHandle, String> {
    Err(
        "no driver exists for this operating system (only macOS and Windows are supported)"
            .to_string(),
    )
}

fn run(out_path: &Path, duration: Option<Duration>) -> Result<(), String> {
    let (tx, rx) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let handle = start_driver(DriverCtx {
        keys: tx,
        stop: Arc::clone(&stop),
    })
    .map_err(|e| format!("cannot start the driver: {e}"))?;

    // The output file is opened only after the driver has started, and never overwrites an existing
    // observation log. If it cannot be opened, the driver is stopped and the run ends.
    let mut out = match OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(out_path)
    {
        Ok(f) => f,
        Err(e) => {
            stop.store(true, Ordering::Release);
            let _ = handle.thread.join();
            return Err(format!(
                "cannot open the output path (an existing observation log is never overwritten): {e}"
            ));
        }
    };
    write_line(&mut out, &meta_line())?;

    let started = Instant::now();
    let observe = handle
        .candidate_window_observer
        .unwrap_or_else(default_candidate_observer);
    let mut monitor = Monitor::new(started, cfg!(windows), observe);
    tracing::info!(out = %out_path.display(), "logging started");
    let mut result = Ok(());
    while duration.is_none_or(|d| started.elapsed() < d) {
        // An unexpected exit of the driver's thread ends the run with an error, per the stop contract
        // in `driver_api.rs`.
        if handle.thread.is_finished() {
            result = Err("the driver's thread has ended unexpectedly".to_string());
            break;
        }
        match rx.recv_timeout(Duration::from_millis(POLL_MS)) {
            Ok(k) => {
                // The virtual-key code is folded into one of fourteen categories before it is written;
                // the code itself is never written.
                result = write_line(
                    &mut out,
                    &key_line(k.t, KeyCategory::from_vk(k.vk), k.press),
                );
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                result = Err("the driver has ended (its key sender was closed)".to_string());
            }
        }
        if result.is_ok() {
            result = monitor.poll(Instant::now(), wall_ms(), &mut out);
        }
        if result.is_err() {
            break;
        }
    }
    // One final, unscheduled cycle just before ending, so that a summary that became ready after the
    // last scheduled poll is not lost.
    if result.is_ok() {
        result = monitor.force_poll(Instant::now(), wall_ms(), &mut out);
    }
    stop.store(true, Ordering::Release);
    let _ = handle.thread.join();
    result
}

/// Runs a recording while the main thread services the main dispatch queue.
///
/// The macOS driver submits its query of the current keyboard input source to the main dispatch
/// queue, which is serviced only while the process's main thread runs its run loop. The recording
/// therefore runs on a helper thread, and the main thread runs the main run loop in slices of at most
/// 100 ms until the recording ends. Without this, an input source selected before the start, or
/// switched by any means other than a mode key, would never be observed.
#[cfg(target_os = "macos")]
fn run_with_main_queue(path: PathBuf, duration: Option<Duration>) -> Result<(), String> {
    use core_foundation::runloop::{kCFRunLoopDefaultMode, CFRunLoop, CFRunLoopRunResult};

    let recording = std::thread::Builder::new()
        .name("recording".to_string())
        .spawn(move || run(&path, duration))
        .map_err(|e| format!("cannot start the recording thread: {e}"))?;
    while !recording.is_finished() {
        // SAFETY: `kCFRunLoopDefaultMode` is an immutable constant exported by CoreFoundation.
        let mode = unsafe { kCFRunLoopDefaultMode };
        let r = CFRunLoop::run_in_mode(mode, Duration::from_millis(POLL_MS), true);
        if matches!(r, CFRunLoopRunResult::Finished) {
            // The run loop had nothing to wait on and returned at once; avoid spinning.
            std::thread::sleep(Duration::from_millis(POLL_MS));
        }
    }
    recording
        .join()
        .unwrap_or_else(|_| Err("the recording thread panicked".to_string()))
}

/// On other platforms the recording runs on the main thread.
#[cfg(not(target_os = "macos"))]
fn run_with_main_queue(path: PathBuf, duration: Option<Duration>) -> Result<(), String> {
    run(&path, duration)
}

// ---------------------------------------------------------------------------
// --selftest (platform-independent): drives the state machine directly with a fixed key sequence,
// without a driver, runs the monitoring loop, and checks the log it wrote back.
// ---------------------------------------------------------------------------

fn selftest(path: &Path) -> Result<(), String> {
    const T0: u64 = 1_700_000_000_000;
    // (milliseconds since T0, virtual-key code): `k a` -> Space -> Enter (committed, 1500ms) / `k` ->
    // Esc (abandoned, 200ms).
    let sessions: [&[(u64, u32)]; 2] = [
        &[(0, 0x4B), (120, 0x41), (400, 0x20), (1500, 0x0D)],
        &[(3000, 0x4B), (3200, 0x1B)],
    ];
    let drained_at = [T0 + 2000, T0 + 3500];

    let mut out = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
        .map_err(|e| format!("cannot open the selftest output path: {e}"))?;
    write_line(&mut out, &meta_line())?;
    let base = Instant::now();
    let mut monitor = Monitor::new(base, cfg!(windows), default_candidate_observer());
    for (i, keys) in sessions.iter().enumerate() {
        for &(dt, vk) in keys.iter() {
            write_line(&mut out, &key_line(T0 + dt, KeyCategory::from_vk(vk), true))?;
            ime_compose::drive(vk, true, Chord::Plain, T0 + dt);
        }
        let now = base + Duration::from_millis(POLL_MS * 2 * (i as u64 + 1));
        monitor.poll(now, drained_at[i], &mut out)?;
    }

    // Windows-only safety-valve path. `K` then `Space` opens the candidate window (`active == true`);
    // neither Enter nor Escape is then sent, so the candidate window stays open past
    // `MONITOR_SAFETY_VALVE_MS`. `K` alone does not raise `active`, since the state machine's
    // transition table only raises it once a candidate is displayed (after `Space`). When
    // `cfg!(windows)` is false (a macOS build), `Monitor.is_windows` is false, so only the reset
    // happens and no line is emitted: the valve's reset is shared by both platforms, but emitting a
    // shadow line from it is Windows-only (see the doc comment of `emit_ime_shadow`).
    let k3_at = T0 + 4000;
    write_line(&mut out, &key_line(k3_at, KeyCategory::from_vk(0x4B), true))?;
    ime_compose::drive(0x4B, true, Chord::Plain, k3_at); // Starts the session (not yet active).
    write_line(
        &mut out,
        &key_line(k3_at + 50, KeyCategory::from_vk(0x20), true),
    )?;
    ime_compose::drive(0x20, true, Chord::Plain, k3_at + 50); // Space -> candidate shown (active = true).
    let compose_poll = base + Duration::from_millis(POLL_MS * 6);
    monitor.poll(compose_poll, k3_at + 50, &mut out)?; // Only stamps active_since; valve not yet tripped.
    let valve_ts = k3_at + MONITOR_SAFETY_VALVE_MS + 1_000;
    let valve_poll = compose_poll + Duration::from_millis(MONITOR_SAFETY_VALVE_MS + 1_000);
    monitor.poll(valve_poll, valve_ts, &mut out)?; // The valve trips here (one line, Windows only).

    drop(out);

    let text =
        std::fs::read_to_string(path).map_err(|e| format!("cannot read the log back: {e}"))?;
    let lines: Vec<&str> = text.lines().collect();
    let count = |needle: &str| lines.iter().filter(|l| l.contains(needle)).count();
    let expect_shadow_lines: usize = if cfg!(windows) { 3 } else { 2 };
    let expect_key_lines: usize = 8;
    let expect_alphabetic: usize = 4;
    let expect: Vec<(bool, String)> = vec![
        (
            lines.len() == 1 + expect_key_lines + expect_shadow_lines,
            format!(
                "line count is not {} (meta 1 + key {expect_key_lines} + ime_shadow {expect_shadow_lines})",
                1 + expect_key_lines + expect_shadow_lines
            ),
        ),
        (
            lines.first().is_some_and(|l| l.starts_with(r#"{"type":"meta","format":"obs/1""#)),
            "the first line is not an obs/1 meta line".to_string(),
        ),
        (count(r#""type":"key""#) == expect_key_lines, format!("key lines are not {expect_key_lines}")),
        (
            count(r#""category":"alphabetic""#) == expect_alphabetic,
            format!("alphabetic key lines are not {expect_alphabetic}"),
        ),
        (lines.contains(&shadow_line(drained_at[0], 1500, 0, false).as_str()), "the ime_shadow line for the committed session is missing".to_string()),
        (lines.contains(&shadow_line(drained_at[1], 200, 0, true).as_str()), "the ime_shadow line for the abandoned session is missing".to_string()),
        (
            !cfg!(windows) || lines.contains(&shadow_line(valve_ts, MONITOR_SAFETY_VALVE_MS + 1_000, 0, false).as_str()),
            "the stale ime_shadow line from the safety valve is missing (Windows only)".to_string(),
        ),
        (lines.iter().all(|l| !l.contains(' ') && !l.contains('\\')), "a line contains a space or an escape".to_string()),
        (!IME_SHADOW_READY.load(Ordering::Acquire), "IME_SHADOW_READY is still set".to_string()),
    ];
    let failed: Vec<&str> = expect
        .iter()
        .filter(|(ok, _)| !ok)
        .map(|(_, why)| why.as_str())
        .collect();
    if failed.is_empty() {
        tracing::info!(lines = lines.len(), "selftest ok");
        Ok(())
    } else {
        Err(format!("selftest failed: {}", failed.join(" / ")))
    }
}

// ---------------------------------------------------------------------------
// Command-line arguments.
// ---------------------------------------------------------------------------

#[derive(Debug, PartialEq)]
struct Args {
    out: Option<PathBuf>,
    duration_s: Option<u64>,
    selftest: bool,
}

fn parse_args(args: &[String]) -> Result<Args, String> {
    let mut parsed = Args {
        out: None,
        duration_s: None,
        selftest: false,
    };
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--selftest" => parsed.selftest = true,
            "--out" => {
                // Passing --out twice is a usage error.
                if parsed.out.is_some() {
                    return Err("--out cannot be given twice".to_string());
                }
                parsed.out = Some(PathBuf::from(it.next().ok_or("--out requires a path")?));
            }
            "--duration-s" => {
                let n = it
                    .next()
                    .ok_or("--duration-s requires a number of seconds")?;
                parsed.duration_s = Some(
                    n.parse()
                        .map_err(|_| "--duration-s must be a non-negative integer")?,
                );
            }
            other => {
                return Err(format!(
                    "unknown argument: {other} (--out <path> / --duration-s <n> / --selftest)"
                ))
            }
        }
    }
    if !parsed.selftest && parsed.out.is_none() {
        return Err("--out <path> is required".to_string());
    }
    Ok(parsed)
}

fn main() {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let parsed = match parse_args(&args) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("{e}");
            std::process::exit(2);
        }
    };
    let result = if parsed.selftest {
        // The default output path (used when --out is not given) is removed after the run.
        let used_default = parsed.out.is_none();
        let path = parsed
            .out
            .unwrap_or_else(|| std::env::temp_dir().join("ime-observation-selftest.jsonl"));
        let r = selftest(&path);
        if used_default {
            let _ = std::fs::remove_file(&path);
        }
        r
    } else {
        let path = parsed.out.unwrap_or_default();
        run_with_main_queue(path, parsed.duration_s.map(Duration::from_secs))
    };
    if let Err(e) = result {
        tracing::error!("{e}");
        std::process::exit(1);
    }
}

// The tests below never touch the real atomics (the shim's statics), because the copied state
// machine's own unit tests use the same statics in parallel. Tests that use `Monitor` instead use
// `ShadowAtomics::fresh_for_test()` (independent atomics allocated with `Box::leak`). Exercising the
// state machine itself is left to `--selftest`, run as a separate process.
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lines_have_no_spaces_or_escapes() {
        for l in [
            meta_line(),
            key_line(1, KeyCategory::Other, false),
            shadow_line(1, 2, 3, true),
        ] {
            assert!(!l.contains(' ') && !l.contains('\\'), "{l}");
        }
    }

    #[test]
    fn meta_line_carries_the_two_line_kinds_and_a_64_hex_manifest_hash() {
        let l = meta_line();
        assert!(
            l.contains(r#""unconditional":["key"]"#)
                && l.contains(r#""config_gated":["ime_shadow"]"#)
        );
        let hex = l
            .split(r#""manifest_sha256":""#)
            .nth(1)
            .and_then(|s| s.split('"').next())
            .unwrap_or("");
        assert!(
            hex.len() == 64 && hex.bytes().all(|b| b.is_ascii_hexdigit()),
            "{hex}"
        );
    }

    #[test]
    fn key_line_writes_a_category_never_the_vk() {
        assert_eq!(
            key_line(5, KeyCategory::from_vk(0x41), true),
            r#"{"type":"key","t":5,"category":"alphabetic","press":true}"#
        );
    }

    #[test]
    fn args_need_out_unless_selftest() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert!(parse_args(&s(&[])).is_err());
        assert!(parse_args(&s(&["--selftest"])).is_ok());
        assert_eq!(
            parse_args(&s(&["--out", "a", "--duration-s", "3"]))
                .unwrap()
                .duration_s,
            Some(3)
        );
        assert!(parse_args(&s(&["--out", "a", "--nope"])).is_err());
    }

    #[test]
    fn args_reject_out_given_twice() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        let e = parse_args(&s(&["--out", "a", "--out", "b"])).unwrap_err();
        assert!(e.contains("twice"), "{e}");
    }

    // -----------------------------------------------------------------
    // `Monitor`: uses independent atomics (`ShadowAtomics::fresh_for_test`), so it does not race with
    // the real atomics that other unit tests read and write in parallel.
    // -----------------------------------------------------------------

    #[test]
    fn poll_uses_injected_candidate_observer_not_the_real_ime_active_atomic() {
        // With an injected observation closure, poll follows its return value and never touches or
        // depends on the real IME_ACTIVE atomic.
        let atomics = ShadowAtomics::fresh_for_test();
        let now = Instant::now();
        let mut monitor = Monitor::new_for_test(now, false, Box::new(|| true), atomics);
        let mut out: Vec<u8> = Vec::new();
        monitor.poll(now, 1, &mut out).expect("poll");
        assert_eq!(
            monitor.last_active,
            Some(true),
            "poll should follow the injected observer's return value"
        );
    }

    #[test]
    fn poll_before_next_poll_time_is_a_no_op() {
        let atomics = ShadowAtomics::fresh_for_test();
        let now = Instant::now();
        let mut monitor = Monitor::new_for_test(now, false, Box::new(|| false), atomics);
        let mut out: Vec<u8> = Vec::new();
        monitor.poll(now, 1, &mut out).unwrap();
        assert_eq!(monitor.last_active, Some(false));
        // Below POLL_MS, the next poll is a no-op and last_active does not change.
        monitor
            .poll(now + Duration::from_millis(1), 2, &mut out)
            .unwrap();
        assert_eq!(monitor.last_active, Some(false));
    }

    #[test]
    fn windows_active_to_false_transition_emits_ime_shadow_from_ready() {
        // Exercises the active-to-false transition path with READY already set.
        let atomics = ShadowAtomics::fresh_for_test();
        atomics.ready.store(true, Ordering::Release);
        atomics.dwell_ms.store(777, Ordering::Relaxed);
        atomics.nav.store(2, Ordering::Relaxed);
        atomics.abandon.store(false, Ordering::Relaxed);
        let now = Instant::now();
        let mut calls = vec![true, false].into_iter();
        let mut monitor = Monitor::new_for_test(
            now,
            true,
            Box::new(move || calls.next().unwrap_or(false)),
            atomics,
        );
        let mut out: Vec<u8> = Vec::new();
        monitor.poll(now, 100, &mut out).unwrap();
        monitor
            .poll(now + Duration::from_millis(200), 200, &mut out)
            .unwrap();
        let text = String::from_utf8(out).unwrap();
        assert_eq!(
            text.lines().collect::<Vec<_>>(),
            vec![shadow_line(200, 777, 2, false)]
        );
    }

    #[test]
    fn windows_stale_path_uses_composition_start_ms_when_ready_is_absent() {
        // Exercises the stale path: no READY, dwell computed from composition_start_ms.
        let atomics = ShadowAtomics::fresh_for_test();
        atomics.composition_start_ms.store(1_000, Ordering::Relaxed);
        let now = Instant::now();
        let mut calls = vec![true, false].into_iter();
        let mut monitor = Monitor::new_for_test(
            now,
            true,
            Box::new(move || calls.next().unwrap_or(false)),
            atomics,
        );
        let mut out: Vec<u8> = Vec::new();
        monitor.poll(now, 100, &mut out).unwrap();
        monitor
            .poll(now + Duration::from_millis(200), 5_100, &mut out)
            .unwrap();
        let text = String::from_utf8(out).unwrap();
        assert_eq!(
            text.lines().collect::<Vec<_>>(),
            vec![shadow_line(5_100, 4_100, 0, false)]
        );
    }

    #[test]
    fn windows_safety_valve_resets_active_and_emits_stale_line() {
        // Exercises the safety-valve path.
        let atomics = ShadowAtomics::fresh_for_test();
        atomics.active.store(true, Ordering::Release);
        atomics.composition_start_ms.store(2_000, Ordering::Relaxed);
        let now = Instant::now();
        let mut monitor = Monitor::new_for_test(now, true, Box::new(|| true), atomics);
        let mut out: Vec<u8> = Vec::new();
        monitor.poll(now, 100, &mut out).unwrap(); // Stamps active_since; the valve has not tripped yet.
        assert!(
            out.is_empty(),
            "a line was emitted before the valve tripped"
        );
        let after = now + Duration::from_millis(MONITOR_SAFETY_VALVE_MS + 1_000);
        monitor.poll(after, 30_100, &mut out).unwrap(); // The valve trips.
        assert!(
            !atomics.active.load(Ordering::Acquire),
            "the valve should clear the candidate-window atomic"
        );
        let text = String::from_utf8(out).unwrap();
        assert_eq!(
            text.lines().collect::<Vec<_>>(),
            vec![shadow_line(30_100, 28_100, 0, false)]
        );
    }

    #[test]
    fn mac_ready_drains_regardless_of_active_transition() {
        // On macOS there is no active-to-false transition path at all. An ime_shadow line comes only
        // from directly polling READY, drained independently of the value of `active` (the state
        // machine's own drain condition looks only at `ready` on that platform).
        let atomics = ShadowAtomics::fresh_for_test();
        atomics.ready.store(true, Ordering::Release);
        atomics.dwell_ms.store(50, Ordering::Relaxed);
        let now = Instant::now();
        let mut calls = vec![true, false].into_iter();
        let mut monitor = Monitor::new_for_test(
            now,
            false,
            Box::new(move || calls.next().unwrap_or(false)),
            atomics,
        );
        let mut out: Vec<u8> = Vec::new();
        // The first poll (active=true) drains immediately.
        monitor.poll(now, 100, &mut out).unwrap();
        // The second poll (active=false) emits nothing, since READY was already consumed.
        monitor
            .poll(now + Duration::from_millis(200), 200, &mut out)
            .unwrap();
        let text = String::from_utf8(out).unwrap();
        assert_eq!(
            text.lines().collect::<Vec<_>>(),
            vec![shadow_line(100, 50, 0, false)]
        );
    }

    #[test]
    fn force_poll_ignores_the_poll_schedule() {
        // A force_poll just before the run ends does not wait for next_poll.
        let atomics = ShadowAtomics::fresh_for_test();
        atomics.ready.store(true, Ordering::Release);
        atomics.dwell_ms.store(9, Ordering::Relaxed);
        let now = Instant::now();
        let mut monitor = Monitor::new_for_test(now, false, Box::new(|| false), atomics);
        let mut out: Vec<u8> = Vec::new();
        // An ordinary poll does nothing before next_poll (= now), but force_poll, given the same
        // timestamp, is expected to run exactly one cycle: this guarantees a final cycle even for a
        // run of zero duration.
        monitor.force_poll(now, 100, &mut out).unwrap();
        let text = String::from_utf8(out).unwrap();
        assert_eq!(
            text.lines().collect::<Vec<_>>(),
            vec![shadow_line(100, 9, 0, false)]
        );
    }
}
