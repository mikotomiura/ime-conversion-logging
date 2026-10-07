# Notes

## Questions and result files

The questions, the estimates that answer them, and the precision expected at the planned numbers of trials
are stated in `preregistration.md`. The result files are:

| Question | Estimates | Result file |
|---|---|---|
| How completely does the observer detect the sessions of the reference input field? | Precision, recall, split and merge rates, per comparison and scope; the prediction's summary per cell, and the rows of the trials in which the reference lost focus | `results/e1_detection.json` |
| How closely do the per-session records agree with the sessions of the reference input field? | Dwell error, abandonment, navigation counts, the 20-second cut-off, plan-versus-ground-truth disagreements | `results/e1_l1.json` |
| How closely do the per-text aggregates a writing researcher might compute agree? | Bland-Altman bias and limits and ICC(A,1) of four per-text measures, each with an interval; biases under mixtures of speeds; per-trial differences by scenario and speed | `results/e1_l2.json` |
| How did the scripted presses arrive? | Send delay, reach in both directions, delay and interval error of the rows, focus losses of the reference | `results/e1_delivery.json` |
| How much of the typed text can be recovered from a published log? | Closed-set identification accuracy against chance, open-set character error rate against a prior-only baseline, unscored blocks | `results/e2_closed_set_<cell>.json`, `results/e2_open_set_<cell>.json` |
| What does the observer cost the machine? | CPU and resident memory under two conditions | `results/e3_<system>.txt` (the standard output of `kit/e3/summarize_e3.py` for both conditions) |

The comparisons are ground truth against observation (`gt_obs`, primary), ground truth against the observer's
specified rows (`gt_pred`) and those rows against the observation (`pred_obs`); the scopes are `under_limit`,
`all_trials` and `over_limit` (section 6.4 of the pre-registration). The result files of questions 1 to 3
(`e1_detection.json`, `e1_l1.json` and `e1_l2.json`) name their primary comparison and scope (`gt_obs` under
`under_limit`). The values under `all_trials` depend on the design's fraction
of long trials.

## Pre-registration

The analysis is frozen in `preregistration.md`. `run.sh` and `pipeline.py` refuse to analyse real data until
that file carries, outside HTML comments and code fences, exactly one line starting `frozen_at: YYYY-MM-DD`
and exactly one line `sha256: <64 lowercase hex digits>`. Placeholders and empty values are not marks. The
recorded `sha256` must equal the value printed by

    python3 kit/analysis/pipeline.py --freeze-hash --config experiment/config.json --seed-file experiment/SEED

which is the SHA-256 of the text made of the SHA-256 (lowercase hex) of `config.json`, of `SEED` and of the
ground-truth rule file named by `gt_rules`, in this order, each followed by a line feed. A person writes the
value in the file when the analysis is frozen; both entry points recompute it and stop with exit status 3
when it differs, so that the analysis settings cannot change after the freeze. Analysis code is fixed by the
tag of the freezing commit. `config.json`, `SEED` and the rule file are stored with LF line ends so that the
hash is the same on every machine. Synthetic runs (`bash run.sh --synthetic`) do not need the freeze because
they read no real data.

The protocol is frozen in two stages. The first freeze covers the macOS cells. The addendum for the Windows
cells and the runtime cost on Windows, frozen before the first Windows measurement, may change only the Windows
parts of existing files (the Windows blocks of the rule file, the Windows sections of the procedure files and the
Windows profile of the plan generator) and add its own section to `preregistration.md` and new files that only
the Windows cells use; it then rewrites the two marks. That `config.json`, `SEED`, the macOS block of the rule
file, the macOS parts of the files of the macOS cells and the macOS sections are unchanged is shown by the
difference between the two freezing tags. The macOS plans are generated, and the macOS analysis is run, from the
macOS freezing tag.

The macOS cells were frozen on 2026-10-02. The freezing commit of the working repository is
`ff122e3246346da7869c4e990e4978acb1f191c6`, tagged `ime-observation-prereg-macos`; the recorded `sha256` is
`64dfbb6e5f08222ade1fb96a8171d97c0fa1bc9ca9adb5cc987fc27bd9a8af2a`. The list of the files exported from that commit
(section 9 of the pre-registration) holds 98 files and has the SHA-256
`29b183ba245939ad82d9fa2d7db26e1a18520e2966f4aa9ec74478a4e51e6c49`. The frozen file was registered on OSF
Registries on 2026-10-02 at https://osf.io/9nh7r (DOI 10.17605/OSF.IO/9NH7R); the copy attached to the
registration has the SHA-256 of the frozen file, `fa6fd586d580c47f13506cd36a87a94c08d6b9ab55b16a7b6df0b67670f87773`.

