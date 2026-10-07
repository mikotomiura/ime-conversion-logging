"""Exploratory analysis, not registered: the distribution of the dwell error per speed.

The registered analysis summarises the difference of `dwell_ms` from the duration of the ground-truth session (observed
minus ground truth) by its median absolute value and by the Bland-Altman bias and 95% limits of agreement
(`l1.py`, `dwell_error`). This script reports, for the same one-to-one pairs, the 2.5th and 97.5th percentiles of the
difference and the fractions of pairs whose absolute difference is at most 100 ms and at most 1,000 ms.

It runs the frozen analysis unchanged and only listens to it: the analysis code is imported from an export of a
freezing tag (`--code`), the whole analysis is run as registered into a scratch directory outside the repository, and
every call of `l1.evaluate` is recorded. The pairs of the primary comparison (ground truth against the logger's rows)
are those whose observation input has the SHA-256 of the run's `obs.jsonl`; the speed of a pair comes from the plan,
as in `l1.py`. Before anything is written, the script recomputes, per speed, the number of pairs, the bias and the
median absolute difference with the frozen `stats.py` and requires them to equal the values of the frozen `e1_l1.json`
written in the scratch directory, it requires the per-speed duration figures of that file to equal those of the
registered result file of the article (`--registered`), and it takes the pairs only from the matching output whose
SHA-256 the registered result file records, so the pairs are those of the registered analysis. The output records that
SHA-256 per scope and the SHA-256 of every analysis module of the export it ran.

Percentiles use linear interpolation between order statistics: for sorted values x[0..n-1] and probability p, the
position is h = (n - 1) p, and the value is x[floor(h)] + (h - floor(h)) (x[floor(h) + 1] - x[floor(h)]). No interval
is computed. A pair whose observed `dwell_ms` is 0 is left out, as in the registered analysis.

usage:
  python explore_dwell_quantiles.py --code <export of the freezing tag> --run <run directory> \\
      --config <experiment/config.json> --seed <n> --seed-file <experiment/SEED> --prereg <preregistration.md> \\
      --registered <experiment/results/.../e1_l1.json> --scratch <empty directory outside this directory> \\
      --out <result file>
  python explore_dwell_quantiles.py --selftest
"""

import argparse
import hashlib
import json
import math
import os
import sys

SPEEDS = ("slow", "mid", "fast")
SCOPES = ("under_limit", "all_trials")
PRIMARY = "gt_obs"
PERCENTILES = (0.025, 0.975)
WITHIN_MS = (100, 1000)
DEFINITION_OF_OS = {"macos": "macos_def", "windows": "windows_def"}


def percentile(sorted_values, p):
    """Linear interpolation between order statistics (see the module docstring)."""
    n = len(sorted_values)
    if n == 0:
        return None
    h = (n - 1) * p
    lo = int(math.floor(h))
    if lo + 1 >= n:
        return float(sorted_values[-1])
    return sorted_values[lo] + (h - lo) * (sorted_values[lo + 1] - sorted_values[lo])


def summarise(diffs):
    """The exploratory figures of one list of differences (ms)."""
    xs = sorted(diffs)
    n = len(xs)
    out = {"n": n, "percentiles": {}, "within": {}}
    for p in PERCENTILES:
        out["percentiles"]["p%s" % ("%g" % (100 * p))] = percentile(xs, p)
    for w in WITHIN_MS:
        out["within"]["abs_le_%d_ms" % w] = (sum(1 for d in xs if abs(d) <= w) / float(n)) if n else None
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Listener(object):
    """Records, for every call of l1.evaluate, the dwell differences of the pairs per speed."""

    def __init__(self, l1, match):
        self.l1, self.match = l1, match
        self.calls = []
        self.original = l1.evaluate

    def __call__(self, match_bytes, sessions_bytes, plan_bytes, oracle_bytes, config_bytes=None):
        result = self.original(match_bytes, sessions_bytes, plan_bytes, oracle_bytes, config_bytes)
        m = self.match._json_loads(self.match._decode(match_bytes, "match"), "match", True)
        plan = self.match.parse_plan_strata(plan_bytes)
        cell = m["config"]["cell"]
        by_speed = {}
        for p in m["matching"]["pairs"]:
            obs = p["obs"]["dwell_ms"]
            if obs == 0:
                continue
            lab = self.l1._label(plan["trials"], cell, p["gt"].get("trial_id"))
            gt = p["gt"]["end_wall_ms"] - p["gt"]["start_wall_ms"]
            by_speed.setdefault(lab[2], []).append((obs, gt))
        self.calls.append({"cell": cell, "definition": m.get("definition"),
                           "scope": (m.get("scope") or {}).get("name", "all_trials"),
                           "obs_sha256": m["inputs_sha256"]["obs"],
                           "match_sha256": hashlib.sha256(match_bytes).hexdigest(), "by_speed": by_speed})
        return result


