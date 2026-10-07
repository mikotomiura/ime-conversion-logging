#!/usr/bin/env python3
# kit: selftest
"""Closed-set identification (attacker knowledge K2): which of K candidate phrases was typed (standard library only).

  closed_set.py --obs OBS... --plan PLAN... --corpus F --recipes F --config F [--K 10,100,1000] [--seed N]
                [--bootstrap B] [--markers-in-obs] [--ime-toggle-in-obs] [--skip-scenario S]... --out F
  closed_set.py --selftest        positive and negative controls on synthetic observations (fast)

--skip-scenario was added after the freeze by deviation D8 (experiment/deviations.md): the registration does not count
the trials of scenario S12, but the frozen code left out only those whose phrase is not in the corpus. Without the
switch the output is the same as that of the frozen code.

Simulation attack. For every candidate phrase the attacker runs the same scenario generator as the experiment
(build_trial of gen/generate_plan.py) to obtain a predicted log, and chooses the candidate whose predicted log is
closest to the observed one. Closeness is the edit distance between category sequences (insertion, deletion,
substitution), plus a penalty for the position and the size of long pauses (PAUSE_MS or longer). Ties are broken
with random.Random(seed).

Attacker knowledge (K2): the candidate set, the scenario, the OS, the typing-speed level, the generator, the recipes
and the configuration. The attacker does not know the typed phrase or the random draws inside the scenario (candidate
index, arrow-key count, thinking pause, inter-key intervals). One stream of random draws is shared by all candidates,
so that differences in draws do not reorder the candidates; this is a design choice available to the attacker. A
candidate's predicted log is scored at several phases of the 1-second grid used by the "bin1s" timing feature (see
PHASES), and the closest phase is kept, because the grid of the observed log is anchored to a time the attacker does
not know. Decoy candidates are drawn only from phrases the scenario could actually have produced (the same
reading-length range and, for a kana-input scenario, the same typable-character constraint that the plan generator
itself applies), and are excluded when the reading they would actually type (see typed_reading of gen/generate_plan.py)
coincides with the reading the true phrase would actually type; this matters for a scenario that only types part of the
reading (S10) or a synthetic reading (S12). Correctness of a trial is judged the same way: on the typed reading, not on
phrase identity.

The attacker receives only the observation (the category and the time of each key row). Trial boundaries are supplied
by the evaluator; the difficulty of locating boundaries is not part of this attack. The typed phrase is taken from the
plan only at evaluation time and is never used for the choice (passing true_index does not change the chosen
candidate). Rows of type ime_shadow are read but not used: their nav and abandon fields are fixed by the intent of the
scenario, not by the phrase, and their dwell time is a function of the key times that the timing feature already uses
(the gain from using it separately has not been measured). The attack code never reads the ground-truth or oracle logs of
the runner; that is the reason the observation reader accepts nothing but the rows of a standalone-logger output.

Removal experiment and the publication-granularity recommendation. An attacker who sees a detailed feature set can
always throw information away and run the attack as if a coarser feature set had been observed; a feature set can
therefore never be safer to publish than any feature set derivable from it by discarding timing detail and/or run
detail (see the coarsening order of FEATURE_SETS, COARSER_OR_EQUAL). Each feature set's reported accuracy is the
maximum, over itself and every feature set derivable from it, of the measured accuracy (acc_raw holds the value
measured for that feature set alone). The publication-granularity recommendation is to be read from the reported
(maximum) value, not from acc_raw.

Assignment of presses and blocks that do not match the plan. The observed presses are assigned to the plan in plan
order (the evaluator supplies the trial boundaries). With --markers-in-obs the block-start markers appear in the
observation and serve as anchors: a block whose presses (its marker and its trials, compared by category) equal the
plan is scored; a block with a missing, extra or different press is left unscored and counted, and the next block is
found again by its own marker, so one dropped press loses one block and never shifts the others (align_blocks). A
marker is recognised at the right end of a run of its category (the press after it is not of its last category), so
arrow presses just before it do not hide it, and a run of arrow presses inside a trial, at most three, cannot be
mistaken for it when the marker is longer; a block is placed only where its whole sequence matches. Presses that no
scored block covers are counted as unassigned. The output holds an `alignment` object (blocks, unscored blocks and
trials by scenario and by speed, unassigned presses); n, the accuracies and the intervals are
computed over the scored trials only. Without --markers-in-obs there is no anchor to resynchronise to, so any mismatch
stops the run (exit 2).

Descriptive block-bootstrap interval. Next to the Wilson intervals, each feature set reports a 95% percentile interval
of acc and of acc_raw from a bootstrap that resamples blocks (the cluster of a trial is its block; blocks of different
sessions are different clusters; the maximum behind acc is recomputed inside each resample), B resamples (--bootstrap),
the seed of --seed. These intervals are descriptive; the decision rule reads the Wilson interval. With fewer than two
blocks the interval is [null, null] and ci_block_status says so. by_speed gives the raw accuracy per speed level.

Scope. The results hold for this attack only. No upper bound on identifiability is claimed: a stronger language model,
boundary estimation or the combination of several trials is not included. Results (acc_raw) are reported as measured,
including the case that identification is possible, and including the case that removing a feature does not lower the
rate; the order of the feature sets is not rearranged to fit an expectation. The fixed constants of the attack
(PAUSE_MS, TAU_MS, W_TIME, PHASE_STEP_MS) were chosen as part of the design and are not tuned on results.
"""
import argparse
import hashlib
import json
import math
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
import generate_plan  # noqa: E402  (public functions: build_trial, trial_rng, validate_plan, load_corpus, load_recipes)
import simulate_obs  # noqa: E402
import stats  # noqa: E402  (Wilson interval)
from simulate_obs import KEYCAT, apply_features, simulate  # noqa: E402

FORMAT = "e2-closed/1"
CATEGORIES = ("alphabetic", "numeric", "punctuation", "sentence_punctuation", "tab", "backspace", "delete", "space",
              "enter", "navigation", "modifier", "function", "escape", "other")

# Feature sets of the removal experiment: name -> (timing, runs). "all" is the full feature set.
FEATURE_SETS = {
    "all": ("exact", "exact"),            # category sequence and times in ms
    "no_time": ("none", "exact"),         # times removed
    "no_runs": ("exact", "count"),        # alphabetic run length removed (one token per run between delimiters)
    "bin1s": ("bin1s", "exact"),          # times coarsened to 1-second bins
    "bin1s_count": ("bin1s", "count"),    # 1-second bins and alphabetic run length removed
    "counts_only": ("none", "count"),     # no times and alphabetic run length removed
}

# Coarsening order of the feature sets, used by the removal-experiment reporting rule above: timing exact > bin1s >
# none, and runs exact > count, each independently. G is derivable from F (an attacker who sees F could compute the
# result an attacker restricted to G would get) exactly when G is not more detailed than F in either dimension.
_TIMING_RANK = {"exact": 2, "bin1s": 1, "none": 0}
_RUNS_RANK = {"exact": 1, "count": 0}


def coarser_or_equal(features):
    """Feature-set names derivable from `features` by discarding timing and/or run detail (itself included)."""
    ft, fr = FEATURE_SETS[features]
    tr, rr = _TIMING_RANK[ft], _RUNS_RANK[fr]
    return [g for g, (gt, gr) in FEATURE_SETS.items() if _TIMING_RANK[gt] <= tr and _RUNS_RANK[gr] <= rr]


DEFAULT_BOOTSTRAP = 2000   # resamples of the block bootstrap (descriptive intervals)
PAUSE_MS = 600      # intervals shorter than this are treated as typing jitter and ignored (their law does not depend on the phrase)
TAU_MS = 1000       # a difference of this size in the length of a pause gives the largest penalty
W_TIME = 0.5        # weight of the pause penalty (one category mismatch costs 1)
PHASE_STEP_MS = 100
PHASES = tuple(range(0, simulate_obs.WINDOW_MS, PHASE_STEP_MS))   # candidate grid phases searched for timing="bin1s"


# ---------------------------------------------------------------- random numbers (random() only, stable across versions)

