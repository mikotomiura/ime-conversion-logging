# Deviations

Departures from the frozen registration (`preregistration.md`, tag `ime-observation-prereg-macos`), recorded
under section 8 before the analysis they affect is run, except D11 and D12, which change how results are reported and
were made after every result had been seen. Each entry gives its date, the reason and the effect. D1
to D5 were made for the Windows cell before any Windows measurement; the evidence that they leave the macOS
cells unchanged is given at the end. D6 concerns the macOS
measurement. D7 was made after the measurement of the Windows cell and before any analysis of it, and concerns
the Windows cell only. D8 was made before the closed-set attack of question 4 was run on any cell, and concerns all
three cells. D9 concerns the Windows measurement. D10 was made when the analysis of the macOS cells stopped, before
any of their results of questions 1 to 3 was seen; the registered results of the Windows cell had been computed by
then. D11 concerns the report of question 3 in all three cells. D12 concerns the report of question 1 in the Windows
cell.

## D1. Scenario S4 presses three Escapes on Windows (2026-10-03)

**What changes.** Under the Windows procedure, scenario S4 (cancel with the candidate window shown) types the
reading, presses Space, pauses and presses Escape three times instead of twice. The table of section 2.3 lists
two Escapes for every system, and the plan generator checked S4 against two Escapes under every profile
(`_scenario_checks` in `kit/gen/generate_plan.py`), so the Windows addendum could not make this change within the
parts that section 8 lets it change. The generator now reads the Escapes of S4 from a new entry of each profile,
`cancel_converted`: three for `windows` and two for both macOS profiles. The Windows sections (`os.windows`) of
both procedure files press the third Escape.

**Reason.** The Windows calibration pilot (Microsoft IME, Windows 11 25H2, build 26200) found that two Escapes
do not cancel a reading that was converted into several segments: the first Escape reverts the focused segment,
the second returns the reading to kana and opens the prediction window, and only the third cancels the
composition; an Escape after the cancel reached the field as a plain key press. A reading converted into one
segment was cancelled by the second Escape. With two Escapes every S4 trial of the pilot (9 of 9) left an
uncommitted reading that was carried into the next trial and changed the procedure of that trial. Together with the
carry-over of S8, whose digit pick needed a Return (fixed within section 8 by the Windows profile), it hid the
clock-alignment marker of the reference input field in three of six blocks. Check runs with both changes (39
trials on each of the two reference fields) left no reading uncommitted, cancelled every S4 trial at the third Escape
and found the marker in every block.

**Effect.** In the Windows cell, S4 keeps its intent (`commit: none`, `abandon: true`). The third Escape adds one key
press to each S4 trial; when the reading is cancelled by the second Escape, the third is expected to reach the
reference input field as a key press outside a composition, and the Escape after which each S4 session ended is
reported (section 10.7). Because the generator reads the Escapes of S4 from the profiles, the difference between the
two freezing tags shows lines that touch the macOS profiles (the new entry, with the two Escapes of the frozen check)
and the check of S4, which all profiles share. On the macOS cells: see the end of this file.

## D2. The analysis accepts the registered plan and reference of the Windows cell (2026-10-03)

**What changes.** Before analysing real data, `kit/analysis/pipeline.py` checks that each run used the plan that
section 2.4 gives its cell (reason 1 of section 3.4). The frozen check accepted a run only when its cell records live
conversion `on` or `off` and its plan log is one of the two macOS measurement plans, so every run of a Windows cell
would have counted as aborted. The check now accepts a run whose cell records the operating system `windows` when its
plan log is the measurement plan of the Windows cell (SHA-256
`b7bdf8fc120036f18ceff53d2bf8def43ef11a818a119bc24b0c7f4fbc4836b0`, section 10.6) and its reference log comes from
the TSF reference input field (header `src` `win_tsf`, section 10.4); the check of the macOS cells is unchanged. When a
Windows cell is analysed, the delivery results record both values (`registered_plan_sha256_windows`,
`windows_reference_src`).

