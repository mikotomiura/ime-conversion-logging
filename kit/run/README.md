# Cell runner

`run_cell.py` measures one cell (operating system x input method x settings) with one command. It starts the
standalone observer and the reference input field as child processes, resets the input method at the start of
each block that asks for it, brings the reference input field to the front, plays a plan through the scenario
emitter (`../emit/emit.py`), stops the children, and leaves everything in one run directory. The runner uses
only the Python standard library.

## Usage

```
python3 run_cell.py --cell CELL.json --plan PLAN.jsonl --run-dir runs/<name>
python3 run_cell.py --cell CELL.json --plan PLAN.jsonl --run-dir runs/<name> --i-understand-this-sends-keys
python3 run_cell.py --selftest
```

- The run directory must not exist; it is created by the runner and an existing one is never reused or
  changed. Run directories are ignored by git (`runs/`).
- The flag `--i-understand-this-sends-keys` is the consent to every operation that changes the state of the
  machine, that is, to typing, to the reset, and to the focus command (which may press a key). A backend other
  than `dryrun` sends real key events to the application in front, and the runner refuses to start (exit status
  2) without the flag. `reset_cmd` and `focus_cmd` are run only with the flag, also with `dryrun`; without it
  neither is run, no `reset` row is written to the oracle, and the manifest records each reset the plan asks for,
  and each focus command when `focus_cmd` is not empty, as skipped, with the reason.
- A real backend needs the backend option that its `focus_back` key uses (`focus_app` on macOS, `focus_window`
  on Windows) in `backend_opts`; without it the runner exits with status 2. The flag check, the check of the
  cell file and the plan, the clock check, and the check that the run directory does not exist all come before
  the first child process is started.
- Exit status: 0 the run completed; 1 the run was aborted (or the manifest could not be written); 2 the run was
  refused or could not start.

## Before a measurement

Check these by hand; the runner records the statements in the cell file and cannot verify them.

1. Time synchronisation is off for the duration of the run (`clock_sync_disabled`), so the wall clock does not
   step. Automatic updates of the operating system and of the input method are off (`auto_update_disabled`).
2. The operating system version and the input method's name and version are written into `ime` (they are copied
   into the manifest).
3. Nothing else is in front of the reference input field, and nobody touches the keyboard or the pointer while a
   real backend is sending: synthetic keys go to whatever application is in front.
4. Sleep, the screen lock, and the screen saver are turned off for the duration of the run; a locked screen
   receives the synthetic keys instead of the reference input field. On macOS the terminal application that runs
   the runner holds the Accessibility permission and, when `focus_cmd` drives System Events (as in
   `cell.example.json`), the Automation permission for System Events; both permissions can be withdrawn after the
   measurement.

   The input method is on (for example Japanese, not the Latin layout) when each block starts. The plan does not
   switch it on; a `focus_cmd` that brings the field forward and then presses the input-method-on key does, provided
   the system keeps one input source for all documents (on macOS, "Automatically switch to a document's input source"
   is off). Record in `ime_settings` how the input method was switched on and whether live conversion is on, and
   build the plan from the recipes file for that setting (on macOS, `recipes.json` with live conversion on and
   `recipes.macos-live-off.json` with it off); the runner cannot check that the plan and the machine agree.
5. The marker keys can trigger the global hotkeys of other applications; close or reconfigure any application that
   registers them. Markers must be keys the observer records (see `../pilot/README.md`); arrow keys are used.
6. `reset_cmd`, when a plan requests a reset, really clears the input method's dictionary and learning, and
   `focus_cmd` really brings the reference input field to the front. Both differ by system and input method and are
   checked in a calibration pilot. In `cell.example.json`, the version in `ime` is a placeholder. `reset_cmd` is
   empty and the `learning` entry of `ime_settings` records that the learning is not reset: no method of resetting
   it was found for the macOS input method, and the measurement plans request no reset (an empty `reset_cmd` with a
   plan that does request one is recorded as `skipped`). The `predictive_candidates` entry records the input
   method's default, which the pilot did not change. The other commands and options are the ones used in the macOS
   calibration pilot, where the input method's learning was not reset either, except lines added to `focus_cmd`
   after the pilot: they check, with the query the emitter uses to read the front application (up to five reads 0.2
   seconds apart, as the emitter does), that the reference input field is in front before the input-method key is
   pressed, and fail otherwise (which aborts the run before the block). The bundle identifier of the reference input
   field appears three times in the example (`focus_cmd` twice and `backend_opts.focus_app`); if it is changed through
   `REFINPUT_BUNDLE_ID` when building, change all three.
7. A cell file is executable configuration: it names programs that are run with your privileges. Run only cell
   files that you have read, and never one from another person unread.
8. The child programs must be the programs themselves, not wrapper scripts (on Windows a `.bat` or `.cmd` file
   is refused), so that stopping the child stops the observer. Each child must hand every record to the
   operating system as it is produced (no buffering until exit), so that the lines written before a forced end
   remain in the log.

## The cell file

A JSON object. Unknown keys and missing keys are refused.

