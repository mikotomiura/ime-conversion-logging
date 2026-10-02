#!/usr/bin/env python3
# kit: selftest
"""Scenario emitter: plays a ``plan/1`` file through a key-sending backend and writes an ``oracle/1`` log.

  emit.py --plan PLAN --out ORACLE --backend dryrun|macos|windows [--backend-opt k=v ...]
  emit.py --validate ORACLE
  emit.py --selftest [--full]

The emitter waits only for the intervals the plan gives (``gap_before_ms``, ``marker_gap_ms``, and the
per-step ``at_ms``); it never chooses an interval itself. Every key it sends is recorded with the wall-clock
and monotonic time taken immediately before dispatch, so the oracle states what was intended and when it was
actually sent. The oracle is used only to verify the observed ground truth, never as an input to an observer.
Both clocks must resolve 1 ms or better; the emitter refuses to start otherwise (``ClockTooCoarse``).

Sending is opt-in, and the gate sits in ``Emitter`` itself: a backend other than ``dryrun`` is not opened
unless the emitter was constructed with ``allow_send=True``, and no output file is created otherwise. The
command line passes ``allow_send`` only when the flag named by ``SEND_FLAG`` is given (exit status 2 without
it). Synthetic keys go to the application that is in front, so run a real backend only against a prepared
target window.

Library use (an orchestrator that also resets the input method between blocks)::

    with Emitter(backend, out_path, plan_sha256, os_name, backend_name,
                 allow_send=True, supported_keys=module.SUPPORTED_KEYS) as em:
        em.write_reset(block_id, exit_code)
        em.run_block(block, trials)

The context manager opens the backend on entry and always closes it on exit, including after an exception.
``run_block`` checks, before it sends anything, that every key of the block is in ``supported_keys``.

Backend contract (``backend_<name>.py``): ``SUPPORTED_KEYS`` (a frozenset of key names, pseudo-keys
included) and a ``Backend`` class with ``__init__(opts)`` (unknown options raise), ``open()``, ``send(key, down)``,
and ``close()``. A plan that uses a key outside ``SUPPORTED_KEYS`` is refused before anything is sent.
A backend module may also define ``KEY_OPTIONS``, a mapping from a key name to the backend option that the key
needs; a plan that uses such a key without the option is refused before anything is sent.

Exit status: 0 success / 1 validation failed or a run failed / 2 the run was refused or could not start.
"""
import argparse
import hashlib
import contextlib
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import types

EMITTER_VERSION = "1"
SEND_FLAG = "--i-understand-this-sends-keys"
HERE = os.path.dirname(os.path.abspath(__file__))
BACKENDS = ("dryrun", "macos", "windows")
OS_NAMES = ("macos", "windows")
SPIN_SECONDS = 0.002
VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
KEYS_OF_KIND = {
    "block_begin": ("block_id",),
    "block_end": ("block_id",),
    "trial_begin": ("trial_id",),
    "trial_end": ("trial_id",),
    "sent": ("trial_id", "step", "key", "down"),
    "marker": ("block_id", "seq"),
    "reset": ("block_id", "exit_code"),
}
TIME_KEYS = ("t_wall_ms", "t_mono_ns")
KEYNAMES = frozenset(
    [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(d) for d in range(10)]
    + ["f%d" % n for n in range(13, 20)]
    + ["space", "enter", "esc", "backspace", "tab", "left", "right", "up", "down", "minus", "comma",
       "period", "ime_on", "ime_off", "shift", "mouse_click", "focus_away", "focus_back"])
MAX_PLAN_BYTES = 16 * 1024 * 1024   # size limit of a plan file
MAX_AT_MS = 3600000                 # upper bound of a step time (one hour into a trial)
MAX_GAP_MS = 3600000                # upper bound of gap_before_ms and marker_gap_ms
SIGTERM_STATUS = 143
MAX_CLOCK_RESOLUTION_S = 0.001      # coarsest clock resolution accepted for the oracle timestamps
FILETIME_UNIX_EPOCH = 116444736000000000  # 1970-01-01 in 100 ns intervals since 1601-01-01


class PlanError(Exception):
    pass


class SendRefused(Exception):
    """Raised when an emitter is asked to open a real backend without permission to send."""


