#!/usr/bin/env python3
# kit: selftest
"""Extract only the meta and key rows from a standalone-logger output (obs/1) and seal the original file (Python
standard library only).

  seal_obs.py --obs FILE --keys-out FILE --sealed-dir DIR   seal (the original file moves into DIR and is removed from FILE's location)
  seal_obs.py --unseal SEALED --out FILE                    unseal (for the post-freeze analysis pipeline only)
  seal_obs.py --selftest                                    positive and negative controls (fast defaults)

The calibration pilot never reads, counts, or matches ImeShadow rows (it is not an accuracy measurement, and its
values must not be seen before the protocol is frozen). To that end, the pilot's analysis reads only the keys-out
file produced after sealing.

The rule is written as an **allow list** (it names the kinds of row to keep, never the kinds to discard):
  * Rows kept: the first row's meta, and every key row. Every other row, of whatever kind, is left out of keys-out.
  * Both meta and key rows are **rebuilt** from the specification's fields (the allow list applies not only to the
    kind of row but also to the fields inside it). meta keeps only the header keys (type / format / os /
    logger_version / manifest_sha256 / capabilities), and within capabilities only the key names are kept. A key
    row is rebuilt from exactly the four fields {type, t, category, press}, and any row with even one field beyond
    these is refused wholesale. A row with the same JSON key twice is also refused (the later value is never
    silently taken).

The output (standard output, seal.json, and error messages) never reports information about a discarded row (its
count, value, line number, or kind). Neither the original byte count nor the line count is reported either: the
byte count, together with the size of the sealed file, would let the number of ImeShadow rows be estimated.
Only the original file's whole-file SHA-256 and the number of key rows kept are reported. Integrity is verified
with SHA-256 alone.
The sealed file is padded to a multiple of 1 MiB (1,048,576 bytes) (padded with zero bytes). The original length is
recorded in a fixed-length field (8 bytes, a big-endian unsigned integer) placed after the padding, and is removed
from there only when unsealing. Under this design, the sealed file's size reveals the original size only to the
nearest 1 MiB, which is the coarseness the byte-count-suppression requirement allows.
No tool is built to open the original file's contents. --unseal only restores the sealed file to the original byte
sequence; it never interprets the contents (it is to be used only by the analysis pipeline after the protocol is
frozen).

An unreadable row (malformed as JSON Lines, a duplicate key, or a field that does not match the specification), a
first row that is not meta, or more than one meta row, is refused without touching anything (exit 1). A file where
keys-out already exists, or where a file of the same name or seal.json already exists at the seal destination, is
also refused. A file the logger is still writing to (size or mtime not yet stable) is refused as well (stop the
logger before sealing).
exit 0 = sealed (or unsealed) / 1 = refused (the original file is left untouched; the only exception is when
post-seal verification fails, which the message states) / 2 = usage error.
"""
import argparse
import datetime
import errno
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time

META_KEYS = ("type", "format", "os", "logger_version", "manifest_sha256", "capabilities")
CAP_GROUPS = ("unconditional", "threshold_gated", "config_gated")
KEEP_CAPABILITIES = ("key",)
KEY_FIELDS = ("type", "t", "category", "press")
KEY_CATEGORIES = (
    "alphabetic", "numeric", "punctuation", "sentence_punctuation", "tab", "backspace",
    "delete", "space", "enter", "navigation", "modifier", "function", "escape", "other",
)
VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PAD_UNIT = 1024 * 1024  # 1 MiB -- the sealed file's size is padded to a multiple of this
TRAILER_LEN = 8  # the fixed-length field after the padding (original length, big-endian u64)
STABLE_CHECKS = 2
STABLE_INTERVAL_S = 0.02

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


class Refused(Exception):
    """A reason not to seal or unseal (the message must never carry information about a discarded row)."""