def reproduces(stats, pairs, entry):
    """Whether the pairs reproduce the registered n, bias and median absolute difference of a speed exactly."""
    obs = [o for o, _g in pairs]
    gt = [g for _o, g in pairs]
    dw = entry.get("dwell")
    if not dw or len(obs) != dw["n"]:
        return False
    if not obs:  # a speed whose every pair was left out: nothing more to compare
        return True
    return (stats.bland_altman(obs, gt)["bias"] == dw["bland_altman"]["bias"]
            and stats.median_abs_error(obs, gt) == dw["mae"])


def primary_call(stats, calls, obs_sha, definition, scope, want, reg_by, where):
    """The recorded call of the primary comparison for one scope: it read the run's observation log under the cell's
    definition, its matching output has the SHA-256 that the registered result file records (want), and its pairs
    reproduce the registered figures of every speed."""
    candidates = [c for c in calls
                  if c["obs_sha256"] == obs_sha and c["definition"] == definition and c["scope"] == scope
                  and c["match_sha256"] == want]
    return select_primary(stats, candidates, reg_by, where)


def select_primary(stats, candidates, reg_by, where):
    """Among the recorded calls that read the run's observation log for one definition and scope (on macOS the
    comparison of the prediction with the logger reads it too), the one whose pairs reproduce the registered values of
    the primary comparison at every speed. Exactly one must."""
    good = []
    for call in candidates:
        ok = True
        for sp in SPEEDS:
            pairs = call["by_speed"].get(sp, [])
            if sp in reg_by:
                ok = ok and reproduces(stats, pairs, reg_by[sp])
            elif pairs:
                ok = False
        if ok:
            good.append(call)
    if len(good) != 1:
        raise SystemExit("%s: %d recorded calls reproduce the registered values of the primary comparison, not 1"
                         % (where, len(good)))
    return good[0]


HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # the directory that holds kit/ and experiment/
DWELL_KEYS = ("n", "n_zero_excluded", "mae", "bland_altman")


def inside(path, root):
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def check_paths(args):
    """Refuse before any work: an existing --out (never overwritten) and a scratch directory inside this directory
    (the frozen analysis writes its result files there, which must not be mistaken for the article's)."""
    if os.path.exists(args.out):
        raise SystemExit("--out exists; it is not overwritten")
    if inside(args.scratch, ROOT):
        raise SystemExit("--scratch must lie outside %s" % ROOT)
    if os.path.isdir(args.scratch) and os.listdir(args.scratch):
        raise SystemExit("--scratch must be empty")


def same_dwell(scratch_block, registered_block, where):
    """The per-speed duration figures of the frozen run must equal those of the registered result file."""
    a = dict((e["stratum"].get("speed"), e.get("dwell")) for e in scratch_block)
    b = dict((e["stratum"].get("speed"), e.get("dwell")) for e in registered_block)
    for sp in SPEEDS:
        x, y = a.get(sp), b.get(sp)
        if (x is None) != (y is None):
            raise SystemExit("%s, %s: the run and the registered result file differ in having a value" % (where, sp))
        if x is not None and any(x.get(k) != y.get(k) for k in DWELL_KEYS):
            raise SystemExit("%s, %s: the duration figures differ from the registered result file" % (where, sp))


