# Scenario emitter

The emitter plays a `plan/1` file (see `../formats/FORMATS.md`) through a key-sending backend and writes an
`oracle/1` log: for every key it sends, the time at which it was actually sent. The oracle records what was
intended and when; it is used only to verify the observed ground truth and is never given to an observer.

| File | Role |
|---|---|
| `emit.py` | Core: timing, oracle writing, oracle validation, command line |
| `backend_dryrun.py` | Sends nothing; used to exercise the core and the timing |
| `backend_macos.py` | Posts synthetic key events through Quartz Event Services (`CGEventPost`, via `ctypes`) |
| `keymap_macos.json` | Key name to macOS virtual key code |

## Usage

```
python3 emit.py --plan PLAN --out ORACLE --backend dryrun
python3 emit.py --plan PLAN --out ORACLE --backend macos --i-understand-this-sends-keys \
    --backend-opt focus_app=<bundle id of the target application>
python3 emit.py --validate ORACLE
python3 emit.py --selftest
python3 backend_macos.py --probe
```

The output file must not exist (an existing oracle is never overwritten). The command line plays the blocks
of a plan in order and does not reset the input method between blocks; a caller that needs a reset uses the
`Emitter` class directly (`write_reset`, then `run_block`) so that the `reset` row precedes `block_begin`.
A plan whose block asks for a reset (`reset_ime` true) is refused by the command line for a real backend
(exit status 2), because the procedure that prepares each cell performs the reset; with `dryrun` the request
is ignored and a note is printed on standard error.

`Emitter(backend, out, plan_sha256, os_name, backend_name, allow_send=False, supported_keys=None)` holds the
gate for real sending. For any backend name other than `dryrun`, entering the context raises `SendRefused`
unless `allow_send` is true and `supported_keys` is given, and it does so before the output file is created
and before the backend is opened. `run_block` checks that every key of the block is in `supported_keys`
before it sends anything. The command line passes `allow_send` only when the flag below is present.

## Safety rules for real sending

- Synthetic keys are delivered to **the application that is in front**. Prepare the target window, and do not
  touch the keyboard or change the front application while a run is in progress.
- A backend other than `dryrun` refuses to start (exit status 2, no output file) unless
  `--i-understand-this-sends-keys` is given; the same rule is enforced inside `Emitter`, so a caller that uses
  the class directly cannot open a real backend by accident. No self-test, probe, or validation path sends a key.
- On `SIGTERM` the command line exits with status 143 through the normal exit path, so the backend releases any
  key that is still held. `SIGKILL` cannot be handled: after it a key may stay pressed, and the oracle is
  incomplete. Check the keyboard state before the next run.
- On macOS the process that runs the emitter (the terminal application) must hold the Accessibility permission.
  Without it the system drops the events silently, so the backend checks the permission on start and refuses
  to run when it is missing. The emitter never changes the permission.
- The `focus_away` and `focus_back` pseudo-keys bring another application to the front through AppleScript.
  `focus_back` requires `--backend-opt focus_app=<bundle id>` and `focus_away` requires
  `--backend-opt away_app=<bundle id>`; neither has a default, and a plan that uses a pseudo-key without its
  option is refused before anything is sent. After each activation the backend reads the front application
  back (up to five readings, 0.2 s apart) and stops the run with an error if it is not the requested one.
- The pseudo-keys consume time (an activation plus the read-back), so the steps after one are sent later than
  the plan says, and consecutive steps may follow each other back to back until the schedule is caught up.
- The backend sets the event flags itself (`CGEventSetFlags`), which can overwrite the flags a key normally
  carries (for example the numeric-pad and function flags of the arrow keys). Whether candidate navigation
  works with the synthetic arrow keys is therefore an item for the on-device pilot, not something the
  self-test can show.
- If `open()` fails (for example the Accessibility permission is missing), an oracle that holds only the header
  has already been written. The next run refuses to overwrite it, so delete that file before running again.
- A plan that uses a key the backend cannot send (for example a key that only another backend provides) is
  refused before anything is sent, and the offending key names are printed.

## Timing

All waiting uses `time.perf_counter()`: the emitter sleeps until 2 ms before the target and busy-waits for the
remainder. It waits only for the plan's `gap_before_ms` (before a trial), `marker_gap_ms` (between marker keys),
and `at_ms` (within a trial, relative to the trial start). The plan reader accepts `at_ms` up to 3,600,000
(one hour), `gap_before_ms` and `marker_gap_ms` up to 3,600,000, and a plan file up to 16 MiB. A marker key is released immediately after its press.
Each `sent` and `marker` row carries `t_wall_ms` (Unix time in milliseconds) and `t_mono_ns` (monotonic clock in
nanoseconds), both taken immediately before dispatch. The default self-test requires that no key is sent before
its planned time and reports the 95th percentile of the difference between actual and planned send time. The
full self-test (`--selftest --full`) also requires that percentile to stay under 20 ms; run it on an otherwise
idle machine, since competing processes delay the wake-up of the emitter (on a heavily loaded machine the
percentile exceeded 20 ms, and fell to about 0.03 ms when the emitter ran at high priority).

Both clocks resolve 1 ms or better. `t_mono_ns` comes from `time.perf_counter_ns()`, the clock that the waits
use. On Windows, `t_wall_ms` comes from `GetSystemTimePreciseAsFileTime`, because `time.time()` and
`time.monotonic()` of some Python versions (3.11 among them) advance there in steps of about 15.6 ms (for
`time.time()`, whenever no process has requested a finer system timer); elsewhere it comes from `time.time_ns()`. If either clock declares a resolution coarser than 1 ms, the emitter stops
before it creates the output file (exit status 2).

## Plan checks before sending

The plan is read completely before the first key: row shapes and integer ranges, unique `block_id` and
`trial_id`, repeated JSON keys, nesting depth, file size, `marker` on every block, `marker_gap_ms` on a block
whose marker is not empty, and within each trial the pairing of presses and releases (no key pressed twice, no
release without a press, no key left pressed at the end). `gap_before_ms` is optional; when it is absent no
wait is inserted before the trial.

## Reading an oracle

Rows appear in this order: header; for each block, an optional `reset`, `block_begin`, one `marker` per marker
key press (`seq` is its index in the block's marker array), then for each trial `trial_begin`, one `sent` per
step (`step` is the index into the plan's `steps`), `trial_end`; finally `block_end`. `--validate` checks the
header, the row kinds and key sets, integer timestamps, integer `step` and `seq` values, key names against
the enumeration, the nesting of blocks and trials, the pairing of presses and releases, that a `reset` row is
directly followed by the `block_begin` of the same `block_id`, and that the monotonic clock does not run
backwards; it prints line numbers and never
prints a value from the file.

## Adding a backend

Provide `backend_<name>.py` with `SUPPORTED_KEYS` (a frozenset of key names, pseudo-keys included) and a
`Backend` class with `__init__(opts)` (unknown options raise), `open()`, `send(key, down)`, and `close()`.
A module may add `KEY_OPTIONS`, a mapping from a key name to the option that key needs.
The core waits; the backend only sends. The core always calls `close()`, including after an exception.