class SealVerificationError(Exception):
    """Post-seal or post-unseal SHA-256 verification failed (kept outside the RuntimeError family, so it is never
    mistaken for a RecursionError)."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _no_dup_pairs(pairs):
    """object_pairs_hook for json.loads. Refuses a row with the same key twice (the later value is never silently
    taken)."""
    out = {}
    for k, v in pairs:
        if k in out:
            raise Refused("a JSON row has the same key twice")
        out[k] = v
    return out


def _parse_line(body):
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_no_dup_pairs)
    except (ValueError, RecursionError):
        # UnicodeDecodeError is a subclass of ValueError. Deep nesting (RecursionError) is also treated as a
        # refusal (never confused with post-seal verification failure -- both are "unreadable", not "corrupted").
        raise Refused("a row does not parse as JSON Lines")


def _meta_line(obj):
    """Rebuild a meta row via the allow list (keeping only the header keys and the capabilities key names). Also
    checks the type and shape of every value."""
    if not isinstance(obj, dict):
        raise Refused("the meta row is not an object")
    if set(obj) - set(META_KEYS):
        raise Refused("the meta row has a key outside the specification")
    if obj.get("type") != "meta":
        raise Refused("the meta row's type is wrong")
    if obj.get("format") != "obs/1":
        raise Refused("the meta row's format is not obs/1")
    if obj.get("os") not in ("macos", "windows"):
        raise Refused("the meta row's os is outside the specification's enumeration")
    if not isinstance(obj.get("logger_version"), str) or not VERSION_RE.match(obj["logger_version"]):
        raise Refused("the meta row's logger_version does not match the specification's shape")
    if not isinstance(obj.get("manifest_sha256"), str) or not SHA256_RE.match(obj["manifest_sha256"]):
        raise Refused("the meta row's manifest_sha256 does not match the specification's shape")
    caps = obj.get("capabilities")
    if not isinstance(caps, dict) or set(caps) != set(CAP_GROUPS):
        raise Refused("the meta row's capabilities do not match the specification's shape")
    kept_caps = {}
    for group in CAP_GROUPS:
        names = caps.get(group)
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise Refused("the meta row's capabilities are not an array of strings")
        kept_caps[group] = [n for n in names if n in KEEP_CAPABILITIES]
    out = {k: obj[k] for k in META_KEYS if k in obj}
    out["capabilities"] = kept_caps
    return _compact(out)


def _key_line(obj):
    """Rebuild a key row from exactly the specification's four fields (refused wholesale if any other field is
    present)."""
    if not isinstance(obj, dict):
        raise Refused("the key row is not an object")
    if set(obj) != set(KEY_FIELDS):
        raise Refused("the key row's keys do not match the specification")
    if obj.get("type") != "key":
        raise Refused("the key row's type is wrong")
    if not _is_int(obj.get("t")) or obj["t"] < 0:
        raise Refused("the key row's t is not an integer of 0 or more")
    if obj.get("category") not in KEY_CATEGORIES:
        raise Refused("the key row's category is outside the specification's 14 kinds")
    if not isinstance(obj.get("press"), bool):
        raise Refused("the key row's press is not a boolean")
    return _compact({"type": "key", "t": obj["t"], "category": obj["category"], "press": obj["press"]})


def _stable(path, checks=STABLE_CHECKS, interval_s=STABLE_INTERVAL_S):
    """Whether the size and mtime stay unchanged across `checks` observations (whether the logger is not currently
    writing to the file)."""
    prev = None
    for i in range(checks):
        try:
            st = os.stat(path)
        except OSError:
            return False
        cur = (st.st_size, getattr(st, "st_mtime_ns", st.st_mtime))
        if prev is not None and cur != prev:
            return False
        prev = cur
        if i + 1 < checks:
            time.sleep(interval_s)
    return True


def _pad_sealed_file(path, orig_len):
    """Append zero padding and a fixed-length field (the original length) to the end of path, so the whole file
    becomes a multiple of PAD_UNIT."""
    pad_len = (-(orig_len + TRAILER_LEN)) % PAD_UNIT
    block = b"\x00" * (1 << 20)
    with open(path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        remaining = pad_len
        while remaining > 0:
            n = min(remaining, len(block))
            f.write(block[:n])
            remaining -= n
        f.write(struct.pack(">Q", orig_len))


def _read_orig_len(path):
    size = os.path.getsize(path)
    if size < TRAILER_LEN:
        raise Refused("the sealed file is too small (the padding field is missing)")
    with open(path, "rb") as f:
        f.seek(size - TRAILER_LEN)
        trailer = f.read(TRAILER_LEN)
    (orig_len,) = struct.unpack(">Q", trailer)
    if orig_len > size - TRAILER_LEN:
        raise Refused("the sealed file's padding field holds an implausible value")
    return orig_len


def _hash_prefix(path, orig_len):
    h = hashlib.sha256()
    remaining = orig_len
    with open(path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk:
                raise SealVerificationError("the sealed file is shorter than the original length (corrupted)")
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def seal(obs, keys_out, sealed_dir):
    """Seal, and return the summary (dict) that may be printed to standard output. A refusal raises Refused."""
    if not os.path.isfile(obs):
        raise Refused("--obs is not a file")
    if os.path.exists(keys_out):
        raise Refused("--keys-out already exists (never overwritten)")
    part = keys_out + ".part"
    if os.path.exists(part):
        raise Refused("the --keys-out working file (.part) already exists")
    target = os.path.join(sealed_dir, os.path.basename(obs))
    seal_json = os.path.join(sealed_dir, "seal.json")
    seal_json_part = seal_json + ".part"
    if os.path.abspath(target) == os.path.abspath(obs):
        raise Refused("--obs is already inside the seal destination")
    if os.path.exists(target) or os.path.exists(seal_json):
        raise Refused("a file of the same name, or seal.json, already exists at the seal destination (never overwritten)")
    if not _stable(obs):
        raise Refused("the size or mtime of --obs is not stable (the logger may still be writing; stop it before sealing)")

    # 1. Write keys-out to .part (computing the whole original file's SHA-256 at the same time).
    h = hashlib.sha256()
    nbytes = 0
    kept = 0
    seen_meta = False
    try:
        with open(obs, "rb") as src, open(part, "wb") as dst:
            for raw in src:
                h.update(raw)
                nbytes += len(raw)
                body = raw.rstrip(b"\r\n")
                if not body.strip():
                    continue
                obj = _parse_line(body)
                if not isinstance(obj, dict):
                    raise Refused("a row does not parse as JSON Lines")
                kind = obj.get("type")
                if not seen_meta and kind != "meta":
                    raise Refused("the first row is not meta")
                if kind == "meta":
                    if seen_meta:
                        raise Refused("more than one meta row is present")
                    seen_meta = True
                    dst.write((_meta_line(obj) + "\n").encode("utf-8"))
                elif kind == "key":
                    dst.write((_key_line(obj) + "\n").encode("utf-8"))
                    kept += 1
        if not seen_meta:
            raise Refused("no meta row is present")
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise

    sha = h.hexdigest()

    # 2. Move the original file. On the same volume this is just os.rename; a different volume is refused
    #    (rename is atomic, so a failure never leaves a partial file behind).
    try:
        os.makedirs(sealed_dir, exist_ok=True)
        os.rename(obs, target)
    except OSError as e:
        if os.path.exists(part):
            os.remove(part)
        if getattr(e, "errno", None) == errno.EXDEV:
            raise Refused("the original file and the seal destination are on different volumes (no copy is made)")
        raise Refused("could not move the file to the seal destination (%s)" % e.__class__.__name__)

    # Append the padding and the fixed-length field. From this point on, target holds only original data (must
    # never be deleted).
    try:
        _pad_sealed_file(target, nbytes)
    except OSError as e:
        if os.path.exists(part):
            os.remove(part)
        raise Refused("could not write the padding to the seal destination (%s). The contents of %s are still the original data" % (
            e.__class__.__name__, target))

    # 3. Verify the SHA-256 (a dedicated exception class, never confused with RecursionError).
    after = _hash_prefix(target, nbytes)
    if after != sha:
        raise SealVerificationError("the post-seal SHA-256 does not match the one read on the way in (original sha256=%s); check the seal destination %s" % (sha, target))

    # 4. Write seal.json to .part and rename it.
    summary = {
        "sealed_file": os.path.basename(obs),
        "sha256": sha,
        "sealed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keys_out": os.path.basename(keys_out),
        "keys_out_sha256": _sha256_file(part),
        "kept_key_rows": kept,
    }
    try:
        with open(seal_json_part, "x", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        os.rename(seal_json_part, seal_json)
    except OSError as e:
        if os.path.exists(seal_json_part):
            os.remove(seal_json_part)
        if os.path.exists(part):
            os.remove(part)
        raise Refused("could not write seal.json (%s). The contents of %s are still the original data" % (
            e.__class__.__name__, target))

    # 5. Finally, rename the keys-out .part. Once this completes, "keys-out exists" means "sealing is complete".
    os.rename(part, keys_out)
    return summary


def unseal(sealed_path, out_path):
    """Restore the original byte sequence from a sealed file and write it to out_path, checking it against
    seal.json's SHA-256. A refusal raises Refused."""
    if not os.path.isfile(sealed_path):
        raise Refused("--unseal is not a file")
    seal_json = os.path.join(os.path.dirname(os.path.abspath(sealed_path)), "seal.json")
    try:
        with open(seal_json, encoding="utf-8") as f:
            info = json.load(f, object_pairs_hook=_no_dup_pairs)
    except (OSError, ValueError) as e:
        raise Refused("cannot read seal.json (%s)" % e.__class__.__name__)
    if not isinstance(info, dict) or not isinstance(info.get("sha256"), str) or not SHA256_RE.match(info["sha256"]):
        raise Refused("seal.json has the wrong shape (sha256 is missing)")
    expected = info["sha256"]

    orig_len = _read_orig_len(sealed_path)
    if os.path.exists(out_path):
        raise Refused("--out already exists (never overwritten)")
    part = out_path + ".part"
    if os.path.exists(part):
        raise Refused("the --out working file (.part) already exists")
    h = hashlib.sha256()
    remaining = orig_len
    try:
        with open(sealed_path, "rb") as src, open(part, "wb") as dst:
            while remaining > 0:
                chunk = src.read(min(1 << 20, remaining))
                if not chunk:
                    raise Refused("the sealed file is shorter than the original length (corrupted)")
                h.update(chunk)
                dst.write(chunk)
                remaining -= len(chunk)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    got = h.hexdigest()
    if got != expected:
        if os.path.exists(part):
            os.remove(part)
        raise SealVerificationError("the unsealed content's SHA-256 does not match seal.json (expected %s, got %s)" % (expected, got))
    os.rename(part, out_path)
    return {"bytes": orig_len, "sha256": got}


