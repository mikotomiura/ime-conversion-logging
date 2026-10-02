#!/usr/bin/env python3
# kit: selftest
"""Preprocessing step that extracts phrases from Aozora Bunko text, assigns a hiragana reading to
each phrase, and writes the result as corpus/1.

  build_corpus.py --src DIR --out FILE --seed N [--tokenizer sudachi|table:JSON]
                  [--min-reading 6] [--max-reading 30] [--per-work 100]
  build_corpus.py --validate FILE      check against corpus/1 (exit 1 on violation)
  build_corpus.py --selftest           positive and negative cases without a dictionary (fast default)

Pipeline: read as cp932 -> strip the title, the "about these symbols" frame, the colophon, annotation
markers, and ruby notation (a work with no frame falls back to discarding everything up to the first
blank line and prints a warning to standard error) -> split into sentences (on `。！？`) -> split into
clauses (on the reading-point and whitespace) -> assign readings -> merge clauses into phrases whose
reading length falls within [min, max] -> keep phrases whose reading is hiragana-only and free of
historical kana (`ゐ・ゑ・ゕ・ゖ`), and whose text (with reading-points and whitespace stripped) consists
only of kanji, hiragana, katakana, the long-vowel mark, or `々` -> sample up to K phrases per work using
the seed.

Readings are assigned by SudachiPy, but the tokenizer is swappable; `sudachipy` is imported only inside
SudachiTokenizer (never at module scope), so --selftest and check.sh stay green on a machine without the
dictionary installed. The selftest runs against a table-based tokenizer (a small lookup table built
inline for the test).
A reading can be wrong (e.g. for a proper noun). The accuracy evaluation only needs a valid key sequence
for hiragana input, even if the reading differs from the source text. The reconstruction evaluation's
ground truth is the "typed reading", not the source text.

Exit codes: 0 = success / 1 = rejected (a validation violation, or no phrase survived) / 2 = could not
build or validate (usage error, unreadable file, tokenizer unavailable). Any non-zero code counts as
rejection.
"""
import argparse
import builtins
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types

FORMAT = "corpus/1"
SOURCE = "aozora"
# The corpus/1 schema is defined in kit/formats/FORMATS.md
READING_RE = re.compile(r"[ぁ-ゖー]+")
# Historical kana (`ゐ・ゑ・ゕ・ゖ`): a reading containing one of these is dropped. The code points are
# built from chr() rather than written as literal characters, per the project's documentation convention.
OLD_KANA_RE = re.compile("[" + chr(0x3090) + chr(0x3091) + chr(0x3095) + chr(0x3096) + "]")
# Character-class filtering on the text side: only kanji, hiragana, katakana, the long-vowel mark, and `々`
# are allowed. A real Sudachi installation can assign a katakana reading to digits and Latin letters, so a
# phrase that would slip through a reading-only filter is dropped here on the text side instead.
TEXT_OK_RE = re.compile(
    "[" + chr(0x4E00) + "-" + chr(0x9FFF) + chr(0x3400) + "-" + chr(0x4DBF)
    + chr(0x3041) + "-" + chr(0x3096) + chr(0x30A1) + "-" + chr(0x30FA)
    + chr(0x30FC) + chr(0x3005) + "]+")
TEXT_STRIP_RE = re.compile(r"[、\s]")  # strip reading-points and whitespace before the text-side check (same as clause splitting)
NAME_RE = re.compile(r"[0-9A-Za-z._+-]{1,64}")
WORK_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,31}")
PHRASE_ID_RE = re.compile(r"([a-z0-9][a-z0-9_]{0,31})-[0-9]+-[0-9]+")
HEADER_KEYS = ("kind", "format", "source", "tokenizer", "dict")
PHRASE_KEYS = ("kind", "phrase_id", "work_id", "text", "reading", "reading_len")

DEFAULT_MIN = 6
DEFAULT_MAX = 30
DEFAULT_PER_WORK = 100
MAX_SHOWN = 50

# Aozora Bunko markup
DASH_RE = re.compile(r"-{10,}")            # the "about these symbols" frame (two dashed rules)
HEAD_SCAN = 40                             # the first dashed rule is expected within this many leading lines
HEAD_SPAN = 60                             # the second dashed rule is expected within this many lines of the first
FOOT_RE = re.compile(r"底本[：:]")          # start of the colophon at the end of the work (line-initial)
ANNOT_RE = re.compile(r"［＃[^］]*］")       # annotation marker
RUBY_RE = re.compile(r"《[^》]*》")          # ruby notation (the reading it carries is discarded)
SENT_END_RE = re.compile(r"[。！？]+[」』）]*")
QUOTE_RE = re.compile(r"[「」『』]")
CLAUSE_RE = re.compile(r"[^、\s]+")         # clause: split on the reading-point and whitespace (including full-width)


