#!/usr/bin/env python3
# kit: selftest
"""Content-free validator for `gt/1` and `obs/1` logs (Python standard library only).

  validate_logs.py --kind gt|obs FILE...   Print violations with line numbers and exit 1;
                                            exit 0 if there are none.
  validate_logs.py --selftest              Positive and negative controls (fast default, under
                                            one second).

Exit 2 means "validation could not run at all" (a usage error, or a file that cannot be opened).
This is distinct from zero violations. Treat any exit code other than 0 as failing: if there is at
least one violation, this returns exit 1 even when an unreadable file is present as well.

What this guarantees is the **shape** of `gt` and `obs` rows: a key that may hold a string value is
constrained to an enumeration or a regular expression from FORMATS.md, an unknown key or an escape
(backslash) is rejected, and only an integer may occupy an integer position.
Out of scope: the **meaning of a value** in an integer field, a version string, or
`manifest_sha256` (payload encoded into it) is not checked. Keeping the log content-free is the
producing side's design responsibility (writing only kind, length, and time); this validator
mechanically confirms that shape.
A violation's description **never includes a value** (so payload content does not leak through the
validator's output if any is present). It shows only the kind of violation and a key name already
known from FORMATS.md (an unknown key name is not shown).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

U64_MAX = 2 ** 64 - 1
U32_MAX = 2 ** 32 - 1
I64_MIN = -(2 ** 63)

VERSION_RE = re.compile(r"[0-9A-Za-z.+-]{1,32}")
SHA256_RE = re.compile(r"[0-9A-Fa-f]{64}")
# The standalone logger emits only two row types. Across the three arrays, each name occurs at
# most once (i.e., two names in total at most).
CAP_NAMES = ("key", "ime_shadow")

OS_NAMES = ("macos", "windows")
GT_SRCS = ("mac_nstic", "win_imm", "win_tsf")
CMDS = (
    "insertNewline:", "cancelOperation:", "deleteBackward:", "deleteForward:",
    "moveLeft:", "moveRight:", "moveUp:", "moveDown:", "insertTab:", "insertBacktab:",
    "noop:", "other",
)
UI_KINDS = ("candidate", "reading", "other")
ENUM_FIELDS = {"cmd": CMDS, "ui_kind": UI_KINDS}

# gt/1's ev and its accompanying fields: ev -> (required, optional). Accompanying fields are
# integers (only cmd / ui_kind are string enums).
_COMMON_EVENTS = {
    "key_down": (("vk",), ()),
    "focus_in": ((), ()),
    "focus_out": ((), ()),
}
_NO_FIELDS = ((), ())
GT_EVENTS = {
    "mac_nstic": dict(
        _COMMON_EVENTS,
        set_marked=(("len", "sel_loc", "sel_len"), ("rep_loc", "rep_len")),
        insert_text=(("len",), ("rep_loc", "rep_len")),
        unmark=_NO_FIELDS,
        first_rect=_NO_FIELDS,
        do_command=(("cmd",), ()),
    ),
    "win_imm": dict(
        _COMMON_EVENTS,
        start_comp=_NO_FIELDS,
        end_comp=_NO_FIELDS,
        comp=(("flags", "comp_len", "result_len"), ()),
        notify=(("imn",), ()),
    ),
    "win_tsf": dict(
        _COMMON_EVENTS,
        tsf_start=_NO_FIELDS,
        tsf_end=((), ("result_len",)),
        tsf_update=(("len",), ()),
        ui_begin=(("ui_kind",), ()),
        ui_update=(("ui_kind",), ()),
        ui_end=(("ui_kind",), ()),
    ),
}
GT_HEADER_KEYS = ("kind", "format", "src", "app_version", "os")
GT_EVENT_BASE = ("kind", "t_wall_ms", "t_mono_ns", "ev")

# obs/1 (the standalone logger's observation log format)
OBS_CATEGORIES = (
    "alphabetic", "numeric", "punctuation", "sentence_punctuation", "tab", "backspace",
    "delete", "space", "enter", "navigation", "modifier", "function", "escape", "other",
)
OBS_HEADER_KEYS = ("type", "format", "os", "logger_version", "manifest_sha256", "capabilities")
CAP_KEYS = ("unconditional", "threshold_gated", "config_gated")
OBS_KEY_KEYS = ("type", "t", "category", "press")
OBS_SHADOW_KEYS = ("type", "t", "dwell_ms", "nav", "abandon")

# Key names that may appear in a violation's description (only names listed in FORMATS.md)
KNOWN_KEYS = frozenset(
    GT_HEADER_KEYS + GT_EVENT_BASE + OBS_HEADER_KEYS + CAP_KEYS + OBS_KEY_KEYS + OBS_SHADOW_KEYS
    + tuple(k for table in GT_EVENTS.values() for req, opt in table.values() for k in req + opt)
)

MAX_SHOWN = 50


class _Reject(ValueError):
    def __init__(self, code, msg):
        ValueError.__init__(self, msg)
        self.code = code


def _no_float(_s):
    raise _Reject("float", "a decimal or exponential-notation number (only integers are allowed)")


def _no_constant(_s):
    raise _Reject("constant", "NaN / Infinity is not a valid JSON value")


def _no_dup(pairs):
    d = {}
    for k, val in pairs:
        if k in d:
            raise _Reject("dup", "the same key appears twice on one line")
        d[k] = val
    return d


def _safe(name):
    """A key name is shown only when it is already known (this keeps payload from leaking through a key name)."""
    if isinstance(name, str) and name in KNOWN_KEYS:
        return name
    return "(name not in FORMATS.md, not shown)"


def _has_space_outside_strings(text):
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in " \t\r\n":
            return True
    return False


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class _Errs(object):
    def __init__(self):
        self.items = []

    def add(self, ln, code, msg):
        self.items.append((ln, code, msg))


def _check_keys(errs, ln, obj, required, optional):
    allowed = set(required) | set(optional)
    for k in obj:
        if k not in allowed:
            errs.add(ln, "unknown_key", "unknown key: " + _safe(k))
    for k in required:
        if k not in obj:
            errs.add(ln, "missing_key", "required key missing: " + k)


def _check_int(errs, ln, key, v, lo=I64_MIN, hi=U64_MAX):
    if not _is_int(v):
        errs.add(ln, "type", "non-integer value in an integer position: " + key)
    elif v < lo or v > hi:
        errs.add(ln, "range", "integer out of range: " + key)


def _check_bool(errs, ln, key, v):
    if not isinstance(v, bool):
        errs.add(ln, "type", "non-boolean value in a boolean position: " + key)


def _check_enum(errs, ln, key, v, allowed):
    if not isinstance(v, str):
        errs.add(ln, "type", "non-string value in a string position: " + key)
    elif v not in allowed:
        errs.add(ln, "enum", "value outside the enumeration: " + key)


def _check_regex(errs, ln, key, v, rx):
    if not isinstance(v, str):
        errs.add(ln, "type", "non-string value in a string position: " + key)
    elif not rx.fullmatch(v):
        errs.add(ln, "regex", "value does not match the regular expression: " + key)


def _check_fixed(errs, ln, code, key, v, want):
    if v != want:
        errs.add(ln, code, "value differs from the fixed value: " + key)


def _parse_line(raw, ln, errs):
    """Validate one line and return a dict. Returns None when it cannot (the reason is added to errs)."""
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    if b"\\" in raw:
        # Every allowed string value is \-free. Spelling it out with an escape lets a validator
        # that searches the raw string for a row slip through.
        errs.add(ln, "escape", "contains an escape (\\) (gt / obs lines do not use escaping)")
        # Keep validating the rest of the line (report every violation at once)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        errs.add(ln, "utf8", "cannot be read as UTF-8")
        return None
    try:
        obj = json.loads(text, object_pairs_hook=_no_dup, parse_float=_no_float,
                         parse_constant=_no_constant)
    except _Reject as e:
        errs.add(ln, e.code, str(e))
        return None
    except (ValueError, RecursionError):
        errs.add(ln, "json", "cannot be read as JSON (including a blank line)")
        return None
    if not isinstance(obj, dict):
        errs.add(ln, "type", "each line must be exactly one object")
        return None
    if _has_space_outside_strings(text):
        errs.add(ln, "sep", "whitespace outside a string (no whitespace is allowed in separators)")
        # Keep validating the rest of the line (report every violation at once)
    return obj


def _check_gt_header(obj, ln, errs):
    _check_keys(errs, ln, obj, GT_HEADER_KEYS, ())
    _check_fixed(errs, ln, "header", "kind", obj.get("kind"), "header")
    if "format" in obj:
        _check_fixed(errs, ln, "format", "format", obj["format"], "gt/1")
    if "src" in obj:
        _check_enum(errs, ln, "src", obj["src"], GT_SRCS)
    if "app_version" in obj:
        _check_regex(errs, ln, "app_version", obj["app_version"], VERSION_RE)
    if "os" in obj:
        _check_enum(errs, ln, "os", obj["os"], OS_NAMES)
    src = obj.get("src")
    return src if isinstance(src, str) and src in GT_SRCS else None


def _check_gt_event(obj, ln, src, errs):
    kind = obj.get("kind")
    if kind != "ev":
        errs.add(ln, "kind", "kind must be ev on every line after the first (the header occupies line 1 only)")
        return
    ev = obj.get("ev")
    if not isinstance(ev, str):
        errs.add(ln, "type", "non-string value in a string position: ev")
        return
    table = GT_EVENTS[src]
    if ev not in table:
        errs.add(ln, "ev", "an ev that does not occur for this src, or an unknown ev")
        return
    required, optional = table[ev]
    _check_keys(errs, ln, obj, GT_EVENT_BASE + tuple(required), optional)
    for k, v in obj.items():
        if k in ("kind", "ev"):
            continue
        if k in ENUM_FIELDS:
            _check_enum(errs, ln, k, v, ENUM_FIELDS[k])
        elif k in GT_EVENT_BASE or k in required or k in optional:
            _check_int(errs, ln, k, v)
        # any other key was already flagged unknown_key by _check_keys


def _check_capabilities(errs, ln, cap):
    if not isinstance(cap, dict):
        errs.add(ln, "type", "non-object value in an object position: capabilities")
        return
    _check_keys(errs, ln, cap, CAP_KEYS, ())
    seen = set()
    for k in CAP_KEYS:
        if k not in cap:
            continue
        names = cap[k]
        if not isinstance(names, list):
            errs.add(ln, "type", "non-array value in an array position: " + k)
            continue
        for n in names:
            _check_enum(errs, ln, k, n, CAP_NAMES)
            if isinstance(n, str) and n in CAP_NAMES:
                if n in seen:
                    errs.add(ln, "dup", "the same capabilities name appears more than once: " + k)
                seen.add(n)


def _check_obs_header(obj, ln, errs):
    _check_keys(errs, ln, obj, OBS_HEADER_KEYS, ())
    _check_fixed(errs, ln, "header", "type", obj.get("type"), "meta")
    if "format" in obj:
        _check_fixed(errs, ln, "format", "format", obj["format"], "obs/1")
    if "os" in obj:
        _check_enum(errs, ln, "os", obj["os"], OS_NAMES)
    if "logger_version" in obj:
        _check_regex(errs, ln, "logger_version", obj["logger_version"], VERSION_RE)
    if "manifest_sha256" in obj:
        _check_regex(errs, ln, "manifest_sha256", obj["manifest_sha256"], SHA256_RE)
    if "capabilities" in obj:
        _check_capabilities(errs, ln, obj["capabilities"])


def _check_obs_line(obj, ln, errs):
    t = obj.get("type")
    if t == "key":
        _check_keys(errs, ln, obj, OBS_KEY_KEYS, ())
        if "t" in obj:
            _check_int(errs, ln, "t", obj["t"], 0, U64_MAX)
        if "category" in obj:
            _check_enum(errs, ln, "category", obj["category"], OBS_CATEGORIES)
        if "press" in obj:
            _check_bool(errs, ln, "press", obj["press"])
    elif t == "ime_shadow":
        _check_keys(errs, ln, obj, OBS_SHADOW_KEYS, ())
        if "t" in obj:
            _check_int(errs, ln, "t", obj["t"], 0, U64_MAX)
        if "dwell_ms" in obj:
            _check_int(errs, ln, "dwell_ms", obj["dwell_ms"], 0, U64_MAX)
        if "nav" in obj:
            _check_int(errs, ln, "nav", obj["nav"], 0, U32_MAX)
        if "abandon" in obj:
            _check_bool(errs, ln, "abandon", obj["abandon"])
    else:
        errs.add(ln, "type", "type other than key / ime_shadow (including a second meta line)")


def validate(kind, data):
    """Validate bytes and return [(line number, code, description)]. Empty means no violations."""
    errs = _Errs()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines:
        errs.add(0, "header", "empty file (line 1 must be the header)")
        return errs.items
    src = None
    for ln, raw in enumerate(lines, 1):
        obj = _parse_line(raw, ln, errs)
        if obj is None:
            continue
        if kind == "gt":
            if ln == 1:
                src = _check_gt_header(obj, ln, errs)
            elif src is not None:
                _check_gt_event(obj, ln, src, errs)
        elif ln == 1:
            _check_obs_header(obj, ln, errs)
        else:
            _check_obs_line(obj, ln, errs)
    return errs.items


# ---------------------------------------------------------------- selftest

def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _gt_header(src="mac_nstic", **over):
    h = {"kind": "header", "format": "gt/1", "src": src, "app_version": "0.1.0",
         "os": "macos" if src == "mac_nstic" else "windows"}
    h.update(over)
    return h


def _ev(name, **fields):
    d = {"kind": "ev", "t_wall_ms": 1, "t_mono_ns": 2, "ev": name}
    d.update(fields)
    return d


_ZERO64 = "0" * 64


def _obs_header(**over):
    h = {"type": "meta", "format": "obs/1", "os": "macos", "logger_version": "0.1.0",
         "manifest_sha256": _ZERO64,
         "capabilities": {"unconditional": ["key"], "threshold_gated": [],
                          "config_gated": ["ime_shadow"]}}
    h.update(over)
    return h


def _key(**over):
    d = {"type": "key", "t": 10, "category": "alphabetic", "press": True}
    d.update(over)
    return d


def _shadow(**over):
    d = {"type": "ime_shadow", "t": 20, "dwell_ms": 900, "nav": 0, "abandon": False}
    d.update(over)
    return d


def _text(*objs):
    """A dict is passed through _dump; a str is used as the line verbatim."""
    return "\n".join(o if isinstance(o, str) else _dump(o) for o in objs) + "\n"


def _positive_gt():
    mac = [
        _ev("key_down", vk=65), _ev("focus_in"), _ev("focus_out"),
        _ev("set_marked", len=3, sel_loc=3, sel_len=0),
        _ev("set_marked", len=3, sel_loc=3, sel_len=0, rep_loc=0, rep_len=2),
        _ev("insert_text", len=2), _ev("insert_text", len=2, rep_loc=0, rep_len=1),
        _ev("unmark"), _ev("first_rect"),
    ] + [_ev("do_command", cmd=c) for c in CMDS]
    imm = [
        _ev("key_down", vk=13), _ev("start_comp"), _ev("end_comp"),
        _ev("comp", flags=2048, comp_len=0, result_len=2), _ev("notify", imn=5),
    ]
    tsf = [
        _ev("key_down", vk=13), _ev("tsf_start"), _ev("tsf_end"), _ev("tsf_end", result_len=2),
        _ev("tsf_update", len=4),
    ] + [_ev(n, ui_kind=u) for n in ("ui_begin", "ui_update", "ui_end") for u in UI_KINDS]
    return {"mac_nstic": mac, "win_imm": imm, "win_tsf": tsf}


def _gt_cases():
    """(name, body, expected code; None means it should pass)"""
    mac_h = _gt_header()
    ok = [_ev("set_marked", len=3, sel_loc=3, sel_len=0)]
    cases = []
    for src, evs in _positive_gt().items():
        cases.append(("gt positive: " + src + " (all ev)", _text(_gt_header(src), *evs), None))
    cases += [
        ("gt positive: header only", _text(mac_h), None),
        ("gt positive: CRLF line endings", _text(mac_h, *ok).replace("\n", "\r\n"), None),
        ("gt positive: integer upper bound (2^64-1)", _text(mac_h, _ev("insert_text", len=U64_MAX)), None),
    ]
    neg = [
        ("payload key (text)", _text(mac_h, _ev("insert_text", len=3, text="日本語")), "unknown_key"),
        ("string in an integer position (digits)", _text(mac_h, _ev("insert_text", len="3")), "type"),
        ("string in an integer position (Japanese text)", _text(mac_h, _ev("insert_text", len="日本")), "type"),
        ("boolean in an integer position", _text(mac_h, _ev("insert_text", len=True)), "type"),
        ("array in an integer position", _text(mac_h, _ev("insert_text", len=[3])), "type"),
        ("object in an integer position", _text(mac_h, _ev("insert_text", len={"a": 1})), "type"),
        ("null in an integer position", _text(mac_h, _ev("insert_text", len=None)), "type"),
        ("decimal number", _text(mac_h, _ev("insert_text", len=3.0)), "float"),
        ("integer out of range (2^64)", _text(mac_h, _ev("insert_text", len=U64_MAX + 1)), "range"),
        ("NaN", _text(mac_h, '{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"insert_text","len":NaN}'),
         "constant"),
        ("cmd outside the enumeration", _text(mac_h, _ev("do_command", cmd="insertText:")), "enum"),
        ("cmd is not a string", _text(mac_h, _ev("do_command", cmd=3)), "type"),
        ("ui_kind outside the enumeration", _text(_gt_header("win_tsf"), _ev("ui_begin", ui_kind="日本語")), "enum"),
        ("unknown key (header)", _text(_gt_header(note="x")), "unknown_key"),
        ("unknown key (a Japanese key name)", _text(mac_h, _ev("unmark", **{"日本語": 1})), "unknown_key"),
        ("unknown key (a romanized key name; the name is not shown)", _text(mac_h, _ev("unmark", nihongowokaku=1)),
         "unknown_key"),
        ("escape (format's backslash-slash)",
         _text('{"kind":"header","format":"gt\\/1","src":"mac_nstic","app_version":"0.1.0","os":"macos"}'),
         "escape"),
        ("escape (ev's backslash-u escape)", _text(mac_h, '{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"\\u0075nmark"}'),
         "escape"),
        ("escape (a key name's backslash-u escape)",
         _text(mac_h, '{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"unmark","t\\u0065xt":1}'), "escape"),
        ("no header", _text(*ok), "header"),
        ("empty file", "", "header"),
        ("wrong format value", _text(_gt_header(format="gt/2")), "format"),
        ("app_version contains Japanese text", _text(_gt_header(app_version="日本語")), "regex"),
        ("app_version is too long", _text(_gt_header(app_version="1" * 33)), "regex"),
        ("app_version contains whitespace", _text(_gt_header(app_version="0.1 0")), "regex"),
        ("app_version has a trailing newline", _text(_gt_header(app_version="0.1.0\n")), "regex"),
        ("src outside the enumeration", _text(_gt_header(src="win_foo")), "enum"),
        ("os outside the enumeration", _text(_gt_header(os="linux")), "enum"),
        ("an ev that does not occur for this src (a win ev under mac)", _text(mac_h, _ev("start_comp")), "ev"),
        ("an ev that does not occur for this src (a mac ev under imm)",
         _text(_gt_header("win_imm"), _ev("set_marked", len=1, sel_loc=0, sel_len=0)), "ev"),
        ("unknown ev", _text(mac_h, _ev("bogus")), "ev"),
        ("a required field is missing", _text(mac_h, _ev("set_marked", len=3, sel_len=0)), "missing_key"),
        ("a timestamp is missing", _text(mac_h, '{"kind":"ev","t_wall_ms":1,"ev":"unmark"}'), "missing_key"),
        ("a timestamp is a string", _text(mac_h, _ev("unmark", t_wall_ms="1")), "type"),
        ("an accompanying field on an ev that should have none", _text(mac_h, _ev("unmark", len=1)), "unknown_key"),
        ("a header on line 2", _text(mac_h, mac_h), "kind"),
        ("unknown kind", _text(mac_h, {"kind": "foo"}), "kind"),
        ("a blank line in the middle", _text(mac_h, "", *ok), "json"),
        ("a line that is not an object", _text(mac_h, "[1]"), "type"),
        ("whitespace in a separator (': ')", _text(mac_h, '{"kind": "ev","t_wall_ms":1,"t_mono_ns":2,"ev":"unmark"}'),
         "sep"),
        ("whitespace in a separator (', ')", _text(mac_h, '{"kind":"ev", "t_wall_ms":1,"t_mono_ns":2,"ev":"unmark"}'),
         "sep"),
        ("the same key twice", _text(mac_h, '{"kind":"ev","t_wall_ms":1,"t_mono_ns":2,"ev":"insert_text",'
                                  '"len":3,"len":4}'), "dup"),
        ("not UTF-8", _text(mac_h).encode("utf-8") + b'{"kind":"ev","ev":"\xff"}\n', "utf8"),
    ]
    for name, body, code in neg:
        cases.append(("gt negative: " + name, body, code))
    return cases


def _obs_cases():
    h = _obs_header()
    cases = [
        ("obs positive: meta + key + ime_shadow", _text(h, _key(), _shadow()), None),
        ("obs positive: header only", _text(h), None),
        ("obs positive: all 14 category kinds",
         _text(h, *[_key(category=c, press=bool(i % 2)) for i, c in enumerate(OBS_CATEGORIES)]), None),
        ("obs positive: os=windows + all-empty capabilities",
         _text(_obs_header(os="windows", capabilities={"unconditional": [], "threshold_gated": [],
                                                       "config_gated": []}), _key()), None),
        ("obs positive: two names in the same array",
         _text(_obs_header(capabilities={"unconditional": ["key", "ime_shadow"], "threshold_gated": [],
                                         "config_gated": []})), None),
        ("obs positive: CRLF line endings", _text(h, _key()).replace("\n", "\r\n"), None),
    ]
    caps = h["capabilities"]

    def cap(**over):
        c = dict(caps)
        c.update(over)
        return c

    neg = [
        ("category outside the 14 kinds", _text(h, _key(category="a")), "enum"),
        ("category is not a string", _text(h, _key(category=1)), "type"),
        ("ime_shadow has a text key", _text(h, _shadow(text="日本語")), "unknown_key"),
        ("key has a vk key (the old format)", _text(h, _key(vk=65)), "unknown_key"),
        ("no header (starts with key)", _text(_key(), _shadow()), "header"),
        ("empty file", "", "header"),
        ("wrong format value", _text(_obs_header(format="obs/2")), "format"),
        ("os outside the enumeration", _text(_obs_header(os="linux")), "enum"),
        ("logger_version contains Japanese text", _text(_obs_header(logger_version="日本語")), "regex"),
        ("manifest_sha256 has 63 digits", _text(_obs_header(manifest_sha256="0" * 63)), "regex"),
        ("manifest_sha256 is not hex", _text(_obs_header(manifest_sha256="g" * 64)), "regex"),
        ("a capabilities name is Japanese text", _text(_obs_header(capabilities=cap(config_gated=["日本語"]))),
         "enum"),
        ("a capabilities name is uppercase", _text(_obs_header(capabilities=cap(unconditional=["Key"]))),
         "enum"),
        ("a capabilities name is a sequence of romanized words",
         _text(_obs_header(capabilities=cap(unconditional=["watashi", "ha", "nihongo", "wo", "kaku"]))),
         "enum"),
        ("a capabilities name is duplicated within the same array",
         _text(_obs_header(capabilities=cap(unconditional=["key", "key"]))), "dup"),
        ("a capabilities name is duplicated across different arrays",
         _text(_obs_header(capabilities=cap(unconditional=["key"], config_gated=["key"]))), "dup"),
        ("a capabilities name is not a string", _text(_obs_header(capabilities=cap(unconditional=[1]))),
         "type"),
        ("a capabilities key is missing",
         _text(_obs_header(capabilities={"unconditional": ["key"], "config_gated": []})), "missing_key"),
        ("capabilities has an unknown key", _text(_obs_header(capabilities=cap(extra=[]))), "unknown_key"),
        ("a capabilities value is not an array", _text(_obs_header(capabilities=cap(unconditional="key"))),
         "type"),
        ("capabilities is not an object", _text(_obs_header(capabilities=["key"])), "type"),
        ("capabilities is missing entirely", _text({k: v for k, v in h.items() if k != "capabilities"}), "missing_key"),
        ("unknown type", _text(h, {"type": "paste", "t": 1}), "type"),
        ("meta on line 2", _text(h, h), "type"),
        ("press is an integer", _text(h, _key(press=1)), "type"),
        ("press is a string", _text(h, _key(press="true")), "type"),
        ("press is missing", _text(h, {"type": "key", "t": 10, "category": "space"}), "missing_key"),
        ("t is a string", _text(h, _key(t="10")), "type"),
        ("t is negative", _text(h, _key(t=-1)), "range"),
        ("nav is outside u32", _text(h, _shadow(nav=U32_MAX + 1)), "range"),
        ("abandon is an integer", _text(h, _shadow(abandon=0)), "type"),
        ("whitespace in a separator", _text(h, '{"type": "key","t":10,"category":"space","press":true}'), "sep"),
        ("escape (type's backslash-u escape)", _text(h, '{"type":"k\\u0065y","t":10,"category":"space","press":true}'),
         "escape"),
        ("a gt header validated as obs", _text(_gt_header(), _ev("unmark")), "header"),
    ]
    for name, body, code in neg:
        cases.append(("obs negative: " + name, body, code))
    return cases


def _as_bytes(body):
    return body if isinstance(body, bytes) else body.encode("utf-8")


# Markers embedded in negative-control payloads. Finding one of these in a violation's description means it leaked.
_LEAK_SENTINELS = ("日本", "insertText", "nihongo", "watashi")


def _run_cases(fails):
    n = 0
    for kind, cases in (("gt", _gt_cases()), ("obs", _obs_cases())):
        for name, body, want in cases:
            n += 1
            found = validate(kind, _as_bytes(body))
            codes = set(c for _, c, _ in found)
            if want is None:
                if found:
                    fails.append("%s: should pass but was rejected: %s" % (name, sorted(codes)))
            elif not found:
                fails.append("%s: should be rejected but passed" % name)
            elif want not in codes:
                fails.append("%s: rejected for a different reason (expected %s, got %s)" % (name, want, sorted(codes)))
            else:
                # Confirm the description leaks neither a value nor an unknown key name
                for _, _, msg in found:
                    if any(s in msg for s in _LEAK_SENTINELS):
                        fails.append("%s: a value leaked into the violation's description: %s" % (name, msg))
    return n


def _run_sweep(fails):
    """For every ev in every table, confirm "missing required is rejected" and "an unknown key is
    rejected" (this catches anything the table forgot)."""
    n = 0
    for src, table in GT_EVENTS.items():
        for ev, (required, optional) in table.items():
            def fill(keys):
                return dict((k, ENUM_FIELDS[k][0] if k in ENUM_FIELDS else 1) for k in keys)
            base = fill(tuple(required))
            hdr = _gt_header(src)
            n += 1
            if validate("gt", _as_bytes(_text(hdr, _ev(ev, **base)))):
                fails.append("sweep %s/%s: a row with only the required fields was rejected" % (src, ev))
            n += 1
            if validate("gt", _as_bytes(_text(hdr, _ev(ev, **dict(base, **fill(tuple(optional))))))):
                fails.append("sweep %s/%s: a row that also carries the optional fields was rejected" % (src, ev))
            for k in required:
                n += 1
                part = dict((kk, vv) for kk, vv in base.items() if kk != k)
                codes = set(c for _, c, _ in validate("gt", _as_bytes(_text(hdr, _ev(ev, **part)))))
                if "missing_key" not in codes:
                    fails.append("sweep %s/%s: dropping the required field %s was not rejected" % (src, ev, k))
            n += 1
            codes = set(c for _, c, _ in validate("gt", _as_bytes(_text(hdr, _ev(ev, **dict(base, zz=1))))))
            if "unknown_key" not in codes:
                fails.append("sweep %s/%s: an unknown key was not rejected" % (src, ev))
    return n