| Key | Meaning |
|---|---|
| `os` | `macos` or `windows`; must equal the plan's `os` |
| `ime` | name and version of the input method (a string) |
| `ime_settings` | the settings of the input method for this cell (an object; copied into the manifest) |
| `refinput_cmd` | argument array that starts the reference input field; `{out}` is replaced by the absolute path of its log |
| `logger_cmd` | argument array that starts the observer; `{out}` is replaced by the absolute path of its log |
| `reset_cmd` | argument array run before each block whose plan row has `reset_ime` true, and only with the send flag; empty means no reset (recorded as skipped) |
| `focus_cmd` | optional preparation, an argument array run before each block, and only with the send flag; empty means nothing is run. It runs before the runner confirms which application is in front, so a key it presses (as in `cell.example.json`, the key that switches the input method on) reaches whatever application is in front at that moment, unless the command checks the front application first, as `cell.example.json` does |
| `ready_wait_ms` | how long to wait after starting the children before the first block |
| `backend` | `dryrun`, `macos`, or `windows` |
| `clock_sync_disabled`, `auto_update_disabled` | statements by the person who prepared the machine |
| `label` | optional; the name of the cell in the analysis (1 to 64 characters from letters, digits and `_ . + -`, for example `macos-live-on`). The analysis requires it and requires it to be unique among the runs that were not aborted, so every cell of a measurement matrix needs one |
| `backend_opts` | optional for `dryrun`; for a real backend it must give the option of its `focus_back` key (the application or window to bring to the front) |
| `stop_wait_ms` | optional, default 5000; how long a child has to end after the termination request |
| `settle_ms` | optional, default 1000; a wait after the last block, before the children are stopped |
| `command_timeout_ms` | optional, default 60000; the limit for `reset_cmd` and `focus_cmd` |

Commands are run without a shell, with standard input closed and standard output discarded.

## Sequence

1. The plan and the cell are read and checked (the plan is read once, and the bytes that were checked are the
   bytes that are copied); a real backend is loaded and checked against the plan's keys and options, and the
   clock resolution is checked.
2. The run directory is created and the plan is copied into it.
3. The observer and then the reference input field are started, each in its own process group (POSIX); the
   runner waits `ready_wait_ms`.
4. For each block: `reset_cmd` (if the block asks for it and the flag was given) with its exit status written
   to the oracle as a `reset` row; then `focus_cmd`; then, with a real backend, the front window is confirmed
   (the backend brings the reference input field to the front and reads the front window back; if it differs,
   nothing is sent to the block); then the block is played. A focus command that cannot start, times out, or
   exits with a non-zero status aborts the run, and so does a failed confirmation, because the keys would go to
   the wrong window. A reset command that fails is recorded with its kind of failure (`could_not_start`,
   `timed_out`, `exit_status`) and the run continues (the analysis decides what to exclude). The standard error
   of the two commands is appended to `reset.stderr.txt` and `focus.stderr.txt`.
5. After `settle_ms`, the children are stopped in the reverse of the order in which they were started, each with
   its descendants. On POSIX the process group receives `SIGTERM`, and the child is recorded as `terminated`
   if it ends within `stop_wait_ms` and `killed` if it had to be killed. On Windows the process tree is ended
   with `taskkill /T /F` and recorded as `forced` (`killed` if it still did not end in time). A child that had
   already exited is recorded as `exited_before_stop`.

If a child exits while the run is in progress, no further key is pressed (the children are checked just before
each key press; a key already held is released), the other child is stopped, and the manifest says `aborted`
with the reason. A run in which a child wrote no output
is aborted too. An unexpected error of the runner itself is recorded as `unexpected error (<type>)`, its
traceback is written to `runner.error.txt` and to standard error, and the children are stopped. An interruption
(Ctrl-C, SIGTERM) is recorded as `interrupted`, the children are stopped, the manifest is written, and the
interruption is raised again; a second Ctrl-C during the stop still ends the remaining children.

## The run directory

| File | Content |
|---|---|
| `plan.jsonl` | a byte-for-byte copy of the plan that was played |
| `oracle.jsonl` | what the emitter sent and when (`oracle/1`, including the `reset` rows) |
| `gt.jsonl` | the reference input field's log (`gt/1`) |
| `obs.jsonl` | the observer's log (`obs/1`) |
| `run.json` | the manifest (`run/1`), below |
| `logger.stderr.txt`, `refinput.stderr.txt`, `reset.stderr.txt`, `focus.stderr.txt` | what each child and command wrote to standard error (created when used) |
| `runner.error.txt` | the traceback of an unexpected error of the runner (only then) |

Deposit `run.json` and the four logs only, bundled and checked with `pipeline.py --pack` as
`experiment/data.md` describes. The standard error files and `runner.error.txt` stay on the measuring machine: they
may contain paths and user names.

`run.json` holds: `cell` (as given, with the optional keys filled in), `plan_sha256`, `git_commit` (the
commit of the checkout that holds the runner, taken at the start, or `null`), `git_dirty` (whether
`git status --porcelain` listed anything at the start, or `null`), `host` (the platform string, the operating
system version, and the Python version), `started` and `ended` (`wall_ms`, and `mono_ns` from
the same clock as the `t_mono_ns` of the oracle rows), `outputs` (`file`, `sha256`, and `lines` of each of the
four logs; `null` for a log that is absent or empty), `children` (`exit_code`, `stopped`, `stderr_file`),
`resets` (per block: `exit_code`, `failure`, or `skipped` with the reason), `focus` (per block: the command's
`exit_code` and `failure`, `skipped` with the reason when `focus_cmd` is not empty and the send flag was not given,
and `front_confirmed`, which is true only when a real backend confirmed the front window), `aborted`, and
`abort_reason`.

Before it is written, every string of the manifest is scanned: the home directory and the root of the checkout
are replaced by `~` and `<repo>` (both spellings of the path separator, the longer path first), so that no user
name or directory layout is deposited.

The `gt` and `obs` logs use the clocks of their own processes. The runner does not align them, and the analysis
does not correct them either: the differences between the logs are measured at the block-start markers
(`kit/pilot/clock_sync.py`) and reported.