class BuildError(Exception):
    def __init__(self, msg, code=2):
        Exception.__init__(self, msg)
        self.code = code


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def kata_to_hira(s):
    """Convert katakana (`ァ-ヶ`) to hiragana. Everything else, such as `ー`, is left unchanged."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


# ---------------------------------------------------------------- tokenizers (interchangeable)

class TableTokenizer(object):
    """Longest-match substitution using a table (surface form -> hiragana reading). A character
    missing from the table is left unchanged (a hiragana character reads as itself).

    A kanji, katakana, digit, or Latin character missing from the table stays in the reading, so that
    clause is dropped. Intended for dictionary-free testing."""

    name = "table"

    def __init__(self, mapping, dict_name):
        self._map = dict(mapping)
        self._maxlen = max([len(k) for k in self._map] or [1])
        self.dict_name = dict_name

    def read(self, text):
        out = []
        p = 0
        while p < len(text):
            for n in range(min(self._maxlen, len(text) - p), 0, -1):
                hit = self._map.get(text[p:p + n])
                if hit is not None:
                    out.append(hit)
                    p += n
                    break
            else:
                out.append(text[p])
                p += 1
        return "".join(out)


def _dist_version(*names):
    """Version of the installed distribution package. Raises if it cannot be determined, since a run
    whose version cannot be recorded cannot be reproduced."""
    from importlib import metadata
    for n in names:
        try:
            return metadata.version(n)
        except metadata.PackageNotFoundError:
            continue
    raise BuildError("could not determine the package version: %s" % "/".join(names), 2)


def _name_safe(s):
    return re.sub(r"[^0-9A-Za-z._+-]", "_", s)[:64]


class SudachiTokenizer(object):
    """SudachiPy-backed tokenizer. sudachipy is imported only here (never at module scope)."""

    def __init__(self):
        try:
            from sudachipy import dictionary, tokenizer
        except ImportError as e:
            raise BuildError("sudachipy is not installed (run: pip install sudachipy sudachidict_core): %s" % e, 2)
        try:
            self._tok = dictionary.Dictionary().create()
            self._mode = tokenizer.Tokenizer.SplitMode.C
        except Exception as e:
            raise BuildError("could not open the SudachiPy dictionary (sudachidict_core is required): %s" % type(e).__name__, 2)
        self.name = _name_safe("sudachipy-" + _dist_version("SudachiPy", "sudachipy"))
        self.dict_name = _name_safe("sudachidict_core-" + _dist_version("SudachiDict-core", "sudachidict_core"))

    def read(self, text):
        # reading_form() is katakana; an empty reading (an unknown word) falls back to the surface form
        # (which is discarded later unless it happens to be hiragana).
        return kata_to_hira("".join(m.reading_form() or m.surface() for m in self._tok.tokenize(text, self._mode)))


def load_table(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise BuildError("could not open the table: %s (%s)" % (path, type(e).__name__), 2)
    try:
        mapping = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise BuildError("table is not readable as JSON (UTF-8): %s" % path, 2)
    if not isinstance(mapping, dict) or not mapping or not all(
            isinstance(k, str) and k and isinstance(v, str) and v for k, v in mapping.items()):
        raise BuildError("table must be a non-empty {surface form: reading} object of strings: %s" % path, 2)
    return TableTokenizer(mapping, hashlib.sha256(raw).hexdigest())


def make_tokenizer(spec):
    if spec == "sudachi":
        return SudachiTokenizer()
    if spec.startswith("table:") and len(spec) > len("table:"):
        return load_table(spec[len("table:"):])
    raise BuildError("--tokenizer must be sudachi or table:<json> (got: %s)" % spec, 2)


# ---------------------------------------------------------------- text normalization and phrase extraction

def body_lines(text):
    """Returns the body lines: (line list, no_dash). Strips the title and author lines, the "about these
    symbols" frame (two dashed rules), and the colophon; strips annotation markers and ruby notation.
    no_dash marks that the frame of two dashed rules was not found (the caller prints a warning).

    Ruby is discarded, keeping the underlying kanji (the reading is assigned by the tokenizer instead).
    When neither dashed rule is found (only one, or none), everything up to the first blank line is
    discarded instead (by Aozora Bunko convention, the title and author lines precede the first blank
    line)."""
    lines = re.split(r"\r\n|\r|\n", text)
    first = next((i for i, ln in enumerate(lines[:HEAD_SCAN]) if DASH_RE.fullmatch(ln.strip())), None)
    second = None
    if first is not None:
        second = next((i for i in range(first + 1, min(len(lines), first + 1 + HEAD_SPAN))
                       if DASH_RE.fullmatch(lines[i].strip())), None)
    no_dash = second is None
    if second is not None:
        lines = lines[second + 1:]
    else:
        blank = next((i for i, ln in enumerate(lines[:HEAD_SCAN]) if ln.strip() == ""), None)
        # Do not truncate if nothing follows the blank line (guards against a trailing blank line
        # produced by the final newline swallowing the whole body).
        if blank is not None and any(ln.strip() for ln in lines[blank + 1:]):
            lines = lines[blank + 1:]
    for i, ln in enumerate(lines):
        if FOOT_RE.match(ln):
            lines = lines[:i]
            break
    out = []
    for ln in lines:
        ln = ANNOT_RE.sub("", ln)
        out.append(RUBY_RE.sub("", ln).replace("｜", ""))
    return out, no_dash


def sentences(lines):
    """Splits each line into sentences (on `。！？`) and strips terminal punctuation and quotation marks
    (`「」『』`). A sentence that becomes empty is not counted."""
    out = []
    for ln in lines:
        parts = []
        pos = 0
        for m in SENT_END_RE.finditer(ln):
            parts.append(ln[pos:m.end()])
            pos = m.end()
        parts.append(ln[pos:])
        for p in parts:
            core = QUOTE_RE.sub("", SENT_END_RE.sub("", p)).strip()
            if core:
                out.append(core)
    return out


def phrases_of(sentence, tok, min_r, max_r):
    """Groups one sentence into phrases: [(text, reading), ...].

    Clauses (units separated by the reading-point and whitespace) whose reading is hiragana-only and
    contains no historical kana (`ゐ・ゑ・ゕ・ゖ`) are merged with the next clause until the combined
    length reaches min (merging stops as soon as the minimum is reached; a merge is never allowed to
    exceed max). A clause with an invalid reading, or one that alone exceeds max, joins no phrase. text
    keeps the original substring (a reading-point between merged clauses is retained). A merged text
    (with reading-points and whitespace stripped) that is not made up solely of kanji, hiragana,
    katakana, the long-vowel mark, or `々` is dropped: the reading alone cannot rule out phrases where a
    real Sudachi installation has assigned a katakana reading to a digit or a Latin letter, so the text
    side performs that check instead."""
    spans = []
    for m in CLAUSE_RE.finditer(sentence):
        r = tok.read(m.group(0))
        ok = READING_RE.fullmatch(r) and not OLD_KANA_RE.search(r)
        spans.append((m.start(), m.end(), r if ok else None))
    out = []
    i = 0
    while i < len(spans):
        total = 0
        j = i
        while j < len(spans) and spans[j][2] is not None and total + len(spans[j][2]) <= max_r:
            total += len(spans[j][2])
            j += 1
            if total >= min_r:
                break
        if total >= min_r:
            text = sentence[spans[i][0]:spans[j - 1][1]]
            if TEXT_OK_RE.fullmatch(TEXT_STRIP_RE.sub("", text)):
                out.append((text, "".join(s[2] for s in spans[i:j])))
                i = j
                continue
        i += 1
    return out


def work_candidates(work_id, text, tok, min_r, max_r):
    """Candidate phrases for one work, in body order: (candidate list, no_dash).

    phrase_id is <work_id>-<sentence index>-<phrase index> (determined entirely by the input; 1-based).
    no_dash marks that body_lines() could not find the frame of two dashed rules (the caller prints a
    warning)."""
    lines, no_dash = body_lines(text)
    cands = []
    for sno, sent in enumerate(sentences(lines), 1):
        for pno, (surface, reading) in enumerate(phrases_of(sent, tok, min_r, max_r), 1):
            cands.append({"kind": "phrase", "phrase_id": "%s-%d-%d" % (work_id, sno, pno), "work_id": work_id,
                          "text": surface, "reading": reading, "reading_len": len(reading)})
    return cands, no_dash


def pick(cands, seed, k):
    """Selects up to k phrases in the order the seed determines, then restores body order.

    Uses SHA-256(seed:phrase_id) rather than a PRNG, so the result does not depend on the Python
    version or operating system."""
    if len(cands) <= k:
        return list(cands)
    ranked = sorted(cands, key=lambda c: hashlib.sha256(("%d:%s" % (seed, c["phrase_id"])).encode("utf-8")).digest())
    chosen = set(c["phrase_id"] for c in ranked[:k])
    return [c for c in cands if c["phrase_id"] in chosen]


def work_id_for(filename):
    """Derives work_id from the file name (lowercase alphanumeric characters and _, up to 32 characters,
    starting with a lowercase letter or digit)."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    wid = re.sub(r"[^a-z0-9_]+", "_", stem)[:32].strip("_")
    if not WORK_ID_RE.fullmatch(wid):
        raise BuildError("could not derive work_id from the file name (use ASCII alphanumeric characters): %s" % filename, 2)
    return wid


