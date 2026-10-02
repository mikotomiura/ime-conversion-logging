# kit/pilot — calibration pilot instrumentation

The calibration pilot verifies, **before any accuracy measurement**, that the instrumentation itself (ground truth,
input, and clocks) is correct, and fixes the operational definition of ground truth (composition start, commit,
cancel, and candidate window) against an on-device sequence of API calls. Neither Apple nor Microsoft documents the
order in which an input method calls a text input field, so the order is determined empirically. The four tools in
this directory produce that material.

| Tool | Output | Input |
|---|---|---|
| `call_types.py` | Per-trial **call-sequence types** (for example `set_marked+ → insert_text`), counted by scenario x source, and each trial's wall-clock duration (median, 95th percentile) | gt, oracle, plan |
| `clock_sync.py` | Per-block **synchronisation markers**: the distribution and drift of the time differences among oracle, gt, and the observation | oracle, (gt), (keys-only observation), marker specification |
| `reach.py` | Whether synthetic input **reaches the observation-side hook** (per trial, comparing presses sent against key rows received) | oracle, keys-only observation |
| `seal_obs.py` | Extracts only the `meta` and `key` rows from a standalone-logger output and **seals** the original file (`--unseal` is for the post-freeze analysis pipeline only) | standalone-logger output |

Python standard library only. Each file has a `--selftest` (marked with `# kit: selftest`), run by `bash kit/check.sh`.

## Boundary: the pilot does not read ImeShadow

The pilot is not an accuracy measurement. Adjusting the operational definition of ground truth, or the scenario set,
after looking at the observation side's ImeShadow values, before the protocol is frozen, would make the later
accuracy evaluation circular (measuring against a definition chosen after seeing the data). Therefore:

- The pilot's analysis **never reads, counts, or matches** ImeShadow rows. It uses only the observation side's `key`
  rows (category, press, and timestamp).
- A standalone-logger output is passed through `seal_obs.py` **before any analysis**. All subsequent analysis reads
  only the keys-out file.
- `call_types.py`, `clock_sync.py`, and `reach.py` contain **no code that reads ImeShadow rows**. If the observation
  input contains any row other than `meta` or `key`, they refuse it without inspecting its content (this stops the
  accident of passing an unsealed file downstream).
- `seal_obs.py` is written as an **allow list** (it names the kinds of row to keep, never the kinds to discard). The
  allow list applies not only to the kind of row but also to the fields inside a row: both the `meta` and `key` rows
  are rebuilt from the specification's fields, and a row with any other field is refused. The count, value, line
  number, or kind of a discarded row is never written to standard output, `seal.json`, or an error message.
- **Neither the original byte count nor the original line count is ever written to standard output or `seal.json`.**
  The byte count, together with the size of the sealed file, would let the number of ImeShadow rows (that is, the
  number of detected sessions) be estimated with high precision, revealing something close to the accuracy before
  the protocol is frozen. Integrity is verified with SHA-256 alone: opening the sealed file and matching its
  SHA-256 confirms the original file was restored intact, without ever needing the original byte count.
- The sealed file is **padded to a multiple of 1 MiB (1,048,576 bytes)** (padded with zero bytes). The original
  length is stored in a fixed-length field after the padding and is removed only when unsealing. Under this design
  the sealed file's size reveals the original size only to the nearest 1 MiB, which is the coarseness the
  byte-count-suppression requirement allows.
- No tool is built to **read or interpret the contents** of a sealed file. `seal_obs.py --unseal SEALED --out FILE`
  only restores the original byte sequence (removing the padding and checking the SHA-256 against `seal.json`); it
  never parses the JSON content. It is to be used only by the analysis pipeline after the protocol is frozen.

## Procedure (used in this order for the macOS and Windows calibration pilot)

1. Run S1 through S15 several times each **using the reference input field only**, recording gt (the call sequence)
   and oracle (the presses sent). On Windows, record both the IMM-compatible and the TSF paths. At the same time,
   run the standalone logger and let it write obs (it may include ImeShadow rows; no one reads them).
2. **Stop the standalone logger, then seal its output immediately** (a file the logger is still writing to is
   refused, because its size and mtime are judged unstable):
   ```bash
   python3 kit/pilot/seal_obs.py --obs obs.jsonl --keys-out obs.keys.jsonl --sealed-dir sealed/
   ```
   `obs.jsonl` moves into `sealed/` (and is removed from its original location) and is padded to a multiple of
   1 MiB. `sealed/seal.json` records the original SHA-256 and the sealing time (the byte count is not recorded).
   `obs.keys.jsonl` passes the `obs/1` validator (`kit/formats/validate_logs.py --kind obs`). If keys-out already
   exists, or a file of the same name or `seal.json` already exists at the seal destination, nothing is touched and
   the tool stops.