# --- selftest begin ---
def _selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    validator = os.path.join(here, "..", "formats", "validate_logs.py")
    me = os.path.abspath(__file__)
    fails = []

    def check(ok, what):
        print("  %s %s" % ("OK" if ok else "NG", what))
        if not ok:
            fails.append(what)

    meta = ('{"type":"meta","format":"obs/1","os":"macos","logger_version":"0.1.0",'
            '"manifest_sha256":"' + "0" * 64 + '","capabilities":{"unconditional":["key"],'
            '"threshold_gated":[],"config_gated":["ime_shadow"]}}')
    good = [
        meta,
        '{"type":"key","t":10,"category":"alphabetic","press":true}',
        '{"type":"ime_shadow","t":20,"dwell_ms":987654,"nav":1,"abandon":false}',
        '{"type":"key","t":30,"category":"space","press":true}',
        '{"type":"ime_shadow","t":40,"dwell_ms":987655,"nav":0,"abandon":true}',
        '{"type":"ime_shadow","t":50,"dwell_ms":987656,"nav":2,"abandon":false}',
        '{"type":"future_row","t":60,"x":1}',
    ]
    leaks = ("ime_shadow", "98765")

    def run(*args):
        p = subprocess.run([sys.executable, me] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def run_seal(obs, keys, sealed):
        return run("--obs", obs, "--keys-out", keys, "--sealed-dir", sealed)

    def write(path, lines, crlf=False):
        with open(path, "wb") as f:
            f.write((("\r\n" if crlf else "\n").join(lines) + ("\r\n" if crlf else "\n")).encode("utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        # positive: the allow list keeps the right rows. No information about a discarded row leaks into the
        # output. The original file moves to the seal destination, and its SHA-256 still matches.
        obs = os.path.join(tmp, "obs.jsonl")
        keys = os.path.join(tmp, "keys.jsonl")
        sealed = os.path.join(tmp, "sealed")
        write(obs, good)
        orig_sha = _sha256_file(obs)
        rc, out = run_seal(obs, keys, sealed)
        check(rc == 0, "can seal (exit 0)")
        with open(keys, encoding="utf-8") as f:
            kept = f.read().splitlines()
        check(len(kept) == 3 and kept[0].startswith('{"type":"meta"'), "keys-out holds only 1 meta row plus 2 key rows (%d rows)" % len(kept))
        check(sum(1 for l in kept if l.startswith('{"type":"key"')) == 2, "keys-out has 2 key rows")
        check(not any(w in "\n".join(kept) for w in leaks), "keys-out carries neither ImeShadow's name nor its values (including meta's capabilities)")
        check('"config_gated":[]' in kept[0] and '"unconditional":["key"]' in kept[0], "meta's capabilities lose every name other than key")
        check("future_row" not in "\n".join(kept), "a row of an unknown kind is not kept either (the allow list)")
        check(not os.path.exists(obs), "the original file is removed from its original location")
        moved = os.path.join(sealed, "obs.jsonl")
        check(os.path.isfile(moved) and _sha256_file(moved) != orig_sha, "the sealed file is not the original content unchanged (padding is appended)")
        check(os.path.getsize(moved) % PAD_UNIT == 0, "the sealed file's size is a multiple of 1 MiB (%d bytes)" % os.path.getsize(moved))
        with open(os.path.join(sealed, "seal.json"), encoding="utf-8") as f:
            seal_text = f.read()
        info = json.loads(seal_text)
        check(info["sha256"] == orig_sha, "seal.json's SHA-256 matches the original")
        check("bytes" not in info, "seal.json does not record a byte count")
        check(info["kept_key_rows"] == 2 and info["sealed_at"].endswith("Z"), "seal.json carries the number of key rows kept and the sealing time")
        check(not any(w in seal_text for w in leaks), "seal.json carries no ImeShadow information")
        check(not any(w in out for w in leaks) and orig_sha in out, "standard output carries no ImeShadow information, though the SHA-256 is printed")
        check("bytes" not in out and "byte" not in out.lower(), "standard output does not print a byte count")
        check(not os.path.exists(keys + ".part"), "no working file (.part) is left behind")
        p = subprocess.run([sys.executable, validator, "--kind", "obs", keys], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 0, "keys-out passes the obs/1 validator (exit %d)" % p.returncode)

        # --unseal: restores the exact original byte sequence, with a matching SHA-256.
        expected_bytes = ("\n".join(good) + "\n").encode("utf-8")
        restored = os.path.join(tmp, "restored.jsonl")
        rc, _ = run("--unseal", moved, "--out", restored)
        check(rc == 0, "can unseal (exit 0)")
        with open(restored, "rb") as f:
            restored_bytes = f.read()
        check(restored_bytes == expected_bytes, "the unsealed content restores the exact original byte sequence")
        check(_sha256_file(restored) == orig_sha, "the unsealed content's SHA-256 matches the original")
        check(os.path.exists(restored + ".part") is False, "no unsealing working file (.part) is left behind")
        rc, out2 = run("--unseal", moved, "--out", restored)
        check(rc == 1 and "refus" in out2, "refuses to unseal when --out already exists (never overwritten)")

        # a CRLF file can also be sealed (a Windows logger's output).
        obs2 = os.path.join(tmp, "obs2.jsonl")
        write(obs2, good, crlf=True)
        rc, _ = run_seal(obs2, os.path.join(tmp, "keys2.jsonl"), os.path.join(tmp, "sealed2"))
        with open(os.path.join(tmp, "keys2.jsonl"), "rb") as f:
            raw2 = f.read()
        check(rc == 0 and b"\r" not in raw2 and raw2.count(b'"type":"key"') == 2, "a CRLF file can be sealed too, and keys-out uses LF")

        # negative: a refusal never touches anything.
        def refuse(name, lines, pre=None, sealed_dir=None):
            o = os.path.join(tmp, name + ".jsonl")
            k = os.path.join(tmp, name + ".keys")
            sd = sealed_dir or os.path.join(tmp, name + "-sealed")
            write(o, lines)
            if pre:
                pre(k, sd)
            before = _sha256_file(o)
            rc, out = run_seal(o, k, sd)
            untouched = os.path.isfile(o) and _sha256_file(o) == before
            check(rc == 1 and untouched and not os.path.exists(k + ".part") and not any(w in out for w in leaks),
                  "refuses: %s (exit %d, original file untouched, no information leaked)" % (name, rc))
            return k, sd

        def make_keys(k, sd):
            with open(k, "w", encoding="utf-8") as f:
                f.write("x\n")

        k, _ = refuse("keys-out-exists", good, pre=make_keys)
        with open(k, encoding="utf-8") as f:
            check(f.read() == "x\n", "an existing keys-out is never overwritten")
        refuse("no-meta", good[1:])
        refuse("meta-not-first", [good[1], good[0]] + good[2:])
        refuse("second-meta", good[:2] + [meta] + good[2:])
        refuse("broken-json", good[:2] + ["{not json"] + good[2:])
        refuse("not-object", good[:2] + ["[1,2]"] + good[2:])
        refuse("empty", [])

        # Deep nesting (RecursionError) is also treated as a refusal (never confused with post-seal verification
        # failure).
        deep = "[" * 3000 + "]" * 3000
        refuse("too-deep", good[:1] + ['{"type":"key","t":1,"category":"other","press":' + deep + '}'] + good[2:])

        # the key row's allow list also applies to its fields: an extra field, a category outside the
        # specification, or the wrong type is refused.
        refuse("key-extra-field", good[:1] + ['{"type":"key","t":10,"category":"space","press":true,"extra":1}'])
        refuse("key-bad-category", good[:1] + ['{"type":"key","t":10,"category":"not_a_real_category","press":true}'])
        refuse("key-press-not-bool", good[:1] + ['{"type":"key","t":10,"category":"space","press":1}'])
        refuse("key-t-not-int", good[:1] + ['{"type":"key","t":"10","category":"space","press":true}'])

        # a row with the same key twice (the later value is never silently taken).
        refuse("dup-key-in-key-row", good[:1] + ['{"type":"key","type":"key","t":10,"category":"space","press":true}'])
        refuse("dup-key-in-meta", ['{"type":"meta","type":"meta","format":"obs/1","os":"macos","logger_version":"0.1.0",'
                                    '"manifest_sha256":"' + "0" * 64 + '","capabilities":{"unconditional":[],'
                                    '"threshold_gated":[],"config_gated":[]}}'] + good[1:])

        # meta's value types and shapes are also checked.
        bad_os_meta = meta.replace('"os":"macos"', '"os":"linux"')
        refuse("meta-bad-os", [bad_os_meta] + good[1:])
        bad_sha_meta = meta.replace('"manifest_sha256":"' + "0" * 64 + '"', '"manifest_sha256":"zz"')
        refuse("meta-bad-sha", [bad_sha_meta] + good[1:])

        def make_seal(k, sd):
            os.makedirs(sd)
            with open(os.path.join(sd, "seal.json"), "w", encoding="utf-8") as f:
                f.write("{}\n")

        refuse("seal-json-exists", good, pre=make_seal)
        same = os.path.join(tmp, "same-name")
        os.makedirs(same)
        o = os.path.join(tmp, "dup.jsonl")
        write(o, good)
        with open(os.path.join(same, "dup.jsonl"), "w", encoding="utf-8") as f:
            f.write("x\n")
        rc, _ = run_seal(o, os.path.join(tmp, "dup.keys"), same)
        check(rc == 1 and os.path.isfile(o), "refuses when a file of the same name already exists at the seal destination, and the original file is untouched")

        # a file the logger is still writing to (size not yet stable) is refused.
        import threading
        unstable = os.path.join(tmp, "unstable.jsonl")
        write(unstable, good)

        def grow_soon():
            time.sleep(STABLE_INTERVAL_S / 2)
            with open(unstable, "ab") as f:
                f.write(b"x")

        th = threading.Thread(target=grow_soon)
        th.start()
        rc, out = run_seal(unstable, os.path.join(tmp, "unstable.keys"), os.path.join(tmp, "unstable-sealed"))
        th.join()
        check(rc == 1 and os.path.isfile(unstable), "a file whose size is not stable is refused (stop the logger before sealing)")
        check(_stable(unstable) is True, "once writing stops, the file is judged stable (positive control)")

        # usage errors.
        p = subprocess.run([sys.executable, me, "--obs", o], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "exits 2 when a required argument is missing")
        p = subprocess.run([sys.executable, me, "--unseal", moved], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "--unseal requires --out (exit 2)")

    if fails:
        print("  ---- seal_obs selftest: %d failed ----" % len(fails))
        return 1
    print("  ---- seal_obs selftest: all green ----")
    return 0
# --- selftest end ---


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract only the meta and key rows of an obs/1 output and seal the original file")
    ap.add_argument("--selftest", action="store_true", help="run the positive and negative controls")
    ap.add_argument("--obs", help="standalone-logger output (to be sealed)")
    ap.add_argument("--keys-out", help="destination for the meta and key rows only (stops if it already exists)")
    ap.add_argument("--sealed-dir", help="destination for the original file (seal.json goes here too)")
    ap.add_argument("--unseal", help="the sealed file (used together with --out; for the post-freeze analysis pipeline only)")
    ap.add_argument("--out", help="destination to write --unseal's output")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.unseal:
        if not args.out:
            ap.error("--unseal requires --out")
        try:
            info = unseal(args.unseal, args.out)
        except Refused as e:
            print("seal_obs: refused: %s" % e, file=sys.stderr)
            return 1
        except SealVerificationError as e:
            print("seal_obs: post-unseal verification failed: %s" % e, file=sys.stderr)
            return 1
        print("unsealed: %s" % args.out)
        print("sha256: %s" % info["sha256"])
        return 0
    if not (args.obs and args.keys_out and args.sealed_dir):
        ap.error("--obs / --keys-out / --sealed-dir are all required (or --unseal / --out)")
    try:
        info = seal(args.obs, args.keys_out, args.sealed_dir)
    except Refused as e:
        print("seal_obs: refused (the original file was left untouched): %s" % e, file=sys.stderr)
        return 1
    except SealVerificationError as e:
        print("seal_obs: %s" % e, file=sys.stderr)
        return 1
    print("sealed: %s" % info["sealed_file"])
    print("sha256: %s" % info["sha256"])
    print("key rows kept: %d" % info["kept_key_rows"])
    print("keys-out: %s" % args.keys_out)
    print("seal.json: %s" % os.path.join(args.sealed_dir, "seal.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