**Reason.** Section 8 lets the Windows addendum change only the Windows parts of the rule file, of the procedure files
and of the generator's profiles, and add new files; the plan check is in none of them. Without the change the
registered analysis could not be run on any Windows run. The check of the reference makes the choice of the reference
input field (section 10.4) a closed rule rather than a statement.

## D3. The scopes of the Windows cell take the long trials from the presses sent (2026-10-03)

**What changes.** Questions 1 to 3 are answered primarily over the trials that are not long (scope `under_limit`,
section 6.4), and the long trials come from the prediction of section 4.5. The prediction models the macOS observer,
which infers sessions from key presses; the Windows observer follows the input method's window (section 10.3), which
the presses do not determine, so no prediction applies to it, and the frozen pipeline computed a Windows cell under
`all_trials` only. For a Windows cell a trial is now long when the presses that the executor sent in it span at least
`limit_ms` (20,000 ms) on the wall clock, from the first press to the last, and `under_limit` and `over_limit` are
built from these trials by the code used on macOS (`kit/analysis/scope_windows.py` hands them to
`kit/analysis/match.py` in the form of a prediction). As a sensitivity analysis, detection under `under_limit` is also
computed with the long trials of the ground truth (a reference session of at least `limit_ms`), and the trials that
the two rules classify differently and those whose reference session lasts from 19 to 21 seconds are counted. The
comparisons with the prediction (`gt_pred`, `pred_obs`) and the prediction's layers are not computed for a Windows
cell. The result files state this (`note_windows` beside the primary estimates, and `long_trials_from` per Windows
cell).

**Reason.** Without a scope the primary agreement of a Windows cell would mix the long trials, in which the observer
may cut a session off, with its errors, and could not be compared with the macOS cells. The rule uses the oracle and the
plan alone, as the macOS scope does, so neither side of a comparison decides which trials are compared. It is not the
rule of the observer's own cut-off, which on Windows depends on the path that drives it (section 10.3); the boundary
counts and the sensitivity analysis report how much this matters.

**Effect.** On a Windows cell the long trials are defined by what was sent. With the corrected Windows procedure, the
trials whose presses span at least 20 seconds were exactly the long trials of the ground truth in two check runs of
the pilot with the TSF field: 10 trials of one plan, run twice.

## D4. A key row of the observation that no sent press explains counts a Windows run as aborted (2026-10-03)

**What changes.** For a run whose plan is for Windows, the rules of section 3.4 have one more: the observation log is
divided into segments at the block starts, as the reference log is, and the run counts as aborted when in any segment
the key rows that no sent press explains exceed the marker presses of the segment (version `excluding_mode_keys` of
`observation.segments` in the delivery report; the observer writes no row for the runner's `VK_IME_ON`, so there is no
allowance for it). `kit/analysis/pipeline.py` applies the rule in the analysis and in `--pack` and `--check-deposit`,
and records it when a Windows cell is analysed (`windows_observation_presses_from_outside_abort`).

**Reason.** The TSF reference input field receives only the keys that the input method does not consume, so a person
typing into it while it has the focus would pass the rule on presses from outside, and, since the focus is not lost,
the rule on the loss of focus. On macOS the reference field received every key-down, so this case was closed. Section
2.1 states that no person's typing is part of the data; without the rule this could not be supported on Windows.

**Effect.** In every segment of the seven runs of the Windows pilot the key rows of the observation that no sent press
explains were exactly the marker presses. The rule also aborts a run in which the observer writes a key row twice,
which is an error of the observer under test; the figures of such a run are reported (section 10.8). It cannot catch
a press of a key that the observer does not record (section 10.3), a person's press that cancels a sent press that the
observation lost, or input with the pointer, touch or pen. It applies to the runs of the Windows cell and of the
`working` condition on Windows, and not to macOS plans.

## D5. The cell file of the Windows measurement names the two programs with backslashes (2026-10-04)

