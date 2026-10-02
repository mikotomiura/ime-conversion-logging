# Reference input field for Windows: (a) IMM compatibility layer ((b) direct TSF capture is described in the final section)

Records the IME messages received by this window (`WM_IME_STARTCOMPOSITION` / `WM_IME_COMPOSITION` /
`WM_IME_ENDCOMPOSITION` / `WM_IME_NOTIFY`) as content-free `gt/1` lines (`src: "win_imm"`), each carrying only
the **event kind, flags, lengths, and times**. This is a small Rust application. The contract for the log
format is specified in [`../formats/FORMATS.md`](../formats/FORMATS.md).

- **No text is ever captured** (see "No text is captured" below).
- **No interpretation is performed**: messages are written in the order received. Which messages actually
  arrive — for example, what a non-TSF compatibility layer delivers — is not documented in Microsoft's
  primary reference (*IME and TSF Accessibility in Windows XP*), so this is confirmed in a calibration pilot
  using a real input method engine. This component only writes what arrives; assigning meaning (assembling a
  conversion session) is left to downstream analysis.
- Because observation is confined to this process, no global hook, DLL injection, or cross-process TSF
  observation is used.

## Layout

| File | Role |
|---|---|
| `src/core.rs` | **The platform-independent core.** Turns an event (`Ev`) into a `gt/1` line (`line`), a header (`header`), a read-back validator (`check_line`), and unit conversions (`units_from_bytes` / `ticks_to_ns`). `cargo test` also runs on macOS. |
| `src/bin/refinput_imm.rs` | Windows only. Creates one window, records messages, and forwards them to `DefWindowProcW`. On any other platform it does nothing and exits with code 2. |

Dependencies are limited to **`windows = "0.58"` (`cfg(windows)` only)**. JSON output carries only integers and
enumerated names, so it is hand-written, and serde is not used.

## Build, run, self-test

```bash
cd papers/ime-observation/kit/refinput-windows
cargo test                                        # unit tests for the core (platform independent)
cargo build --release --bin refinput_imm
target/release/refinput_imm.exe --selftest --out /tmp/gt.jsonl   # no window; writes and reads back through the core for validation
python3 ../formats/validate_logs.py --kind gt /tmp/gt.jsonl      # shape check (exit 0 means the shape matches the contract)
target/release/refinput_imm.exe --out gt_win_imm.jsonl           # opens a window and records (ends when the window is closed)
```

- `--out <path>`: the output path. A recording run never overwrites an existing file (fails with exit code 1,
  so that a raw log cannot be destroyed). `--selftest` recreates the given path (or, with no `--out`, writes
  to a temporary file and removes it).
- `--selftest`: creates no window. Writes a representative sequence through the core — focus, `VK_PROCESSKEY`,
  start, opening and closing of the candidate window, update, commit, end — at fixed timestamps, then reads
  it back and checks that it matches what was written exactly (no byte-order mark, no CR, nothing missing)
  and that every line has the content-free `gt/1` shape (`check_line`). Passing this check does not show
  that a real input method engine would deliver the same messages.
- **Nothing is written to the console.** The result is reported only through the exit code:
  `0` success / `1` a runtime failure (window, write, an existing output path, or a self-test mismatch) /
  `2` usage error, or not running on Windows.
- Output is UTF-8, with no byte-order mark, LF line endings, one JSON object per line, and no spaces around
  separators. Each line is written to the operating system as it is produced, so a record survives even if
  the process later terminates.

## Messages recorded

Recorded only **after being passed to `DefWindowProcW`** (so the input method's default display is never
disturbed). The time recorded is the moment the message arrived: `t_wall_ms` (Unix milliseconds, wall clock)
and `t_mono_ns` (`QueryPerformanceCounter`, converted to nanoseconds; a monotonic clock counted from process
start). Both are recorded because the wall clock can jump, for example under NTP adjustment.

