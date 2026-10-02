# Observing Japanese IME conversion without recording text: Observability limits, a cross-platform logger, and a synthetic-input validation

This directory holds the artifacts of a single paper on observing Japanese IME conversion without
recording its text: the limits of observability, a cross-platform standalone logger, and a
synthetic-input validation. Raw logs are not committed to the repository; they are deposited
separately, and their locations and SHA-256 digests are recorded in `experiment/data.md`.

## Layout

| Directory | Role |
|---|---|
| `kit/` | The reproducible toolset (log formats, generator, emitter, reference input field, standalone logger, analysis). |
| `experiment/` | Experiment records (SEED / env.md / data.md / run.sh / notes.md). Raw logs are not stored here (`experiment/raw/` is ignored); the deposit location and its SHA-256 are recorded in `data.md`. |
| `manuscript/` | The manuscript. |

## License

Split by kind, not by directory.

- Code (source in `.py` / `.sh` / `.swift` / `.rs`, etc.): Apache-2.0
- Data (including the phrases and readings in `kit/corpus/data/`) and documents (`*.md`, the
  records under `experiment/`, `manuscript/`): CC BY 4.0

## Component staging areas

Subsequent components are built at the following locations.

| Directory | Contents |
|---|---|
| `kit/formats/` | Log-format contracts and their validators |
| `kit/corpus/` | Aozora Bunko phrases and their readings |
| `kit/gen/` | Scenario generator |
| `kit/emit/` | Emitter (synthetic key dispatch and the oracle) |
| `kit/run/` | Single-cell orchestration |
| `kit/refinput-macos/` / `kit/refinput-windows/` | Reference input field |
| `kit/logger/` | Standalone logger |
| `kit/pilot/` | Calibration pilot tooling |
| `kit/analysis/` | Ground-truth assembly, matching, L1, L2, pipeline |
| `kit/e2/` / `kit/e3/` | Leakage evaluation / execution cost |

## Verification

```bash
bash papers/ime-observation/kit/check.sh              # run every selftest under kit/ (a no-op counts as red)
bash papers/ime-observation/kit/check.sh --compile    # py_compile every .py under kit/
bash papers/ime-observation/kit/check.sh --self-test  # this entry point's own negative controls
```

- Only the `.py` files under `kit/` that carry the `# kit: selftest` marker comment are run with
  `--selftest` (unmarked helper modules are only compiled).
- The entry point runs on every verify and on every acceptance check, so **selftests are written to
  be fast by default** (under 30 seconds each; exceeding that triggers a warning). Heavy checks are
  split into something like `--selftest --full` and are not run by default.
- The log-format contract is [`kit/formats/FORMATS.md`](kit/formats/FORMATS.md). The **shape** of
  `gt` and `obs` rows (string values fit an enumeration or regular expression, and there are no
  unknown keys or escapes) is verified mechanically by `kit/formats/validate_logs.py`. The meaning
  of integer values, version strings, and `manifest_sha256` is out of scope; keeping the log
  content-free is the producing side's responsibility (writing only kind, length, and time), as
  described in FORMATS.md's "Scope of guarantee".
- The marker is written as a comment where **the entire line reads `# kit: selftest`** (a marker
  inside a string literal is not picked up).
