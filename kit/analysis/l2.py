#!/usr/bin/env python3
# kit: selftest
"""Level-2 agreement: do the aggregate quantities a writing researcher would actually compute agree
between the ground truth and the observation? Uses only the Python standard library (Bland-Altman,
ICC and the cluster bootstrap are imported from stats.py and are not reimplemented here).

  l2.py --match M.json --sessions S.json --obs OBS --plan PLAN --oracle ORACLE [--config CFG.json] --out OUT.json
  l2.py --selftest      fast default (a few seconds)

Why this level exists: a per-session agreement (level 1) can be high while the remaining errors are
concentrated in one condition, and then every aggregate is biased systematically. This level
computes the aggregates from both sides and compares them.

Text (the unit of aggregation). Inside one block of the plan, in plan order, every consecutive run
of `text_size_trials` trials forms one text, so a text always has exactly that many trials. A text is
discarded, and counted, when any of its trials was not run (it is in the plan but not in the oracle
log) or was excluded (it is listed in the exclusion record of the sessions file, whatever the type);
a remainder shorter than the text size is discarded too. The counts are reported by reason
(not_run / excluded), by block and by speed. A text with both kinds of trial is counted under both
reasons. Every trial of a text must have the same speed (the plan builds blocks of one speed),
otherwise the input is rejected: a plan that mixes speeds inside a text is never handled silently.
The stratum of a text is therefore its speed (slow / mid / fast).

Measures, computed per text from both sides (a session is a conversion). They are taken over the
trials of the text that are in the scope (see "Scope"; with the scope all_trials, every trial of the
text), and the definition is the same in every scope:
  conversions_per_trial   the number of sessions of the text divided by its number of trials in scope
  mean_dwell_ms   the mean dwell of the sessions (ground truth: end - start; observation: dwell_ms)
  abandon_rate    the fraction of sessions that were cancelled (ground truth: outcome == "cancel";
                  observation: abandon)
  dwell_fraction  the total dwell divided by the scope duration of the text: the sum, over its trials
                  in scope, of the trial window (end - begin in the oracle log). The gaps between the
                  trials are not in it, so even a text with every trial in scope has a shorter
                  denominator than the span from its first trial begin to its last trial end.
A measure that cannot be computed for a text (no session, zero scope duration) is None, never 0; a
text with no trial in scope has all four measures None. Each aggregated text carries n_trials_in_scope
and scope_duration_ms, and "texts" in the output reports, per speed, the histogram of n_trials_in_scope
over the kept texts and the number of texts without a trial in scope.
An observation row whose dwell_ms is 0 does not carry a measured dwell (the observer produces it
when the clock steps backwards). Such a row is counted as a conversion and for the abandon rate, is
left out of mean_dwell_ms and dwell_fraction, and is reported as n_dwell_zero per text and in total;
in the matching it stays a zero-length interval and so counts as a false positive (as pre-registered).

Inputs. --match is match.py's output. The kept ground-truth sessions and the kept observation rows
are read from its "matching" section (so the exclusions and the clock correction that were applied
there apply here unchanged). --sessions / --obs / --plan / --oracle are the files that match.py was
run on; their SHA-256 hashes must equal the ones recorded in --match, so a matching result can never
be combined with other data. An observation row is attributed to the trial (in the oracle log) it
overlaps most, exactly as match.py attributes rows to strata; a session or row that belongs to no
trial of any kept text is counted under "outside_texts" and is not used, by reason (inside a
discarded remainder / a text discarded as not run / a text discarded as excluded / a trial of a kept
text that is out of the scope / a trial that is not in the plan / overlapping no trial) and, where the
trial is known, by speed. (The matching holds only trials in scope, so the out-of-scope reason is
normally empty; it is a guard.)

Agreement. The sign of every difference is observation minus ground truth. For each measure:
  bland_altman   bias and limits of agreement of the per-text differences
  bias_ci, loa_lo_ci, loa_hi_ci
                 95% percentile intervals for the bias and for the lower and upper limit of agreement
                 that resample the blocks (the texts of one block are one cluster; the same B and seed as
                 the ICC). [null, null] when one cannot be computed, and then <name>_status
                 (bias_ci_status, loa_lo_ci_status, loa_hi_ci_status) says why: "no_bland_altman" (the point
                 estimate is undefined: no text for the bias, fewer than two texts for the limits),
                 "fewer_than_two_blocks", or "undefined_resample" (a resample has fewer than two texts, so
                 its limits are undefined; the interval is withheld, not computed from the other
                 resamples). The bias has its own status because it is defined in every resample that has a
                 text, so it is withheld only for the first two reasons. With two or more blocks every
                 resample has at least two texts, so "undefined_resample" is a guard that cannot be reached
                 through agreement(); it is kept so that no undefined resample is ever dropped silently.
  icc21          ICC(2,1) (absolute agreement) over the texts, columns = (ground truth, observation)
  icc21_ci       a 95% percentile interval that resamples the blocks (the texts of one block are one
                 cluster); [null, null] when it cannot be computed, and then icc21_ci_status says
                 why: "no_icc" (the ICC itself is undefined), "fewer_than_two_blocks", or
                 "undefined_resample" (at least one resample has an undefined ICC; the interval is
                 then withheld rather than computed from the remaining resamples). Any other error
                 is not caught.
The agreement is reported twice: "agreement" pools the texts of every speed, and
"agreement_by_stratum" repeats it inside each speed. The stratified values are the primary
estimates (a level difference between the speeds inflates the pooled ICC); the pooled values are
kept for comparison.

Mixture. A synthetic input has no natural mixing ratio, so the primary estimates are per stratum.
"mixtures" in the configuration name weightings of the speeds; for each, the bias of a measure is
the weighted mean of the per-stratum mean differences (weights are normalised, a stratum with weight
zero is not needed, and a needed stratum without data gives null). If a bias moves between
weightings, the error is concentrated in some strata. Every mixture's bias carries a 95% percentile
interval ("bias_ci" with "bias_ci_status") from a stratified block bootstrap: within each speed, that
speed's blocks (a block holds one speed) are resampled independently; in one replicate every
stratum's bias is recomputed and each mixture is formed from that replicate with its weights, so a
mixture that puts all weight on one speed has the percentile interval of that speed's replicate
biases. The per-speed bias has one registered interval, the one in agreement_by_stratum; "mixture_bias"
gives the per-speed bias and n without an interval of its own. The mixture interval is withheld
([null, null]) with the status "no_data" (the point estimate is null), "fewer_than_two_blocks" (a
needed stratum has fewer than two blocks), or "undefined_resample" (in some replicate a needed
stratum has no text with the measure). Only random() draws of Random(seed) are used, as in the ICC.
A text mixes the scenarios of its block in the plan's order; the plan orders the trials by the seed and
does not balance them within a text, so the scenarios of a block are mixed about equally only in
expectation, and every per-text value and ICC holds for that mix of scenarios only.

Per trial ("per_trial"). So that a reader can reweight the scenarios to another mix, three per-trial
quantities are also given on both sides, by scenario and speed: the number of conversions, the summed
conversion time (ground truth: end - start; observation: dwell_ms) and the number of cancelled
conversions. Per stratum: the number of trials and of blocks, the mean on each side, and the mean,
standard deviation and block-clustered 95% percentile bootstrap interval of the per-trial difference
(observation minus ground truth), and the number of observation rows with dwell_ms == 0 (they add 0
to the summed time). Every (scenario, speed) layer of the plan has a row. A layer with no trial used
has "status": "no_trial", n_trials, n_blocks and n_dwell_zero 0 and null for each of the three
quantities (never 0: a quantity that cannot be computed is null); every row ("status": "ok" or
"no_trial") also gives "trials_not_used", the number of the layer's trials that were not used by
reason (not_run / excluded / out_of_scope, and outside_layer when a list of trials is given), so the
reason a layer is empty is in the row. A trial is used when it is in the plan, was run, is not
excluded and is in the scope; a trial with no session or row on a side counts 0 there. Sessions and rows are attributed to
trials as for the texts. The per-trial differences include contamination by sessions carried over from
an earlier trial (a session that began in one trial and is seen in the next). Two restricted views
repeat the quantities over a list of trials of the match result's scope (null when the list is null,
also when the key is missing in an older result); a trial that is otherwise used but is not in the
list is counted in trials_not_used["outside_layer"] and its sessions and rows count as outside.
"per_trial_uncontaminated" uses scope.uncontaminated_trials, the trials in neither the carried_out nor
the received_carry layer of the prediction; the closing-key trials stay in it, because their
disagreement is the trial's own and not a contamination from another trial. The reweighting to
another mix of scenarios is meant for that view. "per_trial_layer_none" uses scope.layer_none_trials,
the trials in none of the prediction's layers (the closing-key trials are out); it is a diagnostic.

Scope. The match result names its scope (match.py --scope; all_trials when it has none). Its
matching holds only the sessions and rows of the trials in the scope, so under the scope
under_limit, which sets aside the trials whose presses went into a session that the observer is
specified to cut off, the texts are kept whole and the measures of a text are computed over its
trials in the scope (see "Measures": per text, the same definition in every scope, so the values
do not scale with how many of its trials remain); per_trial leaves the set-aside trials out.
scope.layer_none_trials and scope.uncontaminated_trials (each a list of trial ids, or null or
missing) must be a list of strings when present and not null, otherwise the input is rejected.

Configuration (JSON, every key optional): {"text_size_trials": 8, "B": 2000, "seed": 1,
"mixtures": {"<name>": {"slow": w, "mid": w, "fast": w}}}. Keys starting with "_" are ignored. B is
at most 100000; the weights must be finite and their sum must be finite and positive.

The output never includes a path, and identical input always yields identical output bytes.
Exit status: 0 on success; 2 if an input cannot be read, is malformed or usage is incorrect.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import gt_sessions  # noqa: E402  the oracle parser and InputError
import match  # noqa: E402  the interval geometry, trial attribution and file helpers
import stats  # noqa: E402  Bland-Altman, ICC and the cluster bootstrap live there

InputError = gt_sessions.InputError
SPEEDS = match.SPEEDS
MAX_B = 100000
MEASURES = ("mean_dwell_ms", "conversions_per_trial", "abandon_rate", "dwell_fraction")
DEFAULT_MIXTURES = {
    "slow_heavy": {"slow": 0.6, "mid": 0.3, "fast": 0.1},
    "uniform": {"slow": 1, "mid": 1, "fast": 1},
    "fast_heavy": {"slow": 0.1, "mid": 0.3, "fast": 0.6},
}


def _is_int(v):
    return type(v) is int


# ---------------------------------------------------------------- texts

def text_layout(plan, oracle, text_size, excluded_trials=None, out_of_scope=None):
    """Bundle the trials of the plan into texts. plan is match.parse_plan_strata's output, oracle is
    gt_sessions.parse_oracle's output, excluded_trials is a collection of trial ids that were excluded.

    Returns {"texts": [...], "dropped_trials": {block_id: n}, "discarded_texts": [...],
    "trial_status": {trial_id: status}}. Each kept text is {"text_id", "block_id", "speed",
    "trial_ids", "begin_wall_ms", "end_wall_ms", "duration_ms", "trials_in_scope", "n_trials_in_scope",
    "scope_duration_ms"} and has exactly text_size trials, all of them run and none excluded.
    trials_in_scope are its trials that are not in out_of_scope (a collection of trial ids, default
    none) and scope_duration_ms is the sum of their oracle windows (end - begin); duration_ms is the
    span from the first trial begin to the last trial end. Texts follow the plan order inside a block; the blocks are visited
    in the order of their first trial. dropped_trials is the size of the remainder of each block.
    A text with a trial that is not in the oracle log (not run) or is in excluded_trials is
    discarded and appears in discarded_texts as {"text_id", "block_id", "speed", "reasons"} with the
    reasons a subset of ["not_run", "excluded"]. trial_status maps every planned trial that is not in
    a kept text to "remainder", "not_run" or "excluded" (a text with both reasons is "not_run").
    Raises InputError when a text mixes speeds and ValueError when text_size is not a positive
    integer."""
    if not _is_int(text_size) or text_size < 1:
        raise ValueError("text_size_trials must be an integer of at least 1")
    excluded = set(excluded_trials or ())
    out_scope = set(out_of_scope or ())
    windows = dict((t["trial_id"], t) for t in oracle["trials"])
    by_block = {}
    for tid, info in plan["trials"].items():
        by_block.setdefault(info["block_id"], []).append(tid)
    texts, dropped, discarded, status = [], {}, [], {}
    for block_id, tids in by_block.items():
        n_full = len(tids) // text_size
        dropped[block_id] = len(tids) - n_full * text_size
        for t in tids[n_full * text_size:]:
            status[t] = "remainder"
        for k in range(n_full):
            chunk = tids[k * text_size:(k + 1) * text_size]
            speeds = sorted(set(plan["trials"][t]["speed"] for t in chunk))
            if len(speeds) != 1:
                raise InputError("plan: block %s text %d mixes speeds (%s); a text must have one speed" % (
                    block_id, k, ", ".join(speeds)))
            reasons = []
            if any(t not in windows for t in chunk):
                reasons.append("not_run")
            if any(t in excluded for t in chunk):
                reasons.append("excluded")
            text_id = "b%s.%d" % (block_id, k)
            if reasons:
                discarded.append({"text_id": text_id, "block_id": block_id, "speed": speeds[0], "reasons": reasons})
                for t in chunk:
                    status[t] = reasons[0]
                continue
            begin = min(windows[t]["begin_wall_ms"] for t in chunk)
            end = max(windows[t]["end_wall_ms"] for t in chunk)
            in_scope = [t for t in chunk if t not in out_scope]
            texts.append({"text_id": text_id, "block_id": block_id, "speed": speeds[0],
                          "trial_ids": chunk, "begin_wall_ms": begin, "end_wall_ms": end,
                          "duration_ms": end - begin, "trials_in_scope": in_scope,
                          "n_trials_in_scope": len(in_scope),
                          "scope_duration_ms": sum(windows[t]["end_wall_ms"] - windows[t]["begin_wall_ms"]
                                                   for t in in_scope)})
    return {"texts": texts, "dropped_trials": dropped, "discarded_texts": discarded, "trial_status": status}


def texts(plan, oracle, text_size):
    """The list of texts of text_layout() (the dropped remainder is reported by text_layout)."""
    return text_layout(plan, oracle, text_size)["texts"]


def attach_trials(obs_rows, oracle, obs_end_offset_ms=0):
    """Copies of the observation rows with a "trial_id" (None when the row overlaps no trial). The
    trial is the one with the greatest overlap, as in match.py's stratum assignment."""
    trials = sorted(oracle["trials"], key=lambda t: (t["begin_wall_ms"], t["end_wall_ms"]))
    begins = [t["begin_wall_ms"] for t in trials]
    out = []
    for i, r in enumerate(obs_rows):
        lo, hi = match.obs_interval(r, obs_end_offset_ms, "obs_rows[%d]" % i)
        out.append(dict(r, trial_id=match._assign_trial(lo, hi, trials, begins)))
    return out


# ---------------------------------------------------------------- aggregation

