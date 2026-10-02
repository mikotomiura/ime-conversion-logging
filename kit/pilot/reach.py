#!/usr/bin/env python3
# kit: selftest
"""Check whether synthetic input reaches the observation-side hook, using only the observation side's key rows
(Python standard library only).

  reach.py --oracle ORACLE --obs-keys KEYS --out JSON [--pre-ms N] [--tail-ms N] [--ignore-keys a,b,...]
  reach.py --selftest                                       positive and negative controls (fast defaults)

For each trial, compare the **number of presses the runner sent** (oracle's sent rows with down=true, excluding
pseudo-keys) against the **number of key rows (press=true)** that fell inside that trial's interval.
  * reach_rate = sum(min(sent, received)) / sum(sent). The min is taken so that a trial with more received rows
    (duplicates, or rows of another trial) never masks a loss in a different trial; the surplus is counted
    separately in excess_rows.
  * diff_distribution = the per-trial distribution of (received - sent). 0 means everything arrived; a negative
    value means that many presses were lost.
  * The interval runs from oracle's trial_begin to trial_end (wall clock). Because observation-side rows are
    written after the call returns, the interval is extended by --pre-ms (default 20) before and --tail-ms
    (default 300) after.
    The interval's tail end is also cut at the **next oracle row** (reset / block_begin / marker / the next
    trial_begin), whichever comes first (the min with --tail-ms). Without this cut, a marker press at the head of
    the next block would be counted into the last trial of the previous block, masking that trial's loss. A row
    that falls inside no trial's interval is counted in outside_trials_rows (a block-head marker's keys land
    here).

The observation side accepts only keys-only input (the keys-out of seal_obs.py). If it contains any row other than
meta or key, it is refused without inspecting its content. This file contains no code that reads, counts, or
matches ImeShadow rows.
exit 0 = tallied / 1 = nothing to tally (no send at all; an empty run is never reported as success) /
2 = input unreadable, or usage error.
"""
import argparse
import bisect
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

DEFAULT_IGNORE = ("mouse_click", "focus_away", "focus_back")  # pseudo-keys, not real keys (produce no key row)


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


def read_jsonl(path):
    rows = []
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        raise InputError("cannot open %s (%s)" % (path, e.__class__.__name__))
    with f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line, object_pairs_hook=_no_dup_pairs))
            except ValueError:
                raise InputError("%s: line %d does not parse as JSON" % (path, n))
    return rows


def load_oracle(path):
    """Return (list of trials [{trial_id, begin, end, sent}], where begin / end are t_wall_ms and sent is the
    sequence of pressed key names, and the sorted list of "next oracle row" times used to cut an interval
    (reset / block_begin / marker / trial_begin))."""
    trials = {}
    order = []
    cuts = []
    for row in read_jsonl(path):
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind in ("reset", "block_begin", "marker"):
            wall = row.get("t_wall_ms")
            if _is_int(wall):
                cuts.append(wall)
            continue
        if kind not in ("trial_begin", "trial_end", "sent"):
            continue
        tid = row.get("trial_id")
        if not isinstance(tid, str):
            raise InputError("%s: a row has a trial_id that is not a string" % path)
        if tid not in trials:
            trials[tid] = {"trial_id": tid, "begin": None, "end": None, "sent": []}
            order.append(tid)
        t = trials[tid]
        if kind == "sent":
            if row.get("down") is True:
                t["sent"].append(row.get("key"))
            continue
        wall = row.get("t_wall_ms")
        if not _is_int(wall):
            raise InputError("%s: %s row is missing an integer t_wall_ms" % (path, kind))
        field = "begin" if kind == "trial_begin" else "end"
        if t[field] is not None:
            raise InputError("%s: the same trial_id has two %s rows" % (path, kind))
        t[field] = wall
        if kind == "trial_begin":
            cuts.append(wall)
    cuts.sort()
    return [trials[k] for k in order], cuts


def load_keys_only(path):
    """Read a keys-only observation and return the press=true key rows' (t in Unix ms, category) in time order."""
    items = []
    for row in read_jsonl(path):
        kind = row.get("type") if isinstance(row, dict) else None
        if kind == "meta":
            continue
        if kind != "key":
            raise InputError("%s: a row that is not keys-only is present (pass the keys-out of seal_obs.py)" % path)
        if not _is_int(row.get("t")):
            raise InputError("%s: a key row is missing an integer t" % path)
        if row.get("press") is True:
            items.append((row["t"], row.get("category")))
    items.sort(key=lambda x: x[0])
    return items


