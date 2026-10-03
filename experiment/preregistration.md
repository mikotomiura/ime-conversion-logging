# Pre-registration: agreement between an observer of Japanese input-method conversion that records no text and a reference input field (macOS cells)

## Status

This registration covers the macOS cells and was frozen on 2026-10-02, before any measurement was made under it.
The two lines below are the freeze marks of section 9; `run.sh` and `kit/analysis/pipeline.py` analyse real data
only while both are present and the second matches the configuration, seed and rule files. The cells on Windows are
registered in an addendum that is frozen before the first Windows measurement (section 8).

frozen_at: 2026-10-03

sha256: 64dfbb6e5f08222ade1fb96a8171d97c0fa1bc9ca9adb5cc987fc27bd9a8af2a

## 1. Study information

### 1.1 Purpose

Keystroke-logging research on Japanese text production has to take account of the input method: a writer types
a reading, the input method converts it, and the writer accepts, changes or abandons the conversion. The observer
under test (`kit/logger`, a stand-alone program) records this process without recording any text. For every key
press it receives, other than the input-mode keys, it writes one of fourteen key categories and a time (on macOS
the keys F13 to F19 and the modifier keys produce no row either); for every conversion session it writes one summary row
with the session's duration (`dwell_ms`), the number of arrow-key presses during the session (`nav`) and whether
the session was abandoned (`abandon`). No character, reading, candidate string or window name is written.

On macOS no public interface reports the conversion state of another application, so the observer infers each
session from key presses while Japanese input is active. A session opens at the first letter or digit key, is
closed as committed by Return, by any key other than a letter, digit, space, backspace, arrow or input-mode key,
or by a change to non-Japanese input, and is closed as abandoned by Escape. Space marks a candidate window as
shown; backspace and arrow keys keep the session open. A session is cut off, without a row, at the first key press
that comes 20 seconds or more after it opened; that press then starts afresh, so a letter or digit opens a new
session.

This study estimates how closely those rows agree with the sessions assembled, by the rule of section 4.3, from
the calls that a reference input field received from the input method at the same time, under scripted input whose
intent is known. Because the observer's rules are deterministic, the rows it is specified to write can be computed
from the presses that were sent; this prediction (section 4.5) is used to separate the disagreements that follow
from the rules from those that follow from carrying them out (section 6.4). The study also estimates how much of the
typed text can be recovered from a published log, and what the observer costs the machine.

### 1.2 Questions

The study estimates; it tests no hypothesis. Every estimate of agreement (questions 1 to 3) and every accuracy and
error rate of question 4 is reported with an interval; F1, the counts, the mean rank and the per-scenario and
per-speed accuracies of question 4, the delivery figures of section 6.3 and the figures of sections 6.9 and 6.10
are reported without one (section 6.1). No estimate is turned
into a pass or fail decision, except the near-chance rule of the text-recoverability study, which decides which log
granularity is recommended (section 6.8).

1. Detection. What fraction of the sessions assembled from the reference input field does the observer report as
   one row (recall), and what fraction of the observer's rows correspond to one session (precision)? How often does
   the observer split one session into several rows, or merge several sessions into one?
2. Per-session agreement. For sessions the observer reports as one row, how does `dwell_ms` differ from the
   session's duration, how often does `abandon` agree with the session's outcome, and how often does `nav` equal
   the number of arrow keys pressed during the session? For the sessions that reach the 20-second cut-off, does the
   observer write no row, as specified?
3. Per-text agreement. When trials are grouped into texts, how closely do four aggregates a writing researcher
   might compute (conversions per trial, mean conversion time, abandonment rate, fraction of time spent converting)
   agree between the observation and the ground truth?
4. Text recoverability. Given only the key rows of a published log, how accurately can an attacker identify which
   of K candidate phrases was typed in a trial, and how far can an attacker with a language model reconstruct the
   typed reading? Which of two granularities of the key rows that keep no time keeps the identification of a
   phrase near chance? (Section 6.8 states what is not evaluated.)
5. Runtime cost. What CPU time and resident memory does the observer use while the machine is idle and while
   synthetic typing is played?

Questions 1 to 3 are answered primarily for the trials whose presses went into no session that the observer's rules
cut off; the other trials are examined separately (section 6.4). The scripted input also allows checks of the
instruments themselves, which are reported and are not used to remove trials from the evaluation: for each trial,
whether the input method did what the plan intended (one session, committed or cancelled as planned; section
6.3), and for each key press, whether and when it reached the observation and the reference input field (section
6.3). The one exclusion that rests on the input method's behaviour is that of a session carried from one trial into
the next (section 6.2), because such a session cannot be assigned to one trial.

### 1.3 Scope of this registration

This registration covers two cells on macOS: the standard Japanese input method with live conversion on, and the
same input method with live conversion off. It covers the agreement analyses of questions 1 to 3 for these cells,
question 4 on their observation logs, and question 5 on macOS.

An addendum, registered and frozen before any Windows measurement, may add the Windows cells (the standard
Microsoft input method is planned) and question 5 on Windows; nothing else is added by addendum (section 8).
Other input methods, and applications other than the reference input field, are outside this study: each would
need its own procedures and ground-truth rules, calibrated in a pilot of its own, and would answer a different
question (whether the agreement transfers), which is left to a separate study.

## 2. Design

### 2.1 Type of study

A method-validation study with synthetic input only. No person takes part, and no person's typing is part of the
data. All key presses of the measurement are generated from a plan and sent by a program, and the rules of section
3.4 set aside a run whose logs show a sign of a person's key presses (section 7 states what they do not catch).

### 2.2 System under test and instruments

- **Observer** (`kit/logger`). The program under test. For the measurement it is built from an export of this
  directory made at the freezing commit; the SHA-256 of its manifest is written into the header of every log it
  produces (`manifest_sha256`), so that each log can be tied to the published code.
- **Reference input field** (`kit/refinput-macos`). A minimal application that implements the text-input client
  protocol of the macOS text system and records, with wall-clock and monotonic times, every call the input method
  makes: marked text set (with its length), text inserted (with its length), marked text accepted, a request for
  the position of the text, a command, each key-down it receives (key code only), and each change of focus. No text
  is recorded. It empties its text after every commit, so that no trial sees the text of an earlier one.
- **Executor** (`kit/emit`). Sends the planned key presses as system key events and records each press as sent
  (the oracle), with wall-clock and monotonic times taken immediately before dispatch. It refuses to start if
  either clock resolves more coarsely than 1 ms.
- **Runner** (`kit/run/run_cell.py`). Starts the reference input field and the observer, and before every block
  brings the reference input field to the front, confirms that it is in front, and switches the input method to
  Japanese by pressing the kana key. It then sends the block, and after the last block keeps both programs running
  for `settle_ms` (1,000 ms) before stopping them, so that the 50 ms window of the ground-truth rule (section 4.3)
  is observed after the last commit. A run is aborted if the front-window check fails or if either program stops.
- **Prediction** (`kit/analysis/spec_predict.py`). Applies the observer's specified rules to the oracle and the plan
  (section 4.5). Its selftest compares its rule tables with the observer's source code, so that a change on either
  side is detected.
- **Delivery report** (`kit/analysis/delivery.py`). Compares, press by press, the oracle with the plan, with the key
  rows of the observation and with the key-downs of the reference log (section 6.3).

### 2.3 Factors

- **Cell**: live conversion on, live conversion off. Each cell has its own procedure file:
  `kit/gen/recipes.json` (on) and `kit/gen/recipes.macos-live-off.json` (off). They differ only where live
  conversion changes how the input method responds to Return and Escape.
- **Scenario**: S1 to S15 (table below). S9 (choosing a candidate with the mouse) has no procedure, because no means
  of locating the candidate window for a scripted click was established; it is not run and is reported as not
  evaluated. S11 (conversion without Space under live conversion) is run only with live conversion on. A cell
  therefore has 14 scenarios (on) or 13 (off). With live conversion on, S3 and S11 have the same procedure (type the
  reading, Return) and the same intent; they are kept as two scenarios because they were designed to test different
  behaviours of the input method, and they are reported separately, each with its own trials. In pooled values each
  counts with its own trials.
- **Speed**: three levels of the interval between key presses (section 3.3).
- **Think time**: in scenarios with a pause before the decisive key, the pause is drawn per trial from a
  log-uniform distribution between 0.2 and 20 seconds. Its role in the design is to place sessions on both sides of
  the observer's 20-second cut-off. It is not a factor of any registered analysis; any analysis of the dependence
  of an estimate on think time is exploratory (section 6.12).

| Scenario | Procedure (live conversion on) |
|---|---|
| S1 | Type the reading, Space, pause, Return twice (commit) |
| S2 | Type the reading, Space two to five times (cycling the candidates), pause, Return twice |
| S3 | Type the reading, Return (commit without conversion) |
| S4 | Type the reading, Space, pause, Escape twice (cancel with the candidate window shown) |
| S5 | Type the reading, Escape four times (cancel without conversion) |
| S6 | Type the reading, Backspace once per kana typed (delete everything) |
| S7 | Type the reading, Space, right arrow one to three times, pause, Return twice |
| S8 | Type the reading, Space twice, pause, a digit choosing a candidate, Return |
| S10 | Type the first half of the reading, Tab (a predictive candidate), pause, Return |
| S11 | Type the reading, Return (live conversion converts without Space) |
| S12 | Type a kana string with the kana keyboard layout, Space, Return twice |
| S13 | Type the reading, Space, wait 21 seconds, Return twice (a session over 20 seconds) |
| S14 | Type the reading, Space, switch the input mode off and on |
| S15 | Type the reading, Space, bring another application to the front for one second, return, Return twice |

With live conversion off, a single Return commits and a single Escape cancels without conversion, so S1, S5, S7,
S12, S13 and S15 use one Return or one Escape where the table shows two or four. These procedures were fixed in
the calibration pilot (section 3.1).

### 2.4 Plans and randomisation

Each cell has one plan, generated by `kit/gen/generate_plan.py` from the settings in
`experiment/plan_config.macos.json`, the cell's procedure file, the phrase corpus `kit/corpus/data/phrases.jsonl`
(SHA-256 `c8f890cb4d0848bf04e6d7d95efe4e4d3dfd02a23eb1c7f103344f14d13e4620`) and the seed in `experiment/SEED`
(20260929):

    python3 kit/gen/generate_plan.py --config experiment/plan_config.macos.json \
      --recipes kit/gen/<procedure file of the cell> --corpus kit/corpus/data/phrases.jsonl \
      --os macos --seed 20260929 --out <plan>

A block holds 25 trials of one speed. The order of trials within each speed, the cut into blocks, the order of
blocks and the order of scenarios within a block are all determined by the seed, and each trial draws its phrase,
key intervals, think time, candidate index and arrow count from a random state derived from the seed and the
trial identifier. The plan records the SHA-256 of the settings, procedure and corpus files it was built from. The
plans were generated before the freeze on two machines (Python 3.11.9 and 3.14.4) with byte-identical results:
SHA-256 `c7a5469de0febbbc2e90259e477900ab0eb68c74b86f420b1e9491799a1210ba` (live conversion on, 3,150 trials in
126 blocks) and `3759ebf7832ba561820abab504929dbadd7e775a33e22ba0e89b92e4800009a2` (off, 2,925 trials in 117
blocks). The plan used for a run is generated on the measuring machine; a run whose plan has a different SHA-256 is
not analysed, and the run manifest records the SHA-256 of the plan it used.

### 2.5 Blinding

The observer's session rows of the calibration pilot were never read. Before any analysis of the pilot, each
observation log was split into its key rows, which were analysed, and its session rows, which were written to a
sealed file padded to a multiple of 1 MiB (`kit/pilot/seal_obs.py`), so that not even their number could be
inferred; the sealed files were not opened. The key rows that were read are, on macOS, the very input from which
the observer infers its sessions: the blinding withheld the observer's output, not its input. The ground-truth
definition, the procedures, the analysis code and the analysis settings were all fixed without reference to any
session row of the observer. The prediction of section 4.5 uses only the oracle and the plan. No session row of the
measurement is read before the freeze.

## 3. Sampling plan

### 3.1 Data that exist before the freeze

A calibration pilot was run on the measuring machine before the freeze; it is reported in
`experiment/pilot-macos.md`. The criteria that a run of the pilot has to meet for its raw logs to be deposited are
written there before the freeze. The raw logs of the runs that meet them are deposited with the measurement data.
The observation logs of the pilot are deposited whole, so their
sealed session rows (section 2.5) are unsealed after the measurement only to be deposited; they are not analysed,
not even in an exploratory analysis.
Its runs used provisional settings: one to three trials per scenario and speed,
key-interval medians of 300, 200 and 120 ms, a three-key block-start marker (F13, F14, F13 in the first pilot and
`left`, `right`, `left` from then on), and seeds 1 to 6 instead of the one in `experiment/SEED`. Their other
generator settings (the spread of the key intervals, the key hold, the think time, the candidate index, the
arrow count, the phrase length, and the gaps between trials and between marker presses) were those of
`experiment/plan_config.macos.json`. Their plans requested an input-method reset before each block, but no reset
command was given, so no reset was made. Only the reference side was analysed: the reference input field's
calls, the oracle, the plan, and the key rows of the observation. The following were computed from it and
informed this protocol:

