#!/usr/bin/env python3
# kit: selftest
"""Ground-truth session assembly: build the reference-side composition sessions from the raw
reference-input-field log (gt/1) and the executor's record (oracle/1). Uses only the Python
standard library.

  gt_sessions.py --gt GT --oracle ORACLE --rules RULES --out SESSIONS [--plan PLAN]
  gt_sessions.py --selftest       fast default (well under one second)

The unit of analysis is a single reference-side composition session (start = the first marked-text
or composition-start notification; end = a commit or a cancellation). The rules that define start,
commit, cancellation, and the candidate window are read from a configuration file (RULES, JSON).
The call order an input method engine follows is not documented by the operating system and must
instead be determined from the call sequences observed on real devices, so swapping the rule file
changes how sessions are assembled.

Rule file (one block per source; keys starting with `_` are annotations and are not read):
  {"mac_nstic": {"start": <condition>, "commit": [<condition>, ...], "cancel": [<condition>, ...],
                 "candidate": <condition>|null, "candidate_end": <condition>|null,
                 "clock_jump_ms": <integer>},
   "win_imm": {...}, "win_tsf": {...}}
  A condition is an event predicate: {"ev": <name>, <field>: <value>, <field>_gt|ge|lt|le|eq|ne: <integer>, ...}
    - A field must be one of the auxiliary fields the gt/1 schema defines for that event. Writing
      the bare field name asserts equality.
    - Only commit / cancel conditions may additionally carry:
        "after_result": true|false   whether a result string (result_len > 0) has appeared in the
                                       session so far
        "not_followed_by": <condition>, "within_ms": <integer>   no event matching the given
                                       condition arrives within within_ms (monotonic clock) after
                                       this event
      within_ms is required whenever not_followed_by is given, and vice versa. If no matching event
      arrives before the end of the log, it counts as "does not arrive".
      The window includes its end (an event exactly within_ms later still arrives). A log that ends
      within the window therefore closes the session as if nothing followed, not as log_truncated.
    - A trial with abandon = false and commit = none is counted as agreeing in oracle_agreement
      regardless of the assembled session's actual outcome, because the intent is "neither a commit
      nor a cancellation is expected" (see _classify).
  Events are scanned once, in order. When no session is open, a matching start event opens one.
  While a session is open, commit conditions are checked before cancel conditions, and the session
  closes on whichever matches first (the closing event's timestamp is the session end). A start
  event that matches while a session is already open has no effect on the session itself; its count
  is reported separately, for diagnostic purposes, as n_start_while_open. Likewise, an event that
  matches a commit condition's event and result requirement but does not close the session, because
  the condition's not_followed_by event arrives within its window, is counted as
  n_continued_commits (with live conversion on macOS, the input method commits the head of a reading
  while the rest is still being typed).
  A session is treated as having shown a candidate window if any event matching candidate occurs
  within the session interval (inclusive of both ends). When candidate is null, no candidate window
  can be detected, so candidate_shown is null and windows_def cannot be constructed either (null).
  candidate_end is the condition under which the candidate window is taken to have closed; it has
  the same shape as candidate (a single condition, or null). While the candidate window is open, an
  event matching candidate_end closes it (a later candidate event reopens it; a session may contain
  more than one candidate-window interval). If the candidate window has not closed by the time the
  session itself closes (via commit/cancel, or the log ends while open_at_end), it is counted as
  closing at the session's end. candidate_end is null whenever candidate is null.
  clock_jump_ms: if the skew between the wall-clock delta and the monotonic-clock delta of two
  adjacent events exceeds this threshold, the pair is treated as a clock jump and excluded.

Output (SESSIONS, JSON):
  {"src", "rules_sha256", "definitions": {"all", "macos_def", "windows_def"}, "oracle_agreement", "excluded"}
  - A session is {"trial_id", "start_wall_ms", "end_wall_ms", "start_mono_ns", "end_mono_ns",
    "outcome", "candidate_shown", "nav_true", "nav_true_cand", "excluded_by", "n_start_while_open",
    "n_continued_commits"}.
    outcome is commit, cancel, or open_at_end (the log ended before the session closed; end_* is
    null in that case). trial_id is assigned by the greatest overlap, in wall-clock time, with an
    oracle trial_begin..trial_end interval (ties go to the earlier trial); it is null when no trial
    overlaps. nav_true is the number of arrow-key presses (left/right/up/down) the executor sent
    within the session interval (inclusive of both ends, wall-clock time). The interval is taken
    from events the input method engine itself emitted, so the count and the interval it is drawn
    from do not depend on each other. The interval of an open_at_end session runs to the last event
    in the log. For a session that has a trial_id, nav_true and nav_true_cand count only presses the
    oracle recorded as sent for that same trial_id, so that a delayed commit does not pick up arrow
    presses belonging to the next trial. Only a session with a null trial_id counts every press sent
    within its interval.
    nav_true_cand applies to sources whose candidate condition is not null: it is the number of
    arrow presses (counted the same way as nav_true) that fall within the interval(s) during which
    the candidate window was open in that session (all of them, if candidate_end causes it to open
    and close more than once). It is null for a source whose candidate condition is null
    (mac_nstic). The reference definition of the Windows nav count follows the design document: the
    number of arrow presses while the candidate window was open; downstream analyses use this field
    for that quantity.
    excluded_by is an array of reasons this session was flagged for exclusion ("clock_jump",
    "log_truncated", or "carry_over"; empty if none apply). A session is flagged when its trial_id appears in the
    trial_ids of one of the excluded entries below, or when the session interval directly spans a
    clock jump.
    n_start_while_open is a diagnostic integer: the number of start events that arrived while this
    session was already open.
    n_continued_commits is a diagnostic integer: the number of commit events inside this session
    that were followed within the window by the condition's not_followed_by event and therefore did
    not close it (0 for rules without not_followed_by). The outcome is still the reason the session
    finally closed, so a session whose head was committed this way and whose rest was cancelled is a
    cancel; this count lets the analysis report agreement on abandonment separately for such sessions.
  - all is every composition. macos_def is every kana-input segment (identical to all on the
    reference side; the two definitions differ only in how the observation side counts them).
    windows_def contains only sessions during which a candidate window appeared (null when candidate
    is null for that source).
  - oracle_agreement: when --plan (plan/1) is given, each trial's planned intent (expect_session /
    abandon / commit) is compared against the assembled sessions (all). The plan file is required
    because oracle alone does not record intent; if its SHA-256 does not match the plan_sha256 in
    the oracle header, the plan is rejected. Disagreements are not discarded; they are counted by
    type (no_session / unexpected_session / multiple_sessions / expected_cancel_got_commit /
    expected_commit_got_cancel / open_at_end / unknown_trial). Without --plan, the field is only
    {"plan_available": false, "n_trials": N}, and the CLI prints a one-line warning to stderr noting
    that no agreement rate is available (the exit code remains 0).
  - excluded: clock jumps (clock_jump, in either the gt or the oracle log) and the trailing interval
    of a session that never closed before the log ended (log_truncated). In both cases the times are
    those of the two adjacent events (when the wall clock moves backward, end can precede start, in
    which case the monotonic clock decides ordering). Each entry carries trial_ids, the array of
    trial ids the jump or truncation affects. For an oracle-side jump, the range runs from the trial
    that contains the event immediately preceding the jump (or, if the jump falls between trials,
    the next trial) through the last trial of the same block. A gt-side jump follows the same logic,
    but its position is found by mapping the wall-clock time of the gt event immediately preceding
    the jump onto the oracle trial intervals; the trial_id of any session whose interval spans the
    jump is added as well. log_truncated carries the trial_id of the session that never closed
    (empty if that trial_id is null).
    carry_over: a session whose interval (start to end, or to the last event of the log if it never
    closed) overlaps the trial_begin..trial_end intervals of two or more trials (inclusive of both
    ends). The composition was not closed within its own trial and ran on into the next, so the
    trials are no longer independent: the earlier trial is left without a session of its own, and the
    later trial's session holds the earlier trial's text. Its trial_ids are every trial the session
    overlaps, in order, so that both the trial the text was carried from and the trial it was carried
    into are excluded. The times are the session's own. The agreement rate (oracle_agreement) is not
    changed by this entry: a carried trial still counts as a disagreement there.
    The check uses the gt wall clock, like the trial assignment. A forward clock jump inside a session
    lengthens its interval, so a session near the end of a block can also be reported as carry_over with the
    first trial of the next block, which the clock_jump entry (limited to its own block) does not list; this
    errs towards excluding more.

Only the reference side (gt / oracle / plan) is read here; the observation-side log is never read.
Validating the shape of gt / oracle rows is the job of the shared format validator; this module
reads only the fields it needs. Its own output carries only kinds, integers, and enumerated values,
never free text.
Input hardening: every JSON input (rules, gt, oracle, plan) is rejected if the same key appears
twice on one line (enforced via object_pairs_hook). Excessive nesting (RecursionError) is likewise
rejected as an input error. The oracle / plan trial_id is the only free-form string that is echoed
verbatim into the output, so it is constrained to `^[A-Za-z0-9_-]{1,64}$`. --out is rejected when it
names the same file as --gt/--oracle/--rules/--plan (checked via os.path.samefile); output is
written through a temporary file in the same directory and moved into place with os.replace.

Exit status: 0 on success; 2 if an input cannot be read, a rule file is malformed, or usage is
incorrect.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ARROW_KEYS = ("left", "right", "up", "down")
COMMIT_KINDS = ("enter", "digit", "mouse", "none")
MISMATCH_TYPES = (
    "no_session", "unexpected_session", "multiple_sessions",
    "expected_cancel_got_commit", "expected_commit_got_cancel", "open_at_end", "unknown_trial",
)
_OPS = ("gt", "ge", "lt", "le", "eq", "ne")
_BLOCK_KEYS = ("start", "commit", "cancel", "candidate", "candidate_end", "clock_jump_ms")
# The oracle / plan trial_id is the only free-form string echoed verbatim into the output
# (flagged as a low-severity security note in review).
TRIAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InputError(ValueError):
    """Raised when an input (log, rule file, or plan) cannot be read; leads to exit status 2."""


# ---------------------------------------------------------------- gt/1 schema (reused from the shared format validator)

def _gt_tables():
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "formats"))
    if d not in sys.path:
        sys.path.insert(0, d)
    import validate_logs
    return validate_logs.GT_EVENTS, validate_logs.GT_SRCS, validate_logs.ENUM_FIELDS


# ---------------------------------------------------------------- input parsing

def _no_float(_s):
    raise InputError("fractional or exponential numbers are not accepted")


def _no_constant(_s):
    raise InputError("NaN and Infinity are not accepted")


def _lines(data, what):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("%s: not valid UTF-8" % what)
    out = []
    for n, raw in enumerate(text.split("\n"), 1):
        raw = raw.rstrip("\r")
        if raw.strip():
            out.append((n, raw))
    return out


def _no_dup_pairs(pairs, where):
    seen = set()
    out = {}
    for k, v in pairs:
        if k in seen:
            raise InputError("%s: the same key appears twice on one line" % where)
        seen.add(k)
        out[k] = v
    return out


def _loads(raw, where):
    try:
        return json.loads(raw, parse_float=_no_float, parse_constant=_no_constant,
                          object_pairs_hook=lambda pairs: _no_dup_pairs(pairs, where))
    except InputError:
        raise
    except (ValueError, RecursionError):
        raise InputError("%s: not valid JSON" % where)


def _is_int(v):
    return type(v) is int


def _read_times(row, where):
    for k in ("t_wall_ms", "t_mono_ns"):
        if not _is_int(row.get(k)):
            raise InputError("%s: %s is not an integer" % (where, k))


def _check_trial_id(v, where):
    if not isinstance(v, str) or not TRIAL_ID_RE.fullmatch(v):
        raise InputError("%s: trial_id does not match the required shape (^[A-Za-z0-9_-]{1,64}$)" % where)


def parse_gt(data):
    """Parse gt/1 into (src, event list); events must be non-decreasing in the monotonic clock."""
    _events, srcs, _enum = _gt_tables()
    lines = _lines(data, "gt")
    if not lines:
        raise InputError("gt: empty file")
    hdr = _loads(lines[0][1], "gt:%d" % lines[0][0])
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "gt/1":
        raise InputError("gt: line 1 is not a gt/1 header")
    src = hdr.get("src")
    if src not in srcs:
        raise InputError("gt: the header's src is not a recognized value")
    events = []
    prev_mono = None
    for n, raw in lines[1:]:
        where = "gt:%d" % n
        row = _loads(raw, where)
        if not isinstance(row, dict) or row.get("kind") != "ev" or not isinstance(row.get("ev"), str):
            raise InputError("%s: not an ev row" % where)
        _read_times(row, where)
        if prev_mono is not None and row["t_mono_ns"] < prev_mono:
            raise InputError("%s: the monotonic clock goes backward (rows are not in time order)" % where)
        prev_mono = row["t_mono_ns"]
        events.append(row)
    return src, events


def parse_oracle(data):
    """Parse oracle/1 into {"plan_sha256", "rows" (everything but the header), "trials" (trial
    windows), "arrows" (wall-clock time and trial_id of each arrow-key press), "block_of_trial",
    "trials_by_block" (used to locate exclusions within a block)}.
    """
    lines = _lines(data, "oracle")
    if not lines:
        raise InputError("oracle: empty file")
    hdr = _loads(lines[0][1], "oracle:%d" % lines[0][0])
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "oracle/1":
        raise InputError("oracle: line 1 is not an oracle/1 header")
    rows, trials, arrows = [], [], []
    open_trial = None
    seen = set()
    prev_mono = None
    block_of_trial, trials_by_block = {}, {}
    cur_block = None
    for n, raw in lines[1:]:
        where = "oracle:%d" % n
        row = _loads(raw, where)
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
            raise InputError("%s: missing kind" % where)
        _read_times(row, where)
        if prev_mono is not None and row["t_mono_ns"] < prev_mono:
            raise InputError("%s: the monotonic clock goes backward (rows are not in time order)" % where)
        prev_mono = row["t_mono_ns"]
        rows.append(row)
        kind = row["kind"]
        if kind == "block_begin":
            cur_block = row.get("block_id")
            trials_by_block.setdefault(cur_block, [])
        elif kind == "block_end":
            cur_block = None
        elif kind == "trial_begin":
            tid = row.get("trial_id")
            if open_trial is not None:
                raise InputError("%s: trials are nested" % where)
            _check_trial_id(tid, where)
            if tid in seen:
                raise InputError("%s: duplicate trial_id" % where)
            seen.add(tid)
            open_trial = {"trial_id": tid, "begin_wall_ms": row["t_wall_ms"]}
            if cur_block is not None:
                block_of_trial[tid] = cur_block
                trials_by_block.setdefault(cur_block, []).append(tid)
        elif kind == "trial_end":
            if open_trial is None or row.get("trial_id") != open_trial["trial_id"]:
                raise InputError("%s: trial_end does not match the open trial_begin" % where)
            open_trial["end_wall_ms"] = row["t_wall_ms"]
            trials.append(open_trial)
            open_trial = None
        elif kind == "sent":
            tid = row.get("trial_id")
            _check_trial_id(tid, where)
            if row.get("key") in ARROW_KEYS and row.get("down") is True:
                arrows.append((row["t_wall_ms"], tid))
    if open_trial is not None:
        raise InputError("oracle: a trial ended without a matching trial_end")
    return {"plan_sha256": hdr.get("plan_sha256"), "rows": rows, "trials": trials, "arrows": arrows,
            "block_of_trial": block_of_trial, "trials_by_block": trials_by_block}


def parse_plan(data):
    """Parse plan/1 into {trial_id: intent}, validating only the three intent fields that are used."""
    lines = _lines(data, "plan")
    if not lines:
        raise InputError("plan: empty file")
    hdr = _loads(lines[0][1], "plan:%d" % lines[0][0])
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "plan/1":
        raise InputError("plan: line 1 is not a plan/1 header")
    out = {}
    for n, raw in lines[1:]:
        where = "plan:%d" % n
        row = _loads(raw, where)
        if not isinstance(row, dict) or row.get("kind") != "trial":
            continue
        tid, it = row.get("trial_id"), row.get("intent")
        _check_trial_id(tid, where)
        if tid in out:
            raise InputError("%s: duplicate trial_id" % where)
        if (not isinstance(it, dict) or type(it.get("expect_session")) is not bool
                or type(it.get("abandon")) is not bool or it.get("commit") not in COMMIT_KINDS):
            raise InputError("%s: intent has the wrong shape (expect_session / abandon / commit)" % where)
        out[tid] = {"expect_session": it["expect_session"], "abandon": it["abandon"], "commit": it["commit"]}
    return out


# ---------------------------------------------------------------- rule compilation

class _Cond(object):
    __slots__ = ("ev", "preds", "after_result", "nfb", "within_ms")

    def __init__(self, ev, preds, after_result, nfb, within_ms):
        self.ev, self.preds, self.after_result, self.nfb, self.within_ms = ev, preds, after_result, nfb, within_ms


class _Block(object):
    __slots__ = ("start", "commit", "cancel", "candidate", "candidate_end", "clock_jump_ms")


def _compile_pred(key, val, ev_fields, enum_fields, where):
    field, op = key, "eq"
    head, sep, tail = key.rpartition("_")
    if sep and tail in _OPS and head in ev_fields:
        field, op = head, tail
    if field not in ev_fields:
        raise InputError("%s: condition key '%s' is neither a field of this event nor a reserved word" % (where, key))
    if field in enum_fields:
        if op not in ("eq", "ne") or not isinstance(val, str) or val not in enum_fields[field]:
            raise InputError("%s: '%s' is an enumerated field (only eq / ne with an enumerated value are allowed)" % (where, key))
    elif not _is_int(val):
        raise InputError("%s: the value of '%s' must be an integer" % (where, key))
    return (field, op, val)


def _compile_cond(obj, where, src, role):
    events, _srcs, enum_fields = _gt_tables()
    if not isinstance(obj, dict):
        raise InputError("%s: a condition must be an object" % where)
    ev = obj.get("ev")
    if not isinstance(ev, str) or ev not in events[src]:
        raise InputError("%s: ev is missing, or is not an event of this source" % where)
    req, opt = events[src][ev]
    ev_fields = set(req + opt)
    closing = role in ("commit", "cancel")
    preds, after_result, nfb, within_ms = [], None, None, None
    for key, val in obj.items():
        if key == "ev":
            continue
        if key in ("after_result", "not_followed_by", "within_ms"):
            if not closing:
                raise InputError("%s: '%s' may appear only in a commit / cancel condition" % (where, key))
            if key == "after_result":
                if type(val) is not bool:
                    raise InputError("%s: after_result must be a boolean" % where)
                after_result = val
            elif key == "within_ms":
                if not _is_int(val) or val < 0:
                    raise InputError("%s: within_ms must be a non-negative integer" % where)
                within_ms = val
            else:
                nfb = _compile_cond(val, where + ".not_followed_by", src, "nested")
            continue
        preds.append(_compile_pred(key, val, ev_fields, enum_fields, where))
    if (nfb is None) != (within_ms is None):
        raise InputError("%s: not_followed_by and within_ms must be given together" % where)
    return _Cond(ev, tuple(preds), after_result, nfb, within_ms)


def _compile_block(src, obj):
    where = src
    if not isinstance(obj, dict):
        raise InputError("%s: a source's rule block must be an object" % where)
    for key in obj:
        if key not in _BLOCK_KEYS and not key.startswith("_"):
            raise InputError("%s: unknown key '%s'" % (where, key))
    for key in _BLOCK_KEYS:
        if key not in obj:
            raise InputError("%s: '%s' is missing (candidate / candidate_end may be null)" % (where, key))
    b = _Block()
    b.start = _compile_cond(obj["start"], where + ".start", src, "start")
    for role in ("commit", "cancel"):
        lst = obj[role]
        if not isinstance(lst, list) or (role == "commit" and not lst):
            raise InputError("%s: %s must be an array (commit requires at least one entry)" % (where, role))
        setattr(b, role, tuple(_compile_cond(c, "%s.%s[%d]" % (where, role, i), src, role)
                               for i, c in enumerate(lst)))
    b.candidate = (None if obj["candidate"] is None
                   else _compile_cond(obj["candidate"], where + ".candidate", src, "candidate"))
    b.candidate_end = (None if obj["candidate_end"] is None
                        else _compile_cond(obj["candidate_end"], where + ".candidate_end", src, "candidate_end"))
    if not _is_int(obj["clock_jump_ms"]) or obj["clock_jump_ms"] < 0:
        raise InputError("%s: clock_jump_ms must be a non-negative integer" % where)
    b.clock_jump_ms = obj["clock_jump_ms"]
    return b


def compile_rules(rules_bytes):
    _events, srcs, _enum = _gt_tables()
    try:
        obj = _loads(rules_bytes.decode("utf-8"), "rules")
    except UnicodeDecodeError:
        raise InputError("rules: not valid UTF-8")
    if not isinstance(obj, dict):
        raise InputError("rules: the top level must be an object")
    out = {}
    for src, block in obj.items():
        if src.startswith("_"):
            continue
        if src not in srcs:
            raise InputError("rules: unknown src '%s'" % src)
        out[src] = _compile_block(src, block)
    if not out:
        raise InputError("rules: no source rule blocks are present")
    return out


# ---------------------------------------------------------------- event matching

def _ev_match(c, e):
    if e.get("ev") != c.ev:
        return False
    for field, op, val in c.preds:
        v = e.get(field)
        if isinstance(val, str):
            if not isinstance(v, str):
                return False
            if (v == val) != (op == "eq"):
                return False
            continue
        if not _is_int(v):
            return False
        if op == "eq" and not v == val:
            return False
        if op == "ne" and not v != val:
            return False
        if op == "gt" and not v > val:
            return False
        if op == "ge" and not v >= val:
            return False
        if op == "lt" and not v < val:
            return False
        if op == "le" and not v <= val:
            return False
    return True


def _has_result(e):
    v = e.get("result_len")
    return _is_int(v) and v > 0


def _close_match(c, events, i, result_seen):
    e = events[i]
    if not _ev_match(c, e):
        return False
    if c.after_result is not None and result_seen != c.after_result:
        return False
    if c.nfb is not None:
        limit = e["t_mono_ns"] + c.within_ms * 1000000
        for j in range(i + 1, len(events)):
            f = events[j]
            if f["t_mono_ns"] > limit:
                break
            if _ev_match(c.nfb, f):
                return False
    return True


def _run_machine(b, events):
    sessions = []
    cur = None
    for i, e in enumerate(events):
        if cur is None:
            if _ev_match(b.start, e):
                cand_open = e["t_wall_ms"] if (b.candidate is not None and _ev_match(b.candidate, e)) else None
                cur = {"start_wall_ms": e["t_wall_ms"], "start_mono_ns": e["t_mono_ns"],
                       "result_seen": _has_result(e),
                       "candidate_shown": None if b.candidate is None else (cand_open is not None),
                       "n_start_while_open": 0, "n_continued_commits": 0,
                       "cand_open": cand_open, "cand_windows": []}
            continue
        if _ev_match(b.start, e):
            cur["n_start_while_open"] += 1
        if _has_result(e):
            cur["result_seen"] = True
        if b.candidate is not None:
            if cur["cand_open"] is None:
                if _ev_match(b.candidate, e):
                    cur["cand_open"] = e["t_wall_ms"]
                    cur["candidate_shown"] = True
            elif b.candidate_end is not None and _ev_match(b.candidate_end, e):
                cur["cand_windows"].append((cur["cand_open"], e["t_wall_ms"]))
                cur["cand_open"] = None
        outcome = None
        for c in b.commit:
            if _close_match(c, events, i, cur["result_seen"]):
                outcome = "commit"
                break
        if outcome is None:
            for c in b.cancel:
                if _close_match(c, events, i, cur["result_seen"]):
                    outcome = "cancel"
                    break
        if outcome is None:
            # A commit event that did not close the session can only have failed its not_followed_by test.
            for c in b.commit:
                if (c.nfb is not None and _ev_match(c, e)
                        and (c.after_result is None or cur["result_seen"] == c.after_result)):
                    cur["n_continued_commits"] += 1
                    break
        if outcome is not None:
            cur["end_wall_ms"], cur["end_mono_ns"], cur["outcome"] = e["t_wall_ms"], e["t_mono_ns"], outcome
            sessions.append(cur)
            cur = None
    if cur is not None:
        cur["end_wall_ms"], cur["end_mono_ns"], cur["outcome"] = None, None, "open_at_end"
        sessions.append(cur)
    return sessions


# ---------------------------------------------------------------- exclusion, trial assignment, nav counts, and agreement

def _skew_ms(skew_ns):
    q = abs(skew_ns) // 1000000
    return q if skew_ns >= 0 else -q


def _clock_jumps(rows, log, threshold_ms):
    out = []
    for prev, cur in zip(rows, rows[1:]):
        skew_ns = ((cur["t_wall_ms"] - prev["t_wall_ms"]) * 1000000
                   - (cur["t_mono_ns"] - prev["t_mono_ns"]))
        if abs(skew_ns) > threshold_ms * 1000000:
            out.append({"type": "clock_jump", "log": log,
                        "start_wall_ms": prev["t_wall_ms"], "end_wall_ms": cur["t_wall_ms"],
                        "start_mono_ns": prev["t_mono_ns"], "end_mono_ns": cur["t_mono_ns"],
                        "skew_ms": _skew_ms(skew_ns)})
    return out


def _assign_trial(lo_wall, hi_wall, trials):
    best, best_ov = None, 0
    for tr in trials:
        ov = min(hi_wall, tr["end_wall_ms"]) - max(lo_wall, tr["begin_wall_ms"])
        point = (lo_wall == hi_wall and tr["begin_wall_ms"] <= lo_wall <= tr["end_wall_ms"])
        if (ov > best_ov) or (best is None and point):
            best, best_ov = tr, max(ov, 0)
    return None if best is None else best["trial_id"]


def _spanned_trial_ids(lo_wall, hi_wall, trials):
    """The trial_ids whose trial_begin..trial_end interval overlaps [lo_wall, hi_wall] (inclusive of
    both ends), in the oracle's order. Two or more means the session ran on from one trial into the
    next (carry_over)."""
    return [tr["trial_id"] for tr in trials
            if lo_wall <= tr["end_wall_ms"] and hi_wall >= tr["begin_wall_ms"]]


def _count_arrows(arrows, lo, hi, trial_id):
    """arrows is [(t_wall_ms, trial_id), ...]. Within the interval [lo, hi] (inclusive of both
    ends), count only presses sent for the given trial_id when one is supplied, so that a delayed
    commit does not pick up arrow presses belonging to the next trial. When trial_id is None, count
    every press in the interval."""
    n = 0
    for t, tid in arrows:
        if lo <= t <= hi and (trial_id is None or tid == trial_id):
            n += 1
    return n


def _locate_trial(t_wall_ms, trials):
    """Map a wall-clock time t onto the oracle's trial sequence: the trial containing it if one
    exists, otherwise the next trial to begin, or None if neither exists. Used to locate the
    trial_ids of an exclusion. trials is assumed to be in chronological order."""
    for tr in trials:
        if tr["begin_wall_ms"] <= t_wall_ms <= tr["end_wall_ms"]:
            return tr["trial_id"]
    for tr in trials:
        if tr["begin_wall_ms"] > t_wall_ms:
            return tr["trial_id"]
    return None


def _block_range_trial_ids(trial_id, block_of_trial, trials_by_block):
    """The sequence of trial_ids from a located trial through the last trial of the same block."""
    if trial_id is None:
        return []
    block_id = block_of_trial.get(trial_id)
    if block_id is None:
        return [trial_id]
    ids = trials_by_block.get(block_id, [])
    if trial_id not in ids:
        return [trial_id]
    return list(ids[ids.index(trial_id):])


def _jump_trial_ids(prev_wall_ms, oracle):
    pos = _locate_trial(prev_wall_ms, oracle["trials"])
    return _block_range_trial_ids(pos, oracle["block_of_trial"], oracle["trials_by_block"])


def _classify(intent, sess):
    if intent is None:
        return "unknown_trial"
    n = len(sess)
    if not intent["expect_session"]:
        return None if n == 0 else "unexpected_session"
    if n == 0:
        return "no_session"
    if n >= 2:
        return "multiple_sessions"
    s = sess[0]
    if s["outcome"] == "open_at_end":
        return "open_at_end"
    want = "cancel" if intent["abandon"] else ("commit" if intent["commit"] != "none" else None)
    if want is None or want == s["outcome"]:
        return None
    return "expected_cancel_got_commit" if want == "cancel" else "expected_commit_got_cancel"


def _agreement(trials, sessions, plan):
    if plan is None:
        return {"plan_available": False, "n_trials": len(trials)}
    by_trial = {}
    for s in sessions:
        if s["trial_id"] is not None:
            by_trial.setdefault(s["trial_id"], []).append(s)
    counts = dict((t, 0) for t in MISMATCH_TYPES)
    mismatches = []
    agree = 0
    for tr in trials:
        tid = tr["trial_id"]
        kind = _classify(plan.get(tid), by_trial.get(tid, []))
        if kind is None:
            agree += 1
        else:
            counts[kind] += 1
            mismatches.append({"trial_id": tid, "type": kind})
    n = len(trials)
    return {"plan_available": True, "n_trials": n, "n_agree": agree,
            "rate": (agree / n if n else None), "by_type": counts, "mismatches": mismatches,
            "unassigned_sessions": sum(1 for s in sessions if s["trial_id"] is None)}


# ---------------------------------------------------------------- assembly

def assemble(gt_bytes, oracle_bytes, rules_bytes, plan_bytes=None):
    rules = compile_rules(rules_bytes)
    src, events = parse_gt(gt_bytes)
    if src not in rules:
        raise InputError("rules: no rule block for this gt's src (%s)" % src)
    b = rules[src]
    oracle = parse_oracle(oracle_bytes)
    plan = None
    if plan_bytes is not None:
        plan = parse_plan(plan_bytes)
        want = oracle["plan_sha256"]
        if not isinstance(want, str) or want.lower() != hashlib.sha256(plan_bytes).hexdigest():
            raise InputError("plan: SHA-256 does not match the oracle header's plan_sha256 (unrelated plan)")

    sessions = _run_machine(b, events)
    last_wall = events[-1]["t_wall_ms"] if events else None
    for s in sessions:
        hi = last_wall if s["end_wall_ms"] is None else s["end_wall_ms"]
        s["_hi"] = hi
        s["trial_id"] = _assign_trial(s["start_wall_ms"], hi, oracle["trials"])
        s["nav_true"] = _count_arrows(oracle["arrows"], s["start_wall_ms"], hi, s["trial_id"])
        if b.candidate is None:
            s["nav_true_cand"] = None
        else:
            windows = list(s["cand_windows"])
            if s["cand_open"] is not None:
                windows.append((s["cand_open"], hi))
            s["nav_true_cand"] = sum(_count_arrows(oracle["arrows"], lo_c, hi_c, s["trial_id"])
                                     for lo_c, hi_c in windows)

    # ---- exclusion (excluded entries' trial_ids, and each session's excluded_by / n_start_while_open)
    gt_jumps = _clock_jumps(events, "gt", b.clock_jump_ms)
    for j in gt_jumps:
        j["trial_ids"] = _jump_trial_ids(j["start_wall_ms"], oracle)
        lo_j, hi_j = sorted((j["start_wall_ms"], j["end_wall_ms"]))
        for s in sessions:
            if (s["trial_id"] is not None and s["trial_id"] not in j["trial_ids"]
                    and s["start_wall_ms"] <= lo_j and s["_hi"] >= hi_j):
                j["trial_ids"].append(s["trial_id"])
    oc_jumps = _clock_jumps(oracle["rows"], "oracle", b.clock_jump_ms)
    for j in oc_jumps:
        j["trial_ids"] = _jump_trial_ids(j["start_wall_ms"], oracle)
    excluded = gt_jumps + oc_jumps
    if sessions and sessions[-1]["outcome"] == "open_at_end":
        s = sessions[-1]
        excluded.append({"type": "log_truncated", "log": "gt",
                         "start_wall_ms": s["start_wall_ms"], "end_wall_ms": last_wall,
                         "start_mono_ns": s["start_mono_ns"], "end_mono_ns": events[-1]["t_mono_ns"],
                         "trial_ids": [] if s["trial_id"] is None else [s["trial_id"]]})
    for s in sessions:
        spanned = _spanned_trial_ids(s["start_wall_ms"], s["_hi"], oracle["trials"])
        if len(spanned) >= 2:
            excluded.append({"type": "carry_over", "log": "gt",
                             "start_wall_ms": s["start_wall_ms"], "end_wall_ms": s["_hi"],
                             "start_mono_ns": s["start_mono_ns"],
                             "end_mono_ns": events[-1]["t_mono_ns"] if s["end_mono_ns"] is None else s["end_mono_ns"],
                             "trial_ids": spanned})

    for s in sessions:
        reasons = set()
        for e in excluded:
            if s["trial_id"] is not None and s["trial_id"] in e.get("trial_ids", ()):
                reasons.add(e["type"])
            if e["type"] == "clock_jump":
                lo_j, hi_j = sorted((e["start_wall_ms"], e["end_wall_ms"]))
                if s["start_wall_ms"] <= lo_j and s["_hi"] >= hi_j:
                    reasons.add("clock_jump")
        s["excluded_by"] = sorted(reasons)

    pub = [{"trial_id": s["trial_id"], "start_wall_ms": s["start_wall_ms"], "end_wall_ms": s["end_wall_ms"],
            "start_mono_ns": s["start_mono_ns"], "end_mono_ns": s["end_mono_ns"], "outcome": s["outcome"],
            "candidate_shown": s["candidate_shown"], "nav_true": s["nav_true"], "nav_true_cand": s["nav_true_cand"],
            "excluded_by": s["excluded_by"], "n_start_while_open": s["n_start_while_open"],
            "n_continued_commits": s["n_continued_commits"]} for s in sessions]
    return {
        "src": src,
        "rules_sha256": hashlib.sha256(rules_bytes).hexdigest(),
        "definitions": {
            "all": list(pub),
            "macos_def": list(pub),
            "windows_def": None if b.candidate is None else [s for s in pub if s["candidate_shown"]],
        },
        "oracle_agreement": _agreement(oracle["trials"], pub, plan),
        "excluded": excluded,
    }


def render(result):
    """Render the output bytes (LF line endings, UTF-8, no BOM). The same input always yields the
    same bytes."""
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read(path, what):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise InputError("%s: cannot be opened (%s)" % (what, type(e).__name__))


def _same_file(a, b):
    """Whether --out names the same file as an existing input (a low-severity security check).
    Either path missing is treated as distinct files."""
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def build_sessions(gt_path, oracle_path, rules_path, plan_path=None):
    return assemble(_read(gt_path, "gt"), _read(oracle_path, "oracle"), _read(rules_path, "rules"),
                    None if plan_path is None else _read(plan_path, "plan"))


# ---------------------------------------------------------------- selftest

WALL0 = 1700000000000
MONO0 = 50000000


def _jl(*rows):
    return ("\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n").encode("utf-8")


def _gt_bytes(src, evs, wall_shift=None):
    """evs is [(t_ms, ev, {fields}), ...]. wall_shift is {index: milliseconds added to the wall
    clock only}, applied to that event and every one after it."""
    rows = [{"kind": "header", "format": "gt/1", "src": src, "app_version": "0.1.0",
             "os": "macos" if src == "mac_nstic" else "windows"}]
    shift = 0
    for i, (t, ev, kw) in enumerate(evs):
        shift += (wall_shift or {}).get(i, 0)
        row = {"kind": "ev", "t_wall_ms": WALL0 + t + shift, "t_mono_ns": (MONO0 + t) * 1000000, "ev": ev}
        row.update(kw)
        rows.append(row)
    return _jl(*rows)


def _oracle_bytes(trials, sents=(), plan_sha="0" * 64, wall_shift=None):
    """trials is [(trial_id, begin_ms, end_ms)]; sents is [(t_ms, key, down)]."""
    items = [(min(t[1] for t in trials) - 50 if trials else 0, 0, {"kind": "block_begin", "block_id": 0})]
    for tid, b, e in trials:
        items.append((b, 1, {"kind": "trial_begin", "trial_id": tid}))
        items.append((e, 3, {"kind": "trial_end", "trial_id": tid}))
    for k, (t, key, down) in enumerate(sents):
        tid = [x[0] for x in trials if x[1] <= t <= x[2]][0]
        items.append((t, 2, {"kind": "sent", "trial_id": tid, "step": k, "key": key, "down": down}))
    if trials:
        items.append((max(t[2] for t in trials) + 50, 4, {"kind": "block_end", "block_id": 0}))
    items.sort(key=lambda x: (x[0], x[1]))
    rows = [{"kind": "header", "format": "oracle/1", "plan_sha256": plan_sha, "os": "macos",
             "emitter_version": "0", "backend": "dryrun"}]
    shift = 0
    for i, (t, _o, row) in enumerate(items):
        shift += (wall_shift or {}).get(i, 0)
        r = dict(row)
        r["t_wall_ms"], r["t_mono_ns"] = WALL0 + t + shift, (MONO0 + t) * 1000000
        rows.append(r)
    return _jl(*rows)


def _plan_bytes(intents):
    rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1, "generator_version": "0",
             "config_sha256": "0" * 64}]
    for tid, (expect, abandon, commit) in intents:
        rows.append({"kind": "trial", "trial_id": tid, "block_id": 0,
                     "intent": {"expect_session": expect, "abandon": abandon, "commit": commit}})
    return _jl(*rows)


def _example_rules():
    with open(os.path.join(_HERE, "gt_rules.example.json"), "rb") as f:
        return f.read()


def _rules_with(mut):
    obj = json.loads(_example_rules().decode("utf-8"))
    mut(obj)
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _sm(t, n):
    return (t, "set_marked", {"len": n, "sel_loc": n, "sel_len": 0})


def _ins(t, n):
    return (t, "insert_text", {"len": n})


def _wins(trials, sessions):
    return [x for x in sessions if x["trial_id"] in trials]


def _case_five_types():
    """Five session types: basic (start then commit), cancellation, with and without a candidate
    window, two sessions within a single trial, and a session left open when the log ends -- checked
    across all three input sources."""
    f = []
    rules = _example_rules()
    # --- mac_nstic: basic / cancellation / two sessions / looks like a cancellation but commits / left open
    evs = [
        _sm(100, 1), _sm(300, 3), _ins(500, 2),                                   # t00001 basic
        _sm(2100, 2), _sm(2300, 0), (2600, "key_down", {"vk": 53}),               # t00002 cancellation
        _sm(4100, 1), _ins(4200, 1), _sm(4400, 1), (4500, "unmark", {}),          # t00003 two sessions
        _sm(6100, 2), _sm(6200, 0), _ins(6230, 2),                                # t00004 clears then commits immediately
        _sm(8100, 1), (8300, "focus_out", {}),                                    # t00005 left open
    ]
    trials = [("t00001", 0, 900), ("t00002", 2000, 2900), ("t00003", 4000, 4900),
              ("t00004", 6000, 6900), ("t00005", 8000, 8900)]
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials), rules)
    got = [(s["trial_id"], s["outcome"], s["start_wall_ms"] - WALL0,
            None if s["end_wall_ms"] is None else s["end_wall_ms"] - WALL0) for s in r["definitions"]["all"]]
    want = [("t00001", "commit", 100, 500), ("t00002", "cancel", 2100, 2300), ("t00003", "commit", 4100, 4200),
            ("t00003", "commit", 4400, 4500), ("t00004", "commit", 6100, 6230), ("t00005", "open_at_end", 8100, None)]
    if got != want:
        f.append("the five mac session types do not match expectations: %r" % (got,))
    if len(_wins(("t00003",), r["definitions"]["all"])) != 2:
        f.append("mac: a single trial does not yield two sessions")
    if r["definitions"]["windows_def"] is not None or any(s["candidate_shown"] is not None for s in r["definitions"]["all"]):
        f.append("mac: no candidate window is observable (windows_def and candidate_shown must both be null)")
    if r["definitions"]["macos_def"] != r["definitions"]["all"]:
        f.append("macos_def differs from all")
    last = r["definitions"]["all"][-1]
    if last["end_wall_ms"] is not None or last["end_mono_ns"] is not None:
        f.append("the open_at_end session's end_* fields are not null")
    if [x["type"] for x in r["excluded"]] != ["log_truncated"]:
        f.append("a log that ends without closing does not appear in excluded: %r" % (r["excluded"],))

    # --- win_imm: with and without a candidate window (imn 6 does not count) / cancellation / two sessions / left open
    comp = lambda t, cl, rl: (t, "comp", {"flags": 8, "comp_len": cl, "result_len": rl})
    evs = [
        (100, "start_comp", {}), comp(200, 2, 0), (250, "notify", {"imn": 5}), comp(500, 0, 2), (520, "end_comp", {}),
        (2100, "start_comp", {}), comp(2200, 2, 0), (2250, "notify", {"imn": 6}), (2300, "end_comp", {}),
        (4100, "start_comp", {}), comp(4200, 0, 2), (4250, "end_comp", {}),
        (4400, "start_comp", {}), (4450, "notify", {"imn": 5}), comp(4500, 0, 2), (4550, "end_comp", {}),
        (6100, "start_comp", {}), comp(6200, 2, 0), (6300, "focus_out", {}),
    ]
    trials = [("t00001", 0, 900), ("t00002", 2000, 2900), ("t00003", 4000, 4900), ("t00004", 6000, 6900)]
    r = assemble(_gt_bytes("win_imm", evs), _oracle_bytes(trials), rules)
    got = [(s["trial_id"], s["outcome"], s["candidate_shown"]) for s in r["definitions"]["all"]]
    want = [("t00001", "commit", True), ("t00002", "cancel", False), ("t00003", "commit", False),
            ("t00003", "commit", True), ("t00004", "open_at_end", False)]
    if got != want:
        f.append("the five win_imm session types do not match expectations: %r" % (got,))
    wd = r["definitions"]["windows_def"]
    if wd is None or [(s["trial_id"], s["start_wall_ms"] - WALL0) for s in wd] != [("t00001", 100), ("t00003", 4400)]:
        f.append("windows_def does not contain only the sessions that showed a candidate window: %r" % (wd,))
    if len(r["definitions"]["all"]) != 5:
        f.append("all does not contain every composition")

    # --- win_tsf: with a candidate window / a reading-only window does not count / a zero-length result is a cancellation / left open
    evs = [
        (100, "tsf_start", {}), (200, "tsf_update", {"len": 2}), (250, "ui_begin", {"ui_kind": "candidate"}),
        (400, "ui_end", {"ui_kind": "candidate"}), (500, "tsf_end", {"result_len": 2}),
        (2100, "tsf_start", {}), (2200, "tsf_update", {"len": 2}), (2250, "ui_begin", {"ui_kind": "reading"}),
        (2300, "tsf_end", {}),
        (4100, "tsf_start", {}), (4200, "tsf_end", {"result_len": 0}),
        (6100, "tsf_start", {}),
    ]
    trials = [("t00001", 0, 900), ("t00002", 2000, 2900), ("t00003", 4000, 4900), ("t00004", 6000, 6900)]
    r = assemble(_gt_bytes("win_tsf", evs), _oracle_bytes(trials), rules)
    got = [(s["trial_id"], s["outcome"], s["candidate_shown"]) for s in r["definitions"]["all"]]
    want = [("t00001", "commit", True), ("t00002", "cancel", False), ("t00003", "cancel", False),
            ("t00004", "open_at_end", False)]
    if got != want:
        f.append("the five win_tsf session types do not match expectations: %r" % (got,))
    if r["definitions"]["windows_def"] is None or len(r["definitions"]["windows_def"]) != 1:
        f.append("win_tsf: windows_def is not the single session that showed a candidate window")
    return f


def _case_nav_interval():
    """nav_true counts only arrow presses within the session interval; arrows outside the interval
    are not counted."""
    f = []
    evs = [_sm(500, 1), _ins(1000, 1)]
    trials = [("t00001", 0, 2000)]
    sents = [(100, "left", True), (200, "right", True),                       # before the interval (not counted)
             (500, "left", True),                                             # coincides with the start (counted; both ends are inclusive)
             (600, "right", True), (650, "right", False), (700, "a", True),   # a key release and a non-arrow key are not counted
             (800, "down", True), (1000, "up", True),                         # coincides with the end (counted)
             (1500, "left", True)]                                            # after the interval (not counted)
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials, sents), _example_rules())
    got = [s["nav_true"] for s in r["definitions"]["all"]]
    if got != [4]:
        f.append("nav_true is not 4, the count of presses within the interval: %r" % (got,))
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials, [(100, "left", True), (1500, "left", True)]),
                 _example_rules())
    if [s["nav_true"] for s in r["definitions"]["all"]] != [0]:
        f.append("nav_true is not 0 when only out-of-interval arrows are present")
    return f


def _case_nav_true_cand():
    """nav_true_cand: the count of arrow presses within the intervals during which the candidate
    window was open, per the design document. It is null for mac_nstic."""
    f = []
    comp = lambda t, cl, rl: (t, "comp", {"flags": 8, "comp_len": cl, "result_len": rl})
    evs = [
        (100, "start_comp", {}), (150, "notify", {"imn": 5}),                  # candidate window opens (first time)
        comp(200, 2, 0),
        (300, "notify", {"imn": 4}),                                          # closes (first time)
        (350, "notify", {"imn": 5}),                                          # opens (second time)
        comp(400, 0, 2), (420, "notify", {"imn": 4}),                        # closes (second time; a result is present)
        (500, "end_comp", {}),                                                # commit (after_result is true)
    ]
    trials = [("t00001", 0, 900)]
    sents = [(120, "left", True),    # before the candidate window (not counted toward cand; counted toward nav_true)
             (180, "right", True),   # window 1 (counted toward cand)
             (320, "up", True),      # between the two windows (not counted toward cand)
             (380, "down", True),    # window 2 (counted toward cand)
             (600, "left", True)]    # after the session (not counted toward either)
    r = assemble(_gt_bytes("win_imm", evs), _oracle_bytes(trials, sents), _example_rules())
    s = r["definitions"]["all"][0]
    if s["nav_true"] != 4:
        f.append("nav_true is not 4, the count over the whole interval (120/180/320/380): %r" % (s["nav_true"],))
    if s["nav_true_cand"] != 2:
        f.append("nav_true_cand is not 2, the count within the candidate-window intervals only (180/380): %r" % (s["nav_true_cand"],))

    # mac_nstic has a null candidate condition, so nav_true_cand is null as well
    r2 = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(500, 2)]),
                  _oracle_bytes([("t00001", 0, 900)], [(200, "left", True)]), _example_rules())
    if r2["definitions"]["all"][0]["nav_true_cand"] is not None:
        f.append("mac_nstic's nav_true_cand is not null")

    # changing the rules changes the result (negative control): setting candidate_end to null keeps
    # the window open until the session ends
    r3 = assemble(_gt_bytes("win_imm", evs), _oracle_bytes(trials, sents),
                 _rules_with(lambda o: o["win_imm"].__setitem__("candidate_end", None)))
    if r3["definitions"]["all"][0]["nav_true_cand"] != 3:  # 180, 320, 380 (open continuously from 150 onward)
        f.append("nav_true_cand does not change when candidate_end is set to null: %r" %
                 (r3["definitions"]["all"][0]["nav_true_cand"],))
    return f


def _case_nav_trial_scope():
    """nav_true and nav_true_cand count only the sent events belonging to the trial a session was
    assigned to, so that a delayed commit does not pick up arrow presses belonging to the next
    trial. A session with a null trial_id counts every sent event within its interval instead."""
    f = []
    # Session A: mac_nstic, starts at 100, commits only at 950 (assigned to trial t00001 = [0, 900],
    # but its interval extends slightly into the adjacent trial t00002 = [920, 1900]).
    # Session B: 3000 to 3200, overlaps neither trial (trial_id is null).
    evs = [_sm(100, 1), _ins(950, 2), _sm(3000, 1), _ins(3200, 2)]
    rows = [{"kind": "header", "format": "oracle/1", "plan_sha256": "0" * 64, "os": "macos",
             "emitter_version": "0", "backend": "dryrun"}]

    def row(kind, t, **kw):
        r = dict(kw, kind=kind, t_wall_ms=WALL0 + t, t_mono_ns=(MONO0 + t) * 1000000)
        return r

    rows += [
        row("trial_begin", 0, trial_id="t00001"),
        row("sent", 200, trial_id="t00001", step=0, key="left", down=True),
        row("trial_end", 900, trial_id="t00001"),
        row("trial_begin", 920, trial_id="t00002"),
        row("sent", 940, trial_id="t00002", step=0, key="right", down=True),   # an arrow belonging to the adjacent trial (not counted)
        row("sent", 1300, trial_id="t00002", step=1, key="up", down=True),
        row("trial_end", 1900, trial_id="t00002"),
        row("sent", 3100, trial_id="t00002", step=2, key="down", down=True),   # belongs to the session whose trial_id is null (counted)
    ]
    r = assemble(_gt_bytes("mac_nstic", evs), _jl(*rows), _example_rules())
    sessA, sessB = r["definitions"]["all"]
    if sessA["trial_id"] != "t00001":
        f.append("session A's trial_id is not t00001: %r" % (sessA["trial_id"],))
    if sessA["nav_true"] != 1:
        f.append("session A's nav_true is not 1 (it picked up the adjacent trial's arrow at 940): %r" % (sessA["nav_true"],))
    if sessB["trial_id"] is not None:
        f.append("session B's trial_id is not null: %r" % (sessB["trial_id"],))
    if sessB["nav_true"] != 1:
        f.append("session B's (trial_id null) nav_true is not 1, the count of every sent event in its interval: %r" % (sessB["nav_true"],))
    return f


def _case_clock_jump():
    """A clock jump (the wall clock advances without a matching monotonic-clock advance) appears
    in excluded; below the threshold it does not. Checked for both the gt and the oracle logs."""
    f = []
    evs = [_sm(100, 1), _sm(300, 3), _ins(500, 2)]
    trials = [("t00001", 0, 900)]
    for skew, expect in ((5000, True), (1001, True), (1000, False), (900, False), (-5000, True)):
        r = assemble(_gt_bytes("mac_nstic", evs, {2: skew}), _oracle_bytes(trials), _example_rules())
        jumps = [x for x in r["excluded"] if x["type"] == "clock_jump"]
        if expect != bool(jumps):
            f.append("handling of a %d ms gt wall-clock skew (threshold 1000) does not match expectations: %r" % (skew, jumps))
        if expect and (len(jumps) != 1 or jumps[0]["log"] != "gt" or jumps[0]["skew_ms"] != skew):
            f.append("the contents of the gt clock_jump entry are wrong (skew %d): %r" % (skew, jumps))
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials, wall_shift={2: 5000}), _example_rules())
    jumps = [x for x in r["excluded"] if x["type"] == "clock_jump"]
    if len(jumps) != 1 or jumps[0]["log"] != "oracle":
        f.append("an oracle clock jump does not appear in excluded: %r" % (jumps,))
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials), _example_rules())
    if r["excluded"]:
        f.append("excluded is not empty although no jump is present")
    return f


def _case_excluded_by():
    """excluded entries' trial_ids, and each session's excluded_by / n_start_while_open."""
    f = []
    # --- Scenario 1: an oracle clock jump. It is located at a trial in the middle of the block and
    #     spans from there through the last trial of the block. The first trial (t00001) falls
    #     outside that range, and its interval does not span the jump either, so its excluded_by is
    #     expected to stay empty (negative control).
    trials = [("t00001", 0, 900), ("t00002", 1000, 1900), ("t00003", 2000, 2900)]
    evs = [_sm(100, 1), _ins(500, 2),                                  # session 1 (t00001)
           _sm(1100, 1), _sm(1150, 1), _ins(1500, 2),                  # session 2 (t00002; one start event while already open)
           _sm(2100, 1), _ins(2500, 2)]                                # session 3 (t00003)
    # The wall_shift index follows the order of _oracle_bytes items (block_begin, t1begin, t1end,
    # t2begin, t2end, t3begin, t3end, block_end).
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials, wall_shift={4: 5000}), _example_rules())
    jumps = [x for x in r["excluded"] if x["type"] == "clock_jump"]
    if len(r["excluded"]) != 1 or len(jumps) != 1 or jumps[0]["log"] != "oracle":
        f.append("scenario 1: the oracle clock jump is not exactly one entry: %r" % (r["excluded"],))
    elif set(jumps[0]["trial_ids"]) != {"t00002", "t00003"}:
        f.append("scenario 1: trial_ids is not {t00002, t00003}, the range from the middle of the block to its end: %r" % (jumps[0]["trial_ids"],))
    s1, s2, s3 = r["definitions"]["all"]
    if s1["excluded_by"] != []:
        f.append("scenario 1: session 1, which is out of range and whose interval does not span the jump, has a non-empty excluded_by: %r" % (s1["excluded_by"],))
    if s2["excluded_by"] != ["clock_jump"] or s3["excluded_by"] != ["clock_jump"]:
        f.append("scenario 1: sessions 2 and 3 are missing clock_jump from excluded_by: %r / %r" %
                 (s2["excluded_by"], s3["excluded_by"]))
    if (s1["n_start_while_open"], s2["n_start_while_open"], s3["n_start_while_open"]) != (0, 1, 0):
        f.append("scenario 1: n_start_while_open is not (0, 1, 0): %r" %
                 ((s1["n_start_while_open"], s2["n_start_while_open"], s3["n_start_while_open"]),))

    # --- Scenario 2: a gt clock jump. The session with trial_id t00001 falls outside the range
    #     that locating the jump at trial t00002 alone would give, but its interval spans the jump,
    #     so it is added to trial_ids as well.
    trials2 = [("t00001", 0, 900), ("t00002", 3000, 5000)]
    evs2 = [_sm(100, 1), (1005, "focus_out", {}), (1010, "focus_in", {}), _ins(1050, 2)]
    r2 = assemble(_gt_bytes("mac_nstic", evs2, wall_shift={2: 1001}), _oracle_bytes(trials2), _example_rules())
    jumps2 = [x for x in r2["excluded"] if x["type"] == "clock_jump"]
    if len(jumps2) != 1 or jumps2[0]["log"] != "gt":
        f.append("scenario 2: the gt clock jump is not exactly one entry: %r" % (r2["excluded"],))
    elif set(jumps2[0]["trial_ids"]) != {"t00001", "t00002"}:
        f.append("scenario 2: trial_ids is missing either the located trial t00002 or t00001, whose interval spans the jump: %r" %
                 (jumps2[0]["trial_ids"],))
    if r2["definitions"]["all"][0]["trial_id"] != "t00001":
        f.append("scenario 2: the session's trial_id is not t00001: %r" % (r2["definitions"]["all"][0]["trial_id"],))

    # --- Scenario 3: log_truncated's trial_ids (the trial_id of the session that never closed).
    trials3 = [("t00001", 0, 900)]
    r3 = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), (300, "focus_out", {})]), _oracle_bytes(trials3),
                 _example_rules())
    trunc = [x for x in r3["excluded"] if x["type"] == "log_truncated"]
    if len(trunc) != 1 or trunc[0]["trial_ids"] != ["t00001"]:
        f.append("scenario 3: log_truncated's trial_ids is not [t00001]: %r" % (trunc,))
    if r3["definitions"]["all"][0]["excluded_by"] != ["log_truncated"]:
        f.append("scenario 3: the excluded_by of the session that never closed is not [log_truncated]: %r" %
                 (r3["definitions"]["all"][0]["excluded_by"],))
    return f


def _case_carry_over():
    """A session that runs on from one trial into the next is excluded as carry_over, with both
    trials in trial_ids; a commit that lands after its own trial_end but before the next trial
    begins is not (negative control)."""
    f = []
    trials = [("t00001", 0, 900), ("t00002", 1000, 1900), ("t00003", 2000, 2900), ("t00004", 3000, 3900)]
    evs = [_sm(100, 1), _ins(950, 2),                                  # t00001: commits 50 ms after its trial_end
           _sm(1700, 1), _sm(1800, 3),                                 # t00002: left uncommitted ...
           _sm(2100, 5), _ins(2500, 5),                                # ... and committed inside t00003, which it overlaps most
           _sm(3100, 1), _ins(3500, 1)]                                # t00004: independent
    plan = _plan_bytes([(t, (True, False, "enter")) for t, _b, _e in trials])
    r = assemble(_gt_bytes("mac_nstic", evs),
                 _oracle_bytes(trials, plan_sha=hashlib.sha256(plan).hexdigest()), _example_rules(), plan)
    carry = [x for x in r["excluded"] if x["type"] == "carry_over"]
    if len(r["excluded"]) != 1 or len(carry) != 1:
        f.append("not exactly one carry_over entry: %r" % (r["excluded"],))
    elif carry[0]["trial_ids"] != ["t00002", "t00003"] or carry[0]["log"] != "gt":
        f.append("carry_over's trial_ids is not [t00002, t00003] from gt: %r" % (carry[0],))
    elif (carry[0]["start_wall_ms"] - WALL0, carry[0]["end_wall_ms"] - WALL0) != (1700, 2500):
        f.append("carry_over's times are not the session's own (1700, 2500): %r" % (carry[0],))
    by_tid = dict((s["trial_id"], s) for s in r["definitions"]["all"])
    if sorted(by_tid) != ["t00001", "t00003", "t00004"]:
        f.append("the carried session is not assigned to the later trial: %r" % (sorted(by_tid),))
    elif by_tid["t00003"]["excluded_by"] != ["carry_over"]:
        f.append("the carried session's excluded_by is not [carry_over]: %r" % (by_tid["t00003"]["excluded_by"],))
    elif by_tid["t00001"]["excluded_by"] != [] or by_tid["t00004"]["excluded_by"] != []:
        f.append("a session that stayed out of the next trial is flagged: %r / %r" %
                 (by_tid["t00001"]["excluded_by"], by_tid["t00004"]["excluded_by"]))
    agr = r["oracle_agreement"]
    if (agr["n_agree"], agr["mismatches"]) != (3, [{"trial_id": "t00002", "type": "no_session"}]):
        f.append("the agreement rate is changed by carry_over (the carried trial must still count as no_session): %r" % (agr,))
    # A session that never closes and runs into the next trial is carry_over as well as log_truncated.
    r2 = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), (1200, "focus_out", {})]),
                  _oracle_bytes([("t00001", 0, 900), ("t00002", 1000, 1900)]), _example_rules())
    if sorted(x["type"] for x in r2["excluded"]) != ["carry_over", "log_truncated"]:
        f.append("an open session that runs into the next trial is not both carry_over and log_truncated: %r" % (r2["excluded"],))
    elif [x["trial_ids"] for x in r2["excluded"] if x["type"] == "carry_over"] != [["t00001", "t00002"]]:
        f.append("the open session's carry_over trial_ids is not [t00001, t00002]: %r" % (r2["excluded"],))
    # A session over three trials lists the middle one too, although no event of it falls there.
    three = [("t00001", 0, 900), ("t00002", 1000, 1900), ("t00003", 2000, 2900)]
    r3 = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(2500, 1)]), _oracle_bytes(three), _example_rules())
    if [x["trial_ids"] for x in r3["excluded"]] != [["t00001", "t00002", "t00003"]]:
        f.append("a session over three trials does not list all three: %r" % (r3["excluded"],))
    # Both ends are inclusive: a session that ends at the very millisecond the next trial begins spans it.
    r4 = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(1000, 1)]),
                  _oracle_bytes([("t00001", 0, 900), ("t00002", 1000, 1900)]), _example_rules())
    if [x["trial_ids"] for x in r4["excluded"]] != [["t00001", "t00002"]]:
        f.append("a session that ends exactly at the next trial_begin is not carry_over: %r" % (r4["excluded"],))
    return f


def _experiment_rules():
    with open(os.path.join(_HERE, "..", "..", "experiment", "gt_rules.json"), "rb") as f:
        return f.read()


def _case_experiment_rules():
    """The experiment's rule file (experiment/gt_rules.json) keeps one session across an automatic
    commit in the middle of typing (insert_text followed by set_marked within 50 ms, as live
    conversion does on macOS), counts it in n_continued_commits, and still closes a session at a
    commit with no continuation; the example rule set splits the same input in two and counts no
    continued commit (control)."""
    f = []
    trials = [("t00001", 0, 1900), ("t00002", 2000, 3900)]
    evs = [_sm(100, 1), _sm(300, 4), _ins(500, 2), _sm(501, 2), _sm(700, 4), _ins(900, 4),   # one composition, auto-commit at 500
           _sm(2100, 1), _ins(2500, 1), _sm(2600, 1), _ins(2900, 1)]                       # two compositions 100 ms apart
    try:
        rules = _experiment_rules()
    except OSError as e:
        return ["experiment/gt_rules.json cannot be read (%s)" % type(e).__name__]
    r = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials), rules)
    got = [(s["trial_id"], s["outcome"], s["start_wall_ms"] - WALL0, s["end_wall_ms"] - WALL0) for s in r["definitions"]["all"]]
    want = [("t00001", "commit", 100, 900), ("t00002", "commit", 2100, 2500), ("t00002", "commit", 2600, 2900)]
    if got != want:
        f.append("the experiment rules do not keep the auto-commit inside one session: %r" % (got,))
    n_cont = [s["n_continued_commits"] for s in r["definitions"]["all"]]
    if n_cont != [1, 0, 0]:
        f.append("n_continued_commits does not count the automatic commit kept inside the session: %r" % (n_cont,))
    # The head committed automatically and the rest cancelled: one session, a cancellation (11 of the 27
    # cancelled trials of the pilot with live conversion on took this path).
    rc = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _sm(300, 4), _ins(500, 2), _sm(501, 2), _sm(700, 0)]),
                  _oracle_bytes([("t00001", 0, 1900)]), rules)
    got_c = [(s["outcome"], s["start_wall_ms"] - WALL0, s["end_wall_ms"] - WALL0, s["n_continued_commits"])
             for s in rc["definitions"]["all"]]
    if got_c != [("cancel", 100, 700, 1)]:
        f.append("an automatic commit followed by a cancellation is not one cancelled session with one continued commit: %r" % (got_c,))
    # unmark follows the same rule: with a continuation the session stays open, without one it closes.
    for evs_u, want_u in (([_sm(100, 1), (500, "unmark", {}), _sm(510, 1), _ins(900, 1)], [("commit", 100, 900, 1)]),
                          ([_sm(100, 1), (500, "unmark", {}), _sm(700, 1), _ins(900, 1)],
                           [("commit", 100, 500, 0), ("commit", 700, 900, 0)])):
        ru = assemble(_gt_bytes("mac_nstic", evs_u), _oracle_bytes([("t00001", 0, 1900)]), rules)
        got_u = [(s["outcome"], s["start_wall_ms"] - WALL0, s["end_wall_ms"] - WALL0, s["n_continued_commits"])
                 for s in ru["definitions"]["all"]]
        if got_u != want_u:
            f.append("unmark does not follow the continuation rule: %r, not %r" % (got_u, want_u))
    # The window includes its end: a continuation exactly 50 ms after the commit keeps the session open,
    # one at 51 ms does not.
    for gap, want_n in ((50, 1), (51, 2)):
        rb = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(500, 1), _sm(500 + gap, 1), _ins(900, 1)]),
                      _oracle_bytes([("t00001", 0, 1900)]), rules)
        if len(rb["definitions"]["all"]) != want_n:
            f.append("a continuation %d ms after the commit gives %d sessions, not %d" % (gap, len(rb["definitions"]["all"]), want_n))
    r0 = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials), _example_rules())
    if len(_wins(("t00001",), r0["definitions"]["all"])) != 2:
        f.append("control: the example rules no longer split at the auto-commit, so this case tests nothing")
    if any(s["n_continued_commits"] != 0 for s in r0["definitions"]["all"]):
        f.append("rules without not_followed_by count continued commits: %r"
                 % ([s["n_continued_commits"] for s in r0["definitions"]["all"]],))
    return f


def _case_rules_change_result():
    """Changing the rules changes the result, confirming that the rules are actually being read
    (negative control)."""
    f = []
    evs = [_sm(100, 1), _sm(300, 3), _ins(500, 2), _sm(1100, 2), _sm(1200, 0), _ins(1230, 2)]
    trials = [("t00001", 0, 900), ("t00002", 1000, 1900)]
    gt, orc = _gt_bytes("mac_nstic", evs), _oracle_bytes(trials)
    base_rules = _example_rules()
    base = assemble(gt, orc, base_rules)
    if assemble(gt, orc, base_rules) != base:
        f.append("the result changes with the same rules (non-deterministic)")
    b_out = [(s["outcome"], s["start_wall_ms"] - WALL0) for s in base["definitions"]["all"]]
    if b_out != [("commit", 100), ("commit", 1100)]:
        f.append("the baseline result does not match expectations: %r" % (b_out,))

    def only_unmark(o):
        o["mac_nstic"]["commit"] = [{"ev": "unmark"}]

    def start_len_gt_1(o):
        o["mac_nstic"]["start"] = {"ev": "set_marked", "len_gt": 1}

    def tight_within(o):
        o["mac_nstic"]["cancel"][0]["within_ms"] = 5

    def jump_loose(o):
        o["mac_nstic"]["clock_jump_ms"] = 10

    variants = (
        ("restricting commit to unmark alone", only_unmark, lambda r: [s["outcome"] for s in r["definitions"]["all"]] == ["open_at_end"]),
        ("setting start to len_gt 1", start_len_gt_1,
         lambda r: r["definitions"]["all"][0]["start_wall_ms"] - WALL0 == 300),
        ("setting within_ms to 5 (cancels before waiting for the commit 30 ms later)", tight_within,
         lambda r: [(s["outcome"]) for s in r["definitions"]["all"]] == ["commit", "cancel"]),
    )
    for name, mut, check in variants:
        rules = _rules_with(mut)
        r = assemble(gt, orc, rules)
        if r == base or r["rules_sha256"] == base["rules_sha256"]:
            f.append("changing the rule (%s) does not change the result" % name)
        elif not check(r):
            f.append("the result after changing the rule (%s) does not match expectations: %r" % (name, r["definitions"]["all"]))
    # candidate-window rule (win_imm: imn 5 to 6)
    evs = [(100, "start_comp", {}), (150, "notify", {"imn": 5}), (300, "comp", {"flags": 8, "comp_len": 0, "result_len": 2}),
           (320, "end_comp", {})]
    gt_w, orc_w = _gt_bytes("win_imm", evs), _oracle_bytes([("t00001", 0, 900)])
    a = assemble(gt_w, orc_w, base_rules)
    b = assemble(gt_w, orc_w, _rules_with(lambda o: o["win_imm"].__setitem__("candidate", {"ev": "notify", "imn": 6})))
    if not (a["definitions"]["all"][0]["candidate_shown"] is True and b["definitions"]["all"][0]["candidate_shown"] is False):
        f.append("changing the candidate rule does not change candidate_shown")
    # clock-jump threshold
    evs = [_sm(100, 1), _ins(500, 2)]
    gt_j = _gt_bytes("mac_nstic", evs, {1: 3000})
    # Only the clock_jump entries are compared: the shifted wall clock also stretches the session over
    # the next trial, which is reported as carry_over under either threshold.
    def jumps_of(rules):
        return [x for x in assemble(gt_j, orc, rules)["excluded"] if x["type"] == "clock_jump"]
    if not (jumps_of(base_rules)
            and not jumps_of(_rules_with(lambda o: o["mac_nstic"].__setitem__("clock_jump_ms", 10000)))):
        f.append("changing clock_jump_ms does not change excluded")
    return f


def _case_agreement():
    """The agreement rate between the oracle plan and the assembled sessions, and the per-type
    counts of disagreements."""
    f = []
    T = lambda k: "t%05d" % k
    evs = [_sm(100, 1), _ins(300, 2)]                                                   # stray (outside any trial)
    plan_intents = []
    trials = []
    k = 0

    def add(expect, abandon, commit, evlist=None, in_plan=True):
        nonlocal k
        k += 1
        base = k * 2000
        trials.append((T(k), base, base + 900))
        if in_plan:
            plan_intents.append((T(k), (expect, abandon, commit)))
        for (t, ev, kw) in (evlist or ()):
            evs.append((base + t, ev, kw))

    commit_seq = [_sm(100, 1), _ins(300, 2)]
    cancel_seq = [_sm(100, 2), _sm(300, 0), (600, "key_down", {"vk": 53})]
    add(True, False, "enter", commit_seq)                                        # 1 agrees (commit)
    add(False, False, "none")                                                    # 2 agrees (expected not to open, and does not)
    add(True, False, "enter")                                                    # 3 no_session
    add(False, False, "none", commit_seq)                                        # 4 unexpected_session
    add(True, False, "enter", cancel_seq)                                        # 5 expected_commit_got_cancel
    add(True, True, "none", commit_seq)                                          # 6 expected_cancel_got_commit
    add(True, False, "enter", commit_seq + [_sm(500, 1), _ins(700, 1)])          # 7 multiple_sessions
    add(True, True, "none", cancel_seq)                                          # 8 agrees (cancellation)
    add(True, False, "none", commit_seq)                                         # 9 agrees (no specific outcome required)
    add(True, False, "enter", None, in_plan=False)                               # 10 unknown_trial
    add(True, False, "enter", [_sm(100, 1), (300, "focus_out", {})])             # 11 open_at_end
    # open_at_end must be the last session, so trial 10 is placed before trial 11 (chronological order is set by k)
    evs.sort(key=lambda x: x[0])
    plan = _plan_bytes(plan_intents)
    sha = hashlib.sha256(plan).hexdigest()
    orc = _oracle_bytes(trials, plan_sha=sha)
    r = assemble(_gt_bytes("mac_nstic", evs), orc, _example_rules(), plan)
    ag = r["oracle_agreement"]
    want_types = dict((t, 0) for t in MISMATCH_TYPES)
    for t in ("no_session", "unexpected_session", "expected_commit_got_cancel", "expected_cancel_got_commit",
              "multiple_sessions", "unknown_trial", "open_at_end"):
        want_types[t] = 1
    want_ids = [(T(3), "no_session"), (T(4), "unexpected_session"), (T(5), "expected_commit_got_cancel"),
                (T(6), "expected_cancel_got_commit"), (T(7), "multiple_sessions"), (T(10), "unknown_trial"),
                (T(11), "open_at_end")]
    if (ag.get("plan_available"), ag.get("n_trials"), ag.get("n_agree")) != (True, 11, 4):
        f.append("the agreement count does not match expectations (4 of 11 trials): %r" % ({k2: ag.get(k2) for k2 in ("plan_available", "n_trials", "n_agree")},))
    if ag.get("by_type") != want_types:
        f.append("the per-type disagreement counts do not match expectations: %r" % (ag.get("by_type"),))
    if [(m["trial_id"], m["type"]) for m in ag.get("mismatches", [])] != want_ids:
        f.append("the disagreeing trials and their types do not match expectations: %r" % (ag.get("mismatches"),))
    if ag.get("rate") != 4 / 11:
        f.append("the agreement rate is not 4/11")
    if ag.get("unassigned_sessions") != 1:
        f.append("the one session outside any trial is not counted in unassigned_sessions: %r" % (ag.get("unassigned_sessions"),))
    if not any(s["trial_id"] is None for s in r["definitions"]["all"]):
        f.append("a session that overlaps no trial does not have a null trial_id")
    # without a plan
    r0 = assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials), _example_rules())
    if r0["oracle_agreement"] != {"plan_available": False, "n_trials": 11}:
        f.append("oracle_agreement without a plan does not match expectations: %r" % (r0["oracle_agreement"],))
    # a plan that does not correspond to the oracle log is rejected
    try:
        assemble(_gt_bytes("mac_nstic", evs), _oracle_bytes(trials, plan_sha="0" * 64), _example_rules(), plan)
        f.append("a plan whose SHA-256 differs from the oracle header is not rejected")
    except InputError:
        pass
    return f


def _case_rules_validation():
    """A malformed rule file is never silently accepted; it must raise InputError."""
    f = []
    good = json.loads(_example_rules().decode("utf-8"))

    def mut(fn):
        o = json.loads(json.dumps(good))
        fn(o)
        return json.dumps(o).encode("utf-8")

    bad = (
        ("an unknown src", lambda o: o.__setitem__("mac_nstc", o["mac_nstic"])),
        ("an unknown key in a source block", lambda o: o["mac_nstic"].__setitem__("comit", [])),
        ("a missing clock_jump_ms", lambda o: o["mac_nstic"].pop("clock_jump_ms")),
        ("a missing candidate (must be written as null)", lambda o: o["win_imm"].pop("candidate")),
        ("a missing candidate_end (must be written as null)", lambda o: o["win_imm"].pop("candidate_end")),
        ("an empty commit", lambda o: o["mac_nstic"].__setitem__("commit", [])),
        ("not_followed_by without within_ms", lambda o: o["mac_nstic"]["cancel"][0].pop("within_ms")),
        ("within_ms without not_followed_by", lambda o: o["mac_nstic"]["commit"][0].__setitem__("within_ms", 5)),
        ("an ev that does not belong to that src", lambda o: o["win_imm"].__setitem__("start", {"ev": "set_marked"})),
        ("a field that does not belong to that ev", lambda o: o["mac_nstic"]["start"].__setitem__("comp_len_gt", 0)),
        ("a non-integer value", lambda o: o["mac_nstic"]["start"].__setitem__("len_gt", "0")),
        ("a value outside the enumeration", lambda o: o["win_tsf"]["candidate"].__setitem__("ui_kind", "bogus")),
        ("after_result on a start condition", lambda o: o["win_imm"]["start"].__setitem__("after_result", True)),
        ("a non-boolean after_result", lambda o: o["win_imm"]["commit"][0].__setitem__("after_result", 1)),
        ("no source rule blocks at all", lambda o: [o.pop(k) for k in ("mac_nstic", "win_imm", "win_tsf")]),
    )
    for name, fn in bad:
        try:
            compile_rules(mut(fn))
            f.append("a malformed rule set (%s) is not rejected" % name)
        except InputError:
            pass
        except Exception as e:  # an unrelated exception does not count as a rejection
            f.append("a malformed rule set (%s) raised %s instead of InputError" % (name, type(e).__name__))
    try:
        compile_rules(json.dumps(good).encode("utf-8"))
    except InputError as e:
        f.append("a valid rule set was rejected: %s" % e)
    # rejection of malformed input logs
    evs = [_sm(100, 1), _ins(500, 2)]
    ok_gt, ok_or = _gt_bytes("mac_nstic", evs), _oracle_bytes([("t00001", 0, 900)])
    back = _gt_bytes("mac_nstic", [_sm(500, 1), _ins(100, 2)])
    trunc = ok_or.split(b"\n")
    trunc = b"\n".join(x for x in trunc if b'"trial_end"' not in x)
    for name, gt, orc, rules in (
            ("a gt log whose monotonic clock goes backward", back, ok_or, _example_rules()),
            ("an oracle log missing trial_end", ok_gt, trunc, _example_rules()),
            ("a gt log with the wrong header", ok_or, ok_or, _example_rules()),
            ("no rule block for that src", _gt_bytes("win_imm", [(100, "start_comp", {})]), ok_or,
             mut(lambda o: o.pop("win_imm")))):
        try:
            assemble(gt, orc, rules)
            f.append("malformed input (%s) is not rejected" % name)
        except InputError:
            pass
    return f


def _case_input_hardening():
    """Input hardening (a low-severity security concern): JSON with duplicate keys, excessively
    deep nesting, and the shape of the oracle / plan trial_id."""
    f = []
    try:
        _loads('{"a":1,"a":2}', "dup-test")
        f.append("JSON with the same key twice on one line is not rejected")
    except InputError:
        pass
    deep = ("[" * 1000000) + ("]" * 1000000)
    try:
        _loads(deep, "deep-test")
        f.append("excessively nested JSON (RecursionError) is not rejected")
    except InputError:
        pass
    try:
        assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(500, 2)]), _oracle_bytes([("bad id!", 0, 900)]),
                 _example_rules())
        f.append("an oracle trial_id containing symbols is not rejected")
    except InputError:
        pass
    try:
        assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _ins(500, 2)]), _oracle_bytes([("t" * 65, 0, 900)]),
                 _example_rules())
        f.append("a 65-character oracle trial_id is not rejected")
    except InputError:
        pass
    try:
        parse_plan(_plan_bytes([("bad id!", (True, False, "enter"))]))
        f.append("a plan trial_id containing symbols is not rejected")
    except InputError:
        pass
    try:
        parse_plan(_plan_bytes([("t00001", (True, False, "enter"))]))  # positive control: an ordinary trial_id is accepted
    except InputError as e:
        f.append("an ordinary trial_id was rejected: %s" % e)
    return f


def _case_example_rules_minimal():
    """The example rule set processes a minimal input (a single macOS start-then-commit) without
    error."""
    f = []
    r = assemble(_gt_bytes("mac_nstic", [_sm(100, 1), _sm(200, 3), _ins(500, 2)]),
                 _oracle_bytes([("t00001", 0, 600)]), _example_rules())
    if len(r["definitions"]["all"]) != 1 or r["definitions"]["windows_def"] is not None:
        f.append("the example rule set does not yield one session for the minimal input")
    if r["rules_sha256"] != hashlib.sha256(_example_rules()).hexdigest():
        f.append("rules_sha256 is not the SHA-256 of the rule file's bytes")
    return f


def _case_cli_bytes_identical():
    """CLI: identical input yields byte-identical output with LF line endings and no BOM; malformed
    input exits with status 2 rather than crashing."""
    f = []
    tmp = tempfile.mkdtemp(prefix="gt_sessions_selftest_")
    me = os.path.abspath(__file__)
    env = dict(os.environ, PYTHONUTF8="1")
    paths = {}
    try:
        evs = [_sm(100, 1), _sm(300, 3), _ins(500, 2), _sm(2100, 2), _sm(2300, 0), (2600, "key_down", {"vk": 53})]
        plan = _plan_bytes([("t00001", (True, False, "enter")), ("t00002", (True, True, "none"))])
        files = {"gt.jsonl": _gt_bytes("mac_nstic", evs),
                 "oracle.jsonl": _oracle_bytes([("t00001", 0, 900), ("t00002", 2000, 2900)],
                                               plan_sha=hashlib.sha256(plan).hexdigest()),
                 "plan.jsonl": plan, "rules.json": _example_rules()}
        for name, data in files.items():
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "wb") as fh:
                fh.write(data)

        def run(*args):
            p = subprocess.run([sys.executable, me] + list(args), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, env=env)
            return p.returncode, p.stdout.decode("utf-8", "replace")

        base = ["--gt", paths["gt.jsonl"], "--oracle", paths["oracle.jsonl"], "--rules", paths["rules.json"]]
        outs = []
        for i in range(2):
            o = os.path.join(tmp, "out%d" % i, "s.json")
            rc, text = run(*(base + ["--plan", paths["plan.jsonl"], "--out", o]))
            if rc != 0:
                f.append("the CLI did not exit 0 (%d): %s" % (rc, text.strip()[-200:]))
                return f
            with open(o, "rb") as fh:
                outs.append(fh.read())
        if outs[0] != outs[1]:
            f.append("identical input did not yield byte-identical output")
        if b"\r" in outs[0] or outs[0].startswith(b"\xef\xbb\xbf") or not outs[0].endswith(b"\n"):
            f.append("the output is not LF-only, BOM-free, and newline-terminated")
        d = json.loads(outs[0].decode("utf-8"))
        if d["oracle_agreement"].get("n_agree") != 2 or len(d["definitions"]["all"]) != 2:
            f.append("the CLI output's contents do not match expectations")
        if outs[0] != render(assemble(files["gt.jsonl"], files["oracle.jsonl"], files["rules.json"], plan)):
            f.append("the CLI output differs from the result of assemble")

        # without --plan, a warning notes that no agreement rate is available; the exit code stays 0.
        o_noplan = os.path.join(tmp, "noplan.json")
        rc, text = run(*(base + ["--out", o_noplan]))
        if rc != 0:
            f.append("the CLI without --plan did not exit 0 (%d)" % rc)
        elif "agreement rate is not available" not in text:
            f.append("no warning is printed when --plan is missing: %s" % text.strip()[-200:])
        rc, text = run(*(base + ["--plan", paths["plan.jsonl"], "--out", os.path.join(tmp, "withplan.json")]))
        if rc == 0 and "agreement rate is not available" in text:
            f.append("the no-agreement-rate warning is printed even though --plan was given")

        # exit 2 when --out names an existing input file (a low-severity security check); the input
        # file must remain intact.
        rc, text = run(*(base + ["--plan", paths["plan.jsonl"], "--out", paths["gt.jsonl"]]))
        if rc != 2 or "Traceback" in text:
            f.append("exit status is not 2 when --out names the same file as --gt (%d): %s" % (rc, text.strip()[-160:]))
        with open(paths["gt.jsonl"], "rb") as fh:
            if fh.read() != files["gt.jsonl"]:
                f.append("the input file was overwritten even though --out should have been detected as identical to it")

        o = os.path.join(tmp, "x.json")
        for name, args in (
                ("a missing gt file", ["--gt", os.path.join(tmp, "none.jsonl")] + base[2:] + ["--out", o]),
                ("a plan that does not correspond", base + ["--plan", paths["rules.json"], "--out", o])):
            rc, text = run(*args)
            if rc != 2 or "Traceback" in text:
                f.append("the CLI (%s) did not exit 2 (%d): %s" % (name, rc, text.strip()[-160:]))
        rc, text = run("--gt", paths["gt.jsonl"])
        if rc != 2:
            f.append("missing arguments did not exit 2 (%d)" % rc)
        if os.path.exists(o):
            f.append("an output file was created despite the failure")
    finally:
        for root, dirs, names in os.walk(tmp, topdown=False):
            for n in names:
                os.remove(os.path.join(root, n))
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        os.rmdir(tmp)
    return f


CASES = (
    ("five session types (start then commit, cancellation, with/without a candidate window, two "
     "sessions in one trial, and a session left open) across all three sources", _case_five_types),
    ("nav_true counts only arrows within the interval; arrows outside the interval are not counted", _case_nav_interval),
    ("nav_true_cand counts only arrows within the intervals when the candidate window was open", _case_nav_true_cand),
    ("nav_true / nav_true_cand count only the sent events belonging to the assigned trial (trial scoping)", _case_nav_trial_scope),
    ("a clock jump (the wall clock advances alone) appears in excluded", _case_clock_jump),
    ("excluded entries' trial_ids and each session's excluded_by / n_start_while_open", _case_excluded_by),
    ("a session that runs on into the next trial is excluded as carry_over with both trials", _case_carry_over),
    ("the experiment rule file keeps an automatic commit in the middle of typing inside one session", _case_experiment_rules),
    ("changing the rules changes the result (negative control)", _case_rules_change_result),
    ("the oracle-to-ground-truth agreement rate and the disagreement types", _case_agreement),
    ("malformed rules and inputs are rejected", _case_rules_validation),
    ("input hardening (duplicate keys, deep nesting, trial_id shape)", _case_input_hardening),
    ("the example rule set processes a minimal input", _case_example_rules_minimal),
    ("CLI: byte-identical output and exit codes", _case_cli_bytes_identical),
)


def selftest():
    bad = 0
    for name, fn in CASES:
        try:
            fails = fn()
        except Exception as e:  # an unexpected exception counts as a failure
            fails = ["exception %s: %s" % (type(e).__name__, e)]
        if fails:
            bad += 1
            print("  ❌ " + name)
            for m in fails:
                print("     FAIL " + m)
        else:
            print("  ✅ " + name)
    if bad:
        print("selftest failed %d / %d" % (bad, len(CASES)))
        return 1
    print("selftest OK (%d cases)" % len(CASES))
    return 0


# ---------------------------------------------------------------- CLI

def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Ground-truth session assembly (gt/1 + oracle/1 to composition sessions)")
    ap.add_argument("--gt")
    ap.add_argument("--oracle")
    ap.add_argument("--rules")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.gt and args.oracle and args.rules and args.out):
        ap.error("--gt / --oracle / --rules / --out are required (--plan is optional)")
    for inp in (args.gt, args.oracle, args.rules, args.plan):
        if inp and _same_file(inp, args.out):
            print("gt_sessions: --out names the same file as an input (%s)" % inp, file=sys.stderr)
            return 2
    try:
        result = build_sessions(args.gt, args.oracle, args.rules, args.plan)
        data = render(result)
        d = os.path.dirname(os.path.abspath(args.out)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".gt_sessions_tmp_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, args.out)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except InputError as e:
        print("gt_sessions: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("gt_sessions: could not write output (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    if not args.plan:
        print("gt_sessions: no --plan given; the agreement rate is not available", file=sys.stderr)
    allx = result["definitions"]["all"]
    print("gt_sessions: src=%s sessions=%d (commit=%d cancel=%d open_at_end=%d) excluded=%d" % (
        result["src"], len(allx), sum(1 for s in allx if s["outcome"] == "commit"),
        sum(1 for s in allx if s["outcome"] == "cancel"),
        sum(1 for s in allx if s["outcome"] == "open_at_end"), len(result["excluded"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
