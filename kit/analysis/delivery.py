#!/usr/bin/env python3
# kit: selftest
"""Delivery report: how faithfully scripted key presses were delivered, press by press, in a method-validation
run (Python standard library only). Descriptive only: it counts and summarises, and it makes no pass/fail
decision.

  delivery.py --plan PLAN --oracle ORACLE --obs OBS --gt GT --out OUT.json [--pre-ms N] [--tail-ms N]
  delivery.py --selftest      positive and negative controls (fast)

Inputs: the plan (plan/1), the oracle (oracle/1; one row per press as sent by the runner), the observation log
(obs/1) and the reference log (gt/1). From the observation log only the rows whose type is "key" are read (fields
t and press); every other row is skipped without its other fields being inspected, so this tool does not depend on
the session rows. From the reference log only the rows whose ev is "key_down", "focus_out" or "focus_in" are read
(field t_wall_ms, and, for the focus rows, the position in the log); the key code a key_down carries is not
interpreted. A row that does not parse as JSON, or a row that is not an object, is refused.

Definitions.
  * A press is an oracle "sent" row with down = true whose key is not a pseudo-key (PSEUDO_KEYS: focus_away,
    focus_back, mouse_click). Pseudo-keys are never real keys and are ignored everywhere. MODE_KEYS (ime_on,
    ime_off) are input-mode keys, which the observation side does not record by design; every reach figure is
    therefore given twice, over all presses and over the presses excluding the mode keys ("excluding_mode_keys").
  * Send lateness of a press = (sent.t_mono_ns - (trial_begin.t_mono_ns + steps[step].at_ms * 1e6)) / 1e6, in
    milliseconds. Positive means the press was sent later than scheduled. steps[step] is the plan step indexed by
    the sent row's step field.
  * Reach, for each side (observation key rows with press = true; reference key_down rows), per trial: the number
    of presses sent against the number of rows received. A trial's interval runs from the trial_begin wall time
    minus pre_ms (default 20) to the trial_end wall time plus tail_ms (default 300). The tail end is also cut at the
    next oracle row (reset, block_begin, marker or the next trial_begin), whichever comes first, so that a marker
    press at the head of the next block is not counted into the last trial of the previous block. A trial without
    both a trial_begin and a trial_end has no interval and is counted as incomplete. Reported: sent_presses,
    reached = sum over trials of min(sent, received), reach_rate = reached / sent_presses, excess_rows = sum of
    max(0, received - sent), rows_outside_trials (rows that fall inside no interval), trials_with_loss
    (received < sent) and trials_with_excess (received > sent).
  * Segments, per side and in two versions. The block_begin rows of the oracle divide the time of the log into
    half-open segments: one from the start up to the first block_begin (block_id null), then, for each block,
    one from its own block_begin time (included) to the next block_begin time (excluded), the last one to the
    end; a log with no block_begin is one segment with block_id null. The count of sent presses that explains
    rows has two versions: "all_presses" (every press, the mode keys included) and "excluding_mode_keys" (the
    mode keys left out); "segments" of a side holds both, each with the same members. For each segment, in time
    order and also when it holds no row: n_rows = the rows that fall in it, inside a trial's interval or outside
    every one; n_reached = the sum, over the measured trials whose trial_begin wall time lies in the segment,
    of min(sent, the number of rows assigned to the trial (as for reach) that lie in the segment), with sent
    counted as the version says; n_unexplained = n_rows - n_reached (never negative); n_marker_presses = the
    length of the marker array the plan gives the block (0 for the first segment and for a block with no
    marker); n_unexplained_beyond_markers = max(0, n_unexplained - n_marker_presses);
    ms_from_last_row_to_next_block = the wall time of the next segment's start (the next block_begin) minus the
    time of the last row of the segment (null for the last segment and for a segment with no row). A key that
    the runner presses before a block and that is recorded just before the block_begin of the next block falls
    into the segment of the previous block; this figure tells how far before the next block it was recorded.
    Each version heads the list with n_segments, max_unexplained_beyond_markers (the largest
    n_unexplained_beyond_markers over the segments), max_beyond_markers_before_last_block (the largest over
    every segment except the segment of the last block, the segment before the first block included; when there
    is no block_begin, the value of the one segment) and beyond_markers_last_block (the value of the segment of
    the last block; null when there is no block_begin).
    A press that the runner makes outside the oracle, such as the key that selects the input method before a
    block, has no oracle row; wherever it falls, inside the interval of the last trial of the segment or outside
    every interval, it adds one to n_unexplained of the same segment, because min(sent, received) of a trial is
    capped at sent; where it falls changes the count only when a press of that trial was lost (the blind spot
    below). Blind spot: when, in one trial, a sent press does not arrive and a press from outside arrives in
    its place, the two cancel in n_reached and n_unexplained does not change; a replaced press is not
    detected. Likewise, when a press of the procedure (a marker press, a key before a block) or of a trial does
    not reach the log, that many presses from outside do not appear in the count: for a marker press or a key
    before a block, anywhere in the same segment; for a press of a trial, within the interval of the same
    trial. The two versions differ for the mode keys: a mode key that the reference does not receive leaves, in
    "all_presses", room for one press from outside in the interval of its trial, and "excluding_mode_keys"
    leaves none. This tool only counts: the bounds on n_unexplained and the choice of the version belong to
    the pre-registration (section 3.4) and to pipeline.py.
  * Delay and interval error, per side, only in trials in which the number of received rows equals the number of
    presses excluding the mode keys (and is at least one); such a trial is "paired" and its i-th press is matched
    with its i-th row in time order (presses in the order of the oracle file). delay_ms = row wall time - sent
    t_wall_ms (positive: the row is later than the send). interval_error_ms = (row[i+1] - row[i]) -
    (sent[i+1] - sent[i]) over consecutive pairs of one trial. n_trials_paired counts the paired trials and
    n_trials_not_paired the trials with a different count; a trial with no press and no row belongs to neither.
  * Summary = {n, mean, min, p05, median, p95, max}, all null when n = 0. Percentiles use linear interpolation
    between order statistics at position p * (n - 1) (the "type 7" rule); the median is the p = 0.5 case.
    Values are rounded to six decimals.
  * Focus losses of the reference input field: a loss interval runs from a focus_out row to the next focus_in row
    (to the end of the log when none follows); a focus_out inside a loss interval already open starts no new one.
    The focus rows are taken in the order of the log (by time, and rows of equal time by line number): at an equal
    time, a focus_in then a focus_out closes one loss and opens another, and a focus_out then a focus_in gives a
    loss of length zero.
    A loss that is not regained therefore covers every later trial, although the reference field no longer
    receives their keys. A loss interval overlaps a trial when it shares at least one millisecond with the
    trial's interval (as defined for reach). Reported: the numbers of focus_out and of focus_in rows, the number
    of loss intervals, the number of loss intervals that overlap no trial interval, the number of planned loss
    intervals (n_loss_intervals_planned: those that begin and end inside the interval of a trial of scenario S15)
    and of unplanned ones (n_loss_intervals_unplanned: all the others, including a loss between trials, before
    the first block, in a trial of another scenario, or one that is not regained; the two add up to
    n_loss_intervals), the number of unplanned loss intervals in which the observation log holds a key press
    (n_loss_intervals_unplanned_with_presses) and the number of such presses (n_presses_in_unplanned_losses; both
    include the presses that the executor sent); a press counts for a loss when its time lies from
    FOCUS_MARGIN_MS before the focus_out to FOCUS_MARGIN_MS after the focus_in (to the end of the log when the
    loss is not regained), and a press that lies in two such spans is counted once. The executor keeps sending
    keys during a loss, and the observation records every key of the machine, so the rows are also set against
    the keys sent. The span of every loss, planned or not, is widened by FOCUS_MARGIN_MS on both sides (to the
    end of the log when the loss is not regained), and spans that overlap or touch are merged. Two losses are
    added before the widening and the merging; they are not losses in the sense above: they are not counted in
    n_loss_intervals or in any other interval count, are not planned, are not in the trial list, and enter only
    these spans. The observer is started before the reference input field and stopped after it, so (a) the time
    from the start of the logs up to the first focus_in is a loss when the first focus row of the reference log
    (in the order of the log) is a focus_in, and the whole log is one when the log has no focus row at all (a log
    whose first focus row is a focus_out already has a loss from there, and adds nothing); (b) the time from the
    last row of the reference log that this tool reads (the last key_down, focus_out or focus_in; when there is
    none, (a) already covers the whole log) to the end of the logs is a loss. The start of (a) is open
    (minus infinity; None in the code) and is not widened. The executor sends no key before the first block
    that the observation records, so these spans hold no row in a run without a person. n_loss_spans is the
    number of merged spans, these included. A planned switch of S15 is a span too: the switch sends no key, so
    only the rows beyond the keys sent count there. A key the observation is expected to record is a sent row
    with down = true whose key is neither a pseudo-key nor a mode key (of any trial, measured or not), or a
    marker row of the oracle (one for each marker press). The rows (observation key rows with press = true) are
    matched to these keys once for the whole log, one to one, greedily and in time order: a row at time r may
    take a key sent at s when r - OBS_MATCH_AFTER_MS <= s <= r + OBS_MATCH_BEFORE_MS; each row, in ascending
    order, takes the earliest key not yet taken that fits, and a key sent earlier than that window can no longer
    be taken. A row that takes none is unmatched. Unmatched rows are counted in a window [a, e] of each merged
    span [a, b]. When b is open, e is open. Otherwise e starts as b + OBS_MATCH_BEFORE_MS + OBS_MATCH_AFTER_MS
    and is extended: take, in ascending order, the keys sent after b + OBS_MATCH_BEFORE_MS; while the next one is
    at most OBS_MATCH_BEFORE_MS + OBS_MATCH_AFTER_MS after the previous one (the first one counted from
    b + OBS_MATCH_BEFORE_MS), e becomes the larger of e and its time + OBS_MATCH_AFTER_MS; the extension stops at
    the first larger gap. The reason is that the matching is global: when a person's press falls between a key
    sent and that key's own row, the press takes the key and the unmatched row moves to the executor's later row,
    and this shortage can be carried from key to key past the end of the span only while consecutive keys are at
    most that far apart, the displaced row arriving at most OBS_MATCH_AFTER_MS after its key. For each merged
    span, n_unexplained is the number of unmatched rows with a time in its window. Reported are the number of
    spans with n_unexplained above 0 (n_loss_spans_with_unexplained_presses) and the number of distinct
    unmatched rows that lie in at least one window (n_presses_unexplained_in_losses; a row in two overlapping
    windows is counted once). Blind spots of this count: (i) when the observation misses the row of a key sent,
    that key can be matched to one press of a person made from OBS_MATCH_BEFORE_MS before it to
    OBS_MATCH_AFTER_MS after it, and that press then goes uncounted; (ii) a row stamped more than
    OBS_MATCH_BEFORE_MS before its key, or arriving more than OBS_MATCH_AFTER_MS after it, stays unmatched and,
    inside a counting window, is counted although no person is present; (iii) a row that the observation
    duplicates inside a counting window is counted although no person is present.
    The trials that
    overlap at least one loss interval (trials_with_focus_loss, sorted) and per stratum (scenario, speed) the
    number of measured trials and of trials with a focus loss. A planned
    switch to another application (scenario S15) is not counted in the list of trials: an S15 trial is left out
    of the list when every loss interval it overlaps begins and ends inside
    its own interval (the focus came back within the trial); any other loss, including an S15 switch that does
    not return inside the trial, is listed together with every trial it reaches.

Refused (exit 2): a block row of the plan whose block_id is repeated or is not an integer (a boolean is not one)
or whose marker is not an array of strings; a block_begin whose block_id is not an integer, is not in the plan or
appears twice for one block; a plan whose SHA-256 differs from the oracle header's plan_sha256; a sent row whose
trial or step is absent from the plan, or whose key or down flag disagrees with the plan step; a marker
row of the oracle whose block_id is missing, not an integer (a boolean is not one) or not a block of the plan,
whose seq is missing or not an integer or lies outside 0 .. (the length of the block's marker array) - 1 (so a
block with an empty or missing marker array can have no marker row), or whose (block_id, seq) appears twice; a
sent row of a trial with no trial_begin; a duplicated trial_begin or trial_end; and malformed input, a line
nested too deeply to be read included. Any other unexpected error also exits 2, and only its type name is
printed. Error messages carry only
row numbers and field names, never a value read from a log.

The output holds no path and identical input yields identical output bytes (sorted keys, LF, UTF-8, no BOM). The
SHA-256 of each input is recorded under "inputs_sha256".
Exit status: 0 written / 1 selftest failure / 2 usage error or invalid input.
"""
import argparse
import bisect
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