def analyze(trials, cuts, presses, pre_ms, tail_ms, ignore):
    done = sorted((t for t in trials if t["begin"] is not None and t["end"] is not None), key=lambda t: t["begin"])
    starts = [t["begin"] - pre_ms for t in done]
    ends = []
    for t in done:
        end_i = t["end"] + tail_ms
        i = bisect.bisect_right(cuts, t["end"])
        if i < len(cuts):
            end_i = min(end_i, cuts[i] - 1)
        ends.append(end_i)
    counts = [0] * len(done)
    obs_cats = [[] for _ in done]
    outside = 0
    for ts, cat in presses:
        i = bisect.bisect_right(starts, ts) - 1
        if i >= 0 and ts <= ends[i]:
            counts[i] += 1
            obs_cats[i].append(cat)
        else:
            outside += 1
    per_trial = []
    dist = {}
    sent_total = reached = excess = full = lossy = 0
    for t, got, cats in zip(done, counts, obs_cats):
        sent_keys = [k for k in t["sent"] if k not in ignore]
        sent = len(sent_keys)
        per_trial.append({"trial_id": t["trial_id"], "sent": sent, "obs": got, "sent_keys": sent_keys, "obs_categories": cats})
        sent_total += sent
        reached += min(sent, got)
        excess += max(0, got - sent)
        d = got - sent
        dist[d] = dist.get(d, 0) + 1
        if d >= 0:
            full += 1
        else:
            lossy += 1
    return {
        "params": {"pre_ms": pre_ms, "tail_ms": tail_ms, "ignore_keys": sorted(ignore)},
        "trials_measured": len(done),
        "trials_incomplete": len(trials) - len(done),
        "sent_presses": sent_total,
        "reached": reached,
        "reach_rate": (reached / sent_total) if sent_total else None,
        "excess_rows": excess,
        "outside_trials_rows": outside,
        "trials_with_all_reached": full,
        "trials_with_loss": lossy,
        "diff_distribution": {str(k): dist[k] for k in sorted(dist)},
        "per_trial": per_trial,
    }


