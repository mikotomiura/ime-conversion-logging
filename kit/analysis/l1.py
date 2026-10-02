#!/usr/bin/env python3
# kit: selftest
"""Level-1 (L1) agreement measures computed from the 1:1 pairs that match.py produces: the error of
the observed composition duration (dwell_ms), agreement on abandonment, agreement on the number of
navigation key presses, a check of the 20-second cut-off, and a report of how often the executor's
plan disagreed with the ground truth it was compared against. Uses only the Python standard library
(every statistic comes from stats.py and none is reimplemented here).

  l1.py --match M.json --sessions S.json --plan PLAN --oracle ORACLE [--config CFG.json] --out OUT.json
  l1.py --selftest      fast default (a few seconds)

Inputs: M.json is match.py's output (its definition, cell, and input hashes are read from it),
S.json is gt_sessions.py's output (the sessions of that definition, and the oracle agreement), PLAN
and ORACLE are the files those two were built from. The hashes recorded in M.json and the plan hash
recorded in the oracle header must agree with the files given; otherwise the run stops (unrelated
inputs are never combined). The measures cover the matching of M.json, so they cover the trials of its
scope (match.py --scope; the output repeats the scope's name). S.json may also be the prediction in
the form of the ground truth (spec_predict.py's as_sessions_bytes), which carries no oracle agreement.

Measures (each is computed per stratum -- (cell, scenario, speed) --, per speed -- (cell, speed), pooled
over the scenarios -- and pooled over all pairs):
  dwell_ms     the error is  observed - ground truth  (a positive bias means the observation is longer);
               ground-truth dwell = end_wall_ms - start_wall_ms. Reported: the median absolute error with
               a cluster-bootstrap interval, and Bland-Altman bias, limits of agreement, and
               cluster-bootstrap intervals for the bias and for each limit. The resampling unit is the block, because trials
               in one block share the state of the input method and are not independent. A pair whose
               observed dwell_ms is 0 is not a measurement (a clock that moved backwards yields 0), so it
               is left out of every error figure and counted separately (n_zero_excluded).
  abandon      ground truth = the session ended in a cancel; observation = the row's abandon flag.
               Reported: the 2 x 2 table (rows = ground truth, columns = observation, index 1 = abandon),
               Cohen's kappa with a cluster-bootstrap interval (blocks; withheld, with the reason, when
               kappa is undefined in the sample or in any resample -- inside one (scenario, speed)
               stratum the ground truth has mostly one outcome, so kappa is read per speed), and the raw
               agreement with a Wilson interval. The same figures are also
               reported, as a diagnostic, separately for sessions with and without a commit that did
               not close the session because the composition continued (n_continued_commits > 0);
               pairs whose session lacks that field are counted as n_unknown.
  nav          exact-match rate with a Wilson interval, and the distribution of the difference
               (observed - true). The true count is the session's nav_true_cand when it is not null
               (sources with a candidate window: presses while the window was open), otherwise nav_true.
  truncation   the observer stops a composition once its elapsed time reaches limit_ms (default 20000)
               and emits no row, so a session of at least limit_ms is not expected to produce one.
               Reports, over the sessions that entered the matching (pairs, split, merge, miss), how many
               such sessions exist and how many of them nevertheless appear in a pair, split, or merge
               (with their ground-truth durations); the long sessions the matcher excluded are counted
               separately (n_long_excluded); also the number of observed rows of at least limit_ms.
               Reported overall and per stratum (a false positive has no session, so it enters the
               overall count only).
  oracle       the plan-versus-ground-truth disagreements, by type, each with a Wilson interval, overall
               and per stratum (the planned trials of the stratum are the denominator). These sessions
               are reported, never removed from the evaluation. The oracle agreement is built from
               every trial of the oracle and for every session definition, not only the definition
               matched here.
  zero_dwell_rows
               the observed rows whose dwell_ms is 0, counted in every bucket (pairs, split, merge,
               false positives), overall and per stratum for the buckets that carry a session. In the
               matching such a row has zero length, cannot overlap a session of positive length, and
               therefore falls into the false positives and never into a pair. The pre-registration
               keeps this treatment: such a row stays in the matching as a zero-length interval, so it
               counts against precision, and the number of such rows is reported.

Configuration CFG (JSON): {"B": 2000, "seed": 1, "limit_ms": 20000}; B is at most 100000. Keys starting with "_" are
annotations and are not read.
Written as estimation, not significance testing. A quantity that cannot be computed (no pairs, fewer
than two blocks, zero variance) is null, never 0. Randomness uses only random.Random(seed) (through
stats.cluster_bootstrap), so identical input always yields identical output bytes (sorted keys, LF,
UTF-8, no BOM).

Exit status: 0 on success; 2 if an input cannot be read, is inconsistent or malformed, or usage is
incorrect.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import gt_sessions  # noqa: E402  reuses its oracle parser and InputError
import match  # noqa: E402  reuses its parsers, output writer, and interval helpers
import stats  # noqa: E402  every statistic (Wilson, bootstrap, Bland-Altman, kappa) comes from here

InputError = gt_sessions.InputError
DEFAULT_B = 2000
DEFAULT_SEED = 1
DEFAULT_LIMIT_MS = 20000
MAX_B = 100000


# ---------------------------------------------------------------- measures

def _gt_dwell(s):
    return s["end_wall_ms"] - s["start_wall_ms"]


def _block_key(block_of, gt):
    if callable(block_of):
        return block_of(gt)
    return block_of.get(gt.get("trial_id"))


def _ci(iv):
    return [iv[0], iv[1]]


def _clusters(by_block):
    """The per-block lists in a fixed order (a block that is None sorts last)."""
    return [by_block[k] for k in sorted(by_block, key=lambda k: (k is None, str(k)))]


def _flat_mean(cs):
    return statistics.fmean(v for c in cs for v in c)


def _flat_median_abs(cs):
    return statistics.median(abs(v) for c in cs for v in c)


def _flat_loa(sign):
    def stat(cs):
        vals = [v for c in cs for v in c]
        return statistics.fmean(vals) + sign * stats.BA_Z * statistics.stdev(vals)
    return stat


class _Undefined(Exception):
    """Raised inside a resample whose statistic is undefined (so no other error is hidden)."""


def _kappa_of(cs):
    t = [[0, 0], [0, 0]]
    for c in cs:
        for g, o in c:
            t[g][o] += 1
    k = stats.cohen_kappa(t)
    if k is None:
        raise _Undefined()
    return k


def dwell_error(pairs, block_of, B, seed):
    """The error of the observed dwell_ms against the ground-truth duration, over 1:1 pairs
    ({"gt", "obs", "iou"}). The difference is observed - ground truth.

    block_of: a function taking a ground-truth session and returning its block, or a dict from
    trial_id to block. Pairs are resampled block by block.
    Returns {"n", "n_zero_excluded", "n_blocks", "mae", "mae_ci", "bland_altman", "bias_ci",
    "loa_lo_ci", "loa_hi_ci"}: the last two are block-bootstrap intervals for the lower and the upper
    limit of agreement ([null, null] with fewer than two blocks; every resample of two or more blocks
    holds two or more pairs, so the standard deviation is always defined). A pair whose
    observed dwell_ms is 0 is excluded from n and from every figure, and counted in n_zero_excluded.
    """
    obs, gt, by_block, n_zero = [], [], {}, 0
    for p in pairs:
        o = p["obs"]["dwell_ms"]
        if o == 0:
            n_zero += 1
            continue
        g = _gt_dwell(p["gt"])
        obs.append(o)
        gt.append(g)
        by_block.setdefault(_block_key(block_of, p["gt"]), []).append(o - g)
    clusters = _clusters(by_block)
    return {
        "n": len(obs),
        "n_zero_excluded": n_zero,
        "n_blocks": len(clusters),
        "mae": stats.median_abs_error(obs, gt),
        "mae_ci": _ci(stats.cluster_bootstrap(clusters, _flat_median_abs, B, seed)),
        "bland_altman": stats.bland_altman(obs, gt),
        "bias_ci": _ci(stats.cluster_bootstrap(clusters, _flat_mean, B, seed)),
        "loa_lo_ci": _ci(stats.cluster_bootstrap(clusters, _flat_loa(-1), B, seed)),
        "loa_hi_ci": _ci(stats.cluster_bootstrap(clusters, _flat_loa(1), B, seed)),
    }


def abandon_agreement(pairs, block_of=None, B=DEFAULT_B, seed=DEFAULT_SEED):
    """Agreement on abandonment over 1:1 pairs. Ground truth is positive when the session's outcome is
    "cancel"; the observation is positive when the row's abandon flag is true.
    Returns {"table", "n", "kappa", "kappa_ci", "kappa_ci_status", "agree", "agree_ci"}; table =
    [[tn, fp], [fn, tp]] with rows the ground truth and columns the observation. kappa_ci is a
    block-bootstrap interval when block_of is given (as for dwell_error); it is [null, null] and
    kappa_ci_status says why when kappa is undefined ("no_kappa": only one outcome on both sides),
    with fewer than two blocks ("fewer_than_two_blocks"), when any resample has an undefined kappa
    ("undefined_resample"; the interval is then withheld rather than computed from the other
    resamples) or without block_of ("not_requested")."""
    table = [[0, 0], [0, 0]]
    by_block = {}
    for p in pairs:
        g = 1 if p["gt"].get("outcome") == "cancel" else 0
        o = 1 if p["obs"].get("abandon") is True else 0
        table[g][o] += 1
        if block_of is not None:
            by_block.setdefault(_block_key(block_of, p["gt"]), []).append((g, o))
    n = sum(sum(r) for r in table)
    x = table[0][0] + table[1][1]
    kappa = stats.cohen_kappa(table)
    ci, status = (None, None), "ok"
    if kappa is None:
        status = "no_kappa"
    elif block_of is None:
        status = "not_requested"
    elif len(by_block) < 2:
        status = "fewer_than_two_blocks"
    else:
        try:
            ci = stats.cluster_bootstrap(_clusters(by_block), _kappa_of, B, seed)
        except _Undefined:
            status = "undefined_resample"
    return {"table": table, "n": n, "kappa": kappa, "kappa_ci": _ci(ci), "kappa_ci_status": status,
            "agree": (x / n if n else None), "agree_ci": _ci(stats.wilson(x, n))}


def abandon_by_continued_commit(pairs):
    """Agreement on abandonment over 1:1 pairs, split by whether the ground-truth session held a
    commit that did not close it because the composition continued within the rule's window
    (gt_sessions.py's n_continued_commits > 0). With live conversion the input method commits the
    head of a reading while the rest is still being typed; when the rest is then cancelled, the
    session is a cancel on the ground-truth side. This is a diagnostic split of the abandon measure,
    which it does not replace.
    Returns {"with", "without", "n_unknown"}: "with" and "without" have the form of
    abandon_agreement; n_unknown counts the pairs whose session carries no n_continued_commits (a
    non-negative integer), which enter neither stratum."""
    with_c, without_c, unknown = [], [], 0
    for p in pairs:
        k = p["gt"].get("n_continued_commits")
        if not (match._is_int(k) and k >= 0):
            unknown += 1
        elif k > 0:
            with_c.append(p)
        else:
            without_c.append(p)
    return {"with": abandon_agreement(with_c), "without": abandon_agreement(without_c), "n_unknown": unknown}


def _nav_true(gt):
    c = gt.get("nav_true_cand")
    if c is not None:
        return c
    v = gt.get("nav_true")
    if v is None:
        raise ValueError("a ground-truth session has neither nav_true_cand nor nav_true")
    return v


def nav_agreement(pairs):
    """Agreement on the number of navigation key presses over 1:1 pairs. The true count is nav_true_cand
    when the session has one that is not null, otherwise nav_true.
    Returns {"n", "exact", "exact_ci", "diff_hist"}; diff_hist = [[observed - true, count], ...] in
    ascending order of the difference."""
    hist, x, n = {}, 0, 0
    for p in pairs:
        d = p["obs"]["nav"] - _nav_true(p["gt"])
        hist[d] = hist.get(d, 0) + 1
        n += 1
        x += 1 if d == 0 else 0
    return {"n": n, "exact": (x / n if n else None), "exact_ci": _ci(stats.wilson(x, n)),
            "diff_hist": [[d, hist[d]] for d in sorted(hist)]}


def _label(plan_trials, cell, tid):
    info = plan_trials.get(tid)
    return (cell, info["scenario"], info["speed"]) if info else (cell, None, None)


def _label_key(lab):
    return tuple((x is None, str(x)) for x in lab)


def _stratum(lab):
    return dict(zip(["cell", "scenario", "speed"], lab))


def _bucket_rows(match_result):
    """Every observed row of a matching as (bucket, ground-truth session or None, row)."""
    out = [("pairs", p["gt"], p["obs"]) for p in match_result["pairs"]]
    out += [("split", s["gt"], o) for s in match_result["split"] for o in s["obs"]]
    out += [("merge", m["gt"][0] if m["gt"] else None, m["obs"]) for m in match_result["merge"]]
    out += [("false_pos", None, r) for r in match_result["false_pos"]]
    return out


def truncation_check(gt_sessions, match_result, limit_ms=DEFAULT_LIMIT_MS, label=None):
    """The check of the cut-off. The observer stops a composition when its elapsed time reaches
    limit_ms and emits no row, so a ground-truth session of at least limit_ms should not produce one.
    Over the sessions that entered the matching (a pair, split, merge, or miss of match_result) it
    counts the long ones (dwell >= limit_ms) and those that nevertheless appear in a pair, split, or
    merge; a long session that the matcher excluded is counted in n_long_excluded.
    Returns {"limit_ms", "n_long", "n_long_excluded", "n_long_with_row", "with_row_gt_dwell_ms",
    "n_obs_over_limit", "n_obs_over_limit_false_pos"}; with_row_gt_dwell_ms lists the ground-truth
    durations of the long sessions that have a row (ascending); n_obs_over_limit counts the observed
    rows (in any bucket) whose dwell_ms is at least limit_ms, of which n_obs_over_limit_false_pos are
    false positives. n_long_with_row and n_obs_over_limit are expected to be 0.
    label: an optional function taking a ground-truth session and returning its stratum tuple; when
    given, "by_stratum" holds the same counts per stratum (the false positives, which have no
    session, are left out of it; a merged row is attributed to its first session)."""
    rowed = set()
    for p in match_result["pairs"]:
        rowed.add(match._dump(p["gt"]))
    for s in match_result["split"]:
        rowed.add(match._dump(s["gt"]))
    for m in match_result["merge"]:
        for g in m["gt"]:
            rowed.add(match._dump(g))
    entered = set(rowed)
    for s in match_result["miss"]:
        entered.add(match._dump(s))

    def is_long(s):
        return s.get("end_wall_ms") is not None and _gt_dwell(s) >= limit_ms

    def counts(sessions, rows):
        long_s = [s for s in sessions if is_long(s)]
        inn = [s for s in long_s if match._dump(s) in entered]
        with_row = sorted(_gt_dwell(s) for s in inn if match._dump(s) in rowed)
        return {"n_long": len(inn), "n_long_excluded": len(long_s) - len(inn), "n_long_with_row": len(with_row),
                "with_row_gt_dwell_ms": with_row, "n_obs_over_limit": sum(1 for r in rows if r["dwell_ms"] >= limit_ms)}

    brows = _bucket_rows(match_result)
    out = {"limit_ms": limit_ms}
    out.update(counts(gt_sessions, [r for _b, _g, r in brows]))
    out["n_obs_over_limit_false_pos"] = sum(1 for r in match_result["false_pos"] if r["dwell_ms"] >= limit_ms)
    if label is not None:
        labs = {label(s) for s in gt_sessions}
        labs |= {label(g) for _b, g, _r in brows if g is not None}
        by = []
        for lab in sorted(labs, key=_label_key):
            row = {"stratum": _stratum(lab)}
            row.update(counts([s for s in gt_sessions if label(s) == lab],
                              [r for _b, g, r in brows if g is not None and label(g) == lab]))
            by.append(row)
        out["by_stratum"] = by
    return out


def zero_dwell_rows(match_result, label=None):
    """The observed rows whose dwell_ms is 0, counted in every bucket of match_result.
    Returns {"n", "in_pairs", "in_split", "in_merge", "in_false_pos"}; with label (a function taking a
    ground-truth session and returning its stratum tuple) also "by_stratum", one entry
    {"stratum", "n", "in_pairs", "in_split", "in_merge"} per stratum that has such a row (a false
    positive carries no session and is counted overall only)."""
    tot = {"pairs": 0, "split": 0, "merge": 0, "false_pos": 0}
    per = {}
    for b, g, r in _bucket_rows(match_result):
        if r["dwell_ms"] != 0:
            continue
        tot[b] += 1
        if label is not None and g is not None:
            d = per.setdefault(label(g), {"pairs": 0, "split": 0, "merge": 0})
            d[b] += 1
    out = {"n": sum(tot.values()), "in_pairs": tot["pairs"], "in_split": tot["split"],
           "in_merge": tot["merge"], "in_false_pos": tot["false_pos"]}
    if label is not None:
        out["by_stratum"] = [{"stratum": _stratum(lab), "n": sum(per[lab].values()), "in_pairs": per[lab]["pairs"],
                              "in_split": per[lab]["split"], "in_merge": per[lab]["merge"]}
                             for lab in sorted(per, key=_label_key)]
    return out


def _rate_ci(x, n):
    """(rate, Wilson interval) of x / n; both null when n is 0 or smaller than x (a stratum whose
    denominator is unknown)."""
    if n <= 0 or x > n:
        return None, [None, None]
    return x / n, _ci(stats.wilson(x, n))


def _oracle_counts(n, dis, by_type):
    types = {}
    for t in sorted(by_type):
        rate, ci = _rate_ci(by_type[t], n)
        types[t] = {"n": by_type[t], "rate": rate, "ci": ci}
    rate, ci = _rate_ci(dis, n)
    return {"n_trials": n, "n_disagree": dis, "disagree_rate": rate, "disagree_ci": ci, "by_type": types}


def oracle_report(oracle_agreement, plan_trials=None, cell=None):
    """The plan-versus-ground-truth disagreements (gt_sessions.py's oracle_agreement): the count and
    rate of disagreements, and each type's count and rate, with Wilson intervals. The disagreeing
    trials stay in the evaluation. The report covers every trial of the oracle, for every session
    definition.
    With plan_trials ({trial_id: {"block_id", "scenario", "speed"}}) and cell, "by_stratum" gives the
    same figures per stratum: the denominator is the number of planned trials of the stratum, and a
    disagreeing trial that is not in the plan falls into the stratum (cell, null, null), whose
    denominator is 0 (its rates are null)."""
    if not isinstance(oracle_agreement, dict):
        raise ValueError("oracle_agreement must be an object")
    if not oracle_agreement.get("plan_available"):
        nt = oracle_agreement.get("n_trials")
        if nt is not None and not match._is_int(nt):
            raise ValueError("oracle_agreement: n_trials must be an integer")
        return {"plan_available": False, "n_trials": nt}
    try:
        n, agree, by_type = oracle_agreement["n_trials"], oracle_agreement["n_agree"], oracle_agreement["by_type"]
    except KeyError as e:
        raise ValueError("oracle_agreement is missing %s" % e)
    if not (match._is_int(n) and match._is_int(agree) and 0 <= agree <= n):
        raise ValueError("oracle_agreement: n_trials / n_agree must be integers with 0 <= n_agree <= n_trials")
    ua = oracle_agreement.get("unassigned_sessions")
    if ua is not None and not (match._is_int(ua) and ua >= 0):
        raise ValueError("oracle_agreement: unassigned_sessions must be a non-negative integer")
    if not isinstance(by_type, dict) or any(k not in gt_sessions.MISMATCH_TYPES or not match._is_int(v) or v < 0
                                            for k, v in by_type.items()):
        raise ValueError("oracle_agreement: by_type must map known mismatch types to counts")
    dis = n - agree
    out = {"plan_available": True, "n_agree": agree, "unassigned_sessions": oracle_agreement.get("unassigned_sessions"),
           "removed_from_evaluation": False}
    out.update(_oracle_counts(n, dis, by_type))
    if plan_trials is not None:
        mism = oracle_agreement.get("mismatches")
        if not isinstance(mism, list):
            raise ValueError("oracle_agreement is missing mismatches")
        planned, dis_by = {}, {}
        for tid in plan_trials:
            lab = _label(plan_trials, cell, tid)
            planned[lab] = planned.get(lab, 0) + 1
        for m in mism:
            lab = _label(plan_trials, cell, m["trial_id"])
            d = dis_by.setdefault(lab, {})
            d[m["type"]] = d.get(m["type"], 0) + 1
        by = []
        for lab in sorted(set(planned) | set(dis_by), key=_label_key):
            c = dis_by.get(lab, {})
            row = {"stratum": _stratum(lab)}
            row.update(_oracle_counts(planned.get(lab, 0), sum(c.values()), c))
            by.append(row)
        out["by_stratum"] = by
    return out


def _measures(pairs, block_of, B, seed):
    return {"dwell": dwell_error(pairs, block_of, B, seed), "abandon": abandon_agreement(pairs, block_of, B, seed),
            "abandon_by_continued_commit": abandon_by_continued_commit(pairs), "nav": nav_agreement(pairs)}


def _speed_key(lab):
    sp = lab[1]
    return (lab[0] is None, str(lab[0]), match.SPEEDS.index(sp) if sp in match.SPEEDS else len(match.SPEEDS))


def l1_report(match_result, gt_sessions, oracle_agreement, plan_trials, cell, B, seed, limit_ms=DEFAULT_LIMIT_MS):
    """All the L1 measures, per stratum (cell, scenario, speed) and pooled.

    match_result: match.py's "matching" object (with its miss list). gt_sessions: the sessions of the definition that was
    matched. plan_trials: {trial_id: {"block_id", "scenario", "speed"}} (match.parse_plan_strata).
    A pair whose session belongs to no planned trial falls into the stratum (cell, null, null) and
    into one block of its own (block null)."""
    def block_of(gt):
        info = plan_trials.get(gt.get("trial_id"))
        return None if info is None else info["block_id"]

    def label(gt):
        return _label(plan_trials, cell, gt.get("trial_id"))

    groups, speeds = {}, {}
    for p in match_result["pairs"]:
        lab = label(p["gt"])
        groups.setdefault(lab, []).append(p)
        speeds.setdefault((lab[0], lab[2]), []).append(p)
    by_stratum = []
    for lab in sorted(groups, key=_label_key):
        row = {"stratum": _stratum(lab)}
        row.update(_measures(groups[lab], block_of, B, seed))
        by_stratum.append(row)
    by_speed = []
    for lab in sorted(speeds, key=_speed_key):
        row = {"stratum": {"cell": lab[0], "speed": lab[1]}}
        row.update(_measures(speeds[lab], block_of, B, seed))
        by_speed.append(row)
    return {
        "metrics": {"by_stratum": by_stratum, "by_speed": by_speed,
                    "pooled": _measures(match_result["pairs"], block_of, B, seed)},
        "truncation": truncation_check(gt_sessions, match_result, limit_ms, label),
        "oracle": oracle_report(oracle_agreement, plan_trials, cell),
        "zero_dwell_rows": zero_dwell_rows(match_result, label),
    }


# ---------------------------------------------------------------- CLI

def parse_config(data):
    """Parse the configuration JSON (defaults if None) into {"B", "seed", "limit_ms"}."""
    cfg = {"B": DEFAULT_B, "seed": DEFAULT_SEED, "limit_ms": DEFAULT_LIMIT_MS}
    if data is None:
        return cfg
    obj = match._json_loads(match._decode(data, "config"), "config", False)
    if not isinstance(obj, dict):
        raise InputError("config: the top level must be an object")
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        if k not in cfg:
            raise InputError("config: unknown key '%s'" % k)
        if not match._is_int(v) or (k != "seed" and v < 1):
            raise InputError("config: %s must be an integer%s" % (k, "" if k == "seed" else " of at least 1"))
        if k == "B" and v > MAX_B:
            raise InputError("config: B must be at most %d" % MAX_B)
        cfg[k] = v
    return cfg


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def evaluate(match_bytes, sessions_bytes, plan_bytes, oracle_bytes, config_bytes=None):
    """The CLI's core logic (bytes in, an output dict out). Identical input always yields an
    identical dict."""
    cfg = parse_config(config_bytes)
    m = match._json_loads(match._decode(match_bytes, "match"), "match", True)
    if not (isinstance(m, dict) and isinstance(m.get("matching"), dict)
            and isinstance(m["matching"].get("pairs"), list) and isinstance(m.get("config"), dict)
            and isinstance(m.get("inputs_sha256"), dict)):
        raise InputError("match: not match.py's output")
    definition = m.get("definition")
    if definition not in match.DEFINITIONS:
        raise InputError("match: definition is not one of all, macos_def, windows_def")
    for part in ("split", "merge", "miss", "false_pos"):
        if not isinstance(m["matching"].get(part), list):
            raise InputError("match: matching.%s is missing" % part)
    plan = match.parse_plan_strata(plan_bytes)
    oracle = gt_sessions.parse_oracle(oracle_bytes)
    if not isinstance(oracle["plan_sha256"], str) or oracle["plan_sha256"].lower() != plan["sha256"]:
        raise InputError("plan: SHA-256 does not match the oracle header's plan_sha256 (unrelated plan)")
    have = {"sessions": _sha(sessions_bytes), "plan": plan["sha256"], "oracle": _sha(oracle_bytes)}
    for k, v in have.items():
        if m["inputs_sha256"].get(k) != v:
            raise InputError("%s: SHA-256 does not match the one recorded in the match output (unrelated input)" % k)
    _src, sess, _exc = match.parse_sessions(sessions_bytes, definition)
    sobj = match._json_loads(match._decode(sessions_bytes, "sessions"), "sessions", True)
    cell = m["config"].get("cell")
    if not isinstance(cell, str) or not match.CELL_RE.fullmatch(cell):
        raise InputError("match: config.cell is missing or has the wrong shape")
    rep = l1_report(m["matching"], sess, sobj.get("oracle_agreement"), plan["trials"], cell,
                    cfg["B"], cfg["seed"], cfg["limit_ms"])
    scope = m.get("scope").get("name") if isinstance(m.get("scope"), dict) else "all_trials"
    if scope not in match.SCOPES:
        raise InputError("match: scope.name must be one of %s" % ", ".join(match.SCOPES))
    rep.update({
        "definition": definition,
        "scope": scope,
        "config": {"B": cfg["B"], "seed": cfg["seed"], "limit_ms": cfg["limit_ms"], "cell": cell},
        "inputs_sha256": {"match": _sha(match_bytes), "sessions": have["sessions"], "plan": have["plan"],
                          "oracle": have["oracle"], "config": None if config_bytes is None else _sha(config_bytes)},
        "used": {"n_pairs": len(m["matching"]["pairs"])},
        "note": "Pairs whose observed dwell_ms is 0 are left out of the dwell figures and counted in "
                "n_zero_excluded; in the matching a row of dwell_ms 0 is a zero-length interval that never "
                "enters a pair, so it counts as a false positive (as pre-registered), and it is counted in "
                "zero_dwell_rows. Intervals are cluster-bootstrap (blocks) for dwell_ms and Wilson for "
                "proportions; read them as the width visible from this sample. The oracle report is built "
                "from every trial of the oracle and for every session definition, not only the definition "
                "matched here.",
    })
    return rep


def _fmt(v):
    return "n/a" if v is None else "%.3f" % v


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Level-1 agreement measures (dwell error, abandonment, navigation, cut-off, plan disagreements)")
    ap.add_argument("--match")
    ap.add_argument("--sessions")
    ap.add_argument("--plan")
    ap.add_argument("--oracle")
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.match and args.sessions and args.plan and args.oracle and args.out):
        ap.error("--match / --sessions / --plan / --oracle / --out are required (--config is optional)")
    for inp in (args.match, args.sessions, args.plan, args.oracle, args.config):
        if inp and match._same_file(inp, args.out):
            print("l1: --out names the same file as an input (%s)" % inp, file=sys.stderr)
            return 2
    try:
        out = evaluate(match._read(args.match, "match"), match._read(args.sessions, "sessions"),
                       match._read(args.plan, "plan"), match._read(args.oracle, "oracle"),
                       None if args.config is None else match._read(args.config, "config"))
        match._write_atomic(args.out, match.render(out))
    except (InputError, ValueError) as e:
        print("l1: %s" % e, file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        print("l1: an input has an unexpected shape (%s: %s)" % (type(e).__name__, e), file=sys.stderr)
        return 2
    except OSError as e:
        print("l1: could not write output (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    pooled, tr = out["metrics"]["pooled"], out["truncation"]
    print("l1: %d pairs; dwell n=%d (%d zero excluded), MAE %s ms; abandon agreement %s; nav exact %s; %d long sessions (%d with a row)" % (
        out["used"]["n_pairs"], pooled["dwell"]["n"], pooled["dwell"]["n_zero_excluded"], _fmt(pooled["dwell"]["mae"]),
        _fmt(pooled["abandon"]["agree"]), _fmt(pooled["nav"]["exact"]), tr["n_long"], tr["n_long_with_row"]))
    return 0


# ---------------------------------------------------------------- selftest

def _synth(n, seed, bias=0, sigma=50, flip=0.0, cancel_rate=0.3, n_blocks=20):
    """n synthetic 1:1 pairs. The observed dwell_ms is the true duration plus bias plus Gaussian noise
    (standard deviation sigma); the abandon flag is the true one, flipped with probability flip.
    Returns (pairs, block_of) where block_of maps trial_id to a block (n_blocks blocks)."""
    import random
    rng = random.Random(seed)
    t, pairs, block_of = 1700000000000, [], {}
    for i in range(n):
        t += 30000 + int(rng.random() * 90001)
        length = 800 + int(rng.random() * 1701)
        tid = "t%05d" % (i + 1)
        cancel = rng.random() < cancel_rate
        gt = match._gt(tid, t, t + length, outcome="cancel" if cancel else "commit")
        obs_abandon = (not cancel) if rng.random() < flip else cancel
        dwell = max(1, length + bias + int(round(rng.gauss(0, sigma))))
        obs = {"type": "ime_shadow", "t": t + length, "dwell_ms": dwell, "nav": 0, "abandon": obs_abandon}
        pairs.append({"gt": gt, "obs": obs, "iou": 1.0})
        block_of[tid] = i * n_blocks // n
        t += length
    return pairs, block_of


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _case_dwell_bias():
    f = []
    pairs, blk = _synth(1000, 1, bias=20, sigma=50)
    r = dwell_error(pairs, blk, 500, 7)
    lo, hi = r["bias_ci"]
    if r["n"] != 1000 or r["n_zero_excluded"] != 0 or r["n_blocks"] != 20:
        f.append("counts are not (1000, 0, 20): %r" % ((r["n"], r["n_zero_excluded"], r["n_blocks"]),))
    if lo is None or not (lo <= 20 <= hi):
        f.append("bias_ci does not contain the injected +20 ms: %r" % (r["bias_ci"],))
    if lo is not None and hi - lo > 10:
        f.append("bias_ci is too wide to be informative (%r)" % (r["bias_ci"],))
    sd = r["bland_altman"]["sd"]
    if not (45 <= sd <= 55):
        f.append("the Bland-Altman SD is not within 50 +/- 5: %r" % (sd,))
    if not (15 <= r["bland_altman"]["bias"] <= 25):
        f.append("the Bland-Altman bias is not near +20: %r" % (r["bland_altman"]["bias"],))
    # the sign is observed - ground truth: an observation that is too long gives a positive bias
    if r["bland_altman"]["bias"] <= 0:
        f.append("the sign is not observed - ground truth")
    # the interval of the median absolute error contains the point estimate and the theoretical value
    # (the median of |N(20, 50)| is about 36.5)
    mlo, mhi = r["mae_ci"]
    if mlo is None or not (mlo <= r["mae"] <= mhi) or not (mlo <= 36.5 <= mhi) or mhi - mlo > 15:
        f.append("mae_ci does not contain the point estimate %r and 36.5 (or is too wide): %r" % (r["mae"], r["mae_ci"]))
    # the limits of agreement (20 -/+ 1.96 * 50 = -78 / 118) carry their own block-bootstrap intervals
    ba = r["bland_altman"]
    for key, point, true in (("loa_lo_ci", ba["loa_lo"], -78.0), ("loa_hi_ci", ba["loa_hi"], 118.0)):
        a, b = r[key]
        if a is None or not (a <= point <= b) or not (a <= true <= b) or b - a > 25:
            f.append("%s does not contain the point %r and %r (or is too wide): %r" % (key, point, true, r[key]))
    one = dwell_error(pairs[:1], blk, 50, 7)
    if one["loa_lo_ci"] != [None, None] or one["loa_hi_ci"] != [None, None]:
        f.append("one pair gives no interval for the limits of agreement: %r" % (one,))
    # control on an independent draw (another seed): with no injected bias the interval must not contain 20
    # (the interval can tell the two apart)
    pairs0, blk0 = _synth(1000, 101, bias=0, sigma=50)
    lo0, hi0 = dwell_error(pairs0, blk0, 500, 7)["bias_ci"]
    if not (lo0 <= 0 <= hi0) or lo0 <= 20 <= hi0:
        f.append("with no bias the interval should contain 0 and not 20: %r" % ([lo0, hi0],))
    return f


def _case_abandon_flip():
    f = []
    pairs, _blk = _synth(1000, 2, flip=0.10)
    a = abandon_agreement(pairs)
    lo, hi = a["agree_ci"]
    if not (lo <= 0.90 <= hi):
        f.append("agree_ci does not contain 0.90 after a 10%% flip: %r (agree %r)" % (a["agree_ci"], a["agree"]))
    if hi >= 1.0 or a["agree"] >= 0.97:
        f.append("the flip is not visible in the agreement: %r" % (a["agree"],))
    if a["kappa"] is None or not (0.7 <= a["kappa"] <= 0.85):
        f.append("kappa is not near 0.77 (10%% flip, 30%% cancel): %r" % (a["kappa"],))
    if a["n"] != 1000 or sum(sum(r) for r in a["table"]) != 1000:
        f.append("the table does not hold every pair")
    # orientation (rows = ground truth, columns = observation), counted independently of the table code
    fp = sum(1 for p in pairs if p["gt"]["outcome"] != "cancel" and p["obs"]["abandon"])
    fn = sum(1 for p in pairs if p["gt"]["outcome"] == "cancel" and not p["obs"]["abandon"])
    if fp == fn:
        f.append("the fixture cannot tell a table from its transpose (%d = %d)" % (fp, fn))
    if a["table"][0][1] != fp or a["table"][1][0] != fn:
        f.append("the table is not [[tn, fp], [fn, tp]] with rows = ground truth: %r (fp %d, fn %d)" % (a["table"], fp, fn))
    # control: no flip gives perfect agreement
    p0, _b0 = _synth(1000, 2, flip=0.0)
    a0 = abandon_agreement(p0)
    if a0["agree"] != 1.0 or a0["kappa"] != 1.0 or a0["table"][0][1] != 0 or a0["table"][1][0] != 0:
        f.append("with no flip agreement and kappa should be exactly 1: %r" % (a0,))
    # kappa's block-bootstrap interval, and the reasons it is withheld
    ab = abandon_agreement(pairs, _blk, 300, 3)
    klo, khi = ab["kappa_ci"]
    if ab["kappa_ci_status"] != "ok" or klo is None or not (klo <= ab["kappa"] <= khi) or khi - klo > 0.2 or klo == khi:
        f.append("kappa_ci should bracket kappa (status ok): %r / %r" % (ab["kappa_ci"], ab["kappa_ci_status"]))
    if a["kappa_ci_status"] != "not_requested" or a["kappa_ci"] != [None, None]:
        f.append("without blocks the kappa interval is not requested: %r" % (a["kappa_ci_status"],))
    commits = [p for p in p0 if p["gt"]["outcome"] != "cancel"]
    if abandon_agreement(commits, _b0, 50, 1)["kappa_ci_status"] != "no_kappa":
        f.append("one outcome on both sides gives no kappa and says so")
    if abandon_agreement(p0[:40], lambda _g: 0, 50, 1)["kappa_ci_status"] != "fewer_than_two_blocks":
        f.append("one block gives no kappa interval and says so")
    # block 1 holds only commits that agree, so a resample of it alone has no kappa
    mixed = [dict(p, gt=dict(p["gt"], trial_id="a")) for p in commits[:20]] + \
            [dict(p, gt=dict(p["gt"], trial_id="b")) for p in p0[:20]]
    two = abandon_agreement(mixed, lambda g: g["trial_id"], 200, 1)
    if two["kappa"] is None or two["kappa_ci_status"] != "undefined_resample" or two["kappa_ci"] != [None, None]:
        f.append("an undefined resample withholds the kappa interval and says so: %r" % (two,))
    return f


def _case_abandon_by_continued_commit():
    """The diagnostic split puts each pair in the stratum its session's n_continued_commits selects,
    counts a missing or malformed field as unknown, and leaves the pooled abandon measure unchanged."""
    f = []
    pairs, _blk = _synth(300, 4, flip=0.0)
    marked = []
    for i, p in enumerate(pairs):
        k = None if i % 30 == 0 else (2 if i % 3 == 0 else 0)  # 10 unknown, 90 with, 200 without
        gt = dict(p["gt"])
        if k is not None:
            gt["n_continued_commits"] = k
        obs = dict(p["obs"])
        if k == 2:  # disagree on every pair of the "with" stratum, and nowhere else
            obs["abandon"] = not obs["abandon"]
        marked.append(dict(p, gt=gt, obs=obs))
    marked[1]["gt"]["n_continued_commits"] = -1  # malformed: unknown, not "without"
    r = abandon_by_continued_commit(marked)
    if (r["with"]["n"], r["without"]["n"], r["n_unknown"]) != (90, 199, 11):
        f.append("the strata do not hold (with, without, unknown) = (90, 199, 11): %r"
                 % ((r["with"]["n"], r["without"]["n"], r["n_unknown"]),))
    if r["with"]["agree"] != 0.0 or r["without"]["agree"] != 1.0:
        f.append("the disagreement is not confined to the stratum it was put in: with %r, without %r"
                 % (r["with"]["agree"], r["without"]["agree"]))
    whole = abandon_agreement(marked)
    if whole["n"] != 300 or whole["agree"] != 210 / 300:
        f.append("the pooled abandon measure changed or lost pairs: %r" % ((whole["n"], whole["agree"]),))
    e = abandon_by_continued_commit([])
    if e["with"]["n"] != 0 or e["with"]["agree"] is not None or e["n_unknown"] != 0:
        f.append("with no pairs the strata should be empty and null: %r" % (e,))
    m = _measures(marked, _blk, 50, 1)
    if not _same(m.get("abandon_by_continued_commit"), r) or not _same(m.get("abandon"), abandon_agreement(marked, _blk, 50, 1)):
        f.append("the per-stratum and pooled measures do not carry the split next to the abandon measure")
    return f


def _case_dwell_zero():
    f = []
    pairs, blk = _synth(200, 3, bias=0, sigma=50)
    bad = [dict(p, obs=dict(p["obs"], dwell_ms=0)) if i % 20 == 0 else p for i, p in enumerate(pairs)]
    r = dwell_error(bad, blk, 300, 5)
    if r["n_zero_excluded"] != 10 or r["n"] != 190 or r["bland_altman"]["n"] != 190:
        f.append("the zero rows are not excluded and counted: %r" % ((r["n"], r["n_zero_excluded"], r["bland_altman"]["n"]),))
    kept = [p for i, p in enumerate(pairs) if i % 20 != 0]
    ref = dwell_error(kept, blk, 300, 5)
    ref["n_zero_excluded"] = 10
    if not _same(r, ref):
        f.append("the result differs from the one computed on the remaining pairs alone")
    if r["mae"] is None or r["mae"] > 100:
        f.append("a zero dwell_ms leaked into the error (MAE %r)" % (r["mae"],))
    # all pairs zero -> nothing to estimate, reported as null and never 0
    z = dwell_error([dict(p, obs=dict(p["obs"], dwell_ms=0)) for p in pairs[:5]], blk, 100, 5)
    if z["n"] != 0 or z["n_zero_excluded"] != 5 or z["mae"] is not None or z["bland_altman"]["bias"] is not None:
        f.append("with only zero rows the figures should be null: %r" % (z,))
    return f


def _case_shuffle():
    import random
    f = []
    pairs, blk = _synth(1000, 4, bias=0, sigma=50)
    base = dwell_error(pairs, blk, 300, 9)
    rng = random.Random(11)
    d = [p["obs"]["dwell_ms"] for p in pairs]
    for i in range(len(d) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        d[i], d[j] = d[j], d[i]
    shuf = [dict(p, obs=dict(p["obs"], dwell_ms=v)) for p, v in zip(pairs, d)]
    r = dwell_error(shuf, blk, 300, 9)
    b0, b1 = base["bland_altman"], r["bland_altman"]
    if abs(b0["bias"]) > 10 or abs(b1["bias"]) > 60:
        f.append("the bias should stay near 0 (kept the same marginal): %r -> %r" % (b0["bias"], b1["bias"]))
    if not (b1["sd"] > 5 * b0["sd"]):
        f.append("the SD did not grow after shuffling: %r -> %r" % (b0["sd"], b1["sd"]))
    if not ((b1["loa_hi"] - b1["loa_lo"]) > 5 * (b0["loa_hi"] - b0["loa_lo"])):
        f.append("the limits of agreement did not widen after shuffling")
    if not (r["mae"] > 5 * base["mae"]):
        f.append("the MAE did not grow after shuffling: %r -> %r" % (base["mae"], r["mae"]))
    return f


def _case_nav():
    f = []
    P = lambda nt, ntc, nav: {"gt": match._gt("t1", 0, 1, nav_true=nt, nav_true_cand=ntc), "obs": {"nav": nav}, "iou": 1.0}
    # the last pair has a candidate-window count of 0 (a real value, not a missing one): 0 - 0 = 0, whereas
    # falling back to nav_true (3) would give -3
    r = nav_agreement([P(4, 2, 2), P(4, None, 4), P(3, None, 5), P(3, 1, 0), P(3, 0, 0)])
    if r["n"] != 5 or r["exact"] != 0.6:
        f.append("exact is not 3/5: %r" % ((r["n"], r["exact"]),))
    if r["diff_hist"] != [[-1, 1], [0, 3], [2, 1]]:
        f.append("diff_hist is wrong (observed - true; nav_true_cand first, else nav_true): %r" % (r["diff_hist"],))
    lo, hi = r["exact_ci"]
    if not (0 < lo < 0.6 < hi < 1):
        f.append("exact_ci is not a Wilson interval around 0.6: %r" % (r["exact_ci"],))
    e = nav_agreement([])
    if e["exact"] is not None or e["exact_ci"] != [None, None] or e["diff_hist"] != []:
        f.append("no pairs should give null, not 0: %r" % (e,))
    try:
        nav_agreement([{"gt": {"trial_id": "t1"}, "obs": {"nav": 0}, "iou": 1.0}])
        f.append("a session without any true count was accepted")
    except ValueError:
        pass
    return f


def _case_truncation():
    f = []
    long_a = match._gt("t1", 0, 25000)
    long_b = match._gt("t2", 100000, 130000)
    short = match._gt("t3", 200000, 201000)
    long_x = match._gt("t6", 400000, 430000, excluded_by=["clock_jump"])   # the matcher left this one out
    ob = lambda lo, hi: {"type": "ime_shadow", "t": hi, "dwell_ms": hi - lo, "nav": 0, "abandon": False}
    res = {"pairs": [{"gt": short, "obs": ob(200000, 201000), "iou": 1.0}], "split": [], "merge": [],
           "miss": [long_a, long_b], "false_pos": []}
    r = truncation_check([long_a, long_b, short, long_x], res)
    if (r["n_long"], r["n_long_excluded"], r["n_long_with_row"], r["n_obs_over_limit"]) != (2, 1, 0, 0):
        f.append("expected (2 long, 1 excluded, 0 with a row, 0 over the limit): %r" % (r,))
    # positive control: a long session that did get a row, and an observed row over the limit
    res2 = {"pairs": [{"gt": long_a, "obs": ob(0, 25000), "iou": 1.0}], "split": [{"gt": long_b, "obs": [ob(100000, 110000)]}],
            "merge": [], "miss": [short], "false_pos": [ob(300000, 330000)]}
    r2 = truncation_check([long_a, long_b, short, long_x], res2)
    if (r2["n_long"], r2["n_long_excluded"], r2["n_long_with_row"], r2["n_obs_over_limit"]) != (2, 1, 2, 2) \
            or r2["n_obs_over_limit_false_pos"] != 1 or r2["with_row_gt_dwell_ms"] != [25000, 30000]:
        f.append("the check cannot fail: expected (2, 1, 2, 2) with durations [25000, 30000], got %r" % (r2,))
    # a session of exactly the limit is long (the observer stops at a duration of at least the limit and emits no row)
    edge = match._gt("t4", 0, 20000)
    just = match._gt("t5", 0, 19999)
    res3 = {"pairs": [{"gt": just, "obs": ob(0, 19999), "iou": 1.0}], "split": [], "merge": [], "miss": [edge], "false_pos": [ob(0, 20000)]}
    r3 = truncation_check([edge, just], res3)
    if (r3["n_long"], r3["n_long_with_row"], r3["n_obs_over_limit"]) != (1, 0, 1):
        f.append("a session or row of exactly the limit should count, one just below should not: %r" % (r3,))
    if truncation_check([edge], res3, limit_ms=20001)["n_long"] != 0 or truncation_check([edge], res3, limit_ms=20000)["n_long"] != 1:
        f.append("the limit is not configurable or not inclusive")
    open_s = match._gt("t7", 0, None, outcome="open_at_end")
    if truncation_check([open_s], dict(res3, miss=[open_s]))["n_long"] != 0:
        f.append("an unclosed session should not count as long")
    # per stratum
    lab = lambda g: ("c", "S1" if g["trial_id"] == "t1" else "S2", "fast")
    rs = truncation_check([long_a, long_b, short, long_x], res2, label=lab)["by_stratum"]
    got = [(x["stratum"]["scenario"], x["n_long"], x["n_long_excluded"], x["n_long_with_row"], x["n_obs_over_limit"]) for x in rs]
    if got != [("S1", 1, 0, 1, 1), ("S2", 1, 1, 1, 0)]:
        f.append("the per-stratum counts are wrong: %r" % (got,))
    return f


def _case_zero_rows():
    f = []
    ob = lambda lo, hi: {"type": "ime_shadow", "t": hi, "dwell_ms": hi - lo, "nav": 0, "abandon": False}
    g1, g2, g3, g4 = (match._gt("t1", 0, 1000), match._gt("t2", 10000, 11000),
                      match._gt("t3", 20000, 21000), match._gt("t4", 20000, 21000))
    res = {"pairs": [{"gt": g1, "obs": ob(500, 500), "iou": 1.0}, {"gt": g4, "obs": ob(20000, 21000), "iou": 1.0}],
           "split": [{"gt": g2, "obs": [ob(10000, 10000), ob(10500, 11000)]}],
           "merge": [{"gt": [g3, g4], "obs": ob(30000, 30000)}], "miss": [], "false_pos": [ob(40000, 40000), ob(50000, 50100)]}
    lab = lambda g: ("c", "S1" if g["trial_id"] in ("t1", "t2") else "S2", "fast")
    z = zero_dwell_rows(res, lab)
    if (z["n"], z["in_pairs"], z["in_split"], z["in_merge"], z["in_false_pos"]) != (4, 1, 1, 1, 1):
        f.append("a zero-dwell row is missed in some bucket: %r" % (z,))
    got = [(x["stratum"]["scenario"], x["n"], x["in_pairs"], x["in_split"], x["in_merge"]) for x in z["by_stratum"]]
    if got != [("S1", 2, 1, 1, 0), ("S2", 1, 0, 0, 1)]:
        f.append("the per-stratum zero-dwell counts are wrong: %r" % (got,))
    none = zero_dwell_rows(dict(res, pairs=[], split=[], merge=[], false_pos=[ob(1, 5)]), lab)
    if none["n"] != 0 or none["by_stratum"] != []:
        f.append("no zero-dwell rows should give zero counts: %r" % (none,))
    return f


def _case_oracle():
    f = []
    oa = {"plan_available": True, "n_trials": 100, "n_agree": 90, "rate": 0.9, "unassigned_sessions": 2,
          "by_type": {"no_session": 4, "unexpected_session": 1, "multiple_sessions": 5}, "mismatches": []}
    r = oracle_report(oa)
    if (r["n_disagree"], r["disagree_rate"]) != (10, 0.1) or r["removed_from_evaluation"] is not False:
        f.append("the disagreement count is wrong or claims removal: %r" % (r,))
    if not (r["disagree_ci"][0] < 0.1 < r["disagree_ci"][1]):
        f.append("disagree_ci does not surround 0.1: %r" % (r["disagree_ci"],))
    if list(r["by_type"]) != sorted(oa["by_type"]) or r["by_type"]["multiple_sessions"]["n"] != 5:
        f.append("by_type is wrong: %r" % (r["by_type"],))
    plan = {"t1": {"block_id": 0, "scenario": "S1", "speed": "fast"}, "t2": {"block_id": 0, "scenario": "S1", "speed": "fast"},
            "t3": {"block_id": 1, "scenario": "S2", "speed": "fast"}}
    oa2 = {"plan_available": True, "n_trials": 3, "n_agree": 0, "by_type": {"no_session": 2, "multiple_sessions": 1},
           "mismatches": [{"trial_id": "t1", "type": "no_session"}, {"trial_id": "t3", "type": "multiple_sessions"},
                          {"trial_id": "t9", "type": "no_session"}]}
    bs = oracle_report(oa2, plan, "c")["by_stratum"]
    got = [(x["stratum"]["scenario"], x["n_trials"], x["n_disagree"], x["disagree_rate"]) for x in bs]
    if got != [("S1", 2, 1, 0.5), ("S2", 1, 1, 1.0), (None, 0, 1, None)] or bs[0]["by_type"]["no_session"]["n"] != 1:
        f.append("the per-stratum disagreement counts are wrong: %r" % (got,))
    if oracle_report({"plan_available": False, "n_trials": 7}) != {"plan_available": False, "n_trials": 7}:
        f.append("without a plan only the trial count is reported")
    good = {"plan_available": True, "n_trials": 3, "n_agree": 3, "by_type": {}, "unassigned_sessions": 0}
    for name, bad in (("unassigned_sessions", dict(good, unassigned_sessions="x")), ("n_trials", dict(good, n_trials="3", n_agree=3)),
                      ("by_type key", dict(good, by_type={"free text": 1})), ("by_type value", dict(good, by_type={"no_session": "1"}))):
        try:
            oracle_report(bad)
            f.append("an oracle_agreement with a bad %s was accepted" % name)
        except ValueError:
            pass
    try:
        oracle_report({"plan_available": True, "n_trials": 3})
        f.append("an incomplete oracle_agreement was accepted")
    except ValueError:
        pass
    return f


def _case_small_samples():
    f = []
    pairs, blk = _synth(30, 5, bias=10, sigma=20, n_blocks=1)
    r = dwell_error(pairs, blk, 100, 3)
    if r["bias_ci"] != [None, None] or r["mae_ci"] != [None, None] or r["n_blocks"] != 1:
        f.append("one block should give no interval (null, not a point): %r" % (r,))
    e = dwell_error([], {}, 100, 3)
    if e["n"] != 0 or e["mae"] is not None or e["bias_ci"] != [None, None]:
        f.append("no pairs should give null, not 0: %r" % (e,))
    a = abandon_agreement([])
    if a["agree"] is not None or a["kappa"] is not None or a["agree_ci"] != [None, None]:
        f.append("no pairs should give null agreement: %r" % (a,))
    return f


def _case_byte_identical():
    f = []
    pairs, blk = _synth(300, 6, bias=5, sigma=40, flip=0.1)
    a = json.dumps(_measures(pairs, blk, 200, 13), sort_keys=True)
    b = json.dumps(_measures(pairs, blk, 200, 13), sort_keys=True)
    c = json.dumps(_measures(pairs, blk, 200, 14), sort_keys=True)
    if a != b:
        f.append("two runs with the same seed differ")
    if a == c:
        f.append("the seed has no effect on the intervals")
    return f


def _fixture_files(d):
    """match.py's synthetic fixture, with a complete oracle_agreement, run through match.evaluate; every
    input file is written under d. Returns the file paths."""
    fx = match._cli_fixture()
    sobj = json.loads(fx["sessions"].decode("utf-8"))
    sobj["oracle_agreement"] = {"plan_available": True, "n_trials": 90, "n_agree": 88, "rate": 88 / 90,
                                "by_type": {"multiple_sessions": 1, "no_session": 1},
                                "mismatches": [{"trial_id": "t00002", "type": "no_session"},
                                               {"trial_id": "t00003", "type": "multiple_sessions"}],
                                "unassigned_sessions": 0}
    sess_bytes = (json.dumps(sobj, indent=2) + "\n").encode("utf-8")
    # a row of dwell_ms 0 inside trial t00002 (which lies after that trial's session, so it cannot be paired)
    zt = 1700000000000 + 8000 + 4000
    obs_bytes = fx["obs"] + (json.dumps(match._ob(zt, zt)) + "\n").encode("utf-8")
    out = match.evaluate(sess_bytes, "all", obs_bytes, fx["plan"], fx["oracle"], fx["config"])
    files = {"match.json": match.render(out), "sessions.json": sess_bytes, "plan.jsonl": fx["plan"],
             "oracle.jsonl": fx["oracle"], "config.json": b'{"B": 200, "seed": 3}'}
    paths = {}
    for name, data in files.items():
        paths[name] = os.path.join(d, name)
        with open(paths[name], "wb") as fh:
            fh.write(data)
    return paths


def _case_cli():
    import contextlib
    import io
    import tempfile
    f = []
    with tempfile.TemporaryDirectory() as d:
        p = _fixture_files(d)

        def run(out_name, **over):
            a = {"match": p["match.json"], "sessions": p["sessions.json"], "plan": p["plan.jsonl"],
                 "oracle": p["oracle.jsonl"], "config": p["config.json"]}
            a.update(over)
            argv = []
            for k, v in a.items():
                if v is not None:
                    argv += ["--" + k, v]
            argv += ["--out", os.path.join(d, out_name)]
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = main(argv)
            return rc, os.path.join(d, out_name)

        rc1, o1 = run("o1.json")
        rc2, o2 = run("o2.json")
        if rc1 != 0 or rc2 != 0:
            return ["the CLI did not exit 0 (%r, %r)" % (rc1, rc2)]
        b1, b2 = open(o1, "rb").read(), open(o2, "rb").read()
        if b1 != b2:
            f.append("two runs on the same input give different bytes")
        if b"\r" in b1 or b1.startswith(b"\xef\xbb\xbf") or not b1.endswith(b"\n") or d.encode("utf-8") in b1:
            f.append("the output is not LF / no BOM / free of paths")
        out = json.loads(b1.decode("utf-8"))
        pooled = out["metrics"]["pooled"]
        if out["used"]["n_pairs"] != 84 or pooled["dwell"]["n"] != 84 or pooled["dwell"]["mae"] != 0 or pooled["dwell"]["n_blocks"] != 3:
            f.append("pooled dwell is not (84 pairs, MAE 0, 3 blocks): %r" % (pooled["dwell"],))
        if pooled["abandon"]["agree"] != 1.0 or pooled["abandon"]["kappa"] is not None:
            f.append("with one class only, agreement is 1 and kappa null: %r" % (pooled["abandon"],))
        if pooled["nav"]["exact"] != 1.0 or out["truncation"]["n_long"] != 0:
            f.append("nav agreement or truncation is wrong: %r %r" % (pooled["nav"], out["truncation"]))
        if len(out["metrics"]["by_stratum"]) != 9 or out["oracle"]["n_disagree"] != 2:
            f.append("expected 9 strata and 2 oracle disagreements: %r" % (len(out["metrics"]["by_stratum"]),))
        bs = out["metrics"]["by_speed"]
        per = {}
        for r in out["metrics"]["by_stratum"]:
            per[r["stratum"]["speed"]] = per.get(r["stratum"]["speed"], 0) + r["dwell"]["n"]
        if [r["stratum"]["speed"] for r in bs] != ["slow", "mid", "fast"] or any(r["dwell"]["n"] != per[r["stratum"]["speed"]] for r in bs):
            f.append("by_speed should pool each speed's strata, in the order slow, mid, fast: %r" % ([(r["stratum"], r["dwell"]["n"]) for r in bs],))
        if sum(s["dwell"]["n"] for s in out["metrics"]["by_stratum"]) != 84:
            f.append("the strata do not add up to the pooled pairs")
        # a row of dwell_ms 0 that went through match.py lands in the false positives and is counted, not lost
        z = out["zero_dwell_rows"]
        if (z["n"], z["in_pairs"], z["in_split"], z["in_merge"], z["in_false_pos"]) != (1, 0, 0, 0, 1) or pooled["dwell"]["n_zero_excluded"] != 0:
            f.append("the zero-dwell row is not counted as a false positive: %r" % (z,))
        mj = json.loads(open(p["match.json"], "rb").read().decode("utf-8"))
        if sum(1 for r in mj["matching"]["false_pos"] if r["dwell_ms"] == 0) != 1:
            f.append("the fixture's zero-dwell row did not fall into the false positives of match.py")
        # the trials the oracle disagrees on have a pair each, and those pairs stay in the metrics
        mm = {"t00002", "t00003"}
        if sum(1 for q in mj["matching"]["pairs"] if q["gt"]["trial_id"] in mm) != 2:
            f.append("the fixture's disagreeing trials do not each have a pair")
        if out["oracle"]["n_disagree"] != 2 or out["oracle"]["removed_from_evaluation"] is not False or pooled["dwell"]["n"] != 84:
            f.append("a trial the oracle disagrees on was dropped from the metrics")
        ob_ = out["oracle"]["by_stratum"]
        if len(ob_) != 9 or sum(x["n_disagree"] for x in ob_) != 2 or sum(x["n_trials"] for x in ob_) != 90:
            f.append("the per-stratum oracle report is wrong: %r" % (ob_,))
        if len(out["truncation"]["by_stratum"]) != 9 or out["truncation"]["n_long_excluded"] != 0:
            f.append("the per-stratum cut-off report is wrong")
        if "every session definition" not in out["note"] and "every trial of the oracle" not in out["note"]:
            f.append("the note does not say what the oracle report covers")
        if any(s["dwell"]["bias_ci"] != [None, None] for s in out["metrics"]["by_stratum"]):
            f.append("a stratum with a single block should have a null interval")
        # unrelated inputs are refused (exit 2) and never combined
        with open(os.path.join(d, "plan2.jsonl"), "wb") as fh:
            fh.write(open(p["plan.jsonl"], "rb").read() + b"\n")
        if run("o3.json", plan=os.path.join(d, "plan2.jsonl"))[0] != 2:
            f.append("a plan that does not match the oracle header was accepted")
        with open(os.path.join(d, "sess2.json"), "wb") as fh:
            fh.write(open(p["sessions.json"], "rb").read() + b"\n")
        if run("o4.json", sessions=os.path.join(d, "sess2.json"))[0] != 2:
            f.append("a sessions file that does not match the match output was accepted")
        if run("o6.json", config=None)[0] != 0:
            f.append("the CLI should run with the default configuration")
        bad_cfg = os.path.join(d, "badcfg.json")
        for text in (b'{"B": 0}', b'{"nope": 1}', b'{"seed": 1.5}', b'[1]'):
            with open(bad_cfg, "wb") as fh:
                fh.write(text)
            if run("o7.json", config=bad_cfg)[0] != 2:
                f.append("an invalid configuration was accepted: %r" % (text,))
        if run("o8.json", match=os.path.join(d, "absent.json"))[0] != 2:
            f.append("a missing input should exit 2")
        with open(bad_cfg, "wb") as fh:
            fh.write(b'{"B": %d}' % (MAX_B + 1))
        if run("o9.json", config=bad_cfg)[0] != 2:
            f.append("a B above the upper bound was accepted")
        if parse_config(b'{"B": %d}' % MAX_B)["B"] != MAX_B:
            f.append("the largest allowed B was refused")
        mjc = json.loads(open(p["match.json"], "rb").read().decode("utf-8"))
        mjc["config"]["cell"] = "not a cell label"
        with open(os.path.join(d, "badcell.json"), "wb") as fh:
            fh.write(match.render(mjc))
        if run("o11.json", match=os.path.join(d, "badcell.json"))[0] != 2:
            f.append("a cell label of the wrong shape was copied into the output")
        # a malformed match output exits 2 with a message, not with a traceback
        for name, edit in (("no obs", lambda m: m["matching"]["pairs"][0].pop("obs")),
                           ("no dwell_ms", lambda m: m["matching"]["pairs"][0]["obs"].pop("dwell_ms")),
                           ("null end", lambda m: m["matching"]["pairs"][0]["gt"].update(end_wall_ms=None)),
                           ("no nav", lambda m: m["matching"]["pairs"][0]["obs"].pop("nav")),
                           ("no gt", lambda m: m["matching"]["pairs"][0].pop("gt"))):
            mj2 = json.loads(open(p["match.json"], "rb").read().decode("utf-8"))
            edit(mj2)
            bad_m = os.path.join(d, "badmatch.json")
            with open(bad_m, "wb") as fh:
                fh.write(match.render(mj2))
            if run("o10.json", match=bad_m)[0] != 2:
                f.append("a malformed match output (%s) did not exit 2" % name)
    return f


CASES = [
    ("recovers a dwell bias (+20 ms, sigma 50 ms; the interval and the SD)", _case_dwell_bias),
    ("recovers an abandon flip (10% flipped; the agreement interval and kappa)", _case_abandon_flip),
    ("abandon agreement split by continued commits (strata, unknown, pooled unchanged)", _case_abandon_by_continued_commit),
    ("excludes a dwell_ms of zero (and counts it)", _case_dwell_zero),
    ("shuffling widens the limits of agreement (negative control; the bias stays near 0)", _case_shuffle),
    ("navigation agreement (the true count, the difference distribution)", _case_nav),
    ("cut-off check (long sessions, excluded sessions, rows over the limit, the inclusive limit, strata)", _case_truncation),
    ("rows of dwell_ms zero are counted in every bucket", _case_zero_rows),
    ("plan-versus-ground-truth report (counts, types, Wilson, not removed)", _case_oracle),
    ("small samples give null, never 0", _case_small_samples),
    ("byte-identical output for the same seed; the seed matters", _case_byte_identical),
    ("CLI (strata, pooled, byte-identical output, refusal of unrelated inputs, exit 2)", _case_cli),
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
