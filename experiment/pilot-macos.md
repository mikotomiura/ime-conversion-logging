# macOS calibration pilot

The calibration pilot ran the measurement procedure on the macOS measuring machine before the protocol was
frozen. It had four aims: to check that the scripted key presses drive the input method as the procedures
intend, to fix the ground-truth definition of a composition session against the call sequences the reference
input field actually receives, to measure the clock differences and the time one trial takes, and to choose the
block-start marker. Only the reference side (the reference input field's log, the executor's record, and the
plan) and the key rows of the observation were analysed. The observation's composition records were sealed
(`kit/pilot/seal_obs.py`) before any analysis and were not read, so the definitions below were fixed without
reference to the system under test.

## Setup

| Item | Value |
|---|---|
| Machine | Apple Silicon laptop, macOS 27.0, JIS keyboard |
| Input method | macOS Japanese, Romaji input (`com.apple.inputmethod.Kotoeri.RomajiTyping`), ABC layout as the other input source |
| Live conversion | On in the first pilot and in one cell of the second; off in the other cell. Set through the input menu and read back from it |
| Predictive candidates | Not changed from the input method's default (no override is stored in its preferences). Scenario S10 selects a predictive candidate |
| Learning | Not reset between blocks or runs. No method of resetting the learning of this input method was found, and the measurement does not reset it either (see the pre-registration) |
| Initial state | Before each block, `focus_cmd` brings the reference input field to the front, confirms it is in front, and presses the kana key (key code 104) so that the block starts in Japanese input |
| Observation | The stand-alone logger built from an export of this repository |
| Reference input field | `kit/refinput-macos`, wrapped in an application bundle by `build.sh` so that the front-window check can name it |
| Plan | Corpus `c8f890cb…` (see `data.md`), seed 1 for `pilot-1` and `pilot2-on`, 5 for `pilot2-off` and 2 to 6 for the shorter runs, provisional settings: n = 3 per scenario and speed (1 in `verify-1` and `verify-off`), key intervals with medians of 300, 200 and 120 ms, think time 0.2 to 20 s, 25 trials per block. The other generator settings are those of the measurement (`experiment/plan_config.macos.json`) except the marker (see the findings) and the reset request, which the plans made but no reset command carried out (see Learning) |

Scenario S9 is disabled by default in both procedure files, and S11 (which needs live conversion) is not run
with live conversion off, so a cell has 14 or 13 scenarios.

## Runs

| Run | Live conversion | Procedures | Trials | Purpose |
|---|---|---|---|---|
| `pilot-1` | on | first version | 126 | First pilot |
| `verify-1`, `verify-s5` | on | revised | 42, 18 | Checks of the revised procedures |
| `pilot2-on` | on | `kit/gen/recipes.json` | 126 | Second pilot |
| `pilot2-off` | off | `kit/gen/recipes.json` (written for live conversion on) | 117 | Second pilot |
| `verify-off` | off | `kit/gen/recipes.macos-live-off.json` (first version) | 39 | Check of the procedures for live conversion off |

No run was aborted, and the front-window check passed before every block. The procedure file for live conversion
off was changed after `verify-off` only in that S11 now has no procedure, so that naming it stops the generator. The raw logs are not yet deposited
(see the last section).

## Findings of the first pilot and the changes made

1. **A single Enter after a conversion does not commit.** Within 600 ms of the Enter that should have committed,
   the reference input field received `insert_text` 27 times and nothing 54 times. Enter after typed letters
   committed in every case (18 of 18); Enter after Space, after an arrow key or after a change of the front
   application committed in none. Changing how the key was sent (thirteen variants, including a longer press and
   sending it through AppleScript) did not change this, and the same happened when the keys were typed by hand,
   both in the reference input field and in TextEdit. With live conversion on, Enter in the candidate window
   only selects the candidate, and a second Enter commits. Escape steps back one stage at a time (converted text,
   kana, romaji spelling, cancelled), at most three stages. The macOS procedures were changed accordingly: Enter
   twice after a conversion, Enter after choosing a candidate by digit, and Escape four times to cancel. An
   uncommitted composition otherwise runs on into the next trial, so the trials are no longer independent.
2. **The block-start marker did not reach the observation.** The logger observes F1 to F12 on macOS, not F13 to
   F19, so the F13 / F14 / F13 marker left no row there. The marker was changed to arrow keys, which the
   observation records as `navigation`.
3. **The reference input field had no bundle identifier**, which the front-window check needs. `build.sh` now
   builds an application bundle with one.
4. **The initial state of the input method was not part of the plan.** It is now set by `focus_cmd` before each
   block (see Setup).

## Results of the second pilot

### Call sequences

