#!/usr/bin/env python3
# kit: selftest
"""Hiragana-to-key-sequence conversion (a list of keynames). Python standard library only.

  romaji.py --keys 'HIRAGANA'   print the key sequence, space-separated (e.g. `がっこう` -> `g a k k o u`)
  romaji.py --selftest            table coverage, uniqueness (round-trips through the inverse mapping),
                                   and known examples (fast default, under a second)

A single deterministic table (close to Hepburn romanization). Conventions:
  - `ん` is always `nn`.
  - The geminate consonant `っ` doubles the following consonant. When the next sound is a vowel, `ん`, a
    small kana (spelled with an `x` prefix), a symbol, or the end of the phrase, it becomes `xtu` instead.
    `n` is never doubled (`nna` would spell `んあ`, not `っな`).
  - A small kana (`ぁぃぅぇぉゃゅょゎゕゖ`) is spelled with an `x` prefix as a single character, unless it
    forms a contracted sound (such as `きゃ` or `しゃ`, or one of the `ふ`-row combinations listed below).
  - `ー` maps to minus, `、` to comma, `。` to period.
  - How a real IME interprets this spelling can vary by OS and IME; the procedures are checked and revised
    against real hardware during a calibration pilot (editing this table immediately shows up as a failure
    in the reverse-mapping uniqueness selftest).
"""
import argparse
import random
import sys

# Single characters (the 86 characters from `ぁ` to `ゖ`, plus `ー` and `、。`). Symbols map to a single
# internal character (`ー` to a hyphen, `、` to a comma, `。` to a period).
_SINGLE = {
    "ぁ": "xa", "あ": "a", "ぃ": "xi", "い": "i", "ぅ": "xu", "う": "u", "ぇ": "xe", "え": "e", "ぉ": "xo", "お": "o",
    "か": "ka", "が": "ga", "き": "ki", "ぎ": "gi", "く": "ku", "ぐ": "gu", "け": "ke", "げ": "ge", "こ": "ko", "ご": "go",
    "さ": "sa", "ざ": "za", "し": "shi", "じ": "ji", "す": "su", "ず": "zu", "せ": "se", "ぜ": "ze", "そ": "so", "ぞ": "zo",
    "た": "ta", "だ": "da", "ち": "chi", "ぢ": "di", "っ": "xtu", "つ": "tsu", "づ": "du", "て": "te", "で": "de",
    "と": "to", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ば": "ba", "ぱ": "pa", "ひ": "hi", "び": "bi", "ぴ": "pi", "ふ": "fu", "ぶ": "bu", "ぷ": "pu",
    "へ": "he", "べ": "be", "ぺ": "pe", "ほ": "ho", "ぼ": "bo", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "ゃ": "xya", "や": "ya", "ゅ": "xyu", "ゆ": "yu", "ょ": "xyo", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "ゎ": "xwa", "わ": "wa", "ゐ": "wi", "ゑ": "we", "を": "wo", "ん": "nn",
    "ゔ": "vu", "ゕ": "xka", "ゖ": "xke",
    "ー": "-", "、": ",", "。": ".",
}

# Two-character combinations (contracted sounds and the `ふ` row). A "kana + small kana" pair not listed
# here falls back to being spelled one character at a time (the small kana with its x prefix).
_DIGRAPH = {}
for _base, _cons in (("き", "ky"), ("ぎ", "gy"), ("に", "ny"), ("ひ", "hy"), ("び", "by"), ("ぴ", "py"),
                     ("み", "my"), ("り", "ry"), ("ぢ", "dy"), ("し", "sh"), ("ち", "ch"), ("じ", "j")):
    for _small, _vowel in (("ゃ", "a"), ("ゅ", "u"), ("ょ", "o")):
        _DIGRAPH[_base + _small] = _cons + _vowel
for _small, _vowel in (("ぁ", "a"), ("ぃ", "i"), ("ぇ", "e"), ("ぉ", "o")):
    _DIGRAPH["ふ" + _small] = "f" + _vowel

_REV = {}
for _k, _r in list(_SINGLE.items()) + list(_DIGRAPH.items()):
    _REV[_r] = _k
_MAXLEN = max(len(r) for r in _REV)

# Leading characters the geminate consonant may double (vowels, n, x, and symbols are never doubled)
_DOUBLABLE = frozenset("bcdfghjklmpqrstvwyz")

_SYMBOL_TO_KEY = {"-": "minus", ",": "comma", ".": "period"}
_KEY_TO_SYMBOL = dict((v, k) for k, v in _SYMBOL_TO_KEY.items())

# The hiragana range (`ぁ` U+3041 to `ゖ` U+3096)
HIRAGANA_RANGE = (0x3041, 0x3096)


def _tokens(hira):
    """Hiragana string -> [(kana, internal romanization)]. A character missing from the table raises
    ValueError."""
    out = []
    i = 0
    while i < len(hira):
        two = hira[i:i + 2]
        if len(two) == 2 and two in _DIGRAPH:
            out.append((two, _DIGRAPH[two]))
            i += 2
            continue
        c = hira[i]
        if c not in _SINGLE:
            raise ValueError("character not in the table (U+%04X)" % ord(c))
        out.append((c, _SINGLE[c]))
        i += 1
    return out


def to_romaji(hira):
    """Hiragana -> internal romanization (symbols become - , .)."""
    toks = _tokens(hira)
    parts = []
    for idx, (kana, rom) in enumerate(toks):
        if kana == "っ":
            nxt = toks[idx + 1][1] if idx + 1 < len(toks) else ""
            parts.append(nxt[0] if nxt and nxt[0] in _DOUBLABLE else rom)
        else:
            parts.append(rom)
    return "".join(parts)


