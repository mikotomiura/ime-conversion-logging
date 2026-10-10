<p align="center"><b>English</b> | <a href="README.ja.md">Japanese</a></p>

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo-light.svg" width="600" alt="ime-conversion-logging">
</picture>

# Logging Japanese IME conversion without recording text

**A logger that records the conversion sessions of a Japanese input method, with their duration, their arrow-key
presses and whether they were abandoned, without recording a single character.**

*Observability limits, a cross-platform logger, and a synthetic-input validation*

<img src="assets/demo.svg" width="100%" alt="Left: the romaji n, i, h, o, n, g, o typed with the input method on become
a reading in an input field; Space converts it and Enter commits it. Right: the rows that the logger writes at the same
times: a key category and a time for every key press and, when the conversion ends, its duration, the number of
arrow-key presses during it and whether it was abandoned. No character is written.">

[![Code release](https://img.shields.io/badge/code%20release-v1.0.2-1682D4?style=for-the-badge)](https://doi.org/10.5281/zenodo.23183440)
[![Dataset](https://img.shields.io/badge/dataset-Zenodo-1682D4?style=for-the-badge)](https://doi.org/10.5281/zenodo.23183438)
[![Pre-registered](https://img.shields.io/badge/pre--registered-OSF-2C6EAB?style=for-the-badge)](#pre-registration)

[![DOI of the code](https://zenodo.org/badge/DOI/10.5281/zenodo.23183440.svg)](https://doi.org/10.5281/zenodo.23183440)
[![DOI of the data](https://zenodo.org/badge/DOI/10.5281/zenodo.23183438.svg)](https://doi.org/10.5281/zenodo.23183438)
[![Code license: Apache-2.0](https://img.shields.io/badge/code-Apache--2.0-3C873A)](LICENSE-CODE)
[![Data and documents license: CC BY 4.0](https://img.shields.io/badge/data%20and%20documents-CC%20BY%204.0-3C873A)](LICENSE-DATA)
![Platforms: Windows and macOS](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS-555555)

</div>

Keystroke loggers used in writing research record the conversion of a Japanese input method **either not at all or
with text**: key names that spell the reading, or the composition and the screen text. This repository holds the
artifacts of a single paper on observing that conversion **without recording any text**: which documented interfaces
of Windows and macOS make it observable, a **standalone logger** for both systems, and a **pre-registered
validation** of the logger against a reference input field, with synthetic input. Commands in the documents of this
repository are run from its root.

> [!NOTE]
> Raw logs are not committed to this repository. They are deposited as a dataset
> ([10.5281/zenodo.23183438](https://doi.org/10.5281/zenodo.23183438)), and the SHA-256 of every archive is recorded
> in `experiment/data.md` (macOS) and `experiment/data.windows.md` (Windows).

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-the-logger-records">What the logger records</a> ·
  <a href="#how-the-logger-was-validated">Validation</a> ·
  <a href="#reproducing-the-analyses">Reproducing the analyses</a> ·
  <a href="#paper-and-citation">Paper and citation</a> ·
  <a href="#repository-layout">Layout</a> ·
  <a href="#license">License</a>
</p>

## Quick start

Run the selftests of the kit, and every stage of the analysis on two synthetic runs (nothing real is read):

```bash
git clone https://github.com/mikotomiura/ime-conversion-logging.git
cd ime-conversion-logging
bash kit/check.sh
bash experiment/run.sh --synthetic
```

Build and run the logger (Rust and Cargo are needed), then check its log against the format:

```bash
cargo build --release --manifest-path kit/logger/Cargo.toml
kit/logger/target/release/ime-observation-logger --out obs.jsonl --duration-s 60
python3 kit/formats/validate_logs.py --kind obs obs.jsonl
```

On macOS the logger needs the Input Monitoring permission. When it is missing, the logger asks the system to show the
permission prompt and stops with an error; grant the permission and start it again. The logger never overwrites an
existing log. Its README ([`kit/logger/README.md`](kit/logger/README.md)) describes the drivers, the options and the
limitations.

## What the logger records

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/records-dark.svg">
  <img src="assets/records-light.svg" width="100%" alt="Written to the log: for every press and release of an observed
  key, one of fourteen categories, the time and whether it was a press; when a detected conversion session ends, its
  duration, the arrow-key presses during it and whether it was abandoned (a session cut off writes no row). Never
  written: key names and key codes, characters and readings, candidate strings and window names.">
</picture>

The logger (`kit/logger/`) runs on Windows and macOS. It writes a row for every press and release of the keys it
observes, with one of fourteen key categories and the time, and, when a conversion session that it detects ends, a
summary row with its duration, the number of arrow-key presses during it and whether it was abandoned; a session that
is cut off instead, for example at 20 s, writes no row. Which keys are observed and how a session is detected differ
by system; the article gives the rules of each system. The key name and the key code are discarded and only the
category is written, though some categories, such as `backspace` or `enter`, stand for a single key. No character,
candidate string, reading or window name is recorded. The format of the log, `obs/1`, is specified in
[`kit/formats/FORMATS.md`](kit/formats/FORMATS.md). The key rows are not anonymous by construction: the article
measures how far they reveal the typed phrase.

## How the logger was validated

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/validation-dark.svg">
  <img src="assets/validation-light.svg" width="100%" alt="A plan generated from the phrase corpus and the settings
  drives an executor that sends every key press and release and records what it sent. The presses reach the input
  method and a reference input field, which records the calls it receives without text, and the logger. The sessions
  of the reference and the rows of the logger are matched by wall-clock time under the registered rules, with the
  trials and the presses sent taken from the executor's record.">
</picture>

The validation compares the logger's rows with a reference: an input field that records the calls it receives from
the input method. A program generated the plans and sent the key presses, and no person was meant to type during the
runs. The measurement covered three settings:

| Setting | Input method | Trials |
|---|---|---:|
| `macos-standard-live-on` | macOS Japanese input method, Romaji input, live conversion on | 3,150 |
| `macos-standard-live-off` | The same, live conversion off | 2,925 |
| `windows-msime-tsf` | Microsoft IME (Japanese), Romaji input, with a reference field that is a client of the Text Services Framework | 2,925 |

On macOS the logger infers each session from the key presses; on Windows a session lasts while a window of the input
method is visible, which differs in construct from the reference's compositions. The questions, the analyses and the
stopping rules were registered before the measurement ([Pre-registration](#pre-registration)), and the departures
from them are reported in `experiment/deviations.md`.

## Reproducing the analyses

1. Download the archives of the runs from the dataset
   ([10.5281/zenodo.23183438](https://doi.org/10.5281/zenodo.23183438)). Its datasheet (`datasheet.md`, also in
   `experiment/`) describes every file and gives the SHA-256 of every archive and sample.
2. Follow `experiment/data.md` (macOS) and `experiment/data.windows.md` (Windows). For every result file they give
   the code it was made with, the command, its inputs and the SHA-256 of the output.

The result files are in `experiment/results/`. The tables, the values quoted in the text and the figures of the
article are generated from them by `manuscript/tools/results_tables.py`, and `manuscript/BUILD.md` describes how the
manuscript is built.

## Paper and citation

> **Logging Japanese IME conversion without recording text: Observability limits, a cross-platform logger, and a
> synthetic-input validation**<br>
> Mikoto Miura, independent researcher
>
> The article is in preparation, and its sources are in [`manuscript/`](manuscript/). Pre-registration:
> [macOS cells](https://osf.io/9nh7r) and [Windows addendum](https://osf.io/4jwzn). Code:
> [10.5281/zenodo.23183441](https://doi.org/10.5281/zenodo.23183441). Data:
> [10.5281/zenodo.23183438](https://doi.org/10.5281/zenodo.23183438).

`CITATION.cff` gives the citation of the code, and GitHub shows it under "Cite this repository". In BibTeX:

```bibtex
@software{miura2026code,
  author    = {Miura, Mikoto},
  title     = {Logging {J}apanese {IME} conversion without recording text: Logger, validation kit and analysis code},
  version   = {1.0.2},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23273986},
  url       = {https://doi.org/10.5281/zenodo.23273986}
}

@dataset{miura2026data,
  author    = {Miura, Mikoto},
  title     = {Logging {J}apanese {IME} conversion without recording text: Records of the synthetic-input validation},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23183438},
  url       = {https://doi.org/10.5281/zenodo.23183438}
}
```

## Pre-registration

| Registration | Frozen | Record | Tag |
|---|---|---|---|
| macOS cells | 2 October 2026 | [osf.io/9nh7r](https://osf.io/9nh7r) (DOI 10.17605/OSF.IO/9NH7R) | `ime-observation-prereg-macos` |
| Windows addendum | 3 October 2026 | [osf.io/4jwzn](https://osf.io/4jwzn) (DOI 10.17605/OSF.IO/4JWZN) | `ime-observation-prereg-windows` |

The registration is `experiment/preregistration.md`, and the departures from it are in `experiment/deviations.md`.

## Repository layout

| Directory | Contents |
|---|---|
| `kit/formats/` | Log-format contracts and their validators |
| `kit/corpus/` | Aozora Bunko phrases and their readings |
| `kit/gen/` | Scenario generator |
| `kit/emit/` | Emitter (synthetic key dispatch and the oracle) |
| `kit/run/` | Single-cell orchestration |
| `kit/refinput-macos/`, `kit/refinput-windows/` | Reference input fields |
| `kit/logger/` | Standalone logger |
| `kit/pilot/` | Calibration pilot tooling |
| `kit/analysis/` | Ground-truth assembly, matching, L1, L2, pipeline |
| `kit/e2/`, `kit/e3/` | Leakage evaluation, execution cost |
| `experiment/` | Registration, deviations, run records (`data.md`, `data.windows.md`), environment, results and the datasheet of the dataset (`datasheet.md`, also deposited with the data) |
| `manuscript/` | The manuscript, its supplement and the tools that build them |
| `assets/` | The figures of this file and the script that makes them (`make_assets.py`) |

## History of this repository

The repository does not carry the history of the working repository in which the study was developed. Its history
is, in order:

1. the files exported from the freezing commit of the macOS cells, tagged `ime-observation-prereg-macos`;
2. the files exported from the freezing commit of the Windows addendum, tagged `ime-observation-prereg-windows`;
3. a commit that adds only `manuscript/outline.md` as it stood before the results were analysed;
4. release 1.0.0, tagged `v1.0.0`;
5. release 1.0.1, tagged `v1.0.1`. Apart from `CITATION.cff`, `.zenodo.json` and the two README files, it changes
   only how the manuscript is typeset and checked: the five tables that did not fit on their page (Tables 1, 2, 4, 7
   and 13) are set so that they can continue onto the next page, and the build also lists and counts the pages on
   which text or a line lies outside the page or over the page number. The text and the values of the manuscript, the
   logger, the kit, the analysis code and the results are those of release 1.0.0; the script that generates the
   tables changed only in the layout of Table 13.
6. release 1.0.2, tagged `v1.0.2`. Three feature sets of the closed-set attack (`no_runs`, `bin1s_count` and
   `counts_only`) keep the lengths of runs of letter keys, although the registration describes them as dropping
   them. The release corrects their description in the manuscript and in `kit/e2/README.md` (the comments and
   docstrings of the frozen code are left unchanged) and adds an exploratory analysis of two feature sets that drop them, recorded as
   deviation D13 in `experiment/deviations.md`: the script `kit/e2/explore_run_lengths.py`, its results in
   `experiment/results/exploratory/`, the records of its runs in `experiment/data.md` and
   `experiment/data.windows.md`, and the text, the values and a table of the supplement in the manuscript. The
   registered results and the code that produced them are unchanged from release 1.0.0; the script that generates the
   tables also reads the exploratory results.

The first three commits carry the date of the working commit they come from, and their messages name it. The
license texts, this file's identifiers and figures and the line-end rules of `.gitattributes` were added in
release 1.0.0.

<details>
<summary>Recomputing the lists of the freezing tags</summary>

Section 9 of the pre-registration defines the list of the files exported from a freezing commit: one line per file,
with the relative path, a space and the SHA-256 of the file, sorted by the bytes of the path, each line ended by LF.
The registrations give the SHA-256 of these lists: `29b183ba245939ad82d9fa2d7db26e1a18520e2966f4aa9ec74478a4e51e6c49`
(98 files, `ime-observation-prereg-macos`) and `f61af353301e1370ac27c4a7425e8c2eb32991df9f7a2a765a26948da87787b7`
(108 files, `ime-observation-prereg-windows`). The two tagged commits hold no `.gitattributes`, so take their files
without line-end conversion, into a new empty directory. The script prints the SHA-256 and the number of files,
compares them with the values given to it, and exits with 1 when they differ:

```bash
d=$(mktemp -d) && git -c core.autocrlf=false archive ime-observation-prereg-macos | tar -x -C "$d" &&
python3 - "$d" 29b183ba245939ad82d9fa2d7db26e1a18520e2966f4aa9ec74478a4e51e6c49 98 <<'EOF'
import hashlib, os, sys
root, want = sys.argv[1], (sys.argv[2], int(sys.argv[3]))
rows = sorted((os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/").encode("utf-8"),
               hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest())
              for d, _, names in os.walk(root) for n in names)
text = b"".join(p + b" " + h.encode("ascii") + b"\n" for p, h in rows)
got = (hashlib.sha256(text).hexdigest(), len(rows))
print(*got, "match" if got == want else "MISMATCH")
sys.exit(0 if got == want else 1)
EOF
```

For the Windows tag, run the same commands with `ime-observation-prereg-windows`, its SHA-256 and 108. With
`core.autocrlf=true` (the default of Git for Windows) the archive is written with CRLF line ends and the list does
not match.

</details>

## Verification

```bash
bash kit/check.sh              # run every selftest under kit/ (a no-op counts as red)
bash kit/check.sh --compile    # py_compile every .py under kit/
bash kit/check.sh --self-test  # this entry point's own negative controls
```

- Only the `.py` files under `kit/` that carry the `# kit: selftest` marker comment are run with
  `--selftest` (unmarked helper modules are only compiled). The marker is written as a comment where **the entire
  line reads `# kit: selftest`** (a marker inside a string literal is not picked up).
- The entry point runs on every verify and on every acceptance check, so **selftests are written to
  be fast by default** (under 30 seconds each; exceeding that triggers a warning). Heavy checks are
  split into something like `--selftest --full` and are not run by default.
- The log-format contract is [`kit/formats/FORMATS.md`](kit/formats/FORMATS.md). The **shape** of
  `gt` and `obs` rows (string values fit an enumeration or regular expression, and there are no
  unknown keys or escapes) is verified mechanically by `kit/formats/validate_logs.py`. The meaning
  of integer values, version strings, and `manifest_sha256` is out of scope; keeping the log
  content-free is the producing side's responsibility (writing only kind, length, and time), as
  described in FORMATS.md's "Scope of guarantee".

## License

Split by kind, not by directory.

| Kind | License | Full text |
|---|---|---|
| Code (source in `.py`, `.sh`, `.swift`, `.rs` and so on) | Apache License 2.0 | `LICENSE-CODE` |
| Data (including the phrases and readings in `kit/corpus/data/`) and documents (`*.md`, the records under `experiment/`, `manuscript/`) | Creative Commons Attribution 4.0 International | `LICENSE-DATA` |