def aggregate(texts, sessions, obs_rows, side, obs_end_offset_ms=0):
    """The four measures for every text, from one side. side is "gt" (sessions are ground-truth
    sessions, obs_rows is ignored) or "obs" (obs_rows are observation rows carrying a "trial_id",
    see attach_trials; sessions is ignored). Returns {"texts": [...], "n_outside_texts": n}, the
    texts in the order given (text_layout's texts, with their trials_in_scope and scope_duration_ms),
    each {"text_id", "block_id", "speed", "duration_ms", "n_trials_in_scope", "scope_duration_ms",
    <MEASURES>} (an observation text also has "n_dwell_zero"). The measures cover the trials of the
    text in scope (see "Measures" in the module docstring): conversions_per_trial is the number of
    sessions over n_trials_in_scope and dwell_fraction the total dwell over scope_duration_ms; a text
    with no trial in scope has all four None. A record whose trial belongs to no text, or to a trial of
    a text that is not in scope, is counted in n_outside_texts and not used; "outside_trials" gives
    those counts per trial id (None: no trial).
    An observation row with dwell_ms == 0 is counted as a conversion and for the abandon rate but
    not in mean_dwell_ms or dwell_fraction (its dwell is not a measurement); n_dwell_zero counts them.
    obs_end_offset_ms is accepted for symmetry with the pipeline and does not change the measures
    (the dwell of an observation is its dwell_ms)."""
    if side == "gt":
        records = list(sessions)
    elif side == "obs":
        records = list(obs_rows)
    else:
        raise ValueError('side must be "gt" or "obs"')
    index = {}
    for i, t in enumerate(texts):
        for tid in t["trials_in_scope"]:
            index[tid] = i
    dwells = [[] for _ in texts]
    cancels = [0] * len(texts)
    zeros = [0] * len(texts)
    outside, outside_trials = 0, {}
    for n, r in enumerate(records):
        i = index.get(r.get("trial_id"))
        if i is None:
            outside += 1
            outside_trials[r.get("trial_id")] = outside_trials.get(r.get("trial_id"), 0) + 1
            continue
        if side == "gt":
            lo, hi = match.gt_interval(r, "sessions[%d]" % n)
            dwell, cancelled = hi - lo, r.get("outcome") == "cancel"
            if r.get("outcome") not in ("commit", "cancel"):
                raise ValueError("sessions[%d]: outcome must be commit or cancel here" % n)
        else:
            dwell, cancelled = r.get("dwell_ms"), r.get("abandon")
            if not _is_int(dwell) or dwell < 0 or type(cancelled) is not bool:
                raise ValueError("obs_rows[%d]: dwell_ms / abandon has the wrong type" % n)
            if dwell == 0:
                zeros[i] += 1
        dwells[i].append(dwell)
        cancels[i] += 1 if cancelled else 0
    out = []
    for i, t in enumerate(texts):
        n = len(dwells[i])
        measured = n - zeros[i]
        total = sum(dwells[i])
        nt, span = t["n_trials_in_scope"], t["scope_duration_ms"]
        row = {
            "text_id": t["text_id"], "block_id": t["block_id"], "speed": t["speed"],
            "duration_ms": t["duration_ms"], "n_trials_in_scope": nt, "scope_duration_ms": span,
            "conversions_per_trial": n / nt if nt else None,
            "mean_dwell_ms": total / measured if (nt and measured) else None,
            "abandon_rate": cancels[i] / n if (nt and n) else None,
            "dwell_fraction": (total / span if (measured or not n) else None) if (nt and span > 0) else None,
        }
        if side == "obs":
            row["n_dwell_zero"] = zeros[i]
        out.append(row)
    return {"texts": out, "n_outside_texts": outside, "outside_trials": outside_trials}


def _check_aligned(gt_agg, obs_agg):
    g = [t["text_id"] for t in gt_agg["texts"]]
    o = [t["text_id"] for t in obs_agg["texts"]]
    if g != o:
        raise ValueError("the ground-truth and observation aggregates are not over the same texts")


# ---------------------------------------------------------------- agreement

class _UndefinedICC(Exception):
    """Raised inside a resample whose ICC is undefined (not a ValueError, so no other error is hidden)."""


def _icc_interval(rows, B, seed):
    """A block-level bootstrap interval for ICC(2,1) as (lo, hi, status). status is "ok",
    "fewer_than_two_blocks" or "undefined_resample"; the interval is (None, None) unless it is "ok".
    One undefined resample withholds the interval: it is not computed from the other resamples."""
    blocks = {}
    for block_id, g, o in rows:
        blocks.setdefault(block_id, []).append((g, o))
    if len(blocks) < 2:
        return (None, None, "fewer_than_two_blocks")
    clusters = [blocks[b] for b in sorted(blocks)]

    def stat(sample):
        matrix = [[g, o] for c in sample for (g, o) in c]
        v = stats.icc(matrix, "2,1") if len(matrix) >= 2 else None
        if v is None:
            raise _UndefinedICC()
        return v

    try:
        lo, hi = stats.cluster_bootstrap(clusters, stat, B, seed)
    except _UndefinedICC:
        return (None, None, "undefined_resample")
    return (lo, hi, "ok")


def _check_b_seed(B, seed):
    if not _is_int(B) or B < 1 or B > MAX_B:
        raise ValueError("B must be an integer from 1 to %d" % MAX_B)
    if not _is_int(seed):
        raise ValueError("seed must be an integer")


def _ba_stat(kind):
    """The statistic of a resample of blocks (clusters of per-text differences): the bias, or a limit of
    agreement (bias -/+ BA_Z * sd). A resample with fewer than two texts has no limits."""
    def stat(sample):
        vals = [v for c in sample for v in c]
        if kind == "bias":
            if not vals:
                raise _UndefinedICC()
            return statistics.fmean(vals)
        if len(vals) < 2:
            raise _UndefinedICC()
        sign = -1 if kind == "loa_lo" else 1
        return statistics.fmean(vals) + sign * stats.BA_Z * statistics.stdev(vals)
    return stat


def _ba_interval(rows, kind, point, B, seed):
    """A block-level bootstrap interval for the bias ("bias") or a limit of agreement ("loa_lo" /
    "loa_hi") of the per-text differences (observation minus ground truth) as (lo, hi, status). rows
    are (block, gt, obs). status is "no_bland_altman" (the point estimate is None), "fewer_than_two_blocks",
    "undefined_resample" (a resample has no limits because it holds fewer than two texts; the interval is
    withheld) or "ok"; the interval is (None, None) unless it is "ok"."""
    if point is None:
        return (None, None, "no_bland_altman")
    blocks = {}
    for block_id, g, o in rows:
        blocks.setdefault(block_id, []).append(o - g)
    if len(blocks) < 2:
        return (None, None, "fewer_than_two_blocks")
    clusters = [blocks[b] for b in sorted(blocks)]
    try:
        lo, hi = stats.cluster_bootstrap(clusters, _ba_stat(kind), B, seed)
    except _UndefinedICC:
        return (None, None, "undefined_resample")
    return (lo, hi, "ok")


def _agree(rows, B, seed):
    ba = stats.bland_altman([o for _b, _g, o in rows], [g for _b, g, _o in rows])
    bias_lo, bias_hi, bias_status = _ba_interval(rows, "bias", ba["bias"], B, seed)
    lo_lo, lo_hi, lo_status = _ba_interval(rows, "loa_lo", ba["loa_lo"], B, seed)
    hi_lo, hi_hi, hi_status = _ba_interval(rows, "loa_hi", ba["loa_hi"], B, seed)
    value, ci, status = None, (None, None), "no_icc"
    if len(rows) >= 2:
        value = stats.icc([[g, o] for _b, g, o in rows], "2,1")
        if value is not None:
            lo, hi, status = _icc_interval(rows, B, seed)
            ci = (lo, hi)
    return {"n": len(rows), "bland_altman": ba,
            "bias_ci": [bias_lo, bias_hi], "bias_ci_status": bias_status,
            "loa_lo_ci": [lo_lo, lo_hi], "loa_lo_ci_status": lo_status,
            "loa_hi_ci": [hi_lo, hi_hi], "loa_hi_ci_status": hi_status,
            "icc21": value, "icc21_ci": [ci[0], ci[1]], "icc21_ci_status": status}


def agreement(gt_agg, obs_agg, B, seed):
    """{measure: {"n", "bland_altman", "bias_ci", "bias_ci_status", "loa_lo_ci", "loa_lo_ci_status",
    "loa_hi_ci", "loa_hi_ci_status", "icc21", "icc21_ci", "icc21_ci_status"}} over the texts where
    both sides have the measure. The difference is observation minus ground truth. icc21 is None
    when it cannot be computed (fewer than two texts, or no variation at all); the intervals are
    [None, None] with a status when they cannot be computed (see "Agreement" in the module docstring)."""
    _check_b_seed(B, seed)
    _check_aligned(gt_agg, obs_agg)
    out = {}
    for m in MEASURES:
        rows = [(g["block_id"], g[m], o[m]) for g, o in zip(gt_agg["texts"], obs_agg["texts"])
                if g[m] is not None and o[m] is not None]
        out[m] = _agree(rows, B, seed)
    return out


def agreement_by_stratum(gt_agg, obs_agg, B, seed):
    """{speed: agreement()'s value computed over the texts of that speed} for the speeds that have
    texts (the primary estimates: no level difference between the speeds enters the ICC)."""
    _check_b_seed(B, seed)
    _check_aligned(gt_agg, obs_agg)
    out = {}
    for s in SPEEDS:
        keep = [i for i, t in enumerate(gt_agg["texts"]) if t["speed"] == s]
        if not keep:
            continue
        sub = lambda agg: {"texts": [agg["texts"][i] for i in keep], "n_outside_texts": 0}
        out[s] = agreement(sub(gt_agg), sub(obs_agg), B, seed)
    return out


# ---------------------------------------------------------------- mixtures

def stratify(gt_agg, obs_agg):
    """{speed: [{"text_id", "block_id", "gt": {measure: v}, "obs": {measure: v}}]} for the speeds that
    have texts."""
    _check_aligned(gt_agg, obs_agg)
    out = {}
    for g, o in zip(gt_agg["texts"], obs_agg["texts"]):
        out.setdefault(g["speed"], []).append({
            "text_id": g["text_id"], "block_id": g["block_id"],
            "gt": dict((m, g[m]) for m in MEASURES),
            "obs": dict((m, o[m]) for m in MEASURES)})
    return out


def _norm_weights(w, name):
    if not isinstance(w, dict) or not w:
        raise ValueError("mixture %s: the weights must be a non-empty object" % name)
    for k, v in w.items():
        if k not in SPEEDS:
            raise ValueError("mixture %s: unknown stratum %r (expected %s)" % (name, k, ", ".join(SPEEDS)))
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("mixture %s: a weight must be a finite non-negative number" % name)
        try:
            fv = float(v)
        except OverflowError:
            raise ValueError("mixture %s: a weight must be a finite non-negative number" % name)
        if not math.isfinite(fv) or fv < 0:
            raise ValueError("mixture %s: a weight must be a finite non-negative number" % name)
    total = sum(float(w.get(s, 0)) for s in SPEEDS)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("mixture %s: the weights must have a finite positive sum" % name)
    return dict((s, float(w.get(s, 0)) / total) for s in SPEEDS)


def _mix_total(w, get):
    """The normalised-weight sum of the strata values get(speed), None when a stratum with positive
    weight has none."""
    total = 0.0
    for s in SPEEDS:
        if w[s] == 0:
            continue
        b = get(s)
        if b is None:
            return None
        total += w[s] * b
    return total


def _stratified_replicates(texts_by_stratum, B, seed):
    """B replicates of a stratified block bootstrap: within each speed, that speed's blocks are
    resampled with replacement independently of the other speeds (a block holds one speed). Returns
    ({speed: [blocks]}, [{speed: {measure: bias or None}}]) with the replicates in draw order. Only
    Random(seed).random() is used (the draw is the one of stats.cluster_bootstrap), and the speeds
    are visited in SPEEDS order, so the result is deterministic for a fixed B and seed. A stratum's
    bias is None in a replicate where none of its resampled texts has the measure."""
    blocks = {}
    for s in SPEEDS:
        by_block = {}
        for t in texts_by_stratum.get(s, ()):
            d = by_block.setdefault(t["block_id"], dict((m, []) for m in MEASURES))
            for m in MEASURES:
                if t["obs"][m] is not None and t["gt"][m] is not None:
                    d[m].append(t["obs"][m] - t["gt"][m])
        if by_block:
            blocks[s] = [by_block[b] for b in sorted(by_block, key=str)]
    rng = random.Random(seed)
    reps = []
    for _ in range(B):
        rep = {}
        for s in SPEEDS:
            if s not in blocks:
                continue
            k = len(blocks[s])
            sample = [blocks[s][min(k - 1, int(rng.random() * k))] for _ in range(k)]
            rep[s] = {}
            for m in MEASURES:
                vals = [v for c in sample for v in c[m]]
                rep[s][m] = statistics.fmean(vals) if vals else None
        reps.append(rep)
    return blocks, reps


def _percentile_interval(values, conf=0.95):
    values = sorted(values)
    a = (1.0 - conf) / 2.0
    return [stats._percentile(values, a), stats._percentile(values, 1.0 - a)]


def _ci_status(point, n_blocks, values):
    """(interval, status) of one quantity: "no_data" when the point estimate is None,
    "fewer_than_two_blocks" when a needed stratum has fewer than two blocks, "undefined_resample" when a
    replicate is undefined (the interval is withheld, not computed from the rest), else "ok"."""
    if point is None:
        return [None, None], "no_data"
    if n_blocks < 2:
        return [None, None], "fewer_than_two_blocks"
    if any(v is None for v in values):
        return [None, None], "undefined_resample"
    return _percentile_interval(values), "ok"


def mixture_bias(texts_by_stratum, mixtures, B=None, seed=None):
    """The bias (observation minus ground truth) of every measure under each weighting of the strata.

    texts_by_stratum is stratify()'s output; mixtures is {name: {stratum: weight}}. Returns
    {"by_stratum": {speed: {measure: {"bias", "n"}}}, "mixtures": {name:
    {"weights", "bias": {measure: value}, "bias_ci": {measure: [lo, hi]}, "bias_ci_status": {measure:
    status}}}}. A stratum's bias is the mean per-text difference over the texts where both sides have
    the measure. A mixture's bias is the normalised-weight sum of the strata biases; it is None when a
    stratum with positive weight has no data for the measure.

    The per-stratum entries carry no interval: the registered interval of a per-speed bias is the one
    in agreement_by_stratum. The mixture intervals (given B and seed; otherwise they are [None, None]
    with the status "not_requested") are 95% percentile intervals of a stratified block bootstrap: within each speed that speed's blocks
    are resampled independently, and in one replicate every stratum's bias is recomputed and the
    mixtures are formed from the same replicate with the same weights. The status is "no_data" (the
    point estimate is None), "fewer_than_two_blocks" (a stratum that is needed has fewer than two
    blocks; for a mixture, only the strata with positive weight are needed), "undefined_resample" (in
    some replicate a needed stratum has no text with the measure; the interval is withheld) or "ok".
    The interval of a mixture with the weight 1 on one speed is the percentile interval of that
    speed's replicate biases."""
    for s in texts_by_stratum:
        if s not in SPEEDS:
            raise ValueError("unknown stratum %r" % (s,))
    boot = B is not None
    if boot:
        _check_b_seed(B, seed)
    weights = dict((name, _norm_weights(mixtures[name], name)) for name in sorted(mixtures))
    blocks, reps = _stratified_replicates(texts_by_stratum, B, seed) if boot else ({}, [])
    by_stratum = {}
    for s in SPEEDS:
        if s not in texts_by_stratum:
            continue
        by_stratum[s] = {}
        for m in MEASURES:
            diffs = [t["obs"][m] - t["gt"][m] for t in texts_by_stratum[s]
                     if t["obs"][m] is not None and t["gt"][m] is not None]
            by_stratum[s][m] = {"bias": statistics.fmean(diffs) if diffs else None, "n": len(diffs)}
    out = {}
    for name, w in weights.items():
        bias, cis, statuses = {}, {}, {}
        for m in MEASURES:
            bias[m] = _mix_total(w, lambda s: by_stratum.get(s, {}).get(m, {}).get("bias"))
            if not boot:
                cis[m], statuses[m] = [None, None], "not_requested"
                continue
            needed = [s for s in SPEEDS if w[s] != 0]
            # only the blocks that hold a value of this measure count (as in _ba_interval)
            thin = min((sum(1 for b in blocks.get(s, ()) if b[m]) for s in needed), default=0)
            values = [_mix_total(w, lambda s: r[s][m] if s in r else None) for r in reps]
            cis[m], statuses[m] = _ci_status(bias[m], thin, values)
        out[name] = {"weights": w, "bias": bias, "bias_ci": cis, "bias_ci_status": statuses}
    return {"by_stratum": by_stratum, "mixtures": out}


