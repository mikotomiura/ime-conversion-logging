# Windows calibration pilot

The calibration pilot ran the measurement procedure on the Windows measuring machine before the Windows addendum
was frozen (section 10 of the pre-registration). It had the aims of the macOS pilot (`pilot-macos.md`): to check
that the scripted key presses drive the input method as the procedures intend, to fix the ground-truth definition
against the call sequences the reference input field receives, and to measure the clock differences and the time
one trial takes. On Windows it also chose between the two reference input fields of `kit/refinput-windows` (one
that receives the input method's messages through the input-method manager, IMM, and one that is a client of the
Text Services Framework, TSF), and it checked the files that only the Windows cell uses (`kit/run/README.windows.md`).
Only the reference side (the reference input field's log, the executor's record and the plan) and the key rows of the
observation were analysed. The observation's composition records were sealed (`kit/pilot/seal_obs.py`) before any
analysis and were not read.

## Setup

| Item | Value |
|---|---|
| Machine | x86-64 tablet computer (Intel Core i5-1135G7, 8 GB memory), Windows 11 Home 25H2, build 26200.8875, JIS keyboard (106 keys), on mains power; sleep, screen-off and screen saver were already off |
| Input method | Microsoft IME (Japanese), `IMJPTIP.DLL` 10.0.26100.8875, default settings: Romaji input (no other input method or typing mode is set in its settings) |
| Predictive candidates | Not changed from the input method's default. Scenario S10 selects a predictive candidate |
| Learning | Not reset between blocks or runs. The input method offers no command that clears its input history, and the measurement does not reset it either |
| Initial state | Before each block the focus command brings the reference input field to the front, reads the foreground window back, and presses `VK_IME_ON` (0x16) |
| Observation | The stand-alone logger built from an export of this directory made at the macOS freezing tag (the list of exported files had the SHA-256 that the macOS registration gives) |
| Reference input fields | `refinput_imm` (IMM, ground-truth rules `win_imm`) and `refinput_tsf` (TSF, rules `win_tsf`), built from the same export |
| Plans | Generated with `--os windows` from `kit/gen/recipes.json` (S9 and S11 are disabled by default on Windows, so a plan has 13 scenarios), with the measurement's generator settings except the number of trials: n = 1 (39 trials in three blocks) or n = 3 (117 trials in six blocks) per scenario and speed. Seeds 6, 5 and 8 |
| Tools | Python 3.11.9, Rust 1.98.1 with the MSVC tools 14.44 |

## Runs

| Run | Reference | Procedures | Trials | Purpose |
|---|---|---|---|---|
| `short-imm`, `short-tsf` | IMM, TSF | frozen Windows procedures | 39 each | First check |
| `pilot-imm`, `pilot-tsf` | IMM, TSF | frozen Windows procedures | 117 each | Pilot |
| `verify-imm`, `verify-tsf` | IMM, TSF | corrected (S4, S8; see the findings) | 39 each | Check of the corrected procedures |
| `toolcheck-tsf` | TSF | corrected | 39 | Check of `kit/run/start_windows.py`, `focus_windows.py`, `away_windows.py` and a cell made from `kit/run/cell.windows.json` |

None of these runs was aborted, and the front-window check passed before every block. Three earlier attempts were
aborted by the runner: twice the emitter's `SetForegroundWindow` was refused when it opened, and the read-back stopped
the run before any key was sent; once an application window used as the target of S15 was closed during the run,
which stopped it after four trials (finding 5). The same plan was used for both references at each stage, so the two
reference input fields can be compared trial by trial.

## Findings and the changes made

1. **S4 does not cancel with two Escapes.** After a conversion into several segments the first Escape reverts the
   focused segment and the second returns the reading to kana and opens the prediction window; the third cancels.
   A reading converted into one segment was cancelled by the second Escape (also with the Escapes 120 to 860 ms
   apart). With two Escapes all nine S4 trials of `pilot-*` left an uncommitted reading that ran on into the next
   trial. S4 now presses three Escapes on Windows; this is deviation D1 (`experiment/deviations.md`), because the
   frozen generator checked S4 against two Escapes on every system.
2. **A digit picks a candidate but does not commit.** In S8 the composition stayed open after the digit (nine of nine
   trials); a Return after the digit committed it. The Windows profile of the generator now ends S8 with a Return,
   as the macOS profiles do.
3. **S15 commits when the field loses the focus.** When the other window comes to the front the input method
   commits the composition (IMM: `focus_out`, then a result, then `end_comp`; TSF: `tsf_end` with a result). The
   Return pressed after the focus comes back reaches the field as a plain key (a new line). The ground-truth rules
   count the session as committed at that point; no change was made.
4. **The emitter's first `SetForegroundWindow` can be refused** by the foreground lock when the runner is started
   from a shell that is not in the foreground. `kit/run/start_windows.py` brings the field to the front while the
   runner waits for its children; with it (and with an earlier version of it in the four runs `pilot-*` and
   `verify-*`) no run was refused.