3. Count the **call-sequence types**:
   ```bash
   python3 kit/pilot/call_types.py --gt gt.jsonl --oracle oracle.jsonl --plan plan.jsonl --out call_types.json
   ```
   On Windows, run it twice, once per path, with the corresponding gt, and compare the `types` output of the two
   runs to see whether both paths return the same segments (a mismatch is itself worth reporting).
4. Check **reach to the hook** (observation side is keys-only):
   ```bash
   python3 kit/pilot/reach.py --oracle oracle.jsonl --obs-keys obs.keys.jsonl --out reach.json
   ```
5. Produce the **clock correspondence**:
   ```bash
   python3 kit/pilot/clock_sync.py --oracle oracle.jsonl --gt gt.jsonl --obs-keys obs.keys.jsonl \
       --marker marker.json --out clock_sync.json
   ```

The output JSON files may be kept with the experiment record. Raw logs (gt, oracle, plan, obs, and sealed files) are
not committed to git.

## Reading the output

### `call_types.py`

- `by_scenario.<S>.types` maps a type string to a count (most frequent first). A type joins call names with " → ",
  folding a name repeated two or more times in a row into a single entry with a trailing "+". `do_command` appends
  `cmd`, `notify` appends `imn`, and `ui_*` appends `ui_kind`, each in brackets.
- If a scenario's types **split** into more than one form, the operational definition of ground truth must be able
  to absorb that split (for example, the distinction between cancel and commit, or the presence of a candidate
  window). If they converge to a single form, that form is the basis for the definition.
- `duration_ms` is a trial's wall-clock duration (oracle's `trial_begin` to `trial_end`). **It excludes the
  inter-trial wait (`gap_before_ms`), markers, and resets** (the interval ends before them). It is used to review
  the total measurement time and the trial count `n`. `session_wall_ms` is separate: it is the **wall-clock
  duration of the whole session** (from the first `reset` / `block_begin` to the last `block_end`, including waits,
  markers, and resets).
- A large `unassigned_events` / `unassigned_events_by_ev` indicates either that the clocks are not aligned (try a
  different `--clock`, or widen `--pre-ms` / `--tail-ms`) or that calls are arriving between trials or at the start
  of the next block (`reset` / `block_begin` / `marker`); the interval also ends at the next oracle row, so such
  calls appear there. `planned_not_run` counts trials that were in the plan but never appeared in oracle at all
  (trials that ended with only a `trial_begin`, incomplete, are counted separately in `trials_incomplete`, not
  double-counted).
- `per_trial` gives, for each trial, the relative time of its first and last call (in ms from `trial_begin`; `null`
  if there was no call). It is used to check whether the intervals of the Windows IMM and TSF paths agree during
  the calibration pilot.

### `reach.py`

- `reach_rate` = sum(min(sent, received)) / sum(sent). `diff_distribution` is the per-trial distribution of
  (received − sent); values clustered at 0 mean everything arrived, while trials with negative values are missing
  that many presses (look them up with `per_trial`).
- Pseudo-keys (`mouse_click` / `focus_away` / `focus_back`) are not real keys and produce no `key` row, so they are
  not counted (change this with `--ignore-keys`). Sending a key the observation side does not observe makes it
  look like it "did not arrive"; that too is a genuine result of the reach measurement (which keys do not arrive is
  read from the trial's type).
- `outside_trials_rows` counts `key` rows that fall inside no trial's interval (the presses of a block-start marker
  land here). The interval also ends at the next oracle row (`reset` / `block_begin` / `marker` / the next
  `trial_begin`), so a marker press at the start of the next block is not folded into the last trial of the
  previous block.
- `per_trial` gives, per trial, the sequence of key names sent (`sent_keys`) and the sequence of categories
  received (`obs_categories`), both enumerated values. This is used to choose the marker key.

### `clock_sync.py`

Marker specification (JSON):

```json
{"oracle_keys": ["left", "right", "left", "right"], "gt_vks": [123, 124, 123, 124], "obs_categories": ["navigation", "navigation", "navigation", "navigation"], "max_gap_ms": 300}
```

- `max_gap_ms` must exceed the plan's `marker_gap_ms`: the measured press-to-press gap of a 150 ms marker gap was
  152 to 227 ms in the first macOS pilot, so a limit of 150 ms matched no marker at all.