# ---------------------------------------------------------------- per trial

PER_TRIAL = ("n_conversions", "dwell_sum_ms", "n_cancelled")


def _scenario_key(sc):
    return (int(sc[1:]) if isinstance(sc, str) and sc[1:].isdigit() else 0, str(sc))


def per_trial(plan, oracle, gt, obs, excluded_trials, out_of_scope, B, seed, only_trials=None):
    """The per-trial quantities of PER_TRIAL on both sides, by (scenario, speed). gt: ground-truth
    sessions; obs: observation rows carrying a "trial_id" (attach_trials). Returns {"n_trials",
    "trials_not_used": {"not_run", "excluded", "out_of_scope"}, "n_outside": {"gt", "obs"},
    "by_stratum": [...]}, one row per (scenario, speed) of the plan, with "status" ("ok", or "no_trial"
    when no trial of the layer is used), the layer's own "trials_not_used" and, for a "no_trial" row,
    null for each quantity of PER_TRIAL; see "Per trial" in the module docstring. only_trials (a collection of trial
    ids, default None: no restriction) keeps only those trials: a trial that would otherwise be used
    but is not in it is counted under trials_not_used["outside_layer"] (a key that is present only
    when only_trials is given), and its sessions and rows count as outside."""
    _check_b_seed(B, seed)
    run = set(t["trial_id"] for t in oracle["trials"])
    excluded, out_scope = set(excluded_trials), set(out_of_scope)
    only = None if only_trials is None else set(only_trials)
    not_used = {"not_run": 0, "excluded": 0, "out_of_scope": 0}
    if only is not None:
        not_used["outside_layer"] = 0
    acc = {}
    layer_not_used = {}
    for tid, info in plan["trials"].items():
        lnu = layer_not_used.setdefault((info["scenario"], info["speed"]), dict.fromkeys(not_used, 0))
        reason = None
        if tid not in run:
            reason = "not_run"
        elif tid in excluded:
            reason = "excluded"
        elif tid in out_scope:
            reason = "out_of_scope"
        elif only is not None and tid not in only:
            reason = "outside_layer"
        if reason is None:
            acc[tid] = {"gt": [0, 0, 0], "obs": [0, 0, 0], "zero": 0}
        else:
            not_used[reason] += 1
            lnu[reason] += 1
    outside = {"gt": 0, "obs": 0}
    for n, s in enumerate(gt):
        a = acc.get(s.get("trial_id"))
        if a is None:
            outside["gt"] += 1
            continue
        lo, hi = match.gt_interval(s, "sessions[%d]" % n)
        a["gt"][0] += 1
        a["gt"][1] += hi - lo
        a["gt"][2] += 1 if s.get("outcome") == "cancel" else 0
    for n, r in enumerate(obs):
        a = acc.get(r.get("trial_id"))
        if a is None:
            outside["obs"] += 1
            continue
        d, ab = r.get("dwell_ms"), r.get("abandon")
        if not _is_int(d) or d < 0 or type(ab) is not bool:
            raise ValueError("obs_rows[%d]: dwell_ms / abandon has the wrong type" % n)
        a["obs"][0] += 1
        a["obs"][1] += d
        a["obs"][2] += 1 if ab else 0
        a["zero"] += 1 if d == 0 else 0
    groups = {}
    for tid, a in acc.items():
        info = plan["trials"][tid]
        groups.setdefault((info["scenario"], info["speed"]), []).append((info["block_id"], a))
    out = []
    for sc, sp in sorted(layer_not_used, key=lambda k: (_scenario_key(k[0]), SPEEDS.index(k[1]) if k[1] in SPEEDS else 9)):
        items = groups.get((sc, sp), [])
        blocks = sorted(set(b for b, _a in items), key=str)
        row = {"stratum": {"scenario": sc, "speed": sp}, "status": "ok" if items else "no_trial",
               "n_trials": len(items), "n_blocks": len(blocks),
               "n_dwell_zero": sum(a["zero"] for _b, a in items), "trials_not_used": layer_not_used[(sc, sp)]}
        if not items:
            for q in PER_TRIAL:
                row[q] = None
            out.append(row)
            continue
        for i, q in enumerate(PER_TRIAL):
            diffs = [a["obs"][i] - a["gt"][i] for _b, a in items]
            by_block = {}
            for b, a in items:
                by_block.setdefault(b, []).append(a["obs"][i] - a["gt"][i])
            lo, hi = stats.cluster_bootstrap([by_block[b] for b in blocks],
                                             lambda smp: statistics.fmean(v for c in smp for v in c), B, seed)
            row[q] = {"gt_mean": statistics.fmean(a["gt"][i] for _b, a in items),
                      "obs_mean": statistics.fmean(a["obs"][i] for _b, a in items),
                      "diff_mean": statistics.fmean(diffs),
                      "diff_sd": statistics.stdev(diffs) if len(diffs) >= 2 else None,
                      "diff_ci": [lo, hi],
                      "diff_ci_status": "ok" if len(blocks) >= 2 else "fewer_than_two_blocks"}
        out.append(row)
    return {"n_trials": len(acc), "trials_not_used": not_used, "n_outside": outside, "by_stratum": out}


# ---------------------------------------------------------------- pipeline

def parse_config(data):
    """Parse the configuration JSON (defaults if None) into {"text_size_trials", "B", "seed", "mixtures"}."""
    cfg = {"text_size_trials": 8, "B": 2000, "seed": 1, "mixtures": dict(DEFAULT_MIXTURES)}
    if data is None:
        return cfg
    obj = match._json_loads(match._decode(data, "config"), "config", True)
    if not isinstance(obj, dict):
        raise InputError("config: the top level must be an object")
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        if k in ("text_size_trials", "B"):
            if not _is_int(v) or v < 1:
                raise InputError("config: %s must be an integer of at least 1" % k)
            if k == "B" and v > MAX_B:
                raise InputError("config: B must be at most %d" % MAX_B)
            cfg[k] = v
        elif k == "seed":
            if not _is_int(v):
                raise InputError("config: seed must be an integer")
            cfg[k] = v
        elif k == "mixtures":
            if not isinstance(v, dict) or not v:
                raise InputError("config: mixtures must be a non-empty object")
            for name, w in v.items():
                try:
                    _norm_weights(w, name)
                except ValueError as e:
                    raise InputError("config: %s" % e)
            cfg[k] = v
        else:
            raise InputError("config: unknown key '%s'" % k)
    return cfg


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_obj(v):
    return isinstance(v, dict)


def _check_matching(m):
    """Reject a match result whose records do not have the expected shape (InputError, never a crash)."""
    ok = all(_is_obj(p) and _is_obj(p.get("gt")) and _is_obj(p.get("obs")) for p in m["pairs"]) \
        and all(_is_obj(p) and _is_obj(p.get("gt")) and isinstance(p.get("obs"), list) and all(map(_is_obj, p["obs"]))
                for p in m["split"]) \
        and all(_is_obj(p) and _is_obj(p.get("obs")) and isinstance(p.get("gt"), list) and all(map(_is_obj, p["gt"]))
                for p in m["merge"]) \
        and all(map(_is_obj, m["miss"])) and all(map(_is_obj, m["false_pos"]))
    if not ok:
        raise InputError("match: an element of pairs / split / merge / miss / false_pos has the wrong shape")


def _outside_report(agg, status, plan_trials, out_scope):
    """Count the records that fall outside the kept texts or outside the scope, by reason and by speed."""
    by_reason, by_speed = {}, {}
    for tid, n in agg["outside_trials"].items():
        if tid is None:
            reason = "no_trial"
        elif tid in status:
            reason = {"remainder": "discarded_remainder", "not_run": "discarded_text_not_run",
                      "excluded": "discarded_text_excluded"}[status[tid]]
        elif tid in out_scope and tid in plan_trials:
            reason = "out_of_scope"
        else:
            reason = "trial_not_in_plan"
        by_reason[reason] = by_reason.get(reason, 0) + n
        if tid in plan_trials:
            sp = plan_trials[tid]["speed"]
            by_speed[sp] = by_speed.get(sp, 0) + n
    return {"n": agg["n_outside_texts"], "by_reason": dict(sorted(by_reason.items())),
            "by_speed": dict((s, by_speed[s]) for s in SPEEDS if s in by_speed)}


def evaluate(match_bytes, sessions_bytes, obs_bytes, plan_bytes, oracle_bytes, config_bytes=None):
    """The CLI's core logic (bytes in, an output dict out). Identical input always yields an
    identical dict."""
    cfg = parse_config(config_bytes)
    mo = match._json_loads(match._decode(match_bytes, "match"), "match", True)
    need = isinstance(mo, dict) and isinstance(mo.get("matching"), dict) and isinstance(mo.get("inputs_sha256"), dict) \
        and isinstance(mo.get("config"), dict) and _is_int(mo["config"].get("obs_end_offset_ms")) \
        and all(isinstance(mo["matching"].get(k), list) for k in ("pairs", "split", "merge", "miss", "false_pos"))
    if not need:
        raise InputError("match: not match.py's output (missing matching / inputs_sha256 / config)")
    definition = mo.get("definition")
    if definition not in match.DEFINITIONS:
        raise InputError("match: definition must be one of %s" % ", ".join(match.DEFINITIONS))
    _check_matching(mo["matching"])
    for name, data in (("sessions", sessions_bytes), ("obs", obs_bytes), ("plan", plan_bytes), ("oracle", oracle_bytes)):
        if mo["inputs_sha256"].get(name) != _sha(data):
            raise InputError("%s: SHA-256 does not match the one recorded in the match result (another data set)" % name)
    plan = match.parse_plan_strata(plan_bytes)
    oracle = gt_sessions.parse_oracle(oracle_bytes)
    want = oracle["plan_sha256"]
    if not isinstance(want, str) or want.lower() != plan["sha256"]:
        raise InputError("plan: SHA-256 does not match the oracle header's plan_sha256 (unrelated plan)")
    _src, _sess, exclusions = match.parse_sessions(sessions_bytes, definition)
    excluded_trials = set(t for e in exclusions for t in e["trial_ids"] if isinstance(t, str))
    sc = mo.get("scope") if isinstance(mo.get("scope"), dict) else {"name": "all_trials", "long_trials": None}
    scope, long_trials = sc.get("name"), sc.get("long_trials")
    if scope not in match.SCOPES:
        raise InputError("match: scope.name must be one of %s" % ", ".join(match.SCOPES))
    if scope != "all_trials" and not (isinstance(long_trials, list) and all(isinstance(t, str) for t in long_trials)):
        raise InputError("match: scope %s needs the list scope.long_trials" % scope)
    layer_none = sc.get("layer_none_trials")  # missing (an older match result) is the same as null
    if layer_none is not None and not (isinstance(layer_none, list) and all(isinstance(t, str) for t in layer_none)):
        raise InputError("match: scope.layer_none_trials must be a list of trial ids or null")
    uncontaminated = sc.get("uncontaminated_trials")  # likewise
    if uncontaminated is not None and not (isinstance(uncontaminated, list) and
                                           all(isinstance(t, str) for t in uncontaminated)):
        raise InputError("match: scope.uncontaminated_trials must be a list of trial ids or null")
    long_set = set(long_trials or ())
    out_of_scope = set() if scope == "all_trials" else \
        set(t for t in plan["trials"] if (t in long_set) != (scope == "over_limit"))

    m = mo["matching"]
    # The observation side is every row of the match (pairs, split, merge and false positives); the
    # ground-truth side is every session of it (pairs, split, merge and misses).
    gt = [p["gt"] for p in m["pairs"]] + [p["gt"] for p in m["split"]] + \
        [g for p in m["merge"] for g in p["gt"]] + list(m["miss"])
    obs = [p["obs"] for p in m["pairs"]] + [o for p in m["split"] for o in p["obs"]] + \
        [p["obs"] for p in m["merge"]] + list(m["false_pos"])
    off = mo["config"]["obs_end_offset_ms"]
    obs = attach_trials(obs, oracle, off)

    layout = text_layout(plan, oracle, cfg["text_size_trials"], excluded_trials, out_of_scope)
    tx = layout["texts"]
    gt_agg = aggregate(tx, gt, None, "gt")
    obs_agg = aggregate(tx, None, obs, "obs")
    by_speed, in_scope_hist, no_scope = {}, {}, {}
    for t in tx:
        by_speed[t["speed"]] = by_speed.get(t["speed"], 0) + 1
        h = in_scope_hist.setdefault(t["speed"], {})
        h[t["n_trials_in_scope"]] = h.get(t["n_trials_in_scope"], 0) + 1
        no_scope[t["speed"]] = no_scope.get(t["speed"], 0) + (1 if t["n_trials_in_scope"] == 0 else 0)
    disc = layout["discarded_texts"]
    disc_reason, disc_block, disc_speed = {}, {}, {}
    for d in disc:
        for r in d["reasons"]:
            disc_reason[r] = disc_reason.get(r, 0) + 1
        disc_block[str(d["block_id"])] = disc_block.get(str(d["block_id"]), 0) + 1
        disc_speed[d["speed"]] = disc_speed.get(d["speed"], 0) + 1
    n_zero = sum(t["n_dwell_zero"] for t in obs_agg["texts"])
    return {
        "definition": definition,
        "scope": {"name": scope, "n_trials_out_of_scope": len(out_of_scope)},
        "config": {"text_size_trials": cfg["text_size_trials"], "B": cfg["B"], "seed": cfg["seed"],
                   "mixtures": cfg["mixtures"], "obs_end_offset_ms": off},
        "inputs_sha256": {"match": _sha(match_bytes), "sessions": _sha(sessions_bytes), "obs": _sha(obs_bytes),
                          "plan": plan["sha256"], "oracle": _sha(oracle_bytes),
                          "config": None if config_bytes is None else _sha(config_bytes)},
        "texts": {"n": len(tx), "n_by_speed": dict((s, by_speed[s]) for s in SPEEDS if s in by_speed),
                  "dropped_trials_by_block": dict((str(b), n) for b, n in layout["dropped_trials"].items()),
                  "discarded": {"n": len(disc), "by_reason": dict(sorted(disc_reason.items())),
                                "by_block": dict(sorted(disc_block.items())),
                                "by_speed": dict((s, disc_speed[s]) for s in SPEEDS if s in disc_speed)},
                  "trials_in_scope_by_speed": dict(
                      (s, dict((str(k), in_scope_hist[s][k]) for k in sorted(in_scope_hist[s])))
                      for s in SPEEDS if s in in_scope_hist),
                  "n_without_trials_in_scope_by_speed": dict((s, no_scope[s]) for s in SPEEDS if s in no_scope)},
        "outside_texts": {"gt": _outside_report(gt_agg, layout["trial_status"], plan["trials"], out_of_scope),
                          "obs": _outside_report(obs_agg, layout["trial_status"], plan["trials"], out_of_scope)},
        "dwell_zero": {"n_obs_rows": n_zero,
                       "note": "observation rows with dwell_ms == 0 are counted as conversions but are left out of "
                               "mean_dwell_ms and dwell_fraction; in the matching they stay zero-length intervals "
                               "and count as false positives (as pre-registered)"},
        "aggregate": {"gt": gt_agg["texts"], "obs": obs_agg["texts"]},
        "agreement": agreement(gt_agg, obs_agg, cfg["B"], cfg["seed"]),
        "agreement_by_stratum": agreement_by_stratum(gt_agg, obs_agg, cfg["B"], cfg["seed"]),
        "mixture_bias": mixture_bias(stratify(gt_agg, obs_agg), cfg["mixtures"], cfg["B"], cfg["seed"]),
        "per_trial": per_trial(plan, oracle, gt, obs, excluded_trials, out_of_scope, cfg["B"], cfg["seed"]),
        "per_trial_uncontaminated": None if uncontaminated is None else
        per_trial(plan, oracle, gt, obs, excluded_trials, out_of_scope, cfg["B"], cfg["seed"],
                  only_trials=set(uncontaminated)),
        "per_trial_layer_none": None if layer_none is None else
        per_trial(plan, oracle, gt, obs, excluded_trials, out_of_scope, cfg["B"], cfg["seed"],
                  only_trials=set(layer_none)),
        "note": "a synthetic input has no natural mixing proportion; read the per-stratum biases and the "
                "stratified agreement as the primary estimates, the pooled agreement and each mixture as "
                "sensitivity checks. Every per-text value and ICC holds for this design's mix of scenarios "
                "(a text mixes the scenarios of its block only in expectation: the plan orders the trials "
                "by the seed and does not balance them within a text); per_trial gives the per-trial "
                "differences by scenario and speed for reweighting to another mix. The per-trial "
                "differences include contamination by sessions carried over from an earlier trial; "
                "per_trial_uncontaminated restricts them to the trials in neither the carried_out nor the "
                "received_carry layer of the prediction (the closing-key trials stay in), and the reweighting "
                "to another mix of scenarios is meant for that view; per_trial_layer_none (the trials in none "
                "of the prediction's layers) is a diagnostic. Both are null without a prediction. Per-text "
                "measures cover the text's trials in the scope",
    }