def _sub_seed(seed, *tags):
    h = hashlib.sha256((":".join([str(seed)] + [str(t) for t in tags])).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def _below(rng, k):
    return min(int(rng.random() * k), k - 1)


def _normal(rng):
    u1 = 1.0 - rng.random()
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


# ---------------------------------------------------------------- features -> symbol sequence -> distance

def _normalize(events):
    """Shift the times so that the first row is at 0 (the attacker does not use the absolute start of a trial)."""
    if not events:
        return []
    t0 = events[0][0]
    return [(t - t0, c) for t, c in events]


def _tokens(events, runs):
    """[(t, category)] -> ((category, count, interval since the previous token), ...).
    With runs == "count", the alphabetic rows stamped with the same time form one token."""
    toks = []
    for t, c in events:
        if runs == "count" and c == "alphabetic" and toks and toks[-1][0] == "alphabetic" and toks[-1][2] == t:
            toks[-1][1] += 1
        else:
            toks.append([c, 1, t])
    out = []
    prev = None
    for c, k, t in toks:
        out.append((c, k, 0 if prev is None else max(0, t - prev)))
        prev = t
    return tuple(out)


def _gap(iv, timing):
    """Size of a pause, or 0 when iv is shorter than PAUSE_MS (typing jitter, whose size does not depend on the
    phrase). For timing="bin1s" one bin width is discounted first: two token timestamps a single bin apart can
    differ by up to one window purely because of where the 1-second grid falls (see PHASES), which is not evidence
    of a real pause, so a bin edge is not penalised as a gap by itself."""
    if timing == "bin1s":
        iv = max(0, iv - simulate_obs.WINDOW_MS)
    return iv if iv >= PAUSE_MS else 0


def _distance(obs, pred, timing):
    """Distance between two token sequences. Insertion or deletion costs the count of the token. Substitution of a
    different category costs the larger count. Two tokens of the same category cost the difference of their counts
    plus the pause penalty (at most W_TIME)."""
    m = len(pred)
    prev = [0.0] * (m + 1)
    for j in range(1, m + 1):
        prev[j] = prev[j - 1] + pred[j - 1][1]
    for oc, on, oiv in obs:
        cur = [prev[0] + on]
        og = _gap(oiv, timing)
        for j in range(1, m + 1):
            pc, pn, piv = pred[j - 1]
            if oc == pc:
                sub = abs(on - pn)
                pg = _gap(piv, timing)
                if og or pg:
                    d = abs(og - pg)
                    sub += W_TIME * (d / TAU_MS if d < TAU_MS else 1.0)
            else:
                sub = on if on > pn else pn
            a = prev[j - 1] + sub
            b = prev[j] + on
            c = cur[j - 1] + pn
            cur.append(a if a <= b and a <= c else (b if b <= c else c))
        prev = cur
    return prev[m]


# ---------------------------------------------------------------- attack

class Predictor(object):
    """Predicted logs of the candidates. One (phrase, scenario, speed) is built once; the random draws depend only on the seed."""

    def __init__(self, recipes, config, os_name, seed):
        self.recipes, self.config, self.os_name, self.seed = recipes, config, os_name, seed
        self._raw = {}
        self._tok = {}

    def raw(self, phrase, scenario, speed):
        key = (phrase["phrase_id"], scenario, speed)
        if key not in self._raw:
            try:
                trial = generate_plan.build_trial(phrase, scenario, speed, self.os_name, self.recipes, self.config,
                                                  generate_plan.trial_rng(self.seed, "attack"), "t00000", 0)
                self._raw[key] = simulate(trial, KEYCAT)
            except ValueError:
                self._raw[key] = None   # the phrase cannot be typed in this scenario (for example, a character outside the kana layout)
        return self._raw[key]

    def tokens(self, phrase, scenario, speed, timing, runs):
        """Predicted token sequence(s) as a tuple. For timing == "bin1s" this holds one sequence per candidate
        grid phase (PHASES, in order); for the other two timing values it holds a single sequence, because phase
        has no effect on them. None means the phrase cannot be typed in this scenario."""
        key = (phrase["phrase_id"], scenario, speed, timing, runs)
        if key not in self._tok:
            ev = self.raw(phrase, scenario, speed)
            if ev is None:
                self._tok[key] = None
            elif timing == "bin1s":
                self._tok[key] = tuple(_tokens(_normalize(apply_features(ev, timing, runs, phase=p)), runs)
                                       for p in PHASES)
            else:
                self._tok[key] = (_tokens(_normalize(apply_features(ev, timing, runs)), runs),)
        return self._tok[key]


def _feature_pair(features):
    if isinstance(features, str):
        if features not in FEATURE_SETS:
            raise ValueError("features must be one of %s" % "/".join(sorted(FEATURE_SETS)))
        return FEATURE_SETS[features]
    timing, runs = features
    if timing not in simulate_obs.TIMINGS or runs not in simulate_obs.RUNS:
        raise ValueError("features (timing, runs) is outside the allowed values")
    return timing, runs


def attack(observed, candidates, scenario, os_name, recipes, config, features, seed, speed="mid", true_index=None,
           tie_seed=None, cache=None):
    """Choose the candidate closest to the observation ([(t, category)]).

    candidates: phrases (dicts of a corpus phrase row). features: a name of FEATURE_SETS or a (timing, runs) pair.
    Returns {"chosen": candidate index, "ranking": indices from the closest, "distances": list of distances,
    "rank_of_true": 1-based rank (only when true_index is given, otherwise None)}. true_index is used only to report
    the rank and never for the choice. seed drives the attacker's random draws for the predicted logs; tie_seed drives
    the tie-breaking (default: seed). cache: a Predictor to reuse predicted logs.
    """
    if not candidates:
        raise ValueError("no candidates")
    timing, runs = _feature_pair(features)
    pred = cache if cache is not None else Predictor(recipes, config, os_name, seed)
    obs_tok = _tokens(_normalize(apply_features(list(observed), timing, runs)), runs)
    memo = {}
    dist = []
    for ph in candidates:
        variants = pred.tokens(ph, scenario, speed, timing, runs)
        if variants is None:
            dist.append(float("inf"))
            continue
        best = None
        for pt in variants:
            if pt not in memo:
                memo[pt] = _distance(obs_tok, pt, timing)
            d = memo[pt]
            if best is None or d < best:
                best = d
        dist.append(best)
    rng = random.Random(_sub_seed(seed if tie_seed is None else tie_seed, "tie"))
    keys = [rng.random() for _ in candidates]
    order = sorted(range(len(candidates)), key=lambda i: (dist[i], keys[i]))
    rank = None if true_index is None else order.index(true_index) + 1
    return {"chosen": order[0], "ranking": order, "distances": dist, "rank_of_true": rank}


def _scenario_pool(recipes, os_name, scenario, pool):
    """Phrases of pool that the plan generator could actually have chosen as this trial's phrase or as a decoy for
    it: within the scenario's reading-length range and, for a kana-input scenario, typable in the kana layout.
    Mirrors the eligibility rule plan_rows (gen/generate_plan.py) applies when it builds a plan; recipes is data the
    attacker already has, so this reads the recipe entry directly rather than calling a private
    function of the generator."""
    entry = recipes["os"].get(os_name, {}).get(scenario)
    if entry is None:
        return pool
    kana = any(el.get("input") == "kana" for el in entry.get("steps", []) if "type_reading" in el)
    if kana:
        chars = set(recipes["kana_layout"].values())
        pool = [p for p in pool if all(c in chars for c in p["reading"])]
    rl = entry.get("reading_len", {})
    lo, hi = rl.get("min", 1), rl.get("max", 10 ** 9)
    return [p for p in pool if lo <= p["reading_len"] <= hi]


def _typed_reading(recipes, config, phrase, scenario, os_name, speed="mid"):
    """The reading the generator would actually type for phrase under this scenario (generate_plan.typed_reading of
    a built trial): the whole reading for most scenarios, the first half for S10, a synthetic kana string as is for
    S12. The random draws used to build the trial affect only step timing and the non-reading keys after the
    reading, never which reading characters are typed or where the reading portion ends, so any seed gives the same
    result; a fixed one is used so the result depends only on (phrase, scenario, os)."""
    trial = generate_plan.build_trial(phrase, scenario, speed, os_name, recipes, config,
                                      generate_plan.trial_rng(0, "typed-reading-probe"), "t00000", 0)
    return generate_plan.typed_reading(trial, recipes)


def _typed_map(recipes, config, os_name, scenario, phrases):
    """phrase_id -> typed reading (see _typed_reading), for every phrase of phrases."""
    return dict((p["phrase_id"], _typed_reading(recipes, config, p, scenario, os_name)) for p in phrases)


def _make_candidates(rng, pool, true, K, typed_of, true_typed):
    """The typed phrase plus K-1 phrases from pool whose typed reading (see _typed_reading) differs from the typed
    reading of true, in random order. typed_of maps the phrase_id of every phrase of pool to its typed reading under
    the trial's scenario and OS (a scenario that only types part of the reading, such as S10, or a
    synthetic reading, such as S12, can make two phrases indistinguishable even though their full readings differ;
    such a phrase would be an unfair decoy and is excluded here rather than left for the distance to fail to tell
    apart). Returns (candidates, index of the typed phrase)."""
    eligible = [p for p in pool if typed_of[p["phrase_id"]] != true_typed]
    if len(eligible) < K - 1:
        raise ValueError("the corpus has too few phrases with a distinct typed reading (K = %d)" % K)
    for i in range(K - 1):
        j = i + _below(rng, len(eligible) - i)
        eligible[i], eligible[j] = eligible[j], eligible[i]
    cands = [true] + eligible[:K - 1]
    for i in range(len(cands) - 1, 0, -1):
        j = _below(rng, i + 1)
        cands[i], cands[j] = cands[j], cands[i]
    return cands, [c["phrase_id"] for c in cands].index(true["phrase_id"])


def _pool_by_reading(corpus):
    seen = set()
    pool = []
    for p in corpus:
        if p["reading"] not in seen:
            seen.add(p["reading"])
            pool.append(p)
    return pool


def evaluate(trials, corpus, K, features_list, seed, recipes, config, B=DEFAULT_BOOTSTRAP):
    """For every trial, attack with the typed phrase and K-1 decoys drawn from phrases the scenario could actually
    produce (see _scenario_pool), and return, for each feature set, the accuracy the removal experiment recommends
    a publication decision be based on next to the accuracy measured for that feature set alone.

    trials: [{"trial_id", "cluster", "scenario", "speed", "os", "phrase_id", "observed": [(t, category)]}];
    "cluster" names the block of the trial (the unit that the block bootstrap resamples; blocks of different
    sessions are different clusters).
    Returns {feature set: {
      "acc": the maximum, over the feature set and every feature set derivable from it (coarser_or_equal), of the
             measured accuracy -- the value the publication-granularity recommendation is read from,
      "acc_ci": its Wilson 95% interval, "acc_source": the feature set the maximum came from,
      "acc_raw": the accuracy measured for this feature set alone, "acc_ci_raw": its Wilson 95% interval,
      "chance": 1/K, "n", "n_skipped", "mean_rank" (of the typed phrase, from this feature set alone),
      "by_scenario": {scenario: {"acc", "n"}} (raw, per scenario),
      "by_speed": {speed: {"acc", "n"}} (raw, per speed),
      "acc_ci_block", "acc_ci_raw_block": descriptive 95% percentile intervals of acc and acc_raw from a bootstrap
             of B resamples of the blocks (stats.cluster_bootstrap; the maximum behind acc is recomputed inside
             every resample); [None, None] when there are fewer than two blocks,
      "ci_block_status": "ok" or "fewer_than_two_blocks", "n_blocks"}}.
    The intervals of the block bootstrap are descriptive; the decision rule reads the Wilson interval.
    Correctness is judged on the typed reading (see _typed_reading), not on phrase identity, so that a scenario
    which only types part of the reading (S10) or a synthetic reading (S12) is judged the way the attacker's
    evidence actually constrains it. Trials whose phrase is not in the corpus (synthetic kana
    strings) are not counted (n_skipped).
    """
    if not isinstance(K, int) or K < 2:
        raise ValueError("K must be an integer of at least 2")
    by_id = dict((p["phrase_id"], p) for p in corpus)
    base_pool = _pool_by_reading(corpus)
    if len(base_pool) < K:
        raise ValueError("the corpus has %d distinct readings, fewer than K = %d" % (len(base_pool), K))
    used = [t for t in trials if t["phrase_id"] in by_id]
    preds = {}
    pools = {}    # (scenario, os) -> scenario-eligible pool (_scenario_pool)
    typed = {}    # (scenario, os) -> {phrase_id: typed reading} of that pool (_typed_map)
    res = dict((f, {"correct": 0, "ranks": [], "by_scenario": {}, "by_speed": {}, "hits": []}) for f in features_list)
    for tr in used:
        sk = (tr["scenario"], tr["os"])
        if sk not in pools:
            pools[sk] = _scenario_pool(recipes, tr["os"], tr["scenario"], base_pool)
            typed[sk] = _typed_map(recipes, config, tr["os"], tr["scenario"], pools[sk])
        true_ph = by_id[tr["phrase_id"]]
        true_typed = _typed_reading(recipes, config, true_ph, tr["scenario"], tr["os"])
        tseed = _sub_seed(seed, "trial", tr["trial_id"], K)
        cands, ti = _make_candidates(random.Random(_sub_seed(tseed, "cand")), list(pools[sk]), true_ph, K,
                                     typed[sk], true_typed)
        if tr["os"] not in preds:
            preds[tr["os"]] = Predictor(recipes, config, tr["os"], seed)
        for f in features_list:
            r = attack(tr["observed"], cands, tr["scenario"], tr["os"], recipes, config, f, seed, speed=tr["speed"],
                       true_index=ti, tie_seed=_sub_seed(tseed, "tie", f), cache=preds[tr["os"]])
            chosen_typed = true_typed if r["chosen"] == ti else typed[sk][cands[r["chosen"]]["phrase_id"]]
            correct = 1 if chosen_typed == true_typed else 0
            res[f]["correct"] += correct
            res[f]["ranks"].append(r["rank_of_true"])
            bysc = res[f]["by_scenario"].setdefault(tr["scenario"], {"correct": 0, "n": 0})
            bysc["n"] += 1
            bysc["correct"] += correct
            bysp = res[f]["by_speed"].setdefault(tr["speed"], {"correct": 0, "n": 0})
            bysp["n"] += 1
            bysp["correct"] += correct
            res[f]["hits"].append(correct)
    out = {}
    n = len(used)
    all_x = dict((f, res[f]["correct"]) for f in features_list)
    # one cluster per block: a 1-tuple holding (number of trials, correct trials of every feature set), so that a
    # resample can be summed without visiting the trials
    cluster_of = {}
    for i, tr in enumerate(used):
        c = cluster_of.setdefault(tr["cluster"], [0] + [0] * len(features_list))
        c[0] += 1
        for j, f in enumerate(features_list):
            c[1 + j] += res[f]["hits"][i]
    boot_clusters = [(tuple(c),) for c in cluster_of.values()]
    index = dict((f, j + 1) for j, f in enumerate(features_list))
    for f in features_list:
        closure = [g for g in coarser_or_equal(f) if g in features_list]
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
            bseed = _sub_seed(seed, "block_bootstrap", K)

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


# ---------------------------------------------------------------- inputs (publishable logs and plans)

def _no_dup_keys(pairs):
    """object_pairs_hook: reject a JSON object that names the same key twice, rather than silently keeping the
    last value (the standard library's default json.loads behaviour)."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError("duplicate key %r in a JSON object" % (k,))
        seen[k] = v
    return seen


def _loads(text):
    """json.loads with duplicate object keys rejected (_no_dup_keys) and a RecursionError from deeply nested input
    turned into the same ValueError every other malformed input raises, so that a caller need not also expect an
    uncaught Traceback from adversarial nesting."""
    try:
        return json.loads(text, object_pairs_hook=_no_dup_keys)
    except RecursionError:
        raise ValueError("too deeply nested to read as JSON")


def read_obs(path):
    """Read a standalone-logger output. Returns (list of [(t, category)] for key presses, number of ime_shadow rows,
    the os the header declares). Only key rows (press true) and ime_shadow rows are read; any other row type,
    duplicate JSON object key, or unknown key is rejected."""
    with open(path, "rb") as f:
        raw = f.read()
    lines = raw.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise ValueError("empty observation")
    events = []
    shadow = 0
    os_name = None
    for ln, line in enumerate(lines, start=1):
        try:
            obj = _loads(line)
        except ValueError:
            raise ValueError("observation line %d is not JSON" % ln)
        if not isinstance(obj, dict):
            raise ValueError("observation line %d is not an object" % ln)
        if ln == 1:
            if obj.get("type") != "meta" or obj.get("format") != "obs/1" or obj.get("os") not in generate_plan.OS_NAMES:
                raise ValueError("the first observation line is not an obs/1 header")
            os_name = obj["os"]
            continue
        kind = obj.get("type")
        if kind == "key":
            if set(obj) != {"type", "t", "category", "press"} or not isinstance(obj["t"], int) \
                    or isinstance(obj["t"], bool) or obj["category"] not in CATEGORIES or not isinstance(obj["press"], bool):
                raise ValueError("observation line %d: wrong shape for a key row" % ln)
            if obj["press"]:
                events.append((obj["t"], obj["category"]))
        elif kind == "ime_shadow":
            if set(obj) != {"type", "t", "dwell_ms", "nav", "abandon"} or not isinstance(obj["t"], int) \
                    or isinstance(obj["t"], bool) or not isinstance(obj["dwell_ms"], int) \
                    or isinstance(obj["dwell_ms"], bool) or not isinstance(obj["nav"], int) \
                    or isinstance(obj["nav"], bool) or not isinstance(obj["abandon"], bool):
                raise ValueError("observation line %d: wrong shape for an ime_shadow row" % ln)
            shadow += 1
        else:
            raise ValueError("observation line %d: a row type that is not read" % ln)
    for a, b in zip(events, events[1:]):
        if b[0] < a[0]:
            raise ValueError("key rows are not in ascending time order")
    return events, shadow, os_name


def read_plan(path):
    """Read a plan (plan/1). Raises ValueError when the generator's validator rejects it. Returns the rows."""
    with open(path, "rb") as f:
        data = f.read()
    problems = generate_plan.validate_plan(data)
    if problems:
        raise ValueError("the plan is rejected by the plan/1 validator (%d problems, first at line %d [%s])" % (
            len(problems), problems[0][0], problems[0][1]))
    return [json.loads(line) for line in data.decode("utf-8").split("\n") if line]


def expected_categories(trial_row, keycat=KEYCAT, ime_toggle_in_obs=False):
    """The category sequence a standalone-logger observation is expected to hold for this trial's key presses.

    By default, a key whose table entry is None (a block-start marker or an IME toggle) produces no row: this
    matches the Windows observed-key set (is_observed_key), which the marker and toggle virtual keys fall outside
    of, but it has not been confirmed against a logger run (see the README). ime_toggle_in_obs switches the IME
    toggle keys (ime_on, ime_off) to expect a row of category "other" -- the category from_vk gives their
    virtual-key code -- for an observation where they were found to appear after all; markers get the matching
    switch --markers-in-obs, in segment_by_plan, because they are skipped by count rather than typed.
    """
    out = []
    for s in trial_row["steps"]:
        if not s["down"]:
            continue
        cat = keycat[s["key"]]
        if cat is None:
            if ime_toggle_in_obs and s["key"] in ("ime_on", "ime_off"):
                cat = "other"
            else:
                continue
        out.append(cat)
    return out


def marker_categories(marker, keycat=KEYCAT):
    """The category sequence of a block-start marker, for an observation in which the marker presses appear
    (--markers-in-obs). A marker key without a table entry (F13 to F19) is expected as "other", the category
    from_vk gives its virtual-key code, in the same way as the IME toggle keys (see expected_categories)."""
    return [keycat.get(k) or "other" for k in marker]


def _is_anchor(cats, i, marker):
    """True when cats[i:i+len(marker)] is the marker and the press after it, if there is one, is not of the
    marker's last category. The test is anchored on the right: within a run of presses of the marker's category
    that is longer than the marker, only the last len(marker) presses of the run stand as the marker, so a press
    added in front of a marker, or the arrow presses that end the last trial of the block before it, do not hide
    the marker. A run of fewer presses than the marker is not the marker."""
    n = len(marker)
    if n == 0 or cats[i:i + n] != marker:
        return False
    if i + n < len(cats) and cats[i + n] == marker[-1]:
        return False
    return True


def _next_anchor(cats, marker, start):
    """The first index at or after `start` at which the marker stands as an anchor (see _is_anchor), or None."""
    for i in range(max(start, 0), len(cats) - len(marker) + 1):
        if _is_anchor(cats, i, marker):
            return i
    return None


def align_blocks(cats, blocks):
    """Resynchronise the observed category sequence to the plan at the block markers.

    cats: the observed categories. blocks: [(marker categories, [expected category list of each trial])], in plan
    order. The expected sequence of a block is its marker followed by its trials. Returns (starts, n_unassigned);
    starts[i] is the index in cats at which the expected sequence of block i begins, or None when block i is left
    unscored (its observed presses do not match the plan).

    A cursor walks the observation. When the expected sequence of a block equals cats at the cursor, the block is
    placed there. Otherwise the anchors at or after the cursor are scanned in order, an anchor being a position at
    which the marker of the block or the marker of the following block stands at the right end of a run (see
    _is_anchor); the markers may differ between blocks. At each anchor: when the whole expected sequence of the block
    matches there, the block is placed there (the presses before it stay unassigned); otherwise, when the whole
    expected sequence of the following block matches there, the block is left unscored and the cursor moves to that
    anchor. An anchor at which neither matches (a run of the marker's length that arises inside a trial, for
    instance three arrow presses and one added) is passed over. When no anchor qualifies, the block is left
    unscored and the cursor stays, so the next block scans from the same place. Hence one difference (a missing,
    added or changed press, a damaged marker) loses only the block whose presses contain it, also when a missing
    press at the end of a block leaves arrow presses next to the marker of the next block; a press added between two
    blocks loses no block and is counted as unassigned. These properties are checked by the selftest's cases; they
    are not proved in general.
    n_unassigned is the number of presses that no placed block covers (stray presses, the presses of unscored
    blocks, presses after the last block).
    """
    seqs = [list(m) + [c for t in ts for c in t] for m, ts in blocks]
    starts = [None] * len(blocks)
    cursor = 0
    for i, (marker, _) in enumerate(blocks):
        exp = seqs[i]
        if cats[cursor:cursor + len(exp)] == exp:
            starts[i] = cursor
            cursor += len(exp)
            continue
        nxt = blocks[i + 1][0] if i + 1 < len(blocks) else None
        for p in range(cursor, len(cats)):
            if not (_is_anchor(cats, p, marker) or (nxt is not None and _is_anchor(cats, p, nxt))):
                continue
            if cats[p:p + len(exp)] == exp:
                starts[i] = p
                cursor = p + len(exp)
                break
            if nxt is not None and cats[p:p + len(seqs[i + 1])] == seqs[i + 1]:
                cursor = p
                break
    covered = sum(len(seqs[i]) for i, s in enumerate(starts) if s is not None)
    return starts, len(cats) - covered


def align_plan(cats, rows, obs_os, markers_in_obs=False, ime_toggle_in_obs=False, tag=""):
    """Assign the observed categories to the trials of a plan. Returns (scored, alignment).

    scored: [{"row", "start", "n"}] for every trial of a block that matches the plan (cats[start:start+n] is its
    presses). alignment: {"n_blocks", "n_blocks_unscored", "unscored_block_ids", "n_trials", "n_trials_unscored",
    "unscored_by_scenario", "unscored_by_speed", "unscored_reason", "n_presses", "n_presses_unassigned"} (see
    merge_alignments for the meaning of the counts).

    obs_os is the os the observation's own header declared (read_obs); it must agree with the plan's header os, so a
    mismatched pair of files is rejected instead of silently scored under the wrong OS's rules.

    With markers_in_obs, the blocks are resynchronised at their markers (align_blocks): a block whose presses do
    not match the plan (a missing, extra or different press) is left unscored and counted, and the other blocks are
    scored. Without markers_in_obs the observation holds no marker to resynchronise to, so the presses are assigned
    in plan order and any mismatch of a trial's category sequence, or any press left over, raises ValueError (a
    dropped key must not shift the later trials silently). ime_toggle_in_obs is passed to expected_categories.
    """
    header = rows[0]
    if obs_os != header["os"]:
        raise ValueError("the observation's os (%s) does not match the plan's os (%s)" % (obs_os, header["os"]))
    scored = []
    unscored = []     # (block id, [trial rows])
    n_blocks = sum(1 for r in rows[1:] if r["kind"] == "block")
    n_trials = sum(1 for r in rows[1:] if r["kind"] != "block")
    if not markers_in_obs:
        pos = 0
        for r in rows[1:]:
            if r["kind"] == "block":
                continue
            exp = expected_categories(r, ime_toggle_in_obs=ime_toggle_in_obs)
            if cats[pos:pos + len(exp)] != exp:
                raise ValueError("trial %s%s: the presses do not match the plan (missing, extra or different keys)"
                                 % (tag, r["trial_id"]))
            scored.append({"row": r, "start": pos, "n": len(exp)})
            pos += len(exp)
        if pos != len(cats):
            raise ValueError("the observation has %d more presses than the plan" % (len(cats) - pos))
        n_unassigned = 0
    else:
        blocks = []   # (block id, marker categories, [trial rows], [expected categories])
        for r in rows[1:]:
            if r["kind"] == "block":
                blocks.append((r["block_id"], marker_categories(r["marker"]), [], []))
            elif not blocks:
                raise ValueError("the plan has a trial (%s) before its first block" % r["trial_id"])
            else:
                blocks[-1][2].append(r)
                blocks[-1][3].append(expected_categories(r, ime_toggle_in_obs=ime_toggle_in_obs))
        starts, n_unassigned = align_blocks(cats, [(m, exps) for _, m, _, exps in blocks])
        for (bid, m, trs, exps), s in zip(blocks, starts):
            if s is None:
                unscored.append((bid, trs))
                continue
            p = s + len(m)
            for r, e in zip(trs, exps):
                scored.append({"row": r, "start": p, "n": len(e)})
                p += len(e)
    by_sc, by_sp = {}, {}
    for _, trs in unscored:
        for r in trs:
            by_sc[r["scenario"]] = by_sc.get(r["scenario"], 0) + 1
            by_sp[r["speed"]] = by_sp.get(r["speed"], 0) + 1
    alignment = {"n_blocks": n_blocks, "n_blocks_unscored": len(unscored),
                 "unscored_block_ids": [tag + str(bid) for bid, _ in unscored], "n_trials": n_trials,
                 "n_trials_unscored": sum(len(trs) for _, trs in unscored), "unscored_by_scenario": by_sc,
                 "unscored_by_speed": by_sp, "unscored_reason": "block_mismatch", "n_presses": len(cats),
                 "n_presses_unassigned": n_unassigned}
    return scored, alignment


def merge_alignments(alignments):
    """Sum the alignment objects of several sessions (align_plan). n_presses_unassigned counts the presses that no
    scored block covers, which includes the presses of the unscored blocks themselves."""
    out = {"n_blocks": 0, "n_blocks_unscored": 0, "unscored_block_ids": [], "n_trials": 0, "n_trials_unscored": 0,
           "unscored_by_scenario": {}, "unscored_by_speed": {}, "unscored_reason": "block_mismatch", "n_presses": 0,
           "n_presses_unassigned": 0}
    for al in alignments:
        for k in ("n_blocks", "n_blocks_unscored", "n_trials", "n_trials_unscored", "n_presses",
                  "n_presses_unassigned"):
            out[k] += al[k]
        out["unscored_block_ids"].extend(al["unscored_block_ids"])
        for k in ("unscored_by_scenario", "unscored_by_speed"):
            for name, v in al[k].items():
                out[k][name] = out[k].get(name, 0) + v
    return out


def segment_by_plan(events, rows, obs_os, markers_in_obs=False, ime_toggle_in_obs=False, tag=""):
    """Cut the sequence of observed presses into trials in plan order (the evaluator supplies the boundaries).

    The assignment, the resynchronisation at block markers and the stop on a mismatch without markers are those of
    align_plan. Returns ([{"trial_id", "block_id", "cluster", "scenario", "speed", "phrase_id", "os", "observed"}]
    for the scored trials, the alignment object); "cluster" is the block, qualified by tag, that the block
    bootstrap of evaluate resamples.
    """
    scored, alignment = align_plan([c for _, c in events], rows, obs_os, markers_in_obs, ime_toggle_in_obs, tag)
    out = []
    for s in scored:
        r = s["row"]
        out.append({"trial_id": tag + r["trial_id"], "block_id": r["block_id"], "cluster": tag + str(r["block_id"]),
                    "scenario": r["scenario"], "speed": r["speed"], "phrase_id": r["phrase_id"], "os": rows[0]["os"],
                    "observed": events[s["start"]:s["start"] + s["n"]]})
    return out, alignment


# ---------------------------------------------------------------- CLI

ASSUMPTIONS = [
    "attacker knowledge K2: the candidate set, scenario, OS, typing-speed level, generator, recipes and config",
    "the attacker is given the trial boundaries (evaluator-supplied); boundary detection is not part of this attack",
    "candidates: the typed phrase plus K-1 corpus phrases with distinct readings, drawn per trial",
    "attacker uses common random numbers across candidates; timing feature = positions and sizes of pauses >= pause_ms",
    "results are for this attack only; no claim is made about the upper bound of identifiability",
]


def _sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(obs_paths, plan_paths, corpus_path, recipes_path, config_path, Ks, features_list, seed, markers_in_obs,
        ime_toggle_in_obs=False, bootstrap=DEFAULT_BOOTSTRAP, skip_scenarios=()):
    """skip_scenarios: scenarios whose trials are left out of the attack whatever their phrase (deviation D8). The
    trials are aligned first, so that the alignment and n_trials are those of the whole plan; the output then states
    the number left out per scenario (skipped_scenarios). Every trial draws its candidates from its own seed, so leaving
    out trials does not change the draws of the others."""
    if len(obs_paths) != len(plan_paths):
        raise ValueError("--obs and --plan differ in count (%d and %d)" % (len(obs_paths), len(plan_paths)))
    recipes = generate_plan.load_recipes(recipes_path)
    recipes_sha256 = _sha_file(recipes_path)
    config_sha256 = _sha_file(config_path)
    with open(config_path, "rb") as f:
        try:
            config = _loads(f.read().decode("utf-8"))
        except ValueError:
            raise ValueError("the configuration is not JSON")
    generate_plan.normalize_config(config)
    corpus = generate_plan.load_corpus(corpus_path)
    trials = []
    n_shadow = 0
    alignments = []
    for i, (op, pp) in enumerate(zip(obs_paths, plan_paths)):
        plan = read_plan(pp)
        # The candidates' key presses are predicted from these procedures and this configuration (K2), so they must
        # be the ones the plan was built from (a macOS cell with live conversion off has its own recipes file). The
        # corpus is not compared: the candidates are drawn from the corpus passed.
        for what, mine in (("recipes", recipes_sha256), ("config", config_sha256)):
            if plan[0]["%s_sha256" % what] != mine:
                raise ValueError("plan %d was built from a different %s file than --%s (its %s_sha256 differs); pass "
                                 "the file the plan was built from" % (i + 1, what, what, what))
        events, sh, obs_os = read_obs(op)
        n_shadow += sh
        seg, al = segment_by_plan(events, plan, obs_os, markers_in_obs, ime_toggle_in_obs, tag="s%d-" % i)
        trials.extend(seg)
        alignments.append(al)
    skipped = dict((s, 0) for s in skip_scenarios)
    attacked = []
    for t in trials:
        if t["scenario"] in skipped:
            skipped[t["scenario"]] += 1
        else:
            attacked.append(t)
    results = {}
    for K in Ks:
        results[str(K)] = evaluate(attacked, corpus, K, features_list, seed, recipes, config, B=bootstrap)
    out = {"format": FORMAT, "assumptions": ASSUMPTIONS, "seed": seed, "bootstrap": bootstrap,
           "n_sessions": len(obs_paths), "n_trials": len(trials), "alignment": merge_alignments(alignments), "n_ime_shadow_rows_unused": n_shadow, "features": dict(
               (f, {"timing": FEATURE_SETS[f][0], "runs": FEATURE_SETS[f][1]}) for f in features_list),
           "params": {"pause_ms": PAUSE_MS, "tau_ms": TAU_MS, "w_time": W_TIME, "phase_step_ms": PHASE_STEP_MS},
           "corpus_sha256": _sha_file(corpus_path), "recipes_sha256": recipes_sha256,
           "config_sha256": config_sha256, "results": results}
    if skip_scenarios:
        out["skipped_scenarios"] = skipped
    return out


def render(result):
    return (json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------- selftest

def _selftest_world(tmp, n_trials, seed, scenarios):
    """Synthetic world: a synthetic corpus of varied length, a configuration, planned trials and observations
    (times perturbed by 30 ms). Every reading is drawn only from the characters of recipes.json's kana_layout, so
    that every phrase is typable in a kana-input scenario (S12) as well as in a romaji one, and S12 can be exercised
    without any phrase being rejected by build_trial."""
    recipes_path = os.path.join(_KIT, "gen", "recipes.json")
    recipes = generate_plan.load_recipes(recipes_path)
    kana = "".join(sorted(set(recipes["kana_layout"].values())))
    rng = random.Random(seed)
    readings = []
    seen = set()
    while len(readings) < 150:
        r = "".join(kana[_below(rng, len(kana))] for _ in range(2 + _below(rng, 13)))   # 2 to 14 characters
        if r not in seen:
            seen.add(r)
            readings.append(r)
    lines = ['{"kind":"header","format":"corpus/1","source":"aozora","tokenizer":"table","dict":"none"}']
    for i, r in enumerate(readings, start=1):
        lines.append(generate_plan.dumps({"kind": "phrase", "phrase_id": "w1-%d-1" % i, "work_id": "w1", "text": "x",
                                          "reading": r, "reading_len": len(r)}))
    corpus_path = os.path.join(tmp, "corpus.jsonl")
    with open(corpus_path, "wb") as f:
        f.write(("\n".join(lines) + "\n").encode("utf-8"))
    cfg = {"n_per_cell_scenario_speed": 6, "block_size": 12,
           "speeds": {"slow": {"iki_median_ms": 300, "iki_sigma": 0.3}, "mid": {"iki_median_ms": 200, "iki_sigma": 0.3},
                      "fast": {"iki_median_ms": 120, "iki_sigma": 0.3}},
           "key_hold_ms": 60, "think_ms": {"min": 200, "max": 2000}, "cand_index_max": 4, "nav_count_max": 3,
           "inter_trial_ms": 1000, "marker": ["f13", "f14", "f13"], "marker_gap_ms": 150, "reset_ime_every_block": True}
    cfg_path = os.path.join(tmp, "cfg.json")
    with open(cfg_path, "wb") as f:
        f.write(json.dumps(cfg, indent=1).encode("utf-8"))
    corpus = generate_plan.load_corpus(corpus_path)
    trials = []
    for i in range(n_trials):
        tid = "t%05d" % (i + 1)
        ph = corpus[_below(rng, len(corpus))]
        sc = scenarios[i % len(scenarios)]
        sp = generate_plan.SPEEDS[(i // len(scenarios)) % 3]
        row = generate_plan.build_trial(ph, sc, sp, "macos", recipes, cfg, generate_plan.trial_rng(seed, tid), tid, 0)
        obs = simulate(row, KEYCAT, t0=_below(rng, 100000))
        jit = []
        for t, c in obs:
            t = int(round(t + 30.0 * _normal(rng)))
            jit.append((max(t, jit[-1][0]) if jit else t, c))
        trials.append({"trial_id": tid, "cluster": "b%d" % (i // 12), "scenario": sc, "speed": sp, "os": "macos", "phrase_id": ph["phrase_id"],
                       "observed": jit})
    return recipes, cfg, cfg_path, corpus, corpus_path, trials


def selftest():
    fails = []
    n = [0]

    def check(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append(name + (": " + detail if detail else ""))

    def raises(name, fn):
        n[0] += 1
        try:
            fn()
        except ValueError:
            return
        except Exception as e:
            fails.append("%s: a different exception: %r" % (name, e))
            return
        fails.append("%s: no exception was raised" % name)

    tmp = tempfile.mkdtemp(prefix="closed_set_selftest_")
    try:
        scen_used = ("S1", "S2", "S3", "S7", "S10", "S12")
        recipes, cfg, cfg_path, corpus, corpus_path, trials = _selftest_world(tmp, 150, 20260928, scen_used)
        feats = list(FEATURE_SETS)

        # distance, unit checks
        a = (("alphabetic", 1, 0),) * 5 + (("space", 1, 300), ("enter", 1, 100))
        check("distance: identical sequences give 0", _distance(a, a, "exact") == 0.0)
        check("distance: one substitution costs 1", _distance(a, a[:-1] + (("escape", 1, 100),), "exact") == 1.0)
        check("distance: one extra row (insertion) costs 1", _distance(a, a[1:], "exact") == 1.0)
        c5 = (("alphabetic", 5, 0), ("space", 1, 300))
        c7 = (("alphabetic", 7, 0), ("space", 1, 300))
        check("distance: a difference in the alphabetic count costs the difference (runs count)",
              _distance(c5, c7, "exact") == 2.0)
        p1 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 2000))
        p2 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 800))
        check("distance: a mismatch in the size of a long pause never exceeds W_TIME",
              0.0 < _distance(p1, p2, "exact") <= W_TIME)
        p3 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 100))
        p4 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 400))
        check("distance: differences among intervals shorter than PAUSE_MS are ignored", _distance(p3, p4, "exact") == 0.0)

        # _gap's bin1s discount: a single bin edge is not, by itself, evidence of a pause
        q1 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 1000))
        q0 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 0))
        check("positive: for timing=bin1s an interval of exactly one window is not penalised as a pause",
              _distance(q1, q0, "bin1s") == 0.0)
        check("negative: the same pair of token sequences, judged with timing=exact, is penalised "
              "(the discount is specific to bin1s, not a general exemption for this interval size)",
              _distance(q1, q0, "exact") > 0.0)
        q2 = (("alphabetic", 1, 0), ("space", 1, 0), ("enter", 1, 2000))
        check("positive: for timing=bin1s an interval of two windows is still penalised as a pause",
              _distance(q2, q0, "bin1s") > 0.0)

        # positive control, K = 10 and 40
        for K in (10, 40):
            out = evaluate(trials, corpus, K, feats, 7, recipes, cfg)
            for f in feats:
                check("removal experiment K=%d %s: every trial is counted" % (K, f),
                      out[f]["n"] == len(trials) and out[f]["n_skipped"] == 0)
            r = out["all"]
            print("K=%-3d chance %.3f | reported " % (K, 1.0 / K)
                  + " ".join("%s=%.3f" % (f, out[f]["acc"]) for f in feats))
            print("K=%-3d chance %.3f | raw      " % (K, 1.0 / K)
                  + " ".join("%s=%.3f" % (f, out[f]["acc_raw"]) for f in feats))
            check("all features exceed chance (K=%d, Wilson lower bound above 1/K)" % K,
                  r["acc_ci"][0] > 1.0 / K, "acc %.3f CI %r" % (r["acc"], r["acc_ci"]))
            if K == 10:
                check("all features exceed chance (reach an accuracy of at least 0.5 at K=10)", r["acc"] >= 0.5,
                      "acc %.3f" % r["acc"])
        # The raw values of the removal experiment are printed as measured (they need not decrease with more
        # detail); only the shape of acc/acc_raw is checked, and separately that the reported value (acc) is never
        # lower than a coarser feature set's.
        out10 = evaluate(trials, corpus, 10, feats, 7, recipes, cfg)
        check("removal experiment: every feature set has acc and acc_raw in [0, 1], each inside its own CI",
              all(0.0 <= out10[f]["acc"] <= 1.0 and out10[f]["acc_ci"][0] <= out10[f]["acc"] <= out10[f]["acc_ci"][1]
                  and 0.0 <= out10[f]["acc_raw"] <= 1.0
                  and out10[f]["acc_ci_raw"][0] <= out10[f]["acc_raw"] <= out10[f]["acc_ci_raw"][1] for f in feats))
        check("positive: the reported accuracy (acc) of every feature set is at least the reported accuracy of "
              "every feature set derivable from it by discarding detail (coarser_or_equal) -- this is what keeps "
              "the publication-granularity recommendation from understating an attack that throws information away",
              all(out10[f]["acc"] >= out10[g]["acc"] - 1e-12 for f in feats for g in coarser_or_equal(f)),
              str([(f, out10[f]["acc"]) for f in feats]))
        check("positive: the reported accuracy is at least the raw accuracy measured for the feature set itself",
              all(out10[f]["acc"] >= out10[f]["acc_raw"] - 1e-12 for f in feats))
        check("negative: acc_source names a feature set that was evaluated, and acc equals that feature set's "
              "own raw accuracy (the reported value is not invented, only selected)",
              all(out10[f]["acc_source"] in feats and abs(out10[f]["acc"] - out10[out10[f]["acc_source"]]["acc_raw"]) < 1e-9
                  for f in feats))
        check("removal experiment: by_scenario accounts for every counted trial, and covers every scenario used "
              "(including S12)",
              all(sum(v["n"] for v in out10[f]["by_scenario"].values()) == out10[f]["n"] for f in feats)
              and set(out10["all"]["by_scenario"]) == set(scen_used))

        # negative control: an observation without information (raw accuracy; see the note below acc_raw is used
        # rather than acc, because acc is a maximum over several feature sets and a maximum of several noisy
        # near-chance estimates is itself biased upward -- a property of the reporting rule, not of the attack)
        const = [(0, "alphabetic")] * 8 + [(1600, "space"), (2400, "enter")]
        flat = [dict(t, observed=list(const)) for t in trials]
        for K in (10, 40):
            oc = evaluate(flat, corpus, K, ["all", "no_time"], 7, recipes, cfg)
            for f in ("all", "no_time"):
                x = int(round(oc[f]["acc_raw"] * oc[f]["n"]))
                lo, hi = stats.wilson(x, oc[f]["n"], conf=0.99)
                check("uninformative observation stays at chance level (K=%d, %s, 1/K inside the "
                      "99%% Wilson interval on the raw accuracy; trials include S12, whose decoys are drawn from "
                      "the kana-typable pool)" % (K, f),
                      lo <= 1.0 / K <= hi and "S12" in oc[f]["by_scenario"],
                      "acc_raw %.3f interval (%.3f, %.3f) 1/K %.3f scenarios %r"
                      % (oc[f]["acc_raw"], lo, hi, 1.0 / K, sorted(oc[f]["by_scenario"])))
        # negative control 2: observations swapped between trials
        rot = [dict(t, observed=list(trials[(i + 1) % len(trials)]["observed"])) for i, t in enumerate(trials)]
        orr = evaluate(rot, corpus, 10, ["all"], 7, recipes, cfg)["all"]
        xr = int(round(orr["acc_raw"] * orr["n"]))
        lo, hi = stats.wilson(xr, orr["n"], conf=0.99)
        check("uninformative observation stays at chance level (observations swapped between trials; K=10, 99%% Wilson interval)",
              lo <= 0.1 <= hi, "acc_raw %.3f interval (%.3f, %.3f)" % (orr["acc_raw"], lo, hi))

        # determinism
        b1 = json.dumps(evaluate(trials, corpus, 10, feats, 7, recipes, cfg), sort_keys=True)
        b2 = json.dumps(evaluate(trials, corpus, 10, feats, 7, recipes, cfg), sort_keys=True)
        b3 = json.dumps(evaluate(trials, corpus, 10, feats, 8, recipes, cfg), sort_keys=True)
        check("the same seed gives byte-identical results", b1 == b2)
        check("negative control: a different seed changes the results", b1 != b3)

        # the typed phrase does not influence the choice; ties depend on the seed
        pool_typed_cache = {}

        def pool_typed(scenario):
            if scenario not in pool_typed_cache:
                ep = _scenario_pool(recipes, "macos", scenario, _pool_by_reading(corpus))
                pool_typed_cache[scenario] = (ep, _typed_map(recipes, cfg, "macos", scenario, ep))
            return pool_typed_cache[scenario]

        same = True
        for tr in trials[:30]:
            true_ph = next(p for p in corpus if p["phrase_id"] == tr["phrase_id"])
            ep, typed_of = pool_typed(tr["scenario"])
            true_typed = _typed_reading(recipes, cfg, true_ph, tr["scenario"], "macos")
            cands, ti = _make_candidates(random.Random(3), list(ep), true_ph, 10, typed_of, true_typed)
            r0 = attack(tr["observed"], cands, tr["scenario"], "macos", recipes, cfg, "all", 5, speed=tr["speed"])
            r1 = attack(tr["observed"], cands, tr["scenario"], "macos", recipes, cfg, "all", 5, speed=tr["speed"],
                        true_index=ti)
            if r0["chosen"] != r1["chosen"] or r0["ranking"] != r1["ranking"] or r0["rank_of_true"] is not None \
                    or r1["rank_of_true"] is None or r1["ranking"][0] != r1["chosen"]:
                same = False
        check("passing true_index changes neither chosen nor ranking (only rank_of_true is added)", same)

        # S10 decoy dedup by typed reading, not full reading: two phrases whose first half (S10
        # types only the first half) coincides must not appear together as the true phrase and a decoy.
        p_a = {"phrase_id": "w2-1-1", "work_id": "w2", "text": "x", "reading": "あいうえ", "reading_len": 4}
        p_b = {"phrase_id": "w2-2-1", "work_id": "w2", "text": "x", "reading": "あいかき", "reading_len": 4}
        typed_a = _typed_reading(recipes, cfg, p_a, "S10", "macos")
        typed_b = _typed_reading(recipes, cfg, p_b, "S10", "macos")
        check("positive: typed_reading collapses two phrases with a different full reading to the same S10 "
              "(first-half) reading, setting up the collision this dedup must catch",
              typed_a == "あい" and typed_b == "あい" and typed_a == typed_b, "%r %r" % (typed_a, typed_b))
        raises("negative: _make_candidates excludes a decoy whose typed reading collides with the true one, so a "
              "pool of only such phrases has too few eligible decoys and raises rather than silently including one",
              lambda: _make_candidates(random.Random(1), [p_b], p_a, 2, _typed_map(recipes, cfg, "macos", "S10", [p_b]),
                                       typed_a))
        ok_cands, ok_ti = _make_candidates(random.Random(1), [p_b] + corpus[:20], p_a, 5,
                                           _typed_map(recipes, cfg, "macos", "S10", [p_b] + corpus[:20]), typed_a)
        check("positive: with enough non-colliding phrases available, _make_candidates succeeds and never includes "
              "the colliding decoy p_b",
              ok_cands[ok_ti]["phrase_id"] == "w2-1-1" and all(c["phrase_id"] != "w2-2-1" for c in ok_cands))

        # _scenario_pool: a decoy is drawn only from phrases the scenario could actually produce.
        mixed = [
            {"phrase_id": "w3-1-1", "work_id": "w3", "text": "x", "reading": "あいう", "reading_len": 3},
            # not kana-typable: `け` is outside recipes.json's kana_layout
            {"phrase_id": "w3-2-1", "work_id": "w3", "text": "x", "reading": "けんこう", "reading_len": 4},
            # kana-typable but shorter than S10's reading_len minimum (2)
            {"phrase_id": "w3-3-1", "work_id": "w3", "text": "x", "reading": "あ", "reading_len": 1},
        ]
        s12_pool = _scenario_pool(recipes, "macos", "S12", mixed)
        check("positive: _scenario_pool keeps a kana-typable phrase for a kana-input scenario (S12)",
              any(p["phrase_id"] == "w3-1-1" for p in s12_pool))
        check("negative: _scenario_pool drops a phrase with a character outside the kana layout for S12",
              all(p["phrase_id"] != "w3-2-1" for p in s12_pool))
        s10_pool = _scenario_pool(recipes, "macos", "S10", mixed)
        check("negative: _scenario_pool drops a phrase shorter than the scenario's reading_len minimum (S10: 2)",
              all(p["phrase_id"] != "w3-3-1" for p in s10_pool))
        check("positive: _scenario_pool keeps a phrase that satisfies S10's reading_len minimum",
              any(p["phrase_id"] == "w3-1-1" for p in s10_pool))

        # integration: evaluate() must never let a decoy pool include a phrase the scenario could not have produced
        # -- if it did, S12 would try to type a character outside the kana layout and raise.
        mixed_corpus = mixed + list(corpus[:30])
        s12_row = generate_plan.build_trial(mixed[0], "S12", "mid", "macos", recipes, cfg,
                                            generate_plan.trial_rng(1, "s12t"), "s12t", 0)
        s12_trials = [{"trial_id": "s12t", "cluster": "b0", "scenario": "S12", "speed": "mid", "os": "macos",
                      "phrase_id": mixed[0]["phrase_id"], "observed": simulate(s12_row, KEYCAT, t0=0)}]
        try:
            evaluate(s12_trials, mixed_corpus, 10, ["all"], 1, recipes, cfg)
            no_crash = True
        except ValueError:
            no_crash = False
        check("positive: evaluate() does not crash on an S12 trial when the corpus also holds a phrase that is not "
              "kana-typable (that phrase must never be chosen as a decoy)", no_crash)

        dup = [corpus[0]] * 6
        picks = set(attack(const, dup, "S1", "macos", recipes, cfg, "all", 5, tie_seed=s)["chosen"] for s in range(40))
        check("ties depend on tie_seed (identical candidates: several are chosen over 40 seeds)", len(picks) > 1)
        check("the same tie_seed gives the same choice",
              attack(const, dup, "S1", "macos", recipes, cfg, "all", 5, tie_seed=9)["chosen"]
              == attack(const, dup, "S1", "macos", recipes, cfg, "all", 5, tie_seed=9)["chosen"])
        raises("empty candidate list", lambda: attack(const, [], "S1", "macos", recipes, cfg, "all", 5))
        raises("unknown feature set", lambda: attack(const, dup, "S1", "macos", recipes, cfg, "nope", 5))
        kana_only = {"phrase_id": "w9-1-1", "work_id": "w9", "text": "", "reading": "がっこう", "reading_len": 4}
        rk = attack(const, [kana_only, corpus[1]], "S12", "macos", recipes, cfg, "all", 5)
        check("a candidate that cannot be typed in the scenario (S12, character outside the kana layout) has distance inf and ranks last",
              rk["distances"][0] == float("inf") and rk["chosen"] == 1)

        # inputs: publishable logs
        good = ['{"type":"meta","format":"obs/1","os":"macos","logger_version":"0.0.0","manifest_sha256":"%s",'
                '"capabilities":{"unconditional":["key"],"threshold_gated":[],"config_gated":["ime_shadow"]}}' % ("0" * 64),
                '{"type":"key","t":100,"category":"alphabetic","press":true}',
                '{"type":"key","t":160,"category":"alphabetic","press":false}',
                '{"type":"ime_shadow","t":300,"dwell_ms":200,"nav":0,"abandon":false}']

        def obs_file(lines, name="o.ndjson"):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(("\n".join(lines) + "\n").encode("utf-8"))
            return p

        ev, sh, ro = read_obs(obs_file(good))
        check("read_obs takes key presses only (not releases), counts ime_shadow rows, and returns the header's os",
              ev == [(100, "alphabetic")] and sh == 1 and ro == "macos")
        raises("read_obs rejects a row type that is not read", lambda: read_obs(obs_file(good + ['{"type":"sent","t":1}'])))
        raises("read_obs rejects an unknown key in a key row (a raw key code)",
               lambda: read_obs(obs_file(good + ['{"type":"key","t":1,"category":"space","press":true,"vk":32}'])))
        raises("read_obs rejects a category outside the enumeration",
               lambda: read_obs(obs_file(good + ['{"type":"key","t":1,"category":"kana","press":true}'])))
        raises("read_obs rejects a missing header", lambda: read_obs(obs_file(good[1:])))
        raises("read_obs rejects key rows out of time order",
               lambda: read_obs(obs_file(good + ['{"type":"key","t":50,"category":"space","press":true}'])))
        raises("negative: read_obs rejects a header whose os is neither macos nor windows",
               lambda: read_obs(obs_file([good[0].replace('"os":"macos"', '"os":"linux"')] + good[1:])))
        raises("read_obs rejects an ime_shadow row with a non-integer dwell_ms",
               lambda: read_obs(obs_file(good + ['{"type":"ime_shadow","t":1,"dwell_ms":1.5,"nav":0,"abandon":false}'])))
        raises("read_obs rejects an ime_shadow row with a non-boolean abandon",
               lambda: read_obs(obs_file(good + ['{"type":"ime_shadow","t":1,"dwell_ms":1,"nav":0,"abandon":0}'])))
        check("positive: read_obs accepts a well-formed ime_shadow row (control for the two checks above)",
              read_obs(obs_file(good))[1] == 1)
        raises("negative: read_obs rejects a JSON object with a duplicate key rather than keeping the last value",
               lambda: read_obs(obs_file(good + ['{"type":"key","t":900,"t":901,"category":"space","press":true}'])))
        raises("negative: read_obs turns deeply nested JSON into the same clean error as other malformed input, "
              "not an uncaught exception",
               lambda: read_obs(obs_file(good + [("[" * 4000) + ("]" * 4000)])))

        # command line, end to end (plan -> observation -> attack -> JSON)
        cli_cfg_path = os.path.join(tmp, "cfg_cli.json")
        with open(cli_cfg_path, "wb") as f:
            f.write(json.dumps(dict(cfg, scenarios=["S1", "S2", "S3"], n_per_cell_scenario_speed=4)).encode("utf-8"))
        with open(cli_cfg_path, "rb") as f:
            cli_cfg = _loads(f.read().decode("utf-8"))
        rows, _ = generate_plan.plan_rows(corpus, recipes, cli_cfg, "macos", 3, _sha_file(cli_cfg_path),
                                          _sha_file(os.path.join(_KIT, "gen", "recipes.json")), "0" * 64)
        plan_path = os.path.join(tmp, "plan.jsonl")
        with open(plan_path, "wb") as f:
            f.write(generate_plan.render_plan(rows))
        prng = random.Random(5)
        clock = 1000000
        obs_lines = [good[0]]
        mk_lines = [good[0]]
        for r in rows[1:]:
            if r["kind"] == "block":
                clock += 5000
                for _ in r["marker"]:
                    mk_lines.append('{"type":"key","t":%d,"category":"other","press":true}' % clock)
                    clock += r["marker_gap_ms"]
                continue
            clock += r["gap_before_ms"]
            for t, c in simulate(r, KEYCAT, t0=clock):
                t = int(round(t + 30.0 * _normal(prng)))
                line = '{"type":"key","t":%d,"category":"%s","press":true}' % (t, c)
                obs_lines.append(line)
                mk_lines.append(line)
            clock += r["steps"][-1]["at_ms"] + 2000

        def fix_order(lines):
            out, last = [lines[0]], -1
            for ln in lines[1:]:
                o = json.loads(ln)
                o["t"] = max(o["t"], last)
                last = o["t"]
                out.append(json.dumps(o, separators=(",", ":")))
            return out
        obs_p = obs_file(fix_order(obs_lines), "obs1.ndjson")
        mk_p = obs_file(fix_order(mk_lines), "obs_mk.ndjson")
        out_ctr = [0]

        def new_out():
            out_ctr[0] += 1
            return os.path.join(tmp, "out%d.json" % out_ctr[0])

        out_a, out_b = new_out(), new_out()
        base = ["--plan", plan_path, "--corpus", corpus_path, "--recipes", os.path.join(_KIT, "gen", "recipes.json"),
                "--config", cli_cfg_path, "--K", "10,40", "--seed", "3"]
        rc_a = main(["--obs", obs_p] + base + ["--out", out_a])
        rc_b = main(["--obs", obs_p] + base + ["--out", out_b])
        check("command line: an end-to-end run exits 0", rc_a == 0 and rc_b == 0, "%r %r" % (rc_a, rc_b))
        if rc_a == 0 and rc_b == 0:
            with open(out_a, "rb") as f1, open(out_b, "rb") as f2:
                ba, bb = f1.read(), f2.read()
            check("command line: the same input gives byte-identical output", ba == bb)
            js = json.loads(ba.decode("utf-8"))
            check("command line: the output holds both K values, every feature set, the assumptions and n",
                  sorted(js["results"]) == ["10", "40"] and set(js["results"]["10"]) == set(feats)
                  and js["n_trials"] == 36 and js["results"]["10"]["all"]["n"] == 36 and len(js["assumptions"]) >= 4
                  and js["n_ime_shadow_rows_unused"] == 0, str(js.get("n_trials")))
            check("command line: the output's params include phase_step_ms, and every feature set's result holds "
                  "acc_raw, acc_source and by_scenario",
                  js["params"]["phase_step_ms"] == PHASE_STEP_MS
                  and all("acc_raw" in js["results"]["10"][f] and "acc_source" in js["results"]["10"][f]
                          and "by_scenario" in js["results"]["10"][f] for f in feats))
            check("command line: the attack works (all features, K=10, accuracy above chance)",
                  js["results"]["10"]["all"]["acc"] > js["results"]["10"]["all"]["chance"], str(js["results"]["10"]["all"]))
            check("command line: without --skip-scenario the output has no skipped_scenarios (the frozen format)",
                  "skipped_scenarios" not in js)
        out_s = new_out()
        rc_s = main(["--obs", obs_p] + base + ["--out", out_s, "--skip-scenario", "S2"])
        check("command line: --skip-scenario S2 exits 0", rc_s == 0, str(rc_s))
        if rc_s == 0:
            with open(out_s, "rb") as f:
                js_s = json.loads(f.read().decode("utf-8"))
            r_s = js_s["results"]["10"]["all"]
            check("command line: --skip-scenario S2 leaves the 12 S2 trials out of the attack and states them, "
                  "while n_trials and the alignment still cover the whole plan",
                  js_s["skipped_scenarios"] == {"S2": 12} and js_s["n_trials"] == 36 and r_s["n"] == 24
                  and r_s["n_skipped"] == 0 and "S2" not in r_s["by_scenario"]
                  and sorted(r_s["by_scenario"]) == ["S1", "S3"], str((js_s.get("skipped_scenarios"), r_s["n"])))
        out_z = new_out()
        rc_z = main(["--obs", obs_p] + base + ["--out", out_z, "--skip-scenario", "S12"])
        skipped_z = None
        if rc_z == 0:
            with open(out_z, "rb") as f:
                skipped_z = json.loads(f.read().decode("utf-8")).get("skipped_scenarios")
        check("command line: --skip-scenario of a scenario absent from the plan exits 0 and states zero",
              rc_z == 0 and skipped_z == {"S12": 0}, str((rc_z, skipped_z)))
        check("command line: --skip-scenario rejects a name that is not a scenario (exit 2)",
              main(["--obs", obs_p] + base + ["--out", new_out(), "--skip-scenario", "s2"]) == 2)
        check("command line: --skip-scenario rejects the same scenario twice (exit 2)",
              main(["--obs", obs_p] + base + ["--out", new_out(), "--skip-scenario", "S2", "--skip-scenario", "S2"]) == 2)
        rc_m = main(["--obs", mk_p] + base + ["--out", new_out(), "--markers-in-obs"])
        check("command line: --markers-in-obs skips the marker presses and succeeds", rc_m == 0, str(rc_m))
        check("command line: marker presses without --markers-in-obs stop the run (exit 2)",
              main(["--obs", mk_p] + base + ["--out", new_out()]) == 2)
        drop = fix_order(obs_lines[:5] + obs_lines[6:])
        check("command line: one missing press stops the run instead of shifting later trials (exit 2)",
              main(["--obs", obs_file(drop, "drop.ndjson")] + base + ["--out", new_out()]) == 2)
        check("command line: differing counts of --obs and --plan stop the run (exit 2)",
              main(["--obs", obs_p, obs_p] + base + ["--out", new_out()]) == 2)
        check("command line: a corpus too small for K stops the run (exit 2)",
              main(["--obs", obs_p] + [a if a != "10,40" else "1000" for a in base] + ["--out", new_out()]) == 2)
        check("command line: no arguments exit 2", main([]) == 2)
        with open(plan_path, "rb") as f:
            plan_bytes = f.read()
        off_path = os.path.join(_KIT, "gen", "recipes.macos-live-off.json")
        check("negative: command line: a plan built from another recipes file than --recipes stops the run (exit 2)",
              plan_bytes.count(_sha_file(os.path.join(_KIT, "gen", "recipes.json")).encode("ascii")) == 1
              and main(["--obs", obs_p] + [a if a != os.path.join(_KIT, "gen", "recipes.json") else off_path
                                           for a in base] + ["--out", new_out()]) == 2)
        check("negative: command line: a plan built from another config file than --config stops the run (exit 2)",
              plan_bytes.count(_sha_file(cli_cfg_path).encode("ascii")) == 1
              and _sha_file(cfg_path) != _sha_file(cli_cfg_path)
              and main(["--obs", obs_p] + [a if a != cli_cfg_path else cfg_path for a in base]
                       + ["--out", new_out()]) == 2)
        check("positive: command line: --ime-toggle-in-obs is accepted and does not break a run whose plan has no "
              "IME toggle key (this plan's scenarios are S1/S2/S3)",
              main(["--obs", obs_p] + base + ["--out", new_out(), "--ime-toggle-in-obs"]) == 0)

        # --out is create-only: an existing path is refused, and left untouched, instead of being
        # silently overwritten
        pre = new_out()
        with open(pre, "wb") as f:
            f.write(b"{}")
        check("negative: command line: --out refuses to overwrite an existing file (exit 2)",
              main(["--obs", obs_p] + base + ["--out", pre]) == 2)
        with open(pre, "rb") as f:
            check("positive: the pre-existing --out file was left untouched", f.read() == b"{}")

        # the observation's header os must match the plan's os
        mismatched = obs_file([good[0].replace('"os":"macos"', '"os":"windows"')] + good[1:], "os_mismatch.ndjson")
        ev_m, sh_m, os_m = read_obs(mismatched)
        raises("negative: segment_by_plan rejects an observation whose header os differs from the plan's os",
               lambda: segment_by_plan(ev_m, read_plan(plan_path), os_m))

        # --ime-toggle-in-obs / expected_categories / segment_by_plan: the default (no switch) is
        # that neither the block-start markers nor the IME toggle keys produce an observed row.
        s14row = generate_plan.build_trial(corpus[0], "S14", "mid", "macos", recipes, cfg,
                                           generate_plan.trial_rng(1, "s14row"), "t00001", 0)
        exp_default = expected_categories(s14row)
        exp_toggle = expected_categories(s14row, ime_toggle_in_obs=True)
        check("positive: --ime-toggle-in-obs adds the two IME toggle keys, as category other, to the expected "
              "sequence of an S14 trial (reading, space, ime_off, ime_on)",
              exp_toggle == exp_default + ["other", "other"], repr((exp_default, exp_toggle)))
        check("negative: by default (no switch) the IME toggle keys are absent (S14 ends in space, not other)",
              exp_default[-1] == "space" and "other" not in exp_default)
        s14_header = {"kind": "header", "format": "plan/1", "os": "macos"}
        s14_rows = [s14_header, s14row]
        obs14 = simulate(s14row, KEYCAT, t0=0)
        check("positive: segment_by_plan accepts an observation without the IME toggle presses by default",
              len(segment_by_plan(obs14, s14_rows, "macos")[0]) == 1)
        obs14_toggle = obs14 + [(obs14[-1][0] + 10, "other"), (obs14[-1][0] + 20, "other")]
        raises("negative: segment_by_plan rejects the same observation with the two extra IME-toggle rows when "
              "the switch is off",
               lambda: segment_by_plan(obs14_toggle, s14_rows, "macos"))
        check("positive: segment_by_plan accepts the IME-toggle rows once --ime-toggle-in-obs is set",
              len(segment_by_plan(obs14_toggle, s14_rows, "macos", ime_toggle_in_obs=True)[0]) == 1)

        # ---- block resynchronisation (--markers-in-obs): a block that does not match the plan is left unscored
        rows_p = read_plan(plan_path)
        evs_mk, _, os_mk = read_obs(mk_p)
        layout = []    # [marker presses, trial presses, trials, block_id] of every block
        for r in rows_p[1:]:
            if r["kind"] == "block":
                layout.append([len(r["marker"]), 0, 0, r["block_id"]])
            else:
                layout[-1][1] += len(expected_categories(r))
                layout[-1][2] += 1
        blen = [m + k for m, k, _, _ in layout]
        bstart = [sum(blen[:i]) for i in range(len(blen))]
        n_all = sum(t for _, _, t, _ in layout)

        def seg(evs):
            return segment_by_plan(evs, rows_p, os_mk, True)

        def lost(evs, i):
            """segment_by_plan on evs; True when exactly block i is unscored and its counts are right."""
            tr, al = seg(evs)
            ids = set(t["trial_id"] for t in tr)
            return (al["unscored_block_ids"] == [str(layout[i][3])] and al["n_blocks_unscored"] == 1
                    and al["n_trials_unscored"] == layout[i][2] and len(tr) == n_all - layout[i][2]
                    and sum(al["unscored_by_scenario"].values()) == layout[i][2]
                    and sum(al["unscored_by_speed"].values()) == layout[i][2]
                    and not any(t["block_id"] == layout[i][3] for t in tr) and len(ids) == len(tr)), al

        check("alignment setup: the plan has several blocks and an intact observation scores every trial",
              len(layout) >= 3 and len(seg(evs_mk)[0]) == n_all and seg(evs_mk)[1]["n_blocks"] == len(layout)
              and seg(evs_mk)[1]["n_blocks_unscored"] == 0 and seg(evs_mk)[1]["n_presses_unassigned"] == 0
              and len(evs_mk) == sum(blen) and seg(evs_mk)[1]["n_presses"] == len(evs_mk))
        check("alignment: the observed presses of a scored trial are its own presses",
              [c for _, c in seg(evs_mk)[0][0]["observed"]]
              == expected_categories(next(r for r in rows_p[1:] if r["kind"] == "trial")))
        for i in (0, 1, len(layout) - 1):
            ok, al = lost(evs_mk[:bstart[i] + layout[i][0] + 2] + evs_mk[bstart[i] + layout[i][0] + 3:], i)
            check("alignment: one missing press inside block %d leaves only that block unscored" % i, ok, repr(al))
            check("alignment: that block's presses are counted as unassigned (block %d)" % i,
                  al["n_presses_unassigned"] == blen[i] - 1 and al["n_presses"] == len(evs_mk) - 1, repr(al))
        i = 1
        ext = evs_mk[:bstart[i] + layout[i][0] + 2] + [(0, "enter")] + evs_mk[bstart[i] + layout[i][0] + 2:]
        ok, al = lost(ext, i)
        check("alignment: one extra press inside a block leaves only that block unscored", ok, repr(al))
        check("alignment: the extra press and the block's presses are counted as unassigned",
              al["n_presses_unassigned"] == blen[i] + 1, repr(al))
        stray = evs_mk[:bstart[1]] + [(0, "escape")] + evs_mk[bstart[1]:]
        tr_s, al_s = seg(stray)
        check("alignment: a stray press between two blocks loses no block and is counted as one unassigned press",
              len(tr_s) == n_all and al_s["n_blocks_unscored"] == 0 and al_s["n_presses_unassigned"] == 1, repr(al_s))
        stray2 = evs_mk[:bstart[1]] + [(0, "escape"), (0, "escape"), (0, "enter")] + evs_mk[bstart[1]:]
        tr_s2, al_s2 = seg(stray2)
        check("alignment: several stray presses before a block are all counted as unassigned",
              len(tr_s2) == n_all and al_s2["n_presses_unassigned"] == 3, repr(al_s2))
        tail = evs_mk + [(0, "escape")] * 5
        tr_t, al_t = seg(tail)
        check("alignment: presses left after the last block are counted as unassigned, without an error",
              len(tr_t) == n_all and al_t["n_blocks_unscored"] == 0 and al_t["n_presses_unassigned"] == 5, repr(al_t))
        for i in (0, 1, len(layout) - 1):
            ok, al = lost(evs_mk[:bstart[i]] + evs_mk[bstart[i] + 1:], i)
            check("alignment: a press missing from the marker of block %d leaves that block unscored and no other"
                  % i, ok, repr(al))
        bad_two = evs_mk[:bstart[1]] + evs_mk[bstart[1] + 1:bstart[2] + layout[2][0] + 1] \
            + evs_mk[bstart[2] + layout[2][0] + 2:]
        tr_b, al_b = seg(bad_two)
        check("alignment: a damaged marker in one block and a missing press in the next leave both unscored and "
              "the others scored (no crash, no shift)",
              al_b["unscored_block_ids"] == [str(layout[1][3]), str(layout[2][3])]
              and len(tr_b) == n_all - layout[1][2] - layout[2][2]
              and al_b["n_trials_unscored"] == layout[1][2] + layout[2][2], repr(al_b))
        check("alignment: a trial of a scored block is the same with and without damage elsewhere (the cut is by plan)",
              [t["observed"] for t in tr_b if t["block_id"] == layout[0][3]]
              == [t["observed"] for t in seg(evs_mk)[0] if t["block_id"] == layout[0][3]])
        gone = [e for e in evs_mk if e[1] not in ("other",)]
        tr_g, al_g = seg(gone)
        check("alignment: with every marker press missing no block can be found again: all unscored, no error",
              tr_g == [] and al_g["n_blocks_unscored"] == len(layout) and al_g["n_trials_unscored"] == n_all
              and al_g["n_presses_unassigned"] == len(gone), repr(al_g))
        # navigation marker (macOS): anchored at the right end of a run, never a run of arrows inside a trial
        nav = "navigation"
        mk4 = [nav] * 4
        t1 = ["alphabetic", "space", nav, nav, nav, "enter"]
        t2 = ["alphabetic", "enter"]
        nb = [(mk4, [t1, t2]), (mk4, [t2, t1]), (mk4, [t1])]
        sq = [mk4 + t1 + t2, mk4 + t2 + t1, mk4 + t1]
        full = sq[0] + sq[1] + sq[2]
        check("align_blocks: an intact sequence with a four-press navigation marker is placed block by block",
              align_blocks(full, nb) == ([0, len(sq[0]), len(sq[0]) + len(sq[1])], 0))
        cut = len(sq[0])
        check("align_blocks: a press missing from a navigation marker (a run of three) leaves that block unscored; "
              "the next block is found by its own marker",
              align_blocks(full[:cut] + full[cut + 1:], nb) == ([0, None, len(sq[0]) + len(sq[1]) - 1], len(sq[1]) - 1))
        check("align_blocks: an extra navigation press in front of a marker (a run of five) hides nothing: the "
              "block is placed after the added press, which is unassigned",
              align_blocks(full[:cut] + [nav] + full[cut:], nb) == ([0, cut + 1, len(sq[0]) + len(sq[1]) + 1], 1))
        check("_is_anchor: the marker is an anchor at the right end of a run; in a longer run only the last "
              "len(marker) presses are; a shorter run is not",
              _is_anchor([nav] * 4, 0, mk4) and not _is_anchor([nav] * 5, 0, mk4) and _is_anchor([nav] * 5, 1, mk4)
              and _is_anchor([nav] * 7, 3, mk4) and not _is_anchor([nav] * 7, 2, mk4)
              and _is_anchor(["space"] + [nav] * 4 + ["enter"], 1, mk4) and not _is_anchor([nav] * 3, 0, mk4))
        mid = cut + 4 + len(t2) + 3
        check("align_blocks: a missing press among the arrows of a trial leaves only that block unscored",
              align_blocks(full[:mid] + full[mid + 1:], nb)[0] == [0, None, len(sq[0]) + len(sq[1]) - 1])
        check("align_blocks: a run of three arrows inside a trial is never taken for a marker (nothing placed when "
              "only trial presses are present)",
              align_blocks(t1 + t1, nb)[0] == [None, None, None])
        # a run of exactly four navigation presses inside a trial that is not a marker (an added press) loses that block only
        rm = ["nav"] * 4

        def rblk(k):
            return (rm, [["L"] * (3 + k) + ["sp", "ret"], ["L"] * (2 + k % 3) + ["sp", "nav", "nav", "nav", "ret"],
                         ["L"] * (1 + (k * 2) % 5) + ["bs", "ret"]])
        rbl = [rblk(k) for k in range(8)]
        rsq = [list(m) + [c for t in ts for c in t] for m, ts in rbl]
        roff = [sum(len(x) for x in rsq[:j]) for j in range(len(rsq))]
        rcats = [c for x in rsq for c in x]
        rb = 1
        rpos = roff[rb] + 4 + len(rbl[rb][1][0]) + (2 + rb % 3) + 2     # after the third arrow of trial 2 of block 1
        rst, rn = align_blocks(rcats[:rpos] + ["nav"] + rcats[rpos:], rbl)
        check("align_blocks: an added arrow that makes a run of four inside a trial loses that block only; the blocks "
              "after it are placed one press later, and the lost block's presses and the added one are unassigned",
              rst == [roff[0], None] + [roff[j] + 1 for j in range(2, 8)] and rn == len(rsq[rb]) + 1, repr((rst, rn)))
        rpos = roff[rb] + len(rsq[rb]) - 1                  # an added arrow before the last press of block 1
        rst, rn = align_blocks(rcats[:rpos] + ["nav"] + rcats[rpos:], rbl)
        check("align_blocks: an added arrow before the last press of a block loses that block only",
              rst == [roff[0], None] + [roff[j] + 1 for j in range(2, 8)], repr(rst))
        rpos = roff[rb + 1]                                 # an added arrow right before the marker of block 2
        rst, rn = align_blocks(rcats[:rpos] + ["nav"] + rcats[rpos:], rbl)
        check("align_blocks: an added arrow right before a marker (a run of five) loses no block; the added press "
              "is unassigned and the blocks after it are placed one press later",
              rst == [roff[0], roff[1], roff[2] + 1] + [roff[j] + 1 for j in range(3, 8)] and rn == 1, repr((rst, rn)))
        # a block whose last trial ends in arrow presses (as the macOS live-conversion-off recipe does) and whose
        # closing Enter is missing: the arrows join the marker of the next block into a run of five to seven
        for na in (1, 2, 3):
            ends = [("L", "sp") + ("nav",) * na + ("ret",)]
            eb = [(rm, [["L"] * (1 + k) + ["ret"], list(ends[0])]) for k in range(4)]
            esq = [list(m) + [c for t in ts for c in t] for m, ts in eb]
            eoff = [sum(len(x) for x in esq[:j]) for j in range(len(esq))]
            ecats = [c for x in esq for c in x]
            for lost_b in (0, 1, 2):
                cutat = eoff[lost_b] + len(esq[lost_b]) - 1          # the last Enter of block lost_b
                est, en = align_blocks(ecats[:cutat] + ecats[cutat + 1:], eb)
                want = [None if j == lost_b else (eoff[j] if j < lost_b else eoff[j] - 1) for j in range(4)]
                check("align_blocks: the closing Enter of block %d missing after %d arrows loses that block only"
                      % (lost_b, na), est == want and en == len(esq[lost_b]) - 1, repr((est, en)))
        # a press added after the last trial of a block (between two blocks) loses no block
        epos = eoff[2]
        est, en = align_blocks(ecats[:epos] + ["nav"] + ecats[epos:], eb)
        check("align_blocks: an arrow added between two blocks loses no block and is counted as unassigned",
              est == [eoff[0], eoff[1], eoff[2] + 1, eoff[3] + 1] and en == 1, repr((est, en)))
        # an arrow added in front of the marker of the first block: the block is scored
        est, en = align_blocks(["nav"] + ecats, eb)
        check("align_blocks: an arrow added in front of the first marker leaves every block scored",
              est == [eoff[j] + 1 for j in range(4)] and en == 1, repr((est, en)))
        rst, rn = align_blocks(rcats[:roff[3]] + rcats[roff[3] + 1:], rbl)
        check("align_blocks: with an unscored block the next block scans from the same place (a damaged marker in "
              "block 3 loses block 3 only)",
              rst == [roff[0], roff[1], roff[2], None] + [roff[j] - 1 for j in range(4, 8)], repr(rst))
        # the following block, found whole at an anchor, takes the cursor even when block i would match further on
        sw = align_blocks(sq[1] + sq[0], nb[:2])
        check("align_blocks: when the following block matches at an anchor, the block before it is left unscored "
              "even if its own sequence stands later in the observation",
              sw == ([None, 0], len(sq[0])), repr(sw))
        mk3 = [nav] * 3
        s3 = mk3 + t2 + t1
        sw2 = align_blocks(s3 + sq[0], [(mk4, [t1, t2]), (mk3, [t2, t1])])
        check("align_blocks: the marker of the following block is an anchor too, when the markers differ",
              sw2 == ([None, 0], len(sq[0])), repr(sw2))
        nb4 = nb + [(mk4, [t2])]
        sq4 = sq + [mk4 + t2]
        full4 = sq4[0] + sq4[1][:4] + sq4[1][5:] + sq4[2][:4] + sq4[2][5:] + sq4[3]
        al4 = align_blocks(full4, nb4)
        check("align_blocks: two blocks lost in a row leave the blocks after them scored (no anchor qualifies for "
              "the first one; the cursor stays)",
              al4[0] == [0, None, None, len(sq4[0]) + len(sq4[1]) + len(sq4[2]) - 2], repr(al4))
        check("align_blocks: an empty observation places nothing and does not fail",
              align_blocks([], nb) == ([None, None, None], 0))
        last = bstart[1] + blen[1] - 1
        ok, al = lost(evs_mk[:last] + [(evs_mk[last][0], "escape")] + evs_mk[last + 1:], 1)
        check("alignment: a different category in the last press of a block leaves that block unscored", ok, repr(al))
        # without markers there is no anchor: any mismatch stops the run
        ev_nm, _, os_nm = read_obs(obs_p)
        raises("without --markers-in-obs a missing press raises (no anchor to resynchronise to)",
               lambda: segment_by_plan(ev_nm[:7] + ev_nm[8:], rows_p, os_nm))
        raises("without --markers-in-obs an extra press raises",
               lambda: segment_by_plan(ev_nm[:7] + [(0, "enter")] + ev_nm[7:], rows_p, os_nm))
        raises("without --markers-in-obs presses left after the last trial raise",
               lambda: segment_by_plan(ev_nm + [(0, "enter")], rows_p, os_nm))
        miss_p = obs_file(fix_order(mk_lines[:bstart[1] + layout[1][0] + 4] + mk_lines[bstart[1] + layout[1][0] + 5:]),
                          "mk_missing.ndjson")
        out_ms = new_out()
        rc_ms = main(["--obs", miss_p] + base + ["--out", out_ms, "--markers-in-obs", "--bootstrap", "50"])
        check("command line: with --markers-in-obs one missing press does not stop the run (exit 0)", rc_ms == 0, str(rc_ms))
        if rc_ms == 0:
            with open(out_ms, "rb") as fh:
                jm = json.loads(fh.read().decode("utf-8"))
            ali = jm["alignment"]
            check("command line: the output reports the alignment (one unscored block, its trials by scenario and "
                  "speed, the unassigned presses) and n counts the scored trials only",
                  ali["n_blocks"] == len(layout) and ali["n_blocks_unscored"] == 1
                  and ali["unscored_block_ids"] == ["s0-" + str(layout[1][3])]
                  and ali["n_trials_unscored"] == layout[1][2] and jm["n_trials"] == n_all - layout[1][2]
                  and jm["results"]["10"]["all"]["n"] == n_all - layout[1][2]
                  and sum(ali["unscored_by_scenario"].values()) == layout[1][2]
                  and sum(ali["unscored_by_speed"].values()) == layout[1][2]
                  and ali["n_presses_unassigned"] == blen[1] - 1 and jm["bootstrap"] == 50
                  and jm["results"]["10"]["all"]["n_blocks"] == len(layout) - 1, repr(ali))
            check("command line: the output holds the block-bootstrap fields and by_speed",
                  all(k in jm["results"]["10"]["all"] for k in ("acc_ci_block", "acc_ci_raw_block", "ci_block_status",
                                                                  "by_speed", "n_blocks")))
        check("command line: --bootstrap must be at least 1 (exit 2)",
              main(["--obs", mk_p] + base + ["--out", new_out(), "--markers-in-obs", "--bootstrap", "0"]) == 2)

        # ---- descriptive block-bootstrap interval and by_speed
        fb = ["all", "counts_only"]
        e1 = evaluate(trials, corpus, 10, fb, 7, recipes, cfg, B=200)
        e2 = evaluate(trials, corpus, 10, fb, 7, recipes, cfg, B=200)
        e3 = evaluate(trials, corpus, 10, fb, 8, recipes, cfg, B=200)
        for f in fb:
            r1 = e1[f]
            check("block bootstrap %s: status ok, 13 blocks, ordered intervals inside [0, 1]" % f,
                  r1["ci_block_status"] == "ok" and r1["n_blocks"] == 13
                  and 0.0 <= r1["acc_ci_block"][0] <= r1["acc_ci_block"][1] <= 1.0
                  and 0.0 <= r1["acc_ci_raw_block"][0] <= r1["acc_ci_raw_block"][1] <= 1.0, repr(r1))
            check("block bootstrap %s: deterministic for a fixed seed" % f,
                  (r1["acc_ci_block"], r1["acc_ci_raw_block"]) == (e2[f]["acc_ci_block"], e2[f]["acc_ci_raw_block"]))
            check("block bootstrap %s: the interval of the maximum is not below that of the raw accuracy "
                  "(the maximum is taken inside each resample)" % f,
                  r1["acc_ci_block"][0] >= r1["acc_ci_raw_block"][0] - 1e-12
                  and r1["acc_ci_block"][1] >= r1["acc_ci_raw_block"][1] - 1e-12)
        check("block bootstrap: a different seed gives different intervals",
              (e1["all"]["acc_ci_raw_block"], e1["counts_only"]["acc_ci_raw_block"])
              != (e3["all"]["acc_ci_raw_block"], e3["counts_only"]["acc_ci_raw_block"]))
        # the interval of acc_raw reproduced from per-block evaluations (a trial's result does not depend on the
        # other trials) and stats.cluster_bootstrap
        per_block = {}
        for t in trials:
            per_block.setdefault(t["cluster"], []).append(t)
        pb = dict((c, evaluate(ts, corpus, 10, fb, 7, recipes, cfg, B=10)) for c, ts in per_block.items())
        for f in fb:
            cl = [((pb[c][f]["n"], int(round(pb[c][f]["acc_raw"] * pb[c][f]["n"]))),) for c in per_block]
            ref = stats.cluster_bootstrap(cl, lambda smp: sum(c[0][1] for c in smp) / sum(c[0][0] for c in smp), 200,
                                          _sub_seed(7, "block_bootstrap", 10))
            check("block bootstrap %s: acc_ci_raw_block equals stats.cluster_bootstrap over the blocks' own counts" % f,
                  all(abs(a - b) < 1e-12 for a, b in zip(e1[f]["acc_ci_raw_block"], ref)),
                  repr((e1[f]["acc_ci_raw_block"], ref)))
        one = evaluate([dict(t, cluster="only") for t in trials], corpus, 10, fb, 7, recipes, cfg, B=50)
        check("block bootstrap: with one block the intervals are [None, None] and the status says so",
              all(one[f]["acc_ci_block"] == [None, None] and one[f]["acc_ci_raw_block"] == [None, None]
                  and one[f]["ci_block_status"] == "fewer_than_two_blocks" and one[f]["n_blocks"] == 1 for f in fb))
        check("block bootstrap: the point values and the Wilson intervals do not depend on the clustering",
              all(one[f]["acc"] == e1[f]["acc"] and one[f]["acc_ci"] == e1[f]["acc_ci"] for f in fb))
        for f in fb:
            bs = e1[f]["by_speed"]
            check("by_speed %s: one entry per speed with the number of trials of that speed" % f,
                  set(bs) == {"slow", "mid", "fast"}
                  and all(bs[sp]["n"] == sum(1 for t in trials if t["speed"] == sp) for sp in bs)
                  and sum(v["n"] for v in bs.values()) == e1[f]["n"], repr(bs))
            check("by_speed %s: the accuracies add up to the raw accuracy" % f,
                  abs(sum(v["acc"] * v["n"] for v in bs.values()) - e1[f]["acc_raw"] * e1[f]["n"]) < 1e-9)
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)

    if fails:
        for f in fails:
            print("FAIL: " + f)
        print("selftest failed: %d of %d checks" % (len(fails), n[0]))
        return 1
    print("selftest OK (%d checks)" % n[0])
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Closed-set simulation attack and feature-removal experiment")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--obs", nargs="+", help="standalone-logger outputs (obs/1)")
    ap.add_argument("--plan", nargs="+", help="plans (plan/1), in the same order and number as --obs")
    ap.add_argument("--corpus")
    ap.add_argument("--recipes")
    ap.add_argument("--config")
    ap.add_argument("--K", default="10,100,1000", help="numbers of candidates (comma separated)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP,
                    help="resamples of the block bootstrap behind the descriptive intervals acc_ci_block and "
                         "acc_ci_raw_block (default %d); the decision rule does not use them" % DEFAULT_BOOTSTRAP)
    ap.add_argument("--features", default=",".join(FEATURE_SETS), help="feature sets (default: all of them)")
    ap.add_argument("--markers-in-obs", action="store_true",
                    help="the presses of the block-start markers appear in the observation (default: they do not). With "
                         "this flag a block whose presses do not match the plan is left unscored and counted "
                         "(alignment in the output), and the blocks after it are found again by their markers; "
                         "without it any mismatch stops the run (exit 2)")
    ap.add_argument("--ime-toggle-in-obs", action="store_true",
                    help="the presses of the IME toggle keys (ime_on, ime_off) appear in the observation, as "
                         "category other (default: they do not)")
    ap.add_argument("--skip-scenario", action="append", default=[], metavar="S",
                    help="leave every trial of scenario S (such as S12) out of the attack, whatever its phrase; may "
                         "be given more than once (default: none). Added by deviation D8; without it the output is "
                         "that of the frozen code")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.obs and args.plan and args.corpus and args.recipes and args.config and args.out):
        ap.print_usage(sys.stderr)
        return 2
    try:
        Ks = [int(x) for x in args.K.split(",") if x]
        if not Ks or any(k < 2 for k in Ks):
            raise ValueError("--K must be comma-separated integers of at least 2")
        if args.bootstrap < 1:
            raise ValueError("--bootstrap must be an integer of at least 1")
        feats = [x for x in args.features.split(",") if x]
        bad = [x for x in feats if x not in FEATURE_SETS]
        if not feats or bad:
            raise ValueError("--features must be among %s" % "/".join(FEATURE_SETS))
        if any(re.match(r"S[1-9][0-9]*\Z", s) is None for s in args.skip_scenario):
            raise ValueError("--skip-scenario must name a scenario such as S12")
        if len(set(args.skip_scenario)) != len(args.skip_scenario):
            raise ValueError("--skip-scenario names a scenario twice")
        result = run(args.obs, args.plan, args.corpus, args.recipes, args.config, Ks, feats, args.seed,
                     args.markers_in_obs, args.ime_toggle_in_obs, args.bootstrap, tuple(args.skip_scenario))
        # --out is created fresh (never overwritten), and inside the try, so that an existing file or another
        # OSError is reported the same way as any other usage error instead of an uncaught Traceback.
        with open(args.out, "xb") as f:
            f.write(render(result))
    except (ValueError, OSError) as e:
        print("closed_set.py: %s" % e, file=sys.stderr)
        return 2
    al = result["alignment"]
    print("closed-set identification: %d trials scored (%d unscored in %d blocks), K = %s, written to %s" % (
        result["n_trials"], al["n_trials_unscored"], al["n_blocks_unscored"], ",".join(str(k) for k in Ks), args.out))
    if "skipped_scenarios" in result:
        print("left out of the attack by --skip-scenario: %s" % ", ".join(
            "%s %d" % (s, n) for s, n in sorted(result["skipped_scenarios"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
