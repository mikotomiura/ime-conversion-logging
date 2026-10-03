# Running a Windows cell

This file adds to `README.md` what only the Windows cell needs. The runner (`run_cell.py`), the emitter
(`../emit/emit.py`, `../emit/backend_windows.py`) and the checks described in `README.md` are used unchanged; the
files below are used by the Windows cell alone.

| File | Purpose |
|---|---|
| `cell.windows.json` | The cell file of the Windows measurement: the Microsoft input method, the TSF reference input field (`refinput_tsf`, ground-truth rules `win_tsf`), `focus_windows.py` as the focus command, and the window of `away_windows.py` as the target of scenario S15. |
| `focus_windows.py` | The focus command: brings the reference input field to the front, reads the foreground window back, and only then presses `VK_IME_ON`. It fails, and the runner aborts before the block, when the field is not in front. |
| `away_windows.py` | A small window titled `ime-away` that S15 brings to the front for one second. |
| `start_windows.py` | Starts the runner and brings the reference input field to the front while the runner waits for its children, so that the emitter's first `SetForegroundWindow` is not refused by the foreground lock. It also starts the `ime-away` window for the run and closes it afterwards. |

## Steps

Run every command from the top directory of the published materials (the paths in `cell.windows.json` are relative
to it), in a terminal that is not elevated.

1. Build the observer and the reference input fields from the lock files:

   ```
   cargo build --release --locked --manifest-path kit/logger/Cargo.toml
   cargo build --release --locked --bins --manifest-path kit/refinput-windows/Cargo.toml
   ```

2. Generate the plan from the published files as they are (with LF line ends; a checkout that converts line ends
   changes the SHA-256 of the settings and procedure files that the plan records, and with them the plan's; with
   git, check out with `git -c core.autocrlf=false clone` or use the published export):

   ```
   python kit/gen/generate_plan.py --config experiment/plan_config.windows.json \
     --recipes kit/gen/recipes.json --corpus kit/corpus/data/phrases.jsonl \
     --os windows --seed 20260929 --out <plan>
   ```

   The SHA-256 of the plan must be the one the pre-registration gives the Windows cell (section 10.6).

3. Copy `cell.windows.json`, write the Windows version and build and the input method's version into `ime`, and set
   `clock_sync_disabled` and `auto_update_disabled` to `true` once time synchronisation and automatic updates are
   off (`README.md`, before a measurement). On Windows 11 Home automatic updates cannot be turned off; pause them
   (Settings, Windows Update, Pause updates) for a period that covers the run of 12 to 13 hours, and turn off
   "Set time automatically" (Settings, Time & language, Date & time). Read the version of the input method
   (`IMJPTIP.DLL`) and compare it with the pilot's (section 10.8 of the pre-registration). `focus_cmd` starts
   `python`, which Windows looks up in the working directory and then on the PATH; it must be the same Python as the
   runner's, so run from a directory that holds no other `python.exe`.

4. Turn off sleep, the screen lock and the screen saver, close every application that could come to the front, and
   start the run:

   ```
   python kit/run/start_windows.py --cell <cell> --plan <plan> --run-dir runs/<name> --i-understand-this-sends-keys
   ```

   Nobody touches the keyboard or the pointer until the runner has ended: synthetic keys go to the window in front.

## After the run

Record the checks of the run in `experiment/data.windows.md` (not `data.md`), and list the run there once it has been
deposited. The analysis of the Windows cell is run from the Windows freezing tag with the command of section 10.7 of
the pre-registration; `experiment/run.sh` reads `data.md` and serves the macOS cells.

## Notes from the calibration pilot

- The stopping rule of the Windows cell and the reasons for its additions are in section 10.8 of the pre-registration
  (the TSF reference input field receives only the keys that the input method does not consume).
- `SetForegroundWindow` was refused when the runner was started from a shell that was not in the foreground; the
  emitter's read-back then stops the run before any key is sent. `start_windows.py` avoided this in the pilot run
  that used it, and an earlier version of it in the four runs before. Starting `run_cell.py` directly from a terminal
  in the foreground may avoid it as well; this was not tried in the pilot.
- An application window used as the S15 target was closed during one pilot run, which stopped the run; the
  `ime-away` window avoids this.