- On macOS the logger observes F1 to F12 but not F13 to F19, so F13 to F19 cannot serve as markers there (the
  example above uses arrow keys, which are observed as `navigation`; macOS key codes 123 and 124). A marker is
  pressed at the start of a block, after the previous trial has committed, so the field is empty and the arrows
  reach it as commands (in the gt log, `do_command` rows outside every trial). When a composition is carried over
  from the previous trial (2 of 126 trials in the second macOS pilot with live conversion on), the arrows act on
  that composition instead.
- The observation records only a category, so an arrow-key marker is found there as a run of `navigation` rows,
  and one scenario presses the right arrow up to `nav_count_max` times in a row (the limit comes from the
  procedures). A marker whose observed keys are all arrows must therefore be longer than every run of arrow presses
  inside a trial of the plan; the generator refuses to build a plan that breaks this, counting only the keys the
  observation records. The four-key marker of the example is the one chosen for the measurement (it will be frozen
  with the protocol): it is longer than the three arrow presses in a row that `nav_count_max` = 3 allows. The macOS
  pilots used the three-key marker `left`, `right`, `left`, which serves only while no trial presses three arrows
  in a row (for example with `nav_count_max` at 2 or below).
  Otherwise those presses inside a trial count as marker occurrences, and if a block's real marker is lost, an
  occurrence inside its first trial can be matched to that block instead of the block appearing in `missing`. gt
  matches on virtual-key codes, and that scenario presses only the right arrow, so gt is not affected.
- A marker key must **appear in the observation's `key` rows** and must **not change IME state** (a key the
  observation side does not observe never produces a `key` row and therefore cannot serve as a marker). The first
  entries of the three lists correspond to the same press.
- The observation's `key` rows carry only a category (14 kinds), so a marker is found as a sequence of categories
  with intervals between them. Only a run that is unbroken, with every adjacent gap at or below `max_gap_ms`, is
  taken (the gap is measured on gt's monotonic clock and on the observation's wall clock).
- A block is matched to the nearest occurrence within `--window-ms` (default 3000) before or after the wall-clock
  time of the oracle's first marker key, assigned one to one. **A block that is not found appears in `missing`**
  (it is never silently dropped). An occurrence matched to no block appears in `unmatched_occurrences`. If the
  clock offset exceeds the window, nothing is found, so the window should be widened.
- The difference = each side's first key's time − oracle's first key's time (ms). **Latency (the time from sending
  to recording) and clock offset cannot be separated** (the difference reports their sum). A difference that stays
  constant across blocks is latency plus a fixed offset; one that moves with the block index (time) is drift.
- For each difference: median, minimum, maximum, `offset_at_start_ms` (the fitted value at the first block's
  time), `drift_ms_per_hour` (the least-squares slope against the oracle marker times), `residual_max_ms` (the
  largest deviation from the fitted line), and the per-block values. `drift_ms_per_hour` is `null` when fewer than
  two blocks are matched.
- `gt_minus_oracle_mono` is the difference on the monotonic clock. The observation side has only a wall clock
  (Unix ms), so its difference is wall-clock only. Which clock, wall or monotonic, agrees between gt and oracle is
  determined by comparing these two differences.

### `seal_obs.py`

- Standard output: the sealed file's name, its original SHA-256, the number of `key` rows kept, and the location of
  keys-out and `seal.json`. Neither the byte count nor the line count is printed.
- Both the `meta` and `key` rows of keys-out are **rebuilt** from the specification's fields (so no name from
  ImeShadow survives even in `meta`; a `key` row with any field beyond the specification's four is refused
  wholesale).
- An unreadable row (malformed JSON, a duplicate key, or a field that does not match the specification), a first
  row that is not `meta`, more than one `meta` row, or a file still being written to (size or mtime not yet stable)
  are all refused without touching anything (exit 1).
- `--unseal SEALED --out FILE` only restores the sealed file to the original byte sequence (removing the padding
  and checking the SHA-256 against `seal.json`). It is to be used only by the analysis pipeline after the protocol
  is frozen.

## Exit codes

| Tool | 0 | 1 | 2 |
|---|---|---|---|
| `call_types.py` | produced output | no completed trial at all | input unreadable, or usage error |
| `clock_sync.py` | produced output | oracle has no block with a complete marker, or the requested side matched no block at all | same as above |
| `reach.py` | tallied | nothing to tally (no send at all) | same as above |
| `seal_obs.py` | sealed (or, for `--unseal`, unsealed) | refused (the original file is left untouched), or post-unseal verification failed | usage error |

An empty run is never reported as success (exit 1 when there is nothing to measure). Missing some, but not all,
blocks or trials is reported as success (exit 0), with the missing pieces reported in fields such as `missing` /
`trials_incomplete`.