With live conversion on, the input method commits the head of the marked text while the reading is still being
typed and then marks the remainder, so the reference input field receives `insert_text` immediately followed by
`set_marked`. These automatic commits made the raw call sequences vary within a scenario (two to five distinct
sequences per scenario, ignoring `first_rect` and immediate repeats of a call). Once each automatic commit followed by a continuation is folded
away, every scenario reduces to one sequence (`set_marked … insert_text` for a commit, `set_marked … set_marked`
with an empty text for a cancellation), with three exceptions: one trial each of S8 and S10 that left the
composition uncommitted (carried over, below); two trials of S12 (kana input) in which the first Enter committed
and the second produced a new line (`do_command`); and S6 (the reading deleted with Backspace), in which a
Backspace after the composition had been emptied was received as `do_command` in five of nine trials. With live conversion off there were no automatic commits. Run with the
procedures written for live conversion on, the second Enter after a single Space produced a new line in five
scenarios (45 trials), because with live conversion off a single Enter commits; the procedure file for live
conversion off (`verify-off`) removed this, and every scenario reduced to one sequence.

### Ground-truth definition

The rules are in `gt_rules.json` (the `mac_nstic` block); the reasons are given in its `_note`. A commit closes a
session only if no `set_marked` with a non-empty text follows within 50 ms, so that an automatic commit in the
middle of typing stays inside the session it belongs to. In `pilot2-on` the 85 continuations followed the
automatic commit by 0.50 to 7.85 ms (median 1.0 ms) with no key press in between, and the next composition began
at least 958 ms after a final commit. Every threshold from 10 to 900 ms gave byte-identical sessions and
exclusions in all three runs analysed (`pilot2-on`, `pilot2-off`, `verify-off`).

A session's outcome is the reason it finally closed. Text committed automatically earlier in the same session is
not counted, so when the head of a reading was committed and the rest was then cancelled, the session is a
cancellation. With live conversion on this happened in 11 of the 27 cancelled trials (S4: 3, S5: 3, S6: 5 of 9
each). How the observation's abandonment is compared with such sessions is set out in the pre-registration.

The 50 ms window needs the reference log to keep running after a commit. Within a trial no procedure types
again after its committing or cancelling key, and trials are 1,000 ms apart. After the last trial the runner
keeps the reference input field running for `settle_ms` (1,000 ms by default; about 1,070 ms in these runs), so
the window after the last commit is observed; `settle_ms` must not be set below the window.

A session that overlaps two or more trials is excluded, with every trial it overlaps (`carry_over` in
`kit/analysis/gt_sessions.py`), and the number of such trials is reported separately. The carried trial is left
without a session of its own, and the later trial's session contains the earlier trial's text, so neither is an
independent trial.

### Agreement between the plan and the ground truth

| Run | Example rules (`kit/analysis/gt_rules.example.json`) | Rules of this experiment | Carried over |
|---|---|---|---|
| `pilot2-on` | 85 / 126 (40 trials split into several sessions) | 124 / 126 | 2 trials (S10, S8), excluded with the next trial |
| `pilot2-off` | 117 / 117 | 117 / 117 | 0 |
| `verify-off` | 39 / 39 | 39 / 39 | 0 |

Agreement counts a trial as agreeing when the assembled sessions match the plan's intent (one session, committed
or cancelled as intended). The two disagreements under the rules of this experiment are the carried trials.

### Clocks

Differences were measured at the block-start markers (`kit/pilot/clock_sync.py`), wall clock, one value per
block, six blocks per run.

| Run | observation − executor | reference − executor | observation − reference |
|---|---|---|---|
| `pilot2-on`, blocks 1 to 5 | 0 to 3 ms (median of all blocks 1.0 ms) | 3 to 5 ms (5.0 ms) | −5 to −2 ms |
| `pilot2-off`, blocks 1 to 5 | 0 to 2 ms (1.5 ms) | 2 to 4 ms (3.5 ms) | −4 to −1 ms |
| Block 0 | 95 ms and 86 ms | 105 ms and 108 ms | −10 ms and −22 ms |

In both runs the first block, at the start of the run, differed by about
100 ms from the executor on both the observation and the reference side; the observation and the reference
agreed with each other there to within 22 ms. A line fitted to all six blocks gives a drift of about −200 ms per
hour, but this comes from block 0 alone: over blocks 1 to 5 (15 minutes) the fitted drift is within ±5 ms per
hour. The analysis does not correct for these offsets. The dwell error is a difference of durations and does
not depend on them. Two intervals of the same length still pair under the overlap criterion of the matching as long
as the offset stays at or below a third of that length (intervals of different lengths tolerate less), so the
largest difference seen here (22 ms) could on its own affect only sessions shorter than about 66 ms; the delay with
which an observation row is collected (up to about 100 ms; section 4.4 of the pre-registration) adds to it and
raises this length to about 370 ms. The per-block offsets are measured in the same way in every run of the
measurement and reported with the results.

