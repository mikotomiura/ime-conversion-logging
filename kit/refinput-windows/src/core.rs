//! Platform-independent core of the reference input field: turns an IME message or call into
//! one line of the `gt/1` format.
//!
//! Content-free by construction: no variant of `Ev` carries a string type (`String`, `&str`,
//! or `char`). Only the event kind, flags, lengths, and times are recorded; neither the
//! committed nor the uncommitted text can be represented at the type level. The shape of
//! `gt/1` is specified in `kit/formats/FORMATS.md` and checked by
//! `kit/formats/validate_logs.py --kind gt`. JSON is hand-written, since only integers and
//! enumerated names ever appear and no escaping is required. Lines contain no spaces, no
//! escape sequences, and no byte-order mark, following the shared conventions in FORMATS.md.

/// The `format` field of a `gt/1` header.
pub const GT_FORMAT: &str = "gt/1";

/// Capture path of the reference input field (`gt/1`'s `src`). Only the two Windows paths are
/// listed here; macOS is captured by a separate component.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Src {
    /// IMM compatibility layer (this window's own `WM_IME_*` / `IMN_*` messages).
    WinImm,
    /// Direct TSF capture.
    WinTsf,
}

impl Src {
    fn name(self) -> &'static str {
        match self {
            Src::WinImm => "win_imm",
            Src::WinTsf => "win_tsf",
        }
    }
}

/// Kind of UI element, such as a candidate list or a reading window (`ui_kind`). An
/// enumeration only.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UiKind {
    Candidate,
    Reading,
    Other,
}

impl UiKind {
    fn name(self) -> &'static str {
        match self {
            UiKind::Candidate => "candidate",
            UiKind::Reading => "reading",
            UiKind::Other => "other",
        }
    }
}

/// An event to be recorded. No variant carries a string; each holds only integers,
/// enumerations, or booleans. Every length is a count of UTF-16 code units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ev {
    /// `WM_IME_STARTCOMPOSITION`
    StartComp,
    /// `WM_IME_COMPOSITION`: the GCS_* bits of `lParam`, and the lengths of `GCS_COMPSTR` and
    /// `GCS_RESULTSTR`.
    Comp {
        flags: u32,
        comp_len: u32,
        result_len: u32,
    },
    /// `WM_IME_ENDCOMPOSITION`
    EndComp,
    /// `WM_IME_NOTIFY`'s `wParam` (an IMN_* value)
    Notify { imn: u32 },
    /// The virtual-key code of `WM_KEYDOWN`. While the input method is consuming the key this
    /// is `VK_PROCESSKEY` (0xE5); it is recorded as received, without substitution.
    KeyDown { vk: u32 },
    /// `WM_SETFOCUS`
    FocusIn,
    /// `WM_KILLFOCUS`
    FocusOut,
    /// `OnStartComposition`, from the direct-TSF capture path.
    TsfStart,
    /// `OnUpdateComposition`, from the direct-TSF capture path (length is the range's `cch`
    /// only).
    TsfUpdate { len: u32 },
    /// `OnEndComposition`, from the direct-TSF capture path.
    TsfEnd { result_len: Option<u32> },
    /// `ITfUIElementSink::BeginUIElement`, from the direct-TSF capture path.
    UiBegin { kind: UiKind },
    /// `ITfUIElementSink::UpdateUIElement`, from the direct-TSF capture path.
    UiUpdate { kind: UiKind },
    /// `ITfUIElementSink::EndUIElement`, from the direct-TSF capture path.
    UiEnd { kind: UiKind },
}

/// One `gt/1` header line, with no trailing newline. `os` is always `windows`.
///
/// The caller should pass an `app_version` that already matches `^[0-9A-Za-z.+-]{1,32}$`, since
/// the validator rejects anything else. To avoid emitting a JSON escape sequence, any character
/// outside that range is replaced with `_` here.
pub fn header(src: Src, app_version: &str) -> String {
    let mut ver: String = app_version
        .chars()
        .take(32)
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '+' | '-') {
                c
            } else {
                '_'
            }
        })
        .collect();
    if ver.is_empty() {
        ver.push('0');
    }
    format!(
        "{{\"kind\":\"header\",\"format\":\"{}\",\"src\":\"{}\",\"app_version\":\"{}\",\"os\":\"windows\"}}",
        GT_FORMAT,
        src.name(),
        ver
    )
}