- the call sequences the reference input field receives in each scenario, from which the ground-truth rule of
  section 4.3 was fixed (in particular the 50 ms continuation window for live conversion);
- the agreement between the plan and the ground-truth sessions (124 of 126 trials with live conversion on, 117 of
  117 with it off, 39 of 39 with the procedure file for live conversion off), from which the procedures were
  revised and the carry-over exclusion (section 6.2) was defined;
- the clock differences between the observation, the reference input field and the executor at the block-start
  markers;
- the fraction of sent key presses that produced a key row in the observation (4,072 of 4,090 and 3,972 of 3,990;
  the missing presses were the input-mode keys, which the observer does not record by design);
- the time per trial, from which it was checked that a run fits in one night (section 3.3).

In addition, outside the runs, a few key sequences were typed by hand on the measuring machine, in the reference
input field and in another application, to check whether the response of the input method to a single Return
after a conversion depended on how the key was sent (it did not). These checks were diagnostic; their records are
not part of the data and are not deposited.

No session row of the observation was read, no agreement between the observation and the ground truth was
computed, and no text-recoverability or runtime analysis was run on pilot data. Two checks of the analysis code are
made on the pilot's logs before the freeze and reported here; neither reads a session row.

- The assignment of observed key presses to trials that both attacks of question 4 use (section 6.8) is run,
  without any attack, on the key rows of the observation logs of the three pilot runs that used the arrow-key
  marker (`pilot2-on`, `pilot2-off` and `verify-off`; the F13 and F14 presses of the first pilot's marker produce
  no row in the observation), with `--markers-in-obs` and the pilot's three-press marker, and the fraction of blocks
  it scores is reported. This is a diagnostic of the code: if it reveals a defect, the defect is corrected before
  the freeze, and the fractions before and after the correction are reported. Run with the assignment code of this
  registration (the marker looked for at the right end of a run of arrow presses, section 6.8), it scored every
  block of the three runs (15 of 15 blocks, 282 trials) and left no press unassigned, so no defect was found and the
  code was not changed. These logs had no missing or added press, so the check shows that the assignment scores
  clean logs whole; it does not exercise the resynchronisation, which only the selftest's cases do.
- `kit/analysis/delivery.py` is run with the arguments of section 3.4 on the same three runs, and it is reported
  whether the runner's kana press before each block and the input-mode keys of S14 leave key-downs in the reference
  log, the largest count per segment of the reference's key-downs that the sent presses do not explain, the least
  time between the last key-down of a segment and the start of the next block, and the number of losses of focus
  of the reference outside the planned switches of S15. From these the two settings of the rule on presses of
  section 3.4 are fixed: k, and whether the input-mode keys of S14 count among the presses sent. Only counts and
  times are reported; no key code is read or written. The kana press left no key-down in the reference log: in
  every segment of a block the key-downs that the sent presses do not explain were the block's three marker
  presses, and the segment before the first block held no key-down (15 kana presses in all). Neither input-mode key
  of any S14 trial left a key-down either: of the presses sent inside the trials, 4,072 of 4,090, 3,972 of 3,990
  and 1,174 of 1,180 reached the reference, and the 18, 18 and 6 that did not are the two input-mode keys of each
  S14 trial. Hence k = 0, and the input-mode keys of S14 do not count among the presses sent. With these settings
  no segment of the three runs exceeds its bound (the largest count beyond the marker presses is 0). The least time
  between the last key-down of a segment and the start of the next block was 1,582 ms. The reference lost the focus
  outside the planned switches once, in `pilot2-off`: for 31 ms, in the think time of an S1 trial, 2.7 seconds
  after the last press sent, with no press sent within 1 second after it; the observation holds no key row within
  the loss widened by 500 ms on each side (the nearest is that of the last press sent). There was no such loss in
  the other two runs, nor in the three other runs of the pilot to which the same report was applied (`pilot-1`,
  `verify-1` and `verify-s5`; about 87 minutes in the six runs). One loss in about 87 minutes is a single
  observation, so the rate is uncertain, but a rule that aborts a run at any such loss could abort most runs of
  about 12 hours. Before the freeze the rule on the loss of focus was therefore changed to abort a run only when the
  observation records, within a loss, key presses that the executor did not send (section 3.4). A first version of
  the change counted every key row within the loss; in the oracles built from the measurement plans (below), about
  78% of the time of a run lies within 500 ms of a press sent, so that version would have aborted a run at most
  losses during typing, since the executor goes on sending during a loss. The rule as registered (section 3.4)
  also covers the planned switches of S15 and matches the key rows to the presses sent one by one; this final form
  was not applied to the pilot's logs before the freeze (they are on the measuring machine), only the earlier forms
  were. It is applied to them when they are deposited (`experiment/pilot-macos.md`).

Before the freeze, the prediction of section 4.5 was also computed on the two measurement plans, with an oracle
built from the plans' own times (no sending delay; 2 s between blocks), by
`python3 kit/analysis/spec_predict.py --from-plan --plan <plan> --out <file>`. No observation exists or was read for
this. With live conversion on, it predicts 2,638 rows and 719 sessions cut off at 20 seconds; 755 of the 3,150
trials are long (their presses went into a session that is cut off: 447 of the 1,050 slow trials, 198 mid and 110
fast); in 188 trials (all S6) a session runs on into a later trial; in 184 trials presses went into a session
carried over from an earlier trial (113 of them start no session of their own); 314 trials start more than one
session; and 113 trials send the minus, comma or period key (76 of them are S12 trials, in which these keys type
ho, ne and ru). With live conversion off the corresponding figures are 2,417 rows, 682 sessions cut off, 713 long
trials of 2,925 (422 slow, 192 mid, 99 fast), 186 (all S6; 185 of them run on into a later trial, and the session
of the other is cut off by a press outside the trials), 184 (109), 280 and 99 (64 in S12). The number of trials per
stratum that remain under the cut-off is given in section 3.3. The same command also writes the figures of the
plans that sections 3.3 and 6.7 quote (`plan_figures`: per stratum, the number of trials and of blocks and the sum
of m_j squared over the sum of m_j, for the planned trials and for those that are not long; per speed, the
distribution of the number of trials of a text that are not long; and the fraction of the time of the oracle at
which a loss of focus of 31 ms, widened by 500 ms or 100 ms on each side, would hold a press sent, which is 78.1%
and 45.9% with live conversion on and 77.0% and 45.0% with it off). The values of the analysis are computed from
the oracles of the measurement, not from these.

### 3.2 Data collection

Each cell is measured in one run on the measuring machine (Apple Silicon, macOS 27.0 or the version recorded in
`experiment/env.md`, JIS keyboard, Romaji input). The machine runs on mains power, with automatic updates, network
time synchronisation, sleep, screen lock and the screen saver turned off. The input method's predictive candidates
are left at their default (shown). The input method's learning is not reset: no method of resetting it was found,
and the plans request no reset (section 7). The run manifest records the system and input-method versions and
these settings.

The runs are made on separate nights in this order: the cell with live conversion on, the cell with live
conversion off, then the two conditions of question 5 (section 6.10), `idle` and then `working`. The order is
fixed so that the replay of section 6.10 adds no learning before the cells of questions 1 to 4 are measured. An
attempt that has to be repeated (section 3.4) is repeated before the next item of this order is run, so the order
is kept; the learning that the repeated attempt added is not undone (section 7). The check of section 3.4 is made
right after each run of the two cells and of the `working` condition (the `idle` condition sends no key and has
no plan), before the next item of this order.

Before each block the runner switches the input method to Japanese by pressing the kana key, after confirming that
the reference input field is in front. A block begins with a marker of four arrow presses (left, right, left,
right), 150 ms apart, which both logs record and which is used to measure the clock differences; it is longer than
the longest run of arrow presses inside any trial, so it cannot be confused with one. Trials are separated by 1,000
ms.

### 3.3 Sample size, precision and settings of the plan

- **Trials**: 75 per cell, scenario and speed: 3,150 trials with live conversion on and 2,925 with it off. The
  number was chosen for the precision of the per-stratum proportions (table below): near a proportion of 0.95 the
  half-width of a 95% Wilson interval is about 5 percentage points with 75 trials. It was chosen before the key
  intervals of the measurement were set (below), and it was not revised when the prediction later showed how many
  trials the cut-off sets aside (section 3.1); the strata of the primary scope are therefore smaller (below). That a
  run fits in one night is a secondary justification: the generator estimates 11.9 and 11.2 hours (13.6 and 13.8
  seconds per trial, including waits and markers; the estimate leaves out the start of each block), and in the
  pilot the measured time per trial (11.0 seconds with live conversion on, 11.9 with it off) was close to the
  generator's estimate for the pilot's key intervals (11.2 and 11.4 seconds).
- **Precision of proportions**: half-width, in percentage points, of the 95% Wilson interval for round(n x p)
  successes in n trials, for the numbers of trials that occur in this design (75: a planned stratum; 225: a scenario
  over the three speeds; 1,050: a speed over the 14 scenarios; 3,150: a cell; 31: the smallest stratum under the
  cut-off other than those of S6 and S13, see below):

  | n | p = 0.5 | 0.8 | 0.95 | 0.99 |
  |---|---|---|---|---|
  | 31 | 16.6 | 13.5 | 9.5 | 5.5 |
  | 75 | 11.0 | 8.9 | 5.4 | 3.5 |
  | 225 | 6.5 | 5.2 | 2.9 | 1.5 |
  | 1,050 | 3.0 | 2.4 | 1.3 | 0.6 |
  | 3,150 | 1.7 | 1.4 | 0.8 | 0.4 |

  With 75 trials a stratum's recall near 0.95 is located to within about 5 points; this does not guarantee that
  0.95 and 0.90 are told apart (71 of 75 gives the interval 0.871 to 0.979). With 31 trials it is located to within
  about 9 points (31 of 31 gives a lower end of 0.890). In the primary scope a speed level holds 603 (slow), 852
  (mid) and 940 (fast) trials with live conversion on and 553, 783 and 876 with it off (about one session each),
  which locate a recall near 0.95 to within 1.4 to 1.8 points if the trials are treated as independent; a speed
  level is made of whole blocks, so its design effect can reach 25 (below). Pooled values carry intervals that
  ignore the clustering of trials in blocks (section 6.1) and are read as lower bounds of the uncertainty.
- **Trials under the cut-off**: questions 1 to 3 are answered primarily over the trials whose presses went into no
  session that the observer's rules cut off (section 6.4), so their strata are smaller than 75. From the prediction
  on the measurement plans (section 3.1), the trials that remain per scenario and speed (slow / mid / fast), before
  the exclusions of section 6.2, which can only reduce them, are:

  | Scenario | on | off |
  |---|---|---|
  | S1 | 31 / 69 / 73 | 41 / 66 / 71 |
  | S2 | 37 / 60 / 69 | 38 / 65 / 72 |
  | S3 | 59 / 72 / 75 | 58 / 73 / 75 |
  | S4 | 38 / 63 / 71 | 37 / 61 / 74 |
  | S5 | 60 / 71 / 75 | 64 / 71 / 75 |
  | S6 | 3 / 27 / 61 | 5 / 30 / 70 |
  | S7 | 38 / 61 / 70 | 37 / 60 / 73 |
  | S8 | 35 / 62 / 71 | 31 / 64 / 66 |
  | S10 | 71 / 75 / 75 | 72 / 74 / 75 |
  | S11 | 55 / 72 / 75 | not run |
  | S12 | 74 / 75 / 75 | 75 / 75 / 75 |
  | S13 | 0 / 0 / 0 | 0 / 0 / 0 |
  | S14 | 53 / 74 / 75 | 50 / 71 / 75 |
  | S15 | 49 / 71 / 75 | 45 / 73 / 75 |

  S13 is designed to exceed the cut-off and is examined in the over-limit scope (section 6.4); S6 keeps its session
  open with Backspace, so at the slow and middle speeds it is mostly carried into the next trial and cut off there,
  and the trial that received it is set aside with it. Outside S6 and S13, six strata of each cell (all slow) hold
  fewer than 50 trials. Strata are reported with their numbers and intervals, or as null when they are empty
  (section 6.11).