FORMAT = "delivery/1"
PSEUDO_KEYS = ("focus_away", "focus_back", "mouse_click")
MODE_KEYS = ("ime_on", "ime_off")
SPEEDS = ("slow", "mid", "fast")
CUT_KINDS = ("reset", "block_begin", "marker")
# The margin around a loss of focus of the reference within which a key press of the observation log is counted for
# the loss (pre-registration section 3.4): it covers the offset between the two logs' clocks and the delay with which
# a key event reaches the observer, and a key that moves the focus is pressed just before the loss.
FOCUS_MARGIN_MS = 500
# How far before the sent time of a press its row of the observation log may be stamped and still be matched to it
# (clock tolerance; the same value as the default pre_ms).
OBS_MATCH_BEFORE_MS = 20
# How far after the sent time of a press its row may arrive and still be matched to it. It is well above the largest
# difference measured in the pilot (86 to 95 ms in the first block of a run) and equal to the default tail_ms.
OBS_MATCH_AFTER_MS = 300


class InputError(Exception):
    """Input is unreadable, or does not match the contract."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _no_dup_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("a row has the same key twice")
        out[k] = v
    return out


def _lines(data, what):
    """Return [(line number, text)] of the non-empty lines of a UTF-8 byte string."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("%s: not valid UTF-8" % what)
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if line:
            out.append((n, line))
    return out


def _loads(line, where, strict):
    try:
        if strict:
            return json.loads(line, object_pairs_hook=_no_dup_pairs)
        return json.loads(line)
    except ValueError:
        raise InputError("%s: the line does not parse as JSON" % where)


# --- readers ---------------------------------------------------------------------------------------------------

def read_plan(data):
    """Return (trials, markers) from a plan/1 byte string. trials is {trial_id: {"scenario", "speed", "steps":
    [(key, at_ms, down)]}}; markers is {block_id: the number of marker presses the plan puts at the head of the
    block} (0 when the block row has no marker)."""
    lines = _lines(data, "plan")
    if not lines:
        raise InputError("plan: empty file")
    hdr = _loads(lines[0][1], "plan:%d" % lines[0][0], True)
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "plan/1":
        raise InputError("plan: line 1 is not a plan/1 header")
    trials = {}
    markers = {}
    for n, raw in lines[1:]:
        where = "plan:%d" % n
        row = _loads(raw, where, True)
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
            raise InputError("%s: missing kind" % where)
        if row["kind"] == "block":
            bid = row.get("block_id")
            if not _is_int(bid) or bid in markers:
                raise InputError("%s: block_id is missing, not an integer, or repeated" % where)
            marker = row.get("marker", [])
            if not isinstance(marker, list) or not all(isinstance(k, str) for k in marker):
                raise InputError("%s: marker is not an array of strings" % where)
            markers[bid] = len(marker)
            continue
        if row["kind"] != "trial":
            continue
        tid = row.get("trial_id")
        if not isinstance(tid, str) or tid in trials:
            raise InputError("%s: trial_id is missing, not a string, or repeated" % where)
        if row.get("speed") not in SPEEDS:
            raise InputError("%s: speed is not one of the allowed names" % where)
        if not isinstance(row.get("scenario"), str):
            raise InputError("%s: scenario is missing or not a string" % where)
        steps = row.get("steps")
        if not isinstance(steps, list):
            raise InputError("%s: steps is not an array" % where)
        parsed = []
        for st in steps:
            if (not isinstance(st, dict) or not isinstance(st.get("key"), str) or not _is_int(st.get("at_ms"))
                    or not isinstance(st.get("down"), bool)):
                raise InputError("%s: a step is malformed (key, at_ms, down)" % where)
            parsed.append((st["key"], st["at_ms"], st["down"]))
        trials[tid] = {"scenario": row["scenario"], "speed": row["speed"], "steps": parsed}
    return trials, markers


def read_oracle(data, plan, plan_sha256, blocks):
    """Return (trials, cuts, begins, marker_times). trials is a list, in file order, of {trial_id, begin_wall,
    begin_mono, end_wall, sent: [{key, step, wall, mono, down}]}; cuts is the sorted list of wall times of the oracle rows that
    end a trial's interval (reset / block_begin / marker / trial_begin); begins is the list of (wall time,
    block_id) of the block_begin rows, sorted by time; marker_times is the sorted list of wall times of the marker
    marker rows (one row for each marker press). blocks is {block_id: the number of marker presses of the block}
    as read_plan returns it. The sent rows are checked against the plan, a block_begin against the blocks, and a
    marker row against the marker list of its block (block_id in the plan, integer seq in 0 .. length - 1, no
    (block_id, seq) twice)."""
    lines = _lines(data, "oracle")
    if not lines:
        raise InputError("oracle: empty file")
    hdr = _loads(lines[0][1], "oracle:%d" % lines[0][0], True)
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "oracle/1":
        raise InputError("oracle: line 1 is not an oracle/1 header")
    if hdr.get("plan_sha256") != plan_sha256:
        raise InputError("oracle: plan_sha256 in the header does not match the plan given")
    trials = {}
    order = []
    cuts = []
    begins = []
    marker_times = []
    marker_seen = set()
    for n, raw in lines[1:]:
        where = "oracle:%d" % n
        row = _loads(raw, where, True)
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
            raise InputError("%s: missing kind" % where)
        kind = row["kind"]
        if kind in CUT_KINDS:
            if not _is_int(row.get("t_wall_ms")):
                raise InputError("%s: t_wall_ms is missing or not an integer" % where)
            cuts.append(row["t_wall_ms"])
            if kind == "marker":
                bid, seq = row.get("block_id"), row.get("seq")
                if not _is_int(bid) or bid not in blocks:
                    raise InputError("%s: block_id of a marker is missing, not an integer or not in the plan" % where)
                if not _is_int(seq):
                    raise InputError("%s: seq of a marker is missing or not an integer" % where)
                if (bid, seq) in marker_seen:
                    raise InputError("%s: a marker (block_id, seq) appears twice" % where)
                if seq < 0 or seq >= blocks[bid]:
                    raise InputError("%s: seq of a marker is outside the marker list of its block in the plan" % where)
                marker_seen.add((bid, seq))
                marker_times.append(row["t_wall_ms"])
            if kind == "block_begin":
                bid = row.get("block_id")
                if not _is_int(bid) or bid not in blocks:
                    raise InputError("%s: block_id of a block_begin is missing, not an integer or not in the plan" % where)
                if any(b == bid for _t, b in begins):
                    raise InputError("%s: block_begin appears twice for one block" % where)
                begins.append((row["t_wall_ms"], bid))
            continue
        if kind not in ("trial_begin", "trial_end", "sent"):
            continue
        tid = row.get("trial_id")
        if not isinstance(tid, str) or tid not in plan:
            raise InputError("%s: trial_id is missing or not in the plan" % where)
        if not _is_int(row.get("t_wall_ms")) or not _is_int(row.get("t_mono_ns")):
            raise InputError("%s: t_wall_ms or t_mono_ns is missing or not an integer" % where)
        if kind == "trial_begin":
            if tid in trials:
                raise InputError("%s: trial_begin appears twice for one trial" % where)
            trials[tid] = {"trial_id": tid, "begin_wall": row["t_wall_ms"], "begin_mono": row["t_mono_ns"],
                           "end_wall": None, "sent": []}
            order.append(tid)
            cuts.append(row["t_wall_ms"])
            continue
        if tid not in trials:
            raise InputError("%s: a %s row comes before the trial_begin of its trial" % (where, kind))
        t = trials[tid]
        if kind == "trial_end":
            if t["end_wall"] is not None:
                raise InputError("%s: trial_end appears twice for one trial" % where)
            t["end_wall"] = row["t_wall_ms"]
            continue
        step, key, down = row.get("step"), row.get("key"), row.get("down")
        steps = plan[tid]["steps"]
        if not _is_int(step) or step < 0 or step >= len(steps):
            raise InputError("%s: step has no counterpart in the plan" % where)
        if key != steps[step][0] or down != steps[step][2]:
            raise InputError("%s: key or down disagrees with the plan step" % where)
        t["sent"].append({"key": key, "step": step, "down": down, "wall": row["t_wall_ms"],
                          "mono": row["t_mono_ns"]})
    cuts.sort()
    marker_times.sort()
    begins.sort(key=lambda b: b[0])
    return [trials[k] for k in order], cuts, begins, marker_times


def read_obs_presses(data):
    """Return the sorted wall times (ms) of the observation rows of type "key" with press = true. Rows of any
    other type are skipped without reading any field of theirs."""
    out = []
    for n, raw in _lines(data, "obs"):
        where = "obs:%d" % n
        row = _loads(raw, where, False)
        if not isinstance(row, dict):
            raise InputError("%s: the row is not an object" % where)
        if row.get("type") != "key":
            continue
        if not _is_int(row.get("t")) or not isinstance(row.get("press"), bool):
            raise InputError("%s: a key row lacks an integer t or a boolean press" % where)
        if row["press"]:
            out.append(row["t"])
    out.sort()
    return out


def read_gt_events(data, ev):
    """Return the sorted wall times (ms) of the reference rows whose ev is the one given; nothing else is read."""
    out = []
    for n, raw in _lines(data, "gt"):
        where = "gt:%d" % n
        row = _loads(raw, where, False)
        if not isinstance(row, dict):
            raise InputError("%s: the row is not an object" % where)
        if row.get("kind") != "ev" or row.get("ev") != ev:
            continue
        if not _is_int(row.get("t_wall_ms")):
            raise InputError("%s: a %s row lacks an integer t_wall_ms" % (where, ev))
        out.append(row["t_wall_ms"])
    out.sort()
    return out


def read_gt_key_downs(data):
    """Return the sorted wall times (ms) of the reference rows whose ev is "key_down"."""
    return read_gt_events(data, "key_down")


def read_gt_focus(data):
    """Return (focus, last): focus is the list of (t_wall_ms, line number, ev) of the reference rows whose ev is
    "focus_out" or "focus_in", in the order of the log (sorted by time, equal times by line number); last is the
    greatest t_wall_ms of the rows this tool reads (key_down, focus_out, focus_in), None when there is none."""
    out = []
    last = None
    for n, raw in _lines(data, "gt"):
        where = "gt:%d" % n
        row = _loads(raw, where, False)
        if not isinstance(row, dict):
            raise InputError("%s: the row is not an object" % where)
        ev = row.get("ev")
        if row.get("kind") != "ev" or ev not in ("key_down", "focus_out", "focus_in"):
            continue
        if not _is_int(row.get("t_wall_ms")):
            raise InputError("%s: a %s row lacks an integer t_wall_ms" % (where, ev))
        t = row["t_wall_ms"]
        if last is None or t > last:
            last = t
        if ev != "key_down":
            out.append((t, n, ev))
    out.sort(key=lambda e: (e[0], e[1]))
    return out, last


def _loss_intervals(focus):
    """Return [(begin, end)] of the focus loss intervals: from a focus_out to the next focus_in, or to None (the
    end of the log) when none follows. focus = [(t, line number, ev)]; the rows are taken in the order of the log
    (by time, equal times by line number). A focus_out while a loss is open and a focus_in while none is open add
    nothing."""
    out, begin = [], None
    for t, _n, ev in sorted(focus, key=lambda e: (e[0], e[1])):
        if ev == "focus_out" and begin is None:
            begin = t
        elif ev == "focus_in" and begin is not None:
            out.append((begin, t))
            begin = None
    if begin is not None:
        out.append((begin, None))
    return out


def _extended_end(b, sent_times):
    """The end of the window in which the unmatched rows of a merged span ending at b are counted: a row that is
    displaced by a person's press can only take the next key sent when that key is at most OBS_MATCH_BEFORE_MS +
    OBS_MATCH_AFTER_MS after the previous one, and it arrives at most OBS_MATCH_AFTER_MS after its key."""
    gap = OBS_MATCH_BEFORE_MS + OBS_MATCH_AFTER_MS
    e = b + gap
    last = b + OBS_MATCH_BEFORE_MS
    i = bisect.bisect_right(sent_times, last)
    while i < len(sent_times) and sent_times[i] - last <= gap:
        last = sent_times[i]
        e = max(e, last + OBS_MATCH_AFTER_MS)
        i += 1
    return e


