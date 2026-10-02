#!/usr/bin/env python3
# kit: selftest
"""Count per-trial call-sequence types and report their distribution by scenario, together with each trial's
wall-clock duration (Python standard library only).

  call_types.py --gt GT --oracle ORACLE --plan PLAN --out JSON [--clock wall|mono] [--pre-ms N] [--tail-ms N] [--skip-ev a,b,...]
  call_types.py --selftest                                  positive and negative controls (fast defaults)

Produces material for fixing the operational definition of ground truth (composition start, commit, cancel, and
candidate window) against an on-device call sequence, and for reviewing the trial count n.
  * A trial's interval runs from oracle's trial_begin to trial_end. A gt ev row is assigned to a trial's call
    sequence when it falls inside the interval, extended by --pre-ms before and --tail-ms after (defaults 50 / 500
    ms; ground-truth records are written after the call returns, so the result of the last key can arrive after
    trial_end). Times are compared on --clock (default wall = wall clock).
    The interval's tail end is also cut at the **next oracle row** (reset / block_begin / marker / the next
    trial_begin), whichever comes first (the min with --tail-ms). Without this cut, a call triggered at the head of
    the next block (a reset or a marker) would be counted into the last trial of the previous block.
    A row that falls inside no trial's interval is counted in unassigned_events (with a per-ev breakdown in
    unassigned_events_by_ev).
  * A type string joins call names (ev) in time order with " → ", folding a name repeated two or more times in a
    row into a single entry with a trailing "+" (set_marked repeated three times followed by insert_text becomes
    "set_marked+ → insert_text"; a single set_marked stays "set_marked → insert_text"). do_command appends cmd,
    notify appends imn, and ui_* appends ui_kind, each in brackets (do_command[insertNewline:]). A trial with no row
    at all is reported as "(none)".
  * An ev listed in --skip-ev (default key_down) is left out of the type (a keystroke record overlaps with what
    oracle already sent). If first_rect is interleaved on every conversion step and makes the type too fine-grained,
    fold it away with --skip-ev key_down,first_rect.
  * A trial's wall-clock duration runs from oracle's trial_begin to trial_end (monotonic clock, ms). The median and
    95th percentile (nearest-rank method: the ceil(0.95*n)-th of n values sorted ascending) are reported per
    scenario and overall.
    **duration_ms excludes the inter-trial wait (gap_before_ms), markers, and resets** (it covers only trial_begin
    to trial_end). The wall-clock length of the whole session is reported separately in session_wall_ms (from the
    first reset / block_begin to the last block_end).
  * per_trial reports, for each trial, the relative time of its first and last call (ms from trial_begin), used to
    check whether the intervals of the Windows IMM and TSF paths agree during the calibration pilot (null for a
    trial with no call).

The output JSON is keyed by src (gt's header) x scenario (plan's trial rows). To compare the two Windows paths
(IMM / TSF), run this tool twice, once per path with its own gt, and place the two types outputs side by side.
exit 0 = produced output / 1 = no completed trial at all (oracle has matching begin and end, present in plan) /
2 = input unreadable, or usage error.
"""
import argparse
import bisect
import json
import math
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

QUALIFIERS = {"do_command": "cmd", "notify": "imn"}  # ui_* uses ui_kind