- **Precision of continuous measures and the design effect**: for a mean difference (the bias of `dwell_ms`, a per-text
  bias), the half-width of the 95% interval is about 1.96 x SD x sqrt(D / n), where SD is the standard deviation of the
  differences, n the number of pairs or texts, and D the design effect of the blocks. With m_j items in block j, D = 1
  + (sum of m_j squared / sum of m_j - 1) x rho, where rho is the correlation within a block (for blocks of equal size
  m, 1 + (m - 1) x rho). The plan shuffles the trials of a speed before cutting them into blocks, so the 75 trials of a
  planned stratum fall unevenly into 33 to 39 of the 42 blocks of their speed with live conversion on and into 30 to 37
  of the 39 blocks with it off; computed from the two measurement plans, sum of m_j squared over sum of m_j is 2.4 to
  3.1 (on) and 2.5 to 3.4 (off) across the strata. Over the trials of the primary scope (section 6.4) it is at most
  3.05 (on, S14 middle) and 3.22 (off, S15 middle), within the same bound (`plan_figures`, section 3.1). D is therefore
  at most about 3.4 within a stratum, and near 1 only when rho is small. With D = 1 the half-width is about 0.23 x SD
  with 75 pairs (for example 2.3 ms when SD is 10 ms, 11 ms when it is 50 ms) and about 0.35 x SD with 31, the smallest
  stratum of the primary scope other than those of S6 and S13; with D = 3.4 it is about 1.8 times as wide. Over a speed
  level (about 1,050 planned pairs and 553 to 940 in the primary scope, m about 25) it is about 0.06 to 0.08 x SD x
  sqrt(D), with D between 1 and 25. Per speed there are 126 texts (on) and 117 (off) in 42 and 39 blocks (three texts
  per block). The limits of agreement (the bias plus or minus 1.96 SD) are located to about 1.96 x SD x sqrt(3 x D / n)
  (Bland & Altman, 1986), about 0.39 x SD with 75 pairs and D = 1. No width is predicted for kappa and the ICC, because
  it depends on the values estimated; they are read through the intervals reported with them.
- **Key intervals**: log-normal, with medians of 480 ms (slow), 240 ms (middle) and 120 ms (fast) and a log-scale
  standard deviation of 0.3. The medians follow the per-participant mean inter-key intervals that Dhakal, Feit,
  Kristensson and Oulasvirta (2018) report for the slowest tenth of their participants (481 ms), for all
  participants (239 ms) and for the fastest tenth (122 ms). Those values come from transcription typing of English
  sentences; they are used to span the range of typing speeds, not as estimates of Japanese typing. With a
  log-scale standard deviation of 0.3 the mean interval is about 4.6% above the median. The spread is a design
  choice that makes intervals vary; it is not estimated from data. An interval shorter than the key hold plus 1 ms
  (61 ms) is raised to it, which affects about 1% of the intervals of the fast level. The intervals are drawn
  independently of the keys (section 7).
- **Key hold**: 60 ms from key-down to key-up. The generator never overlaps two presses, so the hold must stay below
  the shortest intervals of the fast level; the input method acts on key-down, and key-up times are not part of
  any measure.
- **Other settings**: candidate index up to 4 (S2, S8), up to three arrow presses (S7), kana strings of four to
  eight characters (S12).

### 3.4 Stopping rule

Each cell is run once to completion. If a run is aborted (the runner stops it), the cell is run again from the start
with the same plan, in a new run directory, up to three attempts. A completed run also counts as aborted for any of
four reasons: (1) its plan is not the plan that section 2.4 gives its cell (live conversion on or off); (2)
`kit/analysis/delivery.py` refuses its logs (exit status 2); (3) its reference log holds more key presses than the
executor and the procedure explain (presses from outside, below); or (4) within a loss of focus of the reference input
field, the observation log holds a key press that no press sent by the executor explains (loss of focus, below).
Reasons 3 and 4 are decided by closed rules from the delivery report of the run directory alone. Right after the run
(section 3.2), and before any analysis of it, the report is made with

    python3 kit/analysis/delivery.py --plan <run>/plan.jsonl --oracle <run>/oracle.jsonl --obs <run>/obs.jsonl \
      --gt <run>/gt.jsonl --pre-ms 20 --tail-ms 300 --out <report>

which reads the key rows of the logs and the focus changes of the reference, not the session rows.

**Presses from outside.** The report divides the time of a run at the block starts that the oracle records into
segments: the time before the first block, and each block from its start to the start of the next block (the last
block to the end of the logs). For each segment it counts the key-downs of the reference that the sent presses do
not explain: the key-downs in the segment, minus the presses of the segment's trials that reached the reference (in
each trial, the smaller of the number of presses sent and the number of the key-downs assigned to the trial that lie
in the segment). A press that the oracle does not record adds one to the count of its segment whether it lands
inside the interval of a trial or outside it, unless a press of that trial was lost (see below). Outside the trials
the procedure makes presses that the oracle does not record as sent: the marker presses of a block (four, as the
plan states) and the kana press with which the runner switches the input method to Japanese before each block
(section 3.2). Let k be the number of key-downs that one such kana press leaves in the reference log. The run counts
as aborted when the count of any segment exceeds the presses that the procedure makes in it: k for the segment
before the first block, the marker presses of the block plus k for every other segment except the last, and the
marker presses alone for the segment of the last block, after which no kana press is made. Two settings of the rule
were fixed before the freeze from the logs of the pilot (section 3.1): k = 0, because the kana press left no key-down
in the reference log, and the input-mode keys of S14 do not count among the presses sent, because they left none
either (counting them would leave room for a press from outside in every trial of S14). The count of every segment
is therefore bounded by the marker presses of its block (0 for the segment before the first block), and the version
`excluding_mode_keys` of the report applies. A kana press that does leave a key-down in a run of the measurement
makes the run count as aborted wherever it falls; it cannot let a press from outside pass.

**Loss of focus.** A loss runs from a focus-out of the reference to its next focus-in, or to the end of the logs when
the focus does not come back, in the order of the reference log; the planned switches of S15 are losses too. The
observer is started before the reference input field and stopped after it, so the time before the reference first has
the focus (the whole run when it never has it) and the time after the last row of its log count as losses as well. Each
loss is widened by 500 ms on each side, and losses whose widened spans meet are taken together; the margin covers the
offset between the clocks of the two logs, the delay with which a key event reaches the observer, and a key that moves
the focus, which is pressed just before the loss. Every key row of the observation log is matched, once for the whole
log, to at most one press that the executor sent and that the observation records (the presses of the trials other than
the input-mode keys, and the marker presses): in time order, each row takes the earliest press not yet taken that was
sent from 300 ms before the row to 20 ms after it. The run counts as aborted when a row that takes no press lies in a
widened span or in the stretch counted after it (below). Because the matching runs in time order, a person's press that
falls between a press sent and that press's own row takes the press and leaves the later row without one; while the
presses sent follow each other within 320 ms, that shortage can move on from row to row. The rows are therefore also
counted in a stretch after the end of the span: up to 320 ms after it, and further up to 300 ms after the last of the
presses sent that follow the end of the span at intervals of at most 320 ms. A person who types into another
application takes the focus away from the reference, and the observation records the presses of the keys it observes
(all but the input-mode keys and a few others, such as F13 to F19) whichever application has the focus, so such presses
appear as rows without a press sent. The executor goes on sending during a loss, a planned switch of S15 sends no key
(the other application is brought to the front by a command), and before the first block the executor sends no key that
the observation records, so neither a loss during typing, nor a planned switch, nor the start or the end of a run, nor
a loss in which no key is pressed makes the run count as aborted by itself. The margin of 500 ms and the window of 300
ms are design choices, not measured values; the window is well above the largest difference between the executor's and
the observation's clocks that the pilot showed (86 to 95 ms in the first block of a run and 0 to 3 ms in the others,
`experiment/pilot-macos.md`).

No other reason and no explanation is admitted. The rules apply to the runs of the two cells and of the `working`
condition of question 5. The `idle` condition sends no key and has no plan; its observation log is not deposited,
because the observer is started before the command that begins the sampling is typed, so the log holds a person's
key presses (only the samples of section 6.10 are deposited).

The result of the check, its time, the SHA-256 of the plan, the largest count per segment beyond the marker presses and
the number of widened spans that hold a row without a press sent are recorded in the table of run checks in
`experiment/data.md` before any analysis of the run. `kit/analysis/pipeline.py` applies reasons 2 to 4 to the delivery
report it computes and, on real data, reason 1 to the plan of each run (the two SHA-256 of section 2.4 are constants of
the code, read against the live-conversion setting of the run's cell); it refuses to analyse a run that they catch. A
completed run that counts as aborted is not deposited: it is not analysed, and under reasons 2 to 4 its logs may hold a
person's key presses (the reference log records the key code of every key-down it received, and the observation log
records the presses of the keys it observes on the machine). The same check is made on a run that the runner aborted:
such a run is never analysed, and it is deposited only if the check passes or if it left neither a reference log nor an
observation log. A run that the runner aborts inside a trial does not pass: that trial has no end in the oracle, so its
presses that reached the reference count as not explained (here the count does not mean a press from outside).
`pipeline.py --pack` and `--check-deposit` apply reasons 2 to 4 and the content-free check of section 5 to every run
directory, completed or aborted, and refuse one that they catch or that cannot be checked (an observation log without a
reference log, or logs that `delivery.py` refuses); reason 1 is checked by hand before a run is deposited
(`experiment/data.md`), because the runs of the pilot, which used other plans, are deposited with the same commands.

The rule on presses counts the key-downs that remain beyond the presses that reached the reference, so a lost press
and a press from outside cancel out: a marker press that does not reach the reference against a press from outside
anywhere in the same segment, and a press of a trial that does not reach it against a press from outside in the
interval of the same trial. Such a replacement is not detected (section 7). Outside the widened spans of the losses
of focus and the stretches counted after them, rows of the observation without a sent press are a result of the
observer under test (section 6.3) and never make a run count as aborted; inside them, the rule counts such a row
whatever its cause (section 7). Aborted runs are reported with their reason and their delivery figures, and are
not analysed. A cell that is not completed in three attempts is reported as not measured. The same rules apply to each
condition of question 5: a condition that is aborted or insufficient (section 6.10) is run again on another night, up
to three attempts in all, and every attempt is reported.

## 4. Variables

### 4.1 Manipulated

Cell, scenario and speed (section 2.3); think time varies within scenarios (section 2.3).

### 4.2 Recorded

- The plan (`plan.jsonl`): every trial's scenario, speed, phrase, intent and key schedule.
- The oracle (`oracle.jsonl`): every press as sent, the block markers, and each trial's begin and end.
- The ground-truth log (`gt.jsonl`): the reference input field's calls (kinds, lengths and times only), the key
  code of each key-down it receives, and each change of its focus.
- The observation (`obs.jsonl`): key rows (category and time) and session rows (`t`, `dwell_ms`, `nav`,
  `abandon`).
- The run manifest (`run.json`): the cell settings, the SHA-256 and line count of every log, the SHA-256 of the
  plan, the system and program versions, and the outcome of every front-window check.

### 4.3 Ground-truth sessions

Sessions are assembled from the reference input field's log by `kit/analysis/gt_sessions.py` with the `mac_nstic`
rules of `experiment/gt_rules.json`:

- A session opens at the first call that sets marked text of positive length.
- It closes as committed at a call that inserts text of positive length, or that accepts the marked text, when no
  call setting marked text of positive length follows within 50 ms.
- It closes as cancelled at a call that sets marked text of length zero, when no call inserting text of positive
  length follows within 50 ms.

With live conversion the input method commits the head of a reading while the rest is still being typed; the
50 ms window keeps such a commit inside its session (in the pilot the following call came 0.5 to 7.9 ms later,
and every window between 10 and 900 ms gave the same sessions). The outcome of a session is the reason it finally
closed; a session whose head was committed this way and whose rest was cancelled is a cancellation. Each session
also records how many commits were followed by a continuation (`n_continued_commits`).

A session is assigned to the trial it overlaps most in wall-clock time. Its true navigation count is the number of
arrow presses the oracle records as sent for that trial within the session. A session is reported as lasting 20
seconds or more when its duration reaches 20,000 ms.

### 4.4 Observation intervals

A session row's interval is `[t - dwell_ms, t]` on the wall clock. No offset is added at either end, and no
clock correction is applied (section 6.9). The time `t` is the time at which the row was collected, which can be up
to about 100 ms after the session ended; `dwell_ms` is measured from the session's own start and end and does not
include this delay. The observer holds one finished session until it is collected, so a session that ends before
the previous one was collected replaces it and one row is lost; the prediction does not model this, and such a loss
appears as a missed row in `pred_obs` (section 6.4).

### 4.5 The observer's specified rows (prediction)

`kit/analysis/spec_predict.py` applies the observer's rules (section 1.1, as written in its source: the mapping of
macOS key codes to virtual-key codes, the order in which a key updates the input-mode flag and then drives the
session, the transitions of the session and the 20-second cut-off) to every press in the oracle, in the order and
at the times the oracle records. It also models what the oracle does not record: the kana key that the runner
presses at the start of every block, and the keys of the marker presses (the oracle records their times; the keys
come from the plan). Presses that send no key event (the
switch to another application in S15) are skipped. The result is, per run, the list of sessions the observer is
specified to form, each with its start, end, `nav`, `abandon`, how it ended (a row, cut off, or open at the end of
the log) and the trials whose presses entered it (the press that cuts a session off does not enter it), and per
trial:

- **long**: a press of the trial entered a session that is cut off at 20 seconds (the session may have started in
  the trial or have been carried over from an earlier one);
- **carried out**: a session that the trial started does not end in the trial (it ends in a later trial, is cut off
  by a press outside the trials, such as a marker press, or is still open at the end of the log);