class ClockTooCoarse(Exception):
    """Raised when a clock used for the oracle timestamps resolves coarser than MAX_CLOCK_RESOLUTION_S."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ plan

def _no_duplicate_keys(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError("repeated key")
        obj[k] = v
    return obj


def _check_steps(n, steps):
    if not isinstance(steps, list):
        raise PlanError("line %d: steps must be an array" % n)
    prev, held = 0, set()
    for s in steps:
        if (not isinstance(s, dict) or not isinstance(s.get("key"), str)
                or not _is_int(s.get("at_ms")) or s["at_ms"] < prev or s["at_ms"] > MAX_AT_MS
                or not isinstance(s.get("down"), bool)):
            raise PlanError("line %d: malformed, out-of-order, or too late step (at_ms is at most %d)"
                            % (n, MAX_AT_MS))
        prev = s["at_ms"]
        if s["down"]:
            if s["key"] in held:
                raise PlanError("line %d: a key is pressed twice without a release" % n)
            held.add(s["key"])
        elif s["key"] in held:
            held.discard(s["key"])
        else:
            raise PlanError("line %d: a key is released without a press" % n)
    if held:
        raise PlanError("line %d: a trial ends with a key still pressed" % n)


def _check_gap(n, row, name, required):
    if name not in row:
        if required:
            raise PlanError("line %d: %s is required" % (n, name))
        return
    if not _is_int(row[name]) or not 0 <= row[name] <= MAX_GAP_MS:
        raise PlanError("line %d: %s must be an integer from 0 to %d" % (n, name, MAX_GAP_MS))


def read_plan(path):
    """Return (sha256 of the file bytes, header, [(block, [trial, ...]), ...]); raise PlanError.

    Everything is checked before the caller sends a key: row shapes, integer ranges, unique block and trial
    identifiers, repeated JSON keys, nesting depth, file size, and the pairing of presses and releases in
    every trial. ``gap_before_ms`` is optional (absent means no wait); ``marker_gap_ms`` is required when the
    marker is not empty.
    """
    if os.path.getsize(path) > MAX_PLAN_BYTES:
        raise PlanError("the plan file is larger than %d bytes" % MAX_PLAN_BYTES)
    with open(path, "rb") as f:
        raw = f.read(MAX_PLAN_BYTES + 1)
    if len(raw) > MAX_PLAN_BYTES:
        raise PlanError("the plan file is larger than %d bytes" % MAX_PLAN_BYTES)
    sha = hashlib.sha256(raw).hexdigest()
    header, blocks = None, []
    block_ids, trial_ids = set(), set()
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=_no_duplicate_keys)
        except RecursionError:
            raise PlanError("line %d: nested too deeply" % n)
        except ValueError:
            raise PlanError("line %d: not JSON, or a key is repeated" % n)
        if not isinstance(row, dict):
            raise PlanError("line %d: not an object" % n)
        kind = row.get("kind")
        if header is None:
            if kind != "header" or row.get("format") != "plan/1" or row.get("os") not in OS_NAMES:
                raise PlanError("line %d: the first row must be a plan/1 header with a known os" % n)
            header = row
        elif kind == "block":
            marker = row.get("marker")
            if not _is_int(row.get("block_id")) or not isinstance(marker, list):
                raise PlanError("line %d: malformed block row (block_id and marker are required)" % n)
            if row["block_id"] in block_ids:
                raise PlanError("line %d: block_id repeats" % n)
            block_ids.add(row["block_id"])
            if not all(isinstance(k, str) for k in marker):
                raise PlanError("line %d: marker must list key names" % n)
            _check_gap(n, row, "marker_gap_ms", bool(marker))
            if "reset_ime" in row and not isinstance(row["reset_ime"], bool):
                raise PlanError("line %d: reset_ime must be a boolean" % n)
            blocks.append((row, []))
        elif kind == "trial":
            if not blocks:
                raise PlanError("line %d: a trial precedes every block" % n)
            if row.get("block_id") != blocks[-1][0]["block_id"]:
                raise PlanError("line %d: block_id differs from the preceding block row" % n)
            if not isinstance(row.get("trial_id"), str):
                raise PlanError("line %d: trial_id must be a string" % n)
            if row["trial_id"] in trial_ids:
                raise PlanError("line %d: trial_id repeats" % n)
            trial_ids.add(row["trial_id"])
            _check_gap(n, row, "gap_before_ms", False)
            _check_steps(n, row.get("steps"))
            blocks[-1][1].append(row)
        else:
            raise PlanError("line %d: unknown row kind" % n)
    if header is None:
        raise PlanError("empty plan")
    return sha, header, blocks


def block_keys(block, trials):
    keys = set(block.get("marker", []))
    for t in trials:
        keys.update(s["key"] for s in t["steps"])
    return keys


def check_key_options(module, keys, opts):
    """Return the key names that need a backend option the caller did not give (sorted)."""
    needs = getattr(module, "KEY_OPTIONS", {})
    return sorted(k for k in keys if k in needs and needs[k] not in opts)


def plan_keys(blocks):
    keys = set()
    for block, trials in blocks:
        keys.update(block_keys(block, trials))
    return keys


# ------------------------------------------------------------------ emitter

def _wait_until(target):
    """Sleep until time.perf_counter() reaches target; the last SPIN_SECONDS are a busy-wait."""
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > SPIN_SECONDS:
            time.sleep(remaining - SPIN_SECONDS)


def _wait_ms(ms):
    _wait_until(time.perf_counter() + ms / 1000.0)


def _precise_wall_clock():
    """On Windows, a function returning Unix time in nanoseconds from GetSystemTimePreciseAsFileTime;
    elsewhere None. On Windows, time.time() and time.monotonic() of some Python versions (3.11 among them)
    advance in steps of about 15.6 ms, which would quantise every timestamp of the oracle."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        fn = ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime
    except (ImportError, AttributeError, OSError):
        return None
    fn.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    fn.restype = None

    def wall_ns():
        ft = wintypes.FILETIME()
        fn(ctypes.byref(ft))
        ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return (ticks - FILETIME_UNIX_EPOCH) * 100

    return wall_ns


