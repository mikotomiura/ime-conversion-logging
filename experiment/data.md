# Data

The raw run directories are never committed to git. Each one is deposited in a data repository and is
recorded here with the SHA-256 of its deposited archive.

## Run directories used by `run.sh`

`run.sh` reads the lines of the form `- run_dir: PATH  manifest_sha256: HEX` below (a relative path is relative
to this file; `HEX` is the SHA-256 of that run directory's `run.json`, 64 lowercase hexadecimal digits). The
analysis stops when the hash is missing or differs. Lines inside HTML comments and code fences are not read.
Add one line per run directory after the data has been retrieved from the deposit and unpacked. Runs whose
manifest says `aborted` are left out by the pipeline and are counted in the results. The labels that were
used are printed when the analysis ends.

<!-- - run_dir: /path/to/an/unpacked/run-directory  manifest_sha256: 0000000000000000000000000000000000000000000000000000000000000000 -->

- run_dir: runs/macos-standard-live-on-1  manifest_sha256: 0257755cb0a9d5001fc6ce3097a03df446129840d19342e3d8bf81aed1e7c268
- run_dir: runs/macos-standard-live-off-1  manifest_sha256: 4ac581cb8fcf89aa48e3311e51c0de1a965bdc42592305a9878ca0504ac2f519

## Deposits

| Cell label | Deposit location | SHA-256 of the archive | Date deposited |
|---|---|---|---|
| macos-standard-live-on (attempt 1; packed on the measuring machine) | https://doi.org/10.5281/zenodo.23183438 | `77bd8a8e64563921a9d3e49fc5fa552a67d4633a66669354852dea651fed49b8` | 2026-10-07 |
| macos-standard-live-off (attempt 1; packed on the measuring machine) | https://doi.org/10.5281/zenodo.23183438 | `e1cdf2e8b63ab402a950e37b8841ce302ccb280f396fefc148c8891971d73d10` | 2026-10-07 |
| macos-e3-working (the `working` run of question 5; packed on the measuring machine on 2026-10-06) | https://doi.org/10.5281/zenodo.23183438 | `77b51736e0fb713b181748153927b12e0ff6c7734b97f8c058891836aed5e3ea` | 2026-10-07 |
| macos-pilot-short-1 (calibration pilot; packed on the measuring machine on 2026-10-06) | https://doi.org/10.5281/zenodo.23183438 | `cde623bdab677a997e494fecf97a7d091baf4e542a703172f1997592fe6238be` | 2026-10-07 |
| macos-pilot-pilot-1 (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `6bd8583bfab9483d02b9b292697d2ac45ac836dad27e0f6946ef7569c663a749` | 2026-10-07 |
| macos-pilot-verify-1 (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `31e60d7627e6ddd5a2c7a7601fd9ef1c794cb31235395a8df4403d6dde7a80ae` | 2026-10-07 |
| macos-pilot-verify-s5 (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `96c460291e65506b5b9187fefcd82bf0017290f2ec677cea6f055fb73807c5f4` | 2026-10-07 |
| macos-pilot-pilot2-on (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `3bdf963d8812f63af3a2e10ec72af2b7adf6693da74dd85a19a443a8fdb1e40b` | 2026-10-07 |
| macos-pilot-pilot2-off (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `1e4563d2b196f974407c911fe96be84a50e7916055623d91cf1af2f307c26187` | 2026-10-07 |
| macos-pilot-verify-off (calibration pilot) | https://doi.org/10.5281/zenodo.23183438 | `28ac532edf171217b240ee7a75bb4118f584ce2b9581964b3d764c95dcb5fe11` | 2026-10-07 |
| macos-pilot-dryrun-1 (the dry run before the first pilot; interrupted) | https://doi.org/10.5281/zenodo.23183438 | `fdbcd130d1f2b3209add450cd3fb03b9c10774f0cf96377a56454824ec43f78e` | 2026-10-07 |

## Run checks (section 3.4 of the pre-registration)

One line per attempt, also for an attempt that the runner aborted. The rules are those of section 3.4 of the
pre-registration; with the settings fixed there, the figures of the rule on presses are read from the version
`excluding_mode_keys` of `reference.segments`, and the bounds of the three counts are 0.

| Cell label | Attempt | Aborted by the runner | Checked at | SHA-256 of the plan | `delivery.py` exit status | `max_beyond_markers_before_last_block` | `beyond_markers_last_block` | `n_loss_spans_with_unexplained_presses` | Counts as aborted | Deposited |
|---|---|---|---|---|---|---|---|---|---|---|
| macos-standard-live-on | 1 | no | 2026-10-03 04:15 UTC | `c7a5469de0febbbc2e90259e477900ab0eb68c74b86f420b1e9491799a1210ba` | 0 | 0 | 0 | 0 | no | yes |
| macos-standard-live-off | 1 | no | 2026-10-03 15:34 UTC | `3759ebf7832ba561820abab504929dbadd7e775a33e22ba0e89b92e4800009a2` | 0 | 0 | 0 | 0 | no | yes |
| macos-e3-working | 1 | no | 2026-10-05 01:17 UTC | `c7a5469de0febbbc2e90259e477900ab0eb68c74b86f420b1e9491799a1210ba` | 0 | 0 | 0 | 0 | no | yes |
In the run of the cell with live conversion off, the reference input field lost the focus once outside the plan,
for 2.7 s (2026-10-03, 04:20:33.68 to 04:20:36.35 UTC, in trial `t00004`). By the operator's report this was a
pointer click on another window; the logs cannot confirm the cause. No key was pressed, so no row of the
observation lacks a sent press within the loss, and 11 presses that the executor sent did not reach the reference
input field. The run does not count as aborted (section 3.4), and the trial is analysed (section 6.2).

## Analysis of the macOS cells

Run on 2026-10-05 on the measuring machine (macOS 27.0.1, Python 3.14.4), in an export of the macOS freezing tag
(the list of its files has the SHA-256 that `notes.md` gives), on the run directories listed above, unpacked from the
two archives. Every command exited with status 0. Paths are relative to the export, except `<data>`, which is this file, and
`<results>`, which is `experiment/results` of this directory.

| Question | Command | Output (SHA-256) |
|---|---|---|
| 1 to 3, registered (deviation D10, change 1: `kit/analysis/match.py` of release `v1.0.0`) | `python3 kit/analysis/pipeline.py --runs-from <data> --config experiment/config.json --seed 20260929 --seed-file experiment/SEED --prereg experiment/preregistration.md --out <results>` | `e1_delivery.json` `7b7e6ba085f7c7e16e2807bb46bff9ff4e0c8483fe9357b96b1feec4ff4d4c61`, `e1_detection.json` `ce595253043bddf460aba173420c994f256341fba8119afe573d7a7bc8fefd1b`, `e1_l1.json` `1e22b6fb82435c926c6251bb62e392a03d048923eda5610d9204f6b738d8ca49`, `e1_l2.json` `5642725b04988dabe198bcf78039b911f20fc58b7bff5652b11e2b2e9dd165c8` |
| 1 to 3, corrected (deviation D10, change 2) | the same command with `IME_MATCH_MERGE_RECOUNT=obs` and `--out <results>/d10/macos` | `e1_delivery.json` `7b7e6ba085f7c7e16e2807bb46bff9ff4e0c8483fe9357b96b1feec4ff4d4c61`, `e1_detection.json` `c6295552ba6a8d82c0ce4e1ae925a4913761247525cffc6220520f157951a16b`, `e1_l1.json` `9b8889d5905bb64fa7f234c81fb7d932bfca69276ea9a8783d70fd82c73d168b`, `e1_l2.json` `4a832d156da0b60d17271153cea1c36460a1c64493e1800af3794ecf80d087a4` |
| 4, open set (section 6.8) | `python3 kit/e2/open_set.py --obs <run>/obs.jsonl --plan <run>/plan.jsonl --corpus kit/corpus/data/phrases.jsonl --recipes kit/gen/<procedure file> --split-seed 20260929 --seed 20260929 --eval-fraction 0.2 --split both --n 3 --smoothing wb --beam 8 --length-info both --bootstrap 2000 --markers-in-obs --out <results>/e2_open_set_<cell>.json` | on: `963886dae2f6fa676df20a21ed9bbfb1fbfb986dfd67f35722be641b0aba45cc`, off: `77bba3a3830a03e50bbfc0bf0c71ea711618aaac73cbcdeb5ae20c8286f9c89d` |
| 4, closed set (section 6.8; deviation D8: `kit/e2/closed_set.py` of release `v1.0.0`) | `python3 kit/e2/closed_set.py --obs <run>/obs.jsonl --plan <run>/plan.jsonl --corpus kit/corpus/data/phrases.jsonl --recipes kit/gen/<procedure file> --config experiment/plan_config.macos.json --K 10,100,1000 --seed 20260929 --bootstrap 2000 --markers-in-obs --skip-scenario S12 --out <results>/e2_closed_set_<cell>.json` | on: `e2697df0c8ba1712bdd17142d3efa3989bf16e0942422987eca818fc0a385f88`, off: `e8ca561962d9440752b2967723e6bb5486282a9254b814e4ef626b912f92453e` |
| 5 (section 6.10) | `python3 kit/e3/summarize_e3.py idle=<idle>/samples.tsv working=<working>/samples.tsv > <results>/e3_macos.txt` | `0ada2e3e9c266814858bb7d11c9811498083bce3101f94790c69a0e79c6217ff` |

The procedure file is `kit/gen/recipes.json` for the cell with live conversion on and
`kit/gen/recipes.macos-live-off.json` for the cell with it off. Both runs of the closed set left out the 225 trials
of S12 and scored every block. The two samples of question 5 have the SHA-256
`f2fc37ef12d520d0e757a9e1232c99975475017c75cc824815a79e236d261222` (`idle`) and
`32fb07829de7a4ac0dca0e128089dfe2eed977e26b044841884ef4c3b96921ec` (`working`). The two files that deviations D10 and D8
replace are those of release `v1.0.0` of the public repository: `kit/analysis/match.py` has the SHA-256
`6f817228e2e7d5f17f65895c7a00a721c28c00df24c46df0c8bd4b237ad72cff` and `kit/e2/closed_set.py` the SHA-256
`364cc833b48a73aeb15b9416bff203ec21a71296e26534f914e3a2bb9bd067c3`; each differs from the file of the freezing tag
as `deviations.md` describes.

## Marker times of section 6.9 (run on 2026-10-05 on the measuring machine)

The per-block differences at the block-start markers (section 6.9 of the pre-registration) were measured for both
macOS cells on the machine of the previous section (Python 3.14.4), after the analyses above, in the same way as for
the Windows cell (`data.windows.md`, "Marker times of section 6.9"). `<cell>` is `macos-standard-live-on` or
`macos-standard-live-off`, `<run>` is the run directory of that cell listed above and `<results>` is
`experiment/results` of this directory.

- **Code.** `kit/pilot/clock_sync.py` and `kit/pilot/seal_obs.py` of the macOS freezing tag, taken with `git archive`
  with LF line ends together with `kit/formats`, whose validator the self-test of `seal_obs.py` calls (the two files
  are unchanged in the current commit). Both self-tests passed.
- **Inputs.** `run.json` of each `<run>` has the SHA-256 given above, and `oracle.jsonl`, `gt.jsonl` and `obs.jsonl`
  have the SHA-256 that the run manifest (`run.json`) records. The observation with key rows only was made from a copy of `obs.jsonl`
  outside the repository, by `python3 kit/pilot/seal_obs.py --obs <copy>/obs.jsonl --keys-out <copy>/obs.keys.jsonl
  --sealed-dir <copy>/sealed` (exit status 0; SHA-256 of `obs.keys.jsonl`: on
  `a3d7685c2383a4f937069b407a7778f2df376b169f7096b6e5db369faa6d7322`, off
  `8b8889b66f1a3c33cae780139ae0ebe14625e00485c16cea01a23a28689a39a9`); `<run>` was not changed.
- **Marker specification.** As for the Windows cell, it is the marker of `plan_config.macos.json` (left, right, left,
  right), the macOS key codes of these keys for the reference log (the example in `kit/pilot/README.md`), the category
  `navigation` for the observation, and the largest gap of that example, which is above the 150 ms of the plan's
  `marker_gap_ms`: `{"oracle_keys": ["left", "right", "left", "right"], "gt_vks": [123, 124, 123, 124],
  "obs_categories": ["navigation", "navigation", "navigation", "navigation"], "max_gap_ms": 300}` (one line in the
  file, SHA-256 `cd87248d8eace83dfe68e75b4f97af2452c9f97f8c285a17d5afb58f110c9045`). The window is the default of
  3,000 ms. The settings were fixed before the output was read; the differences correct no estimate (section 6.9).
- **Command.** `python3 kit/pilot/clock_sync.py --oracle <run>/oracle.jsonl --gt <run>/gt.jsonl --obs-keys
  <copy>/obs.keys.jsonl --marker marker.json --out <results>/clock_sync_<cell>.json`. Exit status 0 for both cells; a
  second run gave a byte-identical file. Output SHA-256: on
  `8daab9da3c1c2487d9dcf5786cb27e89405797b70a88a24e65a74a60d15ceb6d`, off
  `e289c5830a6d1d94f823ddebe3d872a968d882304485490c23e5f8b3ad6de7ca`. All 126 blocks of the cell with live conversion
  on and all 117 blocks of the cell with it off have a complete marker in the oracle, and the marker was found in
  every one of these blocks in the reference log and in the observation (none missing).

## Exploratory dwell quantiles of the macOS cells (run on 2026-10-06 on the measuring machine)

Not registered. The 2.5th and 97.5th percentiles of the dwell error and the fractions of pairs within 100 ms and 1 s
were computed for both macOS cells with `kit/analysis/explore_dwell_quantiles.py` on the machine of the previous
sections (macOS 27.0.1, Python 3.14.4). `<cell>` is `macos-standard-live-on` or `macos-standard-live-off`, `<run>` is
the run directory of that cell listed above (`run.json` has the SHA-256 given above), `<export>` is the export of the
macOS freezing tag whose `kit/analysis/match.py` is the one of release `v1.0.0` (SHA-256
`6f817228e2e7d5f17f65895c7a00a721c28c00df24c46df0c8bd4b237ad72cff`; deviation D10, change 1), the same export as for
the registered analysis above, and `<scratch>` is an empty directory outside this directory.

- **Command.** `python3 kit/analysis/explore_dwell_quantiles.py --code <export> --run <run> --config
  <export>/experiment/config.json --seed 20260929 --seed-file <export>/experiment/SEED --prereg
  <export>/experiment/preregistration.md --registered experiment/results/e1_l1.json --scratch <scratch> --out
  experiment/results/exploratory/dwell_quantiles_<cell>.json`. Exit status 0 for both cells; the frozen analysis
  reproduced the number of pairs, bias and median absolute difference of every speed before anything was written. A
  second run gave a byte-identical file.
- **Script.** SHA-256 `453bfc02f961a4c2b8436d542f0e3d1ecf44aca2b4b9c18d22d56ca599a91439` (also recorded in the output).
- **Output SHA-256.** on `eda7cae41160436af7f06c4c3e6fb14125e545793561405cca2d07dfd0a501b8`, off
  `4dc993a2d15ef95cc57b42481fcb630898cbcd877247831956da3476ee57aff8`.

The tables and values of the manuscript were then regenerated with `manuscript/tools/results_tables.py`.

## Deposit procedure

1. Right after every attempt, also one that the runner aborted, before the next run of the order (section 3.2 of
   the pre-registration), and before any analysis of the run, run on the run directory alone (it reads the key
   rows of the logs and the focus changes of the reference, not the session rows)

   ```
   python3 kit/analysis/delivery.py --plan <run>/plan.jsonl --oracle <run>/oracle.jsonl --obs <run>/obs.jsonl \
     --gt <run>/gt.jsonl --pre-ms 20 --tail-ms 300 --out <report>
   ```

   and check by hand that the plan's SHA-256 is the one that section 2.4 of the pre-registration gives the cell (live
   conversion on or off). Under section 3.4 of the pre-registration a completed run counts as an aborted attempt when
   it is not, when the command exits with status 2, when `max_beyond_markers_before_last_block` or
   `beyond_markers_last_block` of `reference.segments.excluding_mode_keys` exceeds 0, or when
   `reference_focus_loss.n_loss_spans_with_unexplained_presses` exceeds 0; such a run is neither analysed nor
   deposited. A run that the runner aborted is never analysed and is deposited only under the conditions of section
   3.4; one aborted inside a trial does not pass the check. No other reason and no explanation is admitted. Record
   the result of this check and its time in the table of run checks above before any analysis. The `idle` condition
   of question 5 has no line in the table: it sends no key, and only its samples are deposited (its observation log
   is not).
2. Deposit only `run.json` and the four logs of a run directory: `plan.jsonl`, `oracle.jsonl`,
   `gt.jsonl` and `obs.jsonl`. A log that an aborted run recorded with a null hash need not exist.
3. Do not deposit the `*.stderr.txt` files or `runner.error.txt`. They stay on the measuring machine.
4. On the measuring machine, bundle the files with
   `python3 kit/analysis/pipeline.py --pack <run-dir> --out <archive.zip> [--also-user NAME ...] [--also-home PATH ...]`.
   Pass the login name and the home directory of the measuring machine with `--also-user` and `--also-home`
   (each may be given more than once). The command refuses, with exit status 1 and without writing the
   archive, a run in which any of the five files contains an absolute path (a drive letter, a UNC path,
   `/Users/`, `/home/`, `/Volumes/`, an MSYS drive path such as `/c/`), the home directory, the repository
   path, a login name, or content in the ground-truth or observation log, and a run, completed or aborted by the
   runner, that the rules of section 3.4 of the pre-registration on the delivery report catch or that cannot be
   checked (an observation log without a reference log, or logs that `delivery.py` refuses); it does not check the
   plan's SHA-256 (step 1 does, by hand), because the pilot's runs used other plans; it exits with status 2 when a log
   no longer matches the hash recorded in `run.json`. Only paths that begin with `~` or `<repo>`, and
   relative paths, are allowed. It never overwrites an existing archive, writes the files in a fixed order
   with fixed times so that packing twice gives the same bytes, checks the contents of the archive again,
   and prints the archive's SHA-256.
5. Record the printed SHA-256 in the table above, together with the hash of `run.json`
   (`manifest_sha256` in the line of the list above).
6. Deposit that archive, unchanged. `python3 kit/analysis/pipeline.py --check-deposit <run-dir> ...` runs the
   same check on unpacked directories without writing an archive.

## Machine that ran the deposit check

Record, for every archive, the machine on which `--pack` was run and whether `--also-user` and `--also-home` were
passed. The check finds only the values it is given (see the docstring of `pipeline.py` for what it cannot find).
Do not write the values themselves here: they are login names and home directories, which this document must not
carry.

| Cell label | Machine (operating system, host role) | `--also-user` passed (number of values) | `--also-home` passed (number of values) | Date checked |
|---|---|---|---|---|
| macos-standard-live-on | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-05 |
| macos-standard-live-off | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-05 |
| macos-e3-working | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-short-1 | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-pilot-1 | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-verify-1 | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-verify-s5 | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-pilot2-on | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-pilot2-off | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-verify-off | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |
| macos-pilot-dryrun-1 | macOS 27.0.1, the measuring machine | yes (1) | yes (1) | 2026-10-06 |

## Calibration-pilot runs and the `working` run, packed on the macOS machine (2026-10-06)

The candidates of the datasheet (Part 2) and the `working` run of question 5 were packed from copies of the run
directories, each with `--also-user` and `--also-home` given once. The sealed observation logs of the pilot runs were
unsealed in the copies only to be deposited (`kit/pilot/seal_obs.py --unseal`); they were not read and are not
analysed. Before packing, `pipeline.py --check-deposit` passed on every copy (exit status 0). For each run the table
gives the two fields of its `run.json` that the datasheet asks for (`git_commit`: the commit of the working repository
that the runner ran from, which is not a commit of the public repository; `git_dirty`: whether that tree had
uncommitted changes) and the SHA-256 of `run.json`. The SHA-256 of each archive is in the table of deposits above.

| Run | `git_commit` | `git_dirty` | Aborted by the runner | SHA-256 of `run.json` |
|---|---|---|---|---|
| macos-e3-working | `null` | `null` | no | `a2fa68e47d6f8d70c316a68cf458eff8344050dda4826348ca758bbfeafde0f9` |
| short-1 | `9f1d28dcb2be473aeec7064128f620c60d189958` | false | no | `4a8a87d07207fd3aff7e92ac34b5055f1bf26f6c32e416de6f4322d7e83c8c6f` |
| pilot-1 | `9f1d28dcb2be473aeec7064128f620c60d189958` | false | no | `e9ca6047eb5df3aa820119b08005ac15d282121a227dee68b93805d7626b70a4` |
| verify-1 | `b38b99046f44e51ce44ed23cb8a61da56f99485c` | false | no | `5d1463b21060889dbd4897f900c100b8b3212ebe878938e3fc707690cf1aedff` |
| verify-s5 | `b38b99046f44e51ce44ed23cb8a61da56f99485c` | true | no | `0f90d21285ded29ab3f51f390670aaa8099bb701c58975b6aac68f10848ffd3a` |
| pilot2-on | `3e729f99edaef138ec0e64e21f0613821e8ff742` | false | no | `09b4e141b0eae1fb7a9aa90856047edefdadf1311700d9e1fc0e140ea0d99418` |
| pilot2-off | `3e729f99edaef138ec0e64e21f0613821e8ff742` | false | no | `5f7a321a8d76a1040bfb86635801245839b27d748a051a8813bfb6c37101a58f` |
| verify-off | `3e729f99edaef138ec0e64e21f0613821e8ff742` | true | no | `d05bb55c415d63eef48e43e2bf2b7092888365cc607bef3166daf51f12abde52` |
| dryrun-1 | `9f1d28dcb2be473aeec7064128f620c60d189958` | false | yes (`interrupted (SystemExit)`) | `3ea7a2a3a520790c72cec11a1d6e03ae2e4e234c43e1935379800db18efdd97e` |

The `working` run has the plan of the cell with live conversion on (`c7a5469d…`, the line `macos-e3-working` of the
run checks above); the logger and runner of that run were started from an export, not from a git work tree, which is why
its two fields are `null`, as for the two measurement runs. Every run of the runner that the measuring machine holds
from before the first pilot is in the table: besides `short-1` (nine trials) it is `dryrun-1`, a run with the backend
`dryrun` (no key sent) that was interrupted and is deposited because it passes the check. No candidate failed the check.
The error output of the programs (`*.stderr.txt`, `*.runner.txt`) is not deposited.

The runtime samples are the files of the `idle` and the `working` condition (`samples.tsv` and `conditions.txt`). They
are not bundled by `--pack`. Before they were moved, they were checked by eye and by a search: none of the four files
holds a path, the login name or the home directory. Their SHA-256 are those given above under question 5.

The SHA-256 of the two `conditions.txt` were read on the measuring machine on 2026-10-07, from the files that were
bundled and from the run directories they were copied from (the two values are equal for each condition).

The `conditions.txt` of the `idle` condition has the SHA-256 `25a08f08c98ce4589ed34475b3dd803e178b8f52429ad66c2a71ec0c7fba680f`.

The `conditions.txt` of the `working` condition has the SHA-256 `95f29b15a7de9fc1b6334f804f1d8dd5f9377ec21002d12167d5d0f916dd7ae4`.

### Move of the macOS archives and samples to the deposit machine (2026-10-07)

The nine archives of the table above and the four sample files were moved from the measuring machine to the
deposit machine through a folder of the researcher's Google Drive (`mac/`, with the sub-folders `pilot/` and
`e3-samples/{idle,working}/`), uploaded by hand. After the upload, the file names and sizes in the folder were
compared with the local files through the Drive API: 13 files, every size equal (the archive of the `working` run
74,028,949 bytes; the eight pilot archives; the two `samples.tsv` 31,631 and 32,047 bytes; the two `conditions.txt`
299 and 335 bytes), and nothing else in the folder. Drive does not return a SHA-256, so the check is of the sizes
only. The SHA-256 of each archive are to be compared with the table of deposits above on the deposit machine
before anything is uploaded to Zenodo.

The two archives of the measurement (`macos-standard-live-on`, 74,044,861 bytes, and `macos-standard-live-off`,
66,091,676 bytes), packed earlier on the measuring machine, were moved later on the same day through the same
folder. On the deposit machine, on 2026-10-07, the deposit tool of the working repository (which is not part of the
code release) found all fifteen files of this record by their SHA-256: every archive in the table of deposits, the
two `samples.tsv` and the two `conditions.txt`. The `run.json` of the two archives of the measurement, whose SHA-256
are those of the list of run directories above, has `null` in `git_commit` and in `git_dirty` (read on the deposit
machine from the archives; the runner ran from an export).

On 2026-10-07 the files of this record were uploaded to the draft of the dataset from the deposit machine with that
tool, except the archives of the two measurement runs and of the `working` run: their uploads from the deposit machine
broke off repeatedly, so they were uploaded by hand through the web form of the archive service from the measuring
machine. The tool then checked that every file of the draft has the MD5 and the size of the file that it had found on
the deposit machine by the SHA-256 recorded here, and that the draft holds exactly the files of the deposit.

## Phrase corpus

`kit/corpus/data/phrases.jsonl` is the phrase corpus read by the scenario generator. It was built once with
`kit/corpus/build_corpus.py`; later runs read only this file and no longer depend on the dictionary.

| Item | Value |
|---|---|
| `--seed` | 1 |
| `--per-work` | 150 |
| `--min-reading` | 6 (default) |
| `--max-reading` | 30 (default) |
| SHA-256 of `phrases.jsonl` | `c8f890cb4d0848bf04e6d7d95efe4e4d3dfd02a23eb1c7f103344f14d13e4620` |
| Tokenizer and dictionary | `SudachiPy==0.7.0`, `sudachidict-core==20260723.1` (pinned in `kit/corpus/requirements.txt`) |
| Phrases | 1,800 (150 from each of 12 works; reading length 6 to 30, median 16) |
| Date built | 2026-09-30 |

Building twice from the same inputs gave byte-identical output.

### Source works

All works are in the public domain and were obtained from Aozora Bunko (https://www.aozora.gr.jp/) as the
ruby-annotated text files in modern orthography (new character forms and modern kana usage). Each archive
holds one `.txt` file, which was renamed to `<work_id>.txt` before the build. The SHA-256 is that of the
downloaded archive.

| `work_id` | Author | Title | Aozora work ID | Text file | SHA-256 of the archive |
|---|---|---|---|---|---|
| `akutagawa_kappa` | Akutagawa Ryunosuke | Kappa | 000069 | `cards/000879/files/69_ruby_1321.zip` | `8daf7b994cebd17dff5a5447fd18d0408a153855ea821b9bec138ee3e68a49e9` |
| `dazai_ningen_shikkaku` | Dazai Osamu | Ningen Shikkaku | 000301 | `cards/000035/files/301_ruby_5915.zip` | `fb37d694f86687f9e84e26643804fa84b4346895f5f56a90dd8843a12e2bc9c8` |
| `dazai_shayo` | Dazai Osamu | Shayo | 001565 | `cards/000035/files/1565_ruby_8220.zip` | `712a759c175c7341bb2916f747f95d0d900923b353d511001ad677b7411eabc8` |
| `hayashi_horoki` | Hayashi Fumiko | Horoki (first edition) | 045649 | `cards/000291/files/45649_ruby_26492.zip` | `179eaee1bd51bfc1987daf16f293d6819083759ba5db0ffc95307f060b988947` |
| `miyazawa_ginga_tetsudo` | Miyazawa Kenji | Ginga Tetsudo no Yoru | 000456 | `cards/000081/files/456_ruby_145.zip` | `8bc90c0c7f97e069d02c4783ce905fa7b959cf60a74df816aeccf94079555076` |
| `nakaya_yuki` | Nakaya Ukichiro | Yuki | 052468 | `cards/001569/files/52468_ruby_49668.zip` | `2d7b144a0f219e8b8e0e861745deba270a37246108fab6271854d4e7653db848` |
| `oda_meoto_zenzai` | Oda Sakunosuke | Meoto Zenzai | 000545 | `cards/000040/files/545_ruby_33101.zip` | `4a88e427c77bcebe21b04bc49aa6cf57e381f618caf7c945a4cc98b42347b89d` |
| `okamoto_hanshichi_01` | Okamoto Kido | Hanshichi Torimonocho 01: Ofumi no Tamashii | 001293 | `cards/000082/files/1293_ruby_5382.zip` | `f409f1374648dbb29d91d50960f95d35e6f94eee14c3a01aa3044f2190f3452e` |
| `sakaguchi_darakuron` | Sakaguchi Ango | Darakuron | 042620 | `cards/001095/files/42620_ruby_21051.zip` | `82db555d429391899b8110b2416739825689596b5196ff5fa3fe87f05abcec40` |
| `soseki_botchan` | Natsume Soseki | Botchan | 000752 | `cards/000148/files/752_ruby_2438.zip` | `53c4d86033d0e4590c0354df5fec2709820ef21c09fa402826ff33376ec21228` |
| `soseki_kokoro` | Natsume Soseki | Kokoro | 000773 | `cards/000148/files/773_ruby_5968.zip` | `c55d7bf6c7cc5bd960ce291949f38cae9ba6a672a6142c3b9e1a9f5a159fa4c4` |
| `terada_kaki_no_tane` | Terada Torahiko | Kaki no Tane | 001684 | `cards/000042/files/1684_ruby_11273.zip` | `ce6792f93e0b98a747b9dd452e4bdb8519b3439975bf68f06c9c605dae747c54` |

The works mix novels, essays and a critical essay so that the corpus is not drawn from a single style.
Text file paths are relative to `https://www.aozora.gr.jp/`.

### Readings

A spot check of 40 randomly drawn phrases found the readings to be valid hiragana key sequences; a few
differ from the reading the author intended (for example, a less common kanji compound read by its
components). This does not affect the accuracy evaluation, which only needs a valid key sequence, and the
reconstruction evaluation uses the typed reading as its ground truth. Nine readings occur twice under
different `phrase_id` values.
