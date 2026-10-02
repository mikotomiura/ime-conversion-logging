#!/usr/bin/env python3
# kit: selftest
"""From per-block synchronisation markers, report the distribution and drift of the time differences among oracle,
ground truth (gt), and the observation (obs) (Python standard library only).

  clock_sync.py --oracle ORACLE [--gt GT] [--obs-keys KEYS] --marker MARKER.json --out JSON [--window-ms N]
  clock_sync.py --selftest                                  positive and negative controls (fast defaults)

MARKER.json = {"oracle_keys":[...], "gt_vks":[...], "obs_categories":[...], "max_gap_ms": int}
  * oracle_keys: the plan's per-block marker (an array of key names). oracle has one marker row per key (seq is a
    0-based index); a block is judged to have "a marker found on the oracle side" once its rows cover seq = 0
    through len-1 in full.
  * gt_vks: the sequence of vk values of the ground-truth side's key_down rows. obs_categories: the sequence of
    categories of the observation side's key rows (press=true). On each side, a marker is picked out where this
    sequence runs **unbroken** and every adjacent gap is at or below max_gap_ms.
    The first entry of all three lists must correspond to the same press (the times compared are each side's first
    key). If only gt or only obs is given, only that side's list is required (gt_vks / obs_categories are needed
    only for the side in use).
  * A block is matched to the nearest occurrence within --window-ms (default 3000) before or after the wall-clock
    time of that block's first oracle key, one occurrence never assigned to two blocks. If the clock offset exceeds
    this width, nothing is found at all.

Reported difference = each side's first key's time - oracle's first key's time (ms). **Latency (the time from
sending to recording) and clock offset cannot be separated** (the difference reports their sum). A difference that
stays **constant** across blocks is latency plus a fixed offset; one that **moves with the block index (time)** is
drift.
  * gt_minus_oracle_wall / gt_minus_oracle_mono / obs_minus_oracle_wall / obs_minus_gt_wall (when both gt and obs
    are given).
    The observation side has only a wall clock (Unix ms), so the monotonic-clock difference exists only between gt
    and oracle.
  * Per-difference statistics: median, minimum, maximum, offset_at_start_ms (the fitted value at the first oracle
    marker's time), drift_ms_per_hour (the least-squares slope against the oracle marker times (hours); null when
    fewer than two blocks are matched or time never moves), residual_max_ms (the largest deviation from the fitted
    line), and the per-block values.

The observation side accepts only keys-only input (the keys-out of seal_obs.py). If it contains any row other than
meta or key, it is refused without inspecting its content. This file contains no code that reads, counts, or
matches ImeShadow rows.
exit 0 = produced output / 1 = oracle has no block with a complete marker, or the requested side matched no block
at all / 2 = input unreadable, or usage error.
"""
import argparse
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

MS_PER_HOUR = 3_600_000.0


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


def load_marker_spec(path, need_gt, need_obs):
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        raise InputError("cannot read the marker specification (%s) (%s)" % (path, e.__class__.__name__))

    def strs(key):
        v = spec.get(key) if isinstance(spec, dict) else None
        if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
            raise InputError("marker specification: %s must be a non-empty array of strings" % key)

    if not isinstance(spec, dict):
        raise InputError("the marker specification must be an object")
    strs("oracle_keys")
    if not _is_int(spec.get("max_gap_ms")) or spec["max_gap_ms"] < 0:
        raise InputError("marker specification: max_gap_ms must be an integer of 0 or more")
    if need_gt:
        v = spec.get("gt_vks")
        if not isinstance(v, list) or not v or not all(_is_int(x) for x in v):
            raise InputError("marker specification: using --gt requires gt_vks (a non-empty array of integers)")
    if need_obs:
        strs("obs_categories")
    return spec


def load_oracle(path):
    """Return (list of block ids, {block_id: {seq: (wall_ms, mono_ms)}})."""
    blocks = []
    markers = {}
    for row in read_jsonl(path):
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind not in ("block_begin", "marker"):
            continue
        bid = row.get("block_id")
        if not _is_int(bid):
            raise InputError("%s: %s row is missing an integer block_id" % (path, kind))
        if kind == "block_begin":
            blocks.append(bid)
            continue
        wall, mono, seq = row.get("t_wall_ms"), row.get("t_mono_ns"), row.get("seq")
        if not (_is_int(wall) and _is_int(mono) and _is_int(seq)):
            raise InputError("%s: a marker row is missing an integer seq / t_wall_ms / t_mono_ns" % path)
        per = markers.setdefault(bid, {})
        if seq in per:
            raise InputError("%s: two marker rows share the same block_id and seq" % path)
        per[seq] = (wall, mono / 1e6)
    return blocks, markers