def read_works(src):
    """Reads the *.txt files (cp932) under --src in name order: [(work_id, text)]."""
    if not os.path.isdir(src):
        raise BuildError("--src is not a directory: %s" % src, 2)
    names = sorted(n for n in os.listdir(src) if n.lower().endswith(".txt") and os.path.isfile(os.path.join(src, n)))
    if not names:
        raise BuildError("no .txt files found: %s" % src, 2)
    works = []
    seen = {}
    for n in names:
        wid = work_id_for(n)
        if wid in seen:
            raise BuildError("work_id collision: %s and %s (both map to %s)" % (seen[wid], n, wid), 2)
        seen[wid] = n
        try:
            with open(os.path.join(src, n), "rb") as f:
                raw = f.read()
        except OSError as e:
            raise BuildError("could not read: %s (%s)" % (n, type(e).__name__), 2)
        try:
            works.append((wid, raw.decode("cp932")))
        except UnicodeDecodeError:
            raise BuildError("not readable as cp932: %s" % n, 2)
    return works


def build_bytes(works, seed, tok, min_r, max_r, per_work, log=None):
    """Builds the corpus/1 byte string (UTF-8, LF) from [(work_id, text)]. Validates its own output
    before returning it."""
    lines = [_dumps({"kind": "header", "format": FORMAT, "source": SOURCE,
                     "tokenizer": tok.name, "dict": tok.dict_name})]
    total = 0
    for wid, text in works:
        cands, no_dash = work_candidates(wid, text, tok, min_r, max_r)
        chosen = pick(cands, seed, per_work)
        if log is not None:
            if no_dash:
                log("%s: the dashed-rule frame was not found; discarded everything up to the first blank line" % wid)
            log("%s: %d candidate phrase(s) -> %d selected" % (wid, len(cands), len(chosen)))
        lines.extend(_dumps(c) for c in chosen)
        total += len(chosen)
    if total == 0:
        raise BuildError("no phrase survived (check the source encoding, the tokenizer, and --min-reading / --max-reading)", 1)
    data = ("\n".join(lines) + "\n").encode("utf-8")
    bad = validate_corpus(data)
    if bad:
        raise BuildError("the corpus/1 output failed its own validation (implementation defect): %s"
                         % "; ".join("line %d [%s]" % (ln, code) for ln, code, _ in bad[:5]), 1)
    return data


# ---------------------------------------------------------------- validation (--validate)

class _Reject(ValueError):
    def __init__(self, code, msg):
        ValueError.__init__(self, msg)
        self.code = code


def _no_float(_s):
    raise _Reject("float", "a decimal or exponential number (only integers are allowed)")


def _no_constant(_s):
    raise _Reject("constant", "NaN / Infinity are not valid JSON values")


