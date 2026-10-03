#!/usr/bin/env python3
# kit: selftest
"""The long trials of a Windows cell, in the form of the prediction that match.py reads for its scopes, and the
diagnostics that the Windows addendum registers (Python standard library only).

  scope_windows.py --from-plan PLAN     the long trials and the design figures of a plan, from its own times
  scope_windows.py --selftest

Why this exists (deviation D3, experiment/deviations.md). The scopes of questions 1 to 3 set aside the long trials
(section 6.4 of the pre-registration). On macOS they come from the prediction (spec_predict.py), which models the
macOS observer from the presses sent. The Windows observer forms its sessions from the input-method window being
shown and hidden (section 10.3), which the presses do not determine, so no prediction applies. For a Windows cell a
trial is long when the presses the executor sent in it (oracle "sent" rows with down = true, other than the pseudo
keys) span at least limit_ms, from the first to the last on the wall clock: like the macOS scope, this is decided
from the oracle and the plan alone. The same split by the ground truth (a session assigned to the trial lasting at
least limit_ms) is computed as a sensitivity analysis, with the trials whose ground-truth session ends within one
second of limit_ms (the boundary band).

as_prediction_bytes() renders a set of long trials as a prediction object (format spec_predict/2), so that
match.evaluate() builds under_limit and over_limit from it unchanged. Only the long flags carry information; the
pipeline does not report the layers of a prediction for such a cell.

windows_diagnostics() counts what section 10.7 registers as diagnostics: the ends of a reference composition that
carry no result length (the TSF field closes a composition it inferred, or could not read the committed length),
per scenario; for the trials of S4, the Escape press after which the ground-truth session ended; and the session
rows of the observation that last from 20 to 25 seconds and 25 seconds or more (the observer's two paths treat
long windows differently, section 10.3).

--from-plan computes, from the plan's own times (every press exactly on schedule), the long trials and, through
spec_predict.plan_figures, the design figures that section 10.7 quotes: per scenario and speed the trials that are
not long and the sum of m_j squared over the sum of m_j.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match  # noqa: E402
import spec_predict  # noqa: E402

SOURCE = "oracle_press_span"
SOURCE_GT = "ground_truth_duration"
PSEUDO_KEYS = ("focus_away", "focus_back", "mouse_click")
BAND_MS = 1000
END_AFTER_PRESS_MS = 2000   # a session end is credited to the last Escape sent at most this long before it
TRIAL_TAIL_MS = 300         # an end of a reference composition belongs to a trial up to this long after trial_end
                            # (the tail of kit/pilot/reach.py and of the delivery report)


def _sent_presses(oracle_bytes):
    """{trial_id: [(t_wall_ms, key), ...]} of the presses sent (down, not a pseudo key), in oracle order."""
    out = {}
    for line in oracle_bytes.decode("utf-8").split("\n")[1:]:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "sent" and r.get("down") is True and r.get("key") not in PSEUDO_KEYS:
            out.setdefault(r["trial_id"], []).append((r["t_wall_ms"], r["key"]))
    return out


def long_trials_oracle(oracle_bytes, limit_ms):
    """Trial ids whose sent presses span at least limit_ms."""
    return set(t for t, p in _sent_presses(oracle_bytes).items() if p[-1][0] - p[0][0] >= limit_ms)


def long_trials_gt(sessions, limit_ms):
    """Trial ids with a closed ground-truth session (list "all") of at least limit_ms. An open session, and a
    session that belongs to no trial, make no trial long."""
    return set(s["trial_id"] for s in sessions["definitions"]["all"]
               if s.get("trial_id") is not None and s.get("end_wall_ms") is not None
               and s["end_wall_ms"] - s["start_wall_ms"] >= limit_ms)


def band_trials_gt(sessions, limit_ms):
    """Trial ids with a closed ground-truth session lasting from limit_ms - BAND_MS to limit_ms + BAND_MS."""
    return set(s["trial_id"] for s in sessions["definitions"]["all"]
               if s.get("trial_id") is not None and s.get("end_wall_ms") is not None
               and abs(s["end_wall_ms"] - s["start_wall_ms"] - limit_ms) <= BAND_MS)


def as_prediction_bytes(longs, oracle_bytes, plan_bytes, limit_ms, source=SOURCE):
    plan = match.parse_plan_strata(plan_bytes)
    by = dict((tid, {"long": tid in longs, "carried_out": False, "received_carry": False,
                     "closing_key_in_reading": False, "n_started": 0})
              for tid in sorted(plan["trials"]))
    obj = {"format": match.PREDICTION_FORMAT, "source": source, "limit_ms": limit_ms,
           "inputs_sha256": {"oracle": hashlib.sha256(oracle_bytes).hexdigest(), "plan": plan["sha256"]},
           "trials": {"n_long": len(set(longs) & set(by)), "by_trial": by}}
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scope_summary(oracle_bytes, plan_bytes, sessions, limit_ms):
    """Counts of the long trials under both definitions, per scenario and speed of the plan."""
    plan = match.parse_plan_strata(plan_bytes)["trials"]
    lo, lg, band = long_trials_oracle(oracle_bytes, limit_ms), long_trials_gt(sessions, limit_ms), \
        band_trials_gt(sessions, limit_ms)
    strata = {}
    for tid, info in plan.items():
        k = "%s|%s" % (info["scenario"], info["speed"])
        s = strata.setdefault(k, {"n_trials": 0, "n_long": 0, "n_long_gt": 0, "n_differ": 0, "n_band": 0})
        s["n_trials"] += 1
        s["n_long"] += tid in lo
        s["n_long_gt"] += tid in lg
        s["n_differ"] += (tid in lo) != (tid in lg)
        s["n_band"] += tid in band
    tot = dict((k, sum(s[k] for s in strata.values())) for k in ("n_trials", "n_long", "n_long_gt", "n_differ", "n_band"))
    return {"source": SOURCE, "sensitivity_source": SOURCE_GT, "limit_ms": limit_ms, "band_ms": BAND_MS,
            "total": tot, "by_stratum": dict(sorted(strata.items()))}


def windows_diagnostics(gt_bytes, oracle_bytes, plan_bytes, sessions, obs_bytes=b""):
    """The diagnostics of section 10.7: ends without a result length per scenario, the Escape after which each
    S4 session ended, and the observed session rows of 20 to 25 seconds and of 25 seconds or more.

    For a session of an S4 trial the count is the number of Escapes the trial sent up to the session's end (so a
    second session of the same trial counts the Escapes before the first as well), and "none" when no Escape was
    sent within END_AFTER_PRESS_MS before the end."""
    plan = match.parse_plan_strata(plan_bytes)["trials"]
    wins = []
    for line in oracle_bytes.decode("utf-8").split("\n")[1:]:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "trial_begin":
            wins.append([r["trial_id"], r["t_wall_ms"], None])
        elif r.get("kind") == "trial_end" and wins and wins[-1][0] == r["trial_id"]:
            wins[-1][2] = r["t_wall_ms"]
    ends = {}
    for line in gt_bytes.decode("utf-8").split("\n")[1:]:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ev") == "tsf_end" and "result_len" not in r:
            tid = next((w[0] for w in wins if w[2] is not None and w[1] <= r["t_wall_ms"] <= w[2] + TRIAL_TAIL_MS),
                       None)
            sc = plan[tid]["scenario"] if tid in plan else "outside_trials"
            ends[sc] = ends.get(sc, 0) + 1
    sent = _sent_presses(oracle_bytes)
    s4 = {}
    for s in sessions["definitions"]["all"]:
        tid = s.get("trial_id")
        if tid not in plan or plan[tid]["scenario"] != "S4" or s.get("end_wall_ms") is None:
            continue
        escs = [t for t, k in sent.get(tid, []) if k == "esc"]
        n = sum(1 for t in escs if t <= s["end_wall_ms"])
        key = str(n) if n and s["end_wall_ms"] - max(t for t in escs if t <= s["end_wall_ms"]) <= END_AFTER_PRESS_MS \
            else "none"
        s4[key] = s4.get(key, 0) + 1
    rows = {"20000_to_24999": 0, "25000_or_more": 0}
    for line in obs_bytes.decode("utf-8").split("\n")[1:]:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("type") == "ime_shadow" and isinstance(r.get("dwell_ms"), int) and r["dwell_ms"] >= 20000:
            rows["25000_or_more" if r["dwell_ms"] >= 25000 else "20000_to_24999"] += 1
    return {"tsf_end_without_result_len": dict(sorted(ends.items())), "s4_session_end_after_escape": dict(sorted(s4.items())),
            "observed_rows_dwell_ms": rows}


def long_trials_plan(plan_bytes, limit_ms):
    """The long trials of a plan from its own times: the planned presses of a trial span at least limit_ms."""
    out = set()
    for line in plan_bytes.decode("utf-8").split("\n")[1:]:
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "trial":
            at = [s["at_ms"] for s in r["steps"] if s["down"] and s["key"] not in PSEUDO_KEYS]
            if at and at[-1] - at[0] >= limit_ms:
                out.add(r["trial_id"])
    return out


def plan_summary(plan_bytes, limit_ms):
    """The figures of section 10.7 from a plan: trials not long per scenario and speed, the long trials per speed,
    and the design figures of spec_predict.plan_figures for the trials that are not long."""
    longs = long_trials_plan(plan_bytes, limit_ms)
    plan = match.parse_plan_strata(plan_bytes)["trials"]
    figs = spec_predict.plan_figures(plan_bytes, dict((t, t in longs) for t in plan))
    under, long_by_speed = {}, {}
    for tid, info in plan.items():
        k = "%s|%s" % (info["scenario"], info["speed"])
        under[k] = under.get(k, 0) + (tid not in longs)
        long_by_speed[info["speed"]] = long_by_speed.get(info["speed"], 0) + (tid in longs)
    return {"limit_ms": limit_ms, "n_trials": len(plan), "n_long": len(longs), "long_by_speed": long_by_speed,
            "under_limit_by_stratum": dict(sorted(under.items())), "plan_figures": figs}


def selftest():
    fails = []

    def check(name, ok):
        print("  %s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            fails.append(name)

    def orow(kind, t, **kw):
        return json.dumps(dict(kw, kind=kind, t_wall_ms=t, t_mono_ns=t * 1000000))

    oracle = "\n".join([json.dumps({"kind": "header", "format": "oracle/1", "plan_sha256": "0" * 64}),
                        orow("trial_begin", 0, trial_id="t00001"), orow("sent", 100, trial_id="t00001", key="a", down=True),
                        orow("sent", 20100, trial_id="t00001", key="enter", down=True), orow("trial_end", 21000, trial_id="t00001"),
                        orow("trial_begin", 30000, trial_id="t00002"), orow("sent", 30100, trial_id="t00002", key="a", down=True),
                        orow("sent", 50150, trial_id="t00002", key="focus_away", down=True),
                        orow("sent", 50099, trial_id="t00002", key="enter", down=True),
                        orow("sent", 50200, trial_id="t00002", key="enter", down=False), orow("trial_end", 51000, trial_id="t00002"),
                        orow("trial_begin", 60000, trial_id="t00003"), orow("sent", 60100, trial_id="t00003", key="a", down=True),
                        orow("sent", 61000, trial_id="t00003", key="space", down=True),
                        orow("sent", 62000, trial_id="t00003", key="esc", down=True),
                        orow("sent", 62200, trial_id="t00003", key="esc", down=True),
                        orow("sent", 62400, trial_id="t00003", key="esc", down=True), orow("trial_end", 63000, trial_id="t00003")]) + "\n"
    ob = oracle.encode()
    lo = long_trials_oracle(ob, 20000)
    check("a span of exactly limit_ms is long, one ms less is not; pseudo keys and releases do not count",
          lo == {"t00001"})
    sess = {"definitions": {"all": [{"trial_id": "t00001", "start_wall_ms": 100, "end_wall_ms": 20099},
                                    {"trial_id": "t00002", "start_wall_ms": 30100, "end_wall_ms": 50100},
                                    {"trial_id": "t00003", "start_wall_ms": 60100, "end_wall_ms": 62250},
                                    {"trial_id": None, "start_wall_ms": 0, "end_wall_ms": 90000}]}}
    check("the ground-truth split counts closed sessions of trials only", long_trials_gt(sess, 20000) == {"t00002"})
    check("the boundary band holds the sessions within one second of limit_ms",
          band_trials_gt(sess, 20000) == {"t00001", "t00002"})
    plan = "\n".join(json.dumps(x) for x in [
        {"kind": "header", "format": "plan/1", "os": "windows"}, {"kind": "block", "block_id": 0, "speed": "slow"},
        {"kind": "trial", "trial_id": "t00001", "block_id": 0, "scenario": "S1", "speed": "slow"},
        {"kind": "trial", "trial_id": "t00002", "block_id": 0, "scenario": "S15", "speed": "slow"},
        {"kind": "trial", "trial_id": "t00003", "block_id": 0, "scenario": "S4", "speed": "slow"}]).encode() + b"\n"
    pb = as_prediction_bytes(lo, ob, plan, 20000)
    parsed = match.parse_prediction(pb, hashlib.sha256(ob).hexdigest(), match.parse_plan_strata(plan)["sha256"])
    check("match.py reads the rendered object, and its long trials are the oracle's", parsed["long"] == {"t00001"})
    try:
        match.parse_prediction(pb, hashlib.sha256(b"x").hexdigest(), match.parse_plan_strata(plan)["sha256"])
        check("an object made from another oracle is refused by match.py", False)
    except match.InputError:
        check("an object made from another oracle is refused by match.py", True)
    summ = scope_summary(ob, plan, sess, 20000)
    check("the summary counts both definitions and the trials they classify differently",
          summ["total"] == {"n_trials": 3, "n_long": 1, "n_long_gt": 1, "n_differ": 2, "n_band": 2})
    gt = "\n".join([json.dumps({"kind": "header", "format": "gt/1", "src": "win_tsf"}),
                    json.dumps({"kind": "ev", "ev": "tsf_end", "t_wall_ms": 20099, "t_mono_ns": 1}),
                    json.dumps({"kind": "ev", "ev": "tsf_end", "result_len": 0, "t_wall_ms": 62250, "t_mono_ns": 2}),
                    json.dumps({"kind": "ev", "ev": "tsf_end", "t_wall_ms": 95000, "t_mono_ns": 3})]) + "\n"
    obs = "\n".join([json.dumps({"type": "meta", "format": "obs/1"}),
                     json.dumps({"type": "ime_shadow", "t": 1, "dwell_ms": 19999, "nav": 0, "abandon": False}),
                     json.dumps({"type": "ime_shadow", "t": 2, "dwell_ms": 20000, "nav": 0, "abandon": False}),
                     json.dumps({"type": "ime_shadow", "t": 3, "dwell_ms": 25000, "nav": 0, "abandon": False}),
                     json.dumps({"type": "key", "t": 4, "category": "alphabetic", "press": True})]) + "\n"
    diag = windows_diagnostics(gt.encode(), ob, plan, sess, obs.encode())
    check("ends without a result length are counted per scenario (and outside the trials)",
          diag["tsf_end_without_result_len"] == {"S1": 1, "outside_trials": 1})
    check("an S4 session that ended after the second Escape is counted as such",
          diag["s4_session_end_after_escape"] == {"2": 1})
    check("observed rows of 20 to 25 s and of 25 s or more are counted apart, shorter rows and key rows not",
          diag["observed_rows_dwell_ms"] == {"20000_to_24999": 1, "25000_or_more": 1})
    print("selftest %s (%d failed)" % ("OK" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-plan")
    ap.add_argument("--limit-ms", type=int, default=20000)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.from_plan:
        ap.error("--from-plan or --selftest is required")
    with open(a.from_plan, "rb") as f:
        out = plan_summary(f.read(), a.limit_ms)
    sys.stdout.write(json.dumps(out, sort_keys=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