# --- selftest begin ---
def _selftest():
    fails = []

    def check(ok, what):
        print("  %s %s" % ("OK" if ok else "NG", what))
        if not ok:
            fails.append(what)

    def dump(path, rows):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    # 6 trials x 4 presses (+ 4 releases), key spacing 100 ms, trial spacing 1500 ms. Only trial 2 contains a
    # pseudo-key.
    t0 = 1_700_000_000_000
    oracle = [{"kind": "header", "format": "oracle/1"}, {"kind": "block_begin", "block_id": 0, "t_wall_ms": t0 - 900, "t_mono_ns": 0}]
    for s in range(3):
        oracle.append({"kind": "marker", "block_id": 0, "seq": s, "t_wall_ms": t0 - 800 + s * 100, "t_mono_ns": 0})
    obs_full = [{"type": "meta", "format": "obs/1"}]
    for s in range(3):  # observation of the marker keys (fall inside no trial's interval)
        obs_full.append({"type": "key", "t": t0 - 800 + s * 100 + 3, "category": "function", "press": True})
    for n in range(6):
        tid = "t%05d" % (n + 1)
        base = t0 + n * 1500
        oracle.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": base, "t_mono_ns": 0})
        for s in range(4):
            at = base + s * 100
            oracle.append({"kind": "sent", "trial_id": tid, "step": 2 * s, "key": "a", "down": True, "t_wall_ms": at, "t_mono_ns": 0})
            oracle.append({"kind": "sent", "trial_id": tid, "step": 2 * s + 1, "key": "a", "down": False, "t_wall_ms": at + 20, "t_mono_ns": 0})
            obs_full.append({"type": "key", "t": at + 3, "category": "alphabetic", "press": True})
            obs_full.append({"type": "key", "t": at + 23, "category": "alphabetic", "press": False})
        if n == 1:
            oracle.append({"kind": "sent", "trial_id": tid, "step": 8, "key": "mouse_click", "down": True, "t_wall_ms": base + 350, "t_mono_ns": 0})
        oracle.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": base + 301, "t_mono_ns": 0})
    oracle.append({"kind": "block_end", "block_id": 0, "t_wall_ms": t0 + 9000, "t_mono_ns": 0})

    trials = None
    with tempfile.TemporaryDirectory() as tmp:
        op = os.path.join(tmp, "oracle.jsonl")
        dump(op, oracle)
        trials, cuts = load_oracle(op)
        check(len(trials) == 6 and all(len(t["sent"]) == 4 + (1 if i == 1 else 0) for i, t in enumerate(trials)),
              "oracle: 6 trials, counting presses only (releases are not counted)")

        def measure(rows, **kw):
            kp = os.path.join(tmp, "keys.jsonl")
            dump(kp, rows)
            return analyze(trials, cuts, load_keys_only(kp), kw.get("pre", 20), kw.get("tail", 300), set(kw.get("ignore", DEFAULT_IGNORE)))

        r = measure(obs_full)
        check(r["reach_rate"] == 1.0 and r["sent_presses"] == 24 and r["diff_distribution"] == {"0": 6},
              "reach rate is 100%% when everything arrives (sent presses %d, pseudo-keys not counted)" % r["sent_presses"])
        check(r["outside_trials_rows"] == 3, "marker keys are counted as rows outside every trial (%d rows)" % r["outside_trials_rows"])
        check(measure(obs_full, ignore=())["sent_presses"] == 25, "not ignoring pseudo-keys adds one more sent press")
        check(r["per_trial"][0]["sent_keys"] == ["a", "a", "a", "a"] and r["per_trial"][0]["obs_categories"] == ["alphabetic"] * 4,
              "per_trial carries the sequence of key names sent and categories received (%s)" % r["per_trial"][0])

        # positive control: removing half of the key rows yields 50%
        press_rows = [r_ for r_ in obs_full if r_.get("press") is True and r_["t"] >= t0]
        drop = set(id(x) for x in press_rows[1::2])
        half = [x for x in obs_full if id(x) not in drop]
        r = measure(half)
        check(r["reach_rate"] == 0.5 and r["trials_with_loss"] == 6, "removing half of the key rows yields 50%% (measured %.1f%%)" % (100 * r["reach_rate"]))
        r = measure([x for x in obs_full if x.get("type") == "meta" or x["t"] < t0])
        check(r["reach_rate"] == 0.0, "reach rate is 0%% when no key row arrives")
        r = measure([x for x in obs_full if x.get("press") is not False])
        check(r["reach_rate"] == 1.0, "reach rate is 100%% with no release (press=false) rows at all (only presses are counted)")
        dup = obs_full + [{"type": "key", "t": t0 + 5, "category": "alphabetic", "press": True}]
        r = measure(dup)
        check(r["reach_rate"] == 1.0 and r["excess_rows"] == 1 and r["diff_distribution"] == {"0": 5, "1": 1},
              "a duplicate row does not raise the reach rate, and is counted in excess_rows")
        check(measure(obs_full, tail=0)["reach_rate"] < 1.0, "with no tail margin, a row that arrives late is lost")

        # positive control: two blocks, with a marker arriving 5 ms after block_end.
        #   Without cutting the interval at the "next oracle row", this marker's press would be counted into the
        #   last trial of the previous block, hiding that trial's loss (sending 2 and receiving only 1), and
        #   reach_rate would read 1.0.
        base2 = t0 + 100_000
        oracle2 = [
            {"kind": "header", "format": "oracle/1"},
            {"kind": "block_begin", "block_id": 0, "t_wall_ms": base2 - 50, "t_mono_ns": 0},
            {"kind": "trial_begin", "trial_id": "b0t1", "t_wall_ms": base2, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b0t1", "step": 0, "key": "a", "down": True, "t_wall_ms": base2, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b0t1", "step": 1, "key": "a", "down": False, "t_wall_ms": base2 + 20, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b0t1", "step": 2, "key": "a", "down": True, "t_wall_ms": base2 + 100, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b0t1", "step": 3, "key": "a", "down": False, "t_wall_ms": base2 + 120, "t_mono_ns": 0},
            {"kind": "trial_end", "trial_id": "b0t1", "t_wall_ms": base2 + 150, "t_mono_ns": 0},
            {"kind": "block_end", "block_id": 0, "t_wall_ms": base2 + 200, "t_mono_ns": 0},
            {"kind": "block_begin", "block_id": 1, "t_wall_ms": base2 + 205, "t_mono_ns": 0},
        ]
        marker_t = base2 + 200 + 5  # 5 ms after block_end
        oracle2.append({"kind": "marker", "block_id": 1, "seq": 0, "t_wall_ms": marker_t, "t_mono_ns": 0})
        oracle2 += [
            {"kind": "trial_begin", "trial_id": "b1t1", "t_wall_ms": base2 + 400, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b1t1", "step": 0, "key": "a", "down": True, "t_wall_ms": base2 + 400, "t_mono_ns": 0},
            {"kind": "sent", "trial_id": "b1t1", "step": 1, "key": "a", "down": False, "t_wall_ms": base2 + 420, "t_mono_ns": 0},
            {"kind": "trial_end", "trial_id": "b1t1", "t_wall_ms": base2 + 450, "t_mono_ns": 0},
            {"kind": "block_end", "block_id": 1, "t_wall_ms": base2 + 500, "t_mono_ns": 0},
        ]
        # observation: b0t1 sends 2 and receives only 1 (a real loss). The marker press arrives at marker_t + 3 ms.
        obs2 = [
            {"type": "meta", "format": "obs/1"},
            {"type": "key", "t": base2 + 3, "category": "alphabetic", "press": True},
            {"type": "key", "t": marker_t + 3, "category": "function", "press": True},
            {"type": "key", "t": base2 + 403, "category": "alphabetic", "press": True},
            {"type": "key", "t": base2 + 423, "category": "alphabetic", "press": False},
        ]
        op2 = os.path.join(tmp, "oracle2.jsonl")
        kp2 = os.path.join(tmp, "keys2b.jsonl")
        dump(op2, oracle2)
        dump(kp2, obs2)
        trials2, cuts2 = load_oracle(op2)
        r2 = analyze(trials2, cuts2, load_keys_only(kp2), 20, 300, set(DEFAULT_IGNORE))
        b0 = next(p for p in r2["per_trial"] if p["trial_id"] == "b0t1")
        check(b0["obs"] == 1 and b0["sent"] == 2, "the next block's marker press is not counted into the previous block's last trial (b0t1: sent %d, received %d)" % (b0["sent"], b0["obs"]))
        check(r2["reach_rate"] < 1.0, "cutting the interval at the next oracle row surfaces the loss that would otherwise be hidden in the reach rate (%.2f)" % r2["reach_rate"])
        check(r2["outside_trials_rows"] == 1, "the marker press is counted as a row outside every trial's interval")

        # input contract and exit codes.
        me = os.path.abspath(__file__)
        kp = os.path.join(tmp, "k.jsonl")
        out = os.path.join(tmp, "out.json")
        dump(kp, obs_full)
        p = subprocess.run([sys.executable, me, "--oracle", op, "--obs-keys", kp, "--out", out], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        check(p.returncode == 0 and data["reach_rate"] == 1.0, "CLI: the output JSON carries the reach rate (exit %d)" % p.returncode)
        dump(kp, obs_full + [{"type": "x", "t": 1}])
        p = subprocess.run([sys.executable, me, "--oracle", op, "--obs-keys", kp, "--out", out + "2"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = p.stdout.decode("utf-8", "replace")
        check(p.returncode == 2 and not os.path.exists(out + "2") and "keys-only" in text, "refuses when a row that is not keys-only is present (exit %d, no output written)" % p.returncode)
        with open(kp, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"type":"meta","type":"meta2"}\n')
        p = subprocess.run([sys.executable, me, "--oracle", op, "--obs-keys", kp, "--out", out + "5"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "refuses when obs-keys has a row with the same key twice (exit %d)" % p.returncode)
        dump(kp, obs_full)
        dump(op + "0", [{"kind": "header", "format": "oracle/1"}])
        p = subprocess.run([sys.executable, me, "--oracle", op + "0", "--obs-keys", kp, "--out", out + "3"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 1, "an empty run (no send at all) exits 1 (exit %d)" % p.returncode)
        p = subprocess.run([sys.executable, me, "--oracle", os.path.join(tmp, "nothing"), "--obs-keys", kp, "--out", out + "4"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        check(p.returncode == 2, "an unopenable oracle exits 2 (exit %d)" % p.returncode)

    if fails:
        print("  ---- reach selftest: %d failed ----" % len(fails))
        return 1
    print("  ---- reach selftest: all green ----")
    return 0
# --- selftest end ---


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check whether synthetic input reaches the observation-side hook, using only key rows")
    ap.add_argument("--selftest", action="store_true", help="run the positive and negative controls")
    ap.add_argument("--oracle", help="runner output (oracle/1)")
    ap.add_argument("--obs-keys", help="keys-only observation (the keys-out of seal_obs.py)")
    ap.add_argument("--out", help="destination for the result JSON")
    ap.add_argument("--pre-ms", type=int, default=20, help="margin before a trial's interval (default 20)")
    ap.add_argument("--tail-ms", type=int, default=300, help="margin after a trial's interval (default 300)")
    ap.add_argument("--ignore-keys", default=",".join(DEFAULT_IGNORE), help="comma-separated key names to exclude from the count (default: the pseudo-keys)")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not (args.oracle and args.obs_keys and args.out):
        ap.error("--oracle / --obs-keys / --out are all required")
    if args.pre_ms < 0 or args.tail_ms < 0:
        ap.error("--pre-ms / --tail-ms must be 0 or greater")
    ignore = set(k for k in args.ignore_keys.split(",") if k)
    try:
        trials, cuts = load_oracle(args.oracle)
        result = analyze(trials, cuts, load_keys_only(args.obs_keys), args.pre_ms, args.tail_ms, ignore)
    except InputError as e:
        print("reach: %s" % e, file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    rate = result["reach_rate"]
    print("trials %d (rows outside every interval %d), sent presses %d, received %d, reach rate %s, excess rows %d" % (
        result["trials_measured"], result["outside_trials_rows"], result["sent_presses"], result["reached"],
        "none" if rate is None else "%.1f%%" % (100 * rate), result["excess_rows"]))
    print("distribution of received - sent: %s" % json.dumps(result["diff_distribution"], separators=(",", ":")))
    if rate is None:
        print("reach: nothing to tally (check that oracle has completed trials with presses)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
