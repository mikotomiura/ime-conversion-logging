# Log format contracts (`gt/1` / `obs/1` / `plan/1` / `oracle/1` / `corpus/1`)

Every component of the kit operates on top of this contract. When adding or changing a format,
update the producing side and the validating side together.

## Common

- JSON Lines, UTF-8, one object per line. **The first line is the header.** Line endings are LF
  (CRLF is allowed).
- Timestamps are integers (`t_wall_ms` = Unix time in milliseconds; `t_mono_ns` = monotonic clock
  in nanoseconds).
- **No whitespace in separators** (the form `{"a":1,"b":2}`). In Python:
  `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`.
  The downstream validator locates rows by matching whitespace-free strings such as `"kind":"sent"`,
  so adding whitespace lets a violation slip through undetected or produces a false positive.
  `validate_logs.py` rejects any `gt` / `obs` line that has whitespace outside of string values
  (including `": "` or `", "`).
- **No escaping.** `validate_logs.py` rejects any `gt` / `obs` line containing a `\` (every allowed
  string value is `\`-free, so correctly produced output always passes). A value such as `"gt\/1"`,
  or an enumeration value spelled out with a backslash followed by the letter u and four hexadecimal
  digits per character, decodes to a value that matches the enumeration, but a validator that
  searches the raw string for a row lets it slip through. Swift's `JSONEncoder` escapes `/` as `\/`
  by default, so set `outputFormatting` to `.withoutEscapingSlashes`. The Python `json.dumps` call
  above already produces valid output as is.
- **No BOM** (a UTF-8 BOM is not valid JSON and is rejected; .NET's `Encoding.UTF8` and
  PowerShell 5's `utf8` encoding add a BOM by default, so this needs attention).
- Only JSON integers are allowed (decimals, exponential notation, `NaN`, and `Infinity` are
  rejected). Ranges: in `obs`, `t` and `dwell_ms` range over 0 to 2^64−1 (an unsigned 64-bit
  integer) and `nav` ranges over 0 to 2^32−1 (an unsigned 32-bit integer); in `gt`, the accompanying
  fields and the timestamps range over −2^63 to 2^64−1. These ranges exist to cap how much payload
  can be packed into a single field, at most 8 bytes (the meaning of a value within its range is not
  checked — see "Scope of guarantee" below).
- The same key does not appear twice on one line (different validators can pick different values
  for a duplicate key, which could be used to hide payload).

## `gt/1` — the raw ground-truth log emitted by the reference input field

- Header: `{"kind":"header","format":"gt/1","src":<"mac_nstic"|"win_imm"|"win_tsf">,"app_version":<str>,"os":<"macos"|"windows">}`
- Event: `{"kind":"ev","t_wall_ms":int,"t_mono_ns":int,"ev":<enum>, ...}`
- `ev` and its accompanying fields (**accompanying fields are integers or booleans only**, except
  that `cmd` and `ui_kind` are the only string-enum fields; every field in the table below is
  currently an integer, and no boolean position exists yet):

| src | ev | accompanying fields |
|---|---|---|
| common | `key_down` | `vk` (the OS key code) |
| common | `focus_in` / `focus_out` | none |
| `mac_nstic` | `set_marked` | `len`, `sel_loc`, `sel_len`, `rep_loc`?, `rep_len`? |
| `mac_nstic` | `insert_text` | `len`, `rep_loc`?, `rep_len`? |
| `mac_nstic` | `unmark` / `first_rect` | none |
| `mac_nstic` | `do_command` | `cmd` ∈ {`insertNewline:`, `cancelOperation:`, `deleteBackward:`, `deleteForward:`, `moveLeft:`, `moveRight:`, `moveUp:`, `moveDown:`, `insertTab:`, `insertBacktab:`, `noop:`, `other`} |
| `win_imm` | `start_comp` / `end_comp` | none |
| `win_imm` | `comp` | `flags` (the GCS_* bits), `comp_len`, `result_len` |
| `win_imm` | `notify` | `imn` (the IMN_* value) |
| `win_tsf` | `tsf_start` / `tsf_end` | `tsf_end` only: `result_len`? |
| `win_tsf` | `tsf_update` | `len` |
| `win_tsf` | `ui_begin` / `ui_update` / `ui_end` | `ui_kind` ∈ {`candidate`, `reading`, `other`} |

All lengths are counted in **UTF-16 code units**. `?` marks an optional field. A common `ev` can be
emitted under any `src`.

An `ev` not listed in the table above is rejected as an `ev` that does not occur for that `src`.

**Content-free rule**: the only keys that may hold a string value are
`kind` / `format` / `src` / `app_version` / `os` / `ev` / `cmd` / `ui_kind`. `app_version` follows
the regular expression `^[0-9A-Za-z.+-]{1,32}$`; every other string field must be one of the
enumerated values above. **Unknown keys are rejected**, and a string, array, or object in an
integer position is rejected (booleans and `null` are rejected too, since neither is an integer).

## `obs/1` — the standalone logger's output

This is the observation log format produced by the standalone logger.

- Header: `{"type":"meta","format":"obs/1","os":<"macos"|"windows">,"logger_version":<str>,"manifest_sha256":<64 hex>,"capabilities":<object>}`
  - `logger_version` follows the same pattern as `app_version` (`^[0-9A-Za-z.+-]{1,32}$`).
    `manifest_sha256` is 64 lowercase hexadecimal digits.
  - `capabilities` has the following shape:
    `{"unconditional":[<name>…],"threshold_gated":[<name>…],"config_gated":[<name>…]}`.
    **All three keys are required**, and each value is an **array of strings** (which may be
    empty). Each name identifies a row type, drawn from the closed enumeration
    **`key` / `ime_shadow`** (the standalone logger emits only these two row types). Across the
    three arrays, **each name occurs at most once** (a duplicate is rejected; two names in total at
    most). Leaving the name open to a regular expression would let an arbitrary sequence of
    romanized words be packed in (an escape hatch for payload), so it is a closed enumeration
    instead.
- `{"type":"key","t":int,"category":<14 kinds>,"press":bool}` — `category` is one of `alphabetic` /
  `numeric` / `punctuation` / `sentence_punctuation` / `tab` / `backspace` / `delete` / `space` /
  `enter` / `navigation` / `modifier` / `function` / `escape` / `other`
- `{"type":"ime_shadow","t":int,"dwell_ms":int,"nav":int,"abandon":bool}`
- Any `type` other than the above (including a second `meta` line), an unknown key, or a string
  outside its enumeration is rejected.

## `plan/1` — the generator's output

- Header: `{"kind":"header","format":"plan/1","os":…,"seed":int,"generator_version":str,"config_sha256":<64 hex>,"recipes_sha256":<64 hex>,"corpus_sha256":<64 hex>}`
- Block: `{"kind":"block","block_id":int,"speed":"slow"|"mid"|"fast","reset_ime":bool,"marker":[<keyname>…],"marker_gap_ms":int}`
- Trial: `{"kind":"trial","trial_id":str,"block_id":int,"scenario":"S1"…"S15","speed":"slow"|"mid"|"fast","gap_before_ms":int,"think_ms":int,"phrase_id":str,"reading_len":int,"steps":[{"key":<keyname>,"at_ms":int,"down":bool}…],"intent":{…}}`
- `gap_before_ms` is the wait from the end of the previous trial to the start of this one;
  `marker_gap_ms` is the interval between marker keys. The emitter waits only by these two values
  (it does not decide the wait itself).
- **Blocks are uniform in speed** (one block has one speed; the block row's `speed` matches the
  `speed` of every trial within it).
- `intent`'s keys are `commit` (`enter` / `digit` / `mouse` / `none`), `cand_index`, `nav_count`,
  `abandon`, `predictive`, `ime_toggle_mid`, `focus_switch_mid`, and `expect_session`.
- A plan is a stimulus (a public-domain phrase's reading), so it is not subject to the content-free
  rule.

### `plan/1` types

- Header: `os` ∈ {`macos`, `windows`}; `seed` is an integer from 0 to 2^63−1; `generator_version`
  follows `^[0-9A-Za-z.+-]{1,32}$`; `config_sha256` / `recipes_sha256` / `corpus_sha256` are the
  SHA-256 (64 lowercase hexadecimal digits) of the **byte content** of the configuration, recipes,
  and corpus files, respectively. This ties each plan to the exact version of the procedure and
  stimuli it was built from, on the assumption that the recipes are revised after the pilot.
- Block: `block_id` is a 0-based sequence number (in file order); `reset_ime` is a boolean; `marker`
  is an array of `keyname` (may be empty, meaning no marker is sent); `marker_gap_ms` is a
  non-negative integer.
- Trial: `trial_id` matches `^t[0-9]{5,}$` (a file-wide, 1-based, zero-padded sequence number, e.g.
  `t00001`); `block_id` is the `block_id` of the **immediately preceding block row** (a trial
  follows that block row, and every trial up to the next block row belongs to that block);
  `gap_before_ms` and `think_ms` are non-negative integers; `phrase_id` follows the `corpus/1`
  format; `reading_len` is the character count of the **phrase's** reading (at least 1; hiragana
  falls within the BMP, so this equals the UTF-16 code unit count). This is not necessarily the
  reading actually typed (S10 types only the first half, and S12 uses a synthesized kana string) —
  the reading actually typed is obtained from the generator's `typed_reading(...)`.
- `steps` elements: `key` is a `keyname`; `at_ms` is a non-negative integer relative to the start of
  the trial, non-decreasing in array order; `down` is a boolean (press = `true`, release = `false`;
  the press and release of the same key are paired).
- `intent` is an object holding **all eight** of the following keys, and no unknown key. The value
  for each scenario is determined by `kit/gen/recipes.json`.

  | key | type | meaning |
  |---|---|---|
  | `commit` | `enter` / `digit` / `mouse` / `none` | The means of committing the conversion. A trial that does not commit (cancellation, clearing everything, etc.) uses `none` |
  | `cand_index` | non-negative integer or `null` | The 0-based index of the committed candidate. `null` for a trial that commits without selecting a candidate, and for a trial that does not commit. Also `null` for a trial that selects a predictive candidate (`predictive` is true) — predictive-candidate indices are not counted |
  | `nav_count` | non-negative integer | The number of arrow-key presses sent during conversion (0 for a trial that sends none) |
  | `abandon` | boolean | Intent to discard the conversion without committing |
  | `predictive` | boolean | Intent to use a predictive candidate |
  | `ime_toggle_mid` | boolean | Intent to turn the IME off and back on mid-conversion |
  | `focus_switch_mid` | boolean | Intent to switch the foreground application mid-conversion |
  | `expect_session` | boolean | Whether the ground-truth side is expected to open a conversion session, under the cross-platform composition definition rather than any OS-specific one |

## `oracle/1` — the emitter's output

- Header: `{"kind":"header","format":"oracle/1","plan_sha256":…,"os":…,"emitter_version":…,"backend":<"dryrun"|"macos"|"windows">}`
- `{"kind":"sent","trial_id":…,"step":int,"key":<keyname>,"down":bool,"t_wall_ms":int,"t_mono_ns":int}`
- `trial_begin` / `trial_end` / `block_begin` / `block_end` (a `trial_id` or `block_id`, plus the
  two timestamps)
- `{"kind":"marker","block_id":int,"seq":int,…the two timestamps}`
- `{"kind":"reset","block_id":int,"exit_code":int,…the two timestamps}`

### `oracle/1` types

- Header: `plan_sha256` is the SHA-256 (64 lowercase hexadecimal digits) of the plan file's **byte
  content**; `os` ∈ {`macos`, `windows`}; `emitter_version` follows `^[0-9A-Za-z.+-]{1,32}$`.
- The "two timestamps" are the integers `t_wall_ms` (Unix time in ms) and `t_mono_ns` (monotonic
  clock in ns). **Every row other than the header carries both.**
- Row shapes (these are the only keys):
  - `{"kind":"block_begin","block_id":int,"t_wall_ms":int,"t_mono_ns":int}` — `block_end` has the
    same shape
  - `{"kind":"trial_begin","trial_id":str,"t_wall_ms":int,"t_mono_ns":int}` — `trial_end` has the
    same shape
  - `{"kind":"sent","trial_id":str,"step":int,"key":<keyname>,"down":bool,"t_wall_ms":int,"t_mono_ns":int}`
    — `step` is the 0-based index into the plan's `steps`
  - `{"kind":"marker","block_id":int,"seq":int,"t_wall_ms":int,"t_mono_ns":int}` — one row per
    **press** of a marker key. `seq` is the 0-based index into that block's `marker` array (the
    release is sent but is not turned into a row)
  - `{"kind":"reset","block_id":int,"exit_code":int,"t_wall_ms":int,"t_mono_ns":int}`
- **Timing points**: `sent` and `marker` are taken **immediately before dispatch**; `trial_begin` is
  taken **after** the `gap_before_ms` wait, immediately before the first dispatch (i.e., the
  inter-trial wait is not included in the trial's own interval); `trial_end` is taken after the
  last dispatch; `block_begin` is taken before sending any marker, and `block_end` after the last
  trial; `reset` is taken after the reset command finishes.
- **Row order**: header → for each block: (`reset`, when `reset_ime` is true and the reset command
  was run) → `block_begin` → `marker`… → for each trial: (`trial_begin` → `sent`… → `trial_end`) →
  `block_end`. A reset that the plan asks for but that is not run (no reset command is configured,
  or the run was not authorised to change the machine's state) leaves no `reset` row; the run
  manifest records it as skipped, with the reason.
- `trial_id` / `block_id` carry the same values as in the plan.

## `corpus/1` — phrases and readings

- Header: `{"kind":"header","format":"corpus/1","source":"aozora","tokenizer":str,"dict":str}`
- `{"kind":"phrase","phrase_id":str,"work_id":str,"text":str,"reading":str,"reading_len":int}`
- `reading` is **hiragana only** (`^[ぁ-ゖー]+$`).

### `corpus/1` types

- Header: `tokenizer` and `dict` each combine a name and a version into one string
  (`^[0-9A-Za-z._+-]{1,64}$`; e.g. `sudachipy-0.6.8`).
- `work_id` matches `^[a-z0-9][a-z0-9_]{0,31}$` (**starts with a lowercase character**; derived
  deterministically from the source work's file name).
- `phrase_id` is `<work_id>-<sentence number>-<phrase number>` (numbers are 1-based decimal) =
  `^[a-z0-9][a-z0-9_]{0,31}-[0-9]+-[0-9]+$`. A synthesized kana string (filled in by the generator)
  matches `^kana-[0-9]+$`. Both forms **start with a lowercase character** — the export-time check
  rejects the shape of a participant identifier (an uppercase `P` followed by three digits), so
  this format is chosen to avoid matching it.
- `reading_len` is the character count of `reading` (at least 1). `text` is the public-domain
  sentence (not subject to the content-free rule).

## The `keyname` enumeration

`a`–`z`, `0`–`9`, `space`, `enter`, `esc`, `backspace`, `tab`, `left`, `right`, `up`, `down`,
`minus`, `comma`, `period`, `ime_on`, `ime_off`, `f13`–`f19`, `shift`, `mouse_click`, `focus_away`,
`focus_back` (the last two are not real keys; they are pseudo-keys that the emitter backend uses to
switch the foreground application by OS-specific means, for S15).

## Validation

- `kit/formats/validate_logs.py --kind gt|obs FILE...` — prints violations with line numbers and
  exits 1; exits 0 if there are none (exit 2 means validation could not run at all. **Any exit code
  other than 0 counts as failing.** If there is at least one violation, the exit code is 1 even when
  an unreadable file is also present). **A violation's description never includes a value** (so
  payload content never leaks through the output, even if present). A key name is shown only when
  it is one of the names listed in this document.
- **Scope of guarantee**: what the validator mechanically guarantees is a row's **shape** — string
  values fit an enumeration or a regular expression, there are no unknown keys, no escaping, and no
  duplicate keys, and only an in-range integer occupies an integer position. **The meaning of a
  value is not guaranteed**: payload encoded into an integer field (`vk`, a length, a timestamp), a
  version string (`app_version` / `logger_version`), or `manifest_sha256` cannot be detected this
  way. Keeping the log content-free is the producing side's design responsibility (writing only
  kind, length, and time); the validator only confirms that this shape holds.
- Only `gt` and `obs` are mechanically validated (the formats that require content-freedom).
  Validation of `plan` / `oracle` / `corpus` lives in the producing component's own selftest.