def _no_dup(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _Reject("dup_key", "the same key appears twice on one line")
        d[k] = v
    return d


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _keys_ok(bad, ln, obj, want):
    ok = True
    for k in want:
        if k not in obj:
            bad(ln, "missing_key", "required key is missing: %s" % k)
            ok = False
    if any(k not in want for k in obj):
        bad(ln, "unknown_key", "an unknown key is present")
        ok = False
    return ok


def _check_header(bad, ln, obj):
    if obj.get("kind") != "header":
        bad(ln, "header", "line 1 is not a header (kind is not header)")
        return
    if not _keys_ok(bad, ln, obj, HEADER_KEYS):
        return
    if obj["format"] != FORMAT:
        bad(ln, "format", "format is not %s" % FORMAT)
    if obj["source"] != SOURCE:
        bad(ln, "source", "source is not %s" % SOURCE)
    for k in ("tokenizer", "dict"):
        if not (isinstance(obj[k], str) and NAME_RE.fullmatch(obj[k])):
            bad(ln, "name", "%s does not match the name-and-version pattern (^[0-9A-Za-z._+-]{1,64}$)" % k)


def _check_phrase(bad, ln, obj, seen):
    kind = obj.get("kind")
    if kind == "header":
        bad(ln, "second_header", "a second header appears")
        return
    if kind != "phrase":
        bad(ln, "kind", "kind is not phrase")
        return
    if not _keys_ok(bad, ln, obj, PHRASE_KEYS):
        return
    for k in ("phrase_id", "work_id", "text", "reading"):
        if not isinstance(obj[k], str):
            bad(ln, "type", "%s is not a string" % k)
            return
    wid = obj["work_id"]
    if not WORK_ID_RE.fullmatch(wid):
        bad(ln, "work_id", "work_id does not match ^[a-z0-9][a-z0-9_]{0,31}$")
    m = PHRASE_ID_RE.fullmatch(obj["phrase_id"])
    if m is None:
        bad(ln, "phrase_id", "phrase_id is not of the form <work_id>-<sentence index>-<phrase index>")
    elif m.group(1) != wid:
        bad(ln, "work_id_mismatch", "the work name embedded in phrase_id differs from work_id")
    if obj["phrase_id"] in seen:
        bad(ln, "dup_id", "phrase_id is duplicated")
    seen.add(obj["phrase_id"])
    if not obj["text"]:
        bad(ln, "text", "text is empty")
    if not READING_RE.fullmatch(obj["reading"]):
        bad(ln, "reading", "reading is not hiragana-only (^[ぁ-ゖー]+$)")
    if not _is_int(obj["reading_len"]):
        bad(ln, "type", "reading_len is not an integer")
    elif obj["reading_len"] != len(obj["reading"]):
        bad(ln, "reading_len", "reading_len does not match the character count of reading")


def validate_corpus(data):
    """Validates corpus/1. Returns a list of (line number, code, description) tuples (empty if valid)."""
    found = []

    def bad(ln, code, msg):
        found.append((ln, code, msg))

    if data.startswith(b"\xef\xbb\xbf"):
        return [(1, "bom", "UTF-8 BOM present (must not be)")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [(0, "encoding", "not readable as UTF-8")]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return [(0, "empty", "empty file (line 1 must be the header)")]
    seen = set()
    phrases = 0
    for ln, raw in enumerate(lines, 1):
        if raw.endswith("\r"):
            raw = raw[:-1]
        try:
            obj = json.loads(raw, object_pairs_hook=_no_dup, parse_float=_no_float, parse_constant=_no_constant)
        except _Reject as e:
            bad(ln, e.code, str(e))
            continue
        except (ValueError, RecursionError):
            bad(ln, "json", "not readable as JSON")
            continue
        if not isinstance(obj, dict):
            bad(ln, "not_object", "not a JSON object")
            continue
        if ln == 1:
            _check_header(bad, ln, obj)
        else:
            phrases += 1
            _check_phrase(bad, ln, obj, seen)
    if phrases == 0:
        bad(len(lines), "no_phrase", "no phrase is present (header only)")
    return found


def cmd_validate(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print("%s: could not open (%s)" % (path, type(e).__name__), file=sys.stderr)
        return 2
    found = validate_corpus(data)
    for ln, code, msg in found[:MAX_SHOWN]:
        print("%s:%d: [%s] %s" % (path, ln, code, msg), file=sys.stderr)
    if len(found) > MAX_SHOWN:
        print("%s: ...and %d more" % (path, len(found) - MAX_SHOWN), file=sys.stderr)
    if found:
        return 1
    print("corpus/1 OK: %s" % path)
    return 0


# ---------------------------------------------------------------- selftest (dictionary-free)

# Lookup table for the table-based tokenizer. A kanji, katakana, digit, or Latin character missing from
# this table stays in the reading (so that clause is dropped).
_TABLE = {
    "今日": "きょう", "青空": "あおぞら", "漢字": "かんじ", "私": "わたし", "朝": "あさ", "雨": "あめ",
    "降": "ふ", "行": "い", "読": "よ", "言": "い", "珈琲": "コーヒー",
}
_RULE = "-" * 55


def _tst_text():
    """A work shaped like Aozora Bunko markup (title, "about these symbols" frame, body, colophon). Lines
    use CRLF."""
    lines = [
        "てすとさくひん", "てすとさくしゃ", "",
        _RULE, "【テキスト中に現れる記号について】", "",
        "《》：ルビ", "（例）漢字《かんじ》", "",
        "｜：ルビの付く文字列の始まりを特定する記号", "（例）｜青空《あおぞら》",
        "これはわくのなかのぶんです。", _RULE, "",
        "［＃ここから見出し］",
        "　今日《こんにち》は｜青空《あおぞら》がきれいですね。",   # 1: ruby (the reading is discarded)
        "漢字を［＃「漢字」に傍点］読みます。",                     # 2: annotation
        "朝、雨が降る、私は行く。",                               # 3: short clauses get merged
        "「今日は青空ですね」と私は言った。",                      # 4: quotation marks
        "珈琲をのみます。",                                      # 5: reading contains katakana
        "今日は3時です。",                                       # 6: reading contains a digit
        "今日はABCです。",                                       # 7: reading contains Latin letters
        "朝です。",                                              # 8: short (`あさです` = 4 characters)
        "あ" * 31 + "。",                                       # 9: long (31 characters)
        "ここではないところへいきたいなあ。",                       # 10: hiragana only
        "わたしは※［＃２行アキ、丸1、1-88-95］行きます。",           # 11: trace of an external-glyph annotation (※) never becomes a phrase
        "", "底本：「テスト」", "これはていほんのぶんです。",
    ]
    return "\r\n".join(lines) + "\r\n"


def _many_text():
    """30 sentences, each with a reading of at least 6 hiragana characters. Exercises K-phrase sampling
    and the effect of the seed."""
    kana = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほま"
    return "\r\n".join("きょうのてんき%sです。" % c for c in kana) + "\r\n"


def _works():
    return [("tst", _tst_text()), ("b_2", _many_text())]


def _build_fx(works=None, seed=1, per_work=100, min_r=DEFAULT_MIN, max_r=DEFAULT_MAX):
    return build_bytes(works if works is not None else [("tst", _tst_text())], seed,
                       TableTokenizer(_TABLE, "0" * 64), min_r, max_r, per_work)


def _rows(data):
    return [json.loads(ln) for ln in data.decode("utf-8").split("\n") if ln]


def _by_id(data):
    return dict((r["phrase_id"], r) for r in _rows(data)[1:])


def _t_annot_ruby():
    got = _by_id(_build_fx())
    r = got.get("tst-1-1")
    if r is None:
        return "a ruby-annotated sentence did not become a phrase"
    if r["text"] != "今日は青空がきれいですね":
        return "ruby markup or ｜ remained in text: %s" % r["text"]
    if r["reading"] != "きょうはあおぞらがきれいですね":
        return "the ruby's reading leaked in (the reading should be assigned by the tokenizer): %s" % r["reading"]
    r = got.get("tst-2-1")
    if r is None or r["text"] != "漢字を読みます" or r["reading"] != "かんじをよみます":
        return "annotation marker ［＃…］ remained, or the phrase was dropped: %s" % (r,)
    for r in got.values():
        if any(c in r["text"] for c in "《》｜［］＃"):
            return "markup characters remained in text: %s" % r["text"]
    return None


def _t_head_foot():
    for r in _by_id(_build_fx()).values():
        for junk in ("ていほん", "わくのなか", "てすとさく"):
            if junk in r["reading"]:
                return "the title, frame, or colophon leaked into a phrase: %s" % r["phrase_id"]
    return None


def _t_same_seed():
    a = _build_fx(_works(), seed=7, per_work=5)
    b = _build_fx(_works(), seed=7, per_work=5)
    return None if a == b else "the output differs for the same input and the same seed"


def _t_seed_changes():
    a = _build_fx(_works(), seed=1, per_work=5)
    b = _build_fx(_works(), seed=2, per_work=5)
    if a == b:
        return "changing the seed did not change the selection (negative control)"
    return None


def _t_dropped():
    got = _by_id(_build_fx())
    for sno in (5, 6, 7, 8, 9):
        if any(k.startswith("tst-%d-" % sno) for k in got):
            return "a phrase whose reading still has katakana, a digit, or a Latin letter, or whose length is out of range, survived (sentence %d)" % sno
    for r in got.values():
        if not READING_RE.fullmatch(r["reading"]):
            return "a non-hiragana reading survived: %s" % r["phrase_id"]
    return None


def _t_length_range():
    got = _by_id(_build_fx())
    for r in got.values():
        if not DEFAULT_MIN <= r["reading_len"] <= DEFAULT_MAX or r["reading_len"] != len(r["reading"]):
            return "length out of range, or reading_len mismatch: %s" % r["phrase_id"]
    # Negative control: widening the range lets a previously dropped phrase back in (confirms the range
    # was the reason it was dropped).
    if "tst-8-1" not in _by_id(_build_fx(min_r=3)):
        return "lowering --min-reading did not let the short phrase (あさです) in"
    long_ = _by_id(_build_fx(max_r=40)).get("tst-9-1")
    if long_ is None or long_["reading_len"] != 31:
        return "raising --max-reading did not let the long phrase (31 characters) in"
    return None


def _t_merge():
    got = _by_id(_build_fx())
    a, b = got.get("tst-3-1"), got.get("tst-3-2")
    if a is None or a["text"] != "朝、雨が降る" or a["reading"] != "あさあめがふる":
        return "a short clause did not merge with its neighbor: %s" % (a,)
    if b is None or b["text"] != "私は行く" or b["reading"] != "わたしはいく":
        return "a clause that reached the minimum did not become its own phrase: %s" % (b,)
    return None


def _t_quotes():
    r = _by_id(_build_fx()).get("tst-4-1")
    if r is None or r["text"] != "今日は青空ですねと私は言った" or r["reading"] != "きょうはあおぞらですねとわたしはいった":
        return "quotation marks 「」 were not stripped from the phrase: %s" % (r,)
    return None


def _t_ids():
    want = ["tst-1-1", "tst-2-1", "tst-3-1", "tst-3-2", "tst-4-1", "tst-10-1"]
    got = [r["phrase_id"] for r in _rows(_build_fx())[1:]]
    return None if got == want else "phrase_id is not determined entirely by the input: %s" % got


def _t_per_work():
    data = _build_fx(_works(), seed=1, per_work=7)
    ids = [r["phrase_id"] for r in _rows(data)[1:]]
    n_b = len([i for i in ids if i.startswith("b_2-")])
    n_t = len([i for i in ids if i.startswith("tst-")])
    if n_b != 7:
        return "work b_2 has 30 candidates, so K = 7 phrases should be selected (got %d)" % n_b
    if n_t != 6:
        return "work tst has 6 candidates (at or below K), so all should be kept (got %d)" % n_t
    key = lambda i: tuple(int(x) for x in i.split("-")[-2:])
    if [i for i in ids if i.startswith("b_2-")] != sorted([i for i in ids if i.startswith("b_2-")], key=key):
        return "the selected phrases are not in body order"
    return None


def _t_header():
    rows = _rows(_build_fx())
    want = {"kind": "header", "format": "corpus/1", "source": "aozora", "tokenizer": "table", "dict": "0" * 64}
    return None if rows[0] == want else "header does not match: %s" % rows[0]


def _t_gaiji_trace():
    """Confirms in the implementation the claim documented in the README ("a clause carrying the trace
    of an external-glyph annotation is dropped")."""
    got = _by_id(_build_fx())
    if any(k.startswith("tst-11-") for k in got):
        return "a clause containing the trace of an external-glyph annotation (※) survived as a phrase"
    for r in got.values():
        if "※" in r["text"] or "※" in r["reading"]:
            return "※ leaked into a phrase: %s" % r["phrase_id"]
    return None


def _t_text_charclass():
    """Character-class filtering on the text side: a phrase is dropped even when the table tokenizer
    assigns a reading to a full-width digit, as long as the digit remains in text. The negative control
    changes the same structure to a kanji instead and confirms the phrase survives (showing that the
    text-side check, not something else, is responsible)."""
    tok = TableTokenizer({"３": "さん"}, "0" * 64)
    dropped = phrases_of("いまは３じです", tok, DEFAULT_MIN, DEFAULT_MAX)
    if dropped:
        return "a full-width digit (３) with a reading was not dropped by the text-side character-class filter: %s" % (dropped,)
    tok2 = TableTokenizer({"並": "なみ"}, "0" * 64)
    kept = phrases_of("いまは並じです", tok2, DEFAULT_MIN, DEFAULT_MAX)
    if not kept:
        return "the negative control (kanji-only text) did not survive as a phrase (cannot confirm the range was not the reason)"
    if kept[0][0] != "いまは並じです":
        return "the negative control's text changed: %s" % (kept,)
    return None


def _t_old_kana():
    """A phrase whose reading contains historical kana (`ゐ・ゑ・ゕ・ゖ`) is dropped. The negative control
    uses the same structure with an ordinary reading and confirms it survives."""
    tok = TableTokenizer({"旧": "ゐ"}, "0" * 64)
    dropped = phrases_of("これはとても旧いはなしです", tok, DEFAULT_MIN, DEFAULT_MAX)
    if dropped:
        return "a phrase whose reading contains historical kana (ゐ) survived: %s" % (dropped,)
    tok2 = TableTokenizer({"旧": "きゅう"}, "0" * 64)
    kept = phrases_of("これはとても旧いはなしです", tok2, DEFAULT_MIN, DEFAULT_MAX)
    if not kept:
        return "the negative control (a reading without historical kana) did not survive as a phrase"
    return None


def _t_self_valid():
    bad = validate_corpus(_build_fx(_works(), seed=3, per_work=5))
    return None if not bad else "the generated output did not pass --validate: %s" % bad[:3]


def _t_sudachi_fake():
    """Exercises the SudachiTokenizer code path against a fake sudachipy module (injected into
    sys.modules). This is not the real dictionary."""
    from importlib import metadata

    class Morph(object):
        def __init__(self, s, r):
            self._s, self._r = s, r

        def surface(self):
            return self._s

        def reading_form(self):
            return self._r

    table = {
        "青空がきれい": [("青空", "アオゾラ"), ("が", "ガ"), ("きれい", "キレイ")],
        "ヴァイオリンをひく": [("ヴァイオリン", "ヴァイオリン"), ("を", "ヲ"), ("ひく", "ヒク")],
        "※です": [("※", ""), ("です", "デス")],
    }
    seen_modes = []

    class FakeTokenizer(object):
        def tokenize(self, text, mode):
            seen_modes.append(mode)
            return [Morph(s, r) for s, r in table[text]]

    fake = types.ModuleType("sudachipy")
    fake.dictionary = types.SimpleNamespace(Dictionary=lambda: types.SimpleNamespace(create=FakeTokenizer))
    fake.tokenizer = types.SimpleNamespace(Tokenizer=types.SimpleNamespace(SplitMode=types.SimpleNamespace(C="MODE_C")))
    versions = {"SudachiPy": "0.6.8", "SudachiDict-core": "20240109"}

    def fake_version(name):
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    old_mod = sys.modules.get("sudachipy")
    old_ver = metadata.version
    sys.modules["sudachipy"] = fake
    metadata.version = fake_version
    try:
        tok = SudachiTokenizer()
        if (tok.name, tok.dict_name) != ("sudachipy-0.6.8", "sudachidict_core-20240109"):
            return "name or version mismatch: %s / %s" % (tok.name, tok.dict_name)
        if tok.read("青空がきれい") != "あおぞらがきれい":
            return "katakana reading was not converted to hiragana: %s" % tok.read("青空がきれい")
        if tok.read("ヴァイオリンをひく") != "ゔぁいおりんをひく":
            return "vu and small katakana were not converted to hiragana"
        if READING_RE.fullmatch(tok.read("※です")):
            return "an unknown word with an empty reading (※) did not fall back to its surface form, and a phrase was built from it"
        if set(seen_modes) != {"MODE_C"}:
            return "split mode is not C: %s" % sorted(set(seen_modes))
    finally:
        metadata.version = old_ver
        if old_mod is None:
            sys.modules.pop("sudachipy", None)
        else:
            sys.modules["sudachipy"] = old_mod
    return None


def _run_self(args, env_extra=None):
    env = dict(os.environ, PYTHONUTF8="1")
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, os.path.abspath(__file__)] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _t_cli():
    """Reads cp932 files under --src and writes the output through the CLI. Confirms it is byte-identical
    to the in-process result and that --validate exits 0."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        for name, text in (("Tst.txt", _tst_text()), ("b-2.txt", _many_text())):
            with open(os.path.join(src, name), "wb") as f:
                f.write(text.encode("cp932"))
        raw_table = _dumps(_TABLE).encode("utf-8")
        table = os.path.join(tmp, "table.json")
        with open(table, "wb") as f:
            f.write(raw_table)
        out = os.path.join(tmp, "out.jsonl")
        rc, text = _run_self(["--src", src, "--out", out, "--seed", "5", "--tokenizer", "table:" + table,
                              "--per-work", "5"])
        if rc != 0:
            return "the CLI write-out did not exit 0 (%d): %s" % (rc, text.strip()[-200:])
        with open(out, "rb") as f:
            got = f.read()
        want = build_bytes(_works(), 5, TableTokenizer(_TABLE, hashlib.sha256(raw_table).hexdigest()),
                           DEFAULT_MIN, DEFAULT_MAX, 5)
        if got != want:
            return "the CLI output is not byte-identical to the in-process output (cp932 / CRLF / work name handling)"
        if b"\r" in got or got.startswith(b"\xef\xbb\xbf"):
            return "the output contains a CR or a BOM"
        if _rows(got)[0]["dict"] != hashlib.sha256(raw_table).hexdigest():
            return "the table header's dict is not the table's SHA-256"
        rc, text = _run_self(["--validate", out])
        if rc != 0:
            return "--validate did not exit 0 for a correct output (%d): %s" % (rc, text.strip()[-200:])
        with open(out, "wb") as f:
            f.write(got.replace(b'"reading":"', '"reading":"亜'.encode("utf-8"), 1))
        rc, text = _run_self(["--validate", out])
        if rc != 1 or "Traceback" in text:
            return "an output with a kanji character injected into a reading was not rejected with exit 1 (%d)" % rc
        rc, text = _run_self(["--validate", os.path.join(tmp, "missing.jsonl")])
        if rc != 2:
            return "--validate on a nonexistent file did not exit 2 (%d)" % rc
        empty = os.path.join(tmp, "empty")
        os.mkdir(empty)
        rc, text = _run_self(["--src", empty, "--out", out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 2:
            return "--src with no .txt files did not exit 2 (%d)" % rc
        rc, text = _run_self(["--src", src, "--out", out, "--tokenizer", "table:" + table])
        if rc != 2:
            return "a missing --seed did not exit 2 (%d)" % rc
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_no_sudachi():
    """On a machine where sudachipy cannot be imported, --tokenizer sudachi stops with exit 2 rather
    than a traceback."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        fake = os.path.join(tmp, "fake")
        src = os.path.join(tmp, "src")
        os.mkdir(fake)
        os.mkdir(src)
        with open(os.path.join(fake, "sudachipy.py"), "w") as f:
            f.write('raise ImportError("sudachipy is not available in this test")\n')
        with open(os.path.join(src, "tst.txt"), "wb") as f:
            f.write(_tst_text().encode("cp932"))
        rc, text = _run_self(["--src", src, "--out", os.path.join(tmp, "o.jsonl"), "--seed", "1",
                              "--tokenizer", "sudachi"], {"PYTHONPATH": fake})
        if rc != 2 or "Traceback" in text or "sudachipy" not in text:
            return "missing sudachipy did not stop with exit 2 and an explanation (%d): %s" % (rc, text.strip()[-200:])
        if os.path.exists(os.path.join(tmp, "o.jsonl")):
            return "an output file was created even though the run stopped"
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_no_dash_warns():
    """A work with no frame of two dashed rules discards everything up to the first blank line, and the
    CLI prints a warning to standard error. Negative control: an ordinary work with a frame prints no such
    warning."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        table = os.path.join(tmp, "table.json")
        with open(table, "wb") as f:
            f.write(_dumps(_TABLE).encode("utf-8"))
        text = "\r\n".join(["だいめい", "さくしゃ", "", "ここではないところへいきたいなあ。"]) + "\r\n"
        with open(os.path.join(src, "nodash.txt"), "wb") as f:
            f.write(text.encode("cp932"))
        out = os.path.join(tmp, "out.jsonl")
        rc, log_text = _run_self(["--src", src, "--out", out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 0:
            return "writing out a work with no dashed-rule frame did not exit 0 (%d): %s" % (rc, log_text.strip()[-200:])
        with open(out, "rb") as f:
            got = f.read()
        for r in _rows(got)[1:]:
            if "だいめい" in r["reading"] or "さくしゃ" in r["reading"]:
                return "the title or author leaked into a phrase despite no frame: %s" % r["phrase_id"]
        if "dashed-rule" not in log_text:
            return "the missing-frame warning was not printed to standard error: %s" % log_text.strip()[-200:]
        src2 = os.path.join(tmp, "src2")
        os.mkdir(src2)
        with open(os.path.join(src2, "tst.txt"), "wb") as f:
            f.write(_tst_text().encode("cp932"))
        rc, log_text2 = _run_self(["--src", src2, "--out", out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 0:
            return "writing out an ordinary work with a frame did not exit 0 (negative control, %d): %s" % (rc, log_text2.strip()[-200:])
        if "dashed-rule" in log_text2:
            return "the warning was printed for an ordinary work with a frame (negative control): %s" % log_text2.strip()[-200:]
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_out_dir_missing():
    """When the directory for --out cannot be created, exit 2 rather than a traceback. Negative control:
    an ordinary --out still exits 0."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        with open(os.path.join(src, "tst.txt"), "wb") as f:
            f.write(_tst_text().encode("cp932"))
        table = os.path.join(tmp, "table.json")
        with open(table, "wb") as f:
            f.write(_dumps(_TABLE).encode("utf-8"))
        bad_out = os.path.join(tmp, "no_such_dir", "out.jsonl")
        rc, text = _run_self(["--src", src, "--out", bad_out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 2 or "Traceback" in text:
            return "a missing parent directory for --out did not stop with exit 2 (%d): %s" % (rc, text.strip()[-300:])
        good_out = os.path.join(tmp, "out.jsonl")
        rc, text = _run_self(["--src", src, "--out", good_out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 0:
            return "an ordinary --out did not exit 0 (negative control, %d): %s" % (rc, text.strip()[-200:])
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_table_recursion():
    """A too-deeply-nested (malformed) table JSON, which raises RecursionError, exits 2 like any other
    unreadable input. Negative control: an ordinary table exits 0."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        with open(os.path.join(src, "tst.txt"), "wb") as f:
            f.write(_tst_text().encode("cp932"))
        deep = os.path.join(tmp, "deep.json")
        with open(deep, "wb") as f:
            f.write(("[" * 5000).encode("utf-8"))
        out = os.path.join(tmp, "out.jsonl")
        rc, text = _run_self(["--src", src, "--out", out, "--seed", "1", "--tokenizer", "table:" + deep])
        if rc != 2 or "Traceback" in text:
            return "a too-deeply-nested table JSON did not stop with exit 2 (%d): %s" % (rc, text.strip()[-300:])
        table = os.path.join(tmp, "table.json")
        with open(table, "wb") as f:
            f.write(_dumps(_TABLE).encode("utf-8"))
        rc, text = _run_self(["--src", src, "--out", out, "--seed", "1", "--tokenizer", "table:" + table])
        if rc != 0:
            return "an ordinary table did not exit 0 (negative control, %d): %s" % (rc, text.strip()[-200:])
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _t_src_read_oserror():
    """When a --src file cannot be read (OSError), the run raises BuildError (exit 2) rather than
    propagating a traceback. Negative control: with the patch removed, the same directory reads normally."""
    tmp = tempfile.mkdtemp(prefix="build_corpus_selftest_")
    try:
        src = os.path.join(tmp, "src")
        os.mkdir(src)
        with open(os.path.join(src, "tst.txt"), "wb") as f:
            f.write(_tst_text().encode("cp932"))
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if os.path.basename(str(path)) == "tst.txt":
                raise PermissionError("simulated: %s" % path)
            return real_open(path, *a, **kw)

        globals()["open"] = fake_open
        try:
            read_works(src)
            return "an OSError did not raise an exception"
        except BuildError as e:
            if e.code != 2:
                return "BuildError.code is not 2: %s" % e.code
        except OSError:
            return "the OSError propagated unconverted, instead of becoming a BuildError"
        finally:
            globals()["open"] = real_open
        works = read_works(src)
        if not works or works[0][0] != "tst":
            return "the directory does not read normally after the patch is removed (negative control): %s" % (works,)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _dump_lines(*objs):
    return "\n".join(_dumps(o) if isinstance(o, dict) else o for o in objs) + "\n"


def _hdr(**over):
    return dict({"kind": "header", "format": "corpus/1", "source": "aozora", "tokenizer": "table", "dict": "none"}, **over)


def _ph(pid="w1-1-1", **over):
    base = {"kind": "phrase", "phrase_id": pid, "work_id": "w1", "text": "青空", "reading": "あおぞら", "reading_len": 4}
    return dict(base, **over)


def _without(obj, key):
    return dict((k, v) for k, v in obj.items() if k != key)


def _validate_cases():
    """(name, content, expected violation code). An expectation of None means the content should be
    accepted."""
    good = _dump_lines(_hdr(), _ph("w1-1-1"), _ph("w1-1-2", reading="ながいーよみ", reading_len=6))
    return [
        ("valid corpus", good, None),
        ("valid corpus (CRLF)", good.replace("\n", "\r\n"), None),
        ("reading contains kanji", _dump_lines(_hdr(), _ph(reading="青空", reading_len=2)), "reading"),
        ("reading contains katakana", _dump_lines(_hdr(), _ph(reading="アオゾラ", reading_len=4)), "reading"),
        ("reading contains a digit", _dump_lines(_hdr(), _ph(reading="あお3", reading_len=3)), "reading"),
        ("reading contains a Latin letter", _dump_lines(_hdr(), _ph(reading="あおA", reading_len=3)), "reading"),
        ("reading is empty", _dump_lines(_hdr(), _ph(reading="", reading_len=0)), "reading"),
        ("reading_len mismatch", _dump_lines(_hdr(), _ph(reading_len=5)), "reading_len"),
        ("reading_len is a string", _dump_lines(_hdr(), _ph(reading_len="4")), "type"),
        ("reading_len is a boolean", _dump_lines(_hdr(), _ph(reading_len=True)), "type"),
        ("reading_len is a decimal", _dump_lines(_hdr(), '{"kind":"phrase","phrase_id":"w1-1-1","work_id":"w1","text":"青空",'
                                          '"reading":"あおぞら","reading_len":4.0}'), "float"),
        ("duplicate phrase_id", _dump_lines(_hdr(), _ph("w1-1-1"), _ph("w1-1-1")), "dup_id"),
        ("malformed phrase_id (uppercase)", _dump_lines(_hdr(), _ph("X001-1-1", work_id="X001")), "phrase_id"),
        ("malformed phrase_id (no index numbers)", _dump_lines(_hdr(), _ph("w1")), "phrase_id"),
        ("phrase_id's work name differs from work_id", _dump_lines(_hdr(), _ph("w2-1-1")), "work_id_mismatch"),
        ("work_id is uppercase", _dump_lines(_hdr(), _ph("W1-1-1", work_id="W1")), "work_id"),
        ("text is empty", _dump_lines(_hdr(), _ph(text="")), "text"),
        ("no header (line 1 is a phrase)", _dump_lines(_ph("w1-1-1")), "header"),
        ("format mismatch", _dump_lines(_hdr(format="corpus/2"), _ph()), "format"),
        ("source mismatch", _dump_lines(_hdr(source="other"), _ph()), "source"),
        ("tokenizer contains whitespace", _dump_lines(_hdr(tokenizer="sudachi py"), _ph()), "name"),
        ("dict is 65 characters", _dump_lines(_hdr(dict="a" * 65), _ph()), "name"),
        ("unknown key", _dump_lines(_hdr(), _ph(extra=1)), "unknown_key"),
        ("required key missing", _dump_lines(_hdr(), _without(_ph(), "work_id")), "missing_key"),
        ("same key twice", _dump_lines(_hdr(), '{"kind":"phrase","kind":"phrase","phrase_id":"w1-1-1","work_id":"w1",'
                                          '"text":"青空","reading":"あおぞら","reading_len":4}'), "dup_key"),
        ("kind mismatch", _dump_lines(_hdr(), _ph(kind="sentence")), "kind"),
        ("two headers", _dump_lines(_hdr(), _ph(), _hdr()), "second_header"),
        ("zero phrases", _dump_lines(_hdr()), "no_phrase"),
        ("empty file", "", "empty"),
        ("blank line in the middle", _dump_lines(_hdr(), "", _ph()), "json"),
        ("malformed JSON", _dump_lines(_hdr(), '{"kind":"phrase",'), "json"),
        ("line is an array", _dump_lines(_hdr(), "[1]"), "not_object"),
        ("has a BOM", b"\xef\xbb\xbf" + good.encode("utf-8"), "bom"),
        ("not UTF-8 (cp932)", "あおぞら".encode("cp932"), "encoding"),
    ]


def _run_validate_cases(fails):
    n = 0
    for name, body, want in _validate_cases():
        n += 1
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        codes = set(c for _, c, _ in validate_corpus(data))
        if want is None:
            if codes:
                fails.append("validate \"%s\": should be accepted but was rejected: %s" % (name, sorted(codes)))
        elif not codes:
            fails.append("validate \"%s\": should be rejected but was accepted" % name)
        elif want not in codes:
            fails.append("validate \"%s\": rejected for a different reason (expected %s, got %s)" % (name, want, sorted(codes)))
    # Confirms every key's absence is rejected (guards against a gap in the required-key table).
    for k in HEADER_KEYS[1:]:
        n += 1
        if "missing_key" not in set(c for _, c, _ in validate_corpus(_dump_lines(_without(_hdr(), k), _ph()).encode("utf-8"))):
            fails.append("validate: a missing required header key %s is not rejected" % k)
    for k in PHRASE_KEYS[1:]:
        n += 1
        if "missing_key" not in set(c for _, c, _ in validate_corpus(_dump_lines(_hdr(), _without(_ph(), k)).encode("utf-8"))):
            fails.append("validate: a missing required phrase key %s is not rejected" % k)
    return n


def _cases():
    return [
        ("annotations and ruby are removed", _t_annot_ruby),
        ("the colophon, the symbols frame, and the title are removed", _t_head_foot),
        ("same seed yields byte-identical output", _t_same_seed),
        ("a different seed changes the output", _t_seed_changes),
        ("phrases with katakana, a digit, or a Latin letter in the reading, or an out-of-range length, are dropped", _t_dropped),
        ("reading length stays within min-max; widening the range changes the selection", _t_length_range),
        ("short clauses are merged with a neighbor into one phrase", _t_merge),
        ("quotation marks 「」 are stripped when forming a phrase", _t_quotes),
        ("phrase_id is determined entirely by the input", _t_ids),
        ("up to K phrases per work, in body order", _t_per_work),
        ("the header records tokenizer and dict", _t_header),
        ("a clause carrying the trace of an external-glyph annotation (※) does not become a phrase", _t_gaiji_trace),
        ("text-side character-class filtering: a phrase is dropped even if a full-width digit has a reading", _t_text_charclass),
        ("a phrase whose reading contains historical kana (ゐ・ゑ・ゕ・ゖ) is dropped", _t_old_kana),
        ("the generated output passes --validate", _t_self_valid),
        ("Sudachi readings are converted to hiragana (fake sudachipy)", _t_sudachi_fake),
        ("cp932 files are written out through the CLI", _t_cli),
        ("exit 2 when sudachipy is unavailable", _t_no_sudachi),
        ("a work with no dashed-rule frame is truncated to the first blank line, with a warning", _t_no_dash_warns),
        ("exit 2 when the directory for --out cannot be created", _t_out_dir_missing),
        ("exit 2 for a too-deeply-nested table JSON (RecursionError)", _t_table_recursion),
        ("exit 2 when a --src file cannot be read (OSError)", _t_src_read_oserror),
    ]


def selftest():
    fails = []
    n = 0
    for name, fn in _cases():
        n += 1
        try:
            msg = fn()
        except Exception as e:
            msg = "exception: %s: %s" % (type(e).__name__, e)
        if msg:
            fails.append("%s: %s" % (name, msg))
    n += _run_validate_cases(fails)
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
    ap = argparse.ArgumentParser(description="build corpus/1 (Aozora Bunko phrases and readings) from source text")
    ap.add_argument("--src", help="directory of Aozora Bunko .txt files (cp932)")
    ap.add_argument("--out", help="output path for corpus/1")
    ap.add_argument("--seed", type=int, help="seed controlling the per-work selection (integer >= 0)")
    ap.add_argument("--tokenizer", default="sudachi", help="sudachi (default) or table:<json>")
    ap.add_argument("--min-reading", type=int, default=DEFAULT_MIN, help="minimum reading length for a phrase (default %d)" % DEFAULT_MIN)
    ap.add_argument("--max-reading", type=int, default=DEFAULT_MAX, help="maximum reading length for a phrase (default %d)" % DEFAULT_MAX)
    ap.add_argument("--per-work", type=int, default=DEFAULT_PER_WORK, help="maximum phrases per work (default %d)" % DEFAULT_PER_WORK)
    ap.add_argument("--validate", metavar="FILE", help="validate a corpus/1 file (exit 1 on violation)")
    ap.add_argument("--selftest", action="store_true", help="run the dictionary-free self-check")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.validate:
        return cmd_validate(args.validate)
    missing = [n for n in ("src", "out", "seed") if getattr(args, n) is None]
    if missing:
        ap.error("writing out requires --src --out --seed (missing: %s)" % " ".join("--" + m for m in missing))
    if args.seed < 0:
        ap.error("--seed must be 0 or greater")
    if args.min_reading < 1 or args.max_reading < args.min_reading or args.per_work < 1:
        ap.error("--min-reading must be >= 1, --max-reading must be >= --min-reading, --per-work must be >= 1")
    try:
        tok = make_tokenizer(args.tokenizer)
        works = read_works(args.src)
        data = build_bytes(works, args.seed, tok, args.min_reading, args.max_reading, args.per_work,
                           log=lambda m: print(m, file=sys.stderr))
    except BuildError as e:
        print("build_corpus.py: %s" % e, file=sys.stderr)
        return e.code
    try:
        with open(args.out, "wb") as f:
            f.write(data)
    except OSError as e:
        print("build_corpus.py: could not write --out: %s (%s)" % (args.out, type(e).__name__), file=sys.stderr)
        return 2
    print("wrote: %s (tokenizer=%s dict=%s)" % (args.out, tok.name, tok.dict_name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
