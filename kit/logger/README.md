# ime-observation-logger

A standalone logger that produces the observations used by the evaluation. Output follows format
`obs/1` (see `../formats/FORMATS.md`).

The observation is content-free by construction: a `key` line carries only one of fourteen categories
(which key was pressed is discarded), and an `ime_shadow` line carries only counts (`dwell_ms`, `nav`,
`abandon`). No character, candidate string, reading or window name is recorded.

## Driver contract (`src/driver_api.rs`)

Each driver exposes `pub fn start(ctx: DriverCtx) -> Result<DriverHandle, String>`. It sends every
observed key transition as `KeyEvent { t, vk, press }` (`t` is wall-clock time in milliseconds; `vk` is
mapped to the Windows virtual-key space) to `ctx.keys`, and exchanges conversion-session state through
the atomics exposed by `src/input/hook.rs`.

- **Stop**: once `ctx.stop` is raised, a driver must end its own thread within
  `driver_api::STOP_DEADLINE_MS` (500 ms by default). The core loop watches driver liveness with
  `JoinHandle::is_finished()` and ends with an error if a driver thread terminates unexpectedly.
- **Candidate-window observation hook**: returning `Some(Box<dyn FnMut() -> bool + Send>)` in
  `DriverHandle.candidate_window_observer` lets the core's monitoring loop (`Monitor::poll`) call it
  once per cycle to judge whether a candidate window is currently open (`None`, the default, falls back
  to reading `IME_ACTIVE` directly). The **calling thread** is whichever thread calls `Monitor::poll`
  (which may differ from a driver's own key-capture thread). A driver whose observer relies on a
  COM-based accessibility API must perform that API's initialization on its first call, on the calling
  thread (see the module documentation of `driver_api.rs` for the reasoning).

## Build, selftest, verification

```bash
# From the repository root.
cargo test  --manifest-path papers/ime-observation/kit/logger/Cargo.toml      # includes the copied state machine's tests
cargo build --release --manifest-path papers/ime-observation/kit/logger/Cargo.toml
papers/ime-observation/kit/logger/target/release/ime-observation-logger --selftest --out /tmp/obs.jsonl
python3 papers/ime-observation/kit/formats/validate_logs.py --kind obs /tmp/obs.jsonl

python3 papers/ime-observation/kit/logger/check_manifest.py --self               # every listed file or excerpt matches the manifest's recorded SHA-256
python3 papers/ime-observation/kit/logger/check_manifest.py --selftest           # negative controls for the verifier itself
```

Run as: `ime-observation-logger --out <path> [--duration-s <n>]` (until a driver is present for the
current platform, this ends with "not implemented"). The output path is opened with
`OpenOptions::new().write(true).create_new(true)` (**an existing observation log is never overwritten**;
the path is opened only after the driver has started successfully). Passing `--out` twice, or a negative
or non-integer `--duration-s`, is a usage error (exit 2). `--selftest` removes its default output path
(used when `--out` is not given) after it runs.

## Limitations

- SHA-256 is computed after normalizing CRLF to LF, so this check cannot by itself distinguish a
  pure line-ending change.
- `Cargo.lock` is resolved once and then kept fixed, so that every dependency is pinned to a single
  version. Bumping a version or adding a crate should re-resolve from that same starting lockfile with
  `cargo check`, rather than running `cargo update` or `cargo generate-lockfile` with no arguments,
  which would resolve everything to its latest version.
- Building this logger on Windows does not compile the `#[cfg(target_os = "macos")]` side.