- **received carry**: a press of the trial entered a session started by an earlier trial;
- **closing key in reading**: the trial sends the minus, comma or period key, which closes a session under the
  observer's rules (in Romaji input these keys type a long-vowel mark, a comma and a full stop; in the kana layout
  of S12 they type ho, ne and ru).

A trial can carry several of these marks. The predicted rows are the predicted sessions that end with a row; like
the ground-truth sessions, each is assigned to the trial it overlaps most. Their times are the oracle's dispatch
times; the observer's rows carry the time at which the event reached the observer, and the time at which the row
was collected (section 4.4).

### 4.6 Texts

For the per-text measures, the trials of each block are cut, in plan order, into texts of eight consecutive
trials; the remainder of a block (one trial with blocks of 25) is not used. Every text is of one speed. The four
measures are computed for every text on both sides, over the trials of the text that are in the scope of the
analysis (section 6.4): the conversions per trial (sessions or rows over the number of those trials), the mean
conversion time (total duration over the sessions, or over the rows with a non-zero `dwell_ms`; every session has a
positive duration), the abandonment rate (cancelled sessions, or rows with `abandon` set, over all), and the
fraction of time spent converting (total duration over the summed duration of those trials, each from its begin to
its end). A text with no trial in the scope has no value (section 6.11). The four measures are examples of
aggregates a writing researcher might compute from such a log; they were not selected from a review of the writing
literature.

## 5. Analysis code and settings

The whole analysis of questions 1 to 3 is run by one command, `bash experiment/run.sh`, which reads the run
directories listed in `experiment/data.md`. The analysis code, including the prediction and the key map it reads
(`kit/emit/keymap_macos.json`), is fixed by the tag of the freezing commit; the analysis is run from a clean checkout
of that tag, and the commit is recorded with the results in `experiment/notes.md`. The result files record the
SHA-256 of the configuration, the rule file, the key map, every manifest and every input log. The settings are in
`experiment/config.json`: overlap criterion `iou_min` = 0.5, observation end offset 0 ms, `theta` = 0.5, sessions
with a start inside an open session not dropped, texts of 8 trials, 2,000 bootstrap resamples, cut-off limit 20,000
ms, and three mixtures of speeds for the mixture analysis. The ground-truth rules are in `experiment/gt_rules.json`
and the seed in `experiment/SEED`. Before any statistic, the pipeline checks the freeze marks, the SHA-256 of every
log against its manifest, the content-free format of every log, and that the observation declares both of its
channels, and it refuses a run that the rules of section 3.4 catch.

The definition of the cell's system (`macos_def`) is the definition of the analysis. The analysis is also run under
the definition common to both systems (`all`); on the reference side of a macOS cell the two hold the same sessions,
which the pipeline checks and reports (`same_sessions_as_all`), and the results under `all` are not otherwise
reported.

## 6. Analysis plan

### 6.1 Units, intervals and wording

The primary estimates are given per stratum: cell, scenario and speed. Values per speed (pooled over the scenarios)
and per cell (pooled over the strata) are sums of the counts, or collections of the pairs, of their strata; every
stratum has the same number of planned trials, so the strata weigh about equally apart from exclusions and the
cut-off scope. Because synthetic input has no natural mix of scenarios, a pooled value is not an estimate of
accuracy in natural typing, and it is labelled as pooled. In the primary scope the trials that remain differ between
speeds in composition: at the slow speed more of the trials with long readings or long pauses are set aside, so a
difference between speeds is not an effect of speed alone. The number of letter keys and the think time of the
trials in each scope are reported per stratum, from the plan.

Proportions carry 95% Wilson intervals (Wilson, 1927), which treat trials as independent: within a planned stratum
the design effect of the blocks is at most about 3.4, an interval at most about 1.8 times wider (section 3.3), and
for pooled proportions the interval is read as a lower bound of the uncertainty. Continuous measures, the bias and
the limits of agreement, kappa, the ICC and the biases under the mixtures of speeds carry 95% percentile bootstrap
intervals (Efron, 1979) that resample whole blocks (Cameron, Gelbach, & Miller, 2008), because trials in one block
share the state of the input method; the biases under the mixtures resample the blocks of each speed separately.
F1, counts, the mean rank and the per-scenario and per-speed accuracies of question 4, the delivery figures of
section 6.3 and the figures of sections 6.9 and 6.10 carry no interval. All resampling uses the seed in
`experiment/SEED`, with 2,000 resamples.

In the abstract and the conclusions, estimates are given as the ranges of their intervals, without evaluative
words (such as good, high or acceptable agreement); no threshold of adequate agreement is set. An estimate in the
primary scope is given together with the fraction of trials that the scope sets aside and the estimate under
`all_trials` (section 6.4).

### 6.2 Exclusions and other contingencies

Applied before matching, and reported by reason, stratum and number. These are the only exclusions:

- `clock_jump`: two adjacent events of the reference or oracle log whose wall-clock and monotonic differences
  disagree by more than 1,000 ms. The affected trials, from the jump to the end of its block, are excluded. The
  threshold equals the wait between trials, so a step of the wall clock that could move an event into another trial
  is caught; a smaller step is not excluded, and with network time synchronisation turned off (section 3.2) none is
  expected.
- `log_truncated`: a session of the ground truth (assembled from the reference log) still open when that log ends.
- `carry_over`: a session of the ground truth that overlaps two or more trials. The composition was not closed in its
  own trial and ran on into the next, so it cannot be assigned to one trial, and every trial it overlaps is excluded
  (in the pilot, two carry-overs with live conversion on, in S8 and S10, excluded four trials). This is the only
  exclusion that rests on what the input method did (section 1.2).
- Observation rows are assigned to the trial they overlap most and are excluded with that trial; the number of
  observation rows excluded is reported by reason.
- Aborted runs (section 3.4).

Not excluded: trials in which the input method did not do what the plan intended (they are counted, section
6.3); sessions in which a start arrived while a session was open (counted); observation rows with `dwell_ms` equal
to zero, which the observer produces when the wall clock steps back (they stay in the matching as intervals of
zero length, so they count as false positives, and their number is reported in every bucket); and the trials whose
presses went into a session that the observer's rules cut off, which are set aside from the primary scope but
analysed (section 6.4).

Other contingencies:

- A loss of focus of the reference input field makes the run count as aborted when the observation holds, within
  it, a key press that no press sent by the executor explains (section 3.4). An analysed run can therefore have a
  loss, also during typing: the presses that the executor sends during it reach another
  application and not the reference, so their trials appear as disagreements between the plan and the ground truth
  (section 6.3). A loss lasts from a focus-out of the reference to its next focus-in (to the end of the log when
  none follows), so it can cover several trials, because the runner confirms the focus only before each block; an
  S15 switch is planned only when it begins and ends inside its own trial. These trials are not excluded: the delivery
  report counts them per stratum, and the detection results report the observation rows assigned to them, and how
  many of these are false positives, in total and per speed, so that a failure of the reference is not read as one
  of the observer.
- If the input method stops responding or restarts during a run, the affected trials are not excluded; they appear
  as disagreements and are reported. A run that the runner aborts is handled by section 3.4.
- If the system or input-method version recorded in a run manifest differs from that of an earlier run, the run is
  analysed as recorded and the difference is reported as a deviation (section 8).
- A text whose measure cannot be computed on one side is left out of that measure's figures and counted (section
  6.11).
- If the observed key presses of a block do not agree with the plan (a press missing, added or changed), the
  attacks of question 4 leave the trials of that block unscored, continue with the next block that matches, and
  report the unscored blocks and trials (section 6.8).
- The run of the `working` condition of question 5 is not used for questions 1 to 4.

### 6.3 Plan versus ground truth, and delivery of the presses

For every trial, the assembled sessions are compared with the plan's intent and classified as agreeing or as one
of the disagreement types (no session, an unexpected session, several sessions, a commit where a cancellation was
planned, a cancellation where a commit was planned, a session open at the end). The counts and rates are reported
with Wilson intervals, per stratum and overall. These trials stay in the evaluation: removing them would keep only
the trials in which the input method behaved as planned.

