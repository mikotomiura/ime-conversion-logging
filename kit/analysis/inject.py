#!/usr/bin/env python3
# kit: selftest
"""Build a synthetic observation log with known injected errors (used to test whether the matcher
recovers its calibration parameters). Uses only the Python standard library.

  inject.py --selftest      fast default (a few seconds). The "parameter recovery" case is the
                              most important check.

  inject(gt_sessions, rates, timing, seed, iou_min=0.5) -> (obs_rows, truth)

    gt_sessions: an array of ground-truth sessions (the contents of one of sessions.json's
      definitions[<definition>] entries; entries whose start / end are integers).
    rates = {"miss", "split", "merge", "false_pos"} (each between 0 and 1; miss + split + merge <= 1).
      One draw is made independently for each ground-truth session: miss drops the observation,
      split breaks the observation into three or more fragments, merge combines it with the next
      session into a single row (consuming two entries), and anything else emits a plain 1:1
      observation. Independently of that draw, false_pos adds, at that rate per ground-truth
      session, one observation row that overlaps none of the ground truth, placed in a gap between
      sessions.
    timing = {"bias_ms", "sd_ms", "end_lag_ms_max"}: the timing offset applied to an observation row
      is bias_ms + N(0, sd_ms) (truncated at +-4 sd), plus a uniformly distributed end-of-session lag
      U{0..end_lag_ms_max} (an integer). One offset is drawn per row and added only to the
      observation row's t (dwell_ms remains the session's true length), so the whole interval is
      translated as a unit; the fragments of a split share the same offset.
    seed: an integer. The only randomness used is random.Random(seed).random() (so the sequence is
      stable across versions).
    Return value: always this pair. obs_rows is an array of obs/1 ime_shadow rows
      ({"type", "t", "dwell_ms", "nav", "abandon"}), sorted by ascending t. nav / abandon mirror the
      ground truth's nav_true / outcome == "cancel" (for a split, nav is on the first fragment and
      abandon on the last; for a merge, nav is the sum and abandon comes from the later session).
      truth records the actual values injected:
      {"n_gt", "n_obs", "n_exact", "n_miss", "n_split", "n_split_obs", "n_merge", "n_merge_gt", "n_false_pos",
       "skipped": {"split", "merge", "false_pos"}, "risk": {"short", "crowded"}, "max_shift_ms", "rates", "timing",
       "expected": {"precision", "recall", "f1", "split_rate", "merge_rate", "miss_rate", "false_pos_rate"}}
    expected gives, for each metric that match.detection_metrics also reports, the {"x", "n"} computed
    from the actual injected counts.

What is injected (matched to the matcher's own definitions): the matcher counts a split or a merge
only when no individual fragment on its own reaches IoU >= iou_min (see the caveat in match.py's
docstring). Injected errors are therefore restricted to that type:
    - A split divides the session into ceil(1/iou_min) + 1 equal fragments (3 by default). Each
      fragment's IoU is at most 1/k, so no single offset can pair it on its own, and a split is only
      injected when the union's IoU stays at or above iou_min even under the worst-case offset
      (max_shift_ms).
    - A merge combines two adjacent sessions into a single row. It is only injected when neither
      session's individual IoU reaches iou_min (neither occupies at least half of the merged
      interval) and the union's IoU stays at or above iou_min even under the worst-case offset.
    - Whenever a session does not meet these conditions, it is left as an ordinary 1:1 observation
      instead, and the skip is counted in truth["skipped"] (so the actual injected count can fall
      short of the nominal rate).
  Preconditions the caller should satisfy for full recovery to be guaranteed (truth["risk"] reports
  how many sessions violate them): risk["short"] counts sessions whose length L is short enough
  (below max_shift_ms * (1 + iou_min) / (1 - iou_min)) that the worst-case offset alone could drop a
  plain 1:1 pairing's IoU below iou_min; risk["crowded"] counts adjacent pairs separated by less than
  max_shift_ms (an offset observation could then intrude into the neighboring ground-truth session).
  When both are zero, the matcher is guaranteed to recover the exact count of each injected error
  type (max_shift_ms is an upper bound on the worst-case offset after truncation).

Source: the design document's section on parameter recovery testing (positive and negative controls).
"""
import argparse
import json
import math
import os
import random
import sys
from statistics import NormalDist

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import match  # noqa: E402
import stats  # noqa: E402  statistics (Wilson intervals, Bland-Altman) are provided by stats.py and are not reimplemented here