def oracle_found(blocks, markers, n_keys):
    """Return ({block_id: (wall_ms, mono_ms)} for blocks whose marker (the first key) is complete, and a list of
    the blocks that are not)."""
    found, missing = {}, []
    for b in blocks:
        per = markers.get(b, {})
        if set(per) == set(range(n_keys)):
            found[b] = per[0]
        else:
            missing.append({"block_id": b, "reason": "no marker rows" if not per else
                            "incomplete marker rows (missing or surplus seq values)"})
    return found, missing


def load_gt(path):
    """Return the key_down rows [(wall_ms, mono_ms, vk)] in file order."""
    items = []
    for row in read_jsonl(path):
        if isinstance(row, dict) and row.get("kind") == "ev" and row.get("ev") == "key_down":
            wall, mono, vk = row.get("t_wall_ms"), row.get("t_mono_ns"), row.get("vk")
            if not (_is_int(wall) and _is_int(mono) and _is_int(vk)):
                raise InputError("%s: a key_down row is missing an integer t_wall_ms / t_mono_ns / vk" % path)
            items.append((wall, mono / 1e6, vk))
    return items


def load_obs_keys(path):
    """Return the keys-only observation's press=true key rows [(t_ms, category)] in time order."""
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


def find_occurrences(symbols, gap_clock, pattern, max_gap_ms):
    """The starting indices (non-overlapping) where symbols[i] matches pattern consecutively and every adjacent
    gap_clock[i] difference is between 0 and max_gap_ms inclusive."""
    n, m = len(symbols), len(pattern)
    out = []
    i = 0
    while i + m <= n:
        ok = all(symbols[i + k] == pattern[k] for k in range(m))
        if ok:
            for k in range(1, m):
                gap = gap_clock[i + k] - gap_clock[i + k - 1]
                if gap < 0 or gap > max_gap_ms:
                    ok = False
                    break
        if ok:
            out.append(i)
            i += m
        else:
            i += 1
    return out


def match_blocks(oracle_walls, occ_walls, window_ms):
    """A one-to-one assignment, nearest first. Returns ({block_id: occurrence index}, the number of occurrences
    matched to no block)."""
    cands = sorted(
        (abs(tw - to), b, i)
        for b, to in oracle_walls.items()
        for i, tw in enumerate(occ_walls)
        if abs(tw - to) <= window_ms
    )
    assigned, used = {}, set()
    for _, b, i in cands:
        if b not in assigned and i not in used:
            assigned[b] = i
            used.add(i)
    return assigned, len(occ_walls) - len(used)


