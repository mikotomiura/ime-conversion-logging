# Environment

Fill in one section per machine that produces or analyses run directories. Record the values at the time
of the run; do not edit them afterwards.

## Analysis machine

| Item | Value |
|---|---|
| Operating system and version | (to be filled in) |
| Python version (`python3 --version`) | (to be filled in) |
| Python packages | none (standard library only) |
| Commit of this repository | (to be filled in) |
| Date of the analysis | (to be filled in) |

## Measuring machines (one row per cell)

| Cell label | Operating system and build | Input method and version | Rust toolchain (`rustc --version`) | Swift version (`swiftc --version`) |
|---|---|---|---|---|
| macos-standard-live-on | macOS 27.0.1 (26A434), Apple Silicon, 10 logical processors | macOS Japanese, Romaji input (JapaneseIM-RomajiTyping 6.3) | rustc 1.99.0 (b940084d7 2026-09-28) | Apple Swift version 6.4 (swiftlang-6.4.0.34.1) |
| macos-standard-live-off | macOS 27.0.1 (26A434), Apple Silicon, 10 logical processors | macOS Japanese, Romaji input (JapaneseIM-RomajiTyping 6.3) | rustc 1.99.0 (b940084d7 2026-09-28) | Apple Swift version 6.4 (swiftlang-6.4.0.34.1) |
| macos-e3-working | macOS 27.0.1 (26A434), Apple Silicon, 10 logical processors | macOS Japanese, Romaji input (JapaneseIM-RomajiTyping 6.3) | rustc 1.99.0 (b940084d7 2026-09-28) | Apple Swift version 6.4 (swiftlang-6.4.0.34.1) |

The `idle` condition of question 5 ran on the same machine with the same build. All macOS runs were made on
2026-10-02 to 2026-10-05 on mains power. The same machine also hosts a runner of a continuous-integration
service, which was left running. Workflow runs of another project that overlapped each run, and the share of the
run's time during which at least one of them was in progress, were: 13 and 23 % for the run with live conversion
on, 9 and 20 % for the run with it off, 7 and 13 % for `idle`, and 1 and 2 % for `working` (from the start and end
times that the service reports for each workflow run; a workflow run also includes a job on another machine, so the
shares are upper bounds for the load on this machine). The pre-registration does not restrict background load; it
is reported as a limitation. The `idle` condition sends no key and has no plan, so it has no line in the table of
run checks in `data.md` (section 3.4); its samples are summarised as section 6.10 describes.

## Lock files

The logger and the reference input fields are built from lock files that are committed with the kit.
Record the SHA-256 of each one used for a run.

| File | SHA-256 |
|---|---|
| `kit/logger/Cargo.lock` | `206d2ed8be683458ca035fed879c057a55cd119e431960a4059544a51621a71b` (macOS runs) |
| `kit/refinput-windows/Cargo.lock` | (to be filled in) |

The macOS reference input field has no lock file; record the Swift version above.