| Message | `ev` | Accompanying fields |
|---|---|---|
| `WM_IME_STARTCOMPOSITION` | `start_comp` | none |
| `WM_IME_COMPOSITION` | `comp` | `flags` (the GCS_* bits of `lParam`), `comp_len`, `result_len` |
| `WM_IME_ENDCOMPOSITION` | `end_comp` | none |
| `WM_IME_NOTIFY` | `notify` | `imn` (the IMN_* value of `wParam`) |
| `WM_KEYDOWN` | `key_down` | `vk` (the virtual-key code) |
| `WM_SETFOCUS` / `WM_KILLFOCUS` | `focus_in` / `focus_out` | none |

- `vk`: a key the input method is processing appears as `VK_PROCESSKEY` (`0xE5` = 229), and is **recorded as
  received**, with meaning left for later. A key the input method does not process appears as the literal
  key that was pressed, so this window must receive **only synthetic (scripted) input** — never a
  participant's own keystrokes. Being content-free covers not capturing text; it does not extend to the kind
  of key pressed.
- `WM_IME_COMPOSITION`'s `wParam` (which carries the most recent single character) is never read; only the
  flags in `lParam` are.
- `comp_len` / `result_len`: counts of **UTF-16 code units**, obtained through `ImmGetCompositionStringW`
  only when the corresponding bit (`GCS_COMPSTR` / `GCS_RESULTSTR`) is set in `lParam`; otherwise 0 (whether
  the bit was set is visible in `flags`). An IMM error (a negative return value) is likewise recorded as 0,
  with no distinction drawn among error causes.
- A message not listed above (such as `WM_SYSKEYDOWN` or a mouse message) is not recorded.

`core.rs`'s `Ev` type also defines variants used by direct TSF capture (`TsfStart` / `TsfUpdate` / `TsfEnd` /
`UiBegin` / `UiUpdate` / `UiEnd`); this binary (the IMM version) does not use them. The TSF version is
described in "(b) Direct TSF capture" below.

## No text is captured

- `ImmGetCompositionStringW` is called with a **null buffer and a length of 0**
  (`ImmGetCompositionStringW(himc, kind, None, 0)`). In this form the string itself is never obtained; only a
  **byte count** is returned (`bytes / 2` = a count of code units). This is the only call site.
- No variant of `Ev` carries a `String` / `&str` / `char`; a string cannot be represented at the type level.
- A message that carries a character (`WM_IME_CHAR` / `WM_CHAR`) is **never recorded**; it is left to the
  default processing. The message loop still calls `TranslateMessage`, since the input method receives keys
  through it, but no character is recorded there either.
- The guarantee of shape and the guarantee of meaning are distinct (see "Guarantee scope" in FORMATS.md).
  `validate_logs.py` confirms a line's shape — an enumeration, an integer, no unknown key — but cannot
  confirm that no text has been encoded into an integer field. That guarantee instead rests on this
  component's design: only the event kind, flags, lengths, and times are ever written.

## Constraints and open questions

- Behavior with a real input method engine is unconfirmed and is checked in the calibration pilot. Which
  `WM_IME_*` / `IMN_*` messages arrive, in what order, and with what flags, is neither interpreted nor
  filled in by this component.
- The window keeps the default input context (no `ImmAssociateContext` or similar call controls the input
  method).
- License: the code is Apache-2.0.

---

# (b) Direct TSF capture — `refinput_tsf`

Records this thread's own TSF composition start, update, and end, and the display of UI elements such as the
candidate window, **without going through the IMM compatibility layer**, as `gt/1` lines (`src: "win_tsf"`).
MS-IME is a TSF-based input method, so it is not intercepted by the translation layer. The format and the
content-free rules are the same as in (a); the contract is
[`../formats/FORMATS.md`](../formats/FORMATS.md).

**TSF capture strategy: path 1**

- Two implementation strategies were considered. In one (path 2), the reference input field becomes the
  owner of the TSF context — implementing `ITextStoreACP` — and receives `ITfContextOwnerCompositionSink`
  directly; this requires implementing an entire text store and is considerably larger. This component
  instead follows **path 1**: it hosts a TSF-aware control (RichEdit, `MSFTEDIT_CLASS`) and observes that
  control's TSF document from its own thread's `ITfThreadMgr`, without ever becoming the context's owner
  (`ITfContextOwnerCompositionSink` is not implemented).