**What changes.** `kit/run/README.windows.md` (step 3) makes the cell file of the measurement by copying
`kit/run/cell.windows.json` and filling in `ime`, `clock_sync_disabled` and `auto_update_disabled`. In the cell file of
the measurement the first entries of `refinput_cmd` and `logger_cmd` are also written with backslashes, as
`kit\refinput-windows\target\release\refinput_tsf.exe` and `kit\logger\target\release\ime-observation-logger.exe`,
still relative to the top directory of the published materials. Nothing else in the cell file differs from the
template, and the template, the runner and every other file of the tag are unchanged. The same cell file is used for
the check of section 10.8 (the input method's version differs from the pilot's) and for the runs of the Windows cell and
of the `working` condition; the run manifest records it.

**Reason.** Started from the top directory as the README describes, the runner could not start the observer or the
reference input field: `subprocess.Popen` on Windows refused the relative program paths with forward slashes (`FileNotFoundError`,
WinError 2), and the first attempt of the check of section 10.8 was aborted by the runner on 2026-10-04 before any key
was sent. The same programs started from the same directory when the paths were written with backslashes or began with
`./`. The pilot did not find this because its cell files named the programs by absolute paths. The
template could not be corrected without changing a file of the tag.

**Effect.** The programs, their arguments and the directory they run from are those of the template; only the
separator in the two paths differs. No key was sent in the aborted attempt, and it is not a run of the Windows cell.

## D6. The macOS runs were not made on separate nights (2026-10-05)

**What changes.** Section 3.2 states that the runs are made on separate nights, in the order live conversion on,
live conversion off, `idle` and `working`, and section 6.10 measures each condition of question 5 on one night. The
runs were made in this order, each after the check of the one before it, but each was started as soon as that check
was done (times in UTC; local time is UTC+9): the cell with live conversion on from 2026-10-02 16:03 to 2026-10-03
04:04, the cell with it off from 04:19 to 15:27 on 2026-10-03, `idle` from 2026-10-03 15:37 to 23:37, and `working`
from 2026-10-04 13:03 to 2026-10-05 01:04. The run with live conversion off began in the local afternoon, and it and
`idle` both fell in the night from 2026-10-03 to 2026-10-04.

**Reason.** The operator chose to start each item when the one before it had been completed and checked, rather than
wait for the next night.

**Effect.** The order of section 3.2, which keeps the learning added by the replay of section 6.10 from preceding the
two cells, is kept, and no two items overlapped. Nothing else of the registration depends on the hour of a run.
The background load on the machine during each run is reported in `env.md`.

## D7. How the attempts of the Windows cell were counted (2026-10-05)

**What changes.** Section 3.4 counts every run that the runner aborts as an attempt, up to three, and admits no other
reason or explanation; it does not name two events that occurred on 2026-10-04 before the completed run of the
Windows cell. (1) The first attempt was started at 00:38 JST and was stopped at about 00:59 JST from outside the
runner: the shell that ran it was ended by the coding assistant's session on the same machine under low memory (the
wording of the table of run checks). The runner wrote no
manifest. This attempt is counted as aborted (attempt 1). (2) A start at about 01:51 JST was made from a terminal with
administrator rights, which section 10.4 excludes. The runner refused to go on before the first trial, and no key was
sent (the oracle log holds its header only). This start is not counted among the attempts. The completed run is
attempt 2 (01:57 to 13:03 JST, all 2,925 planned trials). All three are listed in the table of run checks of
`experiment/data.windows.md`.

**Reason.** The stopped attempt is counted because a run that ends before its plan is complete is an aborted run,
whatever stopped it. The refused start is not counted because it was a start that the registration itself forbids
(section 10.4), and the runner's check stopped it before any trial was played, so no input reached the input method
or the logs.

**Effect.** No key was sent in the refused start, and the stopped attempt is not analysed or deposited. Counting the
refused start would make the completed run the third attempt instead of the second; it would not change which run is
analysed. The analysis of the Windows cell reads only the completed run listed in `experiment/data.windows.md`.

## D8. The closed-set attack leaves out every trial of scenario S12 (2026-10-05)

