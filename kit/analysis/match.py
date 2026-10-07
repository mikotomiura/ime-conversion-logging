#!/usr/bin/env python3
# kit: selftest
"""Matching and detection metrics: pair the ground-truth composition sessions (gt_sessions.py's
output) against the observation-side ime_shadow rows (obs/1) by the overlap of their time
intervals, and report detection precision, recall, F1, split rate, and merge rate, each with a
Wilson 95% confidence interval. Uses only the Python standard library (statistics are imported from
stats.py and are not reimplemented here).

  match.py --sessions S.json --definition all|macos_def|windows_def --obs OBS --plan PLAN --oracle ORACLE
           [--config CFG.json] [--prediction P.json [--scope all_trials|under_limit|over_limit]] --out OUT.json
  match.py --selftest      fast default (a few seconds)

Interval geometry and matching (see the design document):
  - A ground-truth interval is [start_wall_ms, end_wall_ms] (wall-clock ms, inclusive of both ends).
    An observation interval is [end - dwell_ms, end], where end = t + obs_end_offset_ms. The
    ime_shadow row's t is the time the entry was drained, not necessarily the moment the composition
    ended, so an offset may be needed; the default obs_end_offset_ms = 0 gives [t - dwell_ms, t]. The
    offset translates the whole interval (dwell_ms remains the length); to cancel out an average
    end-of-session lag, use a negative value (for a lag uniformly distributed over 0-100 ms, -50).
  - IoU = overlap / union. A 1:1 pair requires IoU >= iou_min (default 0.5), assigned greedily in
    descending order of IoU. Ties are broken by ground-truth start time, then observation start time,
    then content, in ascending order, so the result does not depend on input order and the output is
    itself sorted by content. A zero-length interval pairs only with another zero-length interval at
    the same point.
  - A split occurs when one ground-truth session that did not receive a 1:1 pair overlaps two or more
    unused observations whose union (measured as a set of intervals) reaches IoU >= iou_min against
    it. A merge is the mirror case (one observation, two or more ground-truth sessions). Split and
    merge candidates are resolved one at a time, within each connected component of unused items
    joined by overlap, always taking the candidate with the highest IoU first (each resolution
    removes the items it used and the remaining candidates are recomputed). Whatever ground truth
    remains is a miss; whatever observation remains is a false positive.
  This definition has a fixed, documented property that is not treated as a defect: a fragment
  counts toward a split only when no individual fragment on its own reaches IoU >= iou_min. Splitting
  a session into two adjacent fragments therefore makes the larger fragment (>= 50%) a 1:1 pair and
  leaves the other as a false positive (checked by the "definitional properties" selftest case). The
  same holds for merges: when one side occupies >= 50% of the merged interval, that ground-truth
  session becomes a 1:1 pair and the other side becomes a miss. Likewise, a timing offset makes
  shorter sessions progressively less likely to pair (a session of length L pairs only while the
  offset stays at or below L/3). Because these counts move with session length and offset, read them
  per stratum in any report.

Recount for split_rate / merge_rate: the primary pairing above (used for "pairs", precision, recall
and F1) is never changed. Separately, detection_metrics() takes a second pass over the leftover
false_pos / miss items and reclassifies, for the purpose of split_rate / merge_rate only:
  (a) a false_pos row whose own length has a fraction >= theta covered by an already-paired ground-truth
      session's interval is counted as extra evidence that session was split;
  (b) a miss session whose own length has a fraction >= theta covered by an already-paired observation's
      interval is counted as extra evidence that observation was a merge.
theta defaults to 0.5 and is configurable (CLI config key "theta", 0 < theta <= 1). Each leftover item is
attributed to at most one paired counterpart (the one with the largest covered fraction; ties broken by the
counterpart's start time then content). This recount changes only split_rate / merge_rate and the new
n_split_gt_recount / n_merge_obs_recount / n_fp_as_split / n_miss_as_merge fields below; it never moves an
item between match()'s pairs / split / merge / miss / false_pos buckets, so n_gt, n_obs, precision, recall
and F1 are unaffected. See recount_split_merge() for the underlying computation.

Shape of the return value (downstream analyses depend on this shape):
  match(...) = {"pairs": [{"gt", "obs", "iou"}], "split": [{"gt", "obs": [rows...]}], "merge": [{"obs", "gt": [sessions...]}],
                "miss": [sessions...], "false_pos": [rows...]}
  detection_metrics(result, strata, theta, obs_end_offset_ms) = per stratum, [{"stratum", "n_gt", "n_obs", "n_pairs",
    "n_split_gt", "n_split_obs", "n_merge_obs", "n_merge_gt", "n_miss", "n_false_pos", "n_split_gt_recount",
    "n_merge_obs_recount", "n_fp_as_split", "n_miss_as_merge", "precision", "recall", "f1", "split_rate",
    "merge_rate", "miss_rate", "false_pos_rate"}]. Each metric is {"x", "n", "value", "lo", "hi"} (Wilson 95%;
    n = 0 gives both value and interval as None): precision = pairs / observations  recall = pairs / ground truth
      F1 = 2 x pairs / (ground truth + observations) (the fraction of items that ended up in a pair; the
      Wilson approximation is inexact here because one pair counts as two "successes" (one on the
      ground-truth side, one on the observation side) that are not independent, so its interval should
      be read only as a rough guide -- see the note below)
      split rate = (ground truth counted as split + n_split_gt_recount) / ground truth
      merge rate = (observations counted as merge + n_merge_obs_recount) / observations
      miss rate = misses / ground truth  false-positive rate = false positives / observations.
      n_gt = pairs + split ground truth + merge ground truth + misses, n_obs = pairs + split
      observations + merge observations + false positives (this identity partitions gt / obs into
      disjoint buckets and is unaffected by the theta recount above; n_fp_as_split and n_miss_as_merge
      are informational breakdowns of n_false_pos / n_miss, not separate buckets).

CLI (--sessions is gt_sessions.py's output; --definition selects one of its definitions):
  - A stratum is (cell, scenario, speed). scenario and speed come from the plan's trials; cell comes
    from the configuration's "cell" (default: the plan's os). An item that cannot be attributed to a
    trial (for example a false positive outside any trial) falls into (cell, null, null). A merge
    counts each of its ground-truth sessions in its own stratum (merge_obs is counted only in the
    stratum of the first ground-truth session; merge_gt is counted once per ground-truth session, in
    each of their own strata).
  - Configuration CFG (JSON): {"iou_min": 0.5, "obs_end_offset_ms": 0, "theta": 0.5,
    "clock_offsets_ms": {"<block_id>": ms}, "cell": "<label>", "drop_start_while_open": false}.
    Assumption: the ground-truth session timestamps (sessions.json) come from the same wall clock as
    the oracle log (gt_sessions.py builds both from one t_wall_ms series); clock_offsets_ms corrects
    only for the gap between that shared clock and the observation side's clock.
    clock_offsets_ms is "observation-side clock minus ground-truth-side clock" (the same sign
    convention as kit/pilot/clock_sync.py's obs minus gt). An observation row's "t" is never
    rewritten; the corrected time is always attached in a separate field, "t_gt_clock" (a block
    missing from clock_offsets_ms gets a zero-offset t_gt_clock, and its block_id is listed in the
    output's config.uncorrected_blocks). Matching uses "t_gt_clock" whenever it is present (see
    obs_interval()). Which block an observation belongs to is decided by which block's oracle
    block_begin..block_end window the corrected time is closest to (assuming the correction is
    comfortably smaller than the gap between blocks).
  - Exclusion operates at the level of a trial, never an interval: a session is dropped, and any
    observation assigned to the same trial (by greatest overlap with the oracle's
    trial_begin..trial_end) is also dropped, whenever the session has a non-empty excluded_by, never
    closed (open_at_end), or (when drop_start_while_open is true) has n_start_while_open > 0, or
    belongs to a trial listed in one of the excluded entries' trial_ids. The output retains the count
    for each reason (every exclusion is reported). Note: a session with trial_id == null (for example
    one dropped only via the open_at_end reason) can never appear in excluded[].trial_ids, so
    observations that fall in that time window are not excluded by trial membership alone; only the
    ground-truth session itself is dropped, and any nearby observation is still matched normally (it
    may end up false_pos). n_start_while_open (a diagnostic field) is not used to exclude anything by
    default; kept-session counts with n_start_while_open > 0 are reported per stratum in the output
    under "diagnostics.start_while_open".
  - Prediction (--prediction, spec_predict.py's output for the same oracle and plan). The prediction
    marks, from what was sent and the observer's specified rules alone, the trials whose presses entered
    a session that the observer is specified to cut off at 20 seconds (long; the session may have started
    in the trial or have been carried over from an earlier one). --scope then selects the trials that enter
    the matching, after the exclusions above and at the level of a trial, as exclusion does:
    all_trials (every trial; the default and the only scope without a prediction), under_limit (the
    trials that are not long) or over_limit (the long trials only: their sessions, and the rows
    assigned to them, including the fragments that follow a cut-off). The items of the other trials
    are set aside and counted (scope.set_aside); scope.long_trials lists the long trials and
    scope.layer_none_trials the trials of the layer none (below), a diagnostic view for level 2, and
    scope.uncontaminated_trials the trials of the prediction that are in neither the carried_out nor
    the received_carry layer (the closing_key_in_reading trials are kept: their disagreement is the
    trial's own, not a contamination from another trial), the view for level 2 to reweight the
    scenarios on (both lists are null without a prediction). A
    ground-truth session or row that belongs to no trial is under the limit.
    With a prediction the output also holds diagnostics.prediction_layers: the detection metrics
    restricted to the trials of each layer (pooled and per speed, counted from the same matching):
    carried_out (the trial started a session that ends in a later trial), received_carry (a press of the
    trial entered a session started by an earlier trial), closing_key_in_reading (the trial sends the
    minus, comma or period key, which closes a session under the observer's rules: a long-vowel mark,
    comma or full stop in Romaji input, and ho, ne or ru in kana input) and none (a trial in none of the
    three). A trial can be in several layers.
  - The output never includes a path, and identical input always yields identical output bytes
    (sort_keys, LF, UTF-8, no BOM). The input files' SHA-256 hashes are retained.

Exit status: 0 on success; 2 if an input cannot be read or usage is incorrect.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from bisect import bisect_left, bisect_right

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import gt_sessions  # noqa: E402  reuses its oracle parser and InputError
import stats  # noqa: E402  the Wilson interval is provided by stats.wilson and is not reimplemented here

InputError = gt_sessions.InputError

# Deviation D10. In the frozen code a merge found by the theta recount (n_merge_obs_recount) is counted in the
# stratum of the missed ground-truth session, while n_obs counts that observation row in the stratum of the
# ground-truth session it is paired with; where the two strata differ, the numerator of merge_rate can exceed its
# denominator. Without the environment variable below nothing changes, except that such a rate is reported
# without a value or interval (with NUMERATOR_ABOVE_DENOMINATOR as its note) instead of stopping the analysis. With
# IME_MATCH_MERGE_RECOUNT=obs the recounted merge is counted in the stratum in which n_obs counts its observation
# row (the stratum of the paired ground-truth session), as the split recount already is on the ground-truth side,
# so that the numerator never exceeds the denominator; n_miss_as_merge stays with the missed session.
MERGE_RECOUNT_ENV = "IME_MATCH_MERGE_RECOUNT"
MERGE_RECOUNT_MODES = ("gt", "obs")
NUMERATOR_ABOVE_DENOMINATOR = "no value: the numerator exceeds the denominator (deviation D10)"


def merge_recount_mode():
    """The stratum of a recounted merge: "gt" (the frozen rule, the default) or "obs" (deviation D10)."""
    mode = os.environ.get(MERGE_RECOUNT_ENV, "gt")
    if mode not in MERGE_RECOUNT_MODES:
        raise InputError("%s must be one of %s" % (MERGE_RECOUNT_ENV, ", ".join(MERGE_RECOUNT_MODES)))
    return mode
SCENARIO_RE = re.compile(r"^S([1-9]|1[0-5])$")
SPEEDS = ("slow", "mid", "fast")
CELL_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
DEFINITIONS = ("all", "macos_def", "windows_def")
SCOPES = ("all_trials", "under_limit", "over_limit")
PREDICTION_LAYERS = ("carried_out", "received_carry", "closing_key_in_reading", "none")
PREDICTION_FORMAT = "spec_predict/2"


# ---------------------------------------------------------------- interval geometry

def _is_int(v):
    return type(v) is int


def _check_iou_min(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 < v <= 1.0):
        raise ValueError("iou_min must be a number greater than 0 and at most 1")


def gt_interval(s, where="gt"):
    """The interval (lo, hi) of a ground-truth session. Raises ValueError if either bound is not an
    integer, the session has no end (open_at_end), or the bounds are reversed."""
    if not isinstance(s, dict):
        raise ValueError("%s: a session must be an object" % where)
    lo, hi = s.get("start_wall_ms"), s.get("end_wall_ms")
    if not (_is_int(lo) and _is_int(hi)):
        raise ValueError("%s: start_wall_ms / end_wall_ms is not an integer (the caller must filter out an unclosed open_at_end session)" % where)
    if hi < lo:
        raise ValueError("%s: end_wall_ms precedes start_wall_ms" % where)
    return (lo, hi)


def obs_interval(row, obs_end_offset_ms=0, where="obs"):
    """Observation interval (lo, hi) = [end - dwell_ms, end], end = t_ref + obs_end_offset_ms (the offset
    translates the whole interval; dwell_ms is the length). t_ref is row["t_gt_clock"] when present (the
    observation clock translated into the ground-truth clock; the CLI always attaches it) and row["t"]
    otherwise. Note: "t" itself is always the raw, uncorrected value; any clock correction only ever
    appears in t_gt_clock."""
    if not isinstance(row, dict):
        raise ValueError("%s: an observation row must be an object" % where)
    t = row.get("t_gt_clock")
    if t is None:
        t = row.get("t")
    d = row.get("dwell_ms")
    if not (_is_int(t) and _is_int(d)) or d < 0:
        raise ValueError("%s: t / dwell_ms is not an integer (dwell_ms must be non-negative)" % where)
    end = t + obs_end_offset_ms
    return (end - d, end)


def _check_theta(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 < v <= 1.0):
        raise ValueError("theta must be a number greater than 0 and at most 1")


def _covered_frac(inner, outer):
    """Fraction of interval `inner`'s length that lies inside interval `outer`. A zero-length inner interval
    is fully covered (1.0) when its point lies within outer, else not covered (0.0)."""
    lo, hi = inner
    if lo == hi:
        return 1.0 if outer[0] <= lo <= outer[1] else 0.0
    inter = min(hi, outer[1]) - max(lo, outer[0])
    return max(0.0, inter) / (hi - lo)


def _iou(a, b):
    """The IoU (overlap / union) of two closed intervals a and b. 0 when they do not overlap; 1 when
    both have zero length at the same point."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter < 0:
        return 0.0
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    if union == 0:
        return 1.0
    return inter / union