5. **The S15 target must not be a window that can close.** The window `ime-away` of `kit/run/away_windows.py`
   replaced the application window.
6. **Escape once cancels a reading that was not converted** (S5), and `VK_IME_ON` switches the input method on.

## Results

### Call sequences

The IMM field receives `start_comp`, a `comp` call for every key press of the reading (with `GCS_RESULTSTR` and a
result length when the composition is committed), `end_comp`, and notifications of the candidate list (`notify` with
`IMN_OPENCANDIDATE` and `IMN_CLOSECANDIDATE`) and of other changes; it also receives every key press as a key-down,
the keys the input method processed with the virtual key 229. The TSF field receives `tsf_start`, `tsf_update` with
the length of the composition after each edit, `tsf_end` with the length of the committed text, and the begin and
end of the input method's candidate element (`ui_begin` / `ui_end` of kind `candidate`); it receives as key-downs
only the keys that the input method did not consume. Folded with `kit/pilot/call_types.py` (an immediate repeat of a
call written once with `+`), the TSF sequences reduce to one per scenario in the corrected runs, except S5 and S10
(two each: in one S5 trial the candidate element opened again after the cancel, and in one S10 trial it did not open).
The IMM sequences cannot be folded this way, because a `comp` call and a notification alternate at every key.

The candidate element of the TSF field opens while the reading is typed, before any Space: the input method shows
its prediction window there. Every session of each corrected TSF run but one (an S10 session) showed it, so the Windows
definition of a session with a candidate window (`windows_def`) holds nearly the same sessions as the common
definition (`all`).

### Ground-truth definition

The Windows blocks of `experiment/gt_rules.json` (`win_imm`, `win_tsf`) were written before the pilot as working
hypotheses and were not changed. A session opens at `start_comp` / `tsf_start` and closes at `end_comp` / `tsf_end`,
as committed when a result was received in the session (IMM) or when the result length of `tsf_end` is positive
(TSF), and as cancelled otherwise. With the corrected procedures every trial of both references produced the session
its plan intends (below).

### Agreement between the plan and the ground truth

| Run | IMM | TSF |
|---|---|---|
| `short-*` (frozen procedures) | 33 / 39 | 30 / 39 |
| `pilot-*` (frozen procedures) | 102 / 117 | 93 / 117 |
| `verify-*` (corrected procedures) | 39 / 39 | 39 / 39 |
| `toolcheck-tsf` (corrected procedures, kit tools) | | 39 / 39 |

Agreement counts a trial as agreeing when the assembled sessions match the plan's intent (one session, committed or
cancelled as intended; `kit/analysis/gt_sessions.py`). Every disagreement of the runs with the frozen procedures
belongs to an S4 or S8 trial that left its reading uncommitted (findings 1 and 2) or to a trial into which that
reading ran on (once through two further trials), and the sessions that span two or more trials are excluded with
those trials by the carry-over rule of section 6.2 of the pre-registration (6 such sessions and 12 trials in each
`short-*` run, 15 sessions and 33 trials in each `pilot-*` run). With the
corrected procedures no reading was carried over.

The two references, run with the same plan, returned nearly the same sessions: in `pilot-*` the first call of a trial
came 5 ms later in the TSF field (median; 5th to 95th percentile −5 to 9 ms), and the last call differed by more than
200 ms in eight of 117 trials. Three of them are S7 trials in which the TSF field committed when the right arrow was
pressed beyond the last segment, while the IMM field kept the composition open until the Return; the same input
method thus commits at different points in two kinds of field. Five are S14 trials, in which the IMM field receives a
notification of the input mode after the composition has closed. In the runs with carried readings the two fields
also recorded a carried reading differently (two results in one IMM session; a session without a result followed by
a new one in the TSF field).

### Choice of the reference input field

The measurement uses the TSF field (`kit/run/cell.windows.json`). The Microsoft input method is a text service of
the TSF; a TSF client receives its composition directly, while an IMM window receives it through a compatibility
layer of the system. The choice was made from the reference side alone, before any session row of the observer was
read. A second reason follows from the rule on presses from outside
(section 3.4 of the pre-registration): the IMM field receives the `VK_IME_ON` press of the focus command as a
key-down before every block (one in each segment but the last, in all three IMM runs), which the setting k = 0 of the
registration does not allow, while the TSF field received none (below).

### Clocks

Differences at the block-start markers (`kit/pilot/clock_sync.py`), wall clock, one value per block.

| Run | observation − executor | reference − executor |
|---|---|---|
| `pilot-imm` | 1 to 2 ms (median 1.0 ms, six blocks) | 4 to 5 ms (4 ms, three blocks) |
| `pilot-tsf` | 0 to 2 ms (1.5 ms, six blocks) | 2 to 5 ms (4 ms, three blocks) |
| `short-*`, `verify-*`, `toolcheck-tsf` | 0 to 2 ms (medians 0 to 1 ms, three blocks each) | 2 to 9 ms (medians 3 to 5 ms) |