_PRECISE_WALL_NS = _precise_wall_clock()


def _wall_ns():
    return _PRECISE_WALL_NS() if _PRECISE_WALL_NS is not None else time.time_ns()


def clock_resolutions():
    """The declared resolutions, in seconds, of the wall clock and of the monotonic clock used for timestamps."""
    wall = 1e-7 if _PRECISE_WALL_NS is not None else time.get_clock_info("time").resolution
    return wall, time.get_clock_info("perf_counter").resolution


def _mono_ns():
    """The monotonic timestamp: the clock that the waits use."""
    return time.perf_counter_ns()


def _stamp():
    """Both timestamps of a row."""
    return {"t_wall_ms": _wall_ns() // 1000000, "t_mono_ns": _mono_ns()}


class Emitter(object):
    def __init__(self, backend, out, plan_sha256, os_name, backend_name, allow_send=False, supported_keys=None):
        self.backend = backend
        self.out = out
        self._name = backend_name
        self._allow_send = bool(allow_send)
        self._supported = None if supported_keys is None else frozenset(supported_keys)
        self._header = {"kind": "header", "format": "oracle/1", "plan_sha256": plan_sha256,
                        "os": os_name, "emitter_version": EMITTER_VERSION, "backend": backend_name}
        self._f = None

    def __enter__(self):
        if self._name != "dryrun":
            if not self._allow_send:
                raise SendRefused("backend %s sends real key events; the emitter was not given permission to send"
                                  % self._name)
            if self._supported is None:
                raise SendRefused("backend %s needs supported_keys so that a block is checked before it is sent"
                                  % self._name)
        wall, mono = clock_resolutions()
        if max(wall, mono) > MAX_CLOCK_RESOLUTION_S:
            raise ClockTooCoarse("the clocks for the timestamps resolve %.6f s (wall) and %.6f s (monotonic); "
                                 "at most %.3f s is required" % (wall, mono, MAX_CLOCK_RESOLUTION_S))
        self._f = open(self.out, "x", encoding="utf-8", newline="\n")
        try:
            self._row(self._header)
            self.backend.open()
        except BaseException:
            self._finish()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish()
        return False

    def _finish(self):
        try:
            self.backend.close()
        finally:
            if self._f is not None:
                self._f.close()
                self._f = None

    def _row(self, obj):
        self._f.write(_dumps(obj) + "\n")
        self._f.flush()

    def _emit(self, kind, **fields):
        row = {"kind": kind}
        row.update(fields)
        row.update(_stamp())
        self._row(row)
        return row

    def write_reset(self, block_id, exit_code):
        self._emit("reset", block_id=block_id, exit_code=exit_code)

    def run_block(self, block, trials):
        if self._supported is not None:
            unsupported = sorted(block_keys(block, trials) - self._supported)
            if unsupported:
                raise PlanError("keys not supported by backend %s: %s" % (self._name, ", ".join(unsupported)))
        block_id = block["block_id"]
        self._emit("block_begin", block_id=block_id)
        gap = block.get("marker_gap_ms", 0)
        for seq, key in enumerate(block.get("marker", [])):
            if seq > 0:
                _wait_ms(gap)
            st = _stamp()
            self.backend.send(key, True)
            row = {"kind": "marker", "block_id": block_id, "seq": seq}
            row.update(st)
            self._row(row)
            self.backend.send(key, False)
        for trial in trials:
            self.run_trial(trial)
        self._emit("block_end", block_id=block_id)

    def run_trial(self, trial):
        trial_id = trial["trial_id"]
        _wait_ms(trial.get("gap_before_ms", 0))
        # The steps are scheduled from the recorded trial_begin timestamp itself (the monotonic timestamp and
        # the waits use the same clock), so that a pause of the process between the two cannot make a key
        # appear, in the oracle, to have been sent before its planned time.
        start = self._emit("trial_begin", trial_id=trial_id)["t_mono_ns"] / 1e9
        for i, step in enumerate(trial["steps"]):
            _wait_until(start + step["at_ms"] / 1000.0)
            st = _stamp()
            self.backend.send(step["key"], step["down"])
            row = {"kind": "sent", "trial_id": trial_id, "step": i, "key": step["key"], "down": step["down"]}
            row.update(st)
            self._row(row)
        self._emit("trial_end", trial_id=trial_id)


# ------------------------------------------------------------------ oracle validation

def validate_oracle(path):
    """Return a list of violation descriptions (never containing a value from the file)."""
    errors = []

    def bad(n, msg):
        errors.append("line %d: %s" % (n, msg))

    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ["cannot read the file as UTF-8"]
    if text.startswith("﻿"):
        return ["the file starts with a byte order mark"]
    lines = [(n, l.rstrip("\r")) for n, l in enumerate(text.split("\n"), 1)]
    while lines and not lines[-1][1]:
        lines.pop()
    if not lines:
        return ["empty file"]
    state = "top"            # top -> block -> trial
    block_id, marker_seq, trial_started = None, 0, False
    trial_id, step, down = None, 0, set()
    seen_trials, last_mono = set(), None
    pending_reset = None     # block_id of a reset row that still needs its block_begin
    for idx, (n, line) in enumerate(lines):
        if re.search(r"\s", line) or "\\" in line:
            bad(n, "whitespace or a backslash outside the compact form")
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad(n, "not JSON")
            continue
        if not isinstance(row, dict):
            bad(n, "not an object")
            continue
        kind = row.get("kind")
        if idx == 0:
            want = {"kind", "format", "plan_sha256", "os", "emitter_version", "backend"}
            if (set(row) != want or row.get("kind") != "header" or row.get("format") != "oracle/1"
                    or not isinstance(row.get("plan_sha256"), str) or not HEX64_RE.match(row["plan_sha256"])
                    or row.get("os") not in OS_NAMES or row.get("backend") not in BACKENDS
                    or not isinstance(row.get("emitter_version"), str)
                    or not VERSION_RE.match(row["emitter_version"])):
                bad(n, "malformed header")
            continue
        if kind not in KEYS_OF_KIND:
            bad(n, "unknown or misplaced row kind")
            continue
        if set(row) != set(("kind",) + KEYS_OF_KIND[kind] + TIME_KEYS):
            bad(n, "%s row has the wrong set of keys" % kind)
            continue
        if not all(_is_int(row[k]) and row[k] >= 0 for k in TIME_KEYS):
            bad(n, "timestamps must be non-negative integers")
            continue
        if last_mono is not None and row["t_mono_ns"] < last_mono:
            bad(n, "monotonic time goes backwards")
        last_mono = row["t_mono_ns"]
        if kind in ("block_begin", "block_end", "marker", "reset") and not _is_int(row["block_id"]):
            bad(n, "block_id must be an integer")
            continue
        if pending_reset is not None and kind != "block_begin":
            bad(n, "a reset row must be followed directly by block_begin")
            pending_reset = None
        if kind == "reset":
            if state != "top" or not _is_int(row["exit_code"]):
                bad(n, "reset must sit between blocks and carry an integer exit_code")
            pending_reset = row["block_id"]
        elif kind == "block_begin":
            if state != "top":
                bad(n, "block_begin inside a block")
            if pending_reset is not None and pending_reset != row["block_id"]:
                bad(n, "block_id differs from the preceding reset row")
            pending_reset = None
            state, block_id, marker_seq, trial_started = "block", row["block_id"], 0, False
        elif kind == "marker":
            if (state != "block" or trial_started or row["block_id"] != block_id or not _is_int(row["seq"])
                    or row["seq"] != marker_seq):
                bad(n, "marker out of place or out of sequence")
            marker_seq += 1
        elif kind == "trial_begin":
            if state != "block" or not isinstance(row["trial_id"], str) or row["trial_id"] in seen_trials:
                bad(n, "trial_begin outside a block, or a repeated trial_id")
            state, trial_id, step, down, trial_started = "trial", row["trial_id"], 0, set(), True
            seen_trials.add(row["trial_id"])
        elif kind == "sent":
            if state != "trial" or row["trial_id"] != trial_id or not _is_int(row["step"]) or row["step"] != step:
                bad(n, "sent outside its trial or out of step order")
            elif not isinstance(row["key"], str) or not isinstance(row["down"], bool):
                bad(n, "sent needs a string key and a boolean down")
            elif row["key"] not in KEYNAMES:
                bad(n, "sent carries a key outside the enumeration of key names")
            elif row["down"]:
                if row["key"] in down:
                    bad(n, "key pressed twice without a release")
                down.add(row["key"])
            elif row["key"] not in down:
                bad(n, "release without a press")
            else:
                down.discard(row["key"])
            step += 1
        elif kind == "trial_end":
            if state != "trial" or row["trial_id"] != trial_id:
                bad(n, "trial_end without its trial_begin")
            elif down:
                bad(n, "trial ends with a key still pressed")
            state = "block"
        elif kind == "block_end":
            if state != "block" or row["block_id"] != block_id:
                bad(n, "block_end without its block_begin")
            state = "top"
    if state != "top":
        errors.append("the file ends inside an open %s" % state)
    if pending_reset is not None:
        errors.append("the file ends with a reset row that has no block_begin")
    return errors


# ------------------------------------------------------------------ command line

def load_backend(name):
    path = os.path.join(HERE, "backend_%s.py" % name)
    if not os.path.isfile(path):
        raise PlanError("backend %s is not implemented" % name)
    spec = importlib.util.spec_from_file_location("backend_%s" % name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "SUPPORTED_KEYS") or not hasattr(module, "Backend"):
        raise PlanError("backend %s does not follow the backend contract" % name)
    return module


def parse_opts(items):
    opts = {}
    for item in items:
        if "=" not in item:
            raise PlanError("--backend-opt expects k=v")
        k, v = item.split("=", 1)
        if not k or k in opts:
            raise PlanError("--backend-opt: empty or repeated key")
        opts[k] = v
    return opts


def wants_reset(blocks):
    return any(block.get("reset_ime") is True for block, _ in blocks)


def _terminate(signum, frame):
    sys.exit(SIGTERM_STATUS)


def run(args):
    try:
        opts = parse_opts(args.backend_opt)
        sha, header, blocks = read_plan(args.plan)
    except (PlanError, OSError, UnicodeDecodeError) as e:
        print("emit: %s" % e, file=sys.stderr)
        return 2
    if args.backend != "dryrun" and not args.send_flag:
        print("emit: backend %s sends real key events; it is not started without %s" % (args.backend, SEND_FLAG),
              file=sys.stderr)
        return 2
    try:
        module = load_backend(args.backend)
    except (PlanError, ValueError, OSError, SyntaxError, ImportError) as e:
        print("emit: backend %s could not be loaded: %s" % (args.backend, e), file=sys.stderr)
        return 2
    if args.backend != "dryrun" and header["os"] != args.backend:
        print("emit: the plan is for %s but the backend is %s" % (header["os"], args.backend), file=sys.stderr)
        return 2
    keys = plan_keys(blocks)
    unsupported = sorted(keys - set(module.SUPPORTED_KEYS))
    if unsupported:
        print("emit: keys not supported by backend %s: %s" % (args.backend, ", ".join(unsupported)),
              file=sys.stderr)
        return 2
    missing = check_key_options(module, keys, opts)
    if missing:
        print("emit: keys that need a backend option that was not given: %s" % ", ".join(missing),
              file=sys.stderr)
        return 2
    if wants_reset(blocks):
        if args.backend != "dryrun":
            print("emit: a block asks for an input-method reset, which the command line does not perform; "
                  "the procedure that prepares each cell resets the input method, so drive the Emitter "
                  "class from it instead", file=sys.stderr)
            return 2
        print("emit: note: reset_ime is ignored by the command line (dryrun continues)", file=sys.stderr)
    if os.path.exists(args.out):
        print("emit: refusing to overwrite an existing output file", file=sys.stderr)
        return 2
    try:
        backend = module.Backend(opts)
    except (ValueError, TypeError) as e:
        print("emit: %s" % e, file=sys.stderr)
        return 2
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        with Emitter(backend, args.out, sha, header["os"], args.backend,
                     allow_send=args.send_flag, supported_keys=module.SUPPORTED_KEYS) as em:
            for block, trials in blocks:
                em.run_block(block, trials)
    except (SendRefused, ClockTooCoarse, PlanError) as e:
        print("emit: %s" % e, file=sys.stderr)
        return 2
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print("emit: run failed: %s" % e, file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Play a plan/1 file through a key-sending backend and write an oracle/1 log.")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--backend", choices=BACKENDS)
    ap.add_argument("--backend-opt", action="append", default=[], metavar="K=V")
    ap.add_argument(SEND_FLAG, dest="send_flag", action="store_true",
                    help="required for any backend that sends real key events")
    ap.add_argument("--validate", metavar="ORACLE")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="with --selftest: also check the upper bound on send lateness (run on an idle machine)")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest(full=args.full)
    if args.validate:
        errors = validate_oracle(args.validate)
        for e in errors[:20]:
            print("oracle: %s" % e)
        print("oracle: %s" % ("valid" if not errors else "%d violation(s)" % len(errors)))
        return 1 if errors else 0
    if not (args.plan and args.out and args.backend):
        ap.print_usage(sys.stderr)
        return 2
    return run(args)