The Windows addendum (section 10 of the pre-registration) was frozen on 2026-10-03. The freezing commit of the working
repository is `a57c4fd79134d68364c29b440637bfb717a881cb`, tagged `ime-observation-prereg-windows`; the recorded
`sha256` is unchanged (`64dfbb6e5f08222ade1fb96a8171d97c0fa1bc9ca9adb5cc987fc27bd9a8af2a`), because the addendum
does not change `config.json`, `SEED` or the rule file. The list of the files exported from that commit holds 108
files and has the SHA-256 `f61af353301e1370ac27c4a7425e8c2eb32991df9f7a2a765a26948da87787b7`. The frozen file has
the SHA-256 `f86209631e495d2c7e80dab266a91814728aa55a18485da106df836c3cec2b1d`. The frozen file was registered on OSF
Registries on 2026-10-03 at https://osf.io/4jwzn (DOI 10.17605/OSF.IO/4JWZN), before any Windows measurement; the
copy attached to the registration has the SHA-256 of the frozen file.

The working repository is not public; the registrations name its freezing commits. The public repository does not
carry its history: its first two commits are the exports of the two freezing commits, tagged with the same names, so
that the two lists above can be recomputed from it (`README.md`, "Recomputing the lists of the freezing tags").

## Step 0 replaced

The reproduction template's Step 0 calls a capability-gate script of the parent repository. This
experiment's directory does not refer to anything outside `papers/` (the directory must be exportable on
its own), so Step 0 is replaced by checks that `pipeline.py` performs before any statistic:

1. the content-free validators for the ground-truth and observation logs, the plan validator, and the
   oracle validator (a log with content, or with the wrong shape, stops the run with exit status 1);
2. a check that the observation log's header declares the `key` and `ime_shadow` channels in its
   capabilities, so a run made with a logger that lacks either channel cannot be analysed by mistake;
3. when the header places `ime_shadow` among the gated channels and the ground truth holds sessions, at
   least one `ime_shadow` row must exist: a gated channel that produced no row cannot be judged.

Before these, `pipeline.py` checks the freeze marks, the run manifests, and the SHA-256 of every log against
its manifest.

## Analysis settings

Every cell is analysed under the session definition that follows the operating system of the cell
(`macos_def` or `windows_def`), which is the definition of the analysis, and under the common definition `all`;
the results are stored as `cells[label][definition][comparison][scope]`. On the reference side of a macOS cell
the two definitions hold the same sessions; `e1_detection.json` reports this check (`same_sessions_as_all`).
The cell label comes from the `label` of the cell file recorded in `run.json`. `iou_min`,
`obs_end_offset_ms`, `theta` and `drop_start_while_open` are passed to the matcher; `bootstrap_B`, `limit_ms`,
`text_size_trials` and `mixtures` to the level-1 and level-2 stages.

Clock offsets are not corrected: every block is compared with a zero offset (`clock_offsets_ms` is not passed
to the matcher). The dwell error is a difference of durations and does not depend on the offset. Two intervals
of the same length pair under the overlap criterion while the offset stays at or below a third of that length,
and intervals of different lengths tolerate less (section 6.9 of the pre-registration). The per-block
offsets between the observation, the reference input field and the executor are measured at the block-start
markers with `kit/pilot/clock_sync.py` in every run and reported with the results.

## Borrowed methods

| Method | Where it is used | Source |
|---|---|---|
| Wilson score interval | detection rates, abandonment and navigation agreement, plan-versus-ground-truth disagreements, closed-set accuracy | Wilson (1927) |
| Percentile bootstrap | intervals of the dwell error, the bias and limits of agreement, kappa, the ICC, the biases under mixtures of speeds, the per-trial differences, the open-set error rate, and (descriptively) the closed-set accuracy | Efron (1979) |
| Resampling whole blocks (cluster bootstrap) | the same intervals | Cameron, Gelbach and Miller (2008) |
| Bland-Altman limits of agreement | dwell error, per-text aggregates | Bland and Altman (1986) |
| ICC(A,1) (equal to ICC(2,1)), absolute agreement | per-text aggregates | Shrout and Fleiss (1979); McGraw and Wong (1996) |
| Cohen's kappa | abandonment | Cohen (1960) |
| Witten-Bell smoothing | the open-set attacker's character language model | Witten and Bell (1991) |

Full references are listed in `preregistration.md`.