RATE_KEYS = ("miss", "split", "merge", "false_pos")
TIMING_KEYS = ("bias_ms", "sd_ms", "end_lag_ms_max")
_TRUNC_SD = 4.0          # truncation bound, in standard deviations, for the normal offset (guarantees an upper bound on the worst-case offset)
_FP_MIN_LEN, _FP_MAX_LEN = 200, 1500
_NORMAL = NormalDist()


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_args(rates, timing, seed, iou_min):
    if not isinstance(rates, dict) or set(rates) != set(RATE_KEYS):
        raise ValueError("rates must have exactly these keys: %s" % ", ".join(RATE_KEYS))
    if any(not _is_num(rates[k]) or not (0.0 <= rates[k] <= 1.0) for k in RATE_KEYS):
        raise ValueError("each value in rates must be a number between 0 and 1")
    if rates["miss"] + rates["split"] + rates["merge"] > 1.0 + 1e-12:
        raise ValueError("rates: miss + split + merge must not exceed 1")
    if not isinstance(timing, dict) or set(timing) != set(TIMING_KEYS):
        raise ValueError("timing must have exactly these keys: %s" % ", ".join(TIMING_KEYS))
    if not _is_num(timing["bias_ms"]) or not _is_num(timing["sd_ms"]) or timing["sd_ms"] < 0:
        raise ValueError("timing: bias_ms must be a number and sd_ms a non-negative number")
    if type(timing["end_lag_ms_max"]) is not int or timing["end_lag_ms_max"] < 0:
        raise ValueError("timing: end_lag_ms_max must be a non-negative integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    match._check_iou_min(iou_min)


def _draw_shift(rng, timing):
    """The timing offset (an integer, ms) for one observation row: bias plus a truncated normal
    draw plus a uniformly distributed end-of-session lag."""
    z = 0.0
    if timing["sd_ms"] > 0:
        while True:
            u = rng.random()
            if 0.0 < u < 1.0:
                z = _NORMAL.inv_cdf(u)
                if abs(z) <= _TRUNC_SD:
                    break
    lag = int(rng.random() * (timing["end_lag_ms_max"] + 1)) if timing["end_lag_ms_max"] > 0 else 0
    return int(round(timing["bias_ms"] + timing["sd_ms"] * z)) + lag


def _max_shift(timing):
    """An upper bound on the absolute offset (accounting for truncation and rounding)."""
    b, s, lag = timing["bias_ms"], timing["sd_ms"], timing["end_lag_ms_max"]
    return int(math.ceil(max(abs(b - _TRUNC_SD * s), abs(b + _TRUNC_SD * s + lag)))) + 1


def _worst_pair_iou(length, w):
    """The worst-case IoU when an interval of the given length is translated by w."""
    return 0.0 if w >= length else (length - w) / float(length + w)


def _row(t, dwell, nav, abandon):
    return {"type": "ime_shadow", "t": t, "dwell_ms": dwell, "nav": nav, "abandon": abandon}


def _nav(s):
    v = s.get("nav_true")
    return v if type(v) is int and v >= 0 else 0


def inject(gt_sessions, rates, timing, seed, iou_min=0.5):
    """Build a synthetic observation log (obs_rows, truth) with known injected errors; see the
    module docstring."""
    _check_args(rates, timing, seed, iou_min)
    order = sorted(range(len(gt_sessions)),
                   key=lambda i: (match.gt_interval(gt_sessions[i], "gt_sessions[%d]" % i), i))
    gts = [gt_sessions[i] for i in order]
    iv = [match.gt_interval(s) for s in gts]
    n = len(gts)
    rng = random.Random(seed)
    w = _max_shift(timing)
    k_pieces = max(3, int(1.0 / iou_min) + 1)
    split_need = (w * (1.0 + iou_min) / (1.0 - iou_min)) if iou_min < 1.0 else float("inf")
    p_miss, p_split, p_merge = rates["miss"], rates["miss"] + rates["split"], rates["miss"] + rates["split"] + rates["merge"]

    rows = []
    c = {"exact": 0, "miss": 0, "split": 0, "split_obs": 0, "merge": 0, "fp": 0,
         "skip_split": 0, "skip_merge": 0, "skip_fp": 0, "risk_short": 0}
    i = 0
    while i < n:
        lo, hi = iv[i]
        length = hi - lo
        u = rng.random()
        if u < p_miss:
            c["miss"] += 1
            i += 1
            continue
        if u < p_split:
            if length >= k_pieces and length >= split_need:
                d = _draw_shift(rng, timing)
                cuts = [lo + (length * j) // k_pieces for j in range(k_pieces + 1)]
                for j in range(k_pieces):
                    rows.append(_row(cuts[j + 1] + d, cuts[j + 1] - cuts[j], _nav(gts[i]) if j == 0 else 0,
                                     j == k_pieces - 1 and gts[i].get("outcome") == "cancel"))
                c["split"] += 1
                c["split_obs"] += k_pieces
                i += 1
                continue
            c["skip_split"] += 1
        elif u < p_merge:
            ok = False
            if i + 1 < n and hi <= iv[i + 1][0]:
                la, lb, hull = length, iv[i + 1][1] - iv[i + 1][0], iv[i + 1][1] - lo
                i0 = la + lb - 2 * w
                ok = (hull > 0 and max(la, lb) / float(hull) < iou_min and i0 > 0
                      and i0 / float(hull + 2 * w) >= iou_min)
            if ok:
                d = _draw_shift(rng, timing)
                rows.append(_row(iv[i + 1][1] + d, iv[i + 1][1] - lo, _nav(gts[i]) + _nav(gts[i + 1]),
                                 gts[i + 1].get("outcome") == "cancel"))
                c["merge"] += 1
                i += 2
                continue
            c["skip_merge"] += 1
        d = _draw_shift(rng, timing)
        rows.append(_row(hi + d, length, _nav(gts[i]), gts[i].get("outcome") == "cancel"))
        c["exact"] += 1
        if _worst_pair_iou(length, w) < iou_min:
            c["risk_short"] += 1
        i += 1

    # false positives: placed where they overlap no ground truth (kept max_shift_ms + 10 ms away so an offset observation cannot reach them either)
    pad = w + 10
    usable = [g for g in range(n - 1) if (iv[g + 1][0] - iv[g][1]) - 2 * pad >= _FP_MIN_LEN]
    for _ in range(n):
        if not (rng.random() < rates["false_pos"]):
            continue
        if not usable:
            c["skip_fp"] += 1
            continue
        g = usable[int(rng.random() * len(usable))]
        avail = (iv[g + 1][0] - iv[g][1]) - 2 * pad
        d = _FP_MIN_LEN + int(rng.random() * (min(_FP_MAX_LEN, avail) - _FP_MIN_LEN + 1))
        start = iv[g][1] + pad + int(rng.random() * (avail - d + 1))
        rows.append(_row(start + d, d, 0, False))
        c["fp"] += 1

    rows.sort(key=lambda r: (r["t"], r["dwell_ms"], r["nav"], r["abandon"]))
    crowded = sum(1 for g in range(n - 1) if iv[g + 1][0] - iv[g][1] < w)
    n_obs = len(rows)
    truth = {
        "n_gt": n, "n_obs": n_obs, "n_exact": c["exact"], "n_miss": c["miss"], "n_split": c["split"],
        "n_split_obs": c["split_obs"], "n_merge": c["merge"], "n_merge_gt": 2 * c["merge"], "n_false_pos": c["fp"],
        "skipped": {"split": c["skip_split"], "merge": c["skip_merge"], "false_pos": c["skip_fp"]},
        "risk": {"short": c["risk_short"], "crowded": crowded}, "max_shift_ms": w,
        "rates": dict(rates), "timing": dict(timing),
        "expected": {
            "precision": {"x": c["exact"], "n": n_obs}, "recall": {"x": c["exact"], "n": n},
            "f1": {"x": 2 * c["exact"], "n": n + n_obs}, "split_rate": {"x": c["split"], "n": n},
            "merge_rate": {"x": c["merge"], "n": n_obs}, "miss_rate": {"x": c["miss"], "n": n},
            "false_pos_rate": {"x": c["fp"], "n": n_obs}},
    }
    return rows, truth


# ---------------------------------------------------------------- selftest

RATES = {"miss": 0.05, "split": 0.03, "merge": 0.03, "false_pos": 0.02}
TIMING = {"bias_ms": 20, "sd_ms": 50, "end_lag_ms_max": 100}


def _gt_population(n, seed, len_lo=1500, len_hi=6000, gap_lo=500, gap_hi=5000):
    """Synthetic ground truth: lengths between len_lo and len_hi ms, gaps between gap_lo and gap_hi
    ms (the defaults are comfortably larger than the worst-case offset of 321 ms)."""
    rng = random.Random(seed)
    t, out = 1700000000000, []
    for i in range(n):
        t += gap_lo + int(rng.random() * (gap_hi - gap_lo + 1))
        length = len_lo + int(rng.random() * (len_hi - len_lo + 1))
        out.append(match._gt("t%05d" % (i + 1), t, t + length, nav_true=i % 3,
                             outcome="cancel" if i % 7 == 0 else "commit"))
        t += length
    return out


def _recovery_failures(m, truth):
    """Whether the metric layer m (one layer of detection_metrics) recovers the injected values.
    Returns an array of descriptions for the items that fail to recover.

    f1 intentionally has no Wilson interval (its doubled pair count is not an independent-trials
    proportion -- see match._f1_prop), so it is checked on its count/value only, not on interval
    containment.
    """
    out = []
    for name, e in sorted(truth["expected"].items()):
        g = m[name]
        if (g["x"], g["n"]) != (e["x"], e["n"]):
            out.append("%s: the count %s/%s does not match the injected %s/%s" % (name, g["x"], g["n"], e["x"], e["n"]))
        p = e["x"] / float(e["n"])
        if name == "f1":
            if g["value"] is None or abs(g["value"] - p) > 1e-9:
                out.append("%s: the value %r does not match the injected rate %.4f" % (name, g["value"], p))
            continue
        if g["lo"] is None or not (g["lo"] <= p <= g["hi"]):
            out.append("%s: the Wilson 95%% interval [%s, %s] does not contain the injected rate %.4f" % (name, g["lo"], g["hi"], p))
    return out


def _case_parameter_recovery():
    """Parameter recovery: inject a miss rate of 5%, a split rate of 3%, a merge rate of 3%, and a
    false-positive rate of 2% into 2,000 ground-truth sessions, along with a timing bias of +20 ms,
    a standard deviation of 50 ms, and an end-of-session lag of 0 to 100 ms; run the matcher, and
    confirm that each metric's Wilson 95% interval contains the injected rate (computed from the
    actual injected counts), across five different seeds. In addition to interval containment, this
    also checks the raw counts themselves (a stronger condition than the interval) and recovery of
    the timing bias and spread (from the difference between paired session ends)."""
    f = []
    for seed in (1, 2, 3, 4, 5):
        gts = _gt_population(2000, 100 + seed)
        obs, truth = inject(gts, RATES, TIMING, seed)
        tag = "seed %d" % seed
        if truth["risk"] != {"short": 0, "crowded": 0}:
            f.append("%s: the precondition (risk) is not zero: %r" % (tag, truth["risk"]))
        # confirm the errors were actually injected (near the nominal rate; a zero count must not pass)
        n = truth["n_gt"]
        if not (0.03 <= truth["n_miss"] / n <= 0.07):
            f.append("%s: the miss count is not close to the nominal 5%%: %d / %d" % (tag, truth["n_miss"], n))
        if not (truth["n_split"] >= 25 and truth["n_merge"] >= 25 and truth["n_false_pos"] >= 15):
            f.append("%s: not enough splits / merges / false positives were injected: %r" % (
                tag, (truth["n_split"], truth["n_merge"], truth["n_false_pos"])))
        for offset in (0, -50):     # -50 cancels out the mean end-of-session lag (50 ms)
            r = match.match(gts, obs, obs_end_offset_ms=offset)
            m = match.detection_metrics(r)[0]
            for msg in _recovery_failures(m, truth):
                f.append("%s offset %d: %s" % (tag, offset, msg))
            if (len(r["split"]), len(r["merge"]), len(r["miss"]), len(r["false_pos"]), len(r["pairs"])) != (
                    truth["n_split"], truth["n_merge"], truth["n_miss"], truth["n_false_pos"], truth["n_exact"]):
                f.append("%s offset %d: the five counts do not match the injected values" % (tag, offset))
            # timing bias and spread: the difference between paired session ends (observed minus
            # ground truth). Expected mean = bias + lag/2 + offset; expected sd = sqrt(variance from
            # sd_ms plus variance from the lag).
            ba = stats.bland_altman([p["obs"]["t"] + offset for p in r["pairs"]],
                                    [p["gt"]["end_wall_ms"] for p in r["pairs"]])
            lag = TIMING["end_lag_ms_max"]
            want_mean = TIMING["bias_ms"] + lag / 2.0 + offset
            want_sd = math.sqrt(TIMING["sd_ms"] ** 2 + ((lag + 1) ** 2 - 1) / 12.0 + 1.0 / 12.0)
            se = ba["sd"] / math.sqrt(ba["n"])
            if abs(ba["bias"] - want_mean) > 4.0 * se:
                f.append("%s offset %d: the timing bias %.2f is outside 4 SE (%.2f) of the expected %.2f" % (tag, offset, ba["bias"], 4.0 * se, want_mean))
            if abs(ba["sd"] - want_sd) > 5.0 * want_sd / math.sqrt(2.0 * (ba["n"] - 1)):
                f.append("%s offset %d: the spread %.2f is outside the tolerance around the expected %.2f" % (tag, offset, ba["sd"], want_sd))
    return f


def _case_recovery_negative_control():
    """Confirms the recovery check itself can fail (negative control): a broken matcher or an
    incorrect offset correction causes recovery to fail."""
    f = []
    gts = _gt_population(2000, 101)
    obs, truth = inject(gts, RATES, TIMING, 1)
    ok = match.detection_metrics(match.match(gts, obs))[0]
    if _recovery_failures(ok, truth):
        f.append("the baseline (a correctly configured matcher) fails to recover: %r" % (_recovery_failures(ok, truth),))
    for name, kw in (("an incorrect offset correction (+3 s)", {"obs_end_offset_ms": 3000}), ("an overly strict iou_min (0.99)", {"iou_min": 0.99}),
                     ("an overly loose iou_min (0.05)", {"iou_min": 0.05})):
        m = match.detection_metrics(match.match(gts, obs, **kw))[0]
        if not _recovery_failures(m, truth):
            f.append("the recovery check still passed under %s (the check has no teeth)" % name)
    # corrupting the injected record (truth) must also make the check fail
    bad = json.loads(json.dumps(truth))
    bad["expected"]["recall"]["x"] += 5
    if not _recovery_failures(ok, bad):
        f.append("the recovery check still passed after the injected truth counts were corrupted")
    # removing one observation row breaks the count agreement (a single-row mutation must fail the check)
    m = match.detection_metrics(match.match(gts, obs[:50] + obs[51:]))[0]
    if not _recovery_failures(m, truth):
        f.append("the recovery check still passed after one observation row was removed")
    return f


def _case_sensitivity_of_definition():
    """Sensitivity of the IoU definition (documented behavior, not a defect to fix): short sessions
    can fail to pair once a timing offset is applied. risk flags this, recall drops accordingly, and
    correcting for the end-of-session lag (offset) restores it."""
    f = []
    short = _gt_population(1000, 7, len_lo=200, len_hi=200, gap_lo=3000, gap_hi=3000)
    long_ = _gt_population(1000, 8, len_lo=3000, len_hi=3000, gap_lo=3000, gap_hi=3000)
    zero = {"miss": 0.0, "split": 0.0, "merge": 0.0, "false_pos": 0.0}
    so, st = inject(short, zero, TIMING, 3)
    lo_, lt = inject(long_, zero, TIMING, 3)
    if st["risk"]["short"] != 1000 or lt["risk"]["short"] != 0:
        f.append("risk[short] is not 1000 for 200 ms sessions and 0 for 3000 ms sessions: %r %r" % (st["risk"], lt["risk"]))
    r_short0 = match.detection_metrics(match.match(short, so))[0]["recall"]["value"]
    r_short1 = match.detection_metrics(match.match(short, so, obs_end_offset_ms=-50))[0]["recall"]["value"]
    r_long = match.detection_metrics(match.match(long_, lo_))[0]["recall"]["value"]
    if r_long != 1.0:
        f.append("every 3000 ms session should pair: %r" % r_long)
    if not (r_short0 < 0.6 and r_short0 < r_short1 < 1.0):
        f.append("the 200 ms sessions' recall is not < 0.6 at offset 0 and higher but below 1 at offset -50: %.3f %.3f" % (r_short0, r_short1))
    # when the session length L is less than 3w, split / merge injection is skipped (never inject a
    # type the matcher cannot detect)
    _o, sk = inject(short, {"miss": 0.0, "split": 0.5, "merge": 0.5, "false_pos": 0.0}, TIMING, 4)
    if sk["n_split"] != 0 or sk["n_merge"] != 0 or sk["skipped"]["split"] + sk["skipped"]["merge"] != 1000:
        f.append("a split or merge was injected into a short session (it should have been skipped): %r" % (sk["skipped"],))
    return f


def _case_identity_and_accounting():
    f = []
    gts = _gt_population(200, 5)
    zero = {"miss": 0.0, "split": 0.0, "merge": 0.0, "false_pos": 0.0}
    still = {"bias_ms": 0, "sd_ms": 0, "end_lag_ms_max": 0}
    obs, truth = inject(gts, zero, still, 1)
    if len(obs) != 200 or any(o["t"] != g["end_wall_ms"] or o["dwell_ms"] != g["end_wall_ms"] - g["start_wall_ms"]
                              or o["nav"] != g["nav_true"] or o["abandon"] != (g["outcome"] == "cancel")
                              for o, g in zip(obs, gts)):
        f.append("an error-free, offset-free observation is not an exact copy of the ground truth")
    if truth["n_exact"] != 200 or truth["n_obs"] != 200 or truth["max_shift_ms"] != 1:
        f.append("the error-free truth record is wrong: %r" % ({k: truth[k] for k in ("n_exact", "n_obs", "max_shift_ms")},))
    r = match.match(gts, obs)
    if len(r["pairs"]) != 200 or any(p["iou"] != 1.0 for p in r["pairs"]):
        f.append("not every pair reaches IoU 1.0 when no error is injected")
    if not all(set(o) == {"type", "t", "dwell_ms", "nav", "abandon"} and o["type"] == "ime_shadow" for o in obs):
        f.append("an observation row does not have the obs/1 ime_shadow shape")
    # boundary rates
    obs1, t1 = inject(gts, dict(zero, miss=1.0), still, 1)
    if obs1 != [] or t1["n_miss"] != 200:
        f.append("not every session is dropped when miss = 1.0")
    obs2, t2 = inject(gts, dict(zero, split=1.0), still, 1)
    r2 = match.match(gts, obs2)
    if t2["n_split"] != 200 or len(r2["split"]) != 200 or any(len(s["obs"]) != 3 for s in r2["split"]):
        f.append("not every session becomes a 3-fragment split when split = 1.0")
    # A merge is injected only for a pair where neither IoU reaches iou_min. In a population with
    # varying lengths, imbalanced pairs are skipped (86 rows out of 200 in practice), so the exact
    # count under merge = 1.0 is checked against a population with uniform length and spacing instead.
    even = _gt_population(200, 5, len_lo=2000, len_hi=2000, gap_lo=1000, gap_hi=1000)
    obs3, t3 = inject(even, dict(zero, merge=1.0), still, 1)
    r3 = match.match(even, obs3)
    if t3["n_merge"] != 100 or len(r3["merge"]) != 100 or len(r3["miss"]) != 0 or t3["skipped"]["merge"] != 0:
        f.append("sessions are not merged two at a time into 100 rows when merge = 1.0: %r" % (t3["n_merge"],))
    _o, tu = inject(gts, dict(zero, merge=1.0), still, 1)
    if tu["skipped"]["merge"] == 0 or tu["n_merge"] * 2 + tu["n_exact"] != 200:
        f.append("an imbalanced merge in a population with varying lengths is not skipped: %r" % (tu["skipped"],))
    obs4, t4 = inject(gts, dict(zero, false_pos=1.0), still, 1)
    r4 = match.match(gts, obs4)
    if t4["n_false_pos"] != 200 or len(r4["false_pos"]) != 200 or len(r4["pairs"]) != 200:
        f.append("the number of false positives added does not equal the number of ground-truth sessions when false_pos = 1.0")
    # accounting: the counts must balance for every input
    for seed in range(1, 6):
        _o, t = inject(gts, {"miss": 0.1, "split": 0.2, "merge": 0.2, "false_pos": 0.3}, TIMING, seed)
        if t["n_exact"] + t["n_miss"] + t["n_split"] + t["n_merge_gt"] != t["n_gt"]:
            f.append("seed %d: the ground-truth-side counts do not balance" % seed)
        if t["n_exact"] + t["n_split_obs"] + t["n_merge"] + t["n_false_pos"] != len(_o):
            f.append("seed %d: the observation-side counts (pairs + fragments + merges + false positives) do not equal the row count" % seed)
    return f


def _case_determinism():
    f = []
    gts = _gt_population(300, 9)
    snapshot = json.dumps(gts, sort_keys=True)
    g0 = random.getstate()
    a = inject(gts, RATES, TIMING, 11)
    b = inject(gts, RATES, TIMING, 11)
    c = inject(gts, RATES, TIMING, 12)
    if random.getstate() != g0:
        f.append("the global random state was touched")
    if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
        f.append("the result is not identical for the same seed (not byte-for-byte equal)")
    if json.dumps(a[0], sort_keys=True) == json.dumps(c[0], sort_keys=True):
        f.append("the observation does not change when the seed changes")
    if json.dumps(gts, sort_keys=True) != snapshot:
        f.append("the input ground truth was mutated")
    if inject(list(reversed(gts)), RATES, TIMING, 11) != a:
        f.append("reordering the ground truth changes the result (it should be sorted by start time)")
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

    g = _gt_population(5, 1)
    ok = dict(RATES)
    raises("an unknown rates key", lambda: inject(g, dict(ok, extra=0.1), TIMING, 1))
    raises("a missing rates key", lambda: inject(g, {"miss": 0.1}, TIMING, 1))
    raises("a negative rate", lambda: inject(g, dict(ok, miss=-0.1), TIMING, 1))
    raises("a rate above 1", lambda: inject(g, dict(ok, false_pos=1.5), TIMING, 1))
    raises("miss + split + merge > 1", lambda: inject(g, dict(ok, miss=0.5, split=0.4, merge=0.3), TIMING, 1))
    raises("a boolean rate", lambda: inject(g, dict(ok, miss=True), TIMING, 1))
    raises("a missing timing key", lambda: inject(g, ok, {"bias_ms": 0}, 1))
    raises("a negative sd", lambda: inject(g, ok, dict(TIMING, sd_ms=-1), 1))
    raises("a fractional end_lag", lambda: inject(g, ok, dict(TIMING, end_lag_ms_max=10.5), 1))
    raises("a fractional seed", lambda: inject(g, ok, TIMING, 1.5))
    raises("a boolean seed", lambda: inject(g, ok, TIMING, True))
    raises("an iou_min of 0", lambda: inject(g, ok, TIMING, 1, iou_min=0))
    raises("a session with no end", lambda: inject([match._gt("t1", 0, None)], ok, TIMING, 1))
    if inject([], ok, TIMING, 1)[0] != []:
        f.append("an observation was produced from zero ground-truth sessions")
    return f


def _case_realistic_fragmentation():
    """Definitional-bias report, not a recovery test: randomly cut two-fragment splits, imbalanced
    merges, and short (200-600 ms) sessions are exactly the shapes that match()'s primary partition
    cannot see as a split or a merge. A fragment produced by an interior cut always covers a fraction
    fracA of its own ground-truth session with fracA + fracB == 1, so one side always wins the
    primary 1:1 pairing and match() reports the other as a plain false positive, never as
    result["split"]. This case does not use inject()'s calibrated rates and timing (those are chosen
    so that every injected error stays inside the type the matcher can already detect); instead it
    builds fragmentation directly and measures, then prints, how large the resulting bias is and how
    much of it the theta recount recovers.
    """
    f = []
    rng = random.Random(701)
    gts = _gt_population(400, 501, len_lo=200, len_hi=600, gap_lo=50, gap_hi=300)

    def cut_row(t, dwell, nav=0, abandon=False):
        return _row(t, dwell, nav, abandon)

    rows = []
    n_split_made = n_merge_made = n_exact = 0
    i = 0
    while i < len(gts):
        lo, hi = match.gt_interval(gts[i])
        length = hi - lo
        u = rng.random()
        if u < 0.3 and length >= 20:
            cut = lo + 1 + int(rng.random() * (length - 2))  # uniformly random interior cut, not a midpoint
            rows.append(cut_row(cut, cut - lo, _nav(gts[i])))
            rows.append(cut_row(hi, hi - cut, 0, gts[i].get("outcome") == "cancel"))
            n_split_made += 1
            i += 1
            continue
        if u < 0.5 and i + 1 < len(gts):
            lo2, hi2 = match.gt_interval(gts[i + 1])
            rows.append(cut_row(hi2, hi2 - lo, _nav(gts[i]) + _nav(gts[i + 1]), gts[i + 1].get("outcome") == "cancel"))
            n_merge_made += 1
            i += 2
            continue
        rows.append(cut_row(hi, length, _nav(gts[i]), gts[i].get("outcome") == "cancel"))
        n_exact += 1
        i += 1
    rows.sort(key=lambda r: (r["t"], r["dwell_ms"], r["nav"], r["abandon"]))

    r = match.match(gts, rows)
    raw = {"n_pairs": len(r["pairs"]), "n_split_gt": len(r["split"]), "n_merge_obs": len(r["merge"]),
           "n_miss": len(r["miss"]), "n_false_pos": len(r["false_pos"])}
    m = match.detection_metrics(r)[0]
    print("     [realistic fragmentation] made split=%d merge=%d exact=%d -- raw match(): %r" % (
        n_split_made, n_merge_made, n_exact, raw))
    print("     [realistic fragmentation] recount: n_split_gt_recount=%d n_fp_as_split=%d "
          "n_merge_obs_recount=%d n_miss_as_merge=%d split_rate=%r merge_rate=%r" % (
              m["n_split_gt_recount"], m["n_fp_as_split"], m["n_merge_obs_recount"], m["n_miss_as_merge"],
              m["split_rate"]["value"], m["merge_rate"]["value"]))

    # sanity, not exact recovery: raw match() must hide essentially all of these (the shape it structurally
    # cannot see), and the theta recount must surface most of them back as split / merge evidence.
    if raw["n_split_gt"] != 0:
        f.append("raw match() sees a randomly cut two-fragment split as a split (unexpected): %r" % raw)
    if m["n_split_gt_recount"] < int(0.9 * n_split_made):
        f.append("the theta recount fails to recover most of the randomly cut two-fragment splits: recount=%d made=%d" % (
            m["n_split_gt_recount"], n_split_made))
    if m["n_merge_obs_recount"] + raw["n_merge_obs"] < int(0.5 * n_merge_made):
        f.append("the theta recount plus the genuinely detected merges together fall short of half of the imbalanced merges: recount=%d genuine=%d made=%d" % (
            m["n_merge_obs_recount"], raw["n_merge_obs"], n_merge_made))

    # Also report, in counts, that merge injection falls short of the nominal rate (some are
    # skipped): sessions with a constant length of 200 ms cannot satisfy inject()'s own precondition
    # (neither IoU below iou_min, with the union's IoU still at or above iou_min under the worst-case offset).
    short = _gt_population(500, 9, len_lo=200, len_hi=200, gap_lo=3000, gap_hi=3000)
    _o, sk = inject(short, dict(RATES, merge=0.5, split=0.0), TIMING, 3)
    print("     [merge skip report] nominal merge rate 0.5 on 200 ms sessions -> n_merge=%d skipped.merge=%d (n_gt=%d)"
          % (sk["n_merge"], sk["skipped"]["merge"], sk["n_gt"]))
    if sk["skipped"]["merge"] <= 0:
        f.append("zero merges were skipped even for short sessions (the precondition for skipping no longer holds): %r" % sk)
    if sk["n_merge"] != 0:
        f.append("a merge was injected for 200 ms sessions even though the balanced-merge precondition does not hold: %r" % sk)
    return f


CASES = [
    ("parameter recovery (5 seeds; miss 5% / split 3% / merge 3% / false-positive 2%; bias +20 ms, "
     "sd 50 ms, end-of-session lag 0-100 ms)", _case_parameter_recovery),
    ("parameter recovery's negative control (a broken matcher, an incorrect offset correction, a "
     "corrupted truth record, or a removed row each cause recovery to fail)", _case_recovery_negative_control),
    ("sensitivity of the IoU definition (short sessions, risk, recovery via offset, skipped injections)",
     _case_sensitivity_of_definition),
    ("a report on realistic fragmentation bias (randomly cut two-fragment splits, imbalanced merges, "
     "short sessions)", _case_realistic_fragmentation),
    ("error-free copying, boundary rates, and count accounting", _case_identity_and_accounting),
    ("determinism (seed, global random state, input invariance, ordering)", _case_determinism),
    ("input validation", _case_validation),
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
            for m in fails[:12]:
                print("     FAIL " + m)
        else:
            print("  ✅ " + name)
    if bad:
        print("selftest failed %d / %d" % (bad, len(CASES)))
        return 1
    print("selftest OK (%d cases)" % len(CASES))
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Synthetic observation log with known injected errors "
                                              "(a library module; --selftest is its only standalone entry point)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