Unlike on macOS, the first block of a run did not differ from the others. In `pilot-*` the reference's marker was not
found in blocks 1, 3 and 5: each of these blocks began after a carried reading, and the arrow keys of the marker moved
the cursor within that composition instead of reaching the field. With the corrected procedures the marker was found
in every block. Fitted drifts were within 11 ms per hour in the two runs of six blocks; the runs of three blocks span
ten minutes and give no useful drift.

### Reaching the observation and the reference

Of the presses sent inside trials, 3,882 of 3,900 in each `pilot-*` run and 1,464 of 1,470 in each corrected run
produced a key row in the observation (`kit/pilot/reach.py`); the missing presses are the two input-mode keys of each S14 trial, which
the observation does not record by design. No trial had more rows than presses.

`kit/analysis/delivery.py`, run with the arguments of section 3.4 of the pre-registration on the key rows of the
observation, found in the TSF runs no key-down in the reference log beyond the marker presses in any segment (with
and without the input-mode keys of S14 counted), none in the segment before the first block, and no loss of focus of
the reference outside the planned switches of S15, so no widened span held a row without a press sent. Of the presses
sent inside the trials, 3 to 16 per TSF run reached the field as a key-down, against 1,174 to 3,900 sent
(`reference.reach` of the delivery report; the marker presses are counted separately and reached the field when no
composition was open). Its log therefore shows a person's
key press only when the input method does not consume it; the rule on the loss of focus reads the observation and is
not affected. In the IMM runs each segment but the last held one key-down beyond the markers (the `VK_IME_ON` press),
which the registered setting k = 0 counts as a press from outside; with k = 1 and the input-mode keys of S14
counted among the presses sent (not the registered settings) no segment exceeded its bound, and there was no loss
of focus outside the planned switches either.

The assignment of observed presses to trials that both attacks of question 4 use, run without any attack on the key
rows of all seven runs with the four-press marker in the observation (`--markers-in-obs`), scored every block (27
blocks, 429 trials) and left no press unassigned.

### Time per trial

Including the waits and markers, 117 trials took 27.5 minutes in `pilot-*` (14.1 s per trial; median trial 11.8 s,
95th percentile 28.6 s, set by S13), and 39 trials took 9.9 minutes in the corrected runs (15.2 s per trial; S4 and S8
now end with a key more). The generator's estimate is 13.3 s per trial for the plans of `pilot-*` and 13.0 s for
the plan of the corrected runs.

## Consequences for the measurement

- One cell: the Microsoft input method with the TSF reference input field, the corrected procedures and n = 75 per
  scenario and speed (2,925 trials in 117 blocks; `experiment/plan_config.windows.json`). The generator estimates
  13.75 s per trial (11.2 hours); scaled by the pilot's ratios of measured to estimated time (1.06 for the runs of 117
  trials with the frozen procedures, 1.17 for the runs of 39 trials with the corrected ones, in which the time between
  blocks weighs more), the run takes 12 to 13 hours.
- The run is started with `kit/run/start_windows.py`, and S15 uses the `ime-away` window.
- The settings of the rule on presses from outside fixed on macOS (k = 0, the input-mode keys of S14 not counted
  among the presses sent) hold for the TSF field. Because that field sees few of the presses, a run also counts as
  aborted when the observation holds key rows that no sent press explains (deviation D4).
- The scopes of questions 1 to 3 take the long trials from the span of the presses sent (deviation D3). With the
  corrected procedures, the trials whose presses span at least 20 seconds were exactly the long trials of the ground
  truth in `verify-tsf` and `toolcheck-tsf` (10 of 10 each; the two runs used the same plan).

## Raw logs

The raw logs of the pilot runs are kept on the measuring machine and are not yet deposited. The runs with the TSF
field are deposited with the measurement data under the criteria of the macOS pilot (`pilot-macos.md`, Raw logs),
which are those of the measurement, with the rules that the Windows addendum adds (section 10.8 of the
pre-registration: the reference log comes from the TSF field, and no key row of the observation goes beyond the
marker presses). The plans of these runs are not the plan of the Windows cell, so the rule on the plan's SHA-256 does
not apply to them. Before the freeze of the addendum, `kit/analysis/delivery.py` of the macOS freezing tag was applied
to the key rows of the observation and the reference logs of all seven runs: it caught none of the four TSF runs, and
no run had a key row of the observation beyond the marker presses. The three runs with the IMM field are not
deposited: that field receives the focus command's `VK_IME_ON` as a key-down in every segment but the last, which the
setting k = 0 counts as a press from outside, so `--check-deposit` refuses them. The three aborted attempts are
deposited only if they meet the same criteria at deposit time. `--check-deposit` is run at deposit time, after the
observation logs are unsealed.

The probes of the Escape and digit behaviour (findings 1 and 2) typed into the reference input field alone, without
the observer, and are not deposited.