class InputError(Exception):
    """Input is unreadable, or does not match the contract."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


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
                rows.append(json.loads(line))
            except ValueError:
                raise InputError("%s: line %d does not parse as JSON" % (path, n))
    return rows


def load_plan(path):
    """{trial_id: scenario}."""
    scen = {}
    for row in read_jsonl(path):
        if isinstance(row, dict) and row.get("kind") == "trial":
            tid, sc = row.get("trial_id"), row.get("scenario")
            if not isinstance(tid, str) or not isinstance(sc, str):
                raise InputError("%s: a trial row is missing trial_id / scenario (strings)" % path)
            scen[tid] = sc
    return scen


def load_oracle(path, clock):
    """Return (completed trials [{trial_id, begin, end, dur_ms}], count of incomplete trials, sorted list of cut
    times, session wall-clock length in ms).
    A cut = reset / block_begin / marker / trial_begin (time on --clock).
    Session wall-clock length = the first reset / block_begin's t_wall_ms to the last block_end's t_wall_ms.
    """
    trials, order = {}, []
    cuts = []
    session_start = session_end = None
    for row in read_jsonl(path):
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind in ("reset", "block_begin", "marker"):
            wall, mono = row.get("t_wall_ms"), row.get("t_mono_ns")
            if _is_int(wall) and _is_int(mono):
                cuts.append(wall if clock == "wall" else mono / 1e6)
                if kind in ("reset", "block_begin") and (session_start is None or wall < session_start):
                    session_start = wall
            continue
        if kind == "block_end":
            wall = row.get("t_wall_ms")
            if _is_int(wall) and (session_end is None or wall > session_end):
                session_end = wall
            continue
        if kind not in ("trial_begin", "trial_end"):
            continue
        tid = row.get("trial_id")
        wall, mono = row.get("t_wall_ms"), row.get("t_mono_ns")
        if not (isinstance(tid, str) and _is_int(wall) and _is_int(mono)):
            raise InputError("%s: %s row is missing trial_id / t_wall_ms / t_mono_ns" % (path, kind))
        if tid not in trials:
            trials[tid] = {"trial_id": tid}
            order.append(tid)
        field = "begin" if kind == "trial_begin" else "end"
        if field in trials[tid]:
            raise InputError("%s: the same trial_id has two %s rows" % (path, kind))
        val = wall if clock == "wall" else mono / 1e6
        trials[tid][field] = (val, mono / 1e6)
        if kind == "trial_begin":
            cuts.append(val)
    done, incomplete = [], 0
    started = set(order)
    for tid in order:
        t = trials[tid]
        if "begin" in t and "end" in t:
            done.append({"trial_id": tid, "begin": t["begin"][0], "end": t["end"][0], "dur_ms": t["end"][1] - t["begin"][1]})
        else:
            incomplete += 1
    cuts.sort()
    session_wall_ms = None
    if session_start is not None and session_end is not None and session_end >= session_start:
        session_wall_ms = session_end - session_start
    return done, incomplete, cuts, session_wall_ms, started


def token(row):
    ev = row["ev"]
    key = QUALIFIERS.get(ev, "ui_kind" if ev.startswith("ui_") else None)
    if key is not None and key in row:
        return "%s[%s]" % (ev, row[key])
    return ev


def load_gt(path, clock, skip):
    """Return (src, [(time ms, token, ev)]), in file order."""
    src, events = None, []
    for row in read_jsonl(path):
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "header":
            src = row.get("src")
        elif row.get("kind") == "ev":
            ev, wall, mono = row.get("ev"), row.get("t_wall_ms"), row.get("t_mono_ns")
            if not (isinstance(ev, str) and _is_int(wall) and _is_int(mono)):
                raise InputError("%s: an ev row is missing ev / t_wall_ms / t_mono_ns" % path)
            if ev in skip:
                continue
            events.append((wall if clock == "wall" else mono / 1e6, token(row), ev))
    if not isinstance(src, str):
        raise InputError("%s: gt's header is missing src" % path)
    return src, events


def fold(tokens):
    out = []
    i = 0
    while i < len(tokens):
        j = i
        while j + 1 < len(tokens) and tokens[j + 1] == tokens[i]:
            j += 1
        out.append(tokens[i] + ("+" if j > i else ""))
        i = j + 1
    return " → ".join(out) if out else "(none)"


def stats(values):
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    median = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    p95 = v[max(0, math.ceil(0.95 * n) - 1)]
    r = lambda x: round(x, 3)
    return {"n": n, "median_ms": r(median), "p95_ms": r(p95), "min_ms": r(v[0]), "max_ms": r(v[-1])}


def analyze(src, events, trials, scenarios, pre_ms, tail_ms, cuts, session_wall_ms, started_ids):
    unknown = [t["trial_id"] for t in trials if t["trial_id"] not in scenarios]
    if unknown:
        raise InputError("an oracle trial is missing from plan (%d cases, e.g. %s); check that plan and oracle pair up"
                          % (len(unknown), unknown[0]))
    trials = sorted(trials, key=lambda t: t["begin"])
    starts = [t["begin"] - pre_ms for t in trials]
    ends = []
    for t in trials:
        end_i = t["end"] + tail_ms
        i = bisect.bisect_right(cuts, t["end"])
        if i < len(cuts):
            end_i = min(end_i, cuts[i] - 1)
        ends.append(end_i)
    per = [[] for _ in trials]
    per_when = [[] for _ in trials]
    unassigned = 0
    unassigned_by_ev = {}
    for when, tok, ev in events:
        i = bisect.bisect_right(starts, when) - 1
        if i >= 0 and when <= ends[i]:
            per[i].append(tok)
            per_when[i].append(when)
        else:
            unassigned += 1
            unassigned_by_ev[ev] = unassigned_by_ev.get(ev, 0) + 1
    by = {}
    per_trial_out = []
    for t, toks, whens in zip(trials, per, per_when):
        s = by.setdefault(scenarios[t["trial_id"]], {"trials": 0, "types": {}, "durations": []})
        s["trials"] += 1
        typ = fold(toks)
        s["types"][typ] = s["types"].get(typ, 0) + 1
        s["durations"].append(t["dur_ms"])
        rel = [w - t["begin"] for w in whens]
        per_trial_out.append({
            "trial_id": t["trial_id"],
            "scenario": scenarios[t["trial_id"]],
            "first_call_relative_ms": round(min(rel), 3) if rel else None,
            "last_call_relative_ms": round(max(rel), 3) if rel else None,
        })
    out = {}
    for sc in sorted(by, key=lambda x: (len(x), x)):  # S1, S2, …, S10, …
        s = by[sc]
        ordered = sorted(s["types"].items(), key=lambda kv: (-kv[1], kv[0]))
        out[sc] = {"trials": s["trials"], "types": dict(ordered), "duration_ms": stats(s["durations"])}
    return {
        "src": src,
        "params": {"pre_ms": pre_ms, "tail_ms": tail_ms},
        "trials_measured": len(trials),
        "planned_not_run": len(set(scenarios) - started_ids),
        "unassigned_events": unassigned,
        "unassigned_events_by_ev": dict(sorted(unassigned_by_ev.items())),
        "duration_ms": stats([t["dur_ms"] for t in trials]),
        "session_wall_ms": session_wall_ms,
        "by_scenario": out,
        "per_trial": per_trial_out,
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

    # gt's monotonic clock is offset from oracle by 2.5 s (half of the 4 s trial spacing; exactly 4 s would fall
    # inside the next trial's interval)
    T0, MONO0, MONO_SKEW = 1_700_000_000_000, 9_000_000_000_000, 2_500_000_000
    # Trial: (trial_id, scenario, length ms, gt call sequence). A gt row is (ms from trial start, ev, extra fields).
    def ev(at, name, **kw):
        return (at, name, kw)
    mac = [
        ("t00001", "S1", 1000, [ev(10, "key_down", vk=0), ev(20, "set_marked", len=1), ev(120, "set_marked", len=2),
                                ev(220, "set_marked", len=3), ev(320, "insert_text", len=3)]),
        ("t00002", "S1", 1000, [ev(10, "set_marked", len=1), ev(300, "insert_text", len=1)]),
        ("t00003", "S1", 1000, [ev(10, "set_marked", len=1), ev(120, "set_marked", len=2), ev(1040, "insert_text", len=2)]),  # after trial_end (inside the tail margin)
        ("t00004", "S2", 1000, [ev(10, "set_marked", len=1), ev(200, "do_command", cmd="insertNewline:"), ev(300, "insert_text", len=1)]),
        ("t00005", "S2", 1000, [ev(10, "set_marked", len=1), ev(200, "do_command", cmd="cancelOperation:"), ev(210, "unmark")]),
        ("t00006", "S3", 1000, []),
    ]
    # 20 trials of length 100..2000 ms (S4) -> median 1050, p95 1900
    durs = [("t%05d" % (7 + k), "S4", 100 * (k + 1), [ev(10, "set_marked", len=1)]) for k in range(20)]
    GAP = 3000

    def build(rows, src, header_extra=None):
        oracle = [{"kind": "header", "format": "oracle/1"}]
        plan = [{"kind": "header", "format": "plan/1"}]
        gt = [{"kind": "header", "format": "gt/1", "src": src}]
        base = T0
        first = True
        for tid, sc, length, evs in rows:
            plan.append({"kind": "trial", "trial_id": tid, "scenario": sc})
            if first:
                oracle.append({"kind": "block_begin", "block_id": 0, "t_wall_ms": base - 10, "t_mono_ns": MONO0 - 10_000_000})
                first = False
            oracle.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": base, "t_mono_ns": MONO0 + (base - T0) * 1_000_000})
            oracle.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": base + length, "t_mono_ns": MONO0 + (base + length - T0) * 1_000_000})
            for at, name, kw in evs:
                row = {"kind": "ev", "t_wall_ms": base + at, "t_mono_ns": MONO0 + MONO_SKEW + (base + at - T0) * 1_000_000, "ev": name}
                row.update(kw)
                gt.append(row)
            base += length + GAP
        oracle.append({"kind": "block_end", "block_id": 0, "t_wall_ms": base, "t_mono_ns": MONO0 + (base - T0) * 1_000_000})
        return oracle, plan, gt

    with tempfile.TemporaryDirectory() as tmp:
        def run(rows, src, clock="wall", skip=("key_down",), pre=50, tail=500, extra_gt=()):
            oracle, plan, gt = build(rows, src)
            gt = gt + list(extra_gt)
            paths = {n: os.path.join(tmp, n + ".jsonl") for n in ("oracle", "plan", "gt")}
            dump(paths["oracle"], oracle); dump(paths["plan"], plan); dump(paths["gt"], gt)
            trials, incomplete, cuts, session_wall_ms, started = load_oracle(paths["oracle"], clock)
            s, events = load_gt(paths["gt"], clock, set(skip))
            r = analyze(s, events, trials, load_plan(paths["plan"]), pre, tail, cuts, session_wall_ms, started)
            r["trials_incomplete"] = incomplete
            return r

        r = run(mac + durs, "mac_nstic")
        s1, s2 = r["by_scenario"]["S1"]["types"], r["by_scenario"]["S2"]["types"]
        check(s1 == {"set_marked+ → insert_text": 2, "set_marked → insert_text": 1}, "a known sequence gives the expected type, and repeats fold into + (S1: %s)" % s1)
        check(s2 == {"set_marked → do_command[cancelOperation:] → unmark": 1, "set_marked → do_command[insertNewline:] → insert_text": 1},
              "do_command appends cmd (S2: %s)" % list(s2))
        check(r["by_scenario"]["S3"]["types"] == {"(none)": 1}, "a trial with no row at all is reported as the empty-type placeholder")
        check([k for k in r["by_scenario"]] == ["S1", "S2", "S3", "S4"] and r["by_scenario"]["S1"]["trials"] == 3 and r["by_scenario"]["S4"]["trials"] == 20,
              "per-scenario trial counts (%s)" % {k: v["trials"] for k, v in r["by_scenario"].items()})
        check("key_down" not in json.dumps(r, ensure_ascii=False), "key_down is excluded from the type by default")
        check(run(mac[:1], "mac_nstic", skip=())["by_scenario"]["S1"]["types"] == {"key_down → set_marked+ → insert_text": 1}, "an empty --skip-ev includes key_down in the type as well")
        d = r["by_scenario"]["S4"]["duration_ms"]
        check(d["n"] == 20 and d["median_ms"] == 1050 and d["p95_ms"] == 1900 and d["min_ms"] == 100 and d["max_ms"] == 2000,
              "trial duration: median %s, 95th percentile %s ms" % (d["median_ms"], d["p95_ms"]))
        check(r["duration_ms"]["n"] == 26 and r["src"] == "mac_nstic", "overall duration and src (n=%d)" % r["duration_ms"]["n"])
        check(r["session_wall_ms"] is not None and r["session_wall_ms"] > r["duration_ms"]["n"] * 1000, "session_wall_ms is the wall-clock length of the whole block (%s ms)" % r["session_wall_ms"])
        p1 = next(p for p in r["per_trial"] if p["trial_id"] == "t00001")
        check(p1["first_call_relative_ms"] == 20 and p1["last_call_relative_ms"] == 320, "per_trial carries the relative time of the first and last call (t00001: %s)" % p1)
        p3 = next(p for p in r["per_trial"] if p["trial_id"] == "t00003")
        check(p3["last_call_relative_ms"] == 1040, "a call after trial_end is still included in the relative time (inside the tail margin; t00003: %s)" % p3)
        p6 = next(p for p in r["per_trial"] if p["trial_id"] == "t00006")
        check(p6["first_call_relative_ms"] is None and p6["last_call_relative_ms"] is None, "a trial with no call has a null relative time")

        # A row outside every trial's interval is counted (a row inside the inter-trial wait; a zero tail margin
        # excludes the insert_text that arrives after trial_end)
        stray = [{"kind": "ev", "t_wall_ms": T0 - 5000, "t_mono_ns": 0, "ev": "focus_in"}]
        r2 = run(mac, "mac_nstic", extra_gt=stray)
        check(r2["unassigned_events"] == 1 and r2["unassigned_events_by_ev"] == {"focus_in": 1}, "a row inside no trial's interval is counted in unassigned_events, with a per-ev breakdown")
        r3 = run(mac, "mac_nstic", tail=0)
        check(r3["unassigned_events"] == 1 and r3["by_scenario"]["S1"]["types"].get("set_marked+", 0) == 1,
              "with no tail margin, a call arriving after trial_end is excluded (the type changes)")

        # The interval is also cut at the "next oracle row": a reset / block_begin / marker of the next block is
        # never counted into the last trial of the previous block (call_types cuts here for the same reason).
        oracle4, plan4, gt4 = build(mac[:1], "mac_nstic")
        last_end = mac[0][2] + T0  # wall-clock time of t00001's trial_end
        cut_at = last_end + 40  # after block_end, at the position of the next block's marker (inside tail_ms=500)
        oracle4 = oracle4[:-1] + [{"kind": "marker", "block_id": 1, "seq": 0, "t_wall_ms": cut_at, "t_mono_ns": MONO0 + (cut_at - T0) * 1_000_000}, oracle4[-1]]
        leak = {"kind": "ev", "t_wall_ms": cut_at + 3, "t_mono_ns": MONO0 + (cut_at + 3 - T0) * 1_000_000, "ev": "focus_in"}
        gt4 = gt4 + [leak]
        paths4 = {n: os.path.join(tmp, "cut-" + n + ".jsonl") for n in ("oracle", "plan", "gt")}
        dump(paths4["oracle"], oracle4); dump(paths4["plan"], plan4); dump(paths4["gt"], gt4)
        trials4, inc4, cuts4, sw4, started4 = load_oracle(paths4["oracle"], "wall")
        s4, events4 = load_gt(paths4["gt"], "wall", {"key_down"})
        r4 = analyze(s4, events4, trials4, load_plan(paths4["plan"]), 50, 500, cuts4, sw4, started4)
        check(r4["unassigned_events_by_ev"].get("focus_in", 0) == 1, "a call after the next block's marker is not counted into the previous trial, and shows up outside")
        check(all("focus_in" not in k for k in r4["by_scenario"]["S1"]["types"]), "the type never mixes in a call from the next block (%s)" % list(r4["by_scenario"]["S1"]["types"]))

        # Choosing the monotonic clock excludes everything, because gt's monotonic clock is offset by 2.5 s
        # (the clock choice matters)
        rm = run(mac, "mac_nstic", clock="mono")
        check(rm["unassigned_events"] == sum(len(x[3]) for x in mac) - 1, "--clock mono compares on the monotonic clock (nothing is assigned when the clocks disagree)")

        # Windows: notify appends imn, ui_* appends ui_kind
        win = [("t00001", "S1", 1000, [ev(10, "tsf_start"), ev(20, "tsf_update", len=1), ev(120, "tsf_update", len=2), ev(200, "ui_begin", ui_kind="candidate"),
                                        ev(210, "ui_update", ui_kind="candidate"), ev(400, "ui_end", ui_kind="candidate"), ev(410, "tsf_end", result_len=2)]),
               ("t00002", "S2", 1000, [ev(10, "start_comp"), ev(20, "notify", imn=5), ev(30, "notify", imn=5), ev(40, "comp", flags=8, comp_len=1, result_len=0), ev(50, "end_comp")])]
        r = run(win, "win_tsf")
        check(r["by_scenario"]["S1"]["types"] == {"tsf_start → tsf_update+ → ui_begin[candidate] → ui_update[candidate] → ui_end[candidate] → tsf_end": 1},
              "ui_* appends ui_kind (TSF)")
        check(r["by_scenario"]["S2"]["types"] == {"start_comp → notify[5]+ → comp → end_comp": 1}, "notify appends imn (IMM)")

        # Fix the double-count between planned_not_run and trials_incomplete: a trial absent from plan is already
        # rejected on the unknown-trial path, so instead build "a trial that is in plan but never appears in
        # oracle" and "a trial that is in plan and has a trial_begin in oracle but no trial_end (incomplete)" to
        # tell the two apart.
        oracle5, plan5, gt5 = build(mac[:2], "mac_nstic")
        plan5.append({"kind": "trial", "trial_id": "t99999", "scenario": "S9"})  # in plan but never appears in oracle
        incomplete_row = {"kind": "trial_begin", "trial_id": "t88888", "t_wall_ms": T0 + 50_000, "t_mono_ns": MONO0 + 50_000_000_000}
        plan5.append({"kind": "trial", "trial_id": "t88888", "scenario": "S9"})
        oracle5 = oracle5[:-1] + [incomplete_row, oracle5[-1]]
        paths5 = {n: os.path.join(tmp, "dup-" + n + ".jsonl") for n in ("oracle", "plan", "gt")}
        dump(paths5["oracle"], oracle5); dump(paths5["plan"], plan5); dump(paths5["gt"], gt5)
        trials5, inc5, cuts5, sw5, started5 = load_oracle(paths5["oracle"], "wall")
        s5, events5 = load_gt(paths5["gt"], "wall", {"key_down"})
        r5 = analyze(s5, events5, trials5, load_plan(paths5["plan"]), 50, 500, cuts5, sw5, started5)
        r5["trials_incomplete"] = inc5
        check(inc5 == 1, "t88888 is incomplete, with only a trial_begin (trials_incomplete=%d)" % inc5)
        check(r5["planned_not_run"] == 1, "planned_not_run counts only t99999, which truly never ran (t88888 stays on the trials_incomplete side, not double-counted; measured %d)" % r5["planned_not_run"])

        # input contract and exit codes
        me = os.path.abspath(__file__)
        oracle, plan, gt = build(mac, "mac_nstic")
        paths = {n: os.path.join(tmp, "cli-" + n + ".jsonl") for n in ("oracle", "plan", "gt")}
        dump(paths["oracle"], oracle); dump(paths["plan"], plan); dump(paths["gt"], gt)
        out = os.path.join(tmp, "out.json")

        def cli(o=out, **override):
            p = dict(paths)
            p.update(override)
            r = subprocess.run([sys.executable, me, "--gt", p["gt"], "--oracle", p["oracle"], "--plan", p["plan"], "--out", o],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return r.returncode, r.stdout.decode("utf-8", "replace")

        rc, text = cli()
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        check(rc == 0 and data["by_scenario"]["S1"]["types"]["set_marked+ → insert_text"] == 2, "CLI: the output JSON carries per-scenario types (exit %d)" % rc)
        dump(paths["plan"] + "x", [x for x in plan if x.get("trial_id") != "t00001"])
        rc, text = cli(o=out + "2", plan=paths["plan"] + "x")
        check(rc == 2 and not os.path.exists(out + "2"), "refuses when an oracle trial is missing from plan (exit %d, no output written)" % rc)
        dump(paths["oracle"] + "x", [{"kind": "header", "format": "oracle/1"}])
        rc, _ = cli(o=out + "3", oracle=paths["oracle"] + "x")
        check(rc == 1, "an empty run (no completed trial at all) exits 1 (exit %d)" % rc)
        rc, _ = cli(o=out + "4", gt=os.path.join(tmp, "nothing"))
        check(rc == 2, "an unopenable gt exits 2 (exit %d)" % rc)

    if fails:
        print("  ---- call_types selftest: %d failed ----" % len(fails))
        return 1
    print("  ---- call_types selftest: all green ----")
    return 0
# --- selftest end ---


def main(argv=None):
    ap = argparse.ArgumentParser(description="Count per-trial call-sequence types and report their distribution by scenario, together with each trial's wall-clock duration")
    ap.add_argument("--selftest", action="store_true", help="run the positive and negative controls")
    ap.add_argument("--gt", help="raw ground-truth log (gt/1)")
    ap.add_argument("--oracle", help="runner output (oracle/1)")
    ap.add_argument("--plan", help="plan (plan/1)")
    ap.add_argument("--out", help="destination for the result JSON")
    ap.add_argument("--clock", choices=("wall", "mono"), default="wall", help="the clock used to compare gt and oracle times (default wall)")
    ap.add_argument("--pre-ms", type=int, default=50, help="margin before a trial's interval (default 50)")
    ap.add_argument("--tail-ms", type=int, default=500, help="margin after a trial's interval (default 500)")
    ap.add_argument("--skip-ev", default="key_down", help="comma-separated evs to exclude from the type (default key_down)")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not (args.gt and args.oracle and args.plan and args.out):
        ap.error("--gt / --oracle / --plan / --out are all required")
    if args.pre_ms < 0 or args.tail_ms < 0:
        ap.error("--pre-ms / --tail-ms must be 0 or greater")
    skip = set(x for x in args.skip_ev.split(",") if x)
    try:
        trials, incomplete, cuts, session_wall_ms, started = load_oracle(args.oracle, args.clock)
        src, events = load_gt(args.gt, args.clock, skip)
        result = analyze(src, events, trials, load_plan(args.plan), args.pre_ms, args.tail_ms, cuts, session_wall_ms, started)
    except InputError as e:
        print("call_types: %s" % e, file=sys.stderr)
        return 2
    result["trials_incomplete"] = incomplete
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("src %s, trials %d (incomplete %d, planned but not run %d), rows outside every interval %d" % (
        result["src"], result["trials_measured"], incomplete, result["planned_not_run"], result["unassigned_events"]))
    for sc, s in result["by_scenario"].items():
        d = s["duration_ms"]
        print("%s (%d trials, duration median %s ms, p95 %s ms)" % (sc, s["trials"], d["median_ms"], d["p95_ms"]))
        for typ, n in s["types"].items():
            print("  %4d  %s" % (n, typ))
    if result["trials_measured"] == 0:
        print("call_types: no completed trial at all (check oracle's trial_begin / trial_end and plan)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