def _overlaps(a, b):
    """The notion of "overlap" used for both 1:1 candidates and split / merge candidates: a
    positive-length overlap, or two zero-length intervals at the same point."""
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return inter > 0 or (a[0] == a[1] == b[0] == b[1])


def _segments(ivs):
    """Turn a list of intervals into a list of disjoint intervals (overlapping or touching intervals
    are merged into one)."""
    out = []
    for lo, hi in sorted(ivs):
        if out and lo <= out[-1][1]:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def _group_iou(segs, iv):
    """The IoU between a set of disjoint intervals segs and a single interval iv, measured by set
    measure (gaps between the segments are not counted)."""
    a = sum(hi - lo for lo, hi in segs)
    b = iv[1] - iv[0]
    inter = sum(max(0, min(hi, iv[1]) - max(lo, iv[0])) for lo, hi in segs)
    union = a + b - inter
    return inter / union if union > 0 else 0.0


def _dump(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- matching

def _resolve_groups(unpaired_g, unpaired_o, adj_g, adj_o, g_iv, o_iv, gkey, okey, iou_min):
    """Assign unpaired ground truth / observations to splits and merges, one connected component
    (joined by overlap) at a time.
    Returns (splits = [(gi, [oi, ...])], merges = [(oi, [gi, ...])])."""
    ug, uo = set(unpaired_g), set(unpaired_o)
    seen_g = set()
    splits, merges = [], []
    for g0 in sorted(unpaired_g, key=lambda i: gkey[i]):
        if g0 in seen_g:
            continue
        cg, co = set(), set()
        stack = [("g", g0)]
        while stack:
            kind, i = stack.pop()
            if kind == "g":
                if i in cg:
                    continue
                cg.add(i)
                stack.extend(("o", o) for o in adj_g[i] if o in uo and o not in co)
            else:
                if i in co:
                    continue
                co.add(i)
                stack.extend(("g", g) for g in adj_o[i] if g in ug and g not in cg)
        seen_g |= cg
        while True:
            best = None
            for g in sorted(cg, key=lambda i: gkey[i]):
                s = [o for o in adj_g[g] if o in co]
                if len(s) >= 2:
                    v = _group_iou(_segments([o_iv[o] for o in s]), g_iv[g])
                    if v >= iou_min:
                        k = (-v, g_iv[g][0], 0, gkey[g])
                        if best is None or k < best[0]:
                            best = (k, "split", g, s)
            for o in sorted(co, key=lambda i: okey[i]):
                s = [g for g in adj_o[o] if g in cg]
                if len(s) >= 2:
                    v = _group_iou(_segments([g_iv[g] for g in s]), o_iv[o])
                    if v >= iou_min:
                        k = (-v, o_iv[o][0], 1, okey[o])
                        if best is None or k < best[0]:
                            best = (k, "merge", o, s)
            if best is None:
                break
            _k, kind, anchor, s = best
            if kind == "split":
                cg.discard(anchor)
                co.difference_update(s)
                splits.append((anchor, sorted(s, key=lambda i: okey[i])))
            else:
                co.discard(anchor)
                cg.difference_update(s)
                merges.append((anchor, sorted(s, key=lambda i: gkey[i])))
    return splits, merges


def match(gt_sessions, obs_rows, iou_min=0.5, obs_end_offset_ms=0):
    """Match an array of ground-truth sessions against an array of ime_shadow rows (deterministic;
    independent of input order).

    gt_sessions: the contents of one of sessions.json's definitions[<definition>] entries (an
    array). obs_rows: an array of ime_shadow rows. Returns {"pairs", "split", "merge", "miss",
    "false_pos"}; see the module docstring.
    """
    _check_iou_min(iou_min)
    if not _is_int(obs_end_offset_ms):
        raise ValueError("obs_end_offset_ms must be an integer (ms)")
    gts, obs = list(gt_sessions), list(obs_rows)
    g_iv = [gt_interval(s, "gt_sessions[%d]" % i) for i, s in enumerate(gts)]
    o_iv = [obs_interval(r, obs_end_offset_ms, "obs_rows[%d]" % i) for i, r in enumerate(obs)]
    gkey = [(g_iv[i][0], g_iv[i][1], _dump(gts[i])) for i in range(len(gts))]
    okey = [(o_iv[i][0], o_iv[i][1], _dump(obs[i])) for i in range(len(obs))]

    order = sorted(range(len(obs)), key=lambda i: o_iv[i][0])
    starts = [o_iv[i][0] for i in order]
    max_len = max((hi - lo for lo, hi in o_iv), default=0)
    adj_g = [[] for _ in gts]
    adj_o = [[] for _ in obs]
    cand = []
    for gi, giv in enumerate(g_iv):
        a = bisect_left(starts, giv[0] - max_len)
        b = bisect_right(starts, giv[1])
        for k in range(a, b):
            oi = order[k]
            if not _overlaps(giv, o_iv[oi]):
                continue
            adj_g[gi].append(oi)
            adj_o[oi].append(gi)
            v = _iou(giv, o_iv[oi])
            if v >= iou_min:
                cand.append((-v, gkey[gi], okey[oi], gi, oi))
    cand.sort()
    paired_g, paired_o, pairs = set(), set(), []
    for negv, _gk, _ok, gi, oi in cand:
        if gi in paired_g or oi in paired_o:
            continue
        paired_g.add(gi)
        paired_o.add(oi)
        pairs.append((gi, oi, -negv))

    unpaired_g = [i for i in range(len(gts)) if i not in paired_g]
    unpaired_o = [i for i in range(len(obs)) if i not in paired_o]
    splits, merges = _resolve_groups(unpaired_g, unpaired_o, adj_g, adj_o, g_iv, o_iv, gkey, okey, iou_min)
    used_g = set(g for g, _ in splits) | set(g for _, gl in merges for g in gl)
    used_o = set(o for _, ol in splits for o in ol) | set(o for o, _ in merges)

    pairs.sort(key=lambda p: (gkey[p[0]], okey[p[1]]))
    splits.sort(key=lambda p: gkey[p[0]])
    merges.sort(key=lambda p: okey[p[0]])
    miss = sorted((i for i in unpaired_g if i not in used_g), key=lambda i: gkey[i])
    false_pos = sorted((i for i in unpaired_o if i not in used_o), key=lambda i: okey[i])
    return {
        "pairs": [{"gt": gts[g], "obs": obs[o], "iou": v} for g, o, v in pairs],
        "split": [{"gt": gts[g], "obs": [obs[o] for o in ol]} for g, ol in splits],
        "merge": [{"obs": obs[o], "gt": [gts[g] for g in gl]} for o, gl in merges],
        "miss": [gts[i] for i in miss],
        "false_pos": [obs[i] for i in false_pos],
    }


def recount_split_merge(result, theta=0.5, obs_end_offset_ms=0):
    """Second pass over match()'s leftover items for split_rate / merge_rate.

    The primary 1:1 pairing in match() is never revisited here. This function only looks for extra
    evidence among the items match() already left in "false_pos" / "miss":
      - a false_pos row whose own length has a fraction >= theta inside an already-paired ground-truth
        session's interval is recounted as evidence that session was split;
      - a miss session whose own length has a fraction >= theta inside an already-paired observation's
        interval is recounted as evidence that observation was a merge.
    Each leftover item is attributed to at most one paired counterpart: the one with the largest covered
    fraction (ties broken by the counterpart's start time, then end time, then content, so the result does
    not depend on input order).

    Returns {"split": [{"gt", "obs", "frac"}], "merge": [{"gt", "obs", "frac"}]}. "gt" / "obs" here refer to
    the already-paired session in "split" entries but to the leftover (miss) session in "merge" entries; see
    detection_metrics() for how these feed split_rate / merge_rate and the n_fp_as_split / n_miss_as_merge
    breakdowns.
    """
    _check_theta(theta)
    paired = [(gt_interval(p["gt"], "pairs[].gt"), p["gt"], obs_interval(p["obs"], obs_end_offset_ms, "pairs[].obs"), p["obs"])
              for p in result["pairs"]]

    split = []
    for row in result["false_pos"]:
        oiv = obs_interval(row, obs_end_offset_ms, "false_pos[]")
        best = None
        for giv, gt, _oiv2, _obs2 in paired:
            frac = _covered_frac(oiv, giv)
            if frac >= theta:
                key = (-frac, giv[0], giv[1], _dump(gt))
                if best is None or key < best[0]:
                    best = (key, gt, frac)
        if best is not None:
            split.append({"gt": best[1], "obs": row, "frac": best[2]})

    merge = []
    for s in result["miss"]:
        giv = gt_interval(s, "miss[]")
        best = None
        for _giv2, _gt2, oiv, obs in paired:
            frac = _covered_frac(giv, oiv)
            if frac >= theta:
                key = (-frac, oiv[0], oiv[1], _dump(obs))
                if best is None or key < best[0]:
                    best = (key, obs, frac)
        if best is not None:
            merge.append({"gt": s, "obs": best[1], "frac": best[2]})

    split.sort(key=lambda x: (gt_interval(x["gt"]), _dump(x["gt"]), obs_interval(x["obs"], obs_end_offset_ms), _dump(x["obs"])))
    merge.sort(key=lambda x: (gt_interval(x["gt"]), _dump(x["gt"]), obs_interval(x["obs"], obs_end_offset_ms), _dump(x["obs"])))
    return {"split": split, "merge": merge}


# ---------------------------------------------------------------- detection metrics

def _prop(x, n):
    if _is_int(x) and _is_int(n) and 0 <= n < x:
        # Deviation D10: a numerator above its denominator has no interval; the frozen code stopped here.
        return {"x": x, "n": n, "value": None, "lo": None, "hi": None, "note": NUMERATOR_ABOVE_DENOMINATOR}
    lo, hi = stats.wilson(x, n)
    return {"x": x, "n": n, "value": (x / n if n else None), "lo": lo, "hi": hi}


def _f1_prop(pairs, n_gt, n_obs):
    """F1 = 2 * pairs / (n_gt + n_obs). Unlike the other rates here, this ratio counts each matched pair
    twice (once toward the ground-truth side, once toward the observation side), so its two "successes" are
    perfectly correlated rather than independent Bernoulli trials. Feeding stats.wilson(2*pairs, n_gt+n_obs)
    would treat them as independent and understate the true uncertainty, so lo/hi are left None here
    instead of being reported as a Wilson interval that is not one."""
    n = n_gt + n_obs
    return {"x": 2 * pairs, "n": n, "value": (2.0 * pairs / n if n else None), "lo": None, "hi": None,
            "note": "interval intentionally omitted: the doubled pair count is not an independent-trials proportion"}


def _metrics_row(label, c, names):
    n_gt = c["pairs"] + c["split_gt"] + c["merge_gt"] + c["miss"]
    n_obs = c["pairs"] + c["split_obs"] + c["merge_obs"] + c["fp"]
    if names and isinstance(label, tuple) and len(names) == len(label):
        shown = dict(zip(names, label))
    elif isinstance(label, tuple):
        shown = list(label)
    else:
        shown = label
    return {
        "stratum": shown, "n_gt": n_gt, "n_obs": n_obs, "n_pairs": c["pairs"],
        "n_split_gt": c["split_gt"], "n_split_obs": c["split_obs"],
        "n_merge_obs": c["merge_obs"], "n_merge_gt": c["merge_gt"],
        "n_miss": c["miss"], "n_false_pos": c["fp"],
        "n_split_gt_recount": c["split_gt_recount"], "n_fp_as_split": c["fp_as_split"],
        "n_merge_obs_recount": c["merge_obs_recount"], "n_miss_as_merge": c["miss_as_merge"],
        "precision": _prop(c["pairs"], n_obs),
        "recall": _prop(c["pairs"], n_gt),
        "f1": _f1_prop(c["pairs"], n_gt, n_obs),
        "split_rate": _prop(c["split_gt"] + c["split_gt_recount"], n_gt),
        "merge_rate": _prop(c["merge_obs"] + c["merge_obs_recount"], n_obs),
        "miss_rate": _prop(c["miss"], n_gt),
        "false_pos_rate": _prop(c["fp"], n_obs),
    }


def detection_metrics(result, strata=None, theta=0.5, obs_end_offset_ms=0, recount=None):
    """Turn match()'s return value into per-stratum detection metrics; see the module docstring.

    strata: None (everything in a single stratum, "all") or {"gt": session -> label, "obs":
    observation row -> label, "names": [a name for each element of the label] (optional)}. A label
    is any hashable value (a tuple is fine) or None. Pairs, splits, and misses are counted in the
    ground-truth session's stratum; a merge is counted (as merge_obs) in the stratum of its earliest
    ground-truth session, while each of its ground-truth sessions is also counted (as merge_gt) in
    its own stratum. False positives are counted in the observation row's stratum.

    theta / obs_end_offset_ms: passed straight through to recount_split_merge() to compute the
    n_split_gt_recount / n_merge_obs_recount / n_fp_as_split / n_miss_as_merge fields and split_rate /
    merge_rate. They do not affect n_gt, n_obs, n_pairs, precision, recall or F1.
    recount: recount_split_merge(result, theta, obs_end_offset_ms) when the caller already has it (the
    same matching counted under several strata); computed here when None.
    """
    if strata is None:
        def gl(_s):
            return "all"
        ol = gl
        names = None
    else:
        gl, ol, names = strata["gt"], strata["obs"], strata.get("names")
    counts = {}

    def bucket(label):
        if label not in counts:
            counts[label] = {"pairs": 0, "split_gt": 0, "split_obs": 0, "merge_obs": 0, "merge_gt": 0, "miss": 0, "fp": 0,
                              "split_gt_recount": 0, "fp_as_split": 0, "merge_obs_recount": 0, "miss_as_merge": 0}
        return counts[label]

    for p in result["pairs"]:
        bucket(gl(p["gt"]))["pairs"] += 1
    for sp in result["split"]:
        c = bucket(gl(sp["gt"]))
        c["split_gt"] += 1
        c["split_obs"] += len(sp["obs"])
    for mg in result["merge"]:
        bucket(gl(mg["gt"][0]))["merge_obs"] += 1
        for g in mg["gt"]:
            bucket(gl(g))["merge_gt"] += 1
    for s in result["miss"]:
        bucket(gl(s))["miss"] += 1
    for r in result["false_pos"]:
        bucket(ol(r))["fp"] += 1

    rc = recount_split_merge(result, theta, obs_end_offset_ms) if recount is None else recount
    mode = merge_recount_mode()
    paired_gt = dict((id(p["obs"]), p["gt"]) for p in result["pairs"])
    split_recount_seen = {}
    for e in rc["split"]:
        bucket(ol(e["obs"]))["fp_as_split"] += 1
        st = gl(e["gt"])
        bucket(st)
        split_recount_seen.setdefault(st, set()).add(_dump(e["gt"]))
    merge_recount_seen = {}
    for e in rc["merge"]:
        st = gl(e["gt"])
        bucket(st)["miss_as_merge"] += 1
        bucket(st)
        if mode == "obs":
            if id(e["obs"]) not in paired_gt:
                raise InputError("recount: a recounted merge's observation row is not one of the pairs")
            st = gl(paired_gt[id(e["obs"])])
            bucket(st)
        merge_recount_seen.setdefault(st, set()).add(_dump(e["obs"]))
    for st, s in split_recount_seen.items():
        bucket(st)["split_gt_recount"] = len(s)
    for st, s in merge_recount_seen.items():
        bucket(st)["merge_obs_recount"] = len(s)

    labels = sorted(counts, key=lambda L: (L is None, _dump(L)))
    return [_metrics_row(L, counts[L], names) for L in labels]


# ---------------------------------------------------------------- input parsing (CLI)

def _no_float(_s):
    raise InputError("fractional or exponential numbers are not accepted")


def _no_constant(_s):
    raise InputError("NaN and Infinity are not accepted")


def _no_dup(pairs, where):
    out = {}
    for k, v in pairs:
        if k in out:
            raise InputError("%s: the same key appears twice" % where)
        out[k] = v
    return out


def _json_loads(text, where, allow_float):
    try:
        return json.loads(text, parse_float=(None if allow_float else _no_float), parse_constant=_no_constant,
                          object_pairs_hook=lambda pairs: _no_dup(pairs, where))
    except InputError:
        raise
    except (ValueError, RecursionError):
        raise InputError("%s: not valid JSON" % where)


def _decode(data, what):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("%s: not valid UTF-8" % what)


def _jsonl(data, what):
    out = []
    for n, raw in enumerate(_decode(data, what).split("\n"), 1):
        raw = raw.rstrip("\r")
        if raw.strip():
            out.append((n, _json_loads(raw, "%s:%d" % (what, n), False)))
    return out


def parse_obs(data):
    """Parse obs/1 into an array of ime_shadow rows ({"type", "t", "dwell_ms", "nav", "abandon"}
    only; key rows are not read)."""
    lines = _jsonl(data, "obs")
    if not lines:
        raise InputError("obs: empty file")
    hdr = lines[0][1]
    if not (isinstance(hdr, dict) and hdr.get("type") == "meta" and hdr.get("format") == "obs/1"):
        raise InputError("obs: line 1 is not an obs/1 header")
    rows = []
    for n, row in lines[1:]:
        if not isinstance(row, dict):
            raise InputError("obs:%d: not an object" % n)
        if row.get("type") != "ime_shadow":
            continue
        t, d, nav, ab = row.get("t"), row.get("dwell_ms"), row.get("nav"), row.get("abandon")
        if not (_is_int(t) and _is_int(d) and _is_int(nav) and type(ab) is bool) or t < 0 or d < 0 or nav < 0:
            raise InputError("obs:%d: ime_shadow's t / dwell_ms / nav / abandon has the wrong type" % n)
        rows.append({"type": "ime_shadow", "t": t, "dwell_ms": d, "nav": nav, "abandon": ab})
    return rows


def parse_plan_strata(data):
    """Parse plan/1 into {"os", "sha256", "trials": {trial_id: {"block_id", "scenario", "speed"}}},
    keeping only the fields needed to determine strata."""
    lines = _jsonl(data, "plan")
    if not lines:
        raise InputError("plan: empty file")
    hdr = lines[0][1]
    if not (isinstance(hdr, dict) and hdr.get("kind") == "header" and hdr.get("format") == "plan/1"):
        raise InputError("plan: line 1 is not a plan/1 header")
    if hdr.get("os") not in ("macos", "windows"):
        raise InputError("plan: the header's os is neither macos nor windows")
    trials = {}
    for n, row in lines[1:]:
        if not isinstance(row, dict) or row.get("kind") != "trial":
            continue
        tid, bid, sc, sp = row.get("trial_id"), row.get("block_id"), row.get("scenario"), row.get("speed")
        if not isinstance(tid, str) or not gt_sessions.TRIAL_ID_RE.fullmatch(tid):
            raise InputError("plan:%d: trial_id has the wrong shape" % n)
        if tid in trials:
            raise InputError("plan:%d: duplicate trial_id" % n)
        if not _is_int(bid) or not isinstance(sc, str) or not SCENARIO_RE.fullmatch(sc) or sp not in SPEEDS:
            raise InputError("plan:%d: block_id / scenario / speed has the wrong shape" % n)
        trials[tid] = {"block_id": bid, "scenario": sc, "speed": sp}
    return {"os": hdr["os"], "sha256": hashlib.sha256(data).hexdigest(), "trials": trials}


def parse_sessions(data, definition):
    """Parse sessions.json into (src, the sessions array for the given definition, the excluded
    array). Rejects input whose sessions are missing the fields introduced for trial-level exclusion
    tracking."""
    obj = _json_loads(_decode(data, "sessions"), "sessions", True)
    if not isinstance(obj, dict) or not isinstance(obj.get("definitions"), dict):
        raise InputError("sessions: missing definitions (not gt_sessions.py's output)")
    if definition not in DEFINITIONS:
        raise InputError("definition must be all, macos_def, or windows_def")
    sess = obj["definitions"].get(definition)
    if sess is None:
        raise InputError("sessions: definitions.%s is null (this definition cannot be built for this src)" % definition)
    if not isinstance(sess, list):
        raise InputError("sessions: definitions.%s is not an array" % definition)
    for i, s in enumerate(sess):
        if not isinstance(s, dict) or not isinstance(s.get("excluded_by"), list):
            raise InputError("sessions: definitions.%s[%d] is missing excluded_by (a stale output; rebuild it with gt_sessions.py)" % (definition, i))
        if not _is_int(s.get("start_wall_ms")) or not (s.get("end_wall_ms") is None or _is_int(s.get("end_wall_ms"))):
            raise InputError("sessions: definitions.%s[%d] has the wrong type for start_wall_ms / end_wall_ms" % (definition, i))
        nswo = s.get("n_start_while_open")
        if not (_is_int(nswo) and nswo >= 0):
            raise InputError("sessions: definitions.%s[%d] is missing n_start_while_open (a non-negative integer; a stale output, rebuild it with gt_sessions.py)" % (definition, i))
    exc = obj.get("excluded")
    if not isinstance(exc, list) or any(
            not isinstance(e, dict) or not isinstance(e.get("type"), str) or not isinstance(e.get("trial_ids"), list)
            for e in exc):
        raise InputError("sessions: every excluded entry must have type and trial_ids (a stale output; rebuild it with gt_sessions.py)")
    return obj.get("src"), sess, exc


def parse_config(data):
    """Parse the configuration JSON (defaults if None) into {"iou_min", "obs_end_offset_ms", "theta",
    "clock_offsets_ms": {int: int}, "cell", "drop_start_while_open"}."""
    cfg = {"iou_min": 0.5, "obs_end_offset_ms": 0, "theta": 0.5, "clock_offsets_ms": {}, "cell": None,
           "drop_start_while_open": False}
    if data is None:
        return cfg
    obj = _json_loads(_decode(data, "config"), "config", True)
    if not isinstance(obj, dict):
        raise InputError("config: the top level must be an object")
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        if k == "iou_min":
            try:
                _check_iou_min(v)
            except ValueError as e:
                raise InputError("config: %s" % e)
            cfg[k] = v
        elif k == "obs_end_offset_ms":
            if not _is_int(v):
                raise InputError("config: obs_end_offset_ms must be an integer")
            cfg[k] = v
        elif k == "theta":
            try:
                _check_theta(v)
            except ValueError as e:
                raise InputError("config: %s" % e)
            cfg[k] = v
        elif k == "clock_offsets_ms":
            if not isinstance(v, dict):
                raise InputError("config: clock_offsets_ms must be an object (block_id to ms)")
            for bk, bv in v.items():
                if not re.fullmatch(r"[0-9]{1,9}", bk) or not _is_int(bv):
                    raise InputError("config: clock_offsets_ms keys must be decimal block_ids mapping to integer ms")
                cfg[k][int(bk)] = bv
        elif k == "cell":
            if not isinstance(v, str) or not CELL_RE.fullmatch(v):
                raise InputError("config: cell must match ^[A-Za-z0-9_.+-]{1,64}$")
            cfg[k] = v
        elif k == "drop_start_while_open":
            if type(v) is not bool:
                raise InputError("config: drop_start_while_open must be a boolean")
            cfg[k] = v
        else:
            raise InputError("config: unknown key '%s'" % k)
    return cfg


def parse_prediction(data, oracle_sha256, plan_sha256):
    """Parse spec_predict.py's output into {"sha256", "long": set of trial ids, "layers": {layer: set of
    trial ids}} (see PREDICTION_LAYERS). A prediction made from another oracle or plan is refused."""
    obj = _json_loads(_decode(data, "prediction"), "prediction", True)
    if not isinstance(obj, dict) or obj.get("format") != PREDICTION_FORMAT:
        raise InputError("prediction: not spec_predict.py's output (format %s)" % PREDICTION_FORMAT)
    ins = obj.get("inputs_sha256")
    if not isinstance(ins, dict) or ins.get("oracle") != oracle_sha256 or ins.get("plan") != plan_sha256:
        raise InputError("prediction: made from another oracle or plan (unrelated input)")
    trials = obj.get("trials")
    by = trials.get("by_trial") if isinstance(trials, dict) else None
    if not isinstance(by, dict):
        raise InputError("prediction: trials.by_trial is missing")
    long_trials = set()
    layers = dict((k, set()) for k in PREDICTION_LAYERS)
    for tid, d in by.items():
        if not isinstance(d, dict) or any(type(d.get(k)) is not bool
                                          for k in ("long", "carried_out", "received_carry", "closing_key_in_reading")) \
                or not (_is_int(d.get("n_started")) and d["n_started"] >= 0):
            raise InputError("prediction: an entry of trials.by_trial has the wrong shape")
        if d["long"]:
            long_trials.add(tid)
        if d["carried_out"]:
            layers["carried_out"].add(tid)
        if d["received_carry"]:
            layers["received_carry"].add(tid)
        if d["closing_key_in_reading"]:
            layers["closing_key_in_reading"].add(tid)
    marked = layers["carried_out"] | layers["received_carry"] | layers["closing_key_in_reading"]
    layers["none"] = set(by) - marked
    uncontaminated = set(by) - layers["carried_out"] - layers["received_carry"]
    return {"sha256": hashlib.sha256(data).hexdigest(), "long": long_trials, "layers": layers,
            "uncontaminated": uncontaminated}


def _layer_metrics(result, members, gt_tid, obs_tid, speed_of, theta, off_end, recount):
    """The detection metrics of the items whose trial is in members: pooled and per speed, counted from
    the same matching (nothing is matched again)."""
    def only(lab_of):
        return lambda tid: lab_of(tid) if tid in members else None
    by = detection_metrics(result, {"gt": lambda s: only(speed_of)(gt_tid(s)), "obs": lambda r: only(speed_of)(obs_tid(r))},
                           theta, off_end, recount)
    pooled = detection_metrics(result, {"gt": lambda s: only(lambda _t: "layer")(gt_tid(s)),
                                        "obs": lambda r: only(lambda _t: "layer")(obs_tid(r))}, theta, off_end, recount)
    pooled = [x for x in pooled if x["stratum"] == "layer"]
    return {"n_trials": len(members), "pooled": pooled[0] if pooled else None,
            "by_speed": [dict(x, stratum={"speed": x["stratum"]}) for x in by if x["stratum"] is not None]}


def _block_windows(oracle):
    wins = {}
    for row in oracle["rows"]:
        b = row.get("block_id")
        if row.get("kind") == "block_begin" and _is_int(b):
            wins.setdefault(b, [row["t_wall_ms"], None])
        elif row.get("kind") == "block_end" and b in wins:
            wins[b][1] = row["t_wall_ms"]
    last = oracle["rows"][-1]["t_wall_ms"] if oracle["rows"] else 0
    for w in wins.values():
        if w[1] is None:
            w[1] = last
    return wins


def _assign_block(t, wins, offsets):
    """The block whose window the corrected t is closest to (distance 0 when inside the window);
    ties go to the smaller block_id."""
    best = None
    for b in sorted(wins):
        tc = t - offsets.get(b, 0)
        lo, hi = wins[b]
        d = max(0, lo - tc, tc - hi)
        if best is None or d < best[0]:
            best = (d, b)
    return None if best is None else best[1]


def _assign_trial(lo, hi, trials, begins):
    """The trial_id of the trial with the greatest overlap with the given interval (ties go to the
    earlier trial); None if no trial overlaps. trials must be sorted by start time and pairwise
    disjoint."""
    best, best_ov = None, 0
    i = bisect_right(begins, hi) - 1
    while i >= 0:
        tr = trials[i]
        if tr["end_wall_ms"] < lo:
            break
        ov = min(hi, tr["end_wall_ms"]) - max(lo, tr["begin_wall_ms"])
        point = lo == hi and tr["begin_wall_ms"] <= lo <= tr["end_wall_ms"]
        if (ov > 0 and ov >= best_ov) or (best is None and point):
            best, best_ov = tr, max(ov, 0)
        i -= 1
    return None if best is None else best["trial_id"]


def evaluate(sessions_bytes, definition, obs_bytes, plan_bytes, oracle_bytes, config_bytes=None,
             prediction_bytes=None, scope="all_trials"):
    """The CLI's core logic (bytes in, an output dict out). Identical input always yields an
    identical dict. prediction_bytes / scope: see "Prediction" in the module docstring."""
    src, sess, excluded = parse_sessions(sessions_bytes, definition)
    cfg = parse_config(config_bytes)
    plan = parse_plan_strata(plan_bytes)
    oracle = gt_sessions.parse_oracle(oracle_bytes)
    want = oracle["plan_sha256"]
    if not isinstance(want, str) or want.lower() != plan["sha256"]:
        raise InputError("plan: SHA-256 does not match the oracle header's plan_sha256 (unrelated plan)")
    if scope not in SCOPES:
        raise InputError("scope must be one of %s" % ", ".join(SCOPES))
    pred = None
    if prediction_bytes is not None:
        pred = parse_prediction(prediction_bytes, hashlib.sha256(oracle_bytes).hexdigest(), plan["sha256"])
    elif scope != "all_trials":
        raise InputError("scope %s needs a prediction (the long trials come from it)" % scope)
    rows = parse_obs(obs_bytes)
    cell = cfg["cell"] if cfg["cell"] is not None else plan["os"]
    off_end = cfg["obs_end_offset_ms"]

    offsets = cfg["clock_offsets_ms"]
    wins = _block_windows(oracle)
    for b in offsets:
        if b not in wins:
            raise InputError("config: clock_offsets_ms's block_id %d is not present in oracle" % b)
    # "t" is never rewritten (always the raw observation time); the ground-truth-clock time always
    # goes into the new "t_gt_clock" field, whether or not that block had a configured offset.
    uncorrected_blocks = sorted(b for b in wins if b not in offsets)
    corrected = [dict(r, t_gt_clock=r["t"] - offsets.get(_assign_block(r["t"], wins, offsets), 0)) for r in rows]

    trials = sorted(oracle["trials"], key=lambda t: (t["begin_wall_ms"], t["end_wall_ms"]))
    begins = [t["begin_wall_ms"] for t in trials]
    trial_types = {}
    for e in excluded:
        for tid in e["trial_ids"]:
            trial_types.setdefault(tid, set()).add(e["type"])

    def bump(d, reasons):
        for r in reasons:
            d[r] = d.get(r, 0) + 1

    gt_reasons, kept_gt, n_gt_dropped = {}, [], 0
    for s in sess:
        reasons = set(s["excluded_by"]) | trial_types.get(s.get("trial_id"), set())
        if s["end_wall_ms"] is None:
            reasons.add("open_at_end")
        if cfg["drop_start_while_open"] and s["n_start_while_open"] > 0:
            reasons.add("start_while_open")
        if reasons:
            n_gt_dropped += 1
            bump(gt_reasons, reasons)
        else:
            kept_gt.append(s)
    obs_reasons, kept_obs, n_obs_dropped, obs_tid = {}, [], 0, {}
    for r in corrected:
        lo, hi = obs_interval(r, off_end)
        tid = _assign_trial(lo, hi, trials, begins)
        reasons = trial_types.get(tid, set())
        if reasons:
            n_obs_dropped += 1
            bump(obs_reasons, reasons)
        else:
            kept_obs.append(r)
            obs_tid[id(r)] = tid

    # The scope (after the exclusions, at the level of a trial): the long trials come from the prediction.
    set_aside = {"n_gt": 0, "n_obs": 0}
    if scope != "all_trials":
        want_long = scope == "over_limit"
        in_gt = [s for s in kept_gt if (s.get("trial_id") in pred["long"]) == want_long]
        in_obs = [r for r in kept_obs if (obs_tid[id(r)] in pred["long"]) == want_long]
        set_aside = {"n_gt": len(kept_gt) - len(in_gt), "n_obs": len(kept_obs) - len(in_obs)}
        kept_gt, kept_obs = in_gt, in_obs

    result = match(kept_gt, kept_obs, iou_min=cfg["iou_min"], obs_end_offset_ms=off_end)

    def label(tid):
        info = plan["trials"].get(tid)
        return (cell, info["scenario"], info["speed"]) if info else (cell, None, None)

    strata = {"gt": lambda s: label(s.get("trial_id")), "obs": lambda r: label(obs_tid.get(id(r))),
              "names": ["cell", "scenario", "speed"]}
    theta = cfg["theta"]
    recount = recount_split_merge(result, theta, off_end)
    layers = None
    if pred is not None:
        def speed_of(tid):
            info = plan["trials"].get(tid)
            return info["speed"] if info else None
        layers = dict((L, _layer_metrics(result, pred["layers"][L], lambda s: s.get("trial_id"),
                                         lambda r: obs_tid.get(id(r)), speed_of, theta, off_end, recount))
                      for L in PREDICTION_LAYERS)

    # Diagnostic only: counts, per stratum, of kept ground-truth sessions with n_start_while_open > 0.
    # Not used to exclude anything unless config.drop_start_while_open is true.
    swo_counts = {}
    for s in kept_gt:
        if s["n_start_while_open"] > 0:
            lbl = label(s.get("trial_id"))
            swo_counts[lbl] = swo_counts.get(lbl, 0) + 1
    swo_labels = sorted(swo_counts, key=lambda L: (L is None, _dump(L)))
    swo_by_stratum = [{"stratum": dict(zip(["cell", "scenario", "speed"], L)), "n": swo_counts[L]} for L in swo_labels]

    config_out = {"iou_min": cfg["iou_min"], "obs_end_offset_ms": off_end, "theta": theta,
                  "clock_offsets_ms": dict((str(b), v) for b, v in sorted(offsets.items())), "cell": cell,
                  "drop_start_while_open": cfg["drop_start_while_open"], "uncorrected_blocks": uncorrected_blocks}
    if merge_recount_mode() != "gt":
        config_out["merge_recount"] = merge_recount_mode()  # deviation D10; absent under the frozen rule
    return {
        "src": src,
        "definition": definition,
        "config": config_out,
        "inputs_sha256": {
            "sessions": hashlib.sha256(sessions_bytes).hexdigest(), "obs": hashlib.sha256(obs_bytes).hexdigest(),
            "plan": plan["sha256"], "oracle": hashlib.sha256(oracle_bytes).hexdigest(),
            "config": None if config_bytes is None else hashlib.sha256(config_bytes).hexdigest(),
            "prediction": None if pred is None else pred["sha256"]},
        "input": {"n_gt": len(sess), "n_obs": len(rows)},
        "excluded": {
            "gt": {"n": n_gt_dropped, "by_reason": gt_reasons},
            "obs": {"n": n_obs_dropped, "by_reason": obs_reasons},
            "trials": {"n": len(trial_types), "by_type": _count_types(excluded)}},
        "scope": {"name": scope, "set_aside": set_aside,
                  "long_trials": None if pred is None else sorted(pred["long"]),
                  "layer_none_trials": None if pred is None else sorted(pred["layers"]["none"]),
                  "uncontaminated_trials": None if pred is None else sorted(pred["uncontaminated"])},
        "used": {"n_gt": len(kept_gt), "n_obs": len(kept_obs)},
        "diagnostics": {"start_while_open": {"n": sum(swo_counts.values()), "by_stratum": swo_by_stratum},
                        "prediction_layers": layers},
        "metrics": {"by_stratum": detection_metrics(result, strata, theta, off_end, recount),
                    "pooled": detection_metrics(result, None, theta, off_end, recount)[0] if (kept_gt or kept_obs) else None,
                    "note": "pooled sums counts across strata without weighting them (a synthetic input has no "
                            "natural mixing proportion); treat the per-stratum figures as the primary estimates"},
        "matching": result,
    }


def _count_types(excluded):
    by = {}
    for e in excluded:
        by.setdefault(e["type"], set()).update(e["trial_ids"])
    return dict((k, len(v)) for k, v in sorted(by.items()))


def render(out):
    """Render the output bytes (LF line endings, UTF-8, no BOM, keys in a fixed sorted order)."""
    return (json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read(path, what):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise InputError("%s: cannot be opened (%s)" % (what, type(e).__name__))


def _same_file(a, b):
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def _write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".match_tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- selftest

def _gt(tid, lo, hi, **kw):
    d = {"trial_id": tid, "start_wall_ms": lo, "end_wall_ms": hi, "start_mono_ns": lo * 1000000,
         "end_mono_ns": None if hi is None else hi * 1000000, "outcome": "commit", "candidate_shown": None, "nav_true": 0,
         "nav_true_cand": None, "excluded_by": [], "n_start_while_open": 0}
    d.update(kw)
    return d


def _ob(lo, hi, nav=0, abandon=False):
    """An observation row for the interval [lo, hi] at the default offset (0)."""
    return {"type": "ime_shadow", "t": hi, "dwell_ms": hi - lo, "nav": nav, "abandon": abandon}


def _world(n, seed):
    """Sparse synthetic ground truth: lengths of 800-2500 ms, gaps of 30-120 s (chosen so
    coincidental matches are unlikely)."""
    import random
    rng = random.Random(seed)
    t, out = 1700000000000, []
    for i in range(n):
        t += 30000 + int(rng.random() * 90001)
        length = 800 + int(rng.random() * 1701)
        out.append(_gt("t%05d" % (i + 1), t, t + length))
        t += length
    return out


def _shuffled(xs, seed):
    import random
    rng = random.Random(seed)
    xs = list(xs)
    for i in range(len(xs) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        xs[i], xs[j] = xs[j], xs[i]
    return xs


def _sizes(r):
    return (len(r["pairs"]), len(r["split"]), len(r["merge"]), len(r["miss"]), len(r["false_pos"]))


def _case_five_types():
    f = []
    gts = [_gt("t1", 1000, 2000), _gt("t2", 10000, 13000), _gt("t3", 20000, 21000), _gt("t3", 21500, 22500),
           _gt("t4", 30000, 31000)]
    obs = [_ob(1000, 2000), _ob(10000, 11000), _ob(11000, 12000), _ob(12000, 13000), _ob(20000, 22500),
           _ob(40000, 41000)]
    r = match(gts, obs)
    if _sizes(r) != (1, 1, 1, 1, 1):
        f.append("the five-type counts (pairs, split, merge, miss, false_pos) are not (1,1,1,1,1): %r" % (_sizes(r),))
        return f
    if r["pairs"][0]["gt"] is not gts[0] or r["pairs"][0]["obs"] is not obs[0] or r["pairs"][0]["iou"] != 1.0:
        f.append("the exact-match pair is wrong: %r" % (r["pairs"][0],))
    if r["split"][0]["gt"] is not gts[1] or r["split"][0]["obs"] != obs[1:4]:
        f.append("the split (3 fragments, in time order) is wrong: %r" % (r["split"][0],))
    if r["merge"][0]["obs"] is not obs[4] or r["merge"][0]["gt"] != [gts[2], gts[3]]:
        f.append("the merge (one observation to two ground-truth sessions, in time order) is wrong: %r" % (r["merge"][0],))
    if r["miss"] != [gts[4]]:
        f.append("the miss is wrong: %r" % (r["miss"],))
    if r["false_pos"] != [obs[5]]:
        f.append("the false positive is wrong: %r" % (r["false_pos"],))
    # a partial overlap: IoU = 800 / 1200
    r2 = match([_gt("t1", 50000, 51000)], [_ob(50200, 51200)])
    if _sizes(r2) != (1, 0, 0, 0, 0) or abs(r2["pairs"][0]["iou"] - 800.0 / 1200.0) > 1e-12:
        f.append("a partial overlap's IoU is not 800/1200: %r" % (r2,))
    if _sizes(match([], [])) != (0, 0, 0, 0, 0):
        f.append("empty input does not yield an empty result")
    return f


def _case_definition_properties():
    """Definitional properties (documented in the module docstring): fixed behavior, not to be
    changed. Two adjacent fragments become a pair plus a false positive; two fragments with a gap
    between them become a split."""
    f = []
    r = match([_gt("t1", 0, 1000)], [_ob(0, 600), _ob(600, 1000)])
    if _sizes(r) != (1, 0, 0, 0, 1):
        f.append("two adjacent fragments (60% + 40%) do not become 1 pair + 1 false positive: %r" % (_sizes(r),))
    r = match([_gt("t1", 0, 1000)], [_ob(0, 400), _ob(600, 1000)])
    if _sizes(r) != (0, 1, 0, 0, 0):
        f.append("two fragments with a gap (40% each, union 80%) do not become a split: %r" % (_sizes(r),))
    r = match([_gt("t1", 0, 3000), _gt("t2", 3000, 4000)], [_ob(0, 4000)])
    if _sizes(r) != (1, 0, 0, 1, 0):
        f.append("an imbalanced merge (one side 75%) does not become 1 pair + 1 miss: %r" % (_sizes(r),))
    # The union's IoU counts the portion that overshoots the ground-truth interval as part of the union
    #   (testing only the fragments that fit inside it would mis-measure the intersection).
    #   Fragments [-600,300] + [300,600]: union [-600,600] (1200), intersection 600, sum 1600 -> 0.375 < 0.5, not a split
    r = match([_gt("t1", 0, 1000)], [_ob(-600, 300), _ob(300, 600)])
    if _sizes(r) != (0, 0, 0, 1, 2):
        f.append("two fragments that overshoot the session (union IoU 0.375) do not become 1 miss + 2 false positives: %r" % (_sizes(r),))
    #   Fragments [-100,300] + [300,600] + [600,1100]: intersection 1000, sum 1200 -> 0.833 >= 0.5, a split (some overshoot is tolerated)
    r = match([_gt("t1", 0, 1000)], [_ob(-100, 300), _ob(300, 600), _ob(600, 1100)])
    if _sizes(r) != (0, 1, 0, 0, 0):
        f.append("three slightly overshooting fragments (union IoU 0.833) do not become a split: %r" % (_sizes(r),))
    #   The same holds for merges: observation [0,1000] against ground truth [-800,300] + [300,600]: intersection 600, sum 1800 -> 0.333 < 0.5, not a merge
    r = match([_gt("t1", -800, 300), _gt("t2", 300, 600)], [_ob(0, 1000)])
    if _sizes(r) != (0, 0, 0, 2, 1):
        f.append("two overshooting ground-truth sessions (union IoU 0.333) do not become 2 misses + 1 false positive: %r" % (_sizes(r),))
    # more fragments leave less room for any one of them to pair alone (three fragments of 1/3 each cannot individually pair)
    r = match([_gt("t1", 0, 1200)], [_ob(0, 400), _ob(400, 800), _ob(800, 1200)])
    if _sizes(r) != (0, 1, 0, 0, 0):
        f.append("three fragments do not become a split: %r" % (_sizes(r),))
    # a short session stops pairing once the timing offset is large enough (a session of length L pairs only while the offset stays at or below L/3)
    r = match([_gt("t1", 0, 150)], [_ob(60, 210)])
    if _sizes(r) != (0, 0, 0, 1, 1):
        f.append("shifting a 150 ms session by 60 ms should yield a miss + a false positive (L/3 = 50): %r" % (_sizes(r),))
    return f


def _case_iou_boundary():
    f = []
    if _sizes(match([_gt("t1", 0, 300)], [_ob(100, 400)])) != (1, 0, 0, 0, 0):
        f.append("an IoU of exactly 0.5 (200/400) should pair (>=)")
    if _sizes(match([_gt("t1", 0, 300)], [_ob(101, 401)])) != (0, 0, 0, 1, 1):
        f.append("an IoU of 199/401 < 0.5 should not pair")
    if _sizes(match([_gt("t1", 0, 300)], [_ob(50, 350)], iou_min=0.7)) != (1, 0, 0, 0, 0):
        f.append("with iou_min = 0.7, an IoU of 250/350 = 0.714 should pair")
    if _sizes(match([_gt("t1", 0, 300)], [_ob(60, 360)], iou_min=0.7)) != (0, 0, 0, 1, 1):
        f.append("with iou_min = 0.7, an IoU of 240/360 = 0.667 paired unexpectedly")
    # a zero-length interval pairs only with a zero-length interval at the same point
    if _sizes(match([_gt("t1", 500, 500)], [_ob(500, 500)])) != (1, 0, 0, 0, 0):
        f.append("two zero-length intervals at the same point do not pair")
    if _sizes(match([_gt("t1", 500, 500)], [_ob(0, 1000)])) != (0, 0, 0, 1, 1):
        f.append("a zero-length ground truth paired at a point inside a longer observation")
    return f


def _case_tie_determinism():
    f = []
    # two observations at the same interval with the same IoU: reversing the order gives the same
    # result (the one that sorts first by content pairs first)
    g = [_gt("t1", 0, 1000)]
    a, b = _ob(0, 1000, nav=1), _ob(0, 1000, nav=2)
    r1, r2 = match(g, [a, b]), match(g, [b, a])
    if _dump(r1) != _dump(r2) or r1["pairs"][0]["obs"]["nav"] != 1 or r1["false_pos"] != [b]:
        f.append("a tie (two observations at the same interval) depends on input order, or the one that pairs first is not the one that sorts first by content")
    # two ground-truth sessions at the same interval with one observation
    g2 = [_gt("t1", 0, 1000), _gt("t2", 0, 1000)]
    r1, r2 = match(g2, [_ob(0, 1000)]), match(list(reversed(g2)), [_ob(0, 1000)])
    if _dump(r1) != _dump(r2) or r1["pairs"][0]["gt"]["trial_id"] != "t1":
        f.append("a tie (two ground-truth sessions at the same interval) depends on input order")
    # two ground-truth sessions with a tied IoU (1/3 each) but different start times: the earlier one pairs first
    o = [_ob(1000, 2000)]
    g3 = [_gt("t2", 1500, 2500), _gt("t1", 500, 1500)]
    r = match(g3, o, iou_min=0.3)
    if _sizes(r) != (1, 0, 0, 1, 0) or r["pairs"][0]["gt"]["trial_id"] != "t1":
        f.append("a tied IoU does not pair in ascending order of start time: %r" % (r["pairs"],))
    # reordering the input does not change the result (a mix of pairs, splits, merges, misses, and false positives)
    gts = _world(40, 3)
    obs = [_ob(g["start_wall_ms"] + 30, g["end_wall_ms"] + 30) for g in gts[:30]]
    obs += [_ob(gts[30]["start_wall_ms"], gts[30]["start_wall_ms"] + 300), _ob(gts[30]["start_wall_ms"] + 400, gts[30]["end_wall_ms"])]
    obs += [_ob(gts[35]["start_wall_ms"] - 2000, gts[36]["end_wall_ms"] + 2000)]
    base = _dump(match(gts, obs))
    for seed in range(1, 6):
        if _dump(match(_shuffled(gts, seed), _shuffled(obs, seed + 100))) != base:
            f.append("reordering the input changes the result (seed %d)" % seed)
            break
    return f


def _case_positive_control():
    """Positive control: removing one observation row, or shifting one row, degrades the metrics."""
    f = []
    gts = _world(60, 7)
    obs = [_ob(g["start_wall_ms"] + (i % 7) * 20 - 60, g["end_wall_ms"] + (i % 7) * 20 - 60) for i, g in enumerate(gts)]
    base = detection_metrics(match(gts, obs))[0]
    if base["recall"]["x"] != 60 or base["precision"]["x"] != 60 or base["n_miss"] or base["n_false_pos"]:
        f.append("the baseline (small offsets only) does not pair everything: %r" % (base,))
    dropped = detection_metrics(match(gts, obs[:20] + obs[21:]))[0]
    if not (dropped["recall"]["x"] == 59 and dropped["n_miss"] == 1 and dropped["recall"]["value"] < base["recall"]["value"]):
        f.append("recall does not drop after removing one observation row: %r" % (dropped["recall"],))
    if dropped["precision"]["value"] != 1.0:
        f.append("precision should remain 1 after removing one observation row (no new false positives)")
    moved = list(obs)
    moved[20] = _ob(obs[20]["t"] - obs[20]["dwell_ms"] + 2000, obs[20]["t"] + 2000)
    r = match(gts, moved)
    m = detection_metrics(r)[0]
    if not (m["n_pairs"] == 59 and m["n_miss"] == 1 and m["n_false_pos"] == 1):
        f.append("shifting one row by 2 s should reduce pairs by one and yield 1 miss + 1 false positive: %r" % (_sizes(r),))
    if not (m["recall"]["value"] < base["recall"]["value"] and m["precision"]["value"] < base["precision"]["value"]
            and m["f1"]["value"] < base["f1"]["value"]):
        f.append("recall, precision, and F1 do not drop after the shift")
    if not (m["recall"]["hi"] < 1.0 or m["recall"]["lo"] < base["recall"]["lo"]):
        f.append("the interval does not reflect the degradation")
    return f


def _case_negative_control():
    """Negative control: shifting the observations by 60 s, or shuffling their times, drives the
    pair count down to chance level (near zero)."""
    f = []
    n = 300
    gts = _world(n, 11)
    obs = [_ob(g["start_wall_ms"], g["end_wall_ms"]) for g in gts]
    if detection_metrics(match(gts, obs))[0]["n_pairs"] != n:
        f.append("the baseline (no offset) does not pair everything")
    shifted = [dict(o, t=o["t"] + 60000) for o in obs]
    ms = detection_metrics(match(gts, shifted))[0]
    if ms["n_pairs"] > 0.05 * n:
        f.append("shifting by 60 s does not drive pairs down to chance level (<= 5%%): %d / %d" % (ms["n_pairs"], n))
    # Merely permuting the times among rows leaves the set of end times aligned with the ground truth,
    # so pairs still remain (86% in practice). To see chance level, the observation times must instead
    # be redrawn uniformly over the whole recording span.
    import random
    rng = random.Random(5)
    lo_span, hi_span = gts[0]["start_wall_ms"], gts[-1]["end_wall_ms"]
    scrambled = [dict(o, t=lo_span + o["dwell_ms"] + int(rng.random() * (hi_span - lo_span))) for o in obs]
    mz = detection_metrics(match(gts, scrambled))[0]
    if mz["n_pairs"] > 0.05 * n:
        f.append("shuffling the times does not drive pairs down to chance level (<= 5%%): %d / %d" % (mz["n_pairs"], n))
    if not (ms["recall"]["hi"] < 0.15 and mz["recall"]["hi"] < 0.15):
        f.append("the negative control's recall interval upper bound does not indicate chance level: %r %r" % (ms["recall"], mz["recall"]))
    return f


def _case_byte_identical():
    f = []
    gts = _world(120, 21)
    obs = [_ob(g["start_wall_ms"] + 25, g["end_wall_ms"] + 25) for g in gts[:100]] + [_ob(5, 900)]
    outs = []
    for _ in range(2):
        r = match(gts, obs)
        blob = json.dumps({"r": r, "m": detection_metrics(r)}, sort_keys=True, ensure_ascii=False)
        outs.append(hashlib.sha256(blob.encode("utf-8")).hexdigest())
    if outs[0] != outs[1]:
        f.append("identical input does not yield byte-identical results")
    return f


def _case_metrics():
    f = []
    gts = [_gt("t1", 1000, 2000), _gt("t2", 10000, 13000), _gt("t3", 20000, 21000), _gt("t3", 21500, 22500),
           _gt("t4", 30000, 31000)]
    obs = [_ob(1000, 2000), _ob(10000, 11000), _ob(11000, 12000), _ob(12000, 13000), _ob(20000, 22500),
           _ob(40000, 41000)]
    r = match(gts, obs)
    m = detection_metrics(r)
    if len(m) != 1 or m[0]["stratum"] != "all":
        f.append("without strata, everything should fall into a single stratum, 'all'")
        return f
    m = m[0]
    want = {"precision": (1, 6), "recall": (1, 5), "split_rate": (1, 5), "merge_rate": (1, 6),
            "miss_rate": (1, 5), "false_pos_rate": (1, 6)}
    for k, (x, n) in want.items():
        if (m[k]["x"], m[k]["n"]) != (x, n) or abs(m[k]["value"] - x / n) > 1e-12:
            f.append("%s is not %d/%d: %r" % (k, x, n, m[k]))
        if (m[k]["lo"], m[k]["hi"]) != stats.wilson(x, n):
            f.append("%s's interval is not stats.wilson(%d, %d)" % (k, x, n))
    # F1: the count is still 2*pairs/(n_gt+n_obs), but lo/hi are intentionally None (the doubled pair
    # count is not an independent-trials proportion; see _f1_prop's docstring).
    if (m["f1"]["x"], m["f1"]["n"]) != (2, 11) or abs(m["f1"]["value"] - 2.0 / 11.0) > 1e-12:
        f.append("f1 is not 2/11: %r" % (m["f1"],))
    if (m["f1"]["lo"], m["f1"]["hi"]) != (None, None):
        f.append("f1's interval should intentionally be None (correlated successes are not handled by a Wilson interval): %r" % (m["f1"],))
    if (m["n_gt"], m["n_obs"], m["n_split_obs"], m["n_merge_gt"]) != (5, 6, 3, 2):
        f.append("the counts (n_gt / n_obs / n_split_obs / n_merge_gt) are wrong: %r" % m)
    if (m["n_split_gt_recount"], m["n_fp_as_split"], m["n_merge_obs_recount"], m["n_miss_as_merge"]) != (0, 0, 0, 0):
        f.append("the theta recount should add nothing in this example: %r" % (
            (m["n_split_gt_recount"], m["n_fp_as_split"], m["n_merge_obs_recount"], m["n_miss_as_merge"]),))
    # strata: ground truth determined by trial_id, observations by time
    strata = {"gt": lambda s: "A" if s["trial_id"] in ("t1", "t2") else "B",
              "obs": lambda o: "A" if o["t"] < 15000 else "B"}
    by = dict((x["stratum"], x) for x in detection_metrics(r, strata))
    a, b = by["A"], by["B"]
    if (a["n_pairs"], a["n_split_gt"], a["n_split_obs"], a["n_gt"], a["n_obs"]) != (1, 1, 3, 2, 4):
        f.append("stratum A's counts are wrong: %r" % a)
    if (b["n_merge_obs"], b["n_merge_gt"], b["n_miss"], b["n_false_pos"], b["n_gt"], b["n_obs"]) != (1, 2, 1, 1, 3, 2):
        f.append("stratum B's counts are wrong (a merge is counted in the first ground truth's stratum; a false positive in the observation's stratum): %r" % b)
    # empty input / edge cases
    if detection_metrics({"pairs": [], "split": [], "merge": [], "miss": [], "false_pos": []}) != []:
        f.append("an empty result does not stay empty")
    only_fp = detection_metrics(match([], [_ob(0, 10)]))[0]
    if only_fp["recall"] != {"x": 0, "n": 0, "value": None, "lo": None, "hi": None}:
        f.append("recall with zero ground truth should be None (not rounded to 0): %r" % (only_fp["recall"],))
    only_miss = detection_metrics(match([_gt("t1", 0, 10)], []))[0]
    if only_miss["recall"]["value"] != 0.0 or only_miss["precision"]["value"] is not None:
        f.append("precision with zero observations should be None, and recall should be 0")
    return f


def _case_offset():
    f = []
    # an end-of-session lag of 100 ms (t is 100 ms later than the composition's actual end): the whole interval shifts by 100 ms
    g = [_gt("t1", 0, 1000)]
    late = [{"type": "ime_shadow", "t": 1100, "dwell_ms": 1000, "nav": 0, "abandon": False}]
    if obs_interval(late[0]) != (100, 1100) or obs_interval(late[0], -100) != (0, 1000):
        f.append("obs_interval is not [t + offset - dwell_ms, t + offset]")
    r0, r1, r2 = match(g, late), match(g, late, obs_end_offset_ms=-100), match(g, late, obs_end_offset_ms=100)
    if abs(r0["pairs"][0]["iou"] - 900.0 / 1100.0) > 1e-12 or r1["pairs"][0]["iou"] != 1.0 \
            or abs(r2["pairs"][0]["iou"] - 800.0 / 1200.0) > 1e-12:
        f.append("obs_end_offset_ms does not move the IoU from 900/1100 to 1.0 (-100) or 800/1200 (+100)")
    # a short session (200 ms) fails to pair under a 100 ms lag (L/3 = 66); the offset recovers it
    gs, ls = [_gt("t1", 0, 200)], [{"type": "ime_shadow", "t": 300, "dwell_ms": 200, "nav": 0, "abandon": False}]
    if _sizes(match(gs, ls)) != (0, 0, 0, 1, 1) or _sizes(match(gs, ls, obs_end_offset_ms=-100)) != (1, 0, 0, 0, 0):
        f.append("a short session's lag is not recovered by the offset")
    return f


def _case_validation():
    f = []

    def raises(name, fn):
        try:
            fn()
        except ValueError:
            return
        except Exception as e:
            f.append("%s: raised %r instead of ValueError" % (name, e))
            return
        f.append("%s: no exception was raised" % name)

    g, o = [_gt("t1", 0, 100)], [_ob(0, 100)]
    raises("a null end", lambda: match([_gt("t1", 0, 100, end_wall_ms=None)], o))
    raises("a fractional start", lambda: match([_gt("t1", 0, 100, start_wall_ms=0.5)], o))
    raises("a boolean start", lambda: match([_gt("t1", 0, 100, start_wall_ms=True)], o))
    raises("an end before the start", lambda: match([_gt("t1", 100, 50)], o))
    raises("a session that is not an object", lambda: match([1], o))
    raises("a negative dwell_ms", lambda: match(g, [dict(o[0], dwell_ms=-1)]))
    raises("a missing t", lambda: match(g, [{"dwell_ms": 1}]))
    raises("a string t", lambda: match(g, [dict(o[0], t="1")]))
    raises("iou_min = 0", lambda: match(g, o, iou_min=0))
    raises("iou_min = 1.5", lambda: match(g, o, iou_min=1.5))
    raises("a boolean iou_min", lambda: match(g, o, iou_min=True))
    raises("a fractional obs_end_offset_ms", lambda: match(g, o, obs_end_offset_ms=1.5))
    return f


def _case_recount():
    """The theta recount. match()'s own split, merge, false-positive, and miss buckets never
    change; only split_rate / merge_rate and n_split_gt_recount / n_fp_as_split /
    n_merge_obs_recount / n_miss_as_merge move under this recount."""
    f = []
    # positive control: gt(0,1000) pairs with ob(0,600) (IoU 0.6); the leftover ob(600,1400) (length
    # 800) has 400/800 = 0.5 of its own length inside the paired ground-truth interval (match()
    # itself still reports 1 pair + 1 false positive).
    r = match([_gt("t1", 0, 1000)], [_ob(0, 600), _ob(600, 1400)])
    if _sizes(r) != (1, 0, 0, 0, 1):
        f.append("match()'s own split changed (the recount should never change match() itself): %r" % (_sizes(r),))
    m = detection_metrics(r)[0]
    if (m["n_split_gt_recount"], m["n_fp_as_split"], m["split_rate"]["x"], m["split_rate"]["n"]) != (1, 1, 1, 1):
        f.append("a leftover item with frac 0.5 is not recounted as a split under the default theta = 0.5: %r" % m)
    if m["n_split_gt"] != 0 or m["n_pairs"] != 1 or m["n_false_pos"] != 1:
        f.append("the original n_split_gt / n_pairs / n_false_pos moved under the recount: %r" % m)
    # negative control: raising theta to 0.6 puts frac 0.5 out of reach, so nothing is recounted
    m_strict = detection_metrics(r, theta=0.6)[0]
    if (m_strict["n_split_gt_recount"], m_strict["n_fp_as_split"], m_strict["split_rate"]["x"]) != (0, 0, 0):
        f.append("a frac-0.5 leftover was still recounted at theta = 0.6 (the threshold has no effect): %r" % m_strict)

    # merge: gt1(0,3000) pairs with ob(0,4000) (length 4000, IoU 0.75); gt2(3000,5000) (length 2000)
    # has 1000/2000 = 0.5 of its own length inside the paired observation's interval (match() itself
    # still reports 1 pair + 1 miss).
    r2 = match([_gt("t1", 0, 3000), _gt("t2", 3000, 5000)], [_ob(0, 4000)])
    if _sizes(r2) != (1, 0, 0, 1, 0):
        f.append("match()'s own merge changed: %r" % (_sizes(r2),))
    m2 = detection_metrics(r2)[0]
    if (m2["n_merge_obs_recount"], m2["n_miss_as_merge"], m2["merge_rate"]["x"], m2["merge_rate"]["n"]) != (1, 1, 1, 1):
        f.append("gt2 (frac 0.5) is not recounted as a merge under the default theta = 0.5: %r" % m2)
    if m2["n_merge_obs"] != 0 or m2["n_miss"] != 1:
        f.append("the original n_merge_obs / n_miss moved under the recount: %r" % m2)
    # negative control: at theta = 0.6, gt2's frac of 0.5 does not reach the threshold
    m2_strict = detection_metrics(r2, theta=0.6)[0]
    if (m2_strict["n_merge_obs_recount"], m2_strict["n_miss_as_merge"]) != (0, 0):
        f.append("a frac-0.5 miss was still recounted at theta = 0.6: %r" % m2_strict)

    # precision / recall / n_gt / n_obs do not depend on theta
    for th in (0.3, 0.5, 0.7, 0.9, 1.0):
        mm = detection_metrics(r, theta=th)[0]
        if (mm["n_pairs"], mm["n_gt"], mm["n_obs"], mm["precision"]["x"], mm["recall"]["x"]) != (1, 1, 2, 1, 1):
            f.append("n_pairs / n_gt / n_obs / precision / recall changed at theta = %r: %r" % (th, mm))

    def raises(name, fn):
        try:
            fn()
        except ValueError:
            return
        except Exception as e:
            f.append("%s: raised %r instead of ValueError" % (name, e))
            return
        f.append("%s: no exception was raised" % name)

    raises("theta = 0", lambda: detection_metrics(r, theta=0))
    raises("theta = 1.5", lambda: detection_metrics(r, theta=1.5))
    raises("a boolean theta", lambda: detection_metrics(r, theta=True))
    raises("recount_split_merge's theta = 0", lambda: recount_split_merge(r, theta=0))

    # order-independent: reversing each of result's lists gives the same answer
    r4 = match([_gt("t1", 0, 1000), _gt("t2", 2000, 2200), _gt("t3", 3000, 3200)],
               [_ob(0, 600), _ob(600, 1000), _ob(9000, 9200)])
    base = _dump(recount_split_merge(r4))
    shuffled = {"pairs": list(reversed(r4["pairs"])), "split": list(r4["split"]), "merge": list(r4["merge"]),
                "miss": list(reversed(r4["miss"])), "false_pos": list(reversed(r4["false_pos"]))}
    if _dump(recount_split_merge(shuffled)) != base:
        f.append("recount_split_merge depends on result's ordering")
    return f


def _case_merge_gt_per_stratum():
    """Each of a merge's ground-truth sessions (not only the first) counts merge_gt in its own
    stratum. merge_obs (the single observation) is still counted only in the first ground-truth
    session's stratum, as before."""
    f = []
    # gt1 and gt2 each individually have IoU < 0.5 (0.4), so this is a genuine merge from
    # _resolve_groups (not the imbalanced-merge case that instead becomes a pair + a miss).
    g1, g2 = _gt("t1", 20000, 21000), _gt("t2", 21500, 22500)
    r = match([g1, g2], [_ob(20000, 22500)])
    if _sizes(r) != (0, 0, 1, 0, 0):
        f.append("the merge baseline counts are wrong: %r" % (_sizes(r),))
        return f
    strata = {"gt": lambda s: "A" if s["trial_id"] == "t1" else "B", "obs": lambda _o: "A"}
    by = dict((x["stratum"], x) for x in detection_metrics(r, strata))
    if by.get("A", {}).get("n_merge_gt") != 1 or by.get("B", {}).get("n_merge_gt") != 1:
        f.append("the merge's second ground-truth session is not counted in its own stratum: %r" % (by,))
    if by["A"]["n_merge_obs"] != 1 or by.get("B", {}).get("n_merge_obs") != 0:
        f.append("the merge's single observation should be counted only in the first ground truth's stratum: %r" % (by,))
    if by["A"]["n_gt"] != 1 or by["B"]["n_gt"] != 1:
        f.append("the per-stratum n_gt is wrong (each should be 1): %r" % (by,))
    return f


def _case_f1_interval():
    """F1's lo/hi are always None (correlated successes are not handled by a Wilson interval),
    without exception even at zero counts or when everything pairs; value remains usable as a point
    estimate."""
    f = []
    gts = _world(20, 31)
    obs_full = [_ob(g["start_wall_ms"], g["end_wall_ms"]) for g in gts]
    results = [match([], []), match([_gt("t1", 0, 10)], []), match([], [_ob(0, 10)]),
               match([_gt("t1", 0, 10)], [_ob(0, 10)]), match(gts, obs_full), match(gts[:10], obs_full)]
    for r in results:
        for row in detection_metrics(r):
            if (row["f1"]["lo"], row["f1"]["hi"]) != (None, None):
                f.append("f1's interval is not None: %r" % (row["f1"],))
            if "note" not in row["f1"]:
                f.append("f1 is missing the note explaining why no interval is reported: %r" % (row["f1"],))
    m = detection_metrics(match(gts, obs_full))[0]
    if abs(m["f1"]["value"] - 1.0) > 1e-12 or (m["f1"]["x"], m["f1"]["n"]) != (40, 40):
        f.append("f1's value when everything pairs is not 1.0 (40/40): %r" % (m["f1"],))
    return f


def _case_t_gt_clock():
    """obs_interval() uses a row's t_gt_clock when present and falls back to t otherwise (existing
    callers that invoke match() / inject() directly never set t_gt_clock, so their behavior is
    unaffected)."""
    f = []
    plain = {"type": "ime_shadow", "t": 1000, "dwell_ms": 500, "nav": 0, "abandon": False}
    if obs_interval(plain) != (500, 1000):
        f.append("obs_interval does not use t when t_gt_clock is absent: %r" % (obs_interval(plain),))
    corrected = dict(plain, t_gt_clock=1300)
    if obs_interval(corrected) != (800, 1300):
        f.append("obs_interval does not use t_gt_clock when it is present: %r" % (obs_interval(corrected),))
    r = match([_gt("t1", 800, 1300)], [corrected])
    if not r["pairs"] or r["pairs"][0]["obs"]["t"] != 1000 or r["pairs"][0]["obs"]["t_gt_clock"] != 1300:
        f.append("match()'s output does not preserve t / t_gt_clock: %r" % (r["pairs"],))
    return f


def _case_start_while_open():
    """By default, n_start_while_open is diagnostic only (reported per stratum, never used to
    exclude anything). It becomes a trial-level exclusion reason only when
    config.drop_start_while_open is true."""
    f = []
    fx = _cli_fixture()
    obj = json.loads(fx["sessions"].decode("utf-8"))
    marked = 0
    for s in obj["definitions"]["all"]:
        if s.get("trial_id") in ("t00001", "t00002") and s["end_wall_ms"] is not None:
            s["n_start_while_open"] = 1
            marked += 1
    if marked != 2:
        f.append("the fixture's precondition no longer holds (t00001 / t00002 not found)")
        return f
    sessions_bytes = json.dumps(obj).encode("utf-8")

    default_out = evaluate(sessions_bytes, "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"])
    if default_out["diagnostics"]["start_while_open"]["n"] != 2:
        f.append("the diagnostic count is not 2 under the default (no dropping): %r" % default_out["diagnostics"]["start_while_open"])
    if "start_while_open" in default_out["excluded"]["gt"]["by_reason"]:
        f.append("an n_start_while_open exclusion reason appears under the default (it should not)")
    if default_out["used"]["n_gt"] != 88:
        f.append("sessions with n_start_while_open are excluded from matching under the default: %r" % default_out["used"])

    cfg = json.loads(fx["config"].decode("utf-8"))
    cfg["drop_start_while_open"] = True
    drop_out = evaluate(sessions_bytes, "all", fx["obs"], fx["plan"], fx["oracle"], json.dumps(cfg).encode("utf-8"))
    if drop_out["excluded"]["gt"]["by_reason"].get("start_while_open") != 2:
        f.append("the exclusion reason does not appear when drop_start_while_open = true: %r" % drop_out["excluded"])
    if drop_out["used"]["n_gt"] != 86:
        f.append("the matched set does not shrink even when drop_start_while_open = true: %r" % drop_out["used"])
    if drop_out["diagnostics"]["start_while_open"]["n"] != 0:
        f.append("the diagnostic count is not 0 after dropping (it should count only kept_gt): %r" % drop_out["diagnostics"])
    return f


def _jl(rows):
    return ("\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n").encode("utf-8")


def _cli_fixture():
    """A synthetic plan, oracle, ground truth, and observation set spanning 3 blocks x 30 trials
    each. The placement of special trials corresponds to what _case_cli expects."""
    T0 = 1700000000000
    plan_rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1, "generator_version": "0",
                  "config_sha256": "0" * 64, "recipes_sha256": "0" * 64, "corpus_sha256": "0" * 64}]
    trials = []
    tno = 0
    for b in range(3):
        plan_rows.append({"kind": "block", "block_id": b, "speed": SPEEDS[b], "reset_ime": False, "marker": [],
                          "marker_gap_ms": 0})
        for k in range(30):
            tno += 1
            begin = T0 + b * 2000000 + k * 8000
            trials.append(("t%05d" % tno, b, k, begin, begin + 6000))
            plan_rows.append({"kind": "trial", "trial_id": "t%05d" % tno, "block_id": b, "speed": SPEEDS[b],
                              "scenario": "S%d" % (k % 3 + 1)})
    plan_bytes = _jl(plan_rows)
    orows = [{"kind": "header", "format": "oracle/1", "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
              "os": "macos", "emitter_version": "0", "backend": "dryrun"}]

    def orow(kind, t, **kw):
        return dict(kw, kind=kind, t_wall_ms=t, t_mono_ns=t * 1000000)

    for b in range(3):
        mine = [t for t in trials if t[1] == b]
        orows.append(orow("block_begin", mine[0][3] - 100, block_id=b))
        for tid, _b, _k, begin, end in mine:
            orows.append(orow("trial_begin", begin, trial_id=tid))
            orows.append(orow("trial_end", end, trial_id=tid))
        orows.append(orow("block_end", mine[-1][4] + 100, block_id=b))
    oracle_bytes = _jl(orows)

    sessions, obs = [], []
    for idx, (tid, b, k, begin, _end) in enumerate(trials):
        shift = 700 if b == 1 else 0            # block 1's observation-side clock runs 700 ms ahead
        length = 900 + (idx * 137) % 1500
        lo = begin + 500
        if tid == "t00016":                      # two sessions in one trial -> observed as a single merged row
            sessions += [_gt(tid, lo, lo + 1000), _gt(tid, lo + 1200, lo + 2200)]
            obs.append(_ob(lo + shift, lo + 2200 + shift))
        elif tid == "t00021":                    # split into 3 fragments
            sessions.append(_gt(tid, lo, lo + 3000))
            obs += [_ob(lo + i * 1000 + shift, lo + (i + 1) * 1000 + shift) for i in range(3)]
        elif tid == "t00011":                    # a miss
            sessions.append(_gt(tid, lo, lo + length))
        elif tid == "t00090":                    # the log ends before the session closes
            sessions.append(_gt(tid, lo, None, outcome="open_at_end", excluded_by=["log_truncated"]))
            obs.append(_ob(lo + shift, lo + 400 + shift))
        elif tid in ("t00006", "t00007"):        # excluded by a clock jump
            sessions.append(_gt(tid, lo, lo + length, excluded_by=["clock_jump"]))
            obs.append(_ob(lo + shift, lo + length + shift))
        else:
            sessions.append(_gt(tid, lo, lo + length))
            obs.append(_ob(lo + shift, lo + length + shift))
        if tid == "t00045":                      # a false positive inside a trial (its stratum comes from the trial)
            obs.append(_ob(begin + 4500 + shift, begin + 5000 + shift))
    obs.append(_ob(T0 - 3000, T0 - 2000))        # a false positive before any block (attributable to no trial)
    obs_rows = [{"type": "meta", "format": "obs/1", "os": "macos", "logger_version": "0", "manifest_sha256": "0" * 64,
                 "capabilities": {"unconditional": ["key", "ime_shadow"], "threshold_gated": [], "config_gated": []}},
                {"type": "key", "t": T0, "category": "alphabetic", "press": True}] + obs
    excluded = [{"type": "clock_jump", "log": "oracle", "trial_ids": ["t00006", "t00007"]},
                {"type": "log_truncated", "log": "gt", "trial_ids": ["t00090"]}]
    sessions_obj = {"src": "mac_nstic", "rules_sha256": "0" * 64,
                    "definitions": {"all": sessions, "macos_def": sessions, "windows_def": None},
                    "oracle_agreement": {"plan_available": True, "n_trials": 90, "rate": 0.5}, "excluded": excluded}
    return {"sessions": json.dumps(sessions_obj, indent=2).encode("utf-8"), "obs": _jl(obs_rows), "plan": plan_bytes,
            "oracle": oracle_bytes, "config": json.dumps({"clock_offsets_ms": {"1": 700}, "cell": "mac-std"}).encode("utf-8")}