def summarize(points):
    """points = [(block_id, time x, difference ms)] -> statistics."""
    if not points:
        return None
    points = sorted(points, key=lambda p: p[1])
    ys = [p[2] for p in points]
    ordered = sorted(ys)
    n = len(ys)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    slope = intercept = resid = None
    xs = [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if n >= 2 and sxx > 0:
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        intercept = my - slope * mx
        resid = max(abs(y - (intercept + slope * x)) for x, y in zip(xs, ys))
    r3 = lambda v: None if v is None else round(v, 3)
    return {
        "n": n,
        "median_ms": r3(median),
        "min_ms": r3(ordered[0]),
        "max_ms": r3(ordered[-1]),
        "offset_at_start_ms": r3(intercept),
        "drift_ms_per_hour": r3(slope),
        "residual_max_ms": r3(resid),
        "per_block": [{"block_id": b, "diff_ms": r3(y)} for b, _, y in points],
    }


def analyze(blocks, markers, spec, gt_items, obs_items, window_ms):
    found, missing_oracle = oracle_found(blocks, markers, len(spec["oracle_keys"]))
    result = {
        "marker": {k: spec[k] for k in ("oracle_keys", "gt_vks", "obs_categories", "max_gap_ms") if k in spec},
        "params": {"window_ms": window_ms},
        "oracle": {"blocks": len(blocks), "with_marker": sorted(found), "missing": missing_oracle},
        "differences": {},
    }
    if not found:
        return result, False
    order = sorted(found, key=lambda b: found[b][0])
    t_first = found[order[0]][0]
    hours = {b: (found[b][0] - t_first) / MS_PER_HOUR for b in found}
    oracle_walls = {b: found[b][0] for b in found}
    matched = {}  # side name -> {block_id: (wall_ms, mono_ms)}
    complete = True

    def side(name, occ_first, gap_clock, symbols, pattern):
        nonlocal complete
        idx = find_occurrences(symbols, gap_clock, pattern, spec["max_gap_ms"])
        assigned, unmatched = match_blocks(oracle_walls, [occ_first[i][0] for i in idx], window_ms)
        matched[name] = {b: occ_first[idx[i]] for b, i in assigned.items()}
        miss = sorted(b for b in found if b not in assigned)
        result[name] = {"occurrences": len(idx), "matched": sorted(assigned), "missing": miss, "unmatched_occurrences": unmatched}
        if not assigned:
            complete = False

    if gt_items is not None:
        side("gt", [(w, m) for w, m, _ in gt_items], [m for _, m, _ in gt_items], [v for _, _, v in gt_items], spec["gt_vks"])
    if obs_items is not None:
        side("obs", [(t, t) for t, _ in obs_items], [t for t, _ in obs_items], [c for _, c in obs_items], spec["obs_categories"])

    diffs = result["differences"]
    if "gt" in matched:
        g = matched["gt"]
        diffs["gt_minus_oracle_wall"] = summarize([(b, hours[b], g[b][0] - found[b][0]) for b in g])
        diffs["gt_minus_oracle_mono"] = summarize([(b, hours[b], g[b][1] - found[b][1]) for b in g])
    if "obs" in matched:
        o = matched["obs"]
        diffs["obs_minus_oracle_wall"] = summarize([(b, hours[b], o[b][0] - found[b][0]) for b in o])
    if "gt" in matched and "obs" in matched:
        both = [b for b in matched["gt"] if b in matched["obs"]]
        diffs["obs_minus_gt_wall"] = summarize([(b, hours[b], matched["obs"][b][0] - matched["gt"][b][0]) for b in both])
    return result, complete


# --- selftest begin ---
def _selftest():
    fails = []

    def check(ok, what):
        print("  %s %s" % ("✅" if ok else "❌", what))
        if not ok:
            fails.append(what)

    def dump(path, rows):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    T0, MONO0 = 1_700_000_000_000, 5_000_000_000_000
    SPACING = 1_800_000  # block spacing of 30 minutes -> h = 0, 0.5, 1, ... (the slope becomes an integer in ms)
    GT_OFFSET, GT_DRIFT = 37, 10      # known offset +37 ms, drift +10 ms/hour
    OBS_OFFSET, OBS_DRIFT = 12, -4
    MONO_OFFSET_MS = 250.5
    VKS = [105, 107, 113]
    CATS = ["function", "function", "function"]
    spec = {"oracle_keys": ["f13", "f14", "f15"], "gt_vks": VKS, "obs_categories": CATS, "max_gap_ms": 150}

    def build(n_blocks=6, gt_skip=(), obs_skip=(), oracle_skip=(), jitter=0, gt_gap=100, extra_gt=(), extra_obs=()):
        oracle = [{"kind": "header", "format": "oracle/1"}]
        gt = [{"kind": "header", "format": "gt/1", "src": "mac_nstic"}]
        obs = [{"type": "meta", "format": "obs/1"}]
        for b in range(n_blocks):
            bt = T0 + b * SPACING
            h = b * SPACING / MS_PER_HOUR
            j = jitter * (1 if b % 2 else -1)
            gt_off = int(round(GT_OFFSET + GT_DRIFT * h)) + j
            obs_off = int(round(OBS_OFFSET + OBS_DRIFT * h)) - j
            oracle.append({"kind": "block_begin", "block_id": b, "t_wall_ms": bt - 50, "t_mono_ns": MONO0})
            for k in range(3):
                at = bt + 100 * k
                if b not in oracle_skip:
                    oracle.append({"kind": "marker", "block_id": b, "seq": k, "t_wall_ms": at,
                                   "t_mono_ns": MONO0 + (at - T0) * 1_000_000})
                if b not in gt_skip:
                    gt_at = bt + gt_gap * k + gt_off
                    # the monotonic clock carries a fixed +250.5 ms, separate from the wall-clock offset and drift
                    gt_mono = MONO0 + (bt + gt_gap * k - T0) * 1_000_000 + int(MONO_OFFSET_MS * 1_000_000)
                    gt.append({"kind": "ev", "t_wall_ms": gt_at, "t_mono_ns": gt_mono, "ev": "key_down", "vk": VKS[k]})
                if b not in obs_skip:
                    obs.append({"type": "key", "t": bt + 100 * k + obs_off, "category": CATS[k], "press": True})
                    obs.append({"type": "key", "t": bt + 100 * k + obs_off + 20, "category": CATS[k], "press": False})
            # trial keystrokes (not markers): gt's vk 0..2 and obs's alphabetic
            for k in range(3):
                gt.append({"kind": "ev", "t_wall_ms": bt + 2000 + k * 100, "t_mono_ns": MONO0 + (bt + 2000 + k * 100 - T0) * 1_000_000,
                           "ev": "key_down", "vk": k})
                obs.append({"type": "key", "t": bt + 2000 + k * 100, "category": "alphabetic", "press": True})
        gt.extend(extra_gt)
        obs.extend(extra_obs)
        return oracle, gt, obs

    with tempfile.TemporaryDirectory() as tmp:
        def run(oracle, gt, obs, window=3000, sp=spec):
            paths = {}
            for name, rows in (("oracle", oracle), ("gt", gt), ("obs", obs)):
                paths[name] = os.path.join(tmp, name + ".jsonl")
                dump(paths[name], rows)
            blocks, markers = load_oracle(paths["oracle"])
            return analyze(blocks, markers, sp, load_gt(paths["gt"]), load_obs_keys(paths["obs"]), window)[0]

        # recovers a known offset and drift (gt +37 ms, +10 ms/hour; obs +12 ms, -4 ms/hour)
        r = run(*build())
        g = r["differences"]["gt_minus_oracle_wall"]
        check(g["n"] == 6 and abs(g["offset_at_start_ms"] - GT_OFFSET) < 0.01 and abs(g["drift_ms_per_hour"] - GT_DRIFT) < 0.01,
              "recovers a known offset and drift (gt: offset %s ms, drift %s ms/hour)" % (g["offset_at_start_ms"], g["drift_ms_per_hour"]))
        o = r["differences"]["obs_minus_oracle_wall"]
        check(o["n"] == 6 and abs(o["offset_at_start_ms"] - OBS_OFFSET) < 0.01 and abs(o["drift_ms_per_hour"] - OBS_DRIFT) < 0.01,
              "the observation side's offset and drift are also recovered (offset %s ms, drift %s ms/hour)" % (o["offset_at_start_ms"], o["drift_ms_per_hour"]))
        d = r["differences"]["obs_minus_gt_wall"]
        check(abs(d["offset_at_start_ms"] - (OBS_OFFSET - GT_OFFSET)) < 0.01 and abs(d["drift_ms_per_hour"] - (OBS_DRIFT - GT_DRIFT)) < 0.01,
              "obs - gt is the difference of the two differences (offset %s ms, drift %s ms/hour)" % (d["offset_at_start_ms"], d["drift_ms_per_hour"]))
        check(g["residual_max_ms"] < 0.01 and g["min_ms"] == GT_OFFSET and g["max_ms"] == GT_OFFSET + 25, "range of the difference (%s to %s ms) and the residual" % (g["min_ms"], g["max_ms"]))
        m = r["differences"]["gt_minus_oracle_mono"]
        check(m["n"] == 6 and m["min_ms"] == m["max_ms"] == MONO_OFFSET_MS and abs(m["drift_ms_per_hour"]) < 0.01,
              "the monotonic-clock difference is reported separately (a fixed +%s ms, zero drift)" % MONO_OFFSET_MS)
        check(r["gt"]["missing"] == [] and r["obs"]["missing"] == [] and r["gt"]["unmatched_occurrences"] == 0, "every block is found")

        # noise (a jitter of +/-1 ms) still recovers the slope and offset within 2 ms / (ms/hour)
        r = run(*build(jitter=1))
        g = r["differences"]["gt_minus_oracle_wall"]
        check(abs(g["offset_at_start_ms"] - GT_OFFSET) < 2 and abs(g["drift_ms_per_hour"] - GT_DRIFT) < 2 and g["residual_max_ms"] <= 1.5,
              "recovers under +/-1 ms of jitter (offset %s, drift %s, residual %s)" % (g["offset_at_start_ms"], g["drift_ms_per_hour"], g["residual_max_ms"]))

        # negative control: a block with no marker is reported as "missing"
        r = run(*build(gt_skip=(3,), obs_skip=(1, 4)))
        check(r["gt"]["missing"] == [3] and r["obs"]["missing"] == [1, 4], "a block with no marker is reported as missing (gt %s, obs %s)" % (r["gt"]["missing"], r["obs"]["missing"]))
        g = r["differences"]["gt_minus_oracle_wall"]
        check(g["n"] == 5 and abs(g["drift_ms_per_hour"] - GT_DRIFT) < 0.01, "the drift is still recovered unchanged using only the blocks that were found (n=%d)" % g["n"])
        check(r["differences"]["obs_minus_gt_wall"]["n"] == 3, "obs - gt uses only blocks found on both sides (n=%d)" % r["differences"]["obs_minus_gt_wall"]["n"])
        r = run(*build(oracle_skip=(2,)))
        check([x["block_id"] for x in r["oracle"]["missing"]] == [2] and 2 not in r["oracle"]["with_marker"], "a block with no marker row on the oracle side is also reported as missing")

        # a marker whose gap exceeds max_gap_ms is not picked up; an occurrence near no block is not assigned
        r = run(*build(gt_gap=300))
        check(r["gt"]["matched"] == [] and r["gt"]["missing"] == [0, 1, 2, 3, 4, 5], "a key gap exceeding max_gap_ms disqualifies the sequence as a marker")
        stray = [{"kind": "ev", "t_wall_ms": T0 + 100 * SPACING + k * 100, "t_mono_ns": MONO0, "ev": "key_down", "vk": VKS[k]} for k in range(3)]
        r = run(*build(extra_gt=stray))
        check(r["gt"]["unmatched_occurrences"] == 1 and r["gt"]["matched"] == [0, 1, 2, 3, 4, 5], "an occurrence near no block is left unassigned, and counted")
        partial = [{"kind": "ev", "t_wall_ms": T0 + 100 * SPACING, "t_mono_ns": MONO0, "ev": "key_down", "vk": VKS[0]},
                   {"kind": "ev", "t_wall_ms": T0 + 100 * SPACING + 50, "t_mono_ns": MONO0 + 50_000_000, "ev": "key_down", "vk": VKS[1]}]
        r = run(*build(extra_gt=partial))
        check(r["gt"]["occurrences"] == 6, "a sequence that breaks off partway through is not a marker")
        r = run(*build(), window=1)  # gt's offset is 37 ms or more, and obs's shrinks from 12 to 2 ms, but both exceed 1 ms
        check(r["gt"]["matched"] == [] and r["obs"]["matched"] == [], "nothing is found once the clock offset exceeds --window-ms")

        # input contract and exit codes
        me = os.path.abspath(__file__)
        op, gp, kp = (os.path.join(tmp, n + ".jsonl") for n in ("oracle", "gt", "obs"))
        sp, out = os.path.join(tmp, "marker.json"), os.path.join(tmp, "out.json")
        dump(op, build()[0]); dump(gp, build()[1]); dump(kp, build()[2])
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f)

        def cli(*extra, o=out):
            p = subprocess.run([sys.executable, me, "--oracle", op, "--marker", sp, "--out", o] + list(extra),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return p.returncode, p.stdout.decode("utf-8", "replace")

        rc, text = cli("--gt", gp, "--obs-keys", kp)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        check(rc == 0 and abs(data["differences"]["gt_minus_oracle_wall"]["drift_ms_per_hour"] - GT_DRIFT) < 0.01, "CLI: the output JSON carries the drift (exit %d)" % rc)
        rc, _ = cli("--gt", gp)
        check(rc == 0, "runs with gt alone (exit %d)" % rc)
        dump(kp, build()[2] + [{"type": "x", "t": 1}])
        rc, text = cli("--obs-keys", kp, o=out + "2")
        check(rc == 2 and not os.path.exists(out + "2") and "keys-only" in text, "refuses when a row that is not keys-only is present (exit %d, no output written)" % rc)
        with open(kp, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"type":"meta","type":"meta2"}\n')
        rc, text = cli("--obs-keys", kp, o=out + "2b")
        check(rc == 2 and not os.path.exists(out + "2b"), "refuses when obs-keys has a row with the same key twice (does not silently take the later value; exit %d)" % rc)
        with open(gp, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"kind":"header","format":"gt/1","src":"mac_nstic"}\n')
            f.write('{"kind":"ev","kind":"ev2","t_wall_ms":1,"t_mono_ns":1,"ev":"key_down","vk":105}\n')
        rc, text = cli("--gt", gp, o=out + "2c")
        check(rc == 2 and not os.path.exists(out + "2c"), "refuses when gt has a row with the same key twice (exit %d)" % rc)
        dump(gp, build()[1])
        dump(kp, build(obs_skip=range(6))[2])
        rc, _ = cli("--obs-keys", kp, o=out + "3")
        check(rc == 1, "exits 1 when the requested side matches no block at all (exit %d)" % rc)
        dump(op, build(oracle_skip=range(6))[0])
        rc, _ = cli("--gt", gp, o=out + "4")
        check(rc == 1, "exits 1 when oracle has no block with a complete marker (exit %d)" % rc)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"oracle_keys": ["f13"], "max_gap_ms": 150}, f)
        rc, _ = cli("--gt", gp, o=out + "5")
        check(rc == 2, "exits 2 when --gt is used without gt_vks (exit %d)" % rc)

    if fails:
        print("  ---- clock_sync selftest: %d failed ----" % len(fails))
        return 1
    print("  ---- clock_sync selftest: all green ----")
    return 0