The delivery of the presses is reported descriptively, per cell and speed (`kit/analysis/delivery.py`): the delay of
each press against the plan's schedule; for the observation's key rows and for the reference log's key-downs, the
fraction of sent presses that arrived and the number of rows without a sent press (inside trials, and outside them,
where the block markers and the runner's kana presses fall), and per segment of a block the rows that the sent presses
do not explain (section 3.4); and, in the trials where every press arrived, the delay of each row after its press and
the error of each interval between consecutive rows. Input-mode keys, which the observer does not record by design, are
reported with and without. Whether rows without a sent press make a run count as aborted is decided by the rules of
section 3.4 alone (rows of the reference, and rows of the observation within a loss of focus or the stretch counted
after it); the other rows of the observation without a sent press are a result of the observer and stay in the
analysis. These figures concern events generated in software; no claim is made about the path from a physical keyboard
(section 7).

### 6.4 Scopes and comparisons

**Scopes.** The prediction (section 4.5) marks, from what was sent, the trials whose presses went into a session that
the observer's rules cut off at 20 seconds (long trials). The scopes select trials after the exclusions and, like the
exclusions, at the level of a trial (the sessions and the rows of a trial go together):

- `under_limit`: the trials that are not long. **The primary scope of questions 1, 2 and 3.**
- `over_limit`: the long trials only. For these trials the specified behaviour is that the session that is cut off
  produces no row; a letter or digit pressed after the cut-off opens a fresh session (for example the digit of S8
  after a long pause), whose row is assigned, like every row, to the trial it overlaps most (usually the trial in
  which that press falls).
- `all_trials`: every trial, reported as secondary.

The scope is decided from the oracle and the plan alone, so it does not depend on the observation. A session or row
that belongs to no trial is in `under_limit`. Question 1 is computed under all three scopes; question 2 under
`under_limit` and `all_trials`, with the checks of the cut-off under `over_limit` (section 6.6). Question 3 is
answered under `under_limit` (primary), in which the long trials are left out on both sides while the texts are
kept, and every measure of a text is computed over its remaining trials (section 4.6), and under `all_trials`
(secondary). The values under `all_trials` depend on this design's fraction of long trials (section 3.1), which
follows from its think times, speeds and procedures and is not a property of natural typing. The effect of the
cut-off on a researcher's aggregates follows from the rule stated in section 1.1. A session that is cut off has no
row; the key rows of a log show candidates for one (a run of letter keys that no session row covers), but on macOS
they do not record the input mode, so a candidate cannot be confirmed from the log alone. Leaving out these longest
conversions lowers the mean conversion time and the fraction of time spent converting. The conversions per trial
can move either way, because a letter or digit typed after the cut-off opens a session of its own (section 1.1).
The abandonment rate can move either way too, because a session that is cut off may have been committed or
abandoned.

**Comparisons.** The prediction also allows the disagreement between the ground truth and the observation to be
traced. Questions 1 and 2 are computed for three comparisons, with the same matching and the same measures, and
question 3 for the first only:

- ground truth against observation (`gt_obs`): the questions of this study. **Primary.**
- ground truth against prediction (`gt_pred`): how well the observer's rules, carried out exactly, describe the
  sessions of the reference input field (the validity of the rules). It is computed without the observation.
- prediction against observation (`pred_obs`): whether the observer carries out its rules (missed presses, timing,
  implementation). Here the predicted rows take the place of the ground truth; the exclusions of the ground truth are
  kept, so all three comparisons cover the same trials.

The two secondary comparisons are reported next to the primary one; no arithmetic relation between the three is
claimed. Disagreements that the rules predict (for example the session of S6 kept open by Backspace and carried into
the next trial, the session of S10 closed by Tab, a reading split by the minus, comma or period key, and, with live
conversion on, a session closed by the first of two Returns or Escapes while the ground truth closes at a later
one) are counted in `gt_pred` as disagreements of the rules; they are not removed from `gt_obs`. The primary scope
therefore sets aside only the cut-off; the other disagreements that the rules predict remain in the primary
estimates.

**Layers.** As a diagnostic, the detection measures of each comparison and scope are also reported, pooled and per
speed, for the trials of four layers from the prediction: carried out, received carry, closing key in reading, and
the trials in none of the three. A trial with several marks is counted in each of its layers. A carried-out session
contaminates the next trial whatever its scenario; the layers show where this happens.

### 6.5 Question 1: detection

Sessions and observation rows are matched one to one in descending order of interval overlap (intersection over
union) among the pairs that reach 0.5. Among the items left unmatched, one session with two or more rows whose
combined interval reaches the criterion is a split, and one row with two or more sessions is a merge; the rest
are misses and false positives. Because a fragment that reaches the criterion on its own becomes a one-to-one pair,
the split and merge rates are then recounted: an unmatched row whose length lies at least half (`theta`) within a
matched session counts toward a split, and an unmatched session lying at least half within a matched row counts
toward a merge. The primary pairing is not changed by the recount.

Reported per stratum, per speed and per cell, for each comparison and scope: recall (pairs over sessions),
precision (pairs over rows), split rate, merge rate, miss rate and false-positive rate, each with a Wilson interval,
and F1 without an interval.

### 6.6 Question 2: per-session agreement

Over the one-to-one pairs of each stratum, per speed and per cell, for each comparison, under `under_limit`
(primary) and `all_trials`:

- **Duration**: the error is `dwell_ms` minus the session's duration. Reported: the median absolute error with a
  bootstrap interval, and the Bland-Altman bias and 95% limits of agreement (Bland & Altman, 1986), each with a
  bootstrap interval. A row with `dwell_ms` equal to zero never enters a pair (section 6.2); the number of such rows
  is reported.
- **Abandonment**: the session's outcome (cancelled or not) against the row's `abandon` flag. Reported: the 2 by 2
  table, the raw agreement with a Wilson interval, and Cohen's kappa (Cohen, 1960) with a bootstrap interval. Within
  one stratum the ground truth has mostly one outcome, so kappa is often undefined there; kappa is read per speed,
  where both outcomes occur, and an interval is withheld, with its reason, when kappa is undefined in the sample or in
  any resample. The same figures are reported separately for sessions with and without a commit followed by a
  continuation (`n_continued_commits` greater than zero, or zero), as a diagnostic: with live conversion on, about 4
  in 10 cancellations in the pilot followed such a commit. This split does not replace the main figure.
- **Navigation**: the exact-agreement rate of `nav` with the true count, with a Wilson interval, and the
  distribution of the difference. The true count is the number of arrow presses sent (section 4.3), so this measure
  compares the observer with the executor, not with the reference input field.
- **Cut-off**: under `over_limit`, the number of rows of the long trials and how they compare with the predicted
  rows of those trials (`pred_obs`; specified: the same rows); in every scope, the number of rows of 20 seconds or
  more (specified: zero). The number of ground-truth sessions of 20 seconds or more that nonetheless appear in a pair,
  split or merge is reported as well; it is not specified to be zero, because the ground truth's session can last
  longer than the observer's (for example when the observer closes it at the first of two Returns). Agreement with
  the specified rows is read from the detection counts of `pred_obs` under `over_limit` per stratum (the numbers of
  predicted and of observed rows, and the pairs, misses and false positives between them, section 6.5); no other
  statistic is computed for it. A pair of a short row can fail for timing alone (section 6.9).

### 6.7 Question 3: per-text agreement

Under `under_limit` (primary) and `all_trials` (section 6.4), for each of the four measures of section 4.6 and each
speed: the Bland-Altman bias and limits of agreement of the per-text difference (observation minus ground truth), each
with a 95% interval from resampling blocks (the texts of a block form one cluster), and the intraclass correlation for
absolute agreement of a single measurement, with the two methods as the only raters of interest (a two-way mixed
model): ICC(A,1) in the notation of McGraw and Wong (1996), which has the value of ICC(2,1) of Shrout and Fleiss
(1979). It is computed by `kit/analysis/stats.py` from the mean squares of a two-way analysis of variance with the two
methods as columns, with a 95% interval from resampling blocks. An interval is withheld, with the reason, when its
statistic is undefined in the sample or in any resample. The per-speed values are primary; values pooled over speeds
are reported for comparison, because a difference in level between speeds inflates a pooled ICC. Under `under_limit` a
text keeps only its trials in the scope, and in this design their number r differs between speeds: computed from the
measurement plans and the prediction (section 3.1), the mean r is 4.60, 6.49 and 7.17 (slow, middle, fast) with live
conversion on and 4.56, 6.43 and 7.21 with it off; at the slow speed 30 of 126 and 27 of 117 texts have an r of three
or less, 4 and 0 keep all eight trials, and no text has none. A value computed over fewer trials varies more, so the
limits of agreement and the ICC of the primary scope hold for this design's distribution of r and are not compared
between speeds, nor extrapolated to texts of another length (the per-trial differences below are the material for
that). The bias is the mean of the per-text differences with every text weighted equally, whatever its r. The smaller r
is, the more texts have a measure that cannot be computed on one side (for example no session, so no mean conversion
time) and are left out of that measure (section 6.11). A text is not used if any of its trials was not run or was
excluded; the numbers of such texts are reported by reason, block and speed. The number of a text's trials in the scope
is reported per speed, and the texts with none are counted (section 6.11). The bias of each measure is also reported
for three mixtures of speeds (slow, middle and fast in proportions 0.6/0.3/0.1, equal, and 0.1/0.3/0.6), with intervals
that resample the blocks of each speed separately, to show whether the aggregate's bias moves with the mix; the
interval of a single speed's bias is the one reported with that speed's agreement.

The plan orders the trials of a block by the seed and does not balance the scenarios within a text, so a text mixes
the scenarios about equally only in expectation, and every per-text value and ICC holds for this design's mix of
scenarios only. So that a reader can weight the scenarios differently, three per-trial differences (observation
minus ground truth) are also reported per scenario and speed, with the mean on each side and a block-bootstrap
interval: the number of conversions, the summed conversion time and the number of cancelled conversions; the trials
outside the scope are left out. These differences include sessions carried over from one trial into another, so
they are reported over every trial of the scope and over the trials of the scope that are in neither the
carried-out nor the received-carry layer of section 6.4, for which reweighting to another mix of scenarios is
meant; the trials that send a closing key in their reading stay in that view, because their disagreement is
their own. In the measurement plans the view without carried-over sessions holds no trial of S6, in either cell at
any speed, and S13 has no trial in the primary scope (section 3.3): these scenarios cannot be reweighted, their
strata are reported as null with the reason (section 6.11), and the view contains no disagreement that a session
carried from one trial into another causes. As a diagnostic, the differences are also reported over the trials of
the layer none (section 6.4).

### 6.8 Question 4: text recoverability

Run on the observation logs of each cell separately, with the plan, procedure file and settings that built them
(the attack refuses a mismatch). Block markers are recorded by the observer and are declared to the attack
(`--markers-in-obs`); input-mode switches are not recorded on macOS and are not declared. The ground truth is the
reading that was typed, not the original sentence. The attacks read the category and time of every key row; they
do not use the session rows, so the session rows are outside the scope of the recommendation below.

Both attacks assign the observed key presses to the trials in plan order, block by block. A block is placed where
its block-start marker (four arrow presses) and all its trials match the observed presses. A block whose observed
presses do not agree with the plan (a press missing, added or changed) is not scored: its trials are counted as
unscored, and the assignment continues with the next block that matches. A marker is looked for at the right end of
a run of arrow presses, so arrow presses just before it (for example those that end the last trial of a block whose
closing Return is missing) do not hide it; a run of four arrow presses inside a trial (for example a duplicated row
in a trial that sends three) is not taken for a marker unless the next block matches there. A difference therefore
loses only the block that holds it, and a press added between two blocks loses none; this is checked by the
selftest of the code on the cases it constructs, not proved in general. Presses that no scored block covers
(including those of unscored blocks) are counted. Accuracies and error rates are over the scored trials, and the
numbers of unscored blocks and trials are reported per cell.

**Closed set.** For each trial the attacker is given K candidate phrases, the true one and K - 1 decoys drawn from
the phrases the trial's scenario could have produced (excluding phrases with the same typed reading), and knows
the scenario, the system, the speed level, the generator, the procedures and the settings. For each candidate it
generates the expected log with the same generator and chooses the candidate whose expected log is closest to the
observed one. K is 10, 100 and 1,000; the seed is the one in `experiment/SEED`.

    python3 kit/e2/closed_set.py --obs <obs> --plan <plan> --corpus kit/corpus/data/phrases.jsonl \
      --recipes kit/gen/<procedure file of the cell> --config experiment/plan_config.macos.json \
      --K 10,100,1000 --seed 20260929 --bootstrap 2000 --markers-in-obs --out results/e2_closed_set_<cell>.json

The attack is repeated for six feature sets that remove information from the log: exact times and run lengths (`all`),
no times (`no_time`), no lengths of letter runs (`no_runs`), times in 1-second bins (`bin1s`), 1-second bins without
run lengths (`bin1s_count`), and neither (`counts_only`). An attacker given a detailed log can always discard
information, so the accuracy reported for a feature set is the maximum over that set and every coarser set derivable
from it. Reported: the accuracy with its Wilson interval and, descriptively, a 95% percentile interval that resamples
blocks; the chance level 1/K; the mean rank of the true phrase; and, descriptively and without an interval, the
accuracy of the feature set alone per scenario and per speed. A trial whose phrase is a generated kana string (S12) is
not counted, which leaves 2,925 trials with live conversion on and 2,700 with it off when every block is scored.

The synthetic key intervals are drawn independently of the keys and the pauses follow the procedures (section 3.3),
so the logs carry none of the timing structure of human typing: neither the timing of particular key pairs nor the
pauses between words and phrases (section 7). The attack's accuracy on the feature sets that keep any time (`all`,
`no_runs`, `bin1s`, `bin1s_count`) therefore cannot be carried over to human logs: for these four sets the accuracy
is reported as an estimate only, and they are excluded from the recommendation. A 1-second bin removes the timing of
key pairs, of the order of tens of milliseconds, but keeps pauses of a second or more, which in human logs fall at
the boundaries of words and phrases. The recommendation considers only the two sets that keep no time (`no_time`,
`counts_only`).

The accuracy of a feature set is **near chance** at K = 10 or K = 100 when the upper end of its 95% Wilson interval is
at most 2/K. The rule's behaviour at the planned numbers of trials is fixed before the measurement: the probability
that a set is judged near chance, when its true accuracy is c/K, is

| n (trials) | K | c = 1 | 1.5 | 2 | 3 |
|---|---|---|---|---|---|
| 2,925 (on) | 10 | 1.000 | 1.000 | 0.024 | 0.000 |
| 2,925 | 100 | 0.994 | 0.487 | 0.020 | 0.000 |
| 2,925 | 1,000 | 0.210 | 0.067 | 0.020 | 0.001 |
| 2,700 (off) | 10 | 1.000 | 1.000 | 0.025 | 0.000 |
| 2,700 | 100 | 0.989 | 0.447 | 0.019 | 0.000 |
| 2,700 | 1,000 | 0.067 | 0.017 | 0.004 | 0.000 |

(binomial probability that the Wilson upper end is at most 2/K, for the accuracy of one feature set measured over
the planned numbers of trials; for a set whose reported accuracy is the maximum over coarser sets as well, the
probability is at most this; if blocks are unscored, the rule is applied to the scored trials). At K = 1,000 an
attack at the chance level would be judged near chance only rarely, so K = 1,000 is reported descriptively and is not
part of the rule. A feature set is near chance for the recommendation when it is near chance at both K = 10 and K = 100
in both cells. The recommended granularity is the most detailed of the two sets considered that is near chance in
this sense (`no_time`, of which `counts_only` is a coarsening); if neither is, no granularity is recommended, and
this is reported. The recommendation concerns the key rows of a log published without its session rows: the session
rows carry their times and durations in milliseconds, were not given to the attacks, and could undo the coarsening of
the key rows, so publishing them is not evaluated. The rule judges the identification of one phrase among K
candidates in one trial; combining several trials of one writer, or matching a log against known final texts, is
not evaluated, and the recommendation makes no claim about the safety of a whole log (section 7). The Wilson
interval treats trials as independent. With dependence within a block, the true uncertainty of an accuracy is
larger than its Wilson interval shows, so a rule that reads the Wilson upper end is met more easily than it should
be; the dependence is expected to be small, because the key rows of a trial follow from its own phrase and
procedure, and the phrase, intervals and decoys of each trial are drawn independently, but it is not ruled out, and
the block-resampled interval is reported next to the Wilson interval for this reason (section 7).

**Open set.** The attacker sees only the categories of the key presses and, with a character language model,
searches for the kana string that the romanisation would spell with exactly the observed letter keys and the
observed punctuation, maximising its probability. Two knowledge levels are reported. K0 knows nothing of the genre
beyond the frequency of single characters: its model is a character unigram model estimated from the training
phrases of the same corpus. K1 knows the genre but not the individual work: a character trigram model with
Witten-Bell smoothing (Witten & Bell, 1991) trained on the corpus with the evaluation phrases' whole works held out.
Whole works, in an order drawn with the seed in `experiment/SEED`, are held out for evaluation until they hold at
least a fifth of the phrases (three of the twelve works of this corpus), and a training phrase with the same reading
as an evaluation phrase is removed. A stronger attacker, K1+, for which the corpus is split by phrase instead of by
work, is reported alongside K1. The baseline is the same model without the log, drawing a string from its prior,
either knowing only the number of segments or given the true length (both are reported).

    python3 kit/e2/open_set.py --obs <obs> --plan <plan> --corpus kit/corpus/data/phrases.jsonl \
      --recipes kit/gen/<procedure file of the cell> --split-seed 20260929 --seed 20260929 \
      --eval-fraction 0.2 --split both --n 3 --smoothing wb --beam 8 --length-info both \
      --bootstrap 2000 --markers-in-obs --out results/e2_open_set_<cell>.json

Reported: the character error rate of the attack and of the baseline, their paired difference (`gain`), and the
difference from a negative control in which each trial is decoded from another trial's observation
(`gain_vs_shuffled`), each with a block-clustered percentile bootstrap interval. Kana-input trials (S12) are
excluded. The open set does not enter the rule above; its results are reported next to the recommendation, as a
second view of what the letter-run lengths that `no_time` keeps reveal. Neither attack is claimed to be the strongest
possible; the results hold for these attacks.