def _prediction_fixture(fx, long_trials=(), carried=(), none_started=(), closing=(), received=None):
    """A spec_predict/2 object for _cli_fixture's oracle and plan (only the fields the matcher reads).
    received defaults to none_started (a trial that started nothing received a carried session)."""
    tids = ["t%05d" % i for i in range(1, 91)]
    received = none_started if received is None else received
    by = dict((t, {"long": t in long_trials, "carried_out": t in carried, "n_started": 0 if t in none_started else 1,
                   "received_carry": t in received, "closing_key_in_reading": t in closing, "sessions": []})
              for t in tids)
    obj = {"format": PREDICTION_FORMAT, "limit_ms": 20000,
           "inputs_sha256": {"oracle": hashlib.sha256(fx["oracle"]).hexdigest(), "plan": hashlib.sha256(fx["plan"]).hexdigest()},
           "trials": {"n": len(tids), "by_trial": by}, "sessions": []}
    return json.dumps(obj, sort_keys=True).encode("utf-8")


def _case_prediction_scope():
    """The scopes partition the kept trials by the prediction's long trials (under_limit + over_limit =
    all_trials, including a false positive inside a long trial and a merge), all_trials with a
    prediction gives the same metrics as without one, the layers count only their trials, and a missing
    or foreign prediction is refused."""
    f = []
    fx = _cli_fixture()
    long_t = ("t00016", "t00045", "t00050")
    pb = _prediction_fixture(fx, long_trials=long_t, carried=("t00016",), none_started=("t00017",),
                             closing=("t00021", "t00017"))

    def ev(scope, pred=pb):
        return evaluate(fx["sessions"], "macos_def", fx["obs"], fx["plan"], fx["oracle"], fx["config"], pred, scope)

    plain, full, under, over = ev("all_trials", None), ev("all_trials"), ev("under_limit"), ev("over_limit")
    if full["metrics"] != plain["metrics"] or plain["diagnostics"]["prediction_layers"] is not None:
        f.append("all_trials with a prediction should give the metrics of no prediction")
    if under["scope"]["long_trials"] != list(long_t) or under["scope"]["name"] != "under_limit":
        f.append("scope.long_trials / name: %r" % under["scope"])
    # t00016: two sessions and one merged row; t00045: one pair and a false positive; t00050: one pair
    if over["used"] != {"n_gt": 4, "n_obs": 4} or under["scope"]["set_aside"] != {"n_gt": 4, "n_obs": 4}:
        f.append("over_limit used %r / under_limit set aside %r" % (over["used"], under["scope"]["set_aside"]))
    for k in ("n_gt", "n_obs"):
        if under["used"][k] + over["used"][k] != full["used"][k]:
            f.append("under_limit + over_limit should partition %s" % k)
    po, pu, pf = over["metrics"]["pooled"], under["metrics"]["pooled"], full["metrics"]["pooled"]
    if (po["n_merge_obs"], po["n_false_pos"], po["n_pairs"]) != (1, 1, 2) or pu["n_merge_obs"] != 0:
        f.append("over_limit should hold the merge, the false positive and two pairs: %r" % po)
    if pu["n_pairs"] + po["n_pairs"] != pf["n_pairs"]:
        f.append("the pairs of the two scopes should add up to all_trials")
    lay = full["diagnostics"]["prediction_layers"]
    if sorted(lay) != sorted(PREDICTION_LAYERS):
        f.append("layers: %r" % sorted(lay))
    else:
        if lay["carried_out"]["n_trials"] != 1 or lay["carried_out"]["pooled"]["n_merge_gt"] != 2:
            f.append("carried_out layer: %r" % lay["carried_out"]["pooled"])
        cl = lay["closing_key_in_reading"]["pooled"]
        if lay["closing_key_in_reading"]["n_trials"] != 2 or (cl["n_split_gt"], cl["n_pairs"], cl["n_gt"]) != (1, 1, 2):
            f.append("closing layer (a split and a pair): %r" % cl)
        if lay["none"]["n_trials"] != 87 or lay["received_carry"]["n_trials"] != 1:
            f.append("none / received_carry n_trials: %r / %r" % (lay["none"]["n_trials"], lay["received_carry"]["n_trials"]))
        none_ids = under["scope"]["layer_none_trials"]
        if not isinstance(none_ids, list) or len(none_ids) != 87 or none_ids != sorted(none_ids) \
                or "t00016" in none_ids or "t00017" in none_ids or "t00021" in none_ids:
            f.append("scope.layer_none_trials should list the 87 trials of no layer: %r" % (none_ids,))
        if plain["scope"]["layer_none_trials"] is not None:
            f.append("scope.layer_none_trials should be null without a prediction")
        # uncontaminated: neither carried_out (t00016) nor received_carry (t00017); the closing-key trial
        # t00021 stays in, so the list is the 88 trials that are in no carry layer
        unc = under["scope"]["uncontaminated_trials"]
        if not isinstance(unc, list) or len(unc) != 88 or unc != sorted(unc) or "t00016" in unc or \
                "t00017" in unc or "t00021" not in unc or set(none_ids) - set(unc):
            f.append("scope.uncontaminated_trials should list the 88 trials in no carry layer: %r" % (unc,))
        if plain["scope"]["uncontaminated_trials"] is not None:
            f.append("scope.uncontaminated_trials should be null without a prediction")
        # received_carry comes from the prediction's flag, not from n_started: a trial that received a
        # carried session and also started one of its own is in the layer
        flag = ev("all_trials", _prediction_fixture(fx, received=("t00030",)))["diagnostics"]["prediction_layers"]
        if flag["received_carry"]["n_trials"] != 1 or flag["none"]["n_trials"] != 89:
            f.append("received_carry should follow the flag: %r / %r" % (flag["received_carry"]["n_trials"],
                                                                         flag["none"]["n_trials"]))
        if lay["none"]["pooled"]["n_false_pos"] != pf["n_false_pos"] - 1:
            f.append("the none layer should hold every false positive inside a trial of no layer")
        by = lay["closing_key_in_reading"]["by_speed"]
        if [(r["stratum"], r["n_gt"]) for r in by] != [({"speed": "slow"}, 2)]:
            f.append("a layer's speeds should hold only its trials (both are in the slow block): %r" % by)
    for name, fn in (("under_limit without a prediction", lambda: ev("under_limit", None)),
                     ("an unknown scope", lambda: ev("some")),
                     ("a prediction of another oracle", lambda: evaluate(fx["sessions"], "macos_def", fx["obs"], fx["plan"],
                                                                       fx["oracle"] + b"\n", fx["config"], pb, "under_limit")),
                     ("a malformed by_trial", lambda: ev("under_limit", pb.replace(b'"long": false', b'"long": 0', 1))),
                     ("a by_trial without received_carry",
                      lambda: ev("under_limit", pb.replace(b'"received_carry": false, ', b'', 1))),
                     ("another format", lambda: ev("under_limit", pb.replace(PREDICTION_FORMAT.encode(), b"spec_predict/0")))):
        try:
            fn()
            f.append("%s was not refused" % name)
        except InputError:
            pass
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as d:
        paths = {}
        for k in ("sessions", "obs", "plan", "oracle", "config"):
            paths[k] = os.path.join(d, k)
            with open(paths[k], "wb") as fh:
                fh.write(fx[k])
        paths["prediction"] = os.path.join(d, "prediction")
        with open(paths["prediction"], "wb") as fh:
            fh.write(pb)
        base = ["--sessions", paths["sessions"], "--definition", "macos_def", "--obs", paths["obs"], "--plan", paths["plan"],
                "--oracle", paths["oracle"], "--config", paths["config"]]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc_ok = main(base + ["--prediction", paths["prediction"], "--scope", "over_limit", "--out", os.path.join(d, "o1")])
            rc_bad = main(base + ["--scope", "over_limit", "--out", os.path.join(d, "o2")])
        if rc_ok != 0 or json.loads(open(os.path.join(d, "o1"), encoding="utf-8").read())["used"] != over["used"]:
            f.append("the CLI with --prediction / --scope should write the over_limit result")
        if rc_bad != 2:
            f.append("the CLI should exit 2 for --scope over_limit without --prediction")
    return f