def to_keys(hira):
    """Hiragana -> a list of keynames (only a-z and minus / comma / period ever appear)."""
    return [_SYMBOL_TO_KEY.get(ch, ch) for ch in to_romaji(hira)]


def _match(s, i):
    for length in range(_MAXLEN, 0, -1):
        tok = s[i:i + length]
        if len(tok) == length and tok in _REV:
            return tok
    return None


def from_romaji(s):
    """Internal romanization -> hiragana (the inverse mapping, longest match). A sequence that cannot be
    spelled from the table raises ValueError."""
    out = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i] == s[i + 1] and s[i] in _DOUBLABLE and _match(s, i + 1) is not None:
            out.append("っ")
            i += 1
            continue
        tok = _match(s, i)
        if tok is None:
            raise ValueError("not readable as the romanization (position %d)" % i)
        out.append(_REV[tok])
        i += len(tok)
    return "".join(out)


def from_keys(keys):
    """A list of keynames -> hiragana."""
    chars = []
    for k in keys:
        if k in _KEY_TO_SYMBOL:
            chars.append(_KEY_TO_SYMBOL[k])
        elif len(k) == 1 and "a" <= k <= "z":
            chars.append(k)
        else:
            raise ValueError("not a keyname used for typing a reading")
    return from_romaji("".join(chars))


ALPHABET = tuple(chr(c) for c in range(HIRAGANA_RANGE[0], HIRAGANA_RANGE[1] + 1)) + ("ー", "、", "。")


# ---------------------------------------------------------------- selftest

def selftest():
    fails = []
    n = 0

    def check(name, cond, detail=""):
        nonlocal n
        n += 1
        if not cond:
            fails.append(name + (": " + detail if detail else ""))

    # 1. Coverage: every character from `ぁ` to `ゖ`, plus `ー、。`, has a mapping
    for ch in ALPHABET:
        check("coverage U+%04X" % ord(ch), ch in _SINGLE)
    check("the table has no extra single character", set(_SINGLE) == set(ALPHABET), str(sorted(set(_SINGLE) ^ set(ALPHABET))))

    # 2. Table uniqueness: no spelling is assigned to two different entries
    values = list(_SINGLE.values()) + list(_DIGRAPH.values())
    check("no duplicate spellings", len(values) == len(set(values)))
    for kana, rom in list(_SINGLE.items()) + list(_DIGRAPH.items()):
        check("the inverse table round-trips %s" % kana, _REV[rom] == kana)
    # Only a-z and minus / comma / period ever appear as keys
    allowed = set("abcdefghijklmnopqrstuvwxyz") | {"minus", "comma", "period"}
    for ch in ALPHABET:
        check("keys stay within range U+%04X" % ord(ch), set(to_keys(ch)) <= allowed)

    # 3. Known examples (pins the deterministic table)
    known = [
        ("がっこう", "g a k k o u"), ("しゃしん", "s h a s h i n n"), ("きょう", "k y o u"),
        ("ん", "n n"), ("あっ", "a x t u"), ("いっしょ", "i s s h o"), ("らーめん", "r a minus m e n n"),
        ("っ", "x t u"), ("んな", "n n n a"), ("っん", "x t u n n"), ("っな", "x t u n a"),
        ("っっか", "x t u k k a"), ("ふぁ", "f a"), ("てぃ", "t e x i"), ("ゔ", "v u"), ("ちょっと", "c h o t t o"),
        ("ー", "minus"), ("、。", "comma period"), ("ぁ", "x a"), ("っち", "c c h i"), ("っつ", "t t s u"),
        ("にゃ", "n y a"), ("んや", "n n y a"), ("じゃ", "j a"), ("ぢゃ", "d y a"), ("ゎ", "x w a"),
    ]
    for hira, want in known:
        got = " ".join(to_keys(hira))
        check("known example %s" % hira, got == want, "expected %s, got %s" % (want, got))

    # 4. All-pairs round trip (every pair of adjacent characters maps back to the original hiragana)
    bad = 0
    for a in ALPHABET:
        for b in ALPHABET:
            s = a + b
            try:
                back = from_keys(to_keys(s))
            except ValueError:
                back = None
            if back != s:
                bad += 1
                if bad <= 5:
                    fails.append("pair round trip %s -> %s" % (s, back))
    n += len(ALPHABET) ** 2
    # 5. Random round trips of 3-8 characters (fixed seed, using only random())
    rng = random.Random(1)
    for _ in range(3000):
        k = 3 + int(rng.random() * 6)
        s = "".join(ALPHABET[int(rng.random() * len(ALPHABET))] for _ in range(k))
        try:
            back = from_keys(to_keys(s))
        except ValueError:
            back = None
        n += 1
        if back != s:
            fails.append("random round trip %s -> %s" % (s, back))
            break

    # 6. A character missing from the table, or a sequence unreadable as the romanization, raises
    for name, fn in (("kanji", lambda: to_keys("漢")), ("katakana", lambda: to_keys("ア")),
                     ("a Latin character", lambda: to_keys("a")), ("an isolated consonant", lambda: from_romaji("k")),
                     ("a keyname not used for typing a reading", lambda: from_keys(["space"])), ("an unknown spelling", lambda: from_romaji("qa"))):
        n += 1
        try:
            fn()
            fails.append("did not raise: " + name)
        except ValueError:
            pass

    if fails:
        for m in fails:
            print("FAIL " + m)
        print("selftest failed: %d / %d" % (len(fails), n))
        return 1
    print("selftest OK (%d cases)" % n)
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="hiragana to romanized key sequence")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--keys", metavar="HIRAGANA")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.keys is None:
        ap.error("either --keys HIRAGANA or --selftest is required")
    try:
        print(" ".join(to_keys(args.keys)))
    except ValueError as e:
        print("romaji.py: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