### Reaching the observation

Of the key presses sent inside trials, 4,072 of 4,090 (`pilot2-on`) and 3,972 of 3,990 (`pilot2-off`) produced a
row in the observation (`kit/pilot/reach.py`). The missing presses were, in both runs, the two input-source keys
of each of the nine S14 trials, which the observation does not record by design. Every press of a key the
observation records produced a row; no trial had more rows than presses, and the 18 rows outside the trials equal
the number of marker presses (six blocks of three).

### Time per trial

Including the waits and markers, 126 trials took 23.2 minutes with live conversion on (11.0 s per trial) and 117
trials took 23.2 minutes with it off (11.9 s per trial). The median trial lasted 7.9 s (on) and 9.5 s (off); the 95th percentile was
26.3 s and 26.7 s, set by S13, which waits more than 20 s. For the pilot's key intervals the generator's estimate
(`generate_plan.py --estimate`) is 11.2 s per trial with live conversion on and 11.4 s with it off, close to the
measured times.

## Consequences for the measurement

- n = 75 per scenario and speed, with think time from 0.2 to 20 s. With the pilot's key intervals one macOS cell
  would take about 9.8 hours with live conversion on and 9.3 hours with it off; lowering the upper limit of think
  time to 5 s would save about one hour per cell and would remove the range in which the 20-second cut-off is
  examined. The key intervals of the measurement were set afterwards (medians of 480, 240 and 120 ms; see the
  pre-registration), for which the generator estimates 13.6 and 13.8 s per trial and 11.9 and 11.2 hours per
  cell.
- The block-start marker is `left`, `right`, `left`, `right`. The three-key marker of the pilot cannot be told
  apart from the three arrow presses in a row that scenario S7 may make.
- Cells with live conversion off use `kit/gen/recipes.macos-live-off.json` throughout: for the plan, and for the
  closed-set attack, which checks that the plan and the procedure file match.

The settings of the measurement are in `experiment/plan_config.macos.json` and are frozen with the
pre-registration.

## Raw logs

The raw logs of the pilot runs are kept on the measuring machine and are not yet deposited. They are deposited with
the measurement data under the following criteria, which were written before the freeze of the pre-registration and
are those of the measurement (section 3.4 of the pre-registration). Both logs can carry a person's typing: the
observation records every key press on the machine, and the reference input field records the key code of every
key-down it receives.

- Only runs of the runner are candidates: runs started by the runner from a plan, with a manifest (`run.json`). A
  run that the runner aborted is a candidate on the same terms as in the measurement.
- The rules of section 3.4 of the pre-registration on the delivery report do not catch the run, with the settings
  fixed from this pilot and the run's own marker, which its plan states (three presses in these runs). What the
  rules rest on and what they do not catch are stated in section 3.4 and in the item "The stopping rule counts
  presses, not keys" of section 7 of the pre-registration. The plans of these runs are not those of section 2.4,
  so the rule on the plan's SHA-256 does not apply to them.
- `kit/analysis/pipeline.py --check-deposit` passes on the run directory. It applies the same rules and also checks
  the files for paths and names and the logs, also those of an aborted run, for the content-free form.
- Outside the losses of focus of the reference and the stretches that section 3.4 of the pre-registration counts with
  them, rows of the observation without a sent press do not stop a run from being deposited; they are a result of the
  observer under test, as in the measurement.

The observation logs were sealed (`kit/pilot/seal_obs.py`) and only their key rows were read. Their sealed session
rows are unsealed after the freeze and the measurement only to be deposited; they are not analysed (section 3.1 of
the pre-registration). `--check-deposit` is therefore run at deposit time, after the unsealing. Before the
freeze, the rule on presses and earlier forms of the rule on the loss of focus (which left out the planned switches
of S15 and compared numbers of rows and presses) were applied to the key rows of the observation and to the
reference logs of the six runs listed above and of `short-1`, a run of nine trials made just before the first
pilot, and caught none of them; the one loss of focus outside the planned switches (`pilot2-off`, 31 ms) had no key
press within it (section 3.1 of the pre-registration). The rules in their registered form are applied by
`--check-deposit` at deposit time. Any other run of the runner made before the first pilot, such as a dry run that
was interrupted, is deposited only if it meets the same criteria then.

The hand-typed checks described above (in the reference input field and in TextEdit) and the hand-run probes of the
Enter and Escape behaviour were not runs of the runner and are not deposited.