- Path 1 was chosen for its smaller size (no text store is implemented). Within TSF, a text service's
  composition is constructed inside the application's own context, so it should be possible to enumerate it
  through that context's `ITfContextComposition::EnumCompositions` — a design hypothesis not yet confirmed
  against a primary source for MS-IME specifically, and checked in the calibration pilot.
- Whether path 1 observes notifications at the same granularity as `OnStartComposition` /
  `OnUpdateComposition` / `OnEndComposition` is unconfirmed and checked in the calibration pilot. Path 1's
  own notification is "an edit has finished" (`OnEndEdit`), from which start, update, and end are derived.
  Any discrepancy found bears on how the ground-truth capture path is described in the study's
  preregistration, and is corrected once measured.

## What is received, and what is written

| Received (this thread's own TSF) | `ev` | Accompanying fields |
|---|---|---|
| `ITfTextEditSink::OnEndEdit` (for the focused context) reports that a composition has **newly appeared** | `tsf_start`, immediately followed by `tsf_update` | `len` |
| ...an edit while a composition is present | `tsf_update` | `len` (the sum of `cch` across ranges; a count of UTF-16 code units) |
| ...a composition has **disappeared** | `tsf_end` | `result_len` (see below; sometimes omitted) |
| `ITfUIElementSink::BeginUIElement` / `UpdateUIElement` / `EndUIElement` | `ui_begin` / `ui_update` / `ui_end` | `ui_kind` |
| `ITfThreadMgrEventSink::OnSetFocus` (a document does, or does not, have focus) | `focus_in` / `focus_out` | none |
| `WM_KEYDOWN`, seen in the message loop | `key_down` | `vk` (as in (a); only synthetic input may be sent) |

- On this composition path, a key that the input method engine consumes does not necessarily reach
  `WM_KEYDOWN` at all, and the virtual-key code observed here can differ from what an IMM-based capture would
  report for the same keystroke, because the two mechanisms intercept input at different points. Since `vk`
  records the literal key that was pressed, this input field must only ever receive synthetic (scripted)
  input, never a person's own keystrokes.
- `ui_kind`: `candidate` if the element answers `ITfCandidateListUIElement`, `reading` if it answers
  `ITfReadingInformationUIElement`, `other` otherwise. `ui_update` and `ui_end` reuse the kind decided at
  `ui_begin`, since an element that has already ended cannot always be queried; an ID never seen at
  `ui_begin` is queried at `ui_update` but recorded as `other` at `ui_end`.
- **`tsf_update` is written on every edit**, even when the length is unchanged; interpretation is left
  downstream.
- **`tsf_end`'s `result_len`** is the length of the composition's text that remains in the document — the
  document end after the composition closed, minus the document end just before it that excludes the
  composition. It equals the composition's length on a commit, and zero on a cancellation. Downstream
  analysis distinguishes a commit from a cancellation using `result_len > 0`, so **omitting the field is
  counted as a cancellation**; it is omitted only when the document-end position could not be obtained. When
  an input method replaces the final characters at commit time, the remaining length can differ from the
  last observed length; deriving it from the difference in document ends keeps the remaining length correct.
  Confirmation against a real input method engine is part of the calibration pilot.
- When focus moves away from a context whose composition is still open, the registration that watches that
  context is kept alive and its composition tracker keeps running; only once the input method itself reports
  that composition's own end is `tsf_end` written and the registration released. A newly focused context is
  tracked in parallel, independently. If a single edit notification reports a commit immediately followed by
  a new composition (detected as a change in the start offset or the number of composition ranges while
  already active), that is written as a `tsf_end` (with `result_len` omitted, since the document end in
  between is unknown) followed immediately by `tsf_start`, so a commit and the composition that follows it
  are never merged into one update.
- The candidate window is **never hidden**: `BeginUIElement` returns `*pbShow = TRUE`, and the input method's
  own window is left visible.

## No text is captured (TSF version)

- A length is only `ITfRangeACP::GetExtent`'s **`cch`** (together with the document-end position); no API
  that reads a range's own text — such as `ITfRange`'s text-retrieval methods or `ITfContext`'s formatted-text
  retrieval — is ever called (`tsf.rs` / `refinput_tsf.rs` contain no such call; checked by acceptance
  testing).
- The read-only cookie (`ecReadOnly`) supplied by `OnEndEdit` is used only to obtain the **document-end
  position**, through `ITfContext::GetEnd`.
- `ITextStoreACP` is not implemented, since path 1 does not supply the document's own text to TSF from the
  application side.
- **TSF belonging to another process or another thread is never observed**: no global hook, `WinEvent` hook,
  or DLL injection is used. `ITfThreadMgr::Activate` only activates TSF for the calling thread.

## Layout (TSF version), build, self-test

| File | Role |
|---|---|
| `src/tsf.rs` | Windows only. A pure state machine (`CompTracker` / `UiKinds`, unit tested without COM) and the COM sinks (`OnEndEdit` / `OnSetFocus` / `UIElement`) and their registration (`Tsf::start` / `stop`). |
| `src/bin/refinput_tsf.rs` | Windows only. Creates a parent window and a RichEdit control and pumps messages after activating TSF. `--selftest` does not activate TSF. |

```bash
cd papers/ime-observation/kit/refinput-windows
cargo build --release --bins
target/release/refinput_tsf.exe --selftest --out /tmp/gt.jsonl   # does not activate TSF; writes and reads back through the state machine and the core for validation
python3 ../formats/validate_logs.py --kind gt /tmp/gt.jsonl
target/release/refinput_tsf.exe --out gt_win_tsf.jsonl           # opens a window and records (ends when the window is closed)
```

- Arguments, exit codes, and output format are the same as in (a) (`0` success / `1` failure / `2` usage
  error, or not running on Windows; a recording run never overwrites an existing output path).
- `--selftest`: feeds a representative observation sequence into `CompTracker` (one conversion — commit, then
  cancel — plus a candidate window, a reading window, and another kind of window), obtains the resulting
  events, writes them through the core, and reads them back for validation. Passing this check does not show
  that a real input method engine would deliver the same notifications.
- Dependencies: `windows = "0.58"` with the `implement`, `Win32_UI_TextServices`, `Win32_System_Com`, and
  `Win32_UI_Controls_RichEdit` features added. **`windows-core = "0.58"` is added as a direct dependency**,
  since the expansion of the `#[implement]` macro used for the TSF sink types refers to
  `::windows_core::...` by name; it is already present in Cargo.lock at the same 0.58 line as a transitive
  dependency of `windows`, so this adds no new crate.

## Constraints and open questions (TSF version)

- Behavior with a real input method engine is unconfirmed and is checked in the calibration pilot. In
  particular: (1) whether MS-IME's composition appears in the RichEdit control's TSF document and triggers
  `OnEndEdit`; (2) whether `EnumCompositions`'s ranges return a length through `ITfRangeACP`; (3) whether the
  candidate window's `BeginUIElement` arrives; (4) whether `tsf_end`'s `result_len` distinguishes a commit
  from a cancellation. If any of these fails to hold, or the distinction cannot be made, that is reported as
  a stop condition (path 1 is insufficient).
- TSF's UI-less mode (`ITfThreadMgrEx::ActivateEx`'s `TF_TMAE_UIELEMENTENABLEDONLY`) is **not used**, since it
  would suppress notifications from a text service that does not support UI elements. A plain `Activate` is
  used instead, so notifications are received only from text services that do support UI elements.
- Sending synthetic input is the responsibility of a separate driver component; this component only receives
  and records. With no focus on the window, `focus_in` never arrives, and no notification is received
  either.
- Whether the input method genuinely reports a composition's own end after focus has already moved away from
  its context is unconfirmed; the calibration pilot should check this specifically. If that end notification
  never arrives in that situation, a rule that closes the composition when focus leaves must be added on the
  analysis side instead.