def _run_cli(fails):
    """Exercise the entry point itself (exit code and line-numbered output)."""
    tmp = tempfile.mkdtemp(prefix="validate_logs_selftest_")
    me = os.path.abspath(__file__)
    env = dict(os.environ, PYTHONUTF8="1")
    good = os.path.join(tmp, "good.jsonl")
    leak = os.path.join(tmp, "leak.jsonl")
    with open(good, "wb") as f:
        f.write(_text(_gt_header(), _ev("insert_text", len=2)).encode("utf-8"))
    with open(leak, "wb") as f:
        f.write(_text(_gt_header(), _ev("insert_text", len=2),
                      _ev("insert_text", len=3, text="日本語")).encode("utf-8"))

    calls = [0]

    def run(*args):
        calls[0] += 1
        p = subprocess.run([sys.executable, me] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=env)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    try:
        rc, out = run("--kind", "gt", good)
        if rc != 0:
            fails.append("CLI: a valid gt file did not exit 0 (%d): %s" % (rc, out.strip()[-200:]))
        rc, out = run("--kind", "gt", leak)
        if rc != 1:
            fails.append("CLI: a gt file with payload did not exit 1 (%d)" % rc)
        if ":3:" not in out:
            fails.append("CLI: a violation was not printed with a line number: %s" % out.strip()[-200:])
        if "日本語" in out:
            fails.append("CLI: payload leaked into the output")
        rc, out = run("--kind", "obs", good)
        if rc != 1:
            fails.append("CLI: validating a gt file as obs did not exit 1 (%d)" % rc)
        rc, out = run("--kind", "gt", os.path.join(tmp, "missing.jsonl"))
        if rc != 2:
            fails.append("CLI: a nonexistent file did not exit 2 (%d)" % rc)
        rc, out = run("--kind", "gt", good, leak)
        if rc != 1:
            fails.append("CLI: one violating file among several should exit 1 (got %d)" % rc)
        rc, out = run("--kind", "gt", os.path.join(tmp, "missing.jsonl"), leak)
        if rc != 1:
            fails.append("CLI: a violation takes priority over an unreadable file, exit 1 expected (got %d)" % rc)
        rc, out = run("--kind", "bogus", good)
        if rc != 2:
            fails.append("CLI: an unknown --kind did not exit 2 (%d)" % rc)
        rc, out = run("--kind", "gt")
        if rc != 2:
            fails.append("CLI: a missing FILE did not exit 2 (%d)" % rc)
        return calls[0]
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)


def selftest():
    fails = []
    n = _run_cases(fails) + _run_sweep(fails) + _run_cli(fails)
    if fails:
        for m in fails:
            print("FAIL " + m)
        print("selftest failed: %d / %d" % (len(fails), n))
        return 1
    print("selftest OK (%d cases)" % n)
    return 0


# ---------------------------------------------------------------- CLI

def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="content-free validator for gt/1 and obs/1")
    ap.add_argument("--kind", choices=("gt", "obs"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.kind or not args.files:
        ap.error("--kind gt|obs and FILE are required")
    violated = False
    unreadable = False
    for path in args.files:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            print("%s: cannot open (%s)" % (path, type(e).__name__), file=sys.stderr)
            unreadable = True
            continue
        found = validate(args.kind, data)
        for ln, code, msg in found[:MAX_SHOWN]:
            print("%s:%d: [%s] %s" % (path, ln, code, msg), file=sys.stderr)
        if len(found) > MAX_SHOWN:
            print("%s: ...and %d more" % (path, len(found) - MAX_SHOWN), file=sys.stderr)
        if found:
            violated = True
    # A violation takes priority (so a caller that treats exit 2 as "skipped" still sees red for a violation)
    if violated:
        return 1
    return 2 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
