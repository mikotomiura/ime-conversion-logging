# corpus — Aozora Bunko phrases and readings

Preprocessing step that extracts **phrases** of the length each scenario requires from public-domain
Aozora Bunko texts, attaches a **hiragana reading** to each phrase, and writes the result as `corpus/1`
(see [`../formats/FORMATS.md`](../formats/FORMATS.md)). One accuracy experiment turns the reading into a
key sequence; another treats the reading as the "typed reading" (ground truth).

| File | Role |
|---|---|
| `build_corpus.py` | The preprocessing tool (`--src` through the write-out step / `--validate` / `--selftest`). Python standard library only (except `sudachipy`, used to assign readings) |
| `data/phrases.jsonl` | The output of the production run (placed separately; not produced by this step) |

## Usage

```bash
# Write out the corpus (production runs use the default sudachi tokenizer).
python3 build_corpus.py --src <directory of Aozora Bunko .txt files> --out data/phrases.jsonl --seed 1 \
    [--tokenizer sudachi|table:<json>] [--min-reading 6] [--max-reading 30] [--per-work 100]

python3 build_corpus.py --validate data/phrases.jsonl   # check against corpus/1 (exit 1 on violation)
python3 build_corpus.py --selftest                      # runs without a dictionary (check.sh runs this too)
```

- `--src` reads `*.txt` files (**cp932**) in name order (subdirectories are not scanned). `work_id` is
  derived **from the file name** (lowercased; non-alphanumeric characters other than `_` are replaced;
  truncated to 32 characters). A name with no ASCII alphanumeric characters, or two files that collide on
  the same `work_id`, stops the run.
- Exit codes: 0 = success / 1 = rejected (a validation violation, or no phrase survived) / 2 = could not
  build or validate (usage error, unreadable file, `sudachipy` unavailable). Any non-zero code counts as
  rejection.

## Processing order

1. **Strip**: the leading title and author lines and the "about these symbols" frame (two dashed rules) /
   the colophon at the end of the work (from the `底本：` line onward) / annotation markers `［＃…］` /
   ruby markers `《…》` and `｜`. Ruby is **discarded, keeping the underlying kanji** (the reading is
   assigned by the tokenizer instead). Works where the frame of two dashed rules is not found (only one
   rule, or none) fall back to discarding everything up to the first blank line and print a warning to
   standard error.
2. **Split into sentences**: on `。` `！` `？` (including any trailing `」` `』` `）`) and at line breaks.
   Terminal punctuation and quotation marks `「」『』` are stripped.
3. **Split into clauses**: on the reading-point `、` and whitespace (including full-width). Each clause is
   assigned a reading.
4. **Group into phrases**: clauses whose reading is hiragana-only and **does not contain a historical kana
   character (`ゐ・ゑ・ゕ・ゖ`)** are merged with the following clause until the combined reading reaches
   `--min-reading` (merging stops as soon as the minimum is reached; a merge is never allowed to exceed
   `--max-reading`). A clause with an invalid reading, or one that alone exceeds `--max-reading`, joins no
   phrase. `text` keeps the original substring (a reading-point between merged clauses is retained).
5. **Retain**: phrases whose reading matches `^[ぁ-ゖー]+$` and contains no historical kana, and whose
   `text` (with reading-points and whitespace stripped) consists only of kanji (`U+4E00`–`U+9FFF`,
   `U+3400`–`U+4DBF`), hiragana, katakana, the long-vowel mark `ー`, or `々`. A phrase whose reading or
   `text` still contains a digit, a Latin letter, or another symbol is dropped (a real Sudachi installation
   can assign a katakana reading to digits and Latin letters, so relying on the reading alone would let such
   phrases through).
6. **Sample**: up to `--per-work` phrases per work. If fewer candidates remain, all of them are kept.

`phrase_id` is `<work_id>-<sentence index>-<phrase index>` (**determined entirely by the input**: the
sentence index is the running number of the sentence after normalization, the phrase index is the number
within that sentence; both are 1-based).

### How the seed is used

Selection is not driven by a PRNG: it ranks candidates by the smallest `SHA-256("<seed>:<phrase_id>")` and
keeps the top K, then restores the original order. The result is the same regardless of the Python version
or operating system. For the same input and the same `--seed`, the output is **byte-identical**; changing
`--seed` changes the selection whenever a work has more candidates than K. Output is UTF-8, LF line
endings, no BOM, and no separators between fields.

## Tokenizers