### 6.9 Clock differences

The per-block differences between the observation, the reference input field and the executor are measured at
the block-start markers with `kit/pilot/clock_sync.py` for every run and reported (median, range, drift per hour
and per-block values). They are not used to correct the matching: the duration error is a difference of durations
and does not depend on them. A pair needs an intersection over union of at least 0.5; two intervals of the same
length still reach it when one is shifted by up to a third of that length, and intervals of different lengths
tolerate less. In the pilot the largest difference between the observation and the reference was 22 ms; the
collection delay of a row (section 4.4) shifts an observation interval by up to about 100 ms, so a session
shorter than about 370 ms could fail to pair for these reasons alone, and one whose two intervals differ in length
could fail when longer. In `pred_obs` the predicted rows carry the executor's dispatch times, so the difference
between the executor's clock and the observation's (measured at the same markers) adds to the collection delay:
a short predicted row, such as the fragment of one key interval after a cut-off, can fail to pair for timing alone.
Such a disagreement is counted in `pred_obs` like any other and is not attributed to a cause (section 7).

### 6.10 Question 5: runtime cost

Measured with `kit/e3` under two conditions, one night each, for eight hours at a 30-second interval: `idle` (the
observer runs and nothing operates the machine) and `working` (the observer runs while the runner plays the plan of
the cell with live conversion on; the runner plays the whole plan, about 11.9 hours, and the samples are those of
the first eight hours). Reported for each condition: the mean, median, 95th percentile and maximum of the
observer's CPU (one core is 100%), its final resident memory and the least-squares slope of its resident memory in
MB per hour. A condition with fewer than 480 usable samples or a window shorter than four hours is insufficient
(section 3.4). The two conditions are always reported together and never reduced to one figure. Battery use is not
measured. The observation log of `idle` is not deposited (section 3.4); the samples of both conditions are.

### 6.11 Missing data

A stratum with no pair, a measure that cannot be computed (no session, no trial of a text in the scope, zero
variance, fewer than two blocks) and an interval that cannot be formed are reported as null with the reason, never
as zero. Trials of a plan that were not
run are reported by stratum.

### 6.12 Exploratory analyses

Any analysis not described here (for example, the dependence of the duration error on think time, or comparisons
between the two cells) is reported as exploratory.

## 7. Threats to validity and limitations

- **The reference is a reference input field, not the input method's internal state.** The ground truth is assembled
  from the calls that one minimal client of the macOS text system received. The reference is placed at this software
  layer because an application sees a conversion only through these calls; an external timing device would time the
  path of the keys, not the conversion. Another application may receive other calls from the same input method; the
  agreement is estimated for this client (section 1.3).
- **The ground truth depends on the rule of section 4.3.** With live conversion on, the rule folds the automatic
  commits made while a reading is typed into one session. A rule that did not fold them would define a conversion
  differently (several sessions per reading), not merely with another window; the window's width did not matter in
  the pilot (10 to 900 ms). The sessions affected are marked by `n_continued_commits`, and the agreement of
  abandonment is reported separately for them (section 6.6); the other measures are not split by it.
- **The input is synthetic.** Key events are generated in software by the executor and enter the system's event
  stream where it posts them; the path from a physical keyboard through its hardware and driver is not exercised,
  and no claim is made about it. Key intervals are drawn independently of the keys and pauses follow the procedures,
  so human timing structure (the timing of particular key pairs, pauses between words) is absent; this limits the
  text-recoverability results, whose recommendation keeps no time (section 6.8): the feature sets that keep times
  are reported as estimates only, and granularities between milliseconds and one-second bins are not evaluated. The
  input mode is switched only by the mode keys (the kana key at the start of each block, and the alphanumeric and
  kana keys of S14), so a switch made otherwise (from a menu or with a shortcut), which the observer follows by
  polling the input source, is not exercised. Error correction is limited to the scenarios.
- **The input method's learning is not reset.** Candidate order and the position of automatic commits may change
  over the runs as the input method learns, also through attempts that are repeated (section 3.4). The run order is
  fixed (section 3.2). Intervals resample blocks, which covers dependence within a block but not dependence that runs
  across blocks.
- **One input method, one machine, one system version.** Only the standard Japanese input method of the system is
  measured, in its two live-conversion settings, in the reference input field; other input methods and applications
  are outside this study (section 1.3). The results hold for the recorded versions.
- **Software clocks.** All times come from the clocks of the processes on one machine; no external reference clock
  is used. Clock differences between the logs are measured at the block-start markers and reported (section 6.9);
  the collection delay of a row is up to about 100 ms (section 4.4).
- **S9 is not evaluated** (choosing a candidate with the mouse).
- **The prediction is a model of the observer's specification.** It reproduces the rules of the observer's source
  (its selftest checks them against the source) and models the runner's kana press at the start of each block; it
  does not model modifier keys, which no plan of this study sends, the collection of rows (one finished session is
  held until it is collected, section 4.4) or the delay from dispatch to the observer. A difference between the
  prediction and the observation is reported as such (`pred_obs`), not attributed to a cause; short rows can differ
  for timing alone (section 6.9).
- **The cut-off scope removes trials from the primary estimates.** The primary scope sets aside the trials whose
  presses went into a session that is cut off; the other disagreements that the observer's rules predict stay in the
  primary estimates (section 6.4). How often a trial is long depends on the design (think time, speed, reading
  length, S6, S13), so the fraction of long trials is a property of this design, not of natural typing, and the
  trials that remain differ between speeds in reading length and think time (section 6.1). Values under
  `all_trials`, those of question 3 in particular, hold for this design's fraction of long trials only. Under
  `under_limit` a text keeps fewer trials at the slow speed, so the per-text limits of agreement and ICC hold for
  this design's numbers of trials per text and are not compared between speeds (section 6.7). In a researcher's own
  log the sessions that are cut off cannot be confirmed from the key rows alone (section 6.4).
- **The stopping rule counts presses, not keys.** The rule on presses of section 3.4 counts the key-downs of the
  reference that remain beyond the presses that reached it, so a press that does not reach the reference and a press
  from outside cancel out (a marker press against a press anywhere in the same segment, a press of a trial against a
  press in the same trial), and such a replacement is not detected. The rule on the loss of focus matches each key row
  of the observation to a press sent: a press whose row the observation misses can take one press of a person made from
  20 ms before it to 300 ms after it, which is then not detected, while a row that arrives more than 300 ms after its
  press, or a row that the observer duplicates, makes the run count as aborted within a loss, or among the rows counted
  after it, without a person; there, unlike elsewhere (section 6.3), the behaviour of the observer under test decides
  an abort. A person's key presses that reach neither the reference nor another application, such as a shortcut that
  the system consumes without moving the focus, and presses of keys that the observation does not observe, are caught
  by neither rule. A person who moves the focus away and back without pressing a key is not caught either, and puts no
  key press into the logs. That the rules catch a person's key presses was checked only on cases constructed in the
  selftests of the code, not by typing on the measuring machine; presses into an input field that takes keys without
  taking the key window from the reference input field (such as a search field of the system) may not be caught.
- **The reference input field can lose the focus by itself.** In the pilot it lost the focus once in about 87
  minutes, for 31 ms (section 3.1; a single observation, so the rate is uncertain). The presses sent during such a
  loss reach another application and not the reference, so their trials appear as disagreements between the plan
  and the ground truth; they are reported with the delivery figures and not excluded (section 6.2).
- **Intervals that treat trials as independent.** The Wilson intervals of pooled proportions ignore the clustering of
  trials in blocks and are read as lower bounds of the uncertainty (section 6.1). The near-chance rule of question 4
  uses the Wilson interval; with dependence within a block the rule is met more easily than it should be, and the
  block-resampled interval is reported next to it (section 6.8). The margin of the rule (2/K) and the values of K are
  design choices, not derived from a criterion of privacy.
- **The text-recoverability study judges one phrase at a time** (section 6.8), so the recommended granularity is not
  a claim that a whole log is safe to publish.

## 8. Deviations and addenda

Any departure from this registration after the freeze is recorded in `experiment/deviations.md` with its date,
reason and effect before the affected analysis is run, and is reported in the paper. The results of the analysis
run from the freezing tag are always reported as the registered results; if a defect of the frozen code is found
after the analysis, a corrected analysis is reported next to them as a deviation, not in their place.

The Windows addendum covers only the Windows cells and question 5 on Windows (section 1.3). It may change only the
Windows parts of existing files: the Windows blocks of `experiment/gt_rules.json`, the Windows sections (`os.windows`)
of the two procedure files, and the Windows profile of the generator (`_PROFILES["windows"]` in
`kit/gen/generate_plan.py`), where the numbers of Returns and Escapes of the Windows procedures are fixed. It may
add its own section to this file and new files that only the Windows cells use (for example a generator settings
file for Windows); it may not change any other part of an existing file. When it is frozen it replaces the two
freeze marks; that `experiment/config.json`, `experiment/SEED`, the macOS block of the rule file, the macOS parts of
the files used by the macOS cells and the macOS sections of this file are unchanged is shown by the difference
between the two freezing tags and stated in the addendum. The macOS plans are generated, and the macOS analysis is
run, from the macOS freezing tag, also after the addendum (the procedure files that the addendum may change are read
by the macOS plans too, whose SHA-256 is given in section 2.4). The addendum is registered before the first Windows
measurement.

## 9. Freeze

The freeze adds to this file two lines outside comments and code fences: one starting with `frozen_at:` followed by
the date, and one starting with `sha256:` followed by 64 lowercase hexadecimal digits. The latter is the value that

    python3 kit/analysis/pipeline.py --freeze-hash --config experiment/config.json --seed-file experiment/SEED

prints: the SHA-256 of the SHA-256 of `config.json`, of `SEED` and of `gt_rules.json`, in this order, each
followed by a line feed. The freezing commit is tagged. The frozen file is registered on OSF Registries with the
two marks, the 40-digit identifier of the freezing commit and the SHA-256 of the list of files exported from it,
and the registration is public. The export root is the top directory of the published materials (the directory
that holds `experiment/` and `kit/`); the list holds one line per exported file, with its path relative to that
root and its SHA-256 separated by a space, sorted by the bytes of the path, each line ending in a line feed, so that
the SHA-256 of the list can be computed again from the published materials. The URL and date of the registration
are recorded in `experiment/notes.md`. The registration date precedes the first measurement run.

The settings of the plan generator (`experiment/plan_config.macos.json`), the two procedure files, the corpus, the
key map of the prediction and the analysis code are not part of the hash; they are fixed by the freezing tag. Every
plan records the SHA-256 of its settings, procedure and corpus files, and the SHA-256 of the two measurement plans is
given in section 2.4. At the time of writing these files have the SHA-256:

| File | SHA-256 |
|---|---|
| `experiment/plan_config.macos.json` | `eb0ff955834d135a99d1dff3ae4cb1d58a8734c819835b3afbc5db08edf02209` |
| `kit/gen/recipes.json` | `158cac1bcc15effac2eafc994d788e585b61e3c43f410d558c87c2188866d16f` |
| `kit/gen/recipes.macos-live-off.json` | `36aad15abc118e8f57533db43a880c1615008af8f2fb4dd33b846f6d1abb9168` |
| `kit/corpus/data/phrases.jsonl` | `c8f890cb4d0848bf04e6d7d95efe4e4d3dfd02a23eb1c7f103344f14d13e4620` |
| `kit/emit/keymap_macos.json` | `be191e029fa9d706cf378f20e0d5dac2f67f90c0a20d3b130d8f81ed6c918802` |

If any of them changes before the freeze, the table and the plan hashes of section 2.4 are updated in the freezing
commit.

## 10. Windows addendum

### 10.1 Status of the addendum

This section is the Windows addendum of section 8. It is written after the Windows calibration pilot
(`experiment/pilot-windows.md`; the runs of a calibration pilot are not measurements) and frozen before any Windows
measurement. The date in the Status at the top of this file is that of the macOS freeze; under section 8 that text
is not changed. When the addendum is frozen it replaces the two freeze marks there: `frozen_at:` then gives the date
of the addendum's freeze, and `sha256:` keeps its value, because the addendum does not revise the Windows blocks of
`experiment/gt_rules.json`, and `experiment/config.json` and `experiment/SEED` are unchanged. The freeze marks
therefore do not tell the two freezes apart, and the analysis code accepts the Windows plan in every version that
holds deviation D2. That the Windows measurement follows the addendum is shown by the date of its registration and
by commits pushed to the repository right after each run (section 10.8), whose times are kept by the hosting service,
not by the measuring machine. The freezing commit of the addendum is tagged `ime-observation-prereg-windows`. The
addendum is registered on OSF Registries as the macOS freeze was (section 9): the frozen file with its two marks, the
40-digit identifier of the freezing commit and the SHA-256 of the list of files exported from it; the URL and date of
the registration are recorded in `experiment/notes.md`, and the registration date precedes the first Windows
measurement.