def render(out):
    """Render the output bytes (LF line endings, UTF-8, no BOM, keys in a fixed sorted order)."""
    return match.render(out)


# ---------------------------------------------------------------- selftest

T0 = 1700000000000
BASE_DWELL = {"slow": 1400, "mid": 1000, "fast": 700}


def _world(n_blocks=6, per_block=32):
    """A synthetic plan, oracle windows and ground-truth sessions. Blocks alternate slow / mid / fast;
    the number of sessions per trial, the dwell and the cancel pattern vary from text to text (a
    text is 8 consecutive trials), so that every measure has real variation between texts."""
    plan_trials, windows, gt = {}, [], []
    idx = 0
    for b in range(n_blocks):
        sp = SPEEDS[b % 3]
        for k in range(per_block):
            tid = "t%05d" % (idx + 1)
            plan_trials[tid] = {"block_id": b, "scenario": "S%d" % (k % 3 + 1), "speed": sp}
            begin = T0 + idx * 20000
            windows.append({"trial_id": tid, "begin_wall_ms": begin, "end_wall_ms": begin + 12000})
            text_no = idx // 8
            extra = (text_no * 5) % 8
            for j in range(2 if (idx % 8) < extra else 1):
                d = BASE_DWELL[sp] + 30 * (text_no % 4) + (idx * 37) % 200 + 25 * j
                lo = begin + 500 + j * 5000
                cancel = (idx * 11 + j) % (3 + text_no % 5) == 0
                gt.append(match._gt(tid, lo, lo + d, outcome="cancel" if cancel else "commit"))
            idx += 1
    return {"plan": {"trials": plan_trials}, "oracle": {"trials": windows}, "gt": gt}


def _obs_from(gt, dwell_delta=None, keep=None):
    """Observation rows for the ground-truth sessions (each row ends where its session ends)."""
    rows = []
    for i, s in enumerate(gt):
        if keep is not None and not keep(i, s):
            continue
        d = s["end_wall_ms"] - s["start_wall_ms"] + (dwell_delta(i, s) if dwell_delta else 0)
        rows.append(match._ob(s["end_wall_ms"] - d, s["end_wall_ms"], abandon=(s["outcome"] == "cancel")))
    return rows


def _aggs(w, obs, size=8):
    tx = texts(w["plan"], w["oracle"], size)
    return tx, aggregate(tx, w["gt"], None, "gt"), aggregate(tx, None, attach_trials(obs, w["oracle"]), "obs")


