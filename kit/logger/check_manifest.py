#!/usr/bin/env python3
# kit: selftest
"""Self-verifier for the standalone logger's manifest files (Python standard library only).

  check_manifest.py --self         confirms every file or marked excerpt listed in a manifest
                                    matches its recorded SHA-256 (does not read any other source
                                    tree; runs against a published checkout)
  check_manifest.py --selftest     negative-control suite (fast by default, a few seconds)

The target is every manifest*.json under kit/logger/ (the directory this file lives in, overridable
with --logger-dir). Exit code: 0 = match / 1 = mismatch / 2 = usage error, unreadable input, zero
manifests, zero entries (never report an empty match as success), a path-safety violation (NUL,
absolute path, escapes the base directory) or malformed JSON.

Entry kinds:
  file    the whole file's SHA-256 matches the manifest.
  excerpt a range's SHA-256 matches the manifest. The range is the lines strictly between
          `// @excerpt-begin <id>` and `// @excerpt-end <id>`, matching whole lines and ignoring
          trailing whitespace, and each marker must occur exactly once in the file.
  new     a file written for this logger, checked the same way as `file` (a reason is required).

SHA-256 is computed after normalizing CRLF to LF. `path` is relative to the logger directory (a
leading `/` or `\\`, NUL, or a path that escapes the base directory is rejected). A manifest entry
may carry further fields; this verifier reads only `kind`, `id`, `path` and `sha256`.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import tempfile
import subprocess

for _s in (sys.stdout, sys.stderr):  # keeps non-ASCII output from crashing on a legacy console codepage
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("file", "excerpt", "new")


class Bad(Exception):
    pass


class Unsafe(Bad):
    """A path-safety violation (NUL, absolute path, escapes the base directory, symlink escape, etc.).

    Kept distinct from an ordinary Bad (a content mismatch, such as a SHA-256 difference): `each_entry`
    does not fold this into a per-entry mismatch string but lets it propagate to the caller, which exits
    2 (usage error) rather than reporting it as one more mismatch among others.
    """


def norm(b):
    return b.replace(b"\r\n", b"\n")


def sha(b):
    return hashlib.sha256(norm(b)).hexdigest()


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise Bad("cannot read: %s (%s)" % (path, e.strerror))


def to_lines(b):
    lines = norm(b).decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def join(lines):
    return "".join(l + "\n" for l in lines).encode("utf-8")


def once(lines, want, what):
    hits = [i for i, l in enumerate(lines) if l.rstrip() == want.rstrip()]
    if len(hits) != 1:
        raise Bad("%s does not occur exactly once (%d occurrences)" % (what, len(hits)))
    return hits[0]


def copy_range(lines, eid):
    b, e = once(lines, "// @excerpt-begin " + eid, "copy-side @excerpt-begin " + eid), once(lines, "// @excerpt-end " + eid, "copy-side @excerpt-end " + eid)
    if e < b:
        raise Bad("@excerpt-end precedes @excerpt-begin (%s)" % eid)
    return lines[b + 1:e]


def manifests(ldir):
    paths = sorted(glob.glob(os.path.join(ldir, "manifest*.json")))
    if not paths:
        raise Bad("no manifest*.json found (never report an empty match as success): " + ldir)
    out = []
    for p in paths:
        try:
            m = json.loads(read_bytes(p).decode("utf-8"))
        except ValueError:
            raise Bad("cannot parse as JSON: " + os.path.basename(p))
        if not isinstance(m, dict) or not isinstance(m.get("entries"), list) or not m.get("entries"):
            raise Bad("entries is not an array, or is empty (never report an empty match as success): " + os.path.basename(p))
        out.append((os.path.basename(p), m))
    return out


def safe_rel(p, what):
    """Validates the shape of a relative path. `os.path.isabs` does not treat a leading `/` on Windows
    as absolute, so that case is rejected separately here."""
    if not isinstance(p, str) or not p:
        raise Unsafe("%s is missing" % what)
    if "\x00" in p:
        raise Unsafe("%s contains a NUL byte: %r" % (what, p))
    if p.startswith("/") or p.startswith("\\"):
        raise Unsafe("%s starts with / or \\: %r" % (what, p))
    if os.path.isabs(p) or ":" in p or ".." in p.replace("\\", "/").split("/"):
        raise Unsafe("%s is not a relative path: %r" % (what, p))
    return p


def safe_join(base, rel, what):
    """Returns an absolute path guaranteed to stay inside `base` (resolved with realpath and checked
    with commonpath, which also rejects a symlink that escapes the base directory)."""
    rel = safe_rel(rel, what)
    base_real = os.path.realpath(base)
    p = os.path.realpath(os.path.join(base, rel))
    try:
        common = os.path.commonpath([base_real, p])
    except ValueError:  # commonpath raises ValueError across drives on Windows
        raise Unsafe("%s escapes the base directory (different drive): %r" % (what, rel))
    if common != base_real:
        raise Unsafe("%s escapes the base directory: %r" % (what, rel))
    return p


def read_copy_lines(ldir, ent):
    return to_lines(read_bytes(safe_join(ldir, ent.get("path"), "path")))


def copy_bytes(ldir, ent):
    """The copy-side bytes to be checked."""
    if ent.get("kind") not in KINDS:
        raise Bad("invalid kind: %r" % ent.get("kind"))
    lines = read_copy_lines(ldir, ent)
    if ent["kind"] == "excerpt":
        return join(copy_range(lines, ent.get("id")))
    if ent["kind"] == "new" and not ent.get("reason"):
        raise Bad("a new entry has no reason")
    return join(lines)


def each_entry(ldir, check):
    errs = []
    for name, m in manifests(ldir):
        for ent in m["entries"]:
            try:
                check(ent)
            except Unsafe:
                raise  # A path-safety violation propagates to the caller instead of being folded in.
            except (Bad, UnicodeDecodeError) as e:
                errs.append("%s: %s: %s" % (name, ent.get("id"), e))
    return errs


def check_self(ldir):
    def one(ent):
        if sha(copy_bytes(ldir, ent)) != ent.get("sha256"):
            raise Bad("SHA-256 mismatch (%s)" % ent.get("path"))
    return each_entry(ldir, one)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
FILE_TEXT = "// header\npub const K: u32 = 0x19; // c\npub fn alpha() -> u32 {\n    1\n}\n/// START\npub fn beta() -> u32 {\n    2\n}\n/// END\npub fn gamma() {}\n"
EXCERPT_TEXT = "use std::io;\n// @excerpt-begin ex1\n/// START\npub fn beta() -> u32 {\n    2\n}\n// @excerpt-end ex1\n"
OTHER_TEXT = "// other\n"


def selftest():
    fails = []
    tmp = tempfile.mkdtemp(prefix="check_manifest_selftest_")
    ldir = os.path.join(tmp, "logger")
    script = os.path.abspath(__file__)

    def put(rel, text):
        p = os.path.join(ldir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
        return p

    def write_manifest(name, entries):
        put(name, json.dumps({"entries": entries}, ensure_ascii=False, indent=2))

    def run(*args):
        r = subprocess.run([sys.executable, script, "--logger-dir", ldir] + list(args), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
        return r.returncode, r.stdout + r.stderr

    def expect(name, args, rc_want, needle=None):
        rc, out = run(*args)
        if rc != rc_want or "Traceback" in out or (needle and needle not in out):
            fails.append("%s: exit %d (expected %d) %s" % (name, rc, rc_want, out.strip()[-160:]))

    def mutated(path, old, new, name, args, needle):  # changes one spot to confirm a mismatch, then restores it
        with open(path, "rb") as f:
            orig = f.read()
        assert old.encode() in orig, name
        with open(path, "wb") as f:
            f.write(orig.replace(old.encode(), new.encode(), 1))
        try:
            expect(name, args, 1, needle)
        finally:
            with open(path, "wb") as f:
                f.write(orig)

    cpy = put("src/a_copy.rs", FILE_TEXT)
    ex = put("src/ex.rs", EXCERPT_TEXT)
    other = put("src/other.rs", OTHER_TEXT)

    # Zero manifests is a usage error (exit 2), checked before any manifest*.json exists in ldir.
    expect("zero manifests is a usage error (exit 2)", ["--self"], 2, "manifest")

    ex_sha = sha(join(copy_range(to_lines(EXCERPT_TEXT.encode()), "ex1")))
    write_manifest("manifest.json", [
        {"kind": "file", "id": "a", "path": "src/a_copy.rs", "sha256": sha(FILE_TEXT.encode())},
        {"kind": "excerpt", "id": "ex1", "path": "src/ex.rs", "sha256": ex_sha},
    ])
    write_manifest("manifest.other.json", [
        {"kind": "new", "id": "other", "path": "src/other.rs", "reason": "belongs to a different manifest", "sha256": sha(OTHER_TEXT.encode())},
    ])

    expect("positive control --self", ["--self"], 0)

    mutated(cpy, "alpha", "alphb", "negative 1: a one-byte change to the copy makes --self fail", ["--self"], "mismatch")
    mutated(ex, "2\n", "3\n", "negative 1b: a one-byte change to the excerpted copy makes --self fail", ["--self"], "mismatch")
    mutated(ex, "// @excerpt-end ex1", "// @excerpt-end ex2", "negative 1c: a missing copy-side marker makes --self fail", ["--self"], "@excerpt-end")
    mutated(other, "other", "else", "negative 1d: --self also reads the copy in a different manifest", ["--self"], "manifest.other.json")

    # --- path safety and malformed input (exit 2, not a traceback) ---------------------
    sec = os.path.join(tmp, "sec")
    sec_ldir = os.path.join(sec, "logger")

    def sec_put(rel, obj):
        p = os.path.join(sec_ldir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False))
        return p

    def sec_run(*args):
        return subprocess.run(
            [sys.executable, script, "--logger-dir", sec_ldir] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONUTF8="1"), timeout=60,
        )

    def sec_expect(name, args, rc_want, needle=None):
        r = sec_run(*args)
        out = r.stdout + r.stderr
        if r.returncode != rc_want or "Traceback" in out or (needle and needle not in out):
            fails.append("%s: exit %d (expected %d) %s" % (name, r.returncode, rc_want, out.strip()[-160:]))

    sec_put("manifest.json", {"entries": "abc"})
    sec_expect("negative 10: entries not an array is exit 2 (not a traceback)", ["--self"], 2)

    sec_put("manifest.json", {"entries": [{"kind": "file", "id": "n", "path": "a\x00b", "sha256": "0" * 64}]})
    sec_expect("negative 11: a NUL byte in path is exit 2 (not a traceback)", ["--self"], 2)

    sec_put("manifest.json", {"entries": [{"kind": "file", "id": "n", "path": "/etc/passwd", "sha256": "0" * 64}]})
    sec_expect("negative 12: a path starting with / is exit 2", ["--self"], 2)

    shutil_rmtree_ignore_errors(tmp)
    for f in fails:
        print("NG:", f)
    if not fails:
        print("check_manifest selftest ok")
    return 1 if fails else 0


def shutil_rmtree_ignore_errors(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for flag in ("--self", "--selftest"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--logger-dir", default=HERE)
    a = ap.parse_args()
    modes = [f for f in ("self", "selftest") if getattr(a, f)]
    if len(modes) != 1:
        print("exactly one of --self / --selftest is required", file=sys.stderr)
        return 2
    if a.selftest:
        return selftest()
    ldir = os.path.abspath(a.logger_dir)
    try:
        errs = check_self(ldir)
    except (Bad, ValueError) as e:
        # Reaching here always means a structural error (malformed JSON, zero manifests, a path-safety
        # violation, etc.), distinct from an ordinary mismatch (`errs`); exit 2, not a traceback.
        print("NG:", e)
        return 2
    for e in errs:
        print("NG:", e)
    print("--self: %s" % ("%d mismatches" % len(errs) if errs else "match"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