def _focus_losses(done, plan, focus, last_row, starts, ends, obs_times, trials, marker_times):
    losses = _loss_intervals(focus)
    n_out = sum(1 for e in focus if e[2] == "focus_out")
    def overlap(loss, i):
        return loss[0] <= ends[i] and (loss[1] is None or loss[1] >= starts[i])
    def inside(loss, i):
        return loss[0] >= starts[i] and loss[1] is not None and loss[1] <= ends[i]
    hit = []
    for i, t in enumerate(done):
        mine = [loss for loss in losses if overlap(loss, i)]
        planned = plan[t["trial_id"]]["scenario"] == "S15" and all(inside(loss, i) for loss in mine)
        hit.append(bool(mine) and not planned)
    outside = sum(1 for loss in losses if not any(overlap(loss, i) for i in range(len(done))))
    planned = [any(plan[t["trial_id"]]["scenario"] == "S15" and inside(loss, i) for i, t in enumerate(done))
               for loss in losses]
    planned_losses = sum(1 for p in planned if p)
    spans = [(loss[0] - FOCUS_MARGIN_MS, None if loss[1] is None else loss[1] + FOCUS_MARGIN_MS)
             for loss, p in zip(losses, planned) if not p]
    def within(t, span):
        return (span[0] is None or t >= span[0]) and (span[1] is None or t <= span[1])
    with_presses = sum(1 for span in spans if any(within(t, span) for t in obs_times))
    n_presses = sum(1 for t in obs_times if any(within(t, span) for span in spans))
    # the keys sent that the observation is expected to record, set against its rows in the merged spans
    sent_times = sorted([s["wall"] for t in trials for s in _presses(t) if s["key"] not in MODE_KEYS]
                        + list(marker_times))
    # one global, greedy, time-ordered one-to-one matching of the rows to the keys sent: a row r may take a press
    # sent at s when r - OBS_MATCH_AFTER_MS <= s <= r + OBS_MATCH_BEFORE_MS; rows left without a press are unmatched
    unmatched = []
    ptr = 0
    for r in sorted(obs_times):
        while ptr < len(sent_times) and sent_times[ptr] < r - OBS_MATCH_AFTER_MS:
            ptr += 1
        if ptr < len(sent_times) and sent_times[ptr] <= r + OBS_MATCH_BEFORE_MS:
            ptr += 1
        else:
            unmatched.append(r)
    # the losses of all kinds (planned ones too) and the two added ones: from the start of the logs (None) up to
    # the first focus_in when the first focus row is a focus_in, or over the whole log when it has no focus row;
    # and from the last row of the reference log to the end of the logs. Each is widened by the margin (None stays
    # open) and spans that overlap or touch are merged.
    added = []
    if not focus:
        added.append((None, None))
    elif focus[0][2] == "focus_in":
        added.append((None, focus[0][0]))
    if last_row is not None:
        added.append((last_row, None))
    all_spans = [(None if loss[0] is None else loss[0] - FOCUS_MARGIN_MS,
                  None if loss[1] is None else loss[1] + FOCUS_MARGIN_MS) for loss in list(losses) + added]
    merged = []
    for a, b in sorted(all_spans, key=lambda sp: (sp[0] is not None, sp[0] or 0)):
        if merged and (merged[-1][1] is None or a is None or a <= merged[-1][1]):
            if merged[-1][1] is not None:
                merged[-1][1] = None if b is None else max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # the window in which a merged span counts its unmatched rows: a closed span is extended (see _extended_end)
    windows = [(a, None if b is None else _extended_end(b, sent_times)) for a, b in merged]
    unexplained = [sum(1 for r in unmatched if within(r, w)) for w in windows]
    n_unexplained_rows = sum(1 for r in unmatched if any(within(r, w) for w in windows))
    per = {}
    for t, got in zip(done, hit):
        info = plan[t["trial_id"]]
        c = per.setdefault((info["scenario"], info["speed"]), [0, 0])
        c[0] += 1
        c[1] += 1 if got else 0
    def order(k):
        sc = k[0][1:]
        return (int(sc) if sc.isdigit() else 0, k[0], SPEEDS.index(k[1]))
    return {"n_focus_out": n_out, "n_focus_in": len(focus) - n_out, "n_loss_intervals": len(losses),
            "n_loss_intervals_outside_trials": outside, "n_loss_intervals_planned": planned_losses,
            "n_loss_intervals_unplanned": len(losses) - planned_losses,
            "n_loss_intervals_unplanned_with_presses": with_presses, "n_presses_in_unplanned_losses": n_presses,
            "n_loss_spans": len(merged),
            "n_loss_spans_with_unexplained_presses": sum(1 for u in unexplained if u > 0),
            "n_presses_unexplained_in_losses": n_unexplained_rows,
            "n_trials_with_focus_loss": sum(1 for g in hit if g),
            "trials_with_focus_loss": sorted(t["trial_id"] for t, got in zip(done, hit) if got),
            "by_stratum": [{"scenario": k[0], "speed": k[1], "n_trials": per[k][0], "n_trials_with_focus_loss": per[k][1]}
                           for k in sorted(per, key=order)]}


# --- computation -----------------------------------------------------------------------------------------------

def _percentile(sorted_vals, p):
    pos = p * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (pos - lo) * (sorted_vals[hi] - sorted_vals[lo])


def summarize(values):
    """{n, mean, min, p05, median, p95, max}; null fields when there is no value (linear-interpolation
    percentiles)."""
    vals = sorted(values)
    if not vals:
        return {"n": 0, "mean": None, "min": None, "p05": None, "median": None, "p95": None, "max": None}
    return {"n": len(vals), "mean": round(sum(vals) / len(vals), 6), "min": round(vals[0], 6),
            "p05": round(_percentile(vals, 0.05), 6), "median": round(_percentile(vals, 0.5), 6),
            "p95": round(_percentile(vals, 0.95), 6), "max": round(vals[-1], 6)}


def _by_speed(pairs):
    """pairs = [(speed, value)] -> {"overall": summary, "by_speed": {speed: summary}}."""
    return {"overall": summarize([v for _, v in pairs]),
            "by_speed": {s: summarize([v for sp, v in pairs if sp == s]) for s in SPEEDS}}


def _presses(trial):
    return [s for s in trial["sent"] if s["down"] and s["key"] not in PSEUDO_KEYS]


def _windows(done, cuts, pre_ms, tail_ms):
    starts = [t["begin_wall"] - pre_ms for t in done]
    ends = []
    for t in done:
        end = t["end_wall"] + tail_ms
        i = bisect.bisect_right(cuts, t["end_wall"])
        if i < len(cuts):
            end = min(end, cuts[i] - 1)
        ends.append(end)
    return starts, ends


def _assign(times, starts, ends):
    """Assign sorted row times to the trial intervals. Return (rows per trial, number outside every interval)."""
    rows = [[] for _ in starts]
    outside = 0
    for ts in times:
        i = bisect.bisect_right(starts, ts) - 1
        if i >= 0 and ts <= ends[i]:
            rows[i].append(ts)
        else:
            outside += 1
    return rows, outside


def _reach(sent_counts, got_rows, outside):
    sent = sum(sent_counts)
    reached = sum(min(s, len(g)) for s, g in zip(sent_counts, got_rows))
    return {
        "sent_presses": sent,
        "reached": reached,
        "reach_rate": round(reached / sent, 6) if sent else None,
        "excess_rows": sum(max(0, len(g) - s) for s, g in zip(sent_counts, got_rows)),
        "rows_outside_trials": outside,
        "trials_with_loss": sum(1 for s, g in zip(sent_counts, got_rows) if len(g) < s),
        "trials_with_excess": sum(1 for s, g in zip(sent_counts, got_rows) if len(g) > s),
    }


def _timing(done, speeds, got_rows):
    delays, errors = [], []
    paired = not_paired = 0
    for t, speed, rows in zip(done, speeds, got_rows):
        sent = [s for s in _presses(t) if s["key"] not in MODE_KEYS]
        if len(sent) != len(rows):
            not_paired += 1
            continue
        if not sent:
            continue
        paired += 1
        for s, r in zip(sent, rows):
            delays.append((speed, r - s["wall"]))
        for i in range(len(sent) - 1):
            errors.append((speed, (rows[i + 1] - rows[i]) - (sent[i + 1]["wall"] - sent[i]["wall"])))
    return {"n_trials_paired": paired, "n_trials_not_paired": not_paired,
            "delay_ms": _by_speed(delays), "interval_error_ms": _by_speed(errors)}


def _segments(done, times, rows, begins, markers, counts):
    """Per segment (see Definitions): the rows that fall in it, those explained by a press that was sent (counts =
    the number of presses sent per measured trial, in the order of done), and the rest. begins = [(wall time,
    block_id)] sorted by time; markers = {block_id: marker presses}."""
    cut_times = [b[0] for b in begins]
    n_rows = [0] * (len(begins) + 1)
    reached = [0] * (len(begins) + 1)
    last_row = [None] * (len(begins) + 1)
    for ts in times:
        i = bisect.bisect_right(cut_times, ts)
        n_rows[i] += 1
        last_row[i] = ts
    for t, got, sent in zip(done, rows, counts):
        seg = bisect.bisect_right(cut_times, t["begin_wall"])
        inside = sum(1 for ts in got if bisect.bisect_right(cut_times, ts) == seg)
        reached[seg] += min(sent, inside)
    by_segment = []
    for i in range(len(begins) + 1):
        bid = begins[i - 1][1] if i else None
        marker = markers[bid] if i else 0
        unexplained = n_rows[i] - reached[i]
        to_next = begins[i][0] - last_row[i] if i < len(begins) and last_row[i] is not None else None
        by_segment.append({"block_id": bid, "n_rows": n_rows[i], "n_reached": reached[i],
                           "n_unexplained": unexplained, "n_marker_presses": marker,
                           "n_unexplained_beyond_markers": max(0, unexplained - marker),
                           "ms_from_last_row_to_next_block": to_next})
    beyond = [b["n_unexplained_beyond_markers"] for b in by_segment]
    return {"n_segments": len(by_segment),
            "max_unexplained_beyond_markers": max(beyond),
            "max_beyond_markers_before_last_block": max(beyond[:-1]) if begins else beyond[0],
            "beyond_markers_last_block": beyond[-1] if begins else None,
            "by_segment": by_segment}


def _side(done, speeds, times, starts, ends, begins, markers):
    rows, outside = _assign(times, starts, ends)
    all_counts = [len(_presses(t)) for t in done]
    excl_counts = [len([s for s in _presses(t) if s["key"] not in MODE_KEYS]) for t in done]
    return {"reach": {"all_presses": _reach(all_counts, rows, outside),
                      "excluding_mode_keys": _reach(excl_counts, rows, outside)},
            "timing": _timing(done, speeds, rows),
            "segments": {"all_presses": _segments(done, times, rows, begins, markers, all_counts),
                         "excluding_mode_keys": _segments(done, times, rows, begins, markers, excl_counts)}}


def evaluate(plan_bytes, oracle_bytes, obs_bytes, gt_bytes, pre_ms=20, tail_ms=300):
    """Compute the delivery report from the four input byte strings. Raises InputError on invalid input."""
    if not _is_int(pre_ms) or not _is_int(tail_ms) or pre_ms < 0 or tail_ms < 0:
        raise InputError("pre_ms and tail_ms must be integers of 0 or greater")
    sha = {"plan": hashlib.sha256(plan_bytes).hexdigest(), "oracle": hashlib.sha256(oracle_bytes).hexdigest(),
           "obs": hashlib.sha256(obs_bytes).hexdigest(), "gt": hashlib.sha256(gt_bytes).hexdigest()}
    plan, markers = read_plan(plan_bytes)
    trials, cuts, begins, marker_times = read_oracle(oracle_bytes, plan, sha["plan"], markers)
    obs_times = read_obs_presses(obs_bytes)
    gt_times = read_gt_key_downs(gt_bytes)
    focus, last_row = read_gt_focus(gt_bytes)

    lateness = []
    for t in trials:
        steps = plan[t["trial_id"]]["steps"]
        for s in _presses(t):
            sched_ns = t["begin_mono"] + steps[s["step"]][1] * 1000000
            lateness.append((plan[t["trial_id"]]["speed"], (s["mono"] - sched_ns) / 1e6))

    done = sorted((t for t in trials if t["end_wall"] is not None), key=lambda t: t["begin_wall"])
    speeds = [plan[t["trial_id"]]["speed"] for t in done]
    starts, ends = _windows(done, cuts, pre_ms, tail_ms)
    n_all = sum(len(_presses(t)) for t in trials)
    n_excl = sum(len([s for s in _presses(t) if s["key"] not in MODE_KEYS]) for t in trials)
    return {
        "format": FORMAT,
        "params": {"pre_ms": pre_ms, "tail_ms": tail_ms, "pseudo_keys": sorted(PSEUDO_KEYS),
                   "mode_keys": sorted(MODE_KEYS), "focus_margin_ms": FOCUS_MARGIN_MS,
                   "obs_match_before_ms": OBS_MATCH_BEFORE_MS, "obs_match_after_ms": OBS_MATCH_AFTER_MS},
        "inputs_sha256": sha,
        "trials": {"planned": len(plan), "begun": len(trials), "measured": len(done),
                   "incomplete": len(trials) - len(done)},
        "presses": {"all": n_all, "excluding_mode_keys": n_excl},
        "send_lateness_ms": _by_speed(lateness),
        "observation": _side(done, speeds, obs_times, starts, ends, begins, markers),
        "reference": _side(done, speeds, gt_times, starts, ends, begins, markers),
        "reference_focus_loss": _focus_losses(done, plan, focus, last_row, starts, ends, obs_times,
                                              trials, marker_times),
    }