# --- selftest end ---


def main(argv=None):
    ap = argparse.ArgumentParser(description="From synchronisation markers, report the distribution and drift of the time differences among oracle, gt, and the observation")
    ap.add_argument("--selftest", action="store_true", help="run the positive and negative controls")
    ap.add_argument("--oracle", help="runner output (oracle/1)")
    ap.add_argument("--gt", help="raw ground-truth log (gt/1)")
    ap.add_argument("--obs-keys", help="keys-only observation (the keys-out of seal_obs.py)")
    ap.add_argument("--marker", help="the marker specification JSON file")
    ap.add_argument("--out", help="destination for the result JSON")
    ap.add_argument("--window-ms", type=int, default=3000, help="width for matching an occurrence to a block (default 3000)")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not (args.oracle and args.marker and args.out):
        ap.error("--oracle / --marker / --out are all required")
    if not (args.gt or args.obs_keys):
        ap.error("either --gt or --obs-keys is required")
    if args.window_ms < 0:
        ap.error("--window-ms must be 0 or greater")
    try:
        spec = load_marker_spec(args.marker, bool(args.gt), bool(args.obs_keys))
        blocks, markers = load_oracle(args.oracle)
        gt_items = load_gt(args.gt) if args.gt else None
        obs_items = load_obs_keys(args.obs_keys) if args.obs_keys else None
        result, complete = analyze(blocks, markers, spec, gt_items, obs_items, args.window_ms)
    except InputError as e:
        print("clock_sync: %s" % e, file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    o = result["oracle"]
    print("blocks %d (oracle has a marker on %d, missing %s)" % (
        o["blocks"], len(o["with_marker"]), [x["block_id"] for x in o["missing"]]))
    for name in ("gt", "obs"):
        if name in result:
            s = result[name]
            print("%s: matched %d, missing %s, occurrences matched to no block %d" % (name, len(s["matched"]), s["missing"], s["unmatched_occurrences"]))
    for name, st in result["differences"].items():
        if st:
            print("%s: n=%d median %s ms (range %s to %s), drift %s ms/hour" % (
                name, st["n"], st["median_ms"], st["min_ms"], st["max_ms"], st["drift_ms_per_hour"]))
    if not o["with_marker"]:
        print("clock_sync: oracle has no block with a complete marker (check the marker rows and the length of oracle_keys)", file=sys.stderr)
        return 1
    if not complete:
        print("clock_sync: a side matched no block at all (check --window-ms, the marker specification, and the logs)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
