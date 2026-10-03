# Data of the Windows cell

The runs of the Windows cell and of question 5 on Windows are recorded here, not in `data.md`, so that the
analysis of the macOS cells (from the macOS freezing tag, with `run.sh`, which reads `data.md`) and the analysis of the
Windows cell (from the Windows freezing tag, with the command of section 10.7 of the pre-registration) each read only
their own runs. Everything not stated here is as in `data.md`: the raw run directories are never committed to git,
and each one is deposited and recorded with the SHA-256 of its deposited archive.

## Run directories used by the analysis of the Windows cell

The lines of the form `- run_dir: PATH  manifest_sha256: HEX` below are read as in `data.md` (a relative path is
relative to this file; lines inside HTML comments and code fences are not read). Only runs of the Windows cell are
listed here; the runs of question 5 appear only in the tables below.

<!-- - run_dir: /path/to/an/unpacked/run-directory  manifest_sha256: 0000000000000000000000000000000000000000000000000000000000000000 -->

## Deposits

| Cell label | Deposit location | SHA-256 of the archive | Date deposited |
|---|---|---|---|
| (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) |

## Run checks (sections 3.4 and 10.8 of the pre-registration)

One line per attempt of the Windows cell and of the `working` condition of question 5, also for an attempt that the
runner aborted, made as in `data.md` (Deposit procedure, step 1) with two differences: the plan's SHA-256 is checked
against the plan of section 10.6, and the reference log's header must name `win_tsf`. Right after each attempt its
line is committed and pushed (section 10.8). The figures of the rule on presses are read from the version `excluding_mode_keys` of
`reference.segments`; the figure of deviation D4 is the largest count over the segments of the observation of
`observation.segments` (version `excluding_mode_keys`) minus the marker presses of the segment. The bounds of the
four counts are 0.

| Cell label | Attempt | Aborted by the runner | Checked at | SHA-256 of the plan | `src` of the reference log | `delivery.py` exit status | `max_beyond_markers_before_last_block` | `beyond_markers_last_block` | `n_loss_spans_with_unexplained_presses` | Observation rows beyond the markers (D4) | Counts as aborted | Deposited |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) | (to be filled in) |