def _permuted(agg, seed):
    """The same aggregate with the per-text values permuted among the texts (ids stay in place)."""
    rng = random.Random(seed)
    order = list(range(len(agg["texts"])))
    for i in range(len(order) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        order[i], order[j] = order[j], order[i]
    keep = ("text_id", "block_id", "speed", "duration_ms")
    out = []
    for i, t in enumerate(agg["texts"]):
        src = agg["texts"][order[i]]
        row = dict((k, t[k]) for k in keep)
        row.update((m, src[m]) for m in MEASURES)
        out.append(row)
    return {"texts": out, "n_outside_texts": agg["n_outside_texts"]}


def _case_layout():
    f = []
    plan_trials, windows = {}, []
    for b in range(3):
        for k in range(20):
            tid = "t%05d" % (b * 20 + k + 1)
            plan_trials[tid] = {"block_id": b, "scenario": "S1", "speed": SPEEDS[b]}
            windows.append({"trial_id": tid, "begin_wall_ms": T0 + (b * 20 + k) * 1000, "end_wall_ms": T0 + (b * 20 + k) * 1000 + 800})
    plan, oracle = {"trials": plan_trials}, {"trials": windows}
    lay = text_layout(plan, oracle, 8)
    tx = lay["texts"]
    if len(tx) != 6 or lay["dropped_trials"] != {0: 4, 1: 4, 2: 4}:
        f.append("20 trials per block at size 8 should give 2 texts per block and 4 dropped trials per block: %d texts, %r" % (
            len(tx), lay["dropped_trials"]))
    else:
        if tx[1]["trial_ids"] != ["t%05d" % k for k in range(9, 17)]:
            f.append("the second text of block 0 is not trials 9..16 in plan order: %r" % tx[1]["trial_ids"])
        used = set(t for x in tx for t in x["trial_ids"])
        if "t00017" in used or "t00020" in used:
            f.append("the dropped remainder (trials 17..20 of block 0) is inside a text")
        if [x["speed"] for x in tx] != ["slow", "slow", "mid", "mid", "fast", "fast"]:
            f.append("a text does not carry its block's speed: %r" % [x["speed"] for x in tx])
        if tx[0]["begin_wall_ms"] != T0 or tx[0]["end_wall_ms"] != T0 + 7000 + 800 or tx[0]["duration_ms"] != 7800:
            f.append("a text's time is not the first trial begin to the last trial end: %r" % tx[0])
    if len(text_layout(plan, oracle, 20)["texts"]) != 3 or len(text_layout(plan, oracle, 21)["texts"]) != 0 or \
            text_layout(plan, oracle, 21)["dropped_trials"] != {0: 20, 1: 20, 2: 20}:
        f.append("a text size equal to the block gives 1 text per block; a larger one gives none and drops all trials")
    # plan order (not id order) and interleaved blocks are respected
    shuffled = {}
    for tid in sorted(plan_trials, reverse=True):
        shuffled[tid] = plan_trials[tid]
    lay2 = text_layout({"trials": shuffled}, oracle, 8)
    if lay2["texts"][0]["trial_ids"][0] != "t00060" or lay2["texts"][0]["block_id"] != 2:
        f.append("texts do not follow the plan order: %r" % lay2["texts"][0])
    # sessions and rows in dropped trials are not counted (and are reported as outside the texts)
    gt = [match._gt("t00001", T0 + 100, T0 + 500), match._gt("t00018", T0 + 17000 + 100, T0 + 17000 + 500)]
    agg = aggregate(tx, gt, None, "gt")
    if agg["n_outside_texts"] != 1 or agg["texts"][0]["conversions_per_trial"] != 1 / 8 or \
            sum(t["conversions_per_trial"] for t in agg["texts"]) != 1 / 8:
        f.append("a session in a dropped trial is counted: %r" % agg["n_outside_texts"])
    if agg["texts"][1]["mean_dwell_ms"] is not None or agg["texts"][1]["abandon_rate"] is not None or \
            agg["texts"][1]["conversions_per_trial"] != 0:
        f.append("a text without sessions must have conversions_per_trial 0 and the other measures None")
    # the scope fields: without a scope every trial is in scope and the scope duration is the sum of the windows
    if tx[0]["trials_in_scope"] != tx[0]["trial_ids"] or tx[0]["n_trials_in_scope"] != 8 or \
            tx[0]["scope_duration_ms"] != 8 * 800:
        f.append("a text without a scope must have all its trials in scope and the sum of the windows: %r" % tx[0])
    sc = text_layout(plan, oracle, 8, None, {"t00002", "t00003", "t00050"})["texts"]
    if (sc[0]["n_trials_in_scope"], sc[0]["scope_duration_ms"]) != (6, 6 * 800) or \
            sc[0]["trials_in_scope"] != ["t00001"] + ["t%05d" % k for k in range(4, 9)] or \
            sc[0]["trial_ids"] != tx[0]["trial_ids"] or sc[0]["duration_ms"] != 7800 or \
            (sc[5]["n_trials_in_scope"], sc[5]["scope_duration_ms"]) != (7, 7 * 800):
        f.append("the trials in scope / the scope duration of a text are wrong: %r" % ([(t["text_id"], t["n_trials_in_scope"], t["scope_duration_ms"]) for t in sc],))
    # errors, not silent handling
    mixed = dict((t, dict(i)) for t, i in plan_trials.items())
    mixed["t00003"]["speed"] = "fast"
    try:
        text_layout({"trials": mixed}, oracle, 8)
        f.append("a text with mixed speeds was accepted")
    except InputError:
        pass
    # a text with a trial that was not run, or that was excluded, is discarded and counted (never an error)
    not_run = text_layout(plan, {"trials": windows[1:]}, 8)
    if len(not_run["texts"]) != 5 or not_run["discarded_texts"] != [
            {"text_id": "b0.0", "block_id": 0, "speed": "slow", "reasons": ["not_run"]}]:
        f.append("a text with a trial that was not run is not discarded as not_run: %r" % (not_run["discarded_texts"],))
    if not_run["trial_status"].get("t00002") != "not_run" or not_run["trial_status"].get("t00017") != "remainder":
        f.append("the status of the trials of a discarded text / of a remainder is wrong: %r" % (not_run["trial_status"],))
    excl = text_layout(plan, oracle, 8, {"t00012"})
    if len(excl["texts"]) != 5 or excl["discarded_texts"] != [
            {"text_id": "b0.1", "block_id": 0, "speed": "slow", "reasons": ["excluded"]}]:
        f.append("a text with an excluded trial is not discarded as excluded: %r" % (excl["discarded_texts"],))
    both = text_layout(plan, {"trials": windows[1:]}, 8, {"t00004", "t00030"})
    if [(d["text_id"], d["reasons"]) for d in both["discarded_texts"]] != [
            ("b0.0", ["not_run", "excluded"]), ("b1.1", ["excluded"])]:
        f.append("the reasons of the discarded texts are wrong: %r" % (both["discarded_texts"],))
    if any(len(x["trial_ids"]) != 8 or set(x["trial_ids"]) & {"t00001", "t00004", "t00030"} for x in both["texts"]) or             any(len(x["trial_ids"]) != 8 or "t00012" in x["trial_ids"] for x in excl["texts"]):
        f.append("a kept text is not a complete text of 8 trials that were run and not excluded")
    for bad in (0, -1, 1.5, True, "8"):
        try:
            text_layout(plan, oracle, bad)
            f.append("text size %r was accepted" % (bad,))
        except ValueError:
            pass
    return f


def _case_scope_measures_body(plan, oracle):
    """The measures of a text over its trials in scope (4 trials of 5000 ms each, a span of 35000 ms)."""
    f = []
    # sessions in t00001 (1000 ms, commit) and t00002 (2000 ms, cancel); t00003 and t00004 are out of scope
    gt = [match._gt("t00001", T0 + 100, T0 + 1100), match._gt("t00002", T0 + 10100, T0 + 12100, outcome="cancel")]
    tx = text_layout(plan, oracle, 4, None, {"t00003", "t00004"})["texts"]
    a = aggregate(tx, gt, None, "gt")["texts"][0]
    if a["n_trials_in_scope"] != 2 or a["scope_duration_ms"] != 10000:
        f.append("the trials in scope / the scope duration are wrong: %r" % (a,))
    if a["dwell_fraction"] is None or abs(a["dwell_fraction"] - 3000 / 10000) > 1e-12:
        f.append("the denominator of dwell_fraction must be the duration of the trials in scope, not the text span: %r" % (a,))
    if a["conversions_per_trial"] != 1.0:
        f.append("conversions must be divided by the trials in scope (2 sessions over 2 trials): %r" % (a,))
    if abs(a["mean_dwell_ms"] - 1500) > 1e-9 or abs(a["abandon_rate"] - 0.5) > 1e-12:
        f.append("mean_dwell_ms and abandon_rate must not depend on the scope: %r" % (a,))
    # the same sessions with every trial in scope: the speed of conversion halves, the time fraction too
    full = aggregate(text_layout(plan, oracle, 4)["texts"], gt, None, "gt")["texts"][0]
    if full["conversions_per_trial"] != 0.5 or abs(full["dwell_fraction"] - 3000 / 20000) > 1e-12:
        f.append("the same sessions over all 4 trials must give 0.5 conversions per trial: %r" % (full,))
    # a text with no trial in scope gives None for all four measures (never 0), even with a session on it
    none_tx = text_layout(plan, oracle, 4, None, set(plan["trials"]))["texts"]
    n = aggregate(none_tx, [], None, "gt")["texts"][0]
    if n["n_trials_in_scope"] != 0 or n["scope_duration_ms"] != 0 or any(n[m] is not None for m in MEASURES):
        f.append("a text with no trial in scope must give None for every measure: %r" % (n,))
    nn = aggregate(none_tx, gt, None, "gt")
    if nn["texts"][0]["conversions_per_trial"] is not None or nn["n_outside_texts"] != 2:
        f.append("a session on a text with no trial in scope must be outside, and the measures None: %r" % (nn,))
    # a session of an out-of-scope trial is not used: not in the counts, not in the dwell, counted as outside
    more = gt + [match._gt("t00003", T0 + 20100, T0 + 29100, outcome="cancel")]
    m = aggregate(tx, more, None, "gt")
    if m["texts"][0] != a or m["n_outside_texts"] != 1 or m["outside_trials"] != {"t00003": 1}:
        f.append("a session of an out-of-scope trial was used or not counted as outside: %r" % (m,))
    obs = attach_trials([match._ob(T0 + 100, T0 + 1100), match._ob(T0 + 10100, T0 + 12100, abandon=True),
                         match._ob(T0 + 30100, T0 + 30600)], oracle)
    mo = aggregate(tx, None, obs, "obs")
    if mo["n_outside_texts"] != 1 or mo["texts"][0]["conversions_per_trial"] != 1.0 or \
            abs(mo["texts"][0]["dwell_fraction"] - 3000 / 10000) > 1e-12:
        f.append("an observation row of an out-of-scope trial was used: %r" % (mo,))
    # the reason is reported (a guard: the matching normally holds no such record)
    rep_ = _outside_report(m, {}, plan["trials"], {"t00003", "t00004"})
    if rep_ != {"n": 1, "by_reason": {"out_of_scope": 1}, "by_speed": {"slow": 1}}:
        f.append("an out-of-scope record must be reported under out_of_scope: %r" % (rep_,))
    return f


def _case_definitions():
    f = []
    plan = {"trials": dict(("t%05d" % (k + 1), {"block_id": 0, "scenario": "S1", "speed": "slow"}) for k in range(4))}
    oracle = {"trials": [{"trial_id": "t%05d" % (k + 1), "begin_wall_ms": T0 + k * 10000, "end_wall_ms": T0 + k * 10000 + 5000} for k in range(4)]}
    tx = texts(plan, oracle, 4)
    gt = [match._gt("t00001", T0 + 100, T0 + 1100), match._gt("t00002", T0 + 10100, T0 + 12100, outcome="cancel"),
          match._gt("t00004", T0 + 30100, T0 + 30600)]
    a = aggregate(tx, gt, None, "gt")["texts"][0]
    # dwell 1000, 2000, 500 -> mean 1166.67; 1 of 3 cancelled; 3 sessions over 4 trials; the scope
    # duration is 4 windows of 5000 ms (the span, 35000 ms, is not the denominator)
    if a["conversions_per_trial"] != 3 / 4 or abs(a["mean_dwell_ms"] - 3500 / 3) > 1e-9 or abs(a["abandon_rate"] - 1 / 3) > 1e-12 or \
            abs(a["dwell_fraction"] - 3500 / 20000) > 1e-12 or a["n_trials_in_scope"] != 4 or a["scope_duration_ms"] != 20000:
        f.append("the ground-truth measures are wrong: %r" % (a,))
    obs = attach_trials([match._ob(T0 + 100, T0 + 1100), match._ob(T0 + 10100, T0 + 12100, abandon=True),
                         match._ob(T0 + 30100, T0 + 30600), match._ob(T0 - 9000, T0 - 8000)], oracle)
    ob = aggregate(tx, None, obs, "obs")
    if ob["n_outside_texts"] != 1 or ob["texts"][0]["conversions_per_trial"] != 3 / 4 or \
            abs(ob["texts"][0]["abandon_rate"] - 1 / 3) > 1e-12 or abs(ob["texts"][0]["mean_dwell_ms"] - 3500 / 3) > 1e-9:
        f.append("the observation measures (or the attribution to trials) are wrong: %r" % (ob,))
    # a text of zero duration cannot give a time fraction
    z = aggregate([{"text_id": "z", "block_id": 0, "speed": "slow", "trial_ids": ["t00001"], "duration_ms": 0,
                    "trials_in_scope": ["t00001"], "n_trials_in_scope": 1, "scope_duration_ms": 0}], gt, None, "gt")
    if z["texts"][0]["dwell_fraction"] is not None:
        f.append("a zero-duration text must give dwell_fraction None")
    # a row with dwell_ms == 0 is a conversion but not a measured dwell
    zobs = attach_trials([match._ob(T0 + 100, T0 + 1100), match._ob(T0 + 10100, T0 + 10100, abandon=True),
                          match._ob(T0 + 30100, T0 + 30600)], oracle)
    zt = aggregate(tx, None, zobs, "obs")["texts"][0]
    if zt["conversions_per_trial"] != 3 / 4 or zt["n_dwell_zero"] != 1 or abs(zt["mean_dwell_ms"] - 750) > 1e-9 or \
            abs(zt["abandon_rate"] - 1 / 3) > 1e-12 or abs(zt["dwell_fraction"] - 1500 / 20000) > 1e-12:
        f.append("a dwell_ms of 0 is used as a measured dwell (or is not counted as a conversion): %r" % (zt,))
    zonly = aggregate(tx, None, attach_trials([match._ob(T0 + 100, T0 + 100)], oracle), "obs")["texts"][0]
    if zonly["conversions_per_trial"] != 1 / 4 or zonly["mean_dwell_ms"] is not None or zonly["dwell_fraction"] is not None:
        f.append("a text with only dwell_ms == 0 rows must give mean_dwell_ms and dwell_fraction None: %r" % (zonly,))
    f += _case_scope_measures_body(plan, oracle)
    for name, fn in (("an unknown side", lambda: aggregate(tx, gt, None, "both")),
                     ("an open ground-truth session", lambda: aggregate(tx, [match._gt("t00001", T0, None, outcome="open_at_end")], None, "gt")),
                     ("a non-integer dwell", lambda: aggregate(tx, None, [{"trial_id": "t00001", "dwell_ms": 1.5, "abandon": False}], "obs"))):
        try:
            fn()
            f.append("%s was accepted" % name)
        except ValueError:
            pass
    return f


def _case_recovery():
    """A dwell bias of about +50 ms appears in the mean conversion time and nowhere else."""
    f = []
    w = _world()
    jitter = lambda i, s: 50 + ((i * 13) % 5 - 2) * 10
    _tx, g, o = _aggs(w, _obs_from(w["gt"], dwell_delta=jitter))
    ag = agreement(g, o, 200, 1)
    ba = ag["mean_dwell_ms"]["bland_altman"]
    if not (abs(ba["bias"] - 50) < 0.2 and ba["loa_lo"] <= 50 <= ba["loa_hi"]):
        f.append("the dwell bias of +50 ms is not recovered by the mean conversion time: %r" % (ba,))
    for m in ("conversions_per_trial", "abandon_rate"):
        b = ag[m]["bland_altman"]
        if b["bias"] != 0 or b["sd"] != 0:
            f.append("the dwell bias leaks into %s: %r" % (m, b))
    if not (0.0 < ag["dwell_fraction"]["bland_altman"]["bias"] < 0.2):
        f.append("the dwell bias does not raise the time fraction: %r" % (ag["dwell_fraction"]["bland_altman"],))
    # the sign is observation minus ground truth: a negative bias flips it
    _tx, g2, o2 = _aggs(w, _obs_from(w["gt"], dwell_delta=lambda i, s: -50))
    if abs(agreement(g2, o2, 50, 1)["mean_dwell_ms"]["bland_altman"]["bias"] + 50) > 1e-9:
        f.append("a dwell of 50 ms too short does not give a bias of -50 (the sign is observation minus ground truth)")
    if ag["mean_dwell_ms"]["n"] != 24:
        f.append("the recovery world should have 24 texts: %d" % ag["mean_dwell_ms"]["n"])
    return f


def _case_perfect_and_shuffle():
    """Perfect agreement gives ICC(2,1) = 1; shuffling the observations among the texts drops it."""
    f = []
    w = _world()
    _tx, g, o = _aggs(w, _obs_from(w["gt"]))
    ag = agreement(g, o, 200, 1)
    for m in MEASURES:
        v = ag[m]["icc21"]
        if v is None or abs(v - 1.0) > 1e-9:
            f.append("perfect agreement does not give ICC(2,1) = 1 for %s: %r" % (m, v))
        b = ag[m]["bland_altman"]
        if b["bias"] != 0 or b["sd"] != 0:
            f.append("perfect agreement does not give a zero Bland-Altman bias and sd for %s: %r" % (m, b))
    lo, hi = ag["mean_dwell_ms"]["icc21_ci"]
    if lo is None or not (lo <= 1.0 + 1e-9 and hi >= 1.0 - 1e-9):
        f.append("the ICC interval does not contain the ICC under perfect agreement: %r" % (ag["mean_dwell_ms"]["icc21_ci"],))
    # negative control: values permuted among the texts. A single permutation can land on either side
    # of 0 by chance (24 texts), so many seeds are drawn: the ICC must average about 0 and never
    # approach 1.
    vals = []
    for seed in range(1, 21):
        sh = agreement(g, _permuted(o, seed), 10, 1)
        for m in MEASURES:
            if sh[m]["icc21"] is None:
                f.append("the shuffled ICC of %s is undefined" % m)
            else:
                vals.append(sh[m]["icc21"])
    if vals and (max(vals) > 0.6 or abs(sum(vals) / len(vals)) > 0.15):
        f.append("shuffling the observations among texts leaves the ICC far from 0 (largest %.3f, mean %.3f)" % (
            max(vals), sum(vals) / len(vals)))
    # the speeds sit at different levels, so a shuffle inside each speed keeps a high pooled ICC while
    # the stratified ICC is near 0 (the stratified values are the primary estimates)
    ps = agreement(g, _permuted_within_speed(o, 1), 10, 1)["mean_dwell_ms"]["icc21"]
    st = agreement_by_stratum(g, _permuted_within_speed(o, 1), 10, 1)
    if ps is None or ps < 0.8 or any(st[sp]["mean_dwell_ms"]["icc21"] is None or st[sp]["mean_dwell_ms"]["icc21"] > 0.6
                                     for sp in SPEEDS):
        f.append("the pooled ICC is not inflated by the level difference between speeds, or the stratified ICC is "
                 "not low: pooled %r, stratified %r" % (ps, dict((sp, st[sp]["mean_dwell_ms"]["icc21"]) for sp in st)))
    perfect = agreement_by_stratum(g, o, 10, 1)
    if set(perfect) != set(SPEEDS) or any(perfect[sp]["mean_dwell_ms"]["n"] != 8 or
                                         abs(perfect[sp]["mean_dwell_ms"]["icc21"] - 1.0) > 1e-9 for sp in perfect):
        f.append("agreement_by_stratum does not give 8 texts and ICC 1 per speed under perfect agreement")
    return f


def _permuted_within_speed(agg, seed):
    """The per-text values permuted among the texts of the same speed (ids stay in place)."""
    rng = random.Random(seed)
    out = [dict(t) for t in agg["texts"]]
    for sp in SPEEDS:
        idx = [i for i, t in enumerate(agg["texts"]) if t["speed"] == sp]
        order = list(idx)
        for i in range(len(order) - 1, 0, -1):
            j = int(rng.random() * (i + 1))
            order[i], order[j] = order[j], order[i]
        for i, src in zip(idx, order):
            out[i].update((m, agg["texts"][src][m]) for m in MEASURES)
    return {"texts": out, "n_outside_texts": agg["n_outside_texts"]}


def _case_mixture():
    """Misses confined to the fast stratum make the aggregate bias larger under a fast-heavy mixture."""
    f = []
    w = _world()
    fast_tids = set(t for t, i in w["plan"]["trials"].items() if i["speed"] == "fast")
    keep = lambda i, s: not (s["trial_id"] in fast_tids and i % 2 == 0)
    _tx, g, o = _aggs(w, _obs_from(w["gt"], keep=keep))
    mb = mixture_bias(stratify(g, o), DEFAULT_MIXTURES)
    st = mb["by_stratum"]
    b = dict((s, st[s]["conversions_per_trial"]["bias"]) for s in SPEEDS)
    if b["slow"] != 0 or b["mid"] != 0 or not b["fast"] < -0.3:
        f.append("the misses are not confined to the fast stratum (per-stratum conversions_per_trial bias): %r" % (b,))
    got = dict((n, mb["mixtures"][n]["bias"]["conversions_per_trial"]) for n in DEFAULT_MIXTURES)
    if not (abs(got["fast_heavy"]) > abs(got["uniform"]) > abs(got["slow_heavy"]) > 0):
        f.append("the bias does not grow from slow-heavy to uniform to fast-heavy: %r" % (got,))
    for name, wt in (("fast_heavy", 0.6), ("uniform", 1 / 3), ("slow_heavy", 0.1)):
        if abs(got[name] - wt * b["fast"]) > 1e-9:
            f.append("%s is not the weighted mean of the strata biases: %r vs %r" % (name, got[name], wt * b["fast"]))
    # the same texts without misses: no mixture moves the bias
    _tx2, g2, o2 = _aggs(w, _obs_from(w["gt"]))
    clean = mixture_bias(stratify(g2, o2), DEFAULT_MIXTURES)
    if any(clean["mixtures"][n]["bias"]["conversions_per_trial"] != 0 for n in DEFAULT_MIXTURES):
        f.append("a mixture moved the bias although the observation is exact")
    # weights are normalised; zero-weight strata are not needed; a needed empty stratum gives None
    half = mixture_bias(stratify(g, o), {"x": {"fast": 3}, "y": {"fast": 1, "slow": 0}})
    if half["mixtures"]["x"]["weights"] != {"slow": 0.0, "mid": 0.0, "fast": 1.0} or \
            half["mixtures"]["x"]["bias"] != half["mixtures"]["y"]["bias"]:
        f.append("weights are not normalised / a zero weight changes the result")
    only_slow = mixture_bias({"slow": stratify(g, o)["slow"]}, {"fast_only": {"fast": 1}, "slow_only": {"slow": 1}})
    if only_slow["mixtures"]["fast_only"]["bias"]["conversions_per_trial"] is not None or \
            only_slow["mixtures"]["slow_only"]["bias"]["conversions_per_trial"] != 0:
        f.append("a needed stratum without data must give None (and an available one a value)")
    for name, fn in (("a negative weight", lambda: mixture_bias({}, {"a": {"slow": -1, "fast": 2}})),
                     ("all-zero weights", lambda: mixture_bias({}, {"a": {"slow": 0}})),
                     ("an unknown stratum in the weights", lambda: mixture_bias({}, {"a": {"medium": 1}})),
                     ("an unknown stratum in the texts", lambda: mixture_bias({"medium": []}, {"a": {"slow": 1}})),
                     ("a boolean weight", lambda: mixture_bias({}, {"a": {"slow": True}})),
                     ("a weight too large for a float", lambda: mixture_bias({}, {"a": {"slow": 10 ** 400}})),
                     ("weights whose sum overflows", lambda: mixture_bias({}, {"a": {"slow": 1e308, "mid": 1e308}}))):
        try:
            fn()
            f.append("%s was accepted" % name)
        except ValueError:
            pass
    return f


def _case_seed_and_bytes():
    f = []
    w = _world()
    jitter = lambda i, s: 50 + ((i * 13) % 5 - 2) * 10
    _tx, g, o = _aggs(w, _obs_from(w["gt"], dwell_delta=jitter, keep=lambda i, s: i % 9 != 0))
    state = random.getstate()
    a = agreement(g, o, 300, 7)
    b = agreement(g, o, 300, 7)
    c = agreement(g, o, 300, 8)
    if random.getstate() != state:
        f.append("the global random state was touched")
    ja, jb = json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True)
    if ja != jb:
        f.append("the same seed does not give byte-identical output")
    if a == c:
        f.append("a different seed gives the same intervals (the seed has no effect)")
    if any(a[m]["icc21_ci"][0] is None for m in MEASURES):
        f.append("an ICC interval is missing in a well-behaved world: %r" % ([a[m]["icc21_ci"] for m in MEASURES],))
    # the intervals bracket the point estimate loosely and are ordered
    for m in MEASURES:
        lo, hi = a[m]["icc21_ci"]
        if lo is not None and not lo <= hi:
            f.append("the ICC interval of %s is not ordered" % m)
    # not computable cases are None, never 0
    one_block = _world(n_blocks=1, per_block=32)
    _t, g1, o1 = _aggs(one_block, _obs_from(one_block["gt"], dwell_delta=jitter))
    r = agreement(g1, o1, 50, 1)["mean_dwell_ms"]
    if r["icc21"] is None or r["icc21_ci"] != [None, None]:
        f.append("one block gives an ICC but no block-level interval: %r" % (r,))
    single = _world(n_blocks=1, per_block=8)
    _t, g3, o3 = _aggs(single, _obs_from(single["gt"]))
    s = agreement(g3, o3, 50, 1)["mean_dwell_ms"]
    if s["icc21"] is not None or s["icc21_ci"] != [None, None] or s["bland_altman"]["sd"] is not None:
        f.append("a single text must give no ICC, no interval and no limits of agreement: %r" % (s,))
    # an undefined resample withholds the interval and says so; any other error is not swallowed
    rows = [(0, 1.0, 1.0), (0, 2.0, 2.0), (1, 5.0, 5.0), (1, 5.0, 5.0)]
    if _icc_interval(rows, 50, 1) != (None, None, "undefined_resample"):
        f.append("a resample with an undefined ICC does not give a null interval with its reason: %r" % (
            _icc_interval(rows, 50, 1),))
    if _icc_interval([(0, 1.0, 1.0), (0, 2.0, 2.5), (1, 3.0, 3.5), (1, 4.0, 4.0)], 50, 1)[2] != "ok":
        f.append("a well-defined ICC interval is not reported as ok")
    try:
        _icc_interval([(0, 1.0, 1.0), (0, 2.0, float("nan")), (1, 3.0, 3.5), (1, 4.0, 4.0)], 20, 1)
        f.append("a non-finite value in the ICC interval was swallowed")
    except ValueError:
        pass
    if r["icc21_ci_status"] != "fewer_than_two_blocks":
        f.append("the reason for a missing interval is not reported: %r" % (r,))
    for bad in (dict(B=0, seed=1), dict(B=10, seed="a"), dict(B=True, seed=1), dict(B=MAX_B + 1, seed=1)):
        try:
            agreement(g, o, **bad)
            f.append("agreement accepted %r" % (bad,))
        except ValueError:
            pass
    try:
        agreement(g, _permuted_ids(o), 10, 1)
        f.append("aggregates over different texts were compared")
    except ValueError:
        pass
    return f