The difference between that tag and `ime-observation-prereg-macos` holds:

- the Windows sections (`os.windows`) of the two procedure files and the Windows profile of the generator (S8 ends
  with a Return; section 10.5);
- new files that only the Windows cell uses: `experiment/plan_config.windows.json`, `experiment/pilot-windows.md`,
  `experiment/data.windows.md`, `kit/analysis/scope_windows.py`, `kit/run/cell.windows.json`,
  `kit/run/focus_windows.py`, `kit/run/away_windows.py`, `kit/run/start_windows.py` and `kit/run/README.windows.md`;
- `experiment/deviations.md`, the record of deviations that section 8 names, created for the deviations below;
- this section and the freeze marks;
- a record in `experiment/notes.md` of the registration of the macOS freeze, which is unrelated to the addendum;
- the changes of four deviations, recorded in `experiment/deviations.md` before any Windows measurement, to parts of
  existing files that section 8 does not open to the addendum: D1 (scenario S4 presses three Escapes on Windows; the
  generator's check of S4 reads the Escapes from each profile), D2 (the plan check of `kit/analysis/pipeline.py`
  accepts the registered plan of the Windows cell with its reference input field), D3 (the scopes of the Windows cell
  take the long trials from the presses sent) and D4 (a key row of the observation that no sent press explains counts
  a Windows run as aborted). The changes to `kit/analysis/pipeline.py` include its descriptions, a Windows form of its
  synthetic runs and a case of its self-test.

On the macOS cells the four deviations change nothing that is reported: the macOS plans generated with the changed
generator differ from those of the macOS tag only in the recorded SHA-256 of the procedure file, and on synthetic
macOS runs the result files of the changed analysis code are byte-identical to those of the macOS tag; the commands
and hashes, taken on the freezing commit of the addendum, are given in `experiment/deviations.md`. The macOS plans are generated, and the macOS analysis is run, from
the macOS tag (section 8).

### 10.2 Scope

The addendum adds one cell: the Microsoft input method (Japanese, Romaji input) on Windows 11, measured with the TSF
reference input field. It covers questions 1 to 3 for this cell, question 4 on its observation log and question 5 on
Windows. The macOS sections apply to the Windows cell where this section does not replace them; section 10.3 replaces
the description of the observer in section 1.1, and sections 10.7 to 10.10 state what changes in the analysis, the
stopping rule and question 5.

### 10.3 The observer on Windows

On Windows the observer does not infer sessions from key presses. A session is a period during which a window of the
input method is shown, observed system-wide (the window's application is not identified). The observer has two
paths, chosen from moment to moment:

- **Window events.** The operating system reports the display, change and dismissal of an input-method window
  (`EVENT_OBJECT_IME_SHOW`, `EVENT_OBJECT_IME_CHANGE`, `EVENT_OBJECT_IME_HIDE`), received by an out-of-context event
  hook. A display, or a change while no session is open, opens a session, and a dismissal closes it; `dwell_ms` is the
  time between them on the wall clock. A display while a session is open starts the session again without a row (for
  example when the prediction window and the candidate window are shown one after the other). This path has no cut-off
  at 20 seconds: a window that is still shown 25 seconds after the session opened is ended by a safety valve of the
  monitoring loop, which writes a row with a `dwell_ms` of about 25 seconds, and the next change of the window opens a
  new session.
- **Window cloaking.** On systems that show and hide the candidate window by cloaking it in the desktop compositor,
  the events above do not arrive. Until the first of them arrives in a run, and whenever none has arrived for 60
  seconds, the observer follows the cloak and uncloak notifications of windows of the class `IME` instead: a session
  opens when such a window becomes visible and closes when none is visible, a gap of less than 150 ms being folded into
  the session (its `dwell_ms` ends when the window became invisible, and its row is written after the gap has passed),
  and a session is cut off, without a row, 20 seconds after it opened, whether or not the window is still shown.

Both paths can therefore act within one run, and a row does not record which path wrote it. On both paths `nav` counts
the arrow-key presses and `abandon` records an Escape press made while a session is open, in any application, so both
are upper bounds for the session's window. A session's row is written when the monitoring loop, which runs every 100
ms, collects it, and the row's time is the time of that collection; a session that closes and is followed by another
before the loop collects it is lost. Which path drives the Microsoft input method on the measuring machine was not
determined before the freeze, because that would have meant reading the observation's session rows; rows of 20 to 25
seconds can come only from the first path, and rows of about 25 seconds from its safety valve (section 10.7).

For each key press and release, the observer writes a key row for letters, digits, the numeric keypad, punctuation
keys, Backspace, Return, Space, Tab, Delete, the modifier keys (Shift, Control, Alt and the Windows keys, which macOS
does not record), Page Up, Page Down, Home, End, the arrow keys, Escape and F1 to F12; it writes no row for the
input-mode keys (`VK_IME_ON`, `VK_IME_OFF`) or for F13 and above. Key presses sent by a program are recorded like
physical ones.

A session of the observer is therefore a different construct from a session of the reference input field (a
composition, section 10.7): the window may open after the composition (for example when the prediction window
appears) and close before or after it, and on the second path the observed interval ends at least 150 ms before the
row's time. These differences are part of what the agreement measures; they enter the dwell error and the matching,
and they are not corrected (the settings of section 5 are unchanged).

### 10.4 Instruments and machine

- **Reference input field** (`kit/refinput-windows`, program `refinput_tsf`). A window with a rich-edit control that
  is a client of the Text Services Framework. After each edit it reads the compositions of its document and records,
  with wall-clock and monotonic times, the start of a composition (`tsf_start`), its length after each edit
  (`tsf_update`), its end with the length of the text committed (`tsf_end`; zero when nothing was committed, and no
  length when the field closes a composition it infers from a change of the composition's range or cannot read the
  committed length), the begin and end of the input method's user-interface elements with their kind (`ui_begin`,
  `ui_end`), each change of focus, and a key-down (key code only) for each key that the input method does not consume.
  No text is recorded. The other field of the kit, `refinput_imm`, was used only in the pilot; the reasons for the
  choice are given in `experiment/pilot-windows.md`.
- **Runner.** `kit/run/run_cell.py` with a cell file made from `kit/run/cell.windows.json`, started through
  `kit/run/start_windows.py`, which brings the reference input field to the front while the runner waits for its
  children (the foreground lock of Windows can otherwise refuse the emitter's first activation, which stops the run
  before any key is sent). Before every block the focus command (`kit/run/focus_windows.py`) brings the field to the
  front, confirms that it is in front, and presses `VK_IME_ON`; the window that S15 brings to the front is a window of
  its own (`kit/run/away_windows.py`). How a Windows cell is run is described in `kit/run/README.windows.md`.
- **Executor.** `kit/emit/backend_windows.py` sends the presses through `SendInput`, with the checks of the front
  window described in that file.
- **Measuring machine.** The machine of the Windows pilot: an x86-64 tablet computer (Intel Core i5-1135G7, 8 GB
  memory), Windows 11 Home 25H2 (build 26200 in the pilot), JIS keyboard, Microsoft IME `IMJPTIP.DLL` 10.0.26100.8875
  in the pilot, with its default settings (Romaji input and predictive candidates shown; neither is overridden in the
  input method's settings). The versions at the time of the run are recorded in `experiment/env.md` and in the run
  manifest. The run is made on mains power, with automatic updates, network time synchronisation, sleep, the screen
  lock and the screen saver turned off, from a terminal that is not elevated. The input method's learning is not
  reset: it offers no command that clears its input history, and the plan requests no reset.

### 10.5 Procedures

The Windows sections of `kit/gen/recipes.json` give the procedures. They differ from the table of section 2.3 as
follows: a single Return commits wherever the table shows two (S1, S2, S7, S12, S13 and S15); S4 types the reading,
presses Space, pauses and presses Escape three times (deviation D1); S5 presses Escape once; S8 presses Return after
the digit; S11, which needs live conversion, is not run; and S14 switches the input mode with `VK_IME_OFF` and
`VK_IME_ON` (virtual keys that a program can send; a person switches the mode with the half-width/full-width key).
The cell therefore has 13 scenarios. Before each block the input method is switched on with `VK_IME_ON` rather than
the kana key. The marker, the gaps between trials and the other settings of section 3.2 are those of the macOS cells.

### 10.6 Plan

The plan is generated by

    python kit/gen/generate_plan.py --config experiment/plan_config.windows.json \
      --recipes kit/gen/recipes.json --corpus kit/corpus/data/phrases.jsonl \
      --os windows --seed 20260929 --out <plan>

from the published files as they are, with LF line ends (a checkout that converts line ends changes the SHA-256 of
the files that the plan records). At the Windows tag these files have the SHA-256:

| File | SHA-256 |
|---|---|
| `experiment/plan_config.windows.json` | `400fe5314d0f660f2e776a7f86deb471ae1235a3bea4c1be457b26ebe0a9181f` |
| `kit/gen/recipes.json` | `430ec52c67f0592bc1c9d038a972876a2608ed7c39eb2aabcf68efc4ff908411` |
| `kit/gen/recipes.macos-live-off.json` | `8560f7bd59d04b0a2629af2beabdfd6fabc0aef92d35a2a9ad73a89bfd74d19a` |
| `kit/corpus/data/phrases.jsonl` | `c8f890cb4d0848bf04e6d7d95efe4e4d3dfd02a23eb1c7f103344f14d13e4620` |
| `kit/emit/keymap_windows.json` | `171bfb30ebf4989b4a083a07161b9838b8366bf74493853de7a41340bf69790e` |

The table of section 9 gives these files at the macOS tag; if any of the files above changes before the addendum is
frozen, this table and the plan's SHA-256 below are updated in the freezing commit. The settings equal those of
`experiment/plan_config.macos.json` (n = 75 per scenario and speed, key-interval medians of 480, 240 and 120 ms,
think time from 0.2 to 20 s, 25 trials per block). The plan holds 2,925 trials in 117 blocks; generated before the
freeze on the measuring machine (Windows 11) with Python 3.11.9 and 3.13.15 it was byte-identical, SHA-256
`b7bdf8fc120036f18ceff53d2bf8def43ef11a818a119bc24b0c7f4fbc4836b0`. This is the plan that the analysis accepts for the
Windows cell (deviation D2, reason 1 of section 3.4). The generator estimates 13.75 seconds per trial (11.2 hours).
In the pilot the measured time per trial was 6% (117 trials, frozen procedures: 14.1 s against an estimate of
13.3 s) to 17% (39 trials, corrected procedures: 15.2 s against 13.0 s, with relatively more time spent between blocks)
above the generator's estimate for the pilot's plans, so the run is expected to take 12 to 13 hours.

### 10.7 Ground truth, scopes and analysis

**Ground truth.** Sessions are assembled from the reference log by `kit/analysis/gt_sessions.py` with the `win_tsf`
rules of `experiment/gt_rules.json`, which the pilot left unchanged: a session opens at `tsf_start` and closes at
`tsf_end`, as committed when the length of the committed text is positive and as cancelled otherwise (also when no
length is recorded); a session shows a candidate window when a `ui_begin` of kind `candidate` falls within it. With the
corrected procedures every trial of the two check runs with this field produced the session its plan intends (39 of 39
each).

**Definitions.** As in section 5, the definition of the cell's system (`windows_def`, the sessions that showed a
candidate window) is the definition of the analysis. The Microsoft input method opens its prediction window, which the
field reports as a candidate element, while the reading is typed, so `windows_def` holds nearly all sessions (all but
one, an S10 session, in each of the two check runs of the pilot, which used the same plan), and "with a candidate window" does not mean on Windows that the reading was
converted. Whether the two definitions hold the same sessions is reported per cell, as on macOS.

**Scopes.** The prediction of section 4.5 models the macOS observer; no prediction applies to the Windows observer
(section 10.3), so the comparisons with the prediction (`gt_pred`, `pred_obs`) and the prediction's layers are not
computed for this cell, and the view of question 3 (section 6.7) restricted to the trials in neither carry layer, which uses
those layers, is null for it. For the Windows cell a trial is long when the presses the executor sent in it span at least 20,000
ms on the wall clock, from the first to the last (deviation D3); like the macOS scopes, this is decided from the oracle
and the plan alone. `under_limit` and `over_limit` are formed from these trials, and the primary estimates of the cell
are those of `gt_obs` under `under_limit`, per stratum, as for the macOS cells. The observer's own cut-off does not
follow this rule (section 10.3: on one path a session is cut off without a row 20 seconds after the window opened, on
the other a row of about 25 seconds is written), so the trials near the boundary may disagree for that reason; they
are reported (below). In S15 the presses span the switch to the other window and the Return after it, while the
composition ends at the loss of focus, so the two definitions can differ there more than elsewhere; the trials they
classify differently are read per stratum. The specified
behaviour that section 6.4 states for `over_limit` (no row, and a fresh session at the next letter or digit) is the
macOS observer's; for the Windows cell the results under `over_limit` and the count of observed rows of 20 seconds or
more (section 6.6) are reported descriptively and read with section 10.3. The path that wrote a row is not
recorded and is not inferred: the rows of 20 to 25 seconds and of 25 seconds or more are counted (below) and reported
descriptively, as indications of the first path and of its safety valve.