/// One `gt/1` event line (no trailing newline).
pub fn line(ev: Ev, t_wall_ms: u64, t_mono_ns: u64) -> String {
    let (name, tail) = match ev {
        Ev::StartComp => ("start_comp", String::new()),
        Ev::Comp {
            flags,
            comp_len,
            result_len,
        } => (
            "comp",
            format!(",\"flags\":{flags},\"comp_len\":{comp_len},\"result_len\":{result_len}"),
        ),
        Ev::EndComp => ("end_comp", String::new()),
        Ev::Notify { imn } => ("notify", format!(",\"imn\":{imn}")),
        Ev::KeyDown { vk } => ("key_down", format!(",\"vk\":{vk}")),
        Ev::FocusIn => ("focus_in", String::new()),
        Ev::FocusOut => ("focus_out", String::new()),
        Ev::TsfStart => ("tsf_start", String::new()),
        Ev::TsfUpdate { len } => ("tsf_update", format!(",\"len\":{len}")),
        Ev::TsfEnd { result_len: None } => ("tsf_end", String::new()),
        Ev::TsfEnd {
            result_len: Some(n),
        } => ("tsf_end", format!(",\"result_len\":{n}")),
        Ev::UiBegin { kind } => ("ui_begin", format!(",\"ui_kind\":\"{}\"", kind.name())),
        Ev::UiUpdate { kind } => ("ui_update", format!(",\"ui_kind\":\"{}\"", kind.name())),
        Ev::UiEnd { kind } => ("ui_end", format!(",\"ui_kind\":\"{}\"", kind.name())),
    };
    format!("{{\"kind\":\"ev\",\"t_wall_ms\":{t_wall_ms},\"t_mono_ns\":{t_mono_ns},\"ev\":\"{name}\"{tail}}}")
}

/// Keys allowed to appear in a `gt/1` line (only the names listed in the `gt/1` table of
/// FORMATS.md).
const KEYS: &[&str] = &[
    "kind",
    "format",
    "src",
    "app_version",
    "os",
    "t_wall_ms",
    "t_mono_ns",
    "ev",
    "flags",
    "comp_len",
    "result_len",
    "imn",
    "vk",
    "len",
    "ui_kind",
];

/// Enumerated strings allowed as a value (`app_version` is checked separately, against the
/// version-string shape).
const ENUM_VALUES: &[&str] = &[
    "header",
    "ev",
    "gt/1",
    "win_imm",
    "win_tsf",
    "windows",
    "start_comp",
    "comp",
    "end_comp",
    "notify",
    "key_down",
    "focus_in",
    "focus_out",
    "tsf_start",
    "tsf_update",
    "tsf_end",
    "ui_begin",
    "ui_update",
    "ui_end",
    "candidate",
    "reading",
    "other",
];

/// Checks whether a line read back from disk has the content-free `gt/1` shape (a small,
/// strict checker used by the self-test).
///
/// A line is accepted only if it is a single-line JSON object that is pure ASCII, contains no
/// space and no escape sequence, whose keys are drawn from `KEYS` without duplication, and
/// whose values are either unsigned 64-bit integers or one of the enumerated strings in
/// `ENUM_VALUES` (`app_version` instead being checked against
/// `^[0-9A-Za-z.+-]{1,32}$`). The authoritative shape check is
/// `kit/formats/validate_logs.py`, which is also run at acceptance time. The returned error
/// reason never includes a value taken from the line.
pub fn check_line(s: &str) -> Result<(), &'static str> {
    if !s.is_ascii() {
        return Err("non-ASCII byte");
    }
    if s.len() < 2 || !s.starts_with('{') || !s.ends_with('}') {
        return Err("not a one-line object");
    }
    let mut rest = &s[1..s.len() - 1];
    let mut seen: Vec<&str> = Vec::new();
    loop {
        let (key, after_key) = take_string(rest)?;
        if !KEYS.contains(&key) {
            return Err("unknown key");
        }
        if seen.contains(&key) {
            return Err("duplicate key");
        }
        seen.push(key);
        let after_colon = after_key.strip_prefix(':').ok_or("missing colon")?;
        if after_colon.starts_with('"') {
            let (val, after_val) = take_string(after_colon)?;
            let ok = if key == "app_version" {
                (1..=32).contains(&val.len())
                    && val
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'+' | b'-'))
            } else {
                ENUM_VALUES.contains(&val)
            };
            if !ok {
                return Err("string value outside the enumeration");
            }
            rest = after_val;
        } else {
            let n = after_colon.bytes().take_while(u8::is_ascii_digit).count();
            if n == 0 || after_colon[..n].parse::<u64>().is_err() {
                return Err("not an unsigned integer");
            }
            rest = &after_colon[n..];
        }
        match rest.strip_prefix(',') {
            Some(next) => rest = next,
            None if rest.is_empty() => return Ok(()),
            None => return Err("unexpected text after a value"),
        }
    }
}