def run(args):
    check_paths(args)
    code = os.path.abspath(os.path.join(args.code, "kit", "analysis"))
    if not os.path.isfile(os.path.join(code, "pipeline.py")):
        raise SystemExit("--code: no kit/analysis/pipeline.py under %s" % args.code)
    sys.path.insert(0, code)
    import l1  # noqa: E402  (from the export)
    import match  # noqa: E402
    import pipeline  # noqa: E402
    import stats  # noqa: E402
    for mod in (l1, match, pipeline, stats):
        if os.path.dirname(os.path.abspath(mod.__file__)) != code:
            raise SystemExit("%s was not imported from the export" % mod.__name__)
    listener = Listener(l1, match)
    l1.evaluate = listener
    os.makedirs(args.scratch, exist_ok=True)
    if os.listdir(args.scratch):
        raise SystemExit("--scratch must be empty")
    pipeline.analyse([args.run], args.config, args.seed, args.scratch, prereg=args.prereg, seed_file=args.seed_file)
    with open(os.path.join(args.run, "run.json"), "rb") as f:
        manifest = json.loads(f.read().decode("utf-8"))
    system = manifest["cell"]["os"]
    definition = DEFINITION_OF_OS[system]
    obs_sha = sha256_file(os.path.join(args.run, "obs.jsonl"))
    with open(os.path.join(args.scratch, "e1_l1.json"), "rb") as f:
        frozen = json.loads(f.read().decode("utf-8"))
    with open(args.registered, "rb") as f:
        committed = json.loads(f.read().decode("utf-8"))
    out = {"exploratory": True,
           "note": "not registered; percentiles of the dwell error (observed minus ground truth, ms) of the one-to-one "
                   "pairs of the primary comparison, and the fractions of pairs within 100 ms and 1,000 ms; no interval",
           "percentile_method": "linear interpolation between order statistics, h = (n - 1) p",
           "inputs_sha256": {"run_manifest": sha256_file(os.path.join(args.run, "run.json")), "obs": obs_sha,
                             "config": sha256_file(args.config), "script": sha256_file(os.path.abspath(__file__)),
                             "e1_l1_of_this_run": sha256_file(os.path.join(args.scratch, "e1_l1.json")),
                             "e1_l1_registered": sha256_file(args.registered)},
           "analysis_modules_sha256": dict((n, sha256_file(os.path.join(code, n)))
                                           for n in sorted(os.listdir(code)) if n.endswith(".py")),
           "cells": {}}
    cells = sorted(set(c["cell"] for c in listener.calls))
    if len(cells) != 1:
        raise SystemExit("one run gives one cell; the calls name %s" % cells)
    cell = cells[0]
    for scope in SCOPES:
        reg = frozen["cells"][cell][definition][PRIMARY][scope]["metrics"]["by_speed"]
        try:
            com = committed["cells"][cell][definition][PRIMARY][scope]["metrics"]["by_speed"]
        except (KeyError, TypeError):
            raise SystemExit("--registered: no cell %s under %s, %s, %s" % (cell, definition, PRIMARY, scope))
        same_dwell(reg, com, "cell %s, %s" % (cell, scope))
        # The registered result records the SHA-256 of the matching output it read; the pairs taken here must come from
        # an output with that SHA-256, so they are the registered pairs themselves, not pairs with equal summaries.
        want = committed["cells"][cell][definition][PRIMARY][scope]["inputs_sha256"]["match"]
        reg_by = dict((e["stratum"]["speed"], e) for e in reg if e["stratum"].get("speed") in SPEEDS)
        call = primary_call(stats, listener.calls, obs_sha, definition, scope, want, reg_by,
                            "cell %s, %s" % (cell, scope))
        block = dict((sp, summarise([o - g for o, g in call["by_speed"].get(sp, [])])) for sp in SPEEDS if sp in reg_by)
        block["match_sha256"] = want
        out["cells"].setdefault(cell, {})[scope] = block
    try:
        f = open(args.out, "x", encoding="utf-8", newline="\n")
    except FileExistsError:
        raise SystemExit("--out exists; it is not overwritten")
    with f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote %s (cells: %s)" % (args.out, ", ".join(sorted(out["cells"]))))


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else " " + str(detail)))
        if not cond:
            fails.append(name)

    check("percentile of one value", percentile([5], 0.025) == 5.0 and percentile([5], 0.975) == 5.0)
    xs = [0, 10, 20, 30, 40]
    check("percentile interpolates", abs(percentile(xs, 0.5) - 20) < 1e-12 and abs(percentile(xs, 0.1) - 4) < 1e-12,
          (percentile(xs, 0.5), percentile(xs, 0.1)))
    check("percentile of the ends", percentile(xs, 0.0) == 0 and percentile(xs, 1.0) == 40)
    check("percentile of no value", percentile([], 0.5) is None)
    s = summarise([-1500, -100, -50, 0, 99, 101, 1000, 1001])
    check("within 100 ms counts |d| <= 100", s["within"]["abs_le_100_ms"] == 4 / 8.0, s["within"])
    check("within 1000 ms counts |d| <= 1000", s["within"]["abs_le_1000_ms"] == 6 / 8.0, s["within"])
    check("summarise sorts before the percentiles", summarise([3, 1, 2])["percentiles"]["p2.5"] == 1.05,
          summarise([3, 1, 2])["percentiles"])
    check("summarise of no pair", summarise([])["n"] == 0 and summarise([])["within"]["abs_le_100_ms"] is None)

    class Stub(object):
        @staticmethod
        def bland_altman(obs, gt):
            return {"bias": sum(o - g for o, g in zip(obs, gt)) / float(len(obs))}

        @staticmethod
        def median_abs_error(obs, gt):
            d = sorted(abs(o - g) for o, g in zip(obs, gt))
            return float(d[len(d) // 2]) if len(d) % 2 else (d[len(d) // 2 - 1] + d[len(d) // 2]) / 2.0

    primary = {"by_speed": {"slow": [(110, 100), (90, 100)], "mid": [(205, 200)]}}
    other = {"by_speed": {"slow": [(110, 100), (90, 100)], "mid": [(260, 200)]}}
    reg = {"slow": {"dwell": {"n": 2, "bland_altman": {"bias": 0.0}, "mae": 10.0}},
           "mid": {"dwell": {"n": 1, "bland_altman": {"bias": 5.0}, "mae": 5.0}}}
    check("the call that reproduces every speed is chosen", select_primary(Stub, [other, primary], reg, "t") is primary)
    try:
        select_primary(Stub, [primary, dict(primary)], reg, "t")
        check("two reproducing calls are refused", False)
    except SystemExit:
        check("two reproducing calls are refused", True)
    try:
        select_primary(Stub, [other], reg, "t")
        check("no reproducing call is refused", False)
    except SystemExit:
        check("no reproducing call is refused", True)
    check("a registered entry without a duration value is not reproduced",
          reproduces(Stub, [(1, 0)], {"dwell": None}) is False and reproduces(Stub, [(1, 0)], {}) is False)
    def safe_reproduces(pairs, entry):
        try:
            return reproduces(Stub, pairs, entry)
        except Exception:  # a crash is a failure of the check below, not of the self-test
            return None

    check("a speed without pairs is reproduced by no pairs, and only by them",
          safe_reproduces([], {"dwell": {"n": 0}}) is True and safe_reproduces([(1, 0)], {"dwell": {"n": 0}}) is False)
    import tempfile

    class A(object):
        pass

    tmp = tempfile.mkdtemp(prefix="explore_selftest_")
    try:
        a = A()
        a.out, a.scratch = os.path.join(tmp, "out.json"), os.path.join(tmp, "scratch")
        check_paths(a)
        check("paths: a new output and a scratch directory outside this directory pass", True)
        for name, change in (("an existing output", lambda: open(a.out, "w").close()),
                             ("a scratch directory inside this directory",
                              lambda: setattr(a, "scratch", os.path.join(ROOT, "experiment", "scratch-test"))),
                             ("a scratch directory that is not empty", None)):
            b = A()
            b.out, b.scratch = os.path.join(tmp, "out2.json"), os.path.join(tmp, "scratch2")
            if name == "an existing output":
                b.out = a.out
                change()
            elif name == "a scratch directory inside this directory":
                b.scratch = os.path.join(ROOT, "experiment", "scratch-test")
            else:
                os.makedirs(b.scratch)
                open(os.path.join(b.scratch, "x"), "w").close()
            try:
                check_paths(b)
                check("paths: %s is refused" % name, False)
            except SystemExit:
                check("paths: %s is refused" % name, True)
        check("paths: the check made no directory", not os.path.exists(os.path.join(ROOT, "experiment", "scratch-test")))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    blk = lambda sp, n, mae: {"stratum": {"speed": sp}, "dwell": {"n": n, "n_zero_excluded": 0, "mae": mae,
                                                                   "bland_altman": {"bias": 1.0}}}
    same_dwell([blk("slow", 3, 2.0)], [blk("slow", 3, 2.0)], "t")
    check("registered: equal duration figures pass", True)
    for name, other in (("a different number of pairs", [blk("slow", 4, 2.0)]),
                        ("a different median absolute difference", [blk("slow", 3, 2.5)]),
                        ("a speed missing on one side", []),
                        ("a speed present on one side only", [blk("slow", 3, 2.0), blk("fast", 1, 0.0)])):
        try:
            same_dwell([blk("slow", 3, 2.0)], other, "t")
            check("registered: %s is refused" % name, False)
        except SystemExit:
            check("registered: %s is refused" % name, True)
    # Two matching outputs whose pairs have the same number, bias and median absolute difference but different tails:
    # only the one with the registered SHA-256 may be taken.
    reg2 = {"slow": {"dwell": {"n": 3, "bland_altman": {"bias": 0.0}, "mae": 10.0}}}
    narrow = {"obs_sha256": "o", "definition": "d", "scope": "s", "match_sha256": "m1",
              "by_speed": {"slow": [(90, 100), (100, 100), (110, 100)]}}
    wide = {"obs_sha256": "o", "definition": "d", "scope": "s", "match_sha256": "m2",
            "by_speed": {"slow": [(990, 1000), (1000, 1000), (1010, 1000)]}}
    check("pairs with equal summaries: both reproduce the registered figures",
          select_primary(Stub, [narrow], reg2, "t") is narrow and select_primary(Stub, [wide], reg2, "t") is wide)
    def chosen(want):
        try:
            return primary_call(Stub, [narrow, wide], "o", "d", "s", want, reg2, "t")
        except SystemExit:
            return None

    check("the call is chosen by the SHA-256 of its matching output",
          chosen("m2") is wide and chosen("m1") is narrow)
    try:
        primary_call(Stub, [narrow, wide], "o", "d", "s", "m3", reg2, "t")
        check("no matching output with the registered SHA-256 is refused", False)
    except SystemExit:
        check("no matching output with the registered SHA-256 is refused", True)
    extra = {"by_speed": {"slow": [(110, 100), (90, 100)], "mid": [(205, 200)], "fast": [(1, 0)]}}
    try:
        select_primary(Stub, [extra], reg, "t")
        check("pairs at a speed with no registered entry are refused", False)
    except SystemExit:
        check("pairs at a speed with no registered entry are refused", True)
    print("%d failed" % len(fails))
    return 1 if fails else 0


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("--code", "--run", "--config", "--seed-file", "--prereg", "--registered", "--scratch", "--out"):
        ap.add_argument(name, required=True)
    ap.add_argument("--seed", required=True, type=int)
    run(ap.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