**What changes.** Section 6.8 states that a trial whose phrase is a generated kana string (S12) is not counted, which
leaves 2,925 trials with live conversion on and 2,700 with it off, and section 10.7 states that 2,700 trials remain on
Windows without the S12 trials. The frozen `kit/e2/closed_set.py` leaves out only the trials whose phrase is not in
the corpus. The plan generator gives an S12 trial a phrase of the corpus whose reading can be typed with the kana
layout, as long as such phrases remain, and a generated kana string after that, so the frozen code keeps the S12
trials with a phrase of the corpus. A switch `--skip-scenario` is added to `kit/e2/closed_set.py`: it leaves every
trial of the named scenario out of the attack, after the trials have been aligned with the plan, and states their
number in the output (`skipped_scenarios`). Without the switch the output is that of the frozen code; every trial
draws its candidates from its own seed, so leaving out trials does not change the draws of the others. The
closed-set attack of every cell is run with the command of section 6.8 (and of section 10.7 on Windows) and
`--skip-scenario S12`, with `kit/e2/closed_set.py` of the commit that adds the switch and every other file from the
freezing tag of the cell's system. Nothing else changes: the open-set attack already leaves out the kana-input
trials by their scenario, and the decision rule, the values of K and the seed are those of section 6.8.

**Reason.** On Windows the frozen code stopped with "the corpus has too few phrases with a distinct typed reading
(K = 100)": the decoys of an S12 trial are drawn from the phrases that S12 could have produced, and fewer than 99 of
them have a typed reading different from that of the true phrase. The three plans, generated again from the freezing
tags with the seed in `experiment/SEED`, have the SHA-256 registered in sections 2.4 and 10.6 (`c7a5469d…`,
`3759ebf7…` and `b7bdf8fc…`), and each
holds 225 S12 trials, 74 of them with a phrase of the corpus (74 distinct phrases); the macOS cells would therefore
stop in the same way. The counts that the registration gives (3,150 − 225 = 2,925 with live conversion on, and
2,925 − 225 = 2,700 with it off and on Windows) are those of leaving out every S12 trial.

**Effect.** The closed-set accuracy is estimated on the trials that sections 6.8 and 10.7 count: 2,925 with live
conversion on and 2,700 with it off and on Windows when every block is scored. No attack had been run on any cell
when this entry was made, and the probabilities of the table in section 6.8 for those numbers apply.

## D9. The Windows runs were not made on separate nights (2026-10-05)

**What changes.** Section 10.8 states that the runs are made on separate nights, in the order the Windows cell,
`idle` and `working`, and section 6.10 measures each condition of question 5 on one night. The runs were made in this
order, each after the check of the one before it, and no two overlapped (times in JST): the Windows cell from 01:57
to 13:03 on 2026-10-04, `idle` from 13:16 to 21:16 on 2026-10-04, and `working` from 21:29 on 2026-10-04 (the sampler
started at 21:29 and the runner just after it) to 08:35 on 2026-10-05, the runner having played the whole plan. The
Windows cell ran through the night and the morning, `idle` in the afternoon and evening of the same day, and
`working` in the following night.

**Reason.** The operator started each item when the one before it had been completed and checked, rather than wait
for the next night, as for the macOS runs (D6).

**Effect.** The order of section 10.8 is kept, so the replay of the `working` condition added no learning before the
Windows cell was measured (the reason that section 3.2 gives for the order), and no two items overlapped. Nothing else
of the registration depends on the hour of a run.

## D10. A recounted merge is counted with its paired observation; a rate above one has no value (2026-10-05)

**What changes.** `kit/analysis/match.py` takes a second pass over the missed sessions (section 6.5): a missed
session that lies, by at least `theta` of its length, inside an observation row that is already paired counts as
evidence that the row merged it (`n_merge_obs_recount`, which enters the numerator of the merge rate). The frozen code
counts that row in the stratum of the missed session, while the denominator (`n_obs`) counts it in the stratum of the
session it is paired with. When the two lie in different strata, the numerator of a stratum can exceed its
denominator. The analysis of the macOS cells from the freezing tag stopped on the first such stratum, in the layers of
the prediction per speed (section 4.5), with `x and n must be integers with 0 <= x <= n` and exit status 2, before any
result file was written; the Windows cell has no layers (D3) and was not stopped. Two changes were made, before any
result of questions 1 to 3 of the macOS cells was seen:

1. A rate whose numerator exceeds its denominator is reported with no value and no interval and with the note
   `no value: the numerator exceeds the denominator (deviation D10)`, instead of stopping the analysis. Nothing else
   changes: on two synthetic runs (`pipeline.py --synthetic <dir> --seed 4`, analysed with `--seed 7`, `bootstrap_B`
   2000 and `kit/analysis/gt_rules.example.json`), the four result files were byte-identical to those of the freezing
   tag. The analysis of the macOS cells with this change alone is reported as the registered analysis.
2. With the environment variable `IME_MATCH_MERGE_RECOUNT=obs`, a recounted merge is counted in the stratum in which
   `n_obs` counts its observation row, that is, the stratum of the session the row is paired with, as the recounted
   split already is on the side of the ground truth; the missed session keeps its count in `n_miss_as_merge`. The
   numerator then never exceeds the denominator. The detection results record the setting (`merge_recount`: `obs`).
   This corrected analysis is reported next to the registered one (section 8), for the macOS cells and for the Windows
   cell, whose registered results were computed without it.

**Reason.** The merge rate is a share of observation rows, so each row it counts must be one of the rows of its
denominator. The frozen rule breaks this where a missed session and the row that absorbed it fall in different
strata, for example in different trials.

**Effect.** Precision, recall, F1, the split rate and every count of `match()` are unchanged by both changes. Under
the first change a stratum may report its merge rate without a value; under the second the merge rate of a stratum may
change wherever a recounted merge crosses strata, including the primary strata of section 6.4. The pooled rates
over all trials are the same under both rules. The code of both changes is the `kit/analysis/match.py` of release
`v1.0.0` (SHA-256 `6f817228e2e7d5f17f65895c7a00a721c28c00df24c46df0c8bd4b237ad72cff`), and its selftest checks both
rules on a constructed case.

## D11. A per-text ICC whose reference is the same in every text is reported without a value (2026-10-06)

**What changes.** In the report of question 3 (section 6.7), the ICC(A,1) of a measure and speed whose ground-truth
values are the same in every text is given as no value, with the reason that the reference's value is the same in
every text, next to the number that the frozen code returns, which remains the registered result (section 8); and the
standard deviation of the ground-truth values between the texts is reported next to every ICC. The article first gave
the corrected report in place of the registered number; it has given both since a second simulated review pointed out
section 8 of the registration. The analysis code and the result files do not change: the change is
made by the script that turns the result files into the tables and the values of the article
(`manuscript/tools/results_tables.py`), which reads the ground-truth values of each text from the per-text aggregates
of the result files and requires them to cover the same texts as the ICC.

**Reason.** In the two-way model of the ICC, a column that is the same in every row makes the mean square between the
subjects equal to the residual mean square, so ICC(A,1) is zero whatever the other column holds (or undefined, if the
other column holds that same value in every row too). Section 6.11 of the
registration reports a measure that cannot be computed because of zero variance as missing, with the reason, never as
zero; the frozen code (`kit/analysis/stats.py`) returns no value only when every value of both columns is the same. The
design gives every trial of the primary scope one session in most strata, so the conversions per trial of the
reference are 1 in every text there.

**Effect.** In the primary scope, six of the per-speed ICCs of the conversions per trial are reported without a value,
next to their registered value of 0.000: the three speeds of the macOS cell with live conversion off, the slow speed of
the macOS cell with it on, and the slow and middle speeds of the Windows cell. In the supplement, eight more are
reported in the same way: the ICCs pooled over the speeds of
the macOS cell with live conversion off in the primary scope and over all trials, and, over all trials, the same six
strata as in the primary scope. Every other value of question 3 is unchanged. In two strata (the middle and the fast
speed with live conversion on) the reference differs from 1 in a single text; their ICCs keep their values and are
described in the text as near zero for the same reason. This deviation was made after all results had been seen, when
a simulated review of the article before its submission pointed out these values.

## D12. On Windows the detection under the definition common to both systems is also reported (2026-10-06)

