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
| (to be filled in) | | | | |

## Lock files

The logger and the reference input fields are built from lock files that are committed with the kit.
Record the SHA-256 of each one used for a run.

| File | SHA-256 |
|---|---|
| `kit/logger/Cargo.lock` | (to be filled in) |
| `kit/refinput-windows/Cargo.lock` | (to be filled in) |

The macOS reference input field has no lock file; record the Swift version above.