def render(out):
    """Render the output bytes (LF line endings, UTF-8, no BOM, keys in a fixed sorted order)."""
    return (json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


# --- selftest begin ---
def _jl(rows):
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode("utf-8")


W0 = 1_700_000_000_000
MONO0 = 7_000_000_000
PLAN_STEPS = {
    "t1": ("slow", [("a", 0, True), ("a", 50, False), ("ime_on", 200, True), ("ime_on", 250, False),
                    ("b", 400, True), ("b", 450, False)]),
    "t2": ("fast", [("c", 0, True), ("c", 30, False), ("d", 100, True), ("d", 130, False)]),
    "t3": ("mid", [("focus_away", 0, True), ("focus_away", 10, False), ("e", 100, True), ("e", 130, False)]),
}
BEGIN = {"t1": 0, "t2": 2000, "t3": 4000}
END = {"t1": 500, "t2": 2200, "t3": 4300}
LATE_NS = {("t1", 0): 2_000_000, ("t1", 2): 1_000_000, ("t1", 4): 3_500_000, ("t2", 2): 4_000_000}


FOCUS0 = -100_000  # ms from W0: the reference field has the focus from before every row the fixtures use


def _fixture(plan_edit=None, oracle_edit=None, extra_obs=(), obs_drop=None, focus0=True):
    """Plan, oracle, observation and reference bytes. Hand-computed truth of the default fixture:
    lateness a 2, ime_on 1, b 3.5, c 0, d 4, e 0 ms; observation rows lose the mode-key press of t1 and gain a
    duplicate in t3; reference rows also see the mode key. A marker follows t2 at +2210 ms. The reference log starts
    with a focus_in at FOCUS0 unless focus0 is False."""
    plan_rows = [{"kind": "header", "format": "plan/1", "os": "windows"},
                 {"kind": "block", "block_id": 0, "marker": ["m1", "m2"]}]
    for tid, (speed, steps) in PLAN_STEPS.items():
        plan_rows.append({"kind": "trial", "trial_id": tid, "block_id": 0, "scenario": "S1", "speed": speed,
                          "steps": [{"key": k, "at_ms": at, "down": d} for k, at, d in steps]})
    if plan_edit:
        plan_edit(plan_rows)
    plan = _jl(plan_rows)
    ora = [{"kind": "header", "format": "oracle/1", "plan_sha256": hashlib.sha256(plan).hexdigest()},
           {"kind": "block_begin", "block_id": 0, "t_wall_ms": W0 - 200, "t_mono_ns": MONO0 - 200_000_000},
           {"kind": "marker", "block_id": 0, "seq": 0, "t_wall_ms": W0 - 100, "t_mono_ns": MONO0 - 100_000_000}]
    for tid, (speed, steps) in PLAN_STEPS.items():
        bw, bm = W0 + BEGIN[tid], MONO0 + BEGIN[tid] * 1_000_000
        ora.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": bw, "t_mono_ns": bm})
        for i, (k, at, d) in enumerate(steps):
            late = LATE_NS.get((tid, i), 0)
            ora.append({"kind": "sent", "trial_id": tid, "step": i, "key": k, "down": d,
                        "t_wall_ms": bw + at + late // 1_000_000, "t_mono_ns": bm + at * 1_000_000 + late})
        ora.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": W0 + END[tid],
                    "t_mono_ns": MONO0 + END[tid] * 1_000_000})
        if tid == "t2":
            ora.append({"kind": "marker", "block_id": 0, "seq": 1, "t_wall_ms": W0 + 2210,
                        "t_mono_ns": MONO0 + 2210 * 1_000_000})
    ora.append({"kind": "block_end", "block_id": 0, "t_wall_ms": W0 + 6000, "t_mono_ns": MONO0 + 6000 * 1_000_000})
    if oracle_edit:
        oracle_edit(ora)
    obs_t = [W0 - 97, W0 + 7, W0 + 409, W0 + 2008, W0 + 2114, W0 + 2215, W0 + 4107, W0 + 4120]
    if obs_drop is not None:
        obs_t = [t for t in obs_t if t not in obs_drop]
    obs = [{"type": "meta", "format": "obs/1"}]
    obs += [{"type": "key", "t": t, "category": "alphabetic", "press": True} for t in reversed(obs_t)]
    obs += [{"type": "key", "t": t + 20, "category": "alphabetic", "press": False} for t in obs_t]
    obs += list(extra_obs)
    gt = [{"kind": "header", "format": "gt/1", "src": "x"}]
    if focus0:
        gt.append({"kind": "ev", "t_wall_ms": W0 + FOCUS0, "t_mono_ns": 1, "ev": "focus_in"})
    gt += [{"kind": "ev", "t_wall_ms": W0 + t, "t_mono_ns": 1, "ev": "key_down", "vk": 65}
           for t in (-97, 3, 202, 405, 2001, 2105, 2211, 4101)]
    gt += [{"kind": "ev", "t_wall_ms": W0 + 5, "t_mono_ns": 1, "ev": "set_marked", "len": "bad"},
           {"kind": "ev", "t_wall_ms": W0 + 6, "t_mono_ns": 1, "ev": "key_up", "vk": 65},
           {"kind": "ev", "t_wall_ms": W0 + 4110, "t_mono_ns": 1, "ev": "focus_out"},
           {"kind": "ev", "t_wall_ms": W0 + 5000, "t_mono_ns": 1, "ev": "focus_out"}]
    return plan, _jl(ora), _jl(obs), _jl(gt)


SEG_TRIALS = (("a0", 0, 1000, 1200), ("a1", 0, 2000, 2200), ("b0", 1, 4500, 4700))
SEG_BEGIN = {0: 0, 1: 3500}


def _seg_fixture(gt_extra=(), gt_drop=(), plan_edit=None, oracle_edit=None, focus0=True):
    """Two blocks of four marker presses each (an oracle marker row per press, a reference key_down 2 ms after it)
    and three one-press trials (a0, a1 in block 0; b0 in block 1; the key_down is 5 ms after the send). Times are
    ms from W0. The reference rows of the default fixture: 4 + 2 of block 0, 4 + 1 of block 1. gt_extra adds
    key_down rows, gt_drop removes the ones at the times given."""
    plan_rows = [{"kind": "header", "format": "plan/1", "os": "macos"}]
    for b in (0, 1):
        plan_rows.append({"kind": "block", "block_id": b, "marker": ["m1", "m2", "m3", "m4"]})
    for tid, b, _bg, _en in SEG_TRIALS:
        plan_rows.append({"kind": "trial", "trial_id": tid, "block_id": b, "scenario": "S1", "speed": "mid",
                          "steps": [{"key": "a", "at_ms": 0, "down": True}, {"key": "a", "at_ms": 50, "down": False}]})
    if plan_edit:
        plan_edit(plan_rows)
    plan = _jl(plan_rows)
    ora = [{"kind": "header", "format": "oracle/1", "plan_sha256": hashlib.sha256(plan).hexdigest()}]
    gt_t, obs_t = [], []
    for b in (0, 1):
        ora.append({"kind": "block_begin", "block_id": b, "t_wall_ms": W0 + SEG_BEGIN[b], "t_mono_ns": 1})
        for i in range(4):
            ora.append({"kind": "marker", "block_id": b, "seq": i, "t_wall_ms": W0 + SEG_BEGIN[b] + 10 * (i + 1),
                        "t_mono_ns": 1})
            gt_t.append(SEG_BEGIN[b] + 10 * (i + 1) + 2)
        for tid, tb, bg, en in SEG_TRIALS:
            if tb != b:
                continue
            ora.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": W0 + bg, "t_mono_ns": (W0 + bg) * 1000000})
            for i, down in enumerate((True, False)):
                ora.append({"kind": "sent", "trial_id": tid, "step": i, "key": "a", "down": down,
                            "t_wall_ms": W0 + bg + 50 * i, "t_mono_ns": (W0 + bg + 50 * i) * 1000000})
            ora.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": W0 + en, "t_mono_ns": (W0 + en) * 1000000})
            gt_t.append(bg + 5)
            obs_t.append(bg + 5)
        ora.append({"kind": "block_end", "block_id": b, "t_wall_ms": W0 + SEG_BEGIN[b] + 1900, "t_mono_ns": 1})
    if oracle_edit:
        oracle_edit(ora)
    gt_t = [t for t in gt_t if t not in gt_drop] + list(gt_extra)
    obs = [{"type": "meta", "format": "obs/1"}] + [{"type": "key", "t": W0 + t, "category": "alphabetic", "press": True}
                                                    for t in obs_t]
    gt = [{"kind": "header", "format": "gt/1", "src": "x"}]
    if focus0:
        gt.append({"kind": "ev", "t_wall_ms": W0 + FOCUS0, "t_mono_ns": 1, "ev": "focus_in"})
    gt += [{"kind": "ev", "t_wall_ms": W0 + t, "t_mono_ns": 1, "ev": "key_down", "vk": 65} for t in gt_t]
    return plan, _jl(ora), _jl(obs), _jl(gt)


MINI_NS = 1_000_000


def _mini(blocks, trials, gt, obs=(), marks=(), focus0=True):
    """A small fixture. blocks = [(block_id, begin ms)]; trials = [(trial_id, block_id, scenario, begin ms, end ms,
    keys)]; gt = [(time ms, ev)]; obs = key press times; marks = times of marker rows of the oracle. The i-th key
    of a trial is pressed at begin + 10 + 30 * i and released 10 ms later (the oracle holds both). Times are ms
    from W0; block 0 of the plan has one marker key for each time in marks, the other blocks have none. The reference
    log starts with a focus_in at FOCUS0 unless focus0 is False."""
    plan_rows = [{"kind": "header", "format": "plan/1", "os": "macos"}]
    plan_rows += [{"kind": "block", "block_id": b, "marker": ["m"] * len(marks) if b == 0 else []} for b, _bg in blocks]
    for tid, b, scen, _bg, _en, keys in trials:
        steps = []
        for i, k in enumerate(keys):
            steps += [{"key": k, "at_ms": 10 + 30 * i, "down": True}, {"key": k, "at_ms": 20 + 30 * i, "down": False}]
        plan_rows.append({"kind": "trial", "trial_id": tid, "block_id": b, "scenario": scen, "speed": "mid",
                          "steps": steps})
    plan = _jl(plan_rows)
    ora = [{"kind": "header", "format": "oracle/1", "plan_sha256": hashlib.sha256(plan).hexdigest()}]
    for b, bg in blocks:
        ora.append({"kind": "block_begin", "block_id": b, "t_wall_ms": W0 + bg, "t_mono_ns": (W0 + bg) * MINI_NS})
    for tid, _b, _scen, bg, en, keys in trials:
        ora.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": W0 + bg, "t_mono_ns": (W0 + bg) * MINI_NS})
        for i, k in enumerate(keys):
            for j, down in enumerate((True, False)):
                t = bg + 10 + 30 * i + 10 * j
                ora.append({"kind": "sent", "trial_id": tid, "step": 2 * i + j, "key": k, "down": down,
                            "t_wall_ms": W0 + t, "t_mono_ns": (W0 + t) * MINI_NS})
        ora.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": W0 + en, "t_mono_ns": (W0 + en) * MINI_NS})
    ora += [{"kind": "marker", "block_id": 0, "seq": i, "t_wall_ms": W0 + t, "t_mono_ns": (W0 + t) * MINI_NS}
            for i, t in enumerate(marks)]
    gt_rows = [{"kind": "header", "format": "gt/1", "src": "x"}]
    if focus0:
        gt_rows.append({"kind": "ev", "t_wall_ms": W0 + FOCUS0, "t_mono_ns": 1, "ev": "focus_in"})
    gt_rows += [{"kind": "ev", "t_wall_ms": W0 + t, "t_mono_ns": 1, "ev": ev, "vk": 65} for t, ev in gt]
    obs_rows = [{"type": "meta", "format": "obs/1"}]
    obs_rows += [{"type": "key", "t": W0 + t, "category": "alphabetic", "press": True} for t in obs]
    return plan, _jl(ora), _jl(obs_rows), _jl(gt_rows)


def _kd(*times):
    return [(t, "key_down") for t in times]


def _close(a, b):
    if a is None or b is None:
        return a is b
    return abs(a - b) < 1e-6


def _selftest():
    fails = []

    def check(ok, what):
        print("  %s %s" % ("OK" if ok else "NG", what))
        if not ok:
            fails.append(what)

    def summ_is(s, n, mean, mn, p05, med, p95, mx):
        got = [s["n"], s["mean"], s["min"], s["p05"], s["median"], s["p95"], s["max"]]
        return all(_close(g, e) for g, e in zip(got, [n, mean, mn, p05, med, p95, mx]))

    def refused(fx, needle=None):
        try:
            evaluate(*fx)
        except InputError as e:
            return needle is None or needle not in str(e)
        return False

    fx = _fixture()
    out = evaluate(*fx)

    lat = out["send_lateness_ms"]
    check(summ_is(lat["overall"], 6, 1.75, 0, 0, 1.5, 3.875, 4), "send lateness overall (sign, unit, monotonic clock)")
    check(summ_is(lat["by_speed"]["slow"], 3, 2.166667, 1, 1.1, 2, 3.35, 3.5)
          and summ_is(lat["by_speed"]["fast"], 2, 2, 0, 0.2, 2, 3.8, 4)
          and summ_is(lat["by_speed"]["mid"], 1, 0, 0, 0, 0, 0, 0), "send lateness by speed")
    check(out["presses"] == {"all": 6, "excluding_mode_keys": 5}, "pseudo-keys are not presses")

    o = out["observation"]["reach"]
    a, x = o["all_presses"], o["excluding_mode_keys"]
    check(a["sent_presses"] == 6 and a["reached"] == 5 and _close(a["reach_rate"], 5 / 6)
          and a["trials_with_loss"] == 1, "observation reach over all presses: a lost press lowers reach")
    check(a["excess_rows"] == 1 and a["trials_with_excess"] == 1, "observation: a duplicate row is counted as excess")
    check(x["sent_presses"] == 5 and x["reached"] == 5 and x["reach_rate"] == 1.0 and x["trials_with_loss"] == 0
          and x["excess_rows"] == 1, "observation reach excluding mode keys")
    check(a["rows_outside_trials"] == 2 and x["rows_outside_trials"] == 2,
          "marker rows (head of block, and after trial end) fall outside every trial")
    g = out["reference"]["reach"]
    ga, gx = g["all_presses"], g["excluding_mode_keys"]
    check(ga["sent_presses"] == 6 and ga["reached"] == 6 and ga["excess_rows"] == 0 and ga["trials_with_loss"] == 0
          and ga["rows_outside_trials"] == 2, "reference reach over all presses (mode key is seen)")
    check(gx["sent_presses"] == 5 and gx["reached"] == 5 and gx["excess_rows"] == 1 and gx["trials_with_excess"] == 1,
          "reference reach excluding mode keys counts the mode-key row as excess")

    ot = out["observation"]["timing"]
    check(ot["n_trials_paired"] == 2 and ot["n_trials_not_paired"] == 1, "observation: unequal-count trial is not paired")
    check(summ_is(ot["delay_ms"]["overall"], 4, 7.25, 5, 5.15, 7, 9.7, 10)
          and summ_is(ot["delay_ms"]["by_speed"]["slow"], 2, 5.5, 5, 5.05, 5.5, 5.95, 6)
          and ot["delay_ms"]["by_speed"]["mid"]["n"] == 0 and ot["delay_ms"]["by_speed"]["mid"]["mean"] is None,
          "observation delay (row minus sent), overall and by speed")
    check(summ_is(ot["interval_error_ms"]["overall"], 2, 1.5, 1, 1.05, 1.5, 1.95, 2), "observation interval error "
          "uses both the row gaps and the sent gaps")
    gt_ = out["reference"]["timing"]
    check(gt_["n_trials_paired"] == 2 and gt_["n_trials_not_paired"] == 1
          and summ_is(gt_["delay_ms"]["overall"], 3, 1, 1, 1, 1, 1, 1)
          and summ_is(gt_["interval_error_ms"]["overall"], 1, 0, 0, 0, 0, 0, 0)
          and summ_is(gt_["interval_error_ms"]["by_speed"]["fast"], 1, 0, 0, 0, 0, 0, 0)
          and gt_["interval_error_ms"]["by_speed"]["mid"]["n"] == 0, "reference timing (mode-key trial not paired)")
    fo = out["reference_focus_loss"]
    want_fo = [{"scenario": "S1", "speed": "slow", "n_trials": 1, "n_trials_with_focus_loss": 0},
               {"scenario": "S1", "speed": "mid", "n_trials": 1, "n_trials_with_focus_loss": 1},
               {"scenario": "S1", "speed": "fast", "n_trials": 1, "n_trials_with_focus_loss": 0}]
    check(fo["trials_with_focus_loss"] == ["t3"], "the trials with a focus loss are listed: %r" % (fo["trials_with_focus_loss"],))
    check(fo["n_focus_out"] == 2 and fo["n_focus_in"] == 1 and fo["n_loss_intervals"] == 1
          and fo["n_loss_intervals_outside_trials"] == 0 and fo["n_trials_with_focus_loss"] == 1
          and fo["by_stratum"] == want_fo, "focus losses of the reference are counted per trial and stratum")

    # Focus loss intervals, on the fixture without its own focus_out rows. Trial intervals (ms from W0): t1 -20..800,
    # t2 1980..2209, t3 3980..4600. s15 turns t2 into a scenario S15 trial.
    def loss(events, s15=False):
        def edit(rows):
            if s15:
                rows[3]["scenario"] = "S15"
        f = _fixture(plan_edit=edit)
        keep = [r for r in (json.loads(x) for x in f[3].decode("utf-8").split("\n") if x) if r.get("ev") != "focus_out"]
        keep += [{"kind": "ev", "t_wall_ms": W0 + t, "t_mono_ns": 1, "ev": ev} for t, ev in events]
        return evaluate(f[0], f[1], f[2], _jl(keep))["reference_focus_loss"]
    r = loss([(2100, "focus_out")])
    check(r["trials_with_focus_loss"] == ["t2", "t3"] and r["n_loss_intervals"] == 1 and r["n_focus_in"] == 1,
          "a loss that is not regained covers the following trials up to the end of the log")
    r = loss([(700, "focus_out"), (2100, "focus_in")])
    check(r["trials_with_focus_loss"] == ["t1", "t2"] and r["n_focus_in"] == 2,
          "a loss regained in the next trial covers both trials and not the one after")
    r = loss([(2050, "focus_out"), (2150, "focus_in")], s15=True)
    check(r["trials_with_focus_loss"] == [] and r["n_loss_intervals"] == 1 and r["n_loss_intervals_outside_trials"] == 0,
          "a planned S15 switch that returns inside the trial is not listed")
    r = loss([(2050, "focus_out"), (2150, "focus_in")])
    check(r["trials_with_focus_loss"] == ["t2"], "the same switch in a scenario other than S15 is listed")
    r = loss([(2050, "focus_out")], s15=True)
    check(r["trials_with_focus_loss"] == ["t2", "t3"],
          "an S15 switch that does not return is listed together with the following trials")
    r = loss([(2050, "focus_out"), (2300, "focus_in")], s15=True)
    check(r["trials_with_focus_loss"] == ["t2"], "an S15 switch that returns only after the trial's interval is listed")
    r = loss([(2050, "focus_out"), (2100, "focus_in"), (2150, "focus_out")], s15=True)
    check(r["trials_with_focus_loss"] == ["t2", "t3"],
          "an S15 switch that returns, followed by a second loss that does not, is listed with the following trials")
    r = loss([(2300, "focus_out"), (3500, "focus_in")])
    check(r["trials_with_focus_loss"] == [] and r["n_loss_intervals"] == 1 and r["n_loss_intervals_outside_trials"] == 1,
          "a loss between two trials that is regained before the next trial overlaps no trial")
    r = loss([(3500, "focus_in"), (2050, "focus_out"), (2060, "focus_out"), (2100, "focus_in")])
    check(r["n_loss_intervals"] == 1 and r["n_focus_out"] == 2 and r["n_focus_in"] == 3
          and r["trials_with_focus_loss"] == ["t2"]
          and [(b["speed"], b["n_trials_with_focus_loss"]) for b in r["by_stratum"]]
          == [("slow", 0), ("mid", 0), ("fast", 1)],
          "a focus_in with no loss open and a repeated focus_out add no interval")
    check(out["trials"] == {"planned": 3, "begun": 3, "measured": 3, "incomplete": 0}
          and out["inputs_sha256"]["plan"] == hashlib.sha256(fx[0]).hexdigest(), "trial counts and input hashes")

    # window tail is cut at the next oracle row: t2 receives exactly its own two rows.
    rows, _ = _assign([W0 + 2008, W0 + 2114, W0 + 2215], *_windows(
        [{"begin_wall": W0 + 2000, "end_wall": W0 + 2200}], [W0 + 2000, W0 + 2210], 20, 300))
    check(len(rows[0]) == 2, "the interval tail is cut at the next oracle row (marker row lands outside)")
    one = [{"begin_wall": W0 + 2000, "end_wall": W0 + 2200}]
    check(_windows(one, [], 20, 0) == ([W0 + 1980], [W0 + 2200]) and _windows(one, [], 5, 300) == ([W0 + 1995], [W0 + 2500])
          and evaluate(*fx, pre_ms=0, tail_ms=7)["params"] == dict(out["params"], pre_ms=0, tail_ms=7),
          "pre_ms and tail_ms are honoured")

    # all rows lost, and a lost-only variant.
    none = evaluate(*_fixture(obs_drop=set(W0 + t for t in (7, 409, 2008, 2114, 4107, 4120))))
    check(none["observation"]["reach"]["all_presses"]["reached"] == 0
          and none["observation"]["timing"]["n_trials_paired"] == 0, "no row received: reach is zero, nothing paired")

    # ime_shadow rows of any shape change nothing, and are never read.
    junk = [{"type": "ime_shadow", "t": "bad", "press": True, "dwell_ms": None},
            {"type": "ime_shadow", "t": W0 + 10, "press": True, "dwell_ms": [1]},
            {"type": "ime_shadow", "t": W0 + 2010}, {"type": "ime_shadow"}]
    with_junk = evaluate(*_fixture(extra_obs=junk))
    with_junk["inputs_sha256"]["obs"] = out["inputs_sha256"]["obs"]
    check(with_junk == out, "ime_shadow rows with malformed fields do not change anything")
    check(refused((fx[0], fx[1], fx[2] + b"not json\n", fx[3])), "an unparsable observation row is refused")

    # determinism and rendering.
    r1, r2 = render(evaluate(*fx)), render(evaluate(*_fixture()))
    check(r1 == r2 and r1.endswith(b"\n") and b"\r" not in r1 and not r1.startswith(b"\xef\xbb\xbf"),
          "two runs are byte-identical (LF, no BOM)")
    check(json.loads(r1.decode("utf-8")) == out and "\\" not in r1.decode("utf-8"), "output round-trips and holds no path")

    # refusals.
    def other_plan(rows):
        rows[2]["speed"] = "mid"
    check(refused((_fixture(plan_edit=other_plan)[0], fx[1], fx[2], fx[3])), "an unrelated plan is refused")

    def bad_key(ora):
        next(r for r in ora if r.get("kind") == "sent")["key"] = "zzsecretkey"

    def bad_down(ora):
        next(r for r in ora if r.get("kind") == "sent")["down"] = False

    def bad_step(ora):
        next(r for r in ora if r.get("kind") == "sent")["step"] = 99

    def no_begin(ora):
        ora[:] = [r for r in ora if not (r.get("kind") == "trial_begin" and r["trial_id"] == "t2")]

    def two_ends(ora):
        ora.append(dict(next(r for r in ora if r.get("kind") == "trial_end")))
    check(refused(_oracle_with(fx, bad_key), "zzsecretkey"), "sent key that disagrees with the plan is refused, value not echoed")
    check(refused(_oracle_with(fx, bad_down)), "sent down flag that disagrees with the plan is refused")
    check(refused(_oracle_with(fx, bad_step)), "sent step with no plan step is refused")
    check(refused(_oracle_with(fx, no_begin)), "a sent row without its trial_begin is refused")
    check(refused(_oracle_with(fx, two_ends)), "a repeated trial_end is refused")
    check(refused((fx[0], fx[1][:-1] + b"x\n", fx[2], fx[3])), "malformed oracle is refused")

    # segments. Reference rows per segment of the default fixture: the first holds none (gt_extra adds a key outside
    # any block there), block 0 holds its four marker presses and two trial presses, block 1 four marker presses and
    # one trial press.
    def plain(version):
        """A version of the segments without the time to the next block (checked on its own)."""
        return dict(version, by_segment=[dict((k, v) for k, v in b.items() if k != "ms_from_last_row_to_next_block")
                                         for b in version["by_segment"]])

    def segs(*args, version="all_presses", **kw):
        return plain(evaluate(*_seg_fixture(*args, **kw))["reference"]["segments"][version])

    def row(bid, n_rows, n_reached, marker):
        un = n_rows - n_reached
        return {"block_id": bid, "n_rows": n_rows, "n_reached": n_reached, "n_unexplained": un,
                "n_marker_presses": marker, "n_unexplained_beyond_markers": max(0, un - marker)}
    base = segs()
    check(base["by_segment"] == [row(None, 0, 0, 0), row(0, 6, 2, 4), row(1, 5, 1, 4)] and base["n_segments"] == 3
          and base["max_unexplained_beyond_markers"] == 0, "segments: marker presses are explained by the plan")
    check(segs(version="excluding_mode_keys") == base,
          "segments: the two versions are equal when no trial presses a mode key")
    kana_in, kana_out = segs(gt_extra=(-500, 2300)), segs(gt_extra=(-500, 2900))
    want = [row(None, 1, 0, 0), row(0, 7, 2, 4), row(1, 5, 1, 4)]
    check(kana_in["by_segment"] == want and kana_out["by_segment"] == want and kana_in == kana_out
          and kana_in["max_unexplained_beyond_markers"] == 1,
          "segments: a key without an oracle row counts once in its segment, inside or outside the last trial's window")
    full_in = evaluate(*_seg_fixture(gt_extra=(-500, 2300)))["reference"]["reach"]["all_presses"]
    full_out = evaluate(*_seg_fixture(gt_extra=(-500, 2900)))["reference"]["reach"]["all_presses"]
    check(full_in["excess_rows"] == 1 and full_out["excess_rows"] == 0
          and full_in["rows_outside_trials"] + 1 == full_out["rows_outside_trials"],
          "segments: the whole-run counts of reach do depend on where such a key falls (the reason for the segments)")
    at_begin = segs(gt_extra=(SEG_BEGIN[1],))["by_segment"]
    check(at_begin == [row(None, 0, 0, 0), row(0, 6, 2, 4), row(1, 6, 1, 4)],
          "segments: a row at the time of a block_begin belongs to the new block")
    before_begin = segs(gt_extra=(SEG_BEGIN[1] - 1,))["by_segment"]
    check(before_begin[1] == row(0, 7, 2, 4) and before_begin[2] == row(1, 5, 1, 4),
          "segments: a row one millisecond before a block_begin belongs to the previous block")
    swap = segs(gt_drop=(2005,), gt_extra=(2300,))["by_segment"]
    check(swap == base["by_segment"],
          "segments: a press that does not arrive, replaced by one from outside in the same trial, is not detected")
    lost = segs(gt_drop=(2005,))["by_segment"]
    check(lost[1] == row(0, 5, 1, 4) and lost[1]["n_unexplained"] == base["by_segment"][1]["n_unexplained"],
          "segments: a press that does not arrive adds nothing to n_unexplained")
    obs_seg = plain(evaluate(*_seg_fixture())["observation"]["segments"]["all_presses"])
    check(obs_seg["by_segment"] == [row(None, 0, 0, 0), row(0, 2, 2, 4), row(1, 1, 1, 4)]
          and obs_seg["max_unexplained_beyond_markers"] == 0, "segments: the observation side is counted the same way")
    nob = evaluate(_seg_fixture()[0], _oracle_with(_seg_fixture(), lambda rows: rows.__setitem__(
        slice(None), [r for r in rows if r.get("kind") != "block_begin"]))[1], *_seg_fixture()[2:])
    nob_seg = plain(nob["reference"]["segments"]["all_presses"])
    check(nob_seg["n_segments"] == 1 and nob_seg["by_segment"] == [row(None, 11, 3, 0)],
          "segments: with no block_begin the whole log is one segment with a null block_id")

    def plan_with(edit):
        return _seg_fixture(plan_edit=edit)

    def bad_marker(rows):
        rows[1]["marker"] = "zzmarker"

    def bad_marker_item(rows):
        rows[1]["marker"] = ["m1", 7]

    def dup_block(rows):
        rows.insert(2, dict(rows[1]))

    def extra_begin(ora):
        ora.append({"kind": "block_begin", "block_id": "zzblock", "t_wall_ms": W0 + 5000, "t_mono_ns": 1})

    def twice_begin(ora):
        ora.append({"kind": "block_begin", "block_id": 0, "t_wall_ms": W0 + 5000, "t_mono_ns": 1})

    def no_id_begin(ora):
        ora.append({"kind": "block_begin", "t_wall_ms": W0 + 5000, "t_mono_ns": 1})
    check(refused(plan_with(bad_marker), "zzmarker"), "a block row whose marker is not an array is refused, value not echoed")
    check(refused(plan_with(bad_marker_item)), "a block row whose marker holds a non-string is refused")
    check(refused(plan_with(dup_block)), "a repeated block_id in the plan is refused")
    check(refused(_oracle_with(_seg_fixture(), extra_begin), "zzblock"),
          "a block_begin of a block that is not in the plan is refused, value not echoed")
    check(refused(_oracle_with(_seg_fixture(), twice_begin)), "a repeated block_begin is refused")
    check(refused(_oracle_with(_seg_fixture(), no_id_begin)), "a block_begin without a block_id is refused")
    check(plain(json.loads(render(evaluate(*_seg_fixture())).decode("utf-8"))["reference"]["segments"]["all_presses"])
          == base, "segments: the rendered output holds the same figures")

    # segments: a trial that begins just after a block_begin (closer than pre_ms) and whose interval takes in a row
    # from before that block_begin. The row must not be counted as reached in the new block (the count would go
    # negative): trial b (block 1, begins 5 ms after the block_begin) presses 2 keys; one row arrives before the
    # block_begin (in b's interval), one inside the block, and the second press is lost.
    mini = _mini([(0, 0), (1, 3000)], [("a", 0, "S1", 100, 300, ["a"]), ("b", 1, "S1", 3005, 3300, ["a", "b"])],
                 _kd(115, 2990, 3020))
    sg = evaluate(*mini)["reference"]["segments"]["all_presses"]
    check([(b["n_rows"], b["n_reached"], b["n_unexplained"]) for b in sg["by_segment"]]
          == [(0, 0, 0), (2, 1, 1), (1, 1, 0)] and all(b["n_unexplained"] >= 0 for b in sg["by_segment"]),
          "segments: a row before a block_begin that falls in the interval of the next block's first trial is not "
          "counted as reached in the new block")

    # segments: the two versions, for a trial that presses a mode key which the reference does not receive. A key from
    # outside arrives within the trial's interval (the "a" is received at 140, the foreign key at 200).
    modes = _mini([(0, 0)], [("m", 0, "S1", 100, 400, ["ime_on", "a"])], _kd(140, 200))
    mv = evaluate(*modes)["reference"]["segments"]
    check(mv["all_presses"]["by_segment"][1]["n_unexplained"] == 0
          and mv["excluding_mode_keys"]["by_segment"][1]["n_unexplained"] == 1
          and mv["excluding_mode_keys"]["max_unexplained_beyond_markers"] == 1
          and mv["all_presses"]["max_unexplained_beyond_markers"] == 0,
          "segments: a foreign key in the window of a trial with a mode key shows only in the version that excludes "
          "the mode keys")
    modes_in = _mini([(0, 0)], [("m", 0, "S1", 100, 400, ["ime_on", "a"])], _kd(125, 140, 200))
    mi = evaluate(*modes_in)["reference"]["segments"]
    check(mi["all_presses"]["by_segment"][1]["n_unexplained"] == 1
          and mi["excluding_mode_keys"]["by_segment"][1]["n_unexplained"] == 2,
          "segments: when the mode key is received, the version of all presses counts the foreign key once")

    # segments: the summaries before and in the last block.
    two = [("a", 0, "S1", 100, 300, ["a"]), ("b", 1, "S1", 3100, 3300, ["a"])]
    blocks2 = [(0, 0), (1, 3000)]
    base_gt = [115, 3115]

    def summ(blocks, trials, extra):
        got = evaluate(*_mini(blocks, trials, _kd(*sorted(base_gt + list(extra)))))["reference"]["segments"]["all_presses"]
        return (got["max_beyond_markers_before_last_block"], got["beyond_markers_last_block"],
                got["max_unexplained_beyond_markers"])
    check(summ(blocks2, two, []) == (0, 0, 0), "segments: no foreign key leaves both summaries at 0")
    check(summ(blocks2, two, [3500]) == (0, 1, 1), "segments: a foreign key in the last block shows only in the last-block figure")
    check(summ(blocks2, two, [1000]) == (1, 0, 1), "segments: a foreign key in an earlier block shows only in the before-last figure")
    check(summ(blocks2, two, [-500]) == (1, 0, 1), "segments: a foreign key before the first block counts as before the last block")
    one = evaluate(*_mini([], [("a", 0, "S1", 100, 300, ["a"])], _kd(115, 500)))["reference"]["segments"]["all_presses"]
    check(one["n_segments"] == 1 and one["beyond_markers_last_block"] is None
          and one["max_beyond_markers_before_last_block"] == 1 and one["max_unexplained_beyond_markers"] == 1,
          "segments: with no block_begin the last-block figure is null and the other covers the one segment")

    # segments: the time from the last row of a segment to the next block (hand computed). Five blocks begin at 0,
    # 3000, 6000, 9000 and 12000; a foreign key at 2900 is the last row of the first block's segment; the third
    # block's segment ends with its trial's row at 6115; the fourth block has no row.
    five = [(0, 0), (1, 3000), (2, 6000), (3, 9000), (4, 12000)]
    trials5 = [("a", 0, "S1", 100, 300, ["a"]), ("b", 1, "S1", 3100, 3300, ["a"]), ("c", 2, "S1", 6100, 6300, ["a"]),
               ("d", 4, "S1", 12100, 12300, ["a"])]
    r5 = evaluate(*_mini(five, trials5, _kd(115, 2900, 3115, 6115, 12115)))["reference"]["segments"]["all_presses"]
    check([b["ms_from_last_row_to_next_block"] for b in r5["by_segment"]] == [None, 100, 2885, 2885, None, None],
          "segments: the time from the last row to the next block_begin (null for the last segment and an empty one)")
    check(evaluate(*_mini(five, trials5, _kd(115, 2900, 3115, 6115, 12115)))["observation"]["segments"]["excluding_mode_keys"]
          ["by_segment"][1]["ms_from_last_row_to_next_block"] is None, "segments: no observation row, no time to the next block")

    # focus losses: planned and unplanned loss intervals. S15 trial x runs 1000..2000 (window to 2300), S1 trial y
    # 4000..4500.
    def loss_counts(events):
        rows = _kd(1015, 4015) + [(t, ev) for t, ev in events]
        got = evaluate(*_mini([(0, 0)], [("x", 0, "S15", 1000, 2000, ["a"]), ("y", 0, "S1", 4000, 4500, ["a"])],
                              sorted(rows)))["reference_focus_loss"]
        return got["n_loss_intervals_planned"], got["n_loss_intervals_unplanned"], got["n_loss_intervals"]
    check(loss_counts([(1200, "focus_out"), (1500, "focus_in")]) == (1, 0, 1),
          "focus loss: a switch that returns inside an S15 trial is planned")
    check(loss_counts([(3000, "focus_out"), (3100, "focus_in")]) == (0, 1, 1),
          "focus loss: a loss between trials is unplanned")
    check(loss_counts([(1200, "focus_out")]) == (0, 1, 1), "focus loss: an S15 loss that is not regained is unplanned")
    check(loss_counts([(1200, "focus_out"), (2400, "focus_in")]) == (0, 1, 1),
          "focus loss: an S15 loss regained after the trial's interval is unplanned")
    check(loss_counts([(4100, "focus_out"), (4200, "focus_in")]) == (0, 1, 1),
          "focus loss: a loss inside a trial of another scenario is unplanned")
    check(loss_counts([(-300, "focus_out"), (-200, "focus_in"), (1200, "focus_out"), (1500, "focus_in")]) == (1, 1, 2),
          "focus loss: planned and unplanned losses add up to the loss intervals")
    check(loss_counts([]) == (0, 0, 0), "focus loss: no loss, no interval")

    # focus losses: key presses of the observation in an unplanned loss, with the margin FOCUS_MARGIN_MS around it.
    def loss_presses(events, obs, s15_events=()):
        rows = _kd(1015, 4015) + [(t, ev) for t, ev in list(events) + list(s15_events)]
        got = evaluate(*_mini([(0, 0)], [("x", 0, "S15", 1000, 2000, ["a"]), ("y", 0, "S1", 4000, 4500, ["a"])],
                              sorted(rows), obs))["reference_focus_loss"]
        return got["n_loss_intervals_unplanned_with_presses"], got["n_presses_in_unplanned_losses"]
    m = FOCUS_MARGIN_MS
    gap = [(3000, "focus_out"), (3031, "focus_in")]
    check(loss_presses(gap, [1015, 4015]) == (0, 0), "focus loss: an unplanned loss with no press near it has none")
    check(loss_presses(gap, [3010]) == (1, 1), "focus loss: a press inside an unplanned loss is counted")
    check(loss_presses(gap, [3000 - m, 3031 + m]) == (1, 2) and loss_presses(gap, [3000 - m - 1, 3031 + m + 1]) == (0, 0),
          "focus loss: the margin reaches exactly FOCUS_MARGIN_MS before the focus_out and after the focus_in")
    check(loss_presses([(3000, "focus_out")], [9000]) == (1, 1),
          "focus loss: a loss that is not regained counts the presses up to the end of the log")
    check(loss_presses([], [1300], s15_events=[(1200, "focus_out"), (1500, "focus_in")]) == (0, 0),
          "focus loss: presses during a planned S15 switch are not counted")
    check(loss_presses([(2900, "focus_out"), (2950, "focus_in"), (3100, "focus_out"), (3150, "focus_in")], [3020]) == (2, 1),
          "focus loss: a press within the margins of two losses counts for both losses and once among the presses")
    check(evaluate(*_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], _kd(4015)))["params"]["focus_margin_ms"] == m,
          "focus loss: the margin is recorded in the parameters")
    check(evaluate(*_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], _kd(4015)))["params"]["obs_match_before_ms"]
          == 20 and OBS_MATCH_BEFORE_MS == 20, "focus loss: the match tolerance before is recorded in the parameters")
    pr = evaluate(*_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], _kd(4015)))
    check(pr["params"]["obs_match_after_ms"] == 300 and OBS_MATCH_AFTER_MS == 300
          and "obs_send_delay_ms" not in pr["params"], "focus loss: the match window after is recorded in the parameters")

    # focus losses: observation rows set against the keys sent. Trials x (S15) and y (S1) always send one key each,
    # at 1010 and 4010; `extra` adds trials (id, begin, end, keys); every observation row is given explicitly.
    def unexplained(events, obs, extra=(), marks=(), open_end=False):
        """(n_loss_spans of the losses given, spans with unexplained rows, unexplained rows). The reference log
        holds a focus_in at FOCUS0 and a last key_down far after every row, so that the span of the start and the
        span after the last row (see _focus_losses) hold no row; they are left out of the span count, the span
        after the last row being merged into the losses when open_end (a loss that is not regained)."""
        trials = [("x", 0, "S15", 1000, 2000, ["a"]), ("y", 0, "S1", 4000, 4500, ["a"])]
        trials += [(i, 0, "S1", b, e, k) for i, b, e, k in extra]
        got = evaluate(*_mini([(0, 0)], trials, sorted(list(events) + [(1_000_000, "key_down")]), obs,
                              marks))["reference_focus_loss"]
        return (got["n_loss_spans"] - (1 if open_end else 2), got["n_loss_spans_with_unexplained_presses"],
                got["n_presses_unexplained_in_losses"])
    z2 = [("z", 3000, 3400, ["a", "b"])]
    loss = [(3005, "focus_out"), (3100, "focus_in")]
    check(unexplained(loss, [3010, 3040], z2) == (1, 0, 0),
          "unexplained: rows equal to the keys sent in a loss while typing are explained")
    check(unexplained(loss, [3010, 3040, 3050], z2) == (1, 1, 1),
          "unexplained: a row more than the keys sent is not explained")
    check(unexplained(loss, [3010], z2) == (1, 0, 0),
          "unexplained: rows fewer than the keys sent never count below 0")
    check(unexplained(loss, [], z2) == (1, 0, 0) and unexplained([], [3010], z2) == (0, 0, 0),
          "unexplained: no row, or no loss, nothing to explain")
    check(unexplained(loss, [2515], [("w", 2445, 2490, ["a"])]) == (1, 0, 0),
          "unexplained: a key sent just before the span whose row falls inside it is set against that row")
    a0 = 3005 - m
    b0 = 3100 + m
    # a press sent 50 ms before the span starts, its row 2 ms later (also before it), and one unsent row inside
    check(unexplained(loss, [a0 - 48, a0 + 10], [("w", a0 - 60, a0 - 40, ["a"])]) == (1, 1, 1)
          and unexplained(loss, [a0 - 48], [("w", a0 - 60, a0 - 40, ["a"])]) == (1, 0, 0),
          "unexplained: a press sent before the span with its row before it does not hide a row at the span's start")
    # a press sent 30 ms before the span ends, its row 5 ms after the end, and one unsent row inside
    check(unexplained(loss, [3300, b0 + 5], [("w", b0 - 40, b0 - 20, ["a"])]) == (1, 1, 1)
          and unexplained(loss, [b0 + 5], [("w", b0 - 40, b0 - 20, ["a"])]) == (1, 0, 0),
          "unexplained: a press sent before the span's end with its row after it does not hide a row inside")
    q = [("q", 3000, 3400, ["a"])]
    check(unexplained(loss, [3010 + OBS_MATCH_AFTER_MS], q) == (1, 0, 0)
          and unexplained(loss, [3010 + OBS_MATCH_AFTER_MS + 1], q) == (1, 1, 1),
          "unexplained: a row exactly OBS_MATCH_AFTER_MS after its press is matched, one millisecond later is not")
    check(unexplained(loss, [3010 - OBS_MATCH_BEFORE_MS], q) == (1, 0, 0)
          and unexplained(loss, [3010 - OBS_MATCH_BEFORE_MS - 1], q) == (1, 1, 1),
          "unexplained: a row exactly OBS_MATCH_BEFORE_MS before its press is matched, one millisecond earlier is not")
    check(unexplained(loss, [3020, 3100], q) == (1, 1, 1),
          "unexplained: an unsent row between a press and that press's own later row makes exactly one")
    check(unexplained(loss, [3110], q) == (1, 0, 0),
          "unexplained: a press whose row is missing hides a person's row 100 ms after it (blind spot)")
    check(unexplained(loss, [4012, 4300, 2000]) == (1, 0, 0),
          "unexplained: rows of a press sent outside every span, or unmatched outside every span, are never counted")
    check(unexplained(loss, [3010, 3011], q) == (1, 1, 1),
          "unexplained: a key sent takes one row only, a second row beside it is unexplained")
    check(unexplained(loss, [3010, 3040, 3070], [("m", 3000, 3400, ["ime_on", "focus_away", "a"])]) == (1, 1, 2)
          and unexplained(loss, [3070], [("m", 3000, 3400, ["ime_on", "focus_away", "a"])]) == (1, 0, 0)
          and unexplained(loss, [3070], [("m", 3000, 3400, ["ime_off", "mouse_click", "a"])]) == (1, 0, 0),
          "unexplained: mode keys and pseudo-keys are not keys the observation is expected to record")
    check(unexplained(loss, [3020], marks=[3020]) == (1, 0, 0) and unexplained(loss, [3020]) == (1, 1, 1),
          "unexplained: a marker press counts as a key sent")
    check(unexplained(loss, [3010], [("u", 3000, 3040, ["a"])], marks=[3020]) == (1, 0, 0)
          and unexplained(loss, [3010, 3020, 3030], [("u", 3000, 3040, ["a"])], marks=[3020]) == (1, 1, 1),
          "unexplained: a marker press and a key of a trial are counted together")
    two = [(2900, "focus_out"), (2950, "focus_in"), (3100, "focus_out"), (3150, "focus_in")]
    check(unexplained(two, [3010, 3040], z2) == (1, 0, 0) and unexplained(two, [3010, 3040, 3050], z2) == (1, 1, 1),
          "unexplained: two losses within each other's margins make one span, with rows and keys counted once")
    check(unexplained([(3000, "focus_out"), (3100, "focus_in"), (4100, "focus_out"), (4200, "focus_in")], [])[0] == 1
          and unexplained([(3000, "focus_out"), (3100, "focus_in"), (4101, "focus_out"), (4200, "focus_in")], [])[0] == 2,
          "unexplained: spans that touch are merged and spans that are one millisecond apart are not")
    check(unexplained([(3005, "focus_out")], [4010, 9000], open_end=True) == (1, 1, 1)
          and unexplained([(3005, "focus_out")], [2000], open_end=True) == (1, 0, 0),
          "unexplained: a loss that is not regained runs to the end of the log")
    sw = [(1200, "focus_out"), (1500, "focus_in")]
    check(unexplained(sw, [1013, 1300]) == (1, 1, 1) and unexplained(sw, [1013]) == (1, 0, 0),
          "unexplained: an unsent row inside a planned switch of S15 is unexplained")
    check(unexplained(sw, [1103, 1603], [("p1", 1090, 1100, ["a"]), ("p2", 1590, 1600, ["a"])]) == (1, 0, 0),
          "unexplained: the executor's own presses around a planned switch of S15 give 0")
    check(unexplained([(3005, "focus_out"), (3100, "focus_in")] + sw, [1013, 1300, 3010]) == (2, 2, 2),
          "unexplained: a planned switch beside an unplanned loss is a span of its own")
    check(unexplained(loss, [3010]) == (1, 1, 1) and unexplained(loss, [3010], [("z", 3000, 3040, ["a"])]) == (1, 0, 0),
          "unexplained: a row with no key sent near it is unexplained")

    # focus losses: the order of the focus rows in the log (equal times are taken in the order of the log).
    fo_, fi_ = "focus_out", "focus_in"
    check(_loss_intervals([(100, 1, fo_), (200, 2, fi_), (200, 3, fo_)]) == [(100, 200), (200, None)],
          "focus order: in then out at an equal time closes a loss and opens another that is not regained")
    check(_loss_intervals([(100, 1, fo_), (200, 2, fo_), (200, 3, fi_)]) == [(100, 200)]
          and _loss_intervals([(200, 3, fo_), (200, 2, fi_)]) == [(200, None)]
          and _loss_intervals([(200, 2, fo_), (200, 3, fi_)]) == [(200, 200)],
          "focus order: out then in at an equal time gives a loss of length zero; the line number decides, not the list")
    ordr = evaluate(*_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])],
                           [(100, fo_), (200, fi_), (200, fo_), (4015, "key_down")], [4012]))["reference_focus_loss"]
    check(ordr["n_loss_intervals"] == 2 and ordr["n_focus_out"] == 2 and ordr["n_focus_in"] == 2,
          "focus order: the log order is kept through the evaluation (a loss that starts at the time of a regain)")

    # focus losses: before the first focus_in and after the last row of the reference log (spans added to the losses,
    # outside every count of losses). Trial y sends one key at 4010; its row is at 4012.
    def edge(gt, obs, marks=()):
        got = evaluate(*_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], gt, obs, marks, focus0=False)
                       )["reference_focus_loss"]
        return (got["n_loss_spans"], got["n_loss_spans_with_unexplained_presses"],
                got["n_presses_unexplained_in_losses"], got["n_loss_intervals"])
    tail_row = [(1_000_000, "key_down")]
    check(edge([(1000, fi_), (4015, "key_down")] + tail_row, [500, 4012]) == (2, 1, 1, 0),
          "edge: an unsent row before the first focus_in is counted (the span from the start, without a loss counted)")
    check(edge([(-2000, fi_), (4015, "key_down")] + tail_row, [500, 4012]) == (2, 0, 0, 0),
          "edge: a row after the first focus_in plus the margin is not in the span from the start")
    check(edge([(4015, "key_down")] + tail_row, [4012, 700]) == (1, 1, 1, 0)
          and edge([(4015, "key_down")] + tail_row, [4012]) == (1, 0, 0, 0),
          "edge: a log with no focus row is one loss: an unsent row aborts, the executor's own row does not")
    check(edge([(5000, fi_), (6000, "key_down")], [4012]) == (1, 0, 0, 0)
          and edge([(5000, fi_), (6000, "key_down")], [4012, 4300]) == (1, 1, 1, 0),
          "edge: the executor's own matched rows before the first focus_in do not abort, an unsent one does")
    check(edge([(-5000, fi_), (4015, "key_down")], [4012, 4600]) == (2, 1, 1, 0)
          and edge([(-5000, fi_), (4015, "key_down")], [4012]) == (2, 0, 0, 0),
          "edge: an unsent row after the last row of the reference log aborts, a matched one does not")
    check(edge([(-5000, fi_), (100, "key_down")], [4012]) == (2, 0, 0, 0),
          "edge: a reference log that ends early but whose rows are all matched does not abort")
    check(edge([(100, fo_), (200, "key_down")] + tail_row, [4012, 150]) == (1, 1, 1, 1),
          "edge: a log whose first focus row is a focus_out adds no span at the start of the logs")
    check(edge([(-5000, fi_)], [4012])[:3] == (1, 0, 0) and edge([(-5000, fi_)], [4012, 4300])[:3] == (1, 1, 1),
          "edge: with a focus_in as the only reference row, everything after it falls in the span after the last row")
    check(edge([], [4012])[:3] == (1, 0, 0) and edge([], [4012, 100])[:3] == (1, 1, 1) and edge([], [])[:3] == (1, 0, 0),
          "edge: an empty reference log is one loss over the whole log")
    check(unexplained([(2000, fo_), (2100, fi_), (2200, fo_)], [2300], open_end=True) == (1, 1, 1)
          and unexplained([(2000, fo_), (2100, fi_), (2200, fo_)], [], open_end=True) == (1, 0, 0),
          "unexplained: a closed span merged with a later span that is not regained becomes one open span")
    check(unexplained([(3000, fo_)], [3500, 4500], open_end=True) == (1, 1, 2),
          "unexplained: the span after the last row merges into an open loss and nothing is counted twice")

    # focus losses: the shortage carried past the end of a span (security review: keys 100 ms apart, every row 90 ms
    # after its key, one press of a person inside the loss). Keys at 3500, 3600, ... one trial each.
    def chain(key_times, obs, events=((3000, fo_), (3100, fi_))):
        trials = [("k%d" % i, 0, "S1", t - 10, t + 40, ["a"]) for i, t in enumerate(key_times)]
        got = evaluate(*_mini([(0, 0)], trials, sorted(list(events) + [(1_000_000, "key_down")]), obs)
                       )["reference_focus_loss"]
        return (got["n_loss_spans"] - 2, got["n_loss_spans_with_unexplained_presses"],
                got["n_presses_unexplained_in_losses"])
    keys9 = [3500 + 100 * i for i in range(9)]
    rows9 = [t + 90 for t in keys9]
    check(chain(keys9, rows9) == (1, 0, 0), "chain: every row 90 ms after its key and no person is present gives 0")
    check(chain(keys9, sorted(rows9 + [3520])) == (1, 1, 1),
          "chain: a press of a person inside the loss takes a key and the shortage lands past the end of the span: counted")
    keys7 = [3500, 3600, 3700, 4100, 4200]
    rows7 = [t + 90 for t in keys7]
    check(chain(keys7, sorted(rows7 + [3520])) == (1, 1, 1) and chain(keys7, sorted(rows7 + [3520, 4300])) == (1, 1, 1),
          "chain: a gap of more than OBS_MATCH_BEFORE_MS + OBS_MATCH_AFTER_MS stops the extension (a row beyond is not counted)")
    check(chain([3500, 3600, 3700, 3900], sorted([3590, 3690, 3790, 3990, 3520, 4150])) == (1, 1, 2),
          "chain: within the gap the extension goes on, up to the last key plus OBS_MATCH_AFTER_MS")
    two_spans = [(3000, fo_), (3100, fi_), (4101, fo_), (4150, fi_)]
    check(unexplained(two_spans, [3700]) == (2, 2, 1),
          "unexplained: a row in the windows of two spans counts for both spans and once among the rows")
    check(_extended_end(3600, [3500, 4000]) == 3920 and _extended_end(3600, [3700]) == 4000
          and _extended_end(3600, [3700, 4020]) == 4320 and _extended_end(3600, [3700, 4021]) == 4000
          and _extended_end(3600, []) == 3920,
          "chain: the window end (hand computed, the gap condition is inclusive)")

    # marker rows of the oracle: block_id and seq are checked against the plan.
    seg = _seg_fixture()

    def first_marker(edit):
        def go(ora):
            edit(next(r for r in ora if r.get("kind") == "marker"), ora)
        return go
    def del_field(name):
        return first_marker(lambda r, ora: r.pop(name))
    def set_field(name, value):
        return first_marker(lambda r, ora: r.__setitem__(name, value))
    def dup_marker(r, ora):
        ora.append(dict(r))
    check(refused(_oracle_with(seg, del_field("block_id")))
          and refused(_oracle_with(seg, set_field("block_id", "zzmarkerid")), "zzmarkerid")
          and refused(_oracle_with(seg, set_field("block_id", True)))
          and refused(_oracle_with(seg, set_field("block_id", 7))),
          "a marker row whose block_id is missing, a string, a boolean or not in the plan is refused, value not echoed")
    check(refused(_oracle_with(seg, del_field("seq"))) and refused(_oracle_with(seg, set_field("seq", "zzseq")), "zzseq")
          and refused(_oracle_with(seg, set_field("seq", True))) and refused(_oracle_with(seg, set_field("seq", 1.0))),
          "a marker row whose seq is missing, a string, a boolean or a float is refused, value not echoed")
    check(refused(_oracle_with(seg, set_field("seq", 4))) and refused(_oracle_with(seg, set_field("seq", -1))),
          "a marker row whose seq is outside 0 .. length - 1 of its block's marker array is refused")
    check(refused(_oracle_with(seg, first_marker(dup_marker))),
          "a marker row whose (block_id, seq) appears twice is refused")
    empty_marker = _mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], [(4015, "key_down")], [4012])
    check(not refused(empty_marker)
          and refused(_oracle_with(empty_marker, lambda ora: ora.append(
              {"kind": "marker", "block_id": 0, "seq": 0, "t_wall_ms": W0 + 10, "t_mono_ns": 1}))),
          "a block with no marker array can have no marker row")
    check(not refused(_fixture()) and not refused(seg)
          and not refused(_mini([(0, 0)], [("y", 0, "S1", 4000, 4500, ["a"])], [(4015, "key_down")], [4012], [3000, 3010])),
          "the marker rows of the fixtures (as the emitter and oracle_from_plan write them) pass")

    # block ids must be integers (a string and a boolean are refused, no value is echoed).
    def with_block_id(value):
        def edit(rows):
            rows[1]["block_id"] = value
        return _seg_fixture(plan_edit=edit)
    check(refused(with_block_id("zzblockid"), "zzblockid"), "a plan block_id that is a string is refused, value not echoed")
    check(refused(with_block_id(True)), "a plan block_id that is a boolean is refused")

    def str_begin(ora):
        next(r for r in ora if r.get("kind") == "block_begin")["block_id"] = "0"

    def bool_begin(ora):
        next(r for r in ora if r.get("kind") == "block_begin")["block_id"] = False
    check(refused(_oracle_with(_seg_fixture(), str_begin)) and refused(_oracle_with(_seg_fixture(), bool_begin)),
          "a block_begin whose block_id is a string or a boolean is refused")

    # command line.
    me = os.path.abspath(__file__)
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name, data in zip(("plan", "oracle", "obs", "gt"), fx):
            paths[name] = os.path.join(tmp, name + ".jsonl")
            with open(paths[name], "wb") as f:
                f.write(data)
        outp = os.path.join(tmp, "out.json")
        base = [sys.executable, me, "--plan", paths["plan"], "--oracle", paths["oracle"], "--obs", paths["obs"],
                "--gt", paths["gt"], "--out", outp]
        p = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with open(outp, "rb") as f:
            written = f.read()
        check(p.returncode == 0 and written == render(out), "CLI: exit 0 and the file equals render(evaluate)")
        p = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "CLI: an existing --out is not overwritten (exit 2)")
        with open(outp, "rb") as f:
            check(f.read() == written, "CLI: the existing --out is left untouched")
        deep = b"[" * 200000 + b"\n"
        for i, name in enumerate(("plan", "oracle", "obs", "gt")):
            alt = dict(paths)
            alt[name] = os.path.join(tmp, "deep_" + name + ".jsonl")
            with open(alt[name], "wb") as f:
                f.write(fx[("plan", "oracle", "obs", "gt").index(name)].split(b"\n")[0] + b"\n" + deep)
            args_ = [sys.executable, me, "--plan", alt["plan"], "--oracle", alt["oracle"], "--obs", alt["obs"],
                     "--gt", alt["gt"], "--out", os.path.join(tmp, "deep%d.json" % i)]
            p = subprocess.run(args_, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            check(p.returncode == 2 and b"Traceback" not in p.stdout
                  and not os.path.exists(os.path.join(tmp, "deep%d.json" % i)),
                  "CLI: a deeply nested JSON line in the %s file exits 2 without a traceback" % name)
        def boom(*_a, **_k):
            raise ValueError("zzsecretvalue")
        real_evaluate = globals()["evaluate"]
        globals()["evaluate"] = boom
        errbuf = io.StringIO()
        try:
            with contextlib.redirect_stderr(errbuf):
                rc_boom = main(["--plan", paths["plan"], "--oracle", paths["oracle"], "--obs", paths["obs"],
                                "--gt", paths["gt"], "--out", os.path.join(tmp, "boom.json")])
        finally:
            globals()["evaluate"] = real_evaluate
        check(rc_boom == 2 and "ValueError" in errbuf.getvalue() and "zzsecretvalue" not in errbuf.getvalue()
              and not os.path.exists(os.path.join(tmp, "boom.json")),
              "CLI: any other unexpected error exits 2 and prints only its type name")
        with open(paths["plan"], "ab") as f:
            f.write(b'{"kind":"block","block_id":1}\n')
        p = subprocess.run(base[:-1] + [outp + "2"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2 and not os.path.exists(outp + "2"), "CLI: a plan whose hash differs exits 2, nothing written")
        p = subprocess.run([sys.executable, me, "--plan", paths["plan"]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "CLI: a missing argument exits 2")

    if fails:
        print("  ---- delivery selftest: %d failed ----" % len(fails))
        return 1
    print("  ---- delivery selftest: all green ----")
    return 0


def _oracle_with(fx, edit):
    """The fixture with its oracle edited (the header hash is kept, so only the edit can cause a refusal)."""
    rows = [json.loads(line) for line in fx[1].decode("utf-8").split("\n") if line]
    edit(rows)
    return (fx[0], _jl(rows), fx[2], fx[3])
# --- selftest end ---


def _read_file(path, what):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise InputError("cannot open the %s file (%s)" % (what, e.__class__.__name__))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Descriptive report of how faithfully scripted key presses were delivered")
    ap.add_argument("--selftest", action="store_true", help="run the positive and negative controls")
    ap.add_argument("--plan", help="the plan (plan/1)")
    ap.add_argument("--oracle", help="the runner's record of what was sent (oracle/1)")
    ap.add_argument("--obs", help="the observation log (obs/1)")
    ap.add_argument("--gt", help="the reference log (gt/1)")
    ap.add_argument("--out", help="destination for the result JSON (never overwritten)")
    ap.add_argument("--pre-ms", type=int, default=20, help="margin before a trial's interval (default 20)")
    ap.add_argument("--tail-ms", type=int, default=300, help="margin after a trial's interval (default 300)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if args.selftest:
        return _selftest()
    if not (args.plan and args.oracle and args.obs and args.gt and args.out):
        print("delivery: --plan / --oracle / --obs / --gt / --out are all required", file=sys.stderr)
        return 2
    if args.pre_ms < 0 or args.tail_ms < 0:
        print("delivery: --pre-ms / --tail-ms must be 0 or greater", file=sys.stderr)
        return 2
    if os.path.exists(args.out):
        print("delivery: the output file already exists and is not overwritten", file=sys.stderr)
        return 2
    try:
        data = [_read_file(getattr(args, k), k) for k in ("plan", "oracle", "obs", "gt")]
        body = render(evaluate(data[0], data[1], data[2], data[3], args.pre_ms, args.tail_ms))
    except InputError as e:
        print("delivery: %s" % e, file=sys.stderr)
        return 2
    except RecursionError:
        print("delivery: the input is nested too deeply (RecursionError)", file=sys.stderr)
        return 2
    except Exception as e:
        print("delivery: unexpected error (%s)" % e.__class__.__name__, file=sys.stderr)
        return 2
    try:
        with open(args.out, "xb") as f:
            f.write(body)
    except OSError as e:
        print("delivery: cannot write the output file (%s)" % e.__class__.__name__, file=sys.stderr)
        return 2
    print("delivery: wrote the report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
