# Reference input field for macOS

A minimal Cocoa application (one Swift file, no package dependencies) whose only view implements
`NSTextInputClient`. Every call the input method makes on that protocol is written as a content-free `gt/1`
line (`src: "mac_nstic"`). The contract for the log format is specified in
[`../formats/FORMATS.md`](../formats/FORMATS.md).

- **The text passed to the protocol is never written to the log.** The recording function accepts an
  enumeration and integers only; no overload accepts a string. The byte-level writer is private to the `Sink`
  type, and the only functions that write to the log are `record` and the parameterless header writer, so no
  path in the source carries a string to the log. What is recorded is the kind of call, lengths (UTF-16 code
  units), ranges, and times.
- **The recorded key codes still reflect what was typed.** The `vk` values of the `key_down` lines form the
  sequence of physical keys pressed, from which the typed keys (for romaji input, the reading) can be
  reconstructed. A log of a person typing is therefore to be handled as keystroke content. The claim above is
  limited to not writing the text.
- **The text under composition is kept in memory only.** The input method has to be answered truthfully
  (marked range, selected range, substring queries), so the view holds the text being composed. That text is
  used to answer the protocol and to draw the view. It is not logged.
- **Committed text is not retained.** Unlike an ordinary text field, the view empties its in-memory text and
  selection as soon as no marked text remains (after `insertText` or `unmarkText`). The input method is
  therefore given no left context from the text of an earlier trial. Trials are not independent in every
  respect: the input method keeps its own state, including what it has learned, across trials. The recorded
  lines (for example the `len` of an `insert_text`) are unaffected.
- **No interpretation is performed.** Calls are written in the order received. The protocol documentation
  does not state the order in which the input method calls `unmarkText()` and `insertText`, and it does not
  define what counts as the ground truth of a conversion; that definition is fixed by a calibration pilot with
  a real input method, not by this component.

## Recording function

The function through which every log line passes:

```
記録関数の名前: `record`
```

Its declaration takes `ev` (an enumeration), an optional `cmd` (an enumeration), and optional integers. Its
body emits `cmd` from the enumeration's raw value, so a selector name that is not in the contract cannot be
written.

## Build, run, self-test

```bash
bash papers/ime-observation/kit/refinput-macos/build.sh        # produces .build/refinput-macos and .build/RefInput.app
.build/refinput-macos --selftest --out /tmp/gt.jsonl           # no window; see below
python3 papers/ime-observation/kit/formats/validate_logs.py --kind gt /tmp/gt.jsonl
.build/refinput-macos --out gt_mac_nstic.jsonl                 # opens a window and records until it is closed
```

- `build.sh` also wraps the executable in `.build/RefInput.app` with the bundle identifier
  `local.ime-observation.refinput` (override it with `REFINPUT_BUNDLE_ID`). The emitter brings the field to the
  front with `tell application id "<bundle id>"` and reads the front application's bundle identifier back, which a
  bare executable does not have; a measurement therefore runs `.build/RefInput.app/Contents/MacOS/refinput-macos`
  and names the bundle identifier as `focus_app`.
- `--out <path>`: the output path. The file is created exclusively (`O_EXCL`, no symbolic links followed,
  mode 0600). An existing path of any kind, file or directory, is refused with exit code 1, so a raw log
  cannot be destroyed. `--selftest` follows the same rule; without `--out` it writes to a temporary file that
  it created itself and removes it.
- If a write to the log fails, a fixed message (containing no content) is printed to standard error once, and
  the process exits with code 1 when it terminates.
- The application has no menu, so Cmd+Q and Cmd+W do not work. To end a run, click the window's close button
  (closing the last window terminates the application), or terminate the process.
- Mouse events (`mouseDown`, `mouseDragged`, `mouseUp`) are offered to the input method first and passed on to
  the view only if it does not consume them.
- `--selftest`: creates no window. It calls the protocol methods directly: a marked text of four characters,
  an insertion that replaces it, an emptying of the marked text, a marked text containing a supplementary-plane
  character, an attributed string, `unmarkText()`, `firstRect`, out-of-range and `NSNotFound` ranges,
  partial replacement ranges, and several `doCommand(by:)` calls (selectors from the contract and one outside
  it). After each step it compares `hasMarkedText`, `markedRange`, `selectedRange` and the length of
  `attributedSubstring(forProposedRange:actualRange:)` with the expected values, including that the text is
  empty after every commit (no string is written anywhere). It then reads the output back and checks that (a)
  each recorded `len` equals the UTF-16 length of what was passed, (b) the output is plain ASCII and contains
  none of the strings that were passed, (c) a selector outside the contract is recorded as `other`, and that
  the event order, a repeated focus state recording nothing, and the header are as expected. Exit code `0`
  means all checks passed, `1` a failure.
- Nothing is written to standard output. Failures are described on standard error. Exit code `2` is a usage
  error or a missing `swiftc`.
- Output is UTF-8 with no byte-order mark, LF line endings, one JSON object per line, and no spaces around
  separators. Each line is written to the operating system as it is produced.

## Calls recorded

Times are `t_wall_ms` (Unix time in milliseconds) and `t_mono_ns` (`clock_gettime_nsec_np(CLOCK_UPTIME_RAW)`).
Both are recorded because the wall clock can jump.

| Call | `ev` | Accompanying fields |
|---|---|---|
| `setMarkedText(_:selectedRange:replacementRange:)` | `set_marked` | `len`, `sel_loc`, `sel_len`, `rep_loc`?, `rep_len`? |
| `insertText(_:replacementRange:)` | `insert_text` | `len`, `rep_loc`?, `rep_len`? |
| `unmarkText()` | `unmark` | none |
| `firstRect(forCharacterRange:actualRange:)` | `first_rect` | none |
| `doCommand(by:)` | `do_command` | `cmd` (a selector from the contract, otherwise `other`) |
| `keyDown(with:)` | `key_down` | `vk` (`NSEvent.keyCode`) |
| the window becoming or ceasing to be the key window | `focus_in` / `focus_out` | none (recorded on a change of state only) |

`?` marks an optional field. `rep_loc` and `rep_len` are left out when the replacement range is `NSNotFound`.

The answers to `hasMarkedText`, `markedRange`, `selectedRange`, `attributedSubstring(forProposedRange:actualRange:)`,
`validAttributesForMarkedText`, and `characterIndex(for:)` are not recorded: they are queries and change
nothing.

## Calls not recorded

- The protocol has no method that announces the display of a candidate window. `firstRect` is a request for a
  position, not a notification of display.
- The position returned from `firstRect` is not written (the contract has no field for it).