def _permuted_ids(agg):
    texts_ = [dict(t) for t in agg["texts"]]
    texts_[0]["text_id"], texts_[1]["text_id"] = texts_[1]["text_id"], texts_[0]["text_id"]
    return {"texts": texts_, "n_outside_texts": agg["n_outside_texts"]}


def _jl(rows):
    return match._jl(rows)


def _case_cli():
    f = []
    fx = match._cli_fixture()
    mbytes = match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"]))
    cfg = json.dumps({"B": 100, "seed": 3}).encode("utf-8")
    out = evaluate(mbytes, fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
    # Fixture premises (3 blocks of 30 trials, texts of 8): trials 6 and 7 are excluded (clock jump), so the
    # first text of block 0 is discarded; trial 11 is a miss, trial 16 holds two sessions seen as one merged
    # row, trial 21 is one session seen as three fragments, trial 45 has a false positive inside it, and one
    # more false positive lies before every trial. Every other text has 8 sessions on both sides.
    if out["texts"] != {"n": 8, "n_by_speed": {"slow": 2, "mid": 3, "fast": 3},
                        "dropped_trials_by_block": {"0": 6, "1": 6, "2": 6},
                        "discarded": {"n": 1, "by_reason": {"excluded": 1}, "by_block": {"0": 1},
                                      "by_speed": {"slow": 1}},
                        "trials_in_scope_by_speed": {"slow": {"8": 2}, "mid": {"8": 3}, "fast": {"8": 3}},
                        "n_without_trials_in_scope_by_speed": {"slow": 0, "mid": 0, "fast": 0}}:
        f.append("the text counts or the dropped trials are wrong: %r" % (out["texts"],))
    oo, og = out["outside_texts"]["obs"], out["outside_texts"]["gt"]
    if oo["by_reason"].get("no_trial") != 1 or og["by_reason"].get("no_trial") is not None or \
            not oo["by_reason"].get("discarded_remainder") or not og["by_reason"].get("discarded_text_excluded") or \
            oo["n"] != sum(oo["by_reason"].values()) or og["n"] != sum(og["by_reason"].values()) or \
            sum(oo["by_speed"].values()) != oo["n"] - 1:
        f.append("records outside the kept texts are not reported by reason and speed: %r" % (out["outside_texts"],))
    got_g = dict((t["text_id"], round(t["conversions_per_trial"] * t["n_trials_in_scope"])) for t in out["aggregate"]["gt"])
    got_o = dict((t["text_id"], round(t["conversions_per_trial"] * t["n_trials_in_scope"])) for t in out["aggregate"]["obs"])
    want_g = {"b0.1": 9, "b0.2": 8, "b1.0": 8, "b1.1": 8, "b1.2": 8, "b2.0": 8, "b2.1": 8, "b2.2": 8}
    want_o = {"b0.1": 7, "b0.2": 10, "b1.0": 8, "b1.1": 9, "b1.2": 8, "b2.0": 8, "b2.1": 8, "b2.2": 8}
    if got_g != want_g or got_o != want_o:
        f.append("the conversions per text are wrong (ground truth takes pairs, split, merge and misses; observation "
                 "takes pairs, split, merge and false positives): %r vs %r" % (got_g, got_o))
    if out["agreement"]["conversions_per_trial"]["n"] != 8 or out["definition"] != "all":
        f.append("the agreement is not over the 8 texts")
    if set(out["agreement_by_stratum"]) != set(SPEEDS) or out["agreement_by_stratum"]["slow"]["conversions_per_trial"]["n"] != 2:
        f.append("the agreement by speed is missing or over the wrong texts: %r" % (sorted(out["agreement_by_stratum"]),))
    if out["dwell_zero"]["n_obs_rows"] != 0:
        f.append("the fixture has no observation with dwell_ms == 0")
    if set(out["mixture_bias"]["mixtures"]) != set(DEFAULT_MIXTURES):
        f.append("the default mixtures are not reported")
    with tempfile.TemporaryDirectory() as d:
        paths = {}
        for k, data in (("match", mbytes), ("sessions", fx["sessions"]), ("obs", fx["obs"]), ("plan", fx["plan"]),
                        ("oracle", fx["oracle"]), ("config", cfg)):
            paths[k] = os.path.join(d, k + ".dat")
            with open(paths[k], "wb") as fh:
                fh.write(data)

        def run(out_name, **over):
            a = dict(match=paths["match"], sessions=paths["sessions"], obs=paths["obs"], plan=paths["plan"],
                     oracle=paths["oracle"], config=paths["config"], out=os.path.join(d, out_name))
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
            sub = subprocess.run([sys.executable, os.path.abspath(__file__), "--match", paths["match"],
                                  "--sessions", paths["sessions"], "--obs", paths["obs"], "--plan", paths["plan"],
                                  "--oracle", paths["oracle"], "--config", paths["config"],
                                  "--out", os.path.join(d, "o3.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if sub.returncode != 0 or open(os.path.join(d, "o3.json"), "rb").read() != b1:
                f.append("the CLI run as a subprocess does not exit 0, or is not byte-identical: %r" % sub.stderr[-200:])

        def rc_of(name, want, **over):
            rc, err = run(name, **over)
            if rc != want or "Traceback" in err:
                f.append("%s: exit %r (expected %d) %s" % (name, rc, want, err[-120:]))

        # a result cannot be combined with other data: every input is bound by its hash
        for key in ("sessions", "obs", "plan", "oracle"):
            tampered = os.path.join(d, "t_" + key + ".dat")
            with open(tampered, "wb") as fh:
                fh.write(open(paths[key], "rb").read() + b"\n")
            rc_of("h_%s.json" % key, 2, **{key: tampered})
        rc_of("e1.json", 2, out=paths["obs"])
        rc_of("e2.json", 2, match=os.path.join(d, "no-such-file"))
        rc_of("e3.json", 2, match=paths["sessions"])
        bad_cfg = os.path.join(d, "bad_cfg.json")
        for body in ({"text_size": 8}, {"text_size_trials": 0}, {"B": 0}, {"B": MAX_B + 1}, {"seed": 1.5},
                     {"mixtures": {}}, {"mixtures": {"a": {"medium": 1}}}, {"mixtures": {"a": {"slow": 0}}},
                     {"mixtures": {"a": {"slow": 10 ** 400}}}, {"mixtures": {"a": {"slow": 1e308, "mid": 1e308}}}):
            with open(bad_cfg, "wb") as fh:
                fh.write(json.dumps(body).encode("utf-8"))
            rc_of("c.json", 2, config=bad_cfg)
        # a malformed match result is an input error (exit 2), never a traceback
        good = json.loads(mbytes.decode("utf-8"))
        for label, edit in (("pair_no_gt", lambda o: o["matching"]["pairs"].__setitem__(0, {"obs": {}})),
                            ("pair_not_dict", lambda o: o["matching"]["pairs"].__setitem__(0, 5)),
                            ("miss_not_dict", lambda o: o["matching"]["miss"].__setitem__(0, 5)),
                            ("split_obs_not_dict", lambda o: o["matching"]["split"][0].__setitem__("obs", [5])),
                            ("merge_gt_not_list", lambda o: o["matching"]["merge"][0].__setitem__("gt", 5)),
                            ("bad_definition", lambda o: o.__setitem__("definition", "bogus")),
                            ("no_definition", lambda o: o.pop("definition"))):
            broken = json.loads(json.dumps(good))
            edit(broken)
            bp = os.path.join(d, "m_%s.dat" % label)
            with open(bp, "wb") as fh:
                fh.write(json.dumps(broken).encode("utf-8"))
            rc_of("m_%s.json" % label, 2, match=bp)
        # a configuration that changes the text size changes the texts
        big = os.path.join(d, "big.json")
        with open(big, "wb") as fh:
            fh.write(json.dumps({"B": 50, "text_size_trials": 15}).encode("utf-8"))
        rc_of("big_out.json", 0, config=big)
        if os.path.exists(os.path.join(d, "big_out.json")):
            o = json.loads(open(os.path.join(d, "big_out.json"), "rb").read().decode("utf-8"))
            # two texts of 15 trials per block; the excluded trials 6 / 7 and 90 discard one text each
            if o["texts"]["n"] != 4 or o["texts"]["discarded"]["n"] != 2 or                     o["texts"]["dropped_trials_by_block"] != {"0": 0, "1": 0, "2": 0}:
                f.append("text_size_trials in the configuration does not change the texts: %r" % (o["texts"],))
        rc_of("no_cfg.json", 0, config=None)
    return f


def _case_per_trial():
    """per_trial: the differences are observation minus ground truth per trial, grouped by scenario and
    speed and clustered by block; excluded, out-of-scope and unrun trials are left out and counted."""
    f = []
    w = _world()
    gt = w["gt"]
    cancel_s3 = lambda i, s: not (w["plan"]["trials"][s["trial_id"]]["scenario"] == "S3" and s["outcome"] == "cancel")
    obs = attach_trials(_obs_from(gt, dwell_delta=lambda i, s: 100 if w["plan"]["trials"][s["trial_id"]]["scenario"] == "S2" else 0,
                                  keep=cancel_s3), w["oracle"])
    plan = {"trials": dict(w["plan"]["trials"], t09999={"block_id": 0, "scenario": "S1", "speed": "slow"})}
    r = per_trial(plan, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 200, 1)
    if r["trials_not_used"] != {"not_run": 1, "excluded": 1, "out_of_scope": 1} or r["n_trials"] != 190:
        f.append("trials not used / n_trials: %r / %r" % (r["trials_not_used"], r["n_trials"]))
    n_out = sum(1 for s in gt if s["trial_id"] in ("t00001", "t00002"))
    if r["n_outside"]["gt"] != n_out:
        f.append("the sessions of excluded and out-of-scope trials should be outside: %r" % r["n_outside"])
    labs = [(x["stratum"]["scenario"], x["stratum"]["speed"]) for x in r["by_stratum"]]
    if labs != [(s, sp) for s in ("S1", "S2", "S3") for sp in SPEEDS]:
        f.append("strata order: %r" % labs)
    for x in r["by_stratum"]:
        sc = x["stratum"]["scenario"]
        nc, ds, ca = x["n_conversions"], x["dwell_sum_ms"], x["n_cancelled"]
        if x["n_blocks"] != 2:
            f.append("each stratum spans 2 blocks: %r" % x["n_blocks"])
        if sc == "S1" and (ds["diff_mean"] != 0 or ds["diff_ci"] != [0, 0] or nc["diff_mean"] != 0):
            f.append("S1 has no error: %r" % x)
        if sc == "S2" and (abs(ds["diff_mean"] - 100 * nc["gt_mean"]) > 1e-9 or ds["diff_mean"] <= 0 or nc["diff_mean"] != 0):
            f.append("S2's summed time should be 100 ms longer per conversion: %r" % ds)
        if sc == "S3" and (abs(ca["diff_mean"] + ca["gt_mean"]) > 1e-9 or abs(nc["diff_mean"] - ca["diff_mean"]) > 1e-9
                           or ca["obs_mean"] != 0):
            f.append("S3's missing cancelled rows should lower both counts: %r / %r" % (nc, ca))
        lo, hi = ds["diff_ci"]
        if lo is None or not lo <= ds["diff_mean"] <= hi:
            f.append("the interval should bracket the mean: %r" % ds)
    if any(x[q]["diff_ci_status"] != "ok" for x in r["by_stratum"] if x["status"] == "ok" for q in PER_TRIAL):
        f.append("two blocks should give diff_ci_status ok")
    one = per_trial({"trials": {"t00003": w["plan"]["trials"]["t00003"]}}, w["oracle"], gt, obs, set(), set(), 50, 1)
    if one["by_stratum"][0]["dwell_sum_ms"]["diff_ci"] != [None, None] or one["by_stratum"][0]["dwell_sum_ms"]["diff_sd"] is not None:
        f.append("one trial in one block gives no interval and no standard deviation")
    if any(one["by_stratum"][0][q]["diff_ci_status"] != "fewer_than_two_blocks" for q in PER_TRIAL):
        f.append("one block should give diff_ci_status fewer_than_two_blocks: %r" % (one["by_stratum"][0],))
    return f


def _case_scope():
    """A match result of scope under_limit: the texts are kept, the long trials' records are absent on
    both sides, per_trial leaves them out; all_trials keeps them; a scope without its list is refused."""
    f = []
    fx = match._cli_fixture()
    long_t = ("t00040", "t00041")
    pb = match._prediction_fixture(fx, long_trials=long_t)
    cfg = json.dumps({"B": 50, "seed": 3}).encode("utf-8")
    outs = {}
    for scope in ("all_trials", "under_limit"):
        mb = match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"], pb, scope))
        outs[scope] = evaluate(mb, fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
    a, u = outs["all_trials"], outs["under_limit"]
    if a["texts"]["n"] != u["texts"]["n"] or u["scope"] != {"name": "under_limit", "n_trials_out_of_scope": 2}:
        f.append("texts should be kept under under_limit: %r / %r" % (a["texts"]["n"], u["scope"]))
    count = lambda out, side: dict((t["text_id"], round(t["conversions_per_trial"] * t["n_trials_in_scope"]))
                                   for t in out["aggregate"][side])
    ga, gu = count(a, "gt"), count(u, "gt")
    diff = dict((k, ga[k] - gu[k]) for k in ga if ga[k] != gu[k])
    if diff != {"b1.1": 2}:
        f.append("the two long trials (block 1, text 1) should be absent on the ground-truth side: %r" % diff)
    # the texts stay whole; a text keeps its trials in scope, and the histogram and the per-trial speed follow
    nts = dict((t["text_id"], t["n_trials_in_scope"]) for t in u["aggregate"]["gt"])
    if nts != dict((k, 6 if k == "b1.1" else 8) for k in nts) or \
            set(t["n_trials_in_scope"] for t in a["aggregate"]["gt"]) != {8} or \
            u["texts"]["trials_in_scope_by_speed"] != {"slow": {"8": 2}, "mid": {"6": 1, "8": 2}, "fast": {"8": 3}} or \
            u["texts"]["n_without_trials_in_scope_by_speed"] != {"slow": 0, "mid": 0, "fast": 0}:
        f.append("n_trials_in_scope / its histogram under under_limit are wrong: %r / %r" % (nts, u["texts"]))
    pz = match._prediction_fixture(fx, long_trials=["t%05d" % k for k in range(31, 39)])
    mz = match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"], pz, "under_limit"))
    z0 = evaluate(mz, fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
    zrow = [t for t in z0["aggregate"]["obs"] if t["text_id"] == "b1.0"][0]
    if z0["texts"]["n_without_trials_in_scope_by_speed"] != {"slow": 0, "mid": 1, "fast": 0} or \
            z0["texts"]["trials_in_scope_by_speed"]["mid"] != {"0": 1, "8": 2} or \
            zrow["n_trials_in_scope"] != 0 or any(zrow[m] is not None for m in MEASURES):
        f.append("a text with no trial in scope must be counted and have None measures: %r / %r" % (z0["texts"], zrow))
    row = [t for t in u["aggregate"]["gt"] if t["text_id"] == "b1.1"][0]
    if row["scope_duration_ms"] >= row["duration_ms"] or row["conversions_per_trial"] * 6 != gu["b1.1"]:
        f.append("the scope duration of a text with trials set aside must be below its span: %r" % (row,))
    # a record that the matching holds for an out-of-scope trial (it normally holds none) is not used
    mo = json.loads(match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"],
                                                pb, "under_limit")).decode("utf-8"))
    mall = json.loads(match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"],
                                                  pb, "all_trials")).decode("utf-8"))
    stray = [s for p in mall["matching"]["pairs"] for s in [p["gt"]] if s.get("trial_id") in long_t]
    if not stray:
        stray = [g for p in mall["matching"]["merge"] for g in p["gt"] if g.get("trial_id") in long_t]
    if not stray:
        f.append("the scope fixture has no ground-truth session in a long trial to inject")
    else:
        mo["matching"]["miss"].append(stray[0])
        z = evaluate(json.dumps(mo).encode("utf-8"), fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
        zg = z["outside_texts"]["gt"]
        if zg["by_reason"].get("out_of_scope") != 1 or zg["by_speed"].get("mid") != u["outside_texts"]["gt"]["by_speed"].get("mid", 0) + 1 \
                or count(z, "gt") != gu:
            f.append("a session of an out-of-scope trial in the matching must be outside (out_of_scope) and unused: %r" % (zg,))
    if u["per_trial"]["trials_not_used"]["out_of_scope"] != 2 or a["per_trial"]["trials_not_used"]["out_of_scope"] != 0:
        f.append("per_trial should leave out the out-of-scope trials: %r" % u["per_trial"]["trials_not_used"])
    mo = json.loads(match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"],
                                                pb, "under_limit")).decode("utf-8"))
    mo["scope"]["long_trials"] = None
    try:
        evaluate(json.dumps(mo).encode("utf-8"), fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
        f.append("a scope without its list of long trials was not refused")
    except InputError:
        pass
    return f


def _case_agreement_intervals():
    """Block-bootstrap intervals for the bias and the limits of agreement."""
    f = []
    w = _world()
    jitter = lambda i, s: 50 + ((i * 13) % 5 - 2) * 10
    _tx, g, o = _aggs(w, _obs_from(w["gt"], dwell_delta=jitter, keep=lambda i, s: i % 9 != 0))
    a = agreement(g, o, 300, 7)
    if json.dumps(a, sort_keys=True) != json.dumps(agreement(g, o, 300, 7), sort_keys=True):
        f.append("the same seed does not give identical bias / limit intervals")
    c = agreement(g, o, 300, 8)
    if all(a[m][k] == c[m][k] for m in MEASURES for k in ("bias_ci", "loa_lo_ci", "loa_hi_ci")):
        f.append("a different seed gives the same bias / limit intervals (the seed has no effect)")
    for m in MEASURES:
        ba = a[m]["bland_altman"]
        for name, point in (("bias", ba["bias"]), ("loa_lo", ba["loa_lo"]), ("loa_hi", ba["loa_hi"])):
            lo, hi = a[m][name + "_ci"]
            if a[m][name + "_ci_status"] != "ok" or lo is None or not (lo <= hi):
                f.append("the %s interval of %s is missing or unordered in a well-behaved world: %r / %r" % (
                    name, m, a[m][name + "_ci"], a[m][name + "_ci_status"]))
            elif not (lo - 1e-9 <= point <= hi + 1e-9):
                f.append("the %s interval of %s does not contain the point estimate %r: %r" % (name, m, point, (lo, hi)))
    # a perfect observation: every difference is 0, so every interval is [0, 0]
    _tx, g0, o0 = _aggs(w, _obs_from(w["gt"]))
    p0 = agreement(g0, o0, 50, 1)["mean_dwell_ms"]
    if any(p0[k] != [0, 0] for k in ("bias_ci", "loa_lo_ci", "loa_hi_ci")):
        f.append("a perfect observation does not give zero-width intervals at 0: %r" % (p0,))
    # the stratified agreement carries the intervals too (2 blocks per speed)
    st = agreement_by_stratum(g, o, 50, 1)["slow"]["mean_dwell_ms"]
    if st["bias_ci_status"] != "ok" or st["loa_lo_ci_status"] != "ok" or st["loa_hi_ci_status"] != "ok":
        f.append("agreement_by_stratum does not carry the bias / limit intervals: %r" % (st,))
    # one block: the point estimates exist, the intervals are withheld and say why
    one = _world(n_blocks=1, per_block=32)
    _t, g1, o1 = _aggs(one, _obs_from(one["gt"], dwell_delta=jitter))
    r = agreement(g1, o1, 50, 1)["mean_dwell_ms"]
    if r["bland_altman"]["loa_lo"] is None or any(
            r[k + "_ci"] != [None, None] or r[k + "_ci_status"] != "fewer_than_two_blocks"
            for k in ("bias", "loa_lo", "loa_hi")):
        f.append("one block must give null intervals with fewer_than_two_blocks: %r" % (r,))
    # one text: the bias exists but the limits do not (n < 2); no text: nothing exists
    two_blocks = [(0, 1.0, 2.0), (1, 1.0, 4.0)]
    r1 = _agree(two_blocks[:1], 20, 1)
    if r1["bias_ci_status"] != "fewer_than_two_blocks":
        f.append("one text (one block) must give bias_ci_status fewer_than_two_blocks: %r" % (r1,))
    if r1["loa_lo_ci_status"] != "no_bland_altman" or r1["loa_hi_ci_status"] != "no_bland_altman" or \
            r1["loa_lo_ci"] != [None, None]:
        f.append("one text must give the limit intervals no_bland_altman: %r" % (r1,))
    r0 = _agree([], 20, 1)
    if any(r0[k + "_ci_status"] != "no_bland_altman" or r0[k + "_ci"] != [None, None]
           for k in ("bias", "loa_lo", "loa_hi")):
        f.append("no texts must give no_bland_altman for every interval: %r" % (r0,))
    # two blocks of one text each: every resample has two texts, so the limits are defined
    r2 = _agree(two_blocks, 100, 1)
    if r2["bias_ci_status"] != "ok" or r2["loa_lo_ci_status"] != "ok" or not r2["bias_ci"][0] <= 2.0 <= r2["bias_ci"][1]:
        f.append("two blocks of one text each must give intervals: %r" % (r2,))
    # a resample without enough texts is undefined (the guard), never a value
    for kind, sample in (("bias", []), ("loa_lo", [[1.0]]), ("loa_hi", [[1.0], []])):
        try:
            _ba_stat(kind)(sample)
            f.append("a resample with too few texts gave a value for %s" % kind)
        except _UndefinedICC:
            pass
    if abs(_ba_stat("loa_hi")([[1.0, 3.0], [5.0]]) - (3.0 + stats.BA_Z * 2.0)) > 1e-12 or \
            abs(_ba_stat("loa_lo")([[1.0, 3.0], [5.0]]) - (3.0 - stats.BA_Z * 2.0)) > 1e-12 or \
            abs(_ba_stat("bias")([[1.0, 3.0], [5.0]]) - 3.0) > 1e-12:
        f.append("the resample statistics are not bias and bias -/+ 1.96 sd")
    # the interval resamples blocks: identical differences give a zero-width interval, and two blocks
    # whose means differ give an interval that reaches both block means
    same = [(0, 0.0, 5.0), (0, 0.0, 5.0), (1, 0.0, 5.0), (1, 0.0, 5.0)]
    if _agree(same, 50, 1)["bias_ci"] != [5.0, 5.0]:
        f.append("identical differences must give a zero-width bias interval")
    spread = [(0, 0.0, 0.0), (0, 0.0, 0.0), (1, 0.0, 10.0), (1, 0.0, 10.0)]
    lo, hi = _agree(spread, 300, 1)["bias_ci"]
    if not (lo == 0.0 and hi == 10.0):
        f.append("a bias interval over two blocks that differ should span both block means: %r" % ((lo, hi),))
    return f


def _syn_texts(layout):
    """{speed: [text]} from {speed: [(block_id, gt_value, obs_value or None)]}; every measure alike."""
    out = {}
    for sp, items in layout.items():
        for k, (b, gv, ov) in enumerate(items):
            out.setdefault(sp, []).append({"text_id": "%s%d" % (sp, k), "block_id": b,
                                           "gt": dict((m, gv) for m in MEASURES),
                                           "obs": dict((m, ov) for m in MEASURES)})
    return out


def _case_mixture_intervals():
    f = []
    w = _world(n_blocks=18)  # 6 blocks of 4 texts per speed (with 2 blocks a seed could not change a percentile)
    fast_tids = set(t for t, i in w["plan"]["trials"].items() if i["speed"] == "fast")
    keep = lambda i, s: not (s["trial_id"] in fast_tids and i % 2 == 0)
    jitter = lambda i, s: 20 + ((i * 7) % 5) * 10
    _tx, g, o = _aggs(w, _obs_from(w["gt"], dwell_delta=jitter, keep=keep))
    sx = stratify(g, o)
    if any("block_id" not in t for ts in sx.values() for t in ts):
        f.append("stratify does not carry the block of every text")
    mixes = dict(DEFAULT_MIXTURES, only_fast={"fast": 5}, only_slow={"slow": 1}, only_mid={"mid": 0.2})
    mb = mixture_bias(sx, mixes, 200, 3)
    if json.dumps(mb, sort_keys=True) != json.dumps(mixture_bias(sx, mixes, 200, 3), sort_keys=True):
        f.append("the same B and seed do not give identical mixture intervals")
    other = mixture_bias(sx, mixes, 200, 4)
    if all(mb["mixtures"][n]["bias_ci"] == other["mixtures"][n]["bias_ci"] for n in mixes):
        f.append("a different seed gives the same mixture intervals (the seed has no effect)")
    # the per-speed bias has one registered interval (agreement_by_stratum's): none here
    if any(set(mb["by_stratum"][s][m]) != {"bias", "n"} for s in SPEEDS for m in MEASURES):
        f.append("a per-stratum entry of mixture_bias must hold only bias and n: %r" % (mb["by_stratum"]["slow"]["mean_dwell_ms"],))
    # a mixture with all weight on one speed has the percentile interval of that speed's replicate biases
    _blocks, reps = _stratified_replicates(sx, 200, 3)
    for s, name in (("slow", "only_slow"), ("mid", "only_mid"), ("fast", "only_fast")):
        for m in MEASURES:
            cell, mix = mb["by_stratum"][s][m], mb["mixtures"][name]
            want = _percentile_interval([r[s][m] for r in reps])
            if mix["bias_ci"][m] != want or mix["bias_ci_status"][m] != "ok" or mix["bias"][m] != cell["bias"]:
                f.append("the mixture with all weight on %s is not that speed's bias with the percentile interval of its "
                         "replicate biases for %s: %r vs %r" % (s, m, (mix["bias"][m], mix["bias_ci"][m]), (cell["bias"], want)))
            lo, hi = mix["bias_ci"][m]
            if lo is None or not (lo - 1e-9 <= cell["bias"] <= hi + 1e-9):
                f.append("the %s interval of %s does not contain its bias or is missing: %r" % (s, m, (cell, mix["bias_ci"][m])))
    for name in DEFAULT_MIXTURES:
        for m in MEASURES:
            lo, hi = mb["mixtures"][name]["bias_ci"][m]
            if mb["mixtures"][name]["bias_ci_status"][m] != "ok" or not (lo - 1e-9 <= mb["mixtures"][name]["bias"][m] <= hi + 1e-9):
                f.append("the interval of the mixture %s for %s does not contain its bias: %r" % (name, m, (lo, hi)))
    # without B the intervals are not requested; the points do not change
    plain = mixture_bias(sx, mixes)
    if plain["mixtures"]["uniform"]["bias_ci_status"]["conversions_per_trial"] != "not_requested" or \
            plain["mixtures"]["uniform"]["bias_ci"]["conversions_per_trial"] != [None, None] or \
            plain["by_stratum"] != mb["by_stratum"] or plain["mixtures"]["uniform"]["bias"] != mb["mixtures"]["uniform"]["bias"]:
        f.append("without B the intervals must be not_requested and the points unchanged")
    for bad in (dict(B=0, seed=1), dict(B=5, seed="a"), dict(B=True, seed=1), dict(B=MAX_B + 1, seed=1)):
        try:
            mixture_bias(sx, mixes, **bad)
            f.append("mixture_bias accepted %r" % (bad,))
        except ValueError:
            pass
    # stratification: slow texts differ by +10 and fast texts by -10 in every block, so any resample
    # that stays inside the speeds gives exactly +10 / -10 and a mixture of the two exactly 0
    syn = _syn_texts({"slow": [(0, 0, 10), (0, 0, 10), (1, 0, 10), (1, 0, 10)],
                      "fast": [(2, 10, 0), (2, 10, 0), (3, 10, 0), (3, 10, 0)]})
    sm = mixture_bias(syn, {"both": {"slow": 1, "fast": 1}, "slow": {"slow": 1}, "fast": {"fast": 1}}, 100, 1)
    if sm["mixtures"]["both"]["bias_ci"]["conversions_per_trial"] != [0.0, 0.0] or \
            sm["mixtures"]["slow"]["bias_ci"]["conversions_per_trial"] != [10.0, 10.0] or \
            sm["mixtures"]["fast"]["bias_ci"]["conversions_per_trial"] != [-10.0, -10.0]:
        f.append("the bootstrap is not stratified (a replicate mixed texts of different speeds): %r" % (sm["mixtures"],))
    # two blocks per speed whose means are 0 and 10: the uniform mixture's interval reaches 0 and 10
    syn2 = _syn_texts({"slow": [(0, 0, 0), (1, 0, 10)], "fast": [(2, 0, 0), (3, 0, 10)]})
    lo, hi = mixture_bias(syn2, {"u": {"slow": 1, "fast": 1}}, 400, 1)["mixtures"]["u"]["bias_ci"]["conversions_per_trial"]
    if not (lo == 0.0 and hi == 10.0):
        f.append("the mixture interval does not span the strata's block means: %r" % ((lo, hi),))
    # withheld intervals and their reasons
    thin = {"slow": sx["slow"][:4], "mid": sx["mid"], "fast": sx["fast"]}  # slow: the 4 texts of one block
    tb = mixture_bias(thin, {"needs_slow": {"slow": 1, "mid": 1}, "mid_only": {"mid": 1},
                             "zero_slow": {"slow": 0, "mid": 1}}, 50, 1)
    if len(set(t["block_id"] for t in thin["slow"])) != 1:
        f.append("the thin-stratum fixture should have one slow block")
    if tb["mixtures"]["needs_slow"]["bias_ci_status"]["conversions_per_trial"] != "fewer_than_two_blocks" or \
            tb["mixtures"]["needs_slow"]["bias_ci"]["conversions_per_trial"] != [None, None]:
        f.append("a needed stratum with one block must withhold the interval: %r" % (tb["mixtures"]["needs_slow"],))
    if tb["mixtures"]["mid_only"]["bias_ci_status"]["conversions_per_trial"] != "ok" or \
            tb["mixtures"]["zero_slow"]["bias_ci_status"]["conversions_per_trial"] != "ok":
        f.append("a stratum that is not needed (weight 0 or absent from the weights) must not withhold the interval")
    nd = mixture_bias(_syn_texts({"slow": [(0, 1, None), (1, 1, None)]}), {"s": {"slow": 1}, "f": {"fast": 1}}, 20, 1)
    if nd["mixtures"]["s"]["bias_ci_status"]["conversions_per_trial"] != "no_data" or \
            nd["mixtures"]["f"]["bias_ci_status"]["conversions_per_trial"] != "no_data":
        f.append("a null bias must give the status no_data: %r" % (nd,))
    # a replicate in which a needed stratum has no text with the measure is undefined: withheld, not dropped
    und = _syn_texts({"slow": [(0, 1, None), (1, 1, 3), (2, 1, 5)]})
    ub = mixture_bias(und, {"s": {"slow": 1}}, 200, 1)
    if ub["mixtures"]["s"]["bias_ci_status"]["conversions_per_trial"] != "undefined_resample" or \
            ub["mixtures"]["s"]["bias_ci"]["conversions_per_trial"] != [None, None]:
        f.append("an undefined replicate must withhold the interval with undefined_resample: %r" % (ub,))
    # a stratum with values in one block only (the other block holds none of the measure) is "fewer_than_two_blocks",
    # not "undefined_resample": only the blocks that hold a value of the measure are counted
    ob1 = mixture_bias(_syn_texts({"slow": [(0, 1, None), (1, 1, 3)]}), {"s": {"slow": 1}}, 200, 1)
    if ob1["mixtures"]["s"]["bias_ci_status"]["conversions_per_trial"] != "fewer_than_two_blocks" or \
            ob1["mixtures"]["s"]["bias_ci"]["conversions_per_trial"] != [None, None]:
        f.append("values in one block only must give fewer_than_two_blocks: %r" % (ob1,))
    # the global random state is not touched
    state = random.getstate()
    mixture_bias(sx, mixes, 20, 1)
    if random.getstate() != state:
        f.append("the global random state was touched by the mixture bootstrap")
    return f


def _case_layer_none():
    f = []
    w = _world()
    gt = w["gt"]
    obs = attach_trials(_obs_from(gt), w["oracle"])
    plan = {"trials": dict(w["plan"]["trials"], t09999={"block_id": 0, "scenario": "S1", "speed": "slow"})}
    base = per_trial(plan, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 50, 1)
    if "outside_layer" in base["trials_not_used"]:
        f.append("per_trial without only_trials must not have an outside_layer count: %r" % (base["trials_not_used"],))
    # trial 1 is excluded, 2 is out of scope, 9999 was not run; 58 of 60 listed trials are used (3..60)
    only = set("t%05d" % k for k in range(1, 61)) | {"t09999"}
    r = per_trial(plan, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 50, 1, only_trials=only)
    if r["trials_not_used"] != {"not_run": 1, "excluded": 1, "out_of_scope": 1, "outside_layer": 190 - 58} or \
            r["n_trials"] != 58:
        f.append("only_trials: trials not used / n_trials: %r / %r" % (r["trials_not_used"], r["n_trials"]))
    n_in = sum(1 for s in gt if s["trial_id"] in only and s["trial_id"] not in ("t00001", "t00002"))
    if r["n_outside"]["gt"] != len(gt) - n_in:
        f.append("the sessions of trials outside the layer should count as outside: %r" % (r["n_outside"],))
    if sum(x["n_trials"] for x in r["by_stratum"]) != 58:
        f.append("the strata should hold the 58 trials in the layer")
    # layers of the plan with nothing used: reported, null, with the layer's own reasons
    plan_l = dict(plan, trials=dict(plan["trials"], t09998={"block_id": 7, "scenario": "S9", "speed": "fast"}))
    only_l = set(plan["trials"]) - {tid for tid, i in plan["trials"].items() if i["scenario"] == "S3"}
    rl = per_trial(plan_l, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 50, 1, only_trials=only_l)
    bl = per_trial(plan_l, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 50, 1)
    labs_l = [(x["stratum"]["scenario"], x["stratum"]["speed"]) for x in rl["by_stratum"]]
    if labs_l != [(s_, sp) for s_ in ("S1", "S2", "S3") for sp in SPEEDS] + [("S9", "fast")]:
        f.append("one row per layer of the plan, in scenario then speed order: %r" % (labs_l,))
    for x in rl["by_stratum"]:
        k = (x["stratum"]["scenario"], x["stratum"]["speed"])
        mine = [t for t, i in plan_l["trials"].items() if (i["scenario"], i["speed"]) == k]
        if k[0] in ("S3", "S9"):
            want_nu = {"not_run": len(mine) if k[0] == "S9" else 0, "excluded": 0, "out_of_scope": 0,
                       "outside_layer": 0 if k[0] == "S9" else len(mine)}
            if x["status"] != "no_trial" or x["n_trials"] != 0 or x["n_blocks"] != 0 or x["n_dwell_zero"] != 0 or \
                    any(x[q] is not None for q in PER_TRIAL) or x["trials_not_used"] != want_nu:
                f.append("an empty layer %r should be no_trial with null quantities and its own counts %r: %r" % (
                    k, want_nu, x))
        else:
            nu = x["trials_not_used"]
            if x["status"] != "ok" or x["n_trials"] < 1 or any(x[q] is None for q in PER_TRIAL):
                f.append("a layer with trials should be ok with values: %r" % (x,))
            if nu["outside_layer"] != 0 or nu["not_run"] + nu["excluded"] + nu["out_of_scope"] > 2 or \
                    x["n_trials"] + sum(nu.values()) != len(mine):
                f.append("a layer of S1 / S2 should count its own trials only: %r" % (x,))
    if rl["trials_not_used"]["outside_layer"] != sum(x["trials_not_used"]["outside_layer"] for x in rl["by_stratum"]) or \
            rl["trials_not_used"]["not_run"] != sum(x["trials_not_used"]["not_run"] for x in rl["by_stratum"]) or \
            rl["trials_not_used"]["excluded"] != 1 or rl["trials_not_used"]["out_of_scope"] != 1:
        f.append("the layer counts should add up to the total counts: %r" % (rl["trials_not_used"],))
    # a layer that has trials keeps its values, whether or not an empty layer is shown beside it
    vb = dict(((x["stratum"]["scenario"], x["stratum"]["speed"]), x) for x in bl["by_stratum"])
    for x in rl["by_stratum"]:
        k = (x["stratum"]["scenario"], x["stratum"]["speed"])
        if x["status"] == "ok" and dict((q, x[q]) for q in PER_TRIAL) != dict((q, vb[k][q]) for q in PER_TRIAL):
            f.append("the values of a layer with trials changed: %r" % (k,))
    if [x["status"] for x in bl["by_stratum"]] != ["ok"] * 9 + ["no_trial"]:
        f.append("status of the layers without only_trials: %r" % ([x["status"] for x in bl["by_stratum"]],))
    e = per_trial(plan, w["oracle"], gt, obs, set(), set(), 50, 1, only_trials=set())
    if e["n_trials"] != 0 or e["trials_not_used"]["outside_layer"] != 192:
        f.append("an empty only_trials should leave no trial: %r" % (e["trials_not_used"],))
    if len(e["by_stratum"]) != len(base["by_stratum"]) or \
            any(x["status"] != "no_trial" or x["n_trials"] != 0 or x["n_blocks"] != 0 or x["n_dwell_zero"] != 0 or
                any(x[q] is not None for q in PER_TRIAL) or
                sum(x["trials_not_used"].values()) != sum(
                    1 for i in plan["trials"].values()
                    if (i["scenario"], i["speed"]) == (x["stratum"]["scenario"], x["stratum"]["speed"]))
                for x in e["by_stratum"]):
        f.append("an only_trials that leaves nothing should still give a no_trial row per layer of the plan, with the "
                 "layer's own counts: %r" % (e["by_stratum"],))
    full = per_trial(plan, w["oracle"], gt, obs, {"t00001"}, {"t00002"}, 50, 1, only_trials=set(plan["trials"]))
    strip = lambda rows: [dict((k, v) for k, v in x.items() if k != "trials_not_used") for x in rows]
    if strip(full["by_stratum"]) != strip(base["by_stratum"]) or full["trials_not_used"]["outside_layer"] != 0 or \
            [x["trials_not_used"] for x in full["by_stratum"]] != \
            [dict(x["trials_not_used"], outside_layer=0) for x in base["by_stratum"]]:
        f.append("only_trials covering every trial must change nothing but the extra count")
    # through evaluate: the scope's list of trials in none of the layers
    fx = match._cli_fixture()
    cfg = json.dumps({"B": 30, "seed": 3}).encode("utf-8")
    mo = json.loads(match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"],
                                                fx["config"])).decode("utf-8"))

    def run(scope):
        m2 = json.loads(json.dumps(mo))
        if scope == "__drop__":
            m2.pop("scope", None)
        else:
            m2["scope"] = scope
        return evaluate(json.dumps(m2).encode("utf-8"), fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)

    base_scope = {"name": "all_trials", "long_trials": None}
    for label, scope in (("a missing scope", "__drop__"), ("a scope without the key", dict(base_scope)),
                         ("a null list", dict(base_scope, layer_none_trials=None))):
        if run(scope)["per_trial_layer_none"] is not None:
            f.append("%s must give per_trial_layer_none null" % label)
    lst = ["t%05d" % k for k in range(1, 46)]
    out = run(dict(base_scope, layer_none_trials=lst))
    pl, pa = out["per_trial_layer_none"], out["per_trial"]
    if pl is None or pl["n_trials"] + pl["trials_not_used"]["outside_layer"] != pa["n_trials"] or \
            pl["n_trials"] != sum(x["n_trials"] for x in pl["by_stratum"]) or "outside_layer" in pa["trials_not_used"]:
        f.append("per_trial_layer_none does not split the used trials of per_trial: %r / %r" % (
            None if pl is None else pl["trials_not_used"], pa["trials_not_used"]))
    if pl is not None and not (0 < pl["n_trials"] < pa["n_trials"]):
        f.append("the layer-none view should hold some but not all trials: %r" % (pl["n_trials"],))
    if json.dumps(out, sort_keys=True) != json.dumps(run(dict(base_scope, layer_none_trials=lst)), sort_keys=True):
        f.append("evaluate with layer_none_trials is not deterministic")
    if run(dict(base_scope, layer_none_trials=[]))["per_trial_layer_none"]["n_trials"] != 0:
        f.append("an empty list should give an empty view, not null")
    for bad in ([1, 2], "t00001", {"a": 1}, ["t00001", None], 0, True):
        try:
            run(dict(base_scope, layer_none_trials=bad))
            f.append("a malformed layer_none_trials %r was accepted" % (bad,))
        except InputError:
            pass
    # the uncontaminated view: its own list, independent of layer_none_trials
    for label, scope in (("a missing scope", "__drop__"), ("a scope without the key", dict(base_scope)),
                         ("a null list", dict(base_scope, uncontaminated_trials=None))):
        if run(scope)["per_trial_uncontaminated"] is not None:
            f.append("%s must give per_trial_uncontaminated null" % label)
    unc = ["t%05d" % k for k in range(31, 61)]
    both = run(dict(base_scope, layer_none_trials=lst, uncontaminated_trials=unc))
    pu = both["per_trial_uncontaminated"]
    if pu is None or pu["n_trials"] != 30 or pu["trials_not_used"]["outside_layer"] != pa["n_trials"] - 30 or \
            pu["n_trials"] != sum(x["n_trials"] for x in pu["by_stratum"]):
        f.append("per_trial_uncontaminated does not use exactly the listed trials: %r" % (
            None if pu is None else (pu["n_trials"], pu["trials_not_used"]),))
    if both["per_trial_layer_none"] != out["per_trial_layer_none"] or both["per_trial"] != out["per_trial"]:
        f.append("per_trial_layer_none / per_trial must not depend on uncontaminated_trials")
    if run(dict(base_scope, uncontaminated_trials=unc))["per_trial_layer_none"] is not None:
        f.append("an uncontaminated list alone must not fill per_trial_layer_none")
    if json.dumps(both, sort_keys=True) != json.dumps(run(dict(base_scope, layer_none_trials=lst, uncontaminated_trials=unc)),
                                                       sort_keys=True):
        f.append("evaluate with uncontaminated_trials is not deterministic")
    if run(dict(base_scope, uncontaminated_trials=[]))["per_trial_uncontaminated"]["n_trials"] != 0:
        f.append("an empty uncontaminated list should give an empty view, not null")
    for bad in ([1, 2], "t00001", {"a": 1}, ["t00001", None], 0, True):
        try:
            run(dict(base_scope, uncontaminated_trials=bad))
            f.append("a malformed uncontaminated_trials %r was accepted" % (bad,))
        except InputError:
            pass
    # through match.py: the list of the prediction fixture (t00016 carried out, t00017 received a carry,
    # t00021 and t00017 closing key) feeds the view; the closing-key trial t00021 is in it, t00016 / t00017 are not
    pb = match._prediction_fixture(fx, carried=("t00016",), none_started=("t00017",), closing=("t00021", "t00017"))
    mp = json.loads(match.render(match.evaluate(fx["sessions"], "all", fx["obs"], fx["plan"], fx["oracle"], fx["config"],
                                                pb, "all_trials")).decode("utf-8"))
    ev = evaluate(json.dumps(mp).encode("utf-8"), fx["sessions"], fx["obs"], fx["plan"], fx["oracle"], cfg)
    pu, pl, pa = ev["per_trial_uncontaminated"], ev["per_trial_layer_none"], ev["per_trial"]
    if pu is None or pl is None or pu["n_trials"] != pa["n_trials"] - 2 or pu["trials_not_used"]["outside_layer"] != 2 or \
            pl["n_trials"] != pa["n_trials"] - 3 or pu["n_trials"] != pl["n_trials"] + 1:
        f.append("the uncontaminated view should hold every used trial but the two carry trials (the layer-none view "
                 "one fewer, the closing-key trial): %r / %r" % (None if pu is None else pu["trials_not_used"],
                                                                 None if pl is None else pl["trials_not_used"]))
    return f