| `--tokenizer` | Behavior | Header |
|---|---|---|
| `sudachi` (default) | SudachiPy (split mode C). Converts katakana readings to hiragana. Words with an empty reading fall back to their surface form | `tokenizer` = `sudachipy-<version>`, `dict` = `sudachidict_core-<version>` (both read via `importlib.metadata`) |
| `table:<json>` | Longest-match substitution using `{"surface form": "hiragana reading"}`. Characters missing from the table are left unchanged (so that clause is dropped). **Used for dictionary-free testing** | `tokenizer` = `table`, `dict` = SHA-256 of the JSON file's bytes |

`sudachipy` is imported **only inside** `SudachiTokenizer` (never at module scope). On a machine without it
installed, selecting `--tokenizer sudachi` stops with exit code 2 and an explanation rather than a
traceback.

## Producing the corpus (finalizing the data set)

The loop workers used to build this tool have neither network nor `pip` access. Installing the dictionary
and running the production build is done separately by a human.

1. Select the source works and obtain them from Aozora Bunko (subject to approval). Place the `.txt` files
   (cp932) in a single directory. File names should use ASCII alphanumeric characters (they become
   `work_id`).
2. Create a virtual environment: `python3 -m venv .venv` (`.venv/` is not committed).
3. `pip install sudachipy sudachidict_core` → record the installed **versions** (the two matching lines
   from `pip freeze`) → pin them in `kit/corpus/requirements.txt`.
4. Run the production build: `python3 build_corpus.py --src <dir> --out data/phrases.jsonl --seed <N>
   --per-work <K> [--min-reading <N> --max-reading <M>]`.
5. Confirm `python3 build_corpus.py --validate data/phrases.jsonl` exits 0.
6. **Record the following six items in the experiment data log**: (1) `--seed` (2) `--per-work`
   (3) `--min-reading` (including the default value, if used) (4) `--max-reading` (same) (5) the
   **SHA-256** of `data/phrases.jsonl` (6) the tokenizer and dictionary versions from step 3. Once recorded,
   commit `data/phrases.jsonl`. Later runs (of the accuracy or reconstruction experiments) only read this
   file and no longer depend on the dictionary.

## Handling reading errors

Morphological analysis can assign an incorrect reading (proper nouns, homographs, and similar cases).

- **Accuracy evaluation** (which measures conversion events): a reading that differs from the source text
  is acceptable as long as it is a valid key sequence for hiragana input.
- **Reconstruction evaluation** (which measures leakage): the ground truth is the **typed reading**, not
  the source text. `text` is kept only for provenance and is never used as ground truth.

## Known limitations

- **Duplicate readings across phrases are possible** (different `phrase_id`, same `reading`). Duplicates
  are not removed; whether this matters depends on the downstream use of the closed-set attack.
- Symbols such as `，` (full-width comma), `（`, `〔`, `※` (the trace left after stripping an external-glyph
  annotation), and `…` remain inside a clause, so that clause is dropped. Clauses are only split on `、`
  and whitespace.
- Because a phrase is a run of clauses, a single long sentence with no clause break (exceeding
  `--max-reading`) never becomes a phrase.
- The **surface text** of archaic orthography and historical kana usage is left unchanged (passed through
  to the tokenizer as-is). However, **a phrase whose reading contains a historical kana character** (`ゐ`,
  `ゑ`, `ゕ`, `ゖ`) **is dropped**. How the reading comes out for works using archaic orthography has not
  been verified against a real dictionary (inspect the readings by eye when selecting works for the
  production run).

## Checks

- `bash ../check.sh` runs `build_corpus.py --selftest` (no dictionary needed; about one second).
- The selftest covers: removal of annotations, ruby, the title, the frame, and the colophon; exclusion of
  phrases whose reading still contains katakana, a digit, or a Latin letter, and of phrases outside the
  length range; clause merging; byte-identical output for the same seed; a change in the selection when the
  seed changes; and the positive and negative cases of `--validate`.
  It also checks: character-class filtering on the `text` side (a phrase is dropped even if a full-width
  digit is assigned a reading); exclusion of readings containing historical kana (`ゐ・ゑ・ゕ・ゖ`); that the
  trace of an external-glyph annotation (※) never becomes part of a phrase; truncation to the first blank
  line (with a warning) for a work with no frame; and that exit code 2 (rather than a traceback) is used
  when `--out` cannot be created, when a `--src` file cannot be read (`OSError`), or when the table JSON is
  too deeply nested (`RecursionError`) — each with a matching positive and negative case.
- The Sudachi code path has been exercised only against a **fake `sudachipy`** module (injected into
  `sys.modules`), covering the call shape, katakana-to-hiragana conversion, and version recording. Its
  behavior against the real dictionary can only be confirmed during the production run.