/// Takes the leading `"..."` (containing no `"`, `\`, or control character) and returns the
/// remainder.
fn take_string(s: &str) -> Result<(&str, &str), &'static str> {
    let body = s.strip_prefix('"').ok_or("expected a string")?;
    let end = body.find('"').ok_or("unterminated string")?;
    let val = &body[..end];
    if val.bytes().any(|c| c == b'\\' || c < 0x20 || c == 0x7f) {
        return Err("escape or control character");
    }
    Ok((val, &body[end + 1..]))
}

/// Converts the byte count returned by `ImmGetCompositionStringW` into a count of UTF-16 code
/// units (`bytes / 2`).
///
/// A negative value is an IMM error (`IMM_ERROR_NODATA` = -1, `IMM_ERROR_GENERAL` = -2),
/// meaning no string was obtained. Since a length is a non-negative count of code units, an
/// error is recorded as 0; no distinction is drawn among error causes, and none is inferred.
pub fn units_from_bytes(bytes: i32) -> u32 {
    if bytes <= 0 {
        0
    } else {
        (bytes as u32) / 2
    }
}

/// Converts a `QueryPerformanceCounter` reading (ticks) to nanoseconds. `freq` is ticks per
/// second (0 yields 0). The computation is carried out in 128 bits to avoid intermediate
/// overflow: even at 10 MHz, `ticks * 10^9` can exceed 64 bits.
pub fn ticks_to_ns(ticks: i64, freq: i64) -> u64 {
    if ticks <= 0 || freq <= 0 {
        return 0;
    }
    let ns = (ticks as u128) * 1_000_000_000u128 / (freq as u128);
    u64::try_from(ns).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn l(ev: Ev) -> String {
        line(ev, 1_700_000_000_123, 456_789)
    }

    const T: &str = "\"t_wall_ms\":1700000000123,\"t_mono_ns\":456789";

    #[test]
    fn header_has_the_contract_shape() {
        assert_eq!(
            header(Src::WinImm, "0.1.0"),
            r#"{"kind":"header","format":"gt/1","src":"win_imm","app_version":"0.1.0","os":"windows"}"#
        );
        assert_eq!(
            header(Src::WinTsf, "0.1.0+x-1"),
            r#"{"kind":"header","format":"gt/1","src":"win_tsf","app_version":"0.1.0+x-1","os":"windows"}"#
        );
    }

    #[test]
    fn header_sanitizes_the_version_instead_of_escaping() {
        // A character the validator would reject is replaced with `_` instead of being escaped.
        let h = header(Src::WinImm, "a b\"c\\d");
        assert!(h.contains("\"app_version\":\"a_b_c_d\""), "{h}");
        assert!(!h.contains('\\'), "{h}");
        // Empty, and overly long, version strings.
        assert!(header(Src::WinImm, "").contains("\"app_version\":\"0\""));
        let long = "v".repeat(40);
        assert!(
            header(Src::WinImm, &long).contains(&format!("\"app_version\":\"{}\"", "v".repeat(32)))
        );
        // A multi-byte character is also replaced with `_`, one substitution per character.
        assert!(header(Src::WinImm, "あい").contains("\"app_version\":\"__\""));
    }

    #[test]
    fn every_variant_matches_the_expected_json() {
        let cases: Vec<(Ev, String)> = vec![
            (
                Ev::StartComp,
                format!(r#"{{"kind":"ev",{T},"ev":"start_comp"}}"#),
            ),
            (
                Ev::Comp {
                    flags: 2056,
                    comp_len: 6,
                    result_len: 0,
                },
                format!(
                    r#"{{"kind":"ev",{T},"ev":"comp","flags":2056,"comp_len":6,"result_len":0}}"#
                ),
            ),
            (
                Ev::EndComp,
                format!(r#"{{"kind":"ev",{T},"ev":"end_comp"}}"#),
            ),
            (
                Ev::Notify { imn: 5 },
                format!(r#"{{"kind":"ev",{T},"ev":"notify","imn":5}}"#),
            ),
            (
                Ev::KeyDown { vk: 0xE5 },
                format!(r#"{{"kind":"ev",{T},"ev":"key_down","vk":229}}"#),
            ),
            (
                Ev::FocusIn,
                format!(r#"{{"kind":"ev",{T},"ev":"focus_in"}}"#),
            ),
            (
                Ev::FocusOut,
                format!(r#"{{"kind":"ev",{T},"ev":"focus_out"}}"#),
            ),
            (
                Ev::TsfStart,
                format!(r#"{{"kind":"ev",{T},"ev":"tsf_start"}}"#),
            ),
            (
                Ev::TsfUpdate { len: 3 },
                format!(r#"{{"kind":"ev",{T},"ev":"tsf_update","len":3}}"#),
            ),
            (
                Ev::TsfEnd { result_len: None },
                format!(r#"{{"kind":"ev",{T},"ev":"tsf_end"}}"#),
            ),
            (
                Ev::TsfEnd {
                    result_len: Some(2),
                },
                format!(r#"{{"kind":"ev",{T},"ev":"tsf_end","result_len":2}}"#),
            ),
            (
                Ev::UiBegin {
                    kind: UiKind::Candidate,
                },
                format!(r#"{{"kind":"ev",{T},"ev":"ui_begin","ui_kind":"candidate"}}"#),
            ),
            (
                Ev::UiUpdate {
                    kind: UiKind::Reading,
                },
                format!(r#"{{"kind":"ev",{T},"ev":"ui_update","ui_kind":"reading"}}"#),
            ),
            (
                Ev::UiEnd {
                    kind: UiKind::Other,
                },
                format!(r#"{{"kind":"ev",{T},"ev":"ui_end","ui_kind":"other"}}"#),
            ),
        ];
        for (ev, want) in cases {
            assert_eq!(l(ev), want, "{ev:?}");
        }
    }

    #[test]
    fn lines_have_no_spaces_no_escapes_and_no_newline() {
        let evs = [
            Ev::StartComp,
            Ev::Comp {
                flags: u32::MAX,
                comp_len: u32::MAX,
                result_len: u32::MAX,
            },
            Ev::Notify { imn: 0x10 },
            Ev::TsfEnd {
                result_len: Some(1),
            },
            Ev::UiEnd {
                kind: UiKind::Candidate,
            },
        ];
        for ev in evs {
            let s = line(ev, u64::MAX, u64::MAX);
            assert!(
                !s.contains(' ') && !s.contains('\\') && !s.contains('\n') && !s.contains('\r'),
                "{s}"
            );
        }
        let h = header(Src::WinImm, "0.1.0");
        assert!(
            !h.contains(' ') && !h.contains('\\') && !h.contains('\n'),
            "{h}"
        );
    }

    #[test]
    fn utf16_bytes_convert_to_code_units() {
        assert_eq!(units_from_bytes(0), 0);
        assert_eq!(units_from_bytes(2), 1);
        assert_eq!(units_from_bytes(12), 6);
        // A remainder is truncated; this never occurs for genuine UTF-16 data, but a corrupted
        // value must not overflow.
        assert_eq!(units_from_bytes(7), 3);
        // An IMM error (a negative value) is 0.
        assert_eq!(units_from_bytes(-1), 0);
        assert_eq!(units_from_bytes(-2), 0);
        assert_eq!(units_from_bytes(i32::MIN), 0);
        assert_eq!(units_from_bytes(i32::MAX), (i32::MAX as u32) / 2);
    }

    #[test]
    fn ticks_convert_to_ns_without_overflow() {
        // 10 MHz: 1 tick = 100 ns
        assert_eq!(ticks_to_ns(1, 10_000_000), 100);
        assert_eq!(ticks_to_ns(10_000_000, 10_000_000), 1_000_000_000);
        // Correct even when `ticks * 10^9` exceeds a u64 (about 30 days of uptime at 10 MHz
        // yields roughly 2.6e13 ticks).
        let ticks: i64 = 26_000_000_000_000;
        assert_eq!(ticks_to_ns(ticks, 10_000_000), 2_600_000_000_000_000);
        // A zero frequency, or a negative value, yields 0.
        assert_eq!(ticks_to_ns(5, 0), 0);
        assert_eq!(ticks_to_ns(-5, 10_000_000), 0);
        assert_eq!(ticks_to_ns(5, -1), 0);
    }

    #[test]
    fn check_line_accepts_every_line_this_module_writes() {
        assert_eq!(check_line(&header(Src::WinImm, "0.1.0")), Ok(()));
        assert_eq!(check_line(&header(Src::WinTsf, "a.b+c-1")), Ok(()));
        let evs = [
            Ev::StartComp,
            Ev::Comp {
                flags: 2056,
                comp_len: 6,
                result_len: 0,
            },
            Ev::EndComp,
            Ev::Notify { imn: 5 },
            Ev::KeyDown { vk: 0xE5 },
            Ev::FocusIn,
            Ev::FocusOut,
            Ev::TsfStart,
            Ev::TsfUpdate { len: 3 },
            Ev::TsfEnd { result_len: None },
            Ev::TsfEnd {
                result_len: Some(2),
            },
            Ev::UiBegin {
                kind: UiKind::Candidate,
            },
            Ev::UiUpdate {
                kind: UiKind::Reading,
            },
            Ev::UiEnd {
                kind: UiKind::Other,
            },
        ];
        for ev in evs {
            let s = line(ev, u64::MAX, 0);
            assert_eq!(check_line(&s), Ok(()), "{s}");
        }
    }

    #[test]
    fn check_line_rejects_anything_that_could_carry_text() {
        let good = r#"{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"key_down","vk":13}"#;
        assert_eq!(check_line(good), Ok(()));
        let bad = [
            // An unknown key, at a position that could carry text.
            r#"{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"key_down","text":"a"}"#,
            r#"{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"key_down","vk":"a"}"#,
            // A string outside the enumeration.
            r#"{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"typed"}"#,
            r#"{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"comp","ui_kind":"あ"}"#,
            // A space, an escape sequence, or a non-ASCII byte.
            r#"{"kind":"ev", "t_wall_ms":1,"t_mono_ns":2,"ev":"key_down","vk":13}"#,
            "{\"kind\":\"ev\",\"t_wall_ms\":1,\"t_mono_ns\":2,\"ev\":\"key_down\",\"vk\":13,\"ui_kind\":\"\u{3042}\"}",
            // Not an integer.
            r#"{"kind":"ev","t_wall_ms":-1,"t_mono_ns":2,"ev":"key_down","vk":13}"#,
            r#"{"kind":"ev","t_wall_ms":1.5,"t_mono_ns":2,"ev":"key_down","vk":13}"#,
            r#"{"kind":"ev","t_wall_ms":true,"t_mono_ns":2,"ev":"key_down","vk":13}"#,
            r#"{"kind":"ev","t_wall_ms":18446744073709551616,"t_mono_ns":2,"ev":"key_down","vk":13}"#,
            // A duplicate key, nesting, or a malformed shape.
            r#"{"kind":"ev","kind":"ev"}"#,
            r#"{"kind":"ev","t_wall_ms":{"a":1}}"#,
            r#"{"kind":"ev","t_wall_ms":1,}"#,
            r#"{"kind":"ev","t_wall_ms":1"#,
            r#"["kind"]"#,
            "",
            "{}",
        ];
        for s in bad {
            assert!(check_line(s).is_err(), "{s}");
        }
        // An escape sequence is rejected even when the decoded string would otherwise match the
        // enumeration. The character is assembled from its code point rather than written
        // literally in the source, since a literal escape character can be silently altered by
        // some editing tools.
        let bs = char::from(0x5c_u8);
        let escaped = format!(
            "{{\"kind\":\"ev\",\"t_wall_ms\":1,\"t_mono_ns\":2,\"ev\":\"key{bs}_down\",\"vk\":13}}"
        );
        assert!(check_line(&escaped).is_err(), "{escaped}");
        // `app_version` is checked only against the version-string shape.
        let hdr = |v: &str| {
            format!(
                r#"{{"kind":"header","format":"gt/1","src":"win_imm","app_version":"{v}","os":"windows"}}"#
            )
        };
        assert_eq!(check_line(&hdr("0.1.0")), Ok(()));
        assert!(check_line(&hdr("")).is_err());
        assert!(check_line(&hdr("a b")).is_err());
        assert!(check_line(&hdr(&"v".repeat(33))).is_err());
        // The error reason never includes a value taken from the line.
        let secret = r#"{"kind":"ev","zzsecret":1}"#;
        assert!(!check_line(secret).unwrap_err().contains("zzsecret"));
    }

    #[test]
    fn selftest_sequence_shape_is_plausible_gt_1() {
        // A representative conversion (start, update, commit, end) takes the gt/1 shape (a
        // single-line JSON object).
        let seq = [
            Ev::FocusIn,
            Ev::KeyDown { vk: 0xE5 },
            Ev::StartComp,
            Ev::Comp {
                flags: 0x0008,
                comp_len: 1,
                result_len: 0,
            },
            Ev::Comp {
                flags: 0x0800,
                comp_len: 0,
                result_len: 1,
            },
            Ev::EndComp,
            Ev::FocusOut,
        ];
        for (i, ev) in seq.iter().enumerate() {
            let s = line(*ev, 1000 + i as u64, 2000 + i as u64);
            assert!(
                s.starts_with("{\"kind\":\"ev\",") && s.ends_with('}'),
                "{s}"
            );
        }
    }
}