As a sensitivity analysis, detection under `under_limit` is also computed with the long trials taken from the ground
truth (a trial is long when a session of the reference assigned to it lasts at least 20,000 ms; result key
`under_limit_gt_duration`). Per stratum, the numbers of long trials under both definitions, of the trials they classify
differently, and of the trials whose reference session lasts from 19 to 21 seconds are reported (`long_trials_from`).

From the plan's own times (`python kit/analysis/scope_windows.py --from-plan <plan>`), 617 of the 2,925 trials are
long (401 slow, 123 mid, 93 fast). In the pilot's check runs with the corrected procedures, the long trials of the plan
were exactly those of the ground truth: 10 trials of one plan, run twice. The trials that remain under the cut-off
before the exclusions of section 6.2 are, per scenario and speed (slow / mid / fast):

| Scenario | slow / mid / fast |
|---|---|
| S1 | 42 / 69 / 73 |
| S2 | 42 / 68 / 72 |
| S3 | 59 / 75 / 75 |
| S4 | 32 / 66 / 74 |
| S5 | 65 / 75 / 75 |
| S6 | 30 / 73 / 75 |
| S7 | 39 / 64 / 73 |
| S8 | 32 / 66 / 66 |
| S10 | 66 / 71 / 74 |
| S12 | 75 / 75 / 75 |
| S13 | 0 / 0 / 0 |
| S14 | 46 / 75 / 75 |
| S15 | 46 / 75 / 75 |

S13 has no trial under the cut-off, as on macOS; eight other strata have fewer than 50 trials, the smallest 30 (S6,
slow). A speed level holds 574 (slow), 852 (mid) and 882 (fast) of them, in 39 blocks each. The precision that section
3.3 gives for n = 31 is close to that of the smallest strata. Over the trials under the cut-off, the sum of m_j
squared over the sum of m_j (section 3.3) ranges from 1.56 to 3.19 per stratum (largest S15, mid), within the range
that section 3.3 gives for the macOS plans.

**Diagnostics.** Reported for the Windows cell: the ends of a reference composition that carry no committed
length, per scenario (an end belongs to the trial whose interval, extended by 300 ms as in `kit/pilot/reach.py`,
holds it); for S4, the Escape press after which the reference session ended (the number of Escapes the trial sent up
to the end, when the last of them came at most 2 seconds before it); and the observed session rows of 20 to 25
seconds and of 25 seconds or more. If the observation log of the Windows cell holds no session row at all, the
analysis code refuses the run (step 0 of `kit/analysis/pipeline.py`), and the cell is reported as not measured.

**Question 4.** The two attacks are run on the observation log of the Windows cell with the commands of section 6.8,
with `--recipes kit/gen/recipes.json` (and, for the closed set, `--config experiment/plan_config.windows.json`), and
without a switch for the input-mode keys, which leave no row on Windows. Without the S12 trials 2,700 trials remain, so the probabilities of the table in
section 6.8 for 2,700 apply. The recommendation of section 6.8 is decided from the two macOS cells, as registered; the
same judgement (near chance at both K = 10 and K = 100) is reported for the Windows cell separately, and when it does
not hold there, this is reported as a qualification of the recommendation for logs recorded on Windows; it does not
change the recommendation. The plans send no modifier key and the attacks read key presses only, so the judgement on
Windows concerns the key rows of presses other than modifier keys; the modifier rows and the release rows that the
Windows observer writes (section 10.3) are not evaluated, and publishing them is outside the recommendation, as the
session rows are (section 6.8).

**Running the analysis.** The analysis of the Windows cell is run from the Windows tag, on the runs listed in
`experiment/data.windows.md`, by

    python kit/analysis/pipeline.py --runs-from experiment/data.windows.md --config experiment/config.json \
      --seed 20260929 --seed-file experiment/SEED --prereg experiment/preregistration.md \
      --out experiment/results/windows

The macOS cells are not analysed from the Windows tag; results of the macOS cells computed from it are not reported.

### 10.8 Stopping rule and data collection

Section 3.4 applies with its settings, with these additions; like section 3.4, they apply to the runs of the Windows
cell and of the `working` condition of question 5 on Windows, which plays the plan of the Windows cell with the same
cell file.

- In the pilot, `VK_IME_ON` before each block and the input-mode keys of S14 left no key-down in the log of the TSF
  field, so k = 0 and the version `excluding_mode_keys` of the delivery report hold.
- A run whose plan is not the plan of section 10.6, or whose reference log does not come from `refinput_tsf` (`src`
  `win_tsf`), counts as aborted (deviation D2).
- The TSF field records a key-down only for a key that the input method does not consume, so a person typing into the
  field while it has the focus would pass the rule on presses from outside and, since the focus is not lost, the rule
  on the loss of focus as well. A run therefore also counts as aborted when, in any segment of the observation log (as
  section 3.4 divides the reference log), the key rows that no sent press explains exceed the marker presses of the
  segment (deviation D4; in the pilot they never did). The rule cannot catch a press of a key that the observer does
  not record (section 10.3), a person's press that cancels a sent press that the observation lost (as section 7 states
  for the reference), or input with the pointer, touch or pen; it also aborts a run in which the observer writes a key
  row twice. Since such a row is an error of the observer under test, the figures of a run aborted by this rule (the
  segment and the number of rows) are reported with the results.
- Before the first run, the versions of the system and of the input method are read. If the input method's version
  differs from the pilot's (section 10.4), the check plan of the pilot (`verify` in `experiment/pilot-windows.md`: 39
  trials, one per scenario and speed) is run first with the measurement's cell file, and the Windows cell is measured
  only if every trial produces the session its plan intends (39 of 39); otherwise the cell is not measured and this is
  reported.
- Right after each attempt, its line in the table of run checks of `experiment/data.windows.md`, with the SHA-256 of
  its manifest, is committed and pushed to the repository, so that the time of each run is kept outside the measuring
  machine.

The checks are recorded in `experiment/data.windows.md`, where the plan's SHA-256 is checked by hand against section
10.6. The runs are made on separate nights in this order: the Windows cell, then the two conditions of question 5 on
Windows, `idle` and then `working`.

### 10.9 Question 5 on Windows

As section 6.10, with the Windows sampler of `kit/e3` and, for the `working` condition, the plan of the Windows cell
played by the runner (12 to 13 hours; the samples are those of the first eight hours). On Windows the CPU value of a
sample is the processor time of the observer over about one second at the moment of reading, not an average over the
interval, and the memory is the working set, so the values are compared within each system and not between systems. A
counter that cannot be read gives zero. In the `working` condition the observer processes the key presses of the plan,
so a condition in which every CPU sample is zero (`cpu_all_zero`) is taken as a counter that could not be read: it is
insufficient and is run again under section 3.4. In the `idle` condition a true zero is possible, so the condition is
reported with the flag. For the Windows cell this rule takes precedence over the description of the flag in
`kit/e3/README.md`.

### 10.10 Data that exist before the freeze of the addendum

The Windows calibration pilot is reported in `experiment/pilot-windows.md`, which also states the criteria under which
its raw logs are deposited. As on macOS, only the reference side and the key rows of the observation were analysed; the
session rows of the observation were sealed before any analysis and were not read; they are unsealed only to be
deposited and are not analysed, not even in an exploratory analysis. No agreement between the observation and the
ground truth was computed, and no text-recoverability or runtime analysis was run on pilot data. On Windows the key
rows are not the input from which the observer forms its sessions. Two checks of the code were made on the pilot's
logs before the freeze, as on macOS: the assignment of observed presses to trials that both attacks of question 4 use
scored every block of the seven runs of the pilot (27 blocks, 429 trials) and left no press unassigned; and
`kit/analysis/delivery.py` in its registered form, with the settings of the measurement, caught none of the four runs
with the TSF field, and none of the seven runs had a key row of the observation beyond the marker presses (deviation
D4). The three runs with the IMM field received the focus command's `VK_IME_ON` as a key-down in every segment but the
last, which the setting k = 0 counts as a press from outside, so they are not deposited (`experiment/pilot-windows.md`).
The plan of the Windows cell was generated (section 10.6), and its long trials were counted from it (section 10.7); no
observation of it exists.

### 10.11 Limitations specific to the Windows cell

- **Synthetic input, one input method, one kind of field, one machine.** As on macOS (section 7), the input is
  synthetic. The cell is measured with one version of the system and of the Microsoft input method, with its default
  settings (predictive candidates shown), on one machine. The observer sees a session only while the input method
  shows its window; a field decides whether the input method may show its user-interface elements, and the reference
  input field lets it show them, so in applications that draw the candidates themselves the observer may see nothing.
  This was not tested. With predictive candidates turned off, the window would open at the conversion rather than while
  the reading is typed. The reference depends on the kind of field as well: in the pilot the TSF field committed when
  the right arrow was pressed beyond the last segment (S7), where an IMM window kept the composition open, and the last
  call of a trial differed by more than 200 ms between the two fields in 8 of 117 trials. The agreement measured here
  therefore cannot be carried to an application whose field does not record a reference, and no procedure of this kit
  measures it there.
- **Two constructs.** The observer's session is a period of a visible input-method window, the reference's a
  composition (section 10.3); the agreement measures both the observer's errors and the difference of the constructs.
- **Commit at a loss of focus.** In S15 the input method commits when the other window comes to the front; the session
  ends there, and the Return pressed afterwards reaches the field as a plain key.
- **Long trials and the observer's paths.** On Windows the scope is decided from the span of the presses sent, which
  is not the rule of the observer's cut-off (section 10.7); the trials near the boundary and the sensitivity analysis
  are reported. The two paths of the observer can both act in a run, and a row does not record its path.
- **Timing of the observed interval.** On the cloaking path the observed interval ends at least 150 ms before the
  row's time (section 10.3), and on both paths the row's time is that of the collection, up to 100 ms after the
  session closed. The matching places the observed interval by the row's time (section 4.4) and is not adjusted, so
  short sessions can fail to pair for this reason; the differences are reported as part of the observer's agreement.
- **The reference is software on the same machine.** The times of the reference's compositions come from the TSF
  notifications that the field receives after each edit; they were not checked against an external clock. In the
  pilot the first call of a trial differed by a median of 5 ms between the TSF and the IMM field.
- **Selection by the stopping rule.** Deviation D4 aborts a run when the observer writes a key row that no sent press
  explains, which can also be an error of the observer; if every attempt is aborted, the cell is not measured, and the
  figures of the aborted runs are reported (section 10.8).
- **The reference sees few of the presses.** The TSF field receives only the keys that the input method does not
  consume, so the reference side of the delivery report counts few presses, and the stopping rule relies on the
  observation for presses from outside (section 10.8).
- **Key paths not tested.** The input mode is switched with virtual keys that a program sends, not with the key a
  person presses, and the response of the input method to the presses was not compared with hand-typed presses.
- **Clocks.** The drift between the clocks was measured over at most 25 minutes in the pilot (within 11 ms per hour in
  the two runs of six blocks); the differences per block of the measurement are reported as in section 6.9, and the
  largest difference that section 6.9 quotes for macOS does not apply to the Windows cell.
- **The learning of the input method is not reset**, as on macOS (section 7).

## References

Bland, J. M., & Altman, D. G. (1986). Statistical methods for assessing agreement between two methods of clinical
measurement. *The Lancet, 327*(8476), 307-310. https://doi.org/10.1016/S0140-6736(86)90837-8

Cameron, A. C., Gelbach, J. B., & Miller, D. L. (2008). Bootstrap-based improvements for inference with clustered
errors. *The Review of Economics and Statistics, 90*(3), 414-427. https://doi.org/10.1162/rest.90.3.414

Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement,
20*(1), 37-46. https://doi.org/10.1177/001316446002000104

Dhakal, V., Feit, A. M., Kristensson, P. O., & Oulasvirta, A. (2018). Observations on typing from 136 million
keystrokes. In *Proceedings of the 2018 CHI Conference on Human Factors in Computing Systems* (Paper 646).
https://doi.org/10.1145/3173574.3174220

Efron, B. (1979). Bootstrap methods: Another look at the jackknife. *The Annals of Statistics, 7*(1), 1-26.
https://doi.org/10.1214/aos/1176344552

McGraw, K. O., & Wong, S. P. (1996). Forming inferences about some intraclass correlation coefficients.
*Psychological Methods, 1*(1), 30-46. https://doi.org/10.1037/1082-989X.1.1.30

Shrout, P. E., & Fleiss, J. L. (1979). Intraclass correlations: Uses in assessing rater reliability. *Psychological
Bulletin, 86*(2), 420-428. https://doi.org/10.1037/0033-2909.86.2.420

Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the
American Statistical Association, 22*(158), 209-212. https://doi.org/10.1080/01621459.1927.10502953

Witten, I. H., & Bell, T. C. (1991). The zero-frequency problem: Estimating the probabilities of novel events in
adaptive text compression. *IEEE Transactions on Information Theory, 37*(4), 1085-1094.
https://doi.org/10.1109/18.87000