CASES = [
    ("texts (bundling in plan order, dropped remainder counted, one speed per text, errors)", _case_layout),
    ("per trial: differences by scenario and speed, clustered by block", _case_per_trial),
    ("scope: under_limit keeps the texts and leaves the long trials out", _case_scope),
    ("measure definitions (both sides, attribution to trials, None when not computable)", _case_definitions),
    ("recovery: a dwell bias appears in the mean conversion time only", _case_recovery),
    ("perfect agreement gives ICC 1; shuffling lowers the ICC (negative control)", _case_perfect_and_shuffle),
    ("positive control: the bias moves with the mixture ratio", _case_mixture),
    ("same seed gives byte-identical results; not computable cases are None", _case_seed_and_bytes),
    ("intervals for the bias and the limits of agreement (block bootstrap, statuses)", _case_agreement_intervals),
    ("intervals for the mixture biases (stratified block bootstrap, statuses)", _case_mixture_intervals),
    ("per_trial restricted to a list of trials (only_trials / per_trial_layer_none / per_trial_uncontaminated)", _case_layer_none),
    ("CLI (hash binding, byte-identical output, exit 2)", _case_cli),
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
    ap = argparse.ArgumentParser(description="Level-2 agreement of text-level aggregates (ground truth against observation)")
    ap.add_argument("--match")
    ap.add_argument("--sessions")
    ap.add_argument("--obs")
    ap.add_argument("--plan")
    ap.add_argument("--oracle")
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.match and args.sessions and args.obs and args.plan and args.oracle and args.out):
        ap.error("--match / --sessions / --obs / --plan / --oracle / --out are required (--config is optional)")
    for inp in (args.match, args.sessions, args.obs, args.plan, args.oracle, args.config):
        if inp and match._same_file(inp, args.out):
            print("l2: --out names the same file as an input (%s)" % inp, file=sys.stderr)
            return 2
    try:
        out = evaluate(match._read(args.match, "match"), match._read(args.sessions, "sessions"),
                       match._read(args.obs, "obs"), match._read(args.plan, "plan"),
                       match._read(args.oracle, "oracle"),
                       None if args.config is None else match._read(args.config, "config"))
        match._write_atomic(args.out, render(out))
    except (InputError, ValueError) as e:
        print("l2: %s" % e, file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, IndexError) as e:  # a malformed input that no check caught
        print("l2: malformed input (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    except OSError as e:
        print("l2: could not write output (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    print("l2: %d texts (%s), %d discarded; pooled ICC(2,1) mean dwell %s, conversions %s; "
          "by speed (primary), mean dwell %s" % (
              out["texts"]["n"], ", ".join("%s %d" % (s, n) for s, n in out["texts"]["n_by_speed"].items()),
              out["texts"]["discarded"]["n"],
              _fmt(out["agreement"]["mean_dwell_ms"]["icc21"]), _fmt(out["agreement"]["conversions_per_trial"]["icc21"]),
              ", ".join("%s %s" % (sp, _fmt(v["mean_dwell_ms"]["icc21"])) for sp, v in out["agreement_by_stratum"].items())))
    return 0


def _fmt(v):
    return "n/a" if v is None else "%.3f" % v


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