**What changes.** For a cell in which the definition of the analysis and the definition common to both systems
(`all`, every session) do not hold the same sessions, which in this study is the Windows cell only, the article
reports for the primary comparison, in the primary scope and over all trials, how many reference sessions the
definition of the analysis leaves out, the ranges of the per-stratum Wilson intervals of recall and precision under the
common definition by the rule of the primary estimates (the strata that keep at least 30 trials in the scope and have a
value), how many of these strata have another estimate under the common definition and the largest such change, and
the detection pooled over the strata under the common definition (recall, precision, split rate and merge rate with
their Wilson intervals), next to the same figures under the definition of the analysis. Sections 5 and 10.7 of the
registration report under the common definition only whether the two definitions hold the same sessions; section 5
states that the results under `all` are not otherwise reported. The
analysis code and the result files do not change: the result files hold the detection under both definitions, and the
values are taken from them by the script that turns the result files into the tables and the values of the article
(`manuscript/tools/results_tables.py`), which also requires the analysis to have reported for every cell whether the
two definitions hold the same sessions, and the definition of the analysis to hold no more sessions than the common
one in the primary scope and over all trials, and as many where the two are reported to hold the same sessions.

**Reason.** On Windows the definition of the analysis keeps only the sessions in which the reference field reported a
candidate element. That the two definitions do not hold the same sessions does not show how many sessions differ or
how far the estimates of question 1 depend on the choice of definition.

**Effect.** No registered value changes; values are added to the text of question 1 and a table to the supplement. In
the macOS cells the two definitions held the same sessions, and no value or table is added for them; the supplement
says that they held the same sessions. This deviation was made
after all results had been seen, including the detection under the common definition, when a simulated review of the
article before its submission asked for the size of the difference between the two definitions. The ranges of the
per-stratum intervals were added afterwards: it was recorded that they would be reported whatever their values before
they were computed. The counts of the strata that change were added after the ranges had been seen, when a review of
the discussion found that the per-stratum estimates within the ranges change.

## The macOS cells are unchanged

The macOS plans and analysis are generated and run from the macOS tag (section 8). That the changed files would give
the same results was checked on 2026-10-03, on Windows 11 with Python 3.11.9, in checkouts with LF line ends of the
macOS tag and of the commit that holds the code of the addendum (a commit of the working repository, which is not
public; the commits after it up to the freezing commit of the addendum change only `preregistration.md` and
`deviations.md`, so its code is the code of the Windows freezing tag):

- Plans: in each checkout,
  `python kit/gen/generate_plan.py --config experiment/plan_config.macos.json --recipes kit/gen/<file> --corpus
  kit/corpus/data/phrases.jsonl --os macos --seed 20260929 --out <plan>` for both procedure files. From the macOS tag
  the plans have the SHA-256 that section 2.4 gives (`c7a5469d…` and `3759ebf7…`); from the changed files they have
  `e7a541b17830f94c48e7f284e0f89f608a257ed66eb8abf541be5a0ba7f7ed3f` and
  `3bf24b2eb7ba67fcb8692d45d06c120b890f39f26bae97dc120888b2496e91fb`, and with the recorded SHA-256 of the procedure
  file replaced by the same string the two plans of each file are byte-identical.
- Analysis: two synthetic macOS runs made by `python kit/analysis/pipeline.py --synthetic <dir> --seed 4` (macOS tag)
  were analysed in each checkout by `python kit/analysis/pipeline.py --runs <dir>/run-a <dir>/run-b --config <cfg>
  --seed 7 --out <out>`, with a configuration file holding `bootstrap_B` 2000 and the rule file
  `kit/analysis/gt_rules.example.json`. The four result files were byte-identical: `e1_delivery.json` `d8298a16…`,
  `e1_detection.json` `a8fa275d…`, `e1_l1.json` `d849701d…`, `e1_l2.json` `8a95c080…`.
- The self-test of `kit/analysis/pipeline.py` checks, on synthetic runs, that an analysis of macOS cells alone records
  none of the values of D2 to D4 and that the rule of D4 does not apply to a macOS run; it does not compare with the
  macOS tag.