def _case_cli():
    import contextlib
    import io
    import subprocess
    f = []
    fx = _cli_fixture()
    ev = lambda **kw: evaluate(fx["sessions"], kw.get("definition", "all"), fx["obs"], fx["plan"], fx["oracle"],
                               kw.get("config", fx["config"]))
    out = ev()
    p = out["metrics"]["pooled"]
    if (p["n_pairs"], p["n_split_gt"], p["n_split_obs"], p["n_merge_obs"], p["n_merge_gt"], p["n_miss"], p["n_false_pos"]) \
            != (84, 1, 3, 1, 2, 1, 2) or (p["n_gt"], p["n_obs"]) != (88, 90):
        f.append("the overall counts are not (84,1,3,1,2,1,2) with n_gt 88 and n_obs 90 (clock correction, exclusion, and pooling across strata): %r" % p)
    if any(x["iou"] != 1.0 for x in out["matching"]["pairs"]):
        f.append("after clock correction, every pair should reach IoU 1.0")
    # "t" never changes; the corrected time always lives in "t_gt_clock".
    moved = [p["obs"] for p in out["matching"]["pairs"] if p["obs"]["t"] != p["obs"]["t_gt_clock"]]
    all_obs_rows = [p["obs"] for p in out["matching"]["pairs"]] + \
        [o for sp in out["matching"]["split"] for o in sp["obs"]] + \
        [mg["obs"] for mg in out["matching"]["merge"]] + out["matching"]["false_pos"]
    if not all("t_gt_clock" in r for r in all_obs_rows) or len(moved) != 30 or \
            any(r["t"] - r["t_gt_clock"] != 700 for r in moved):
        f.append("the corrected observations (30 pairs in block 1) do not have t_gt_clock = t - 700 without rewriting t: %d rows" % len(moved))
    if out["config"]["uncorrected_blocks"] != [0, 2]:
        f.append("uncorrected_blocks is not [0, 2] (only block 1 is present in clock_offsets_ms): %r" % out["config"]["uncorrected_blocks"])
    if out["config"]["theta"] != 0.5 or out["config"]["drop_start_while_open"] is not False:
        f.append("the defaults for theta / drop_start_while_open are wrong: %r" % out["config"])
    if out["diagnostics"]["start_while_open"] != {"n": 0, "by_stratum": []}:
        f.append("the diagnostic is not empty when every n_start_while_open is 0: %r" % out["diagnostics"])
    if out["excluded"] != {"gt": {"n": 3, "by_reason": {"clock_jump": 2, "log_truncated": 1, "open_at_end": 1}},
                           "obs": {"n": 3, "by_reason": {"clock_jump": 2, "log_truncated": 1}},
                           "trials": {"n": 3, "by_type": {"clock_jump": 2, "log_truncated": 1}}}:
        f.append("the exclusion counts do not break down by reason: %r" % (out["excluded"],))
    if (out["input"], out["used"]) != ({"n_gt": 91, "n_obs": 93}, {"n_gt": 88, "n_obs": 90}):
        f.append("the input / used counts are wrong: %r %r" % (out["input"], out["used"]))
    strata = out["metrics"]["by_stratum"]
    if len(strata) != 10 or sum(s["n_pairs"] for s in strata) != 84:
        f.append("there should be 10 strata (3 scenarios x 3 speeds, plus outside-any-trial) with pairs summing to 84: %d strata" % len(strata))
    s1 = [s for s in strata if s["stratum"] == {"cell": "mac-std", "scenario": "S1", "speed": "slow"}]
    if len(s1) != 1 or (s1[0]["n_pairs"], s1[0]["n_merge_obs"], s1[0]["n_merge_gt"], s1[0]["n_gt"], s1[0]["n_obs"]) != (8, 1, 2, 10, 9):
        f.append("the (mac-std, S1, slow) stratum's counts are wrong: %r" % (s1,))
    outside = [s for s in strata if s["stratum"] == {"cell": "mac-std", "scenario": None, "speed": None}]
    if len(outside) != 1 or outside[0]["n_false_pos"] != 1:
        f.append("the false positive that belongs to no trial is not the single entry in the (cell, null, null) stratum")
    # t00045 is trial k=14 of block 1 (mid), which is scenario S3
    mid = [s for s in strata if s["stratum"] == {"cell": "mac-std", "scenario": "S3", "speed": "mid"}]
    if len(mid) != 1 or mid[0]["n_false_pos"] != 1:
        f.append("the false positive inside t00045 does not appear in the (S3, mid) stratum: %r" % (mid,))
    if sum(s["n_false_pos"] for s in strata) != 2:
        f.append("there are not 2 false positives across all strata (1 outside any trial, 1 inside t00045)")
    # without the correction, block 1 fails to pair (evidence that the correction matters)
    no_cfg = ev(config=json.dumps({"cell": "mac-std"}).encode("utf-8"))
    if no_cfg["metrics"]["pooled"]["n_pairs"] >= 84:
        f.append("removing clock_offsets_ms does not reduce pairs (the correction has no effect)")
    # also works with no configuration at all (cell defaults to the plan's os)
    default = evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], None)
    if default["config"]["cell"] != "macos" or default["config"]["iou_min"] != 0.5:
        f.append("the defaults with no configuration (cell = the plan's os, iou_min 0.5) are wrong")

    # byte-identical output (twice on the same input, to different output paths, and via a subprocess)
    with tempfile.TemporaryDirectory() as d:
        paths = {}
        for k in ("sessions", "obs", "plan", "oracle", "config"):
            paths[k] = os.path.join(d, k + ".dat")
            with open(paths[k], "wb") as fh:
                fh.write(fx[k])
        cfg_path = os.path.join(d, "cfg.json")
        with open(cfg_path, "wb") as fh:
            fh.write(fx["config"])

        def run(out_name, **over):
            a = dict(sessions=paths["sessions"], definition="all", obs=paths["obs"], plan=paths["plan"],
                     oracle=paths["oracle"], config=cfg_path, out=os.path.join(d, out_name))
            a.update(over)
            argv = []
            for k, v in a.items():
                if v is not None:
                    argv += ["--" + k, v]
            buf, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                    rc = main(argv)
            except SystemExit as e:
                rc = e.code
            return rc, err.getvalue()

        rc1, _ = run("o1.json")
        rc2, _ = run("o2.json")
        if rc1 != 0 or rc2 != 0:
            f.append("the CLI does not exit 0: %r %r" % (rc1, rc2))
        else:
            b1, b2 = open(os.path.join(d, "o1.json"), "rb").read(), open(os.path.join(d, "o2.json"), "rb").read()
            if b1 != b2 or b1 != render(out):
                f.append("the CLI's output is not byte-identical across two runs, or differs from evaluate's")
            if b"\r" in b1 or b1.startswith(b"\xef\xbb\xbf") or d.encode("utf-8") in b1:
                f.append("the output contains a CR, a BOM, or an input path")
            sub = subprocess.run([sys.executable, os.path.abspath(__file__), "--sessions", paths["sessions"],
                                  "--definition", "all", "--obs", paths["obs"], "--plan", paths["plan"],
                                  "--oracle", paths["oracle"], "--config", cfg_path, "--out", os.path.join(d, "o3.json")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if sub.returncode != 0 or open(os.path.join(d, "o3.json"), "rb").read() != b1:
                f.append("the CLI run as a subprocess does not exit 0, or is not byte-identical: %r" % sub.stderr[-200:])

        # a malformed input exits 2 (a rejection, not a crash)
        def rc_of(name, want, **over):
            rc, err = run(name, **over)
            if rc != want or "Traceback" in err:
                f.append("%s: exit %r (expected %d) %s" % (name, rc, want, err[-120:]))

        rc_of("e1.json", 2, definition="windows_def")
        rc_of("e2.json", 2, out=paths["obs"])
        rc_of("e3.json", 2, plan=os.path.join(d, "no-such-file"))
        bad_plan = os.path.join(d, "bad_plan.dat")
        with open(bad_plan, "wb") as fh:
            fh.write(fx["plan"] + b"\n")
        rc_of("e4.json", 2, plan=bad_plan)
        bad_cfg = os.path.join(d, "bad_cfg.json")
        for name, body in (("unknown key", {"iou": 0.5}), ("unknown block", {"clock_offsets_ms": {"9": 1}}),
                           ("float offset", {"obs_end_offset_ms": 1.5}), ("iou 0", {"iou_min": 0}),
                           ("theta 0", {"theta": 0}), ("a non-boolean drop_start_while_open", {"drop_start_while_open": 1})):
            with open(bad_cfg, "wb") as fh:
                fh.write(json.dumps(body).encode("utf-8"))
            rc_of("e5.json", 2, config=bad_cfg)
        old = json.loads(fx["sessions"].decode("utf-8"))
        for s in old["definitions"]["all"]:
            s.pop("excluded_by")
        old_path = os.path.join(d, "old.json")
        with open(old_path, "wb") as fh:
            fh.write(json.dumps(old).encode("utf-8"))
        rc_of("e6.json", 2, sessions=old_path)
        no_swo = json.loads(fx["sessions"].decode("utf-8"))
        for s in no_swo["definitions"]["all"]:
            s.pop("n_start_while_open")
        no_swo_path = os.path.join(d, "no_swo.json")
        with open(no_swo_path, "wb") as fh:
            fh.write(json.dumps(no_swo).encode("utf-8"))
        rc_of("e6b.json", 2, sessions=no_swo_path)
        rc_of("e7.json", 2, obs=None)
        rc_of("e8.json", 2, definition="bogus")
    return f


def _case_d10():
    """Deviation D10: a recounted merge whose missed session lies in another stratum than the paired one."""
    f = []
    # gt t1 (0, 3000) pairs with ob (0, 4000); gt t2 (3000, 5000) is missed and has 0.5 of its length inside the
    # paired observation, so the recount finds a merge. Strata by trial: t2's stratum holds no observation row.
    r = match([_gt("t1", 0, 3000), _gt("t2", 3000, 5000)], [_ob(0, 4000)])
    strata = {"gt": lambda s: s["trial_id"], "obs": lambda _r: "t1"}
    saved = os.environ.pop(MERGE_RECOUNT_ENV, None)
    try:
        rows = dict((m["stratum"], m) for m in detection_metrics(r, strata))
        t2 = rows.get("t2")
        if t2 is None or (t2["n_obs"], t2["n_merge_obs_recount"], t2["n_miss_as_merge"]) != (0, 1, 1):
            f.append("frozen rule: the recounted merge is not in the missed session's stratum: %r" % (t2,))
        elif (t2["merge_rate"]["value"], t2["merge_rate"]["lo"], t2["merge_rate"]["note"]) != (
                None, None, NUMERATOR_ABOVE_DENOMINATOR):
            f.append("frozen rule: 1 / 0 is not reported without a value and with the note: %r" % (t2["merge_rate"],))
        if rows.get("t1", {}).get("n_merge_obs_recount") != 0:
            f.append("frozen rule: the recounted merge was also counted in the paired stratum")
        os.environ[MERGE_RECOUNT_ENV] = "obs"
        rows = dict((m["stratum"], m) for m in detection_metrics(r, strata))
        t1, t2 = rows.get("t1"), rows.get("t2")
        if t1 is None or (t1["merge_rate"]["x"], t1["merge_rate"]["n"]) != (1, 1) or "note" in t1["merge_rate"]:
            f.append("obs rule: the recounted merge is not counted with its paired observation: %r" % (t1,))
        if t2 is None or (t2["n_merge_obs_recount"], t2["n_miss_as_merge"], t2["merge_rate"]["x"]) != (0, 1, 0):
            f.append("obs rule: the missed session's stratum still holds the merge, or lost n_miss_as_merge: %r" % (t2,))
        pooled = detection_metrics(r)[0]
        os.environ.pop(MERGE_RECOUNT_ENV)
        if pooled != detection_metrics(r)[0]:
            f.append("the single stratum differs between the two rules")
        os.environ[MERGE_RECOUNT_ENV] = "x"
        try:
            detection_metrics(r)
            f.append("an unknown value of %s was accepted" % MERGE_RECOUNT_ENV)
        except InputError:
            pass
    finally:
        os.environ.pop(MERGE_RECOUNT_ENV, None)
        if saved is not None:
            os.environ[MERGE_RECOUNT_ENV] = saved
    # a numerator within its denominator keeps the Wilson interval
    p = _prop(1, 2)
    if p["lo"] is None or "note" in p:
        f.append("_prop(1, 2) lost its interval: %r" % (p,))
    return f


CASES = [
    ("five types (exact match, split, merge, miss, false positive)", _case_five_types),
    ("definitional properties (adjacent fragments, imbalanced merges, short sessions)", _case_definition_properties),
    ("IoU boundaries (>= 0.5, iou_min, zero length)", _case_iou_boundary),
    ("tie-breaking determinism (independent of input order)", _case_tie_determinism),
    ("positive control (removing or shifting one observation row degrades the metrics)", _case_positive_control),
    ("negative control (a 60 s shift or shuffling reaches chance level)", _case_negative_control),
    ("byte-identical output (two runs on the same input)", _case_byte_identical),
    ("detection metrics (counts, Wilson intervals, strata)", _case_metrics),
    ("obs_end_offset_ms (correcting for the end-of-session lag)", _case_offset),
    ("input validation", _case_validation),
    ("the theta recount (positive / negative controls for split and merge)", _case_recount),
    ("each merged ground truth is counted in its own stratum", _case_merge_gt_per_stratum),
    ("F1's interval is always None", _case_f1_interval),
    ("t_gt_clock takes priority; t is never rewritten", _case_t_gt_clock),
    ("n_start_while_open's diagnostic role and drop_start_while_open", _case_start_while_open),
    ("prediction: scopes partition the trials, layers count only their trials, refusals", _case_prediction_scope),
    ("CLI (exclusion, strata, clock correction, byte-identical output, exit 2)", _case_cli),
    ("deviation D10 (a recounted merge across strata: no value under the frozen rule, the paired stratum under obs)",
     _case_d10),
]


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
    ap = argparse.ArgumentParser(description="Matching and detection metrics (ground-truth sessions against observed ime_shadow rows)")
    ap.add_argument("--sessions")
    ap.add_argument("--definition", choices=DEFINITIONS)
    ap.add_argument("--obs")
    ap.add_argument("--plan")
    ap.add_argument("--oracle")
    ap.add_argument("--config")
    ap.add_argument("--prediction")
    ap.add_argument("--scope", choices=SCOPES, default="all_trials")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.sessions and args.definition and args.obs and args.plan and args.oracle and args.out):
        ap.error("--sessions / --definition / --obs / --plan / --oracle / --out are required (--config and --prediction are optional)")
    for inp in (args.sessions, args.obs, args.plan, args.oracle, args.config, args.prediction):
        if inp and _same_file(inp, args.out):
            print("match: --out names the same file as an input (%s)" % inp, file=sys.stderr)
            return 2
    try:
        out = evaluate(_read(args.sessions, "sessions"), args.definition, _read(args.obs, "obs"),
                       _read(args.plan, "plan"), _read(args.oracle, "oracle"),
                       None if args.config is None else _read(args.config, "config"),
                       None if args.prediction is None else _read(args.prediction, "prediction"), args.scope)
        _write_atomic(args.out, render(out))
    except (InputError, ValueError) as e:
        print("match: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("match: could not write output (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    p = out["metrics"]["pooled"]
    if p is not None:
        print("match: %d ground truth, %d observations -> %d pairs (recall %s, precision %s); excluded %d ground truth / %d observations" % (
            p["n_gt"], p["n_obs"], p["n_pairs"], _fmt(p["recall"]["value"]), _fmt(p["precision"]["value"]),
            out["excluded"]["gt"]["n"], out["excluded"]["obs"]["n"]))
    return 0


def _fmt(v):
    return "n/a" if v is None else "%.3f" % v


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