# ------------------------------------------------------------------ self-test (dryrun only)

def _make_plan(path, with_unsupported=False):
    sha = "0" * 64
    rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1, "generator_version": "0",
             "config_sha256": sha}]
    n = 0
    for b in range(2):
        rows.append({"kind": "block", "block_id": b, "speed": "mid", "reset_ime": b == 1,
                     "marker": ["f13", "f14"], "marker_gap_ms": 5})
        for _ in range(3):
            n += 1
            steps = []
            for i in range(20):
                steps.append({"key": "a", "at_ms": i * 8, "down": True})
                steps.append({"key": "a", "at_ms": i * 8 + 4, "down": False})
            if with_unsupported:
                steps.append({"key": "mouse_click", "at_ms": 200, "down": True})
            rows.append({"kind": "trial", "trial_id": "t%05d" % n, "block_id": b, "scenario": "S3",
                         "speed": "mid", "gap_before_ms": 50, "think_ms": 0, "phrase_id": "w1-1-1",
                         "reading_len": 1, "steps": steps, "intent": {}})
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(_dumps(r) + "\n")


def _load_rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _quiet_main(argv):
    """Run main() with its messages captured; the self-test reports results itself."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def selftest(full=False):
    """Dryrun checks. The default run contains only checks that do not depend on the load of the machine;
    full=True adds the upper bound on send lateness, which is meaningful only on an otherwise idle machine."""
    failures = []

    def check(name, cond):
        print("%s %s" % ("ok  " if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        plan = os.path.join(tmp, "plan.jsonl")
        out = os.path.join(tmp, "oracle.jsonl")
        _make_plan(plan)
        rc = _quiet_main(["--plan", plan, "--out", out, "--backend", "dryrun"])
        check("a dryrun of a two-block plan exits 0", rc == 0)
        check("the produced oracle passes validation", _quiet_main(["--validate", out]) == 0)
        rows = _load_rows(out)
        kinds = [r["kind"] for r in rows]
        check("row order is header, block_begin, markers, trials, block_end",
              kinds[:4] == ["header", "block_begin", "marker", "marker"] and kinds[-1] == "block_end"
              and kinds.count("trial_begin") == 6 and kinds.count("trial_end") == 6)
        check("one sent row per planned step", kinds.count("sent") == 6 * 40)
        check("marker rows carry a 0-based seq", [r["seq"] for r in rows if r["kind"] == "marker"][:2] == [0, 1])

        # timing: actual send time against the planned time, and the plan's own waits
        prev_end, gaps = None, []
        for r in rows:
            if r["kind"] == "trial_begin" and prev_end is not None:
                gaps.append((r["t_mono_ns"] - prev_end) / 1e6)
            elif r["kind"] == "trial_end":
                prev_end = r["t_mono_ns"]
        _, _, blocks = read_plan(plan)
        planned = {}
        for _b, trials in blocks:
            for t in trials:
                planned[t["trial_id"]] = t["steps"]
        starts, late = {}, []
        for r in rows:
            if r["kind"] == "trial_begin":
                starts[r["trial_id"]] = r["t_mono_ns"]
            elif r["kind"] == "sent":
                due = starts[r["trial_id"]] + planned[r["trial_id"]][r["step"]]["at_ms"] * 1000000
                late.append((r["t_mono_ns"] - due) / 1e6)
        late.sort()
        p95 = late[int(0.95 * (len(late) - 1))]
        check("no key is sent before its planned time", late[0] >= -1.0)
        if full:
            check("95th percentile of send lateness is under 20 ms (%.2f ms)" % p95, p95 < 20.0)
        else:
            print("info 95th percentile of send lateness: %.2f ms (the 20 ms bound depends on the load of the "
                  "machine and is checked by --selftest --full)" % p95)
        check("the inter-trial wait of the plan is honoured", all(g >= 49.0 for g in gaps) and len(gaps) >= 4)

        # a pause of the process just before the trial_begin timestamp (simulated by delaying that one
        # timestamp by 5 ms) must not make a key appear, in the oracle, to have been sent early
        class _DelayedBegin(Emitter):
            def _emit(self, kind, **fields):
                if kind == "trial_begin":
                    time.sleep(0.005)
                return Emitter._emit(self, kind, **fields)

        delayed = os.path.join(tmp, "delayed.jsonl")
        plan_sha, plan_header, plan_blocks = read_plan(plan)
        dry = load_backend("dryrun")
        with _DelayedBegin(dry.Backend({}), delayed, plan_sha, plan_header["os"], "dryrun",
                           supported_keys=dry.SUPPORTED_KEYS) as em:
            first_block, first_trials = plan_blocks[0]
            em.run_block(first_block, first_trials[:2])
        begun, delayed_late = {}, []
        for r in _load_rows(delayed):
            if r["kind"] == "trial_begin":
                begun[r["trial_id"]] = r["t_mono_ns"]
            elif r["kind"] == "sent":
                due = begun[r["trial_id"]] + planned[r["trial_id"]][r["step"]]["at_ms"] * 1000000
                delayed_late.append((r["t_mono_ns"] - due) / 1e6)
        check("keys are scheduled from the recorded trial_begin timestamp",
              len(delayed_late) == 2 * 40 and min(delayed_late) >= -1.0)

        # negative controls for the validator
        text = open(out, encoding="utf-8").read()
        bad_cases = {
            "a missing trial_end": "".join(l + "\n" for l in text.splitlines() if '"kind":"trial_end"' not in l),
            "a missing header": "".join(l + "\n" for l in text.splitlines()[1:]),
            "a release without a press": text.replace('"down":true', '"down":false', 1),
            "a missing block_end": "".join(l + "\n" for l in text.splitlines() if '"kind":"block_end"' not in l),
            "whitespace in a row": text.replace('"kind":"sent",', '"kind": "sent",', 1),
            "a non-integer timestamp": text.replace('"t_wall_ms":', '"t_wall_ms":1.5,"x":', 1),
        }
        for name, body in bad_cases.items():
            p = os.path.join(tmp, "bad.jsonl")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            check("validation rejects %s" % name, _quiet_main(["--validate", p]) == 1)

        marks = [r for r in rows if r["kind"] == "marker"]
        check("the marker gap of the plan is honoured",
              len(marks) >= 2 and (marks[1]["t_mono_ns"] - marks[0]["t_mono_ns"]) >= (5 - 1) * 1000000)

        # the timestamp clocks: declared resolution, observed step, and the refusal of a coarse clock
        check("the timestamp clocks declare a resolution of 1 ms or better",
              max(clock_resolutions()) <= MAX_CLOCK_RESOLUTION_S)
        steps, prev, deadline = [], _stamp()["t_wall_ms"], time.perf_counter() + 0.5
        while len(steps) < 20 and time.perf_counter() < deadline:
            cur = _stamp()["t_wall_ms"]
            if cur != prev:
                steps.append(cur - prev)
                prev = cur
        steps.sort()
        check("the wall-clock timestamp of a row advances in steps of 1 ms",
              len(steps) == 20 and steps[len(steps) // 2] <= 1)
        # whether time.time() steps coarsely depends on the timer resolution other processes request, so the
        # source of both timestamps is also checked directly
        original_clocks = (globals()["_wall_ns"], globals()["_mono_ns"])
        globals()["_wall_ns"], globals()["_mono_ns"] = (lambda: 1700000000123456789), (lambda: 42)
        try:
            check("row timestamps come from the high-resolution clocks",
                  _stamp() == {"t_wall_ms": 1700000000123, "t_mono_ns": 42})
        finally:
            globals()["_wall_ns"], globals()["_mono_ns"] = original_clocks
        original_resolutions = globals()["clock_resolutions"]
        globals()["clock_resolutions"] = lambda: (0.015625, 1e-7)
        try:
            o = os.path.join(tmp, "coarse.jsonl")
            check("a clock coarser than 1 ms stops the run before any output",
                  _quiet_main(["--plan", plan, "--out", o, "--backend", "dryrun"]) == 2 and not os.path.exists(o))
        finally:
            globals()["clock_resolutions"] = original_resolutions

        # sending is opt-in: the real-backend path runs the real main() with load_backend replaced by a sentinel
        # that raises when called, so the check cannot reach a backend module at all
        loaded = []

        def sentinel_loader(name):
            loaded.append(name)
            raise AssertionError("load_backend must not be reached without the send flag")

        original_loader = globals()["load_backend"]
        globals()["load_backend"] = sentinel_loader
        try:
            for real in ("macos", "windows"):
                o = os.path.join(tmp, "real_%s.jsonl" % real)
                check("backend %s without the send flag exits 2 and writes nothing" % real,
                      _quiet_main(["--plan", plan, "--out", o, "--backend", real]) == 2 and not os.path.exists(o))
        finally:
            globals()["load_backend"] = original_loader
        check("no backend module was loaded on the refused paths", loaded == [])

        # the gate is in Emitter: used directly, it does not open a real backend without allow_send
        class Sentinel(object):
            opened = 0
            closed = 0

            def open(self):
                Sentinel.opened += 1
                raise AssertionError("a real backend was opened without permission")

            def send(self, key, down):
                raise AssertionError("a key was sent without permission")

            def close(self):
                Sentinel.closed += 1

        o = os.path.join(tmp, "direct.jsonl")
        try:
            with Emitter(Sentinel(), o, "0" * 64, "macos", "macos", supported_keys=["a"]):
                pass
            refused = False
        except SendRefused:
            refused = True
        check("an Emitter used directly does not open a real backend without allow_send",
              refused and Sentinel.opened == 0 and not os.path.exists(o))
        try:
            with Emitter(Sentinel(), o, "0" * 64, "macos", "macos", allow_send=True):
                pass
            refused = False
        except SendRefused:
            refused = True
        check("a real backend needs supported_keys as well", refused and Sentinel.opened == 0
              and not os.path.exists(o))

        # read_plan refuses a malformed plan before anything is sent
        base_rows = _load_rows(plan)

        def plan_with(edit):
            rows2 = json.loads(json.dumps(base_rows))
            edit(rows2)
            p2 = os.path.join(tmp, "variant.jsonl")
            with open(p2, "w", encoding="utf-8", newline="\n") as f:
                for r in rows2:
                    f.write(_dumps(r) + "\n")
            return p2

        def rejected(path2):
            try:
                read_plan(path2)
                return False
            except PlanError:
                return True

        def double_press(rows2):
            rows2[2]["steps"].insert(1, {"key": "a", "at_ms": 1, "down": True})

        def stray_release(rows2):
            rows2[2]["steps"].insert(0, {"key": "b", "at_ms": 0, "down": False})

        def held_at_end(rows2):
            rows2[2]["steps"].append({"key": "c", "at_ms": 300, "down": True})

        def no_marker(rows2):
            del rows2[1]["marker"]

        def no_marker_gap(rows2):
            del rows2[1]["marker_gap_ms"]

        def repeated_trial(rows2):
            rows2[3]["trial_id"] = rows2[2]["trial_id"]

        def repeated_block(rows2):
            rows2[-4]["block_id"] = 0

        def too_late(rows2):
            rows2[2]["steps"].append({"key": "d", "at_ms": MAX_AT_MS + 1, "down": True})

        cases = {"a key pressed twice": double_press, "a release without a press": stray_release,
                 "a key still pressed at the end of a trial": held_at_end, "a block without marker": no_marker,
                 "a marker without marker_gap_ms": no_marker_gap, "a repeated trial_id": repeated_trial,
                 "a step later than the limit": too_late}
        for name, edit in cases.items():
            check("read_plan rejects %s" % name, rejected(plan_with(edit)))
        check("read_plan rejects a repeated block_id", rejected(plan_with(repeated_block)))
        p2 = os.path.join(tmp, "dup.jsonl")
        with open(plan, encoding="utf-8") as f:
            lines = f.read().splitlines()
        lines[1] = lines[1].replace('"kind":"block",', '"kind":"block","kind":"block",', 1)
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        check("read_plan rejects a repeated JSON key", rejected(p2))
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write(lines[0] + "\n" + "[" * 100000 + "\n")
        check("read_plan rejects deep nesting without crashing", rejected(p2))
        saved = globals()["MAX_PLAN_BYTES"]
        globals()["MAX_PLAN_BYTES"] = 100
        try:
            check("read_plan rejects an oversized file", rejected(plan))
        finally:
            globals()["MAX_PLAN_BYTES"] = saved

        # validation of the oracle beyond the basic shape
        good = open(out, encoding="utf-8").read()
        olines = good.splitlines()
        reset_row = _dumps({"kind": "reset", "block_id": 5, "exit_code": 0, "t_wall_ms": 1, "t_mono_ns": 1})
        extra = {
            "a boolean step": good.replace('"step":0,', '"step":false,', 1),
            "a key outside the enumeration": good.replace('"key":"a"', '"key":"zz"', 1),
            "a reset row with no block_begin after it": good + _dumps(
                {"kind": "reset", "block_id": 9, "exit_code": 0, "t_wall_ms": 1,
                 "t_mono_ns": 10 ** 18}) + "\n",
            "a reset row for another block": "\n".join(olines[:1] + [reset_row] + olines[1:]) + "\n",
        }
        for name, body in extra.items():
            p3 = os.path.join(tmp, "extra.jsonl")
            with open(p3, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            check("validation rejects %s" % name, _quiet_main(["--validate", p3]) == 1)

        # a key outside SUPPORTED_KEYS stops the run before anything is sent
        plan_bad = os.path.join(tmp, "plan_bad.jsonl")
        _make_plan(plan_bad, with_unsupported=True)
        o = os.path.join(tmp, "unsupported.jsonl")
        check("an unsupported key stops the run before any send",
              _quiet_main(["--plan", plan_bad, "--out", o, "--backend", "dryrun"]) == 2 and not os.path.exists(o))

        # the backend is always closed, including after a failure mid-run
        module = load_backend("dryrun")
        _, _, blocks = read_plan(plan)
        quiet = module.Backend({})
        try:
            with Emitter(quiet, os.path.join(tmp, "prechecked.jsonl"), "0" * 64, "macos", "dryrun",
                         supported_keys=["a"]) as em:
                em.run_block(blocks[0][0], blocks[0][1])
            stopped = False
        except PlanError:
            stopped = True
        check("Emitter checks every key of a block against supported_keys before sending",
              stopped and quiet.sends == 0)
        fake = types.SimpleNamespace(KEY_OPTIONS={"focus_away": "away_app"})
        check("a key that needs a backend option is refused when the option is missing",
              check_key_options(fake, {"a", "focus_away"}, {}) == ["focus_away"]
              and check_key_options(fake, {"a", "focus_away"}, {"away_app": "x"}) == [])
        check("the plan asks for an input-method reset in its second block", wants_reset(blocks))
        backend = module.Backend({"fail_after": "10"})
        try:
            with Emitter(backend, os.path.join(tmp, "failed.jsonl"), "0" * 64, "macos", "dryrun") as em:
                for block, trials in blocks:
                    em.run_block(block, trials)
            raised = False
        except RuntimeError:
            raised = True
        check("an injected failure propagates", raised)
        check("close ran exactly once after the failure", backend.closed == 1 and backend.opened == 1)
        check("an unknown backend option is refused",
              _quiet_main(["--plan", plan, "--out", os.path.join(tmp, "x.jsonl"), "--backend", "dryrun",
                    "--backend-opt", "bogus=1"]) == 2)
        check("an existing output file is not overwritten",
              _quiet_main(["--plan", plan, "--out", out, "--backend", "dryrun"]) == 2)

        # reset rows come before block_begin when the orchestrator writes them
        o2 = os.path.join(tmp, "reset.jsonl")
        with Emitter(module.Backend({}), o2, "0" * 64, "macos", "dryrun") as em:
            em.write_reset(0, 0)
            em.run_block(blocks[0][0], blocks[0][1][:1])
        check("a reset row before block_begin validates", _quiet_main(["--validate", o2]) == 0
              and [r["kind"] for r in _load_rows(o2)][1:3] == ["reset", "block_begin"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
