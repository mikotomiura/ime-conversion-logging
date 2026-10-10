#!/usr/bin/env python3
# kit: selftest
"""Exploratory analysis, not registered: the closed-set attack on two feature sets that drop the lengths of runs.

  explore_run_lengths.py --obs OBS --plan PLAN --corpus F --recipes F --config F --registered RESULT \\
      [--K 10,100,1000] [--seed N] [--bootstrap B] [--markers-in-obs] [--skip-scenario S]... --out F
  explore_run_lengths.py --selftest

Why (deviation D13, experiment/deviations.md). The registration describes the feature sets no_runs, bin1s_count and
counts_only as having no lengths of letter runs. In closed_set.py, runs="count" stamps every row of a run of letter
keys with the time of its first row and makes the run one token whose count is the number of its presses; the distance
charges the difference of the counts. The length of a run is therefore kept, and what these sets drop is the time of
every press of a run after its first. With no times, the tokens of counts_only expanded by their counts give the
category of every press in order, which is what no_time keeps: the two sets hold the same information and differ only
in how the distance counts a run of letter keys.

This script runs the same attack on two feature sets that do drop the lengths of runs, without any time:

  letter_runs_unit  every run of consecutive letter-key presses is one token of count 1; every other press is a token
                    of count 1; the order of the tokens is kept (the registered intent of "no lengths of letter runs")
  all_runs_unit     every run of consecutive presses of the same category, whatever the category, is one token of
                    count 1 (the numbers of Backspace, arrow and other repeated presses are dropped as well); the order
                    is kept

all_runs_unit is derivable from letter_runs_unit, so, by the registered reporting rule, the accuracy reported for
letter_runs_unit is the larger of the two measured accuracies and that of all_runs_unit its own (acc_source names the
set it came from). The values of the six registered feature sets are not changed.

The attack is that of closed_set.py, imported unchanged: the predicted logs (Predictor.raw with the seed), the distance
(_distance with no time), the candidates of each trial (_make_candidates from the trial's own seed), the tie-breaking
(the seed of the trial, the tag "tie" and the name of the feature set), the Wilson intervals and the descriptive
bootstrap that resamples blocks. Only the tokens are new. The evaluation loop of closed_set.evaluate is copied here
with the token function as a parameter; two checks show that the copy is the registered procedure:

  - the self-test runs the copy and closed_set.evaluate on a synthetic world for no_time and counts_only and requires
    the two outputs to be equal;
  - every run also computes no_time and counts_only with the copy and requires every value of them, at every K, to
    equal the registered result file of the cell (--registered); the inputs (the SHA-256 of the corpus, procedures and
    settings, the seed, the number of resamples, the alignment and the trials left out) are compared as well. On any
    difference nothing is written and the exit status is 2.

The output (format e2-closed-posthoc/1) holds, per K, the values of the two new sets in the shape of the registered
result file, the SHA-256 of every input and of the code, and the result of the comparison. The near-chance judgement is
made from it by manuscript/tools/results_tables.py with the registered rule. --out is created fresh, never overwritten.
The switch --ime-toggle-in-obs of closed_set.py is not offered: the registered runs did not use it. The procedure files
must be those the plan was built from (for the macOS cells, those of their freezing tag; the plan records their
SHA-256 and the run stops otherwise).
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_KIT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_KIT, "gen"), os.path.join(_KIT, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import closed_set  # noqa: E402
import generate_plan  # noqa: E402
import stats  # noqa: E402
from simulate_obs import apply_features  # noqa: E402

FORMAT = "e2-closed-posthoc/1"
CONTROLS = ("no_time", "counts_only")
NEW_SETS = ("letter_runs_unit", "all_runs_unit")
# The sets over which the reported accuracy of a set is the maximum: the set and the measured sets derivable from it.
# For the two registered sets this is the registered closure (closed_set.coarser_or_equal over the six sets).
CLOSURE = {
    "no_time": ("no_time", "counts_only"),
    "counts_only": ("counts_only",),
    "letter_runs_unit": ("letter_runs_unit", "all_runs_unit"),
    "all_runs_unit": ("all_runs_unit",),
}
CODE_FILES = ("e2/closed_set.py", "e2/simulate_obs.py", "e2/explore_run_lengths.py", "gen/generate_plan.py",
              "gen/romaji.py", "analysis/stats.py")
NOTE = ("Exploratory analysis, not registered (deviation D13): the registered closed-set attack on two feature sets "
        "without times that drop the lengths of runs. The registered values and recommendation are unchanged.")


# ---------------------------------------------------------------- tokens

def _registered_tokens(events, runs):
    """The tokens of the registered set with no time and the given runs, as closed_set.attack and Predictor.tokens
    build them."""
    return closed_set._tokens(closed_set._normalize(apply_features(list(events), "none", runs)), runs)


def _unit_tokens(events, letters_only):
    """One token of count 1 per run of the same category (letter keys only when letters_only), in order, no time."""
    out = []
    for _, c in events:
        if out and out[-1][0] == c and (c == "alphabetic" or not letters_only):
            continue
        out.append((c, 1, 0))
    return tuple(out)


TOKENS = {
    "no_time": lambda ev: _registered_tokens(ev, "exact"),
    "counts_only": lambda ev: _registered_tokens(ev, "count"),
    "letter_runs_unit": lambda ev: _unit_tokens(ev, True),
    "all_runs_unit": lambda ev: _unit_tokens(ev, False),
}


# ---------------------------------------------------------------- attack and evaluation (copied from closed_set)

def _choose(obs_tok, pred_toks, tie_seed, true_index):
    """closed_set.attack with one predicted sequence per candidate (None: the phrase cannot be typed). Returns (chosen
    index, 1-based rank of true_index)."""
    memo = {}
    dist = []
    for pt in pred_toks:
        if pt is None:
            dist.append(float("inf"))
            continue
        if pt not in memo:
            memo[pt] = closed_set._distance(obs_tok, pt, "none")
        dist.append(memo[pt])
    rng = random.Random(closed_set._sub_seed(tie_seed, "tie"))
    keys = [rng.random() for _ in pred_toks]
    order = sorted(range(len(pred_toks)), key=lambda i: (dist[i], keys[i]))
    return order[0], order.index(true_index) + 1


def evaluate(trials, corpus, K, sets, seed, recipes, config, B=closed_set.DEFAULT_BOOTSTRAP):
    """closed_set.evaluate with the feature sets given by name in TOKENS and the closure of CLOSURE (restricted to
    the sets evaluated). Returns the same structure."""
    if not isinstance(K, int) or K < 2:
        raise ValueError("K must be an integer of at least 2")
    by_id = dict((p["phrase_id"], p) for p in corpus)
    base_pool = closed_set._pool_by_reading(corpus)
    if len(base_pool) < K:
        raise ValueError("the corpus has %d distinct readings, fewer than K = %d" % (len(base_pool), K))
    used = [t for t in trials if t["phrase_id"] in by_id]
    preds = {}
    pools = {}
    typed = {}
    tok_cache = {}
    res = dict((f, {"correct": 0, "ranks": [], "by_scenario": {}, "by_speed": {}, "hits": []}) for f in sets)
    for tr in used:
        sk = (tr["scenario"], tr["os"])
        if sk not in pools:
            pools[sk] = closed_set._scenario_pool(recipes, tr["os"], tr["scenario"], base_pool)
            typed[sk] = closed_set._typed_map(recipes, config, tr["os"], tr["scenario"], pools[sk])
        true_ph = by_id[tr["phrase_id"]]
        true_typed = closed_set._typed_reading(recipes, config, true_ph, tr["scenario"], tr["os"])
        tseed = closed_set._sub_seed(seed, "trial", tr["trial_id"], K)
        cands, ti = closed_set._make_candidates(random.Random(closed_set._sub_seed(tseed, "cand")), list(pools[sk]),
                                                true_ph, K, typed[sk], true_typed)
        if tr["os"] not in preds:
            preds[tr["os"]] = closed_set.Predictor(recipes, config, tr["os"], seed)
        pred = preds[tr["os"]]
        for f in sets:
            pts = []
            for ph in cands:
                key = (f, tr["os"], ph["phrase_id"], tr["scenario"], tr["speed"])
                if key not in tok_cache:
                    ev = pred.raw(ph, tr["scenario"], tr["speed"])
                    tok_cache[key] = None if ev is None else TOKENS[f](ev)
                pts.append(tok_cache[key])
            chosen, rank = _choose(TOKENS[f](tr["observed"]), pts, closed_set._sub_seed(tseed, "tie", f), ti)
            chosen_typed = true_typed if chosen == ti else typed[sk][cands[chosen]["phrase_id"]]
            correct = 1 if chosen_typed == true_typed else 0
            res[f]["correct"] += correct
            res[f]["ranks"].append(rank)
            bysc = res[f]["by_scenario"].setdefault(tr["scenario"], {"correct": 0, "n": 0})
            bysc["n"] += 1
            bysc["correct"] += correct
            bysp = res[f]["by_speed"].setdefault(tr["speed"], {"correct": 0, "n": 0})
            bysp["n"] += 1
            bysp["correct"] += correct
            res[f]["hits"].append(correct)
    out = {}
    n = len(used)
    all_x = dict((f, res[f]["correct"]) for f in sets)
    cluster_of = {}
    for i, tr in enumerate(used):
        c = cluster_of.setdefault(tr["cluster"], [0] + [0] * len(sets))
        c[0] += 1
        for j, f in enumerate(sets):
            c[1 + j] += res[f]["hits"][i]
    boot_clusters = [(tuple(c),) for c in cluster_of.values()]
    index = dict((f, j + 1) for j, f in enumerate(sets))
    for f in sets:
        closure = [g for g in CLOSURE[f] if g in sets]
        best_g = min(closure, key=lambda g: (-all_x[g], g))
        x_rep, x_raw = all_x[best_g], all_x[f]
        lo, hi = stats.wilson(x_rep, n)
        lo_r, hi_r = stats.wilson(x_raw, n)
        by_sc = dict((s, {"acc": (v["correct"] / v["n"]) if v["n"] else None, "n": v["n"]})
                     for s, v in res[f]["by_scenario"].items())
        by_sp = dict((s, {"acc": (v["correct"] / v["n"]) if v["n"] else None, "n": v["n"]})
                     for s, v in res[f]["by_speed"].items())
        if n and len(boot_clusters) >= 2:
            members = [index[g] for g in closure]
            col = index[f]
            bseed = closed_set._sub_seed(seed, "block_bootstrap", K)

            def stat_rep(sample, members=members):
                tot = sum(c[0][0] for c in sample)
                return max(sum(c[0][m] for c in sample) for m in members) / tot

            def stat_raw(sample, col=col):
                return sum(c[0][col] for c in sample) / sum(c[0][0] for c in sample)
            ci_b = list(stats.cluster_bootstrap(boot_clusters, stat_rep, B, bseed))
            ci_rb = list(stats.cluster_bootstrap(boot_clusters, stat_raw, B, bseed))
            status = "ok"
        else:
            ci_b, ci_rb, status = [None, None], [None, None], "fewer_than_two_blocks"
        out[f] = {"acc": (x_rep / n) if n else None, "acc_ci": [lo, hi], "acc_source": best_g,
                  "acc_ci_block": ci_b, "acc_ci_raw_block": ci_rb, "ci_block_status": status,
                  "n_blocks": len(boot_clusters), "by_speed": by_sp,
                  "acc_raw": (x_raw / n) if n else None, "acc_ci_raw": [lo_r, hi_r],
                  "chance": 1.0 / K, "n": n, "n_skipped": len(trials) - n,
                  "mean_rank": (sum(res[f]["ranks"]) / n) if n else None, "by_scenario": by_sc}
    return out


# ---------------------------------------------------------------- comparison with the registered result file

def _plain(x):
    """The value as it reads back from JSON (tuples become lists)."""
    return json.loads(json.dumps(x))


def compare_registered(registered, mine):
    """Differences between the registered result file and the run: the inputs, and every value of the two registered
    sets at every K. mine: {"seed", "bootstrap", "n_sessions", "n_trials", "alignment", "skipped_scenarios",
    "corpus_sha256", "recipes_sha256", "config_sha256", "params", "results": {K: {set: values}}}. Returns a list of
    strings (empty: the same)."""
    diffs = []
    for key in ("seed", "bootstrap", "n_sessions", "n_trials", "alignment", "skipped_scenarios", "corpus_sha256",
                "recipes_sha256", "config_sha256", "params"):
        if _plain(mine.get(key)) != registered.get(key):
            diffs.append("%s differs from the registered result file" % key)
    for f in CONTROLS:
        want = {"timing": closed_set.FEATURE_SETS[f][0], "runs": closed_set.FEATURE_SETS[f][1]}
        if registered.get("features", {}).get(f) != want:
            diffs.append("the registered result file does not hold %s as registered" % f)
    reg_res = registered.get("results", {})
    if sorted(reg_res) != sorted(mine["results"]):
        diffs.append("the values of K differ from the registered result file (%s, registered %s)" % (
            ",".join(sorted(mine["results"])), ",".join(sorted(reg_res))))
    for k in sorted(mine["results"]):
        for f in CONTROLS:
            got = _plain(mine["results"][k].get(f))
            exp = reg_res.get(k, {}).get(f)
            if got != exp:
                fields = sorted(set(got or {}) | set(exp or {}))
                bad = [x for x in fields if (got or {}).get(x) != (exp or {}).get(x)]
                diffs.append("K = %s, %s: %s differ from the registered result file" % (k, f, ", ".join(bad)))
    return diffs


# ---------------------------------------------------------------- run

def _sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_trials(obs_paths, plan_paths, recipes_path, config_path, markers_in_obs, skip_scenarios):
    """The reading part of closed_set.run: the trials to attack, the alignment and the counts it reports."""
    if len(obs_paths) != len(plan_paths):
        raise ValueError("--obs and --plan differ in count (%d and %d)" % (len(obs_paths), len(plan_paths)))
    recipes = generate_plan.load_recipes(recipes_path)
    recipes_sha256 = _sha_file(recipes_path)
    config_sha256 = _sha_file(config_path)
    with open(config_path, "rb") as f:
        try:
            config = closed_set._loads(f.read().decode("utf-8"))
        except ValueError:
            raise ValueError("the configuration is not JSON")
    generate_plan.normalize_config(config)
    trials = []
    alignments = []
    for i, (op, pp) in enumerate(zip(obs_paths, plan_paths)):
        plan = closed_set.read_plan(pp)
        for what, mine in (("recipes", recipes_sha256), ("config", config_sha256)):
            if plan[0].get("%s_sha256" % what) != mine:
                raise ValueError("plan %d was built from a different %s file than --%s" % (i + 1, what, what))
        events, _, obs_os = closed_set.read_obs(op)
        seg, al = closed_set.segment_by_plan(events, plan, obs_os, markers_in_obs, False, tag="s%d-" % i)
        trials.extend(seg)
        alignments.append(al)
    skipped = dict((s, 0) for s in skip_scenarios)
    attacked = []
    for t in trials:
        if t["scenario"] in skipped:
            skipped[t["scenario"]] += 1
        else:
            attacked.append(t)
    return {"recipes": recipes, "config": config, "trials": attacked, "n_trials": len(trials),
            "alignment": closed_set.merge_alignments(alignments), "skipped": skipped,
            "recipes_sha256": recipes_sha256, "config_sha256": config_sha256}


def run(obs_paths, plan_paths, corpus_path, recipes_path, config_path, registered_path, Ks, seed, markers_in_obs,
        bootstrap, skip_scenarios):
    with open(registered_path, "rb") as f:
        registered = closed_set._loads(f.read().decode("utf-8"))
    if not isinstance(registered, dict) or registered.get("format") != closed_set.FORMAT:
        raise ValueError("--registered is not a result file of closed_set.py (%s)" % closed_set.FORMAT)
    # The structure that compare_registered reads; another structure is an input error, reported as such (an error of
    # this script itself is not caught and keeps its traceback).
    res = registered.get("results")
    if (not isinstance(registered.get("features"), dict) or not isinstance(res, dict)
            or not all(isinstance(v, dict) and all(isinstance(r, dict) for r in v.values()) for v in res.values())):
        raise ValueError("--registered does not have the structure of a result file of closed_set.py")
    loaded = load_trials(obs_paths, plan_paths, recipes_path, config_path, markers_in_obs, skip_scenarios)
    corpus = generate_plan.load_corpus(corpus_path)
    results = {}
    for K in Ks:
        results[str(K)] = evaluate(loaded["trials"], corpus, K, CONTROLS + NEW_SETS, seed, loaded["recipes"],
                                   loaded["config"], B=bootstrap)
    mine = {"seed": seed, "bootstrap": bootstrap, "n_sessions": len(obs_paths), "n_trials": loaded["n_trials"],
            "alignment": loaded["alignment"],
            "skipped_scenarios": loaded["skipped"] if skip_scenarios else None,
            "corpus_sha256": _sha_file(corpus_path), "recipes_sha256": loaded["recipes_sha256"],
            "config_sha256": loaded["config_sha256"],
            "params": {"pause_ms": closed_set.PAUSE_MS, "tau_ms": closed_set.TAU_MS, "w_time": closed_set.W_TIME,
                       "phase_step_ms": closed_set.PHASE_STEP_MS},
            "results": results}
    diffs = compare_registered(registered, mine)
    if diffs:
        raise ValueError("the copy of the registered procedure does not reproduce the registered result file: "
                         + "; ".join(diffs))
    out = dict((k, v) for k, v in mine.items() if k != "results")
    out.update({
        "format": FORMAT, "note": NOTE,
        "sets": {"letter_runs_unit": "no time; every run of letter-key presses is one token of count 1; order kept",
                 "all_runs_unit": "no time; every run of presses of the same category is one token of count 1; "
                                  "order kept"},
        "closure": dict((f, list(CLOSURE[f])) for f in NEW_SETS),
        "registered_check": {"sets": list(CONTROLS), "K": [str(k) for k in Ks], "result": "identical",
                             "registered_sha256": _sha_file(registered_path)},
        "inputs_sha256": {"obs": [_sha_file(p) for p in obs_paths], "plan": [_sha_file(p) for p in plan_paths]},
        "code_sha256": dict((p, _sha_file(os.path.join(_KIT, p))) for p in CODE_FILES),
        "results": dict((k, dict((f, v[f]) for f in NEW_SETS)) for k, v in results.items()),
    })
    if out["skipped_scenarios"] is None:
        del out["skipped_scenarios"]
    return out


def render(result):
    return (json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------- selftest

def selftest():
    fails = []
    n_checks = [0]

    def check(name, cond, detail=""):
        n_checks[0] += 1
        if not cond:
            fails.append(name + ((": " + detail) if detail else ""))

    A = "alphabetic"
    seq = [(0, A), (5, A), (9, A), (20, "punctuation"), (30, A), (40, "backspace"), (41, "backspace"), (50, A),
           (60, "space"), (61, "space"), (70, "enter")]
    check("letter_runs_unit: letter runs become one token of count 1, other presses stay one token each",
          TOKENS["letter_runs_unit"](seq) == ((A, 1, 0), ("punctuation", 1, 0), (A, 1, 0), ("backspace", 1, 0),
                                              ("backspace", 1, 0), (A, 1, 0), ("space", 1, 0), ("space", 1, 0),
                                              ("enter", 1, 0)), str(TOKENS["letter_runs_unit"](seq)))
    check("all_runs_unit: every run of a category becomes one token of count 1",
          TOKENS["all_runs_unit"](seq) == ((A, 1, 0), ("punctuation", 1, 0), (A, 1, 0), ("backspace", 1, 0), (A, 1, 0),
                                           ("space", 1, 0), ("enter", 1, 0)), str(TOKENS["all_runs_unit"](seq)))
    check("counts_only: a letter run is one token whose count is its length",
          TOKENS["counts_only"](seq)[0] == (A, 3, 0), str(TOKENS["counts_only"](seq)))

    def ev(cats):
        return [(150 * i, c) for i, c in enumerate(cats)]

    def dist(f, x, y):
        return closed_set._distance(TOKENS[f](ev(x)), TOKENS[f](ev(y)), "none")
    ten, twenty = [A] * 10 + ["space", "enter"], [A] * 20 + ["space", "enter"]
    check("counts_only keeps the length of a letter run (10 against 20 letters cost 10)",
          dist("counts_only", ten, twenty) == 10.0, str(dist("counts_only", ten, twenty)))
    check("no_time: 10 against 20 letters cost 10", dist("no_time", ten, twenty) == 10.0)
    check("letter_runs_unit drops the length of a letter run (10 against 20 letters cost 0)",
          dist("letter_runs_unit", ten, twenty) == 0.0, str(dist("letter_runs_unit", ten, twenty)))
    bs2, bs5 = [A] * 6 + ["backspace"] * 2 + ["enter"], [A] * 6 + ["backspace"] * 5 + ["enter"]
    check("letter_runs_unit keeps the number of Backspace presses", dist("letter_runs_unit", bs2, bs5) == 3.0,
          str(dist("letter_runs_unit", bs2, bs5)))
    check("all_runs_unit drops the number of Backspace presses", dist("all_runs_unit", bs2, bs5) == 0.0,
          str(dist("all_runs_unit", bs2, bs5)))
    ordered_a = [A] * 5 + ["punctuation"] + [A] * 3 + ["enter"]
    ordered_b = [A] * 3 + ["punctuation"] + [A] * 5 + ["enter"]
    check("letter_runs_unit: two logs that differ only in the lengths of their letter runs cost 0",
          dist("letter_runs_unit", ordered_a, ordered_b) == 0.0)
    swapped = ["punctuation"] + [A] * 8 + ["enter"]
    check("letter_runs_unit keeps the order of the tokens", dist("letter_runs_unit", ordered_a, swapped) > 0.0)

    # On random logs: counts_only expands to no_time (the same information), all_runs_unit is derivable from
    # letter_runs_unit, and letter_runs_unit is derivable from counts_only.
    rng = random.Random(7)
    cats = [A, A, A, "space", "punctuation", "backspace", "enter", "navigation"]
    bad_expand = bad_ab = bad_ca = 0
    for _ in range(500):
        t = 0
        e = []
        for _ in range(rng.randint(1, 40)):
            t += rng.randint(0, 900)
            e.append((t, rng.choice(cats)))
        co, nt = TOKENS["counts_only"](e), TOKENS["no_time"](e)
        if [c for c, k, _ in co for _ in range(k)] != [c for c, _, _ in nt]:
            bad_expand += 1
        a = TOKENS["letter_runs_unit"](e)
        if _unit_tokens([(0, c) for c, _, _ in a], False) != TOKENS["all_runs_unit"](e):
            bad_ab += 1
        if tuple((c, 1, 0) if c == A else (c, k, 0) for c, k, _ in co) != a:
            bad_ca += 1
    check("counts_only expanded by its counts is no_time (500 random logs)", bad_expand == 0, str(bad_expand))
    check("all_runs_unit is derivable from letter_runs_unit (500 random logs)", bad_ab == 0, str(bad_ab))
    check("letter_runs_unit is counts_only with every letter count set to 1 (500 random logs)", bad_ca == 0,
          str(bad_ca))
    # The closure: for the registered sets it is closed_set.coarser_or_equal; all_runs_unit is derivable from
    # letter_runs_unit (shown above) and not the other way round (two logs that all_runs_unit cannot tell apart).
    for f in CONTROLS:
        check("closure of %s is the registered one" % f,
              set(CLOSURE[f]) == set(g for g in closed_set.coarser_or_equal(f) if g in CONTROLS), str(CLOSURE[f]))
    one_b, two_b = [A, "backspace", "enter"], [A, "backspace", "backspace", "enter"]
    check("letter_runs_unit is not derivable from all_runs_unit",
          TOKENS["all_runs_unit"](ev(one_b)) == TOKENS["all_runs_unit"](ev(two_b))
          and TOKENS["letter_runs_unit"](ev(one_b)) != TOKENS["letter_runs_unit"](ev(two_b)))
    check("closure of letter_runs_unit holds all_runs_unit, that of all_runs_unit only itself",
          CLOSURE["letter_runs_unit"] == ("letter_runs_unit", "all_runs_unit")
          and CLOSURE["all_runs_unit"] == ("all_runs_unit",), str(CLOSURE))

    # The copy of the evaluation reproduces closed_set.evaluate for the two registered sets, on a synthetic world.
    with tempfile.TemporaryDirectory() as tmp:
        recipes, cfg, _, corpus, corpus_path, trials = closed_set._selftest_world(tmp, 60, 11,
                                                                                  ["S1", "S2", "S6", "S10"])
        for K in (5, 20):
            reg = closed_set.evaluate(trials, corpus, K, list(CONTROLS), 13, recipes, cfg, B=40)
            mine = evaluate(trials, corpus, K, CONTROLS + NEW_SETS, 13, recipes, cfg, B=40)
            for f in CONTROLS:
                check("K = %d, %s: the copy equals closed_set.evaluate" % (K, f), _plain(mine[f]) == _plain(reg[f]))
            check("K = %d: letter_runs_unit reports the larger of the two new sets" % K,
                  mine["letter_runs_unit"]["acc"] == max(mine["letter_runs_unit"]["acc_raw"],
                                                         mine["all_runs_unit"]["acc_raw"]))
            check("K = %d: all_runs_unit reports its own accuracy" % K,
                  mine["all_runs_unit"]["acc"] == mine["all_runs_unit"]["acc_raw"])
            # Each set draws its own ties, so its choices do not depend on the other sets evaluated.
            alone = evaluate(trials, corpus, K, ("letter_runs_unit",), 13, recipes, cfg, B=40)["letter_runs_unit"]
            check("K = %d: letter_runs_unit alone makes the same choices" % K,
                  (alone["acc_raw"], alone["mean_rank"], alone["by_scenario"]) == (
                      mine["letter_runs_unit"]["acc_raw"], mine["letter_runs_unit"]["mean_rank"],
                      mine["letter_runs_unit"]["by_scenario"]) and alone["acc"] == alone["acc_raw"])

        # compare_registered: identical values pass, a changed value or input is reported.
        reg_file = {"format": closed_set.FORMAT, "seed": 13, "bootstrap": 40, "n_sessions": 1, "n_trials": 60,
                    "alignment": {"a": 1},
                    "skipped_scenarios": None, "corpus_sha256": "c", "recipes_sha256": "r", "config_sha256": "g",
                    "params": {"p": 1},
                    "features": dict((f, {"timing": closed_set.FEATURE_SETS[f][0],
                                          "runs": closed_set.FEATURE_SETS[f][1]}) for f in CONTROLS),
                    "results": {"5": _plain(dict((f, reg[f]) for f in CONTROLS))}}
        reg_file = _plain(reg_file)
        mine_run = {"seed": 13, "bootstrap": 40, "n_sessions": 1, "n_trials": 60, "alignment": {"a": 1},
                    "skipped_scenarios": None, "corpus_sha256": "c", "recipes_sha256": "r", "config_sha256": "g",
                    "params": {"p": 1}, "results": {"5": reg}}
        check("compare_registered: the same values pass", compare_registered(reg_file, mine_run) == [],
              str(compare_registered(reg_file, mine_run)))
        changed = json.loads(json.dumps(reg_file))
        changed["results"]["5"]["counts_only"]["mean_rank"] += 0.5
        d = compare_registered(changed, mine_run)
        check("compare_registered: a changed value of a registered set is reported",
              len(d) == 1 and "mean_rank" in d[0] and "counts_only" in d[0], str(d))
        changed = json.loads(json.dumps(reg_file))
        changed["seed"] = 14
        check("compare_registered: a changed input is reported", compare_registered(changed, mine_run) != [])
        changed = json.loads(json.dumps(reg_file))
        changed["results"]["10"] = changed["results"]["5"]
        check("compare_registered: a K missing from the run is reported", compare_registered(changed, mine_run) != [])
        changed = json.loads(json.dumps(reg_file))
        changed["features"]["counts_only"] = {"timing": "none", "runs": "exact"}
        check("compare_registered: a registered file without the registered sets is reported",
              compare_registered(changed, mine_run) != [])
        changed = json.loads(json.dumps(reg_file))
        changed["n_sessions"] = 2
        check("compare_registered: another number of sessions is reported", compare_registered(changed, mine_run) != [])

        # The command line, end to end: a plan and an observation built as in the self-test of closed_set.py, the
        # registered result file written by closed_set.py, then this script.
        recipes_json = os.path.join(_KIT, "gen", "recipes.json")
        cli_cfg_path = os.path.join(tmp, "cfg_cli.json")
        with open(cli_cfg_path, "wb") as f:
            f.write(json.dumps(dict(cfg, scenarios=["S1", "S2", "S6"], n_per_cell_scenario_speed=3)).encode("utf-8"))
        with open(cli_cfg_path, "rb") as f:
            cli_cfg = closed_set._loads(f.read().decode("utf-8"))
        rows, _ = generate_plan.plan_rows(corpus, recipes, cli_cfg, "macos", 3, _sha_file(cli_cfg_path),
                                          _sha_file(recipes_json), "0" * 64)
        plan_path = os.path.join(tmp, "plan.jsonl")
        with open(plan_path, "wb") as f:
            f.write(generate_plan.render_plan(rows))
        header = ('{"type":"meta","format":"obs/1","os":"macos","logger_version":"0.0.0","manifest_sha256":"%s",'
                  '"capabilities":{"unconditional":["key"],"threshold_gated":[],"config_gated":["ime_shadow"]}}'
                  % ("0" * 64))
        obs_lines, clock, last = [header], 1000000, -1
        for r in rows[1:]:
            if r["kind"] == "block":
                clock += 5000
                continue
            clock += r["gap_before_ms"]
            for t, c in closed_set.simulate(r, closed_set.KEYCAT, t0=clock):
                last = max(t, last)
                obs_lines.append('{"type":"key","t":%d,"category":"%s","press":true}' % (last, c))
            clock += r["steps"][-1]["at_ms"] + 2000
        obs_path = os.path.join(tmp, "obs_cli.ndjson")
        with open(obs_path, "wb") as f:
            f.write(("\n".join(obs_lines) + "\n").encode("utf-8"))
        common = ["--obs", obs_path, "--plan", plan_path, "--corpus", corpus_path, "--recipes", recipes_json,
                  "--config", cli_cfg_path, "--K", "5,20", "--seed", "3", "--bootstrap", "30"]
        reg_path = os.path.join(tmp, "registered.json")
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            rc_reg = closed_set.main(common + ["--features", ",".join(CONTROLS), "--out", reg_path])
        check("command line: closed_set.py writes the registered file of the synthetic cell", rc_reg == 0,
              sink.getvalue()[-300:])

        def cli(registered, out, extra=()):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = main(common[:-4] + list(extra or ["--seed", "3", "--bootstrap", "30"])
                          + ["--registered", registered, "--out", out])
            return rc, buf.getvalue()

        out_ok = os.path.join(tmp, "posthoc.json")
        rc, msg = cli(reg_path, out_ok)
        ok_doc = None
        if rc == 0 and os.path.isfile(out_ok):
            with open(out_ok, "rb") as f:
                ok_doc = json.loads(f.read().decode("utf-8"))
        check("command line: a run against its registered file exits 0 and writes the two sets at every K",
              rc == 0 and ok_doc is not None and ok_doc["registered_check"]["result"] == "identical"
              and sorted(ok_doc["results"]) == ["20", "5"]
              and all(sorted(ok_doc["results"][k]) == sorted(NEW_SETS) for k in ok_doc["results"]), msg[-300:])
        with open(reg_path, "rb") as f:
            reg_doc = json.loads(f.read().decode("utf-8"))

        def write_json(name, doc):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(json.dumps(doc).encode("utf-8"))
            return p
        tampered = json.loads(json.dumps(reg_doc))
        tampered["results"]["5"]["counts_only"]["mean_rank"] += 0.5
        for name, reg_p, extra, why in (
                ("a changed registered value", write_json("tampered.json", tampered), None,
                 "does not reproduce the registered result file"),
                ("another seed", reg_path, ["--seed", "4", "--bootstrap", "30"],
                 "does not reproduce the registered result file"),
                ("a registered file that is not an object", write_json("list.json", []), None,
                 "is not a result file of closed_set.py"),
                ("a registered file whose features are not an object",
                 write_json("features.json", dict(reg_doc, features=[])), None, "does not have the structure"),
                ("a registered file whose results are not objects",
                 write_json("results.json", dict(reg_doc, results={"5": []})), None, "does not have the structure")):
            out_bad = os.path.join(tmp, "bad.json")
            rc, msg = cli(reg_p, out_bad, extra)
            check("command line: %s exits 2, says why and writes nothing" % name,
                  rc == 2 and why in msg and not os.path.exists(out_bad), "%r %s" % (rc, msg[-200:]))
        out_kept = os.path.join(tmp, "kept.json")
        with open(out_kept, "wb") as f:
            f.write(b"kept\n")
        rc, msg = cli(reg_path, out_kept)
        with open(out_kept, "rb") as f:
            kept = f.read()
        check("command line: an existing --out exits 2 and is left as it was", rc == 2 and kept == b"kept\n",
              "%r %r" % (rc, kept))

    for f in fails:
        print("FAIL: " + f)
    print("explore_run_lengths.py selftest: %d checks, %d failed" % (n_checks[0], len(fails)))
    return 1 if fails else 0


# ---------------------------------------------------------------- CLI

def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Exploratory closed-set attack on feature sets that drop run lengths")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--obs", nargs="+")
    ap.add_argument("--plan", nargs="+")
    ap.add_argument("--corpus")
    ap.add_argument("--recipes")
    ap.add_argument("--config")
    ap.add_argument("--registered", help="the registered result file of the cell (e2_closed_set_<cell>.json)")
    ap.add_argument("--K", default="10,100,1000")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bootstrap", type=int, default=closed_set.DEFAULT_BOOTSTRAP)
    ap.add_argument("--markers-in-obs", action="store_true")
    ap.add_argument("--skip-scenario", action="append", default=[], metavar="S")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.obs and args.plan and args.corpus and args.recipes and args.config and args.registered and args.out):
        ap.print_usage(sys.stderr)
        return 2
    try:
        Ks = [int(x) for x in args.K.split(",") if x]
        if not Ks or any(k < 2 for k in Ks):
            raise ValueError("--K must be comma-separated integers of at least 2")
        if args.bootstrap < 1:
            raise ValueError("--bootstrap must be an integer of at least 1")
        if any(re.match(r"S[1-9][0-9]*\Z", s) is None for s in args.skip_scenario):
            raise ValueError("--skip-scenario must name a scenario such as S12")
        if len(set(args.skip_scenario)) != len(args.skip_scenario):
            raise ValueError("--skip-scenario names a scenario twice")
        result = run(args.obs, args.plan, args.corpus, args.recipes, args.config, args.registered, Ks, args.seed,
                     args.markers_in_obs, args.bootstrap, tuple(args.skip_scenario))
        data = render(result)   # before the file is created, so that nothing is left if rendering fails
        with open(args.out, "xb") as f:
            f.write(data)
    except (ValueError, OSError) as e:
        print("explore_run_lengths.py: %s" % e, file=sys.stderr)
        return 2
    print("exploratory closed set: no_time and counts_only reproduce the registered file at K = %s; written to %s" % (
        ",".join(str(k) for k in Ks), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
