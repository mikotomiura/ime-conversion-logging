#!/usr/bin/env python3
# kit: selftest
"""Statistical building blocks shared by evaluation E1 (levels L1 / L2) and evaluation E2 (uses only
the Python standard library).

  stats.py --selftest      known values, closed-form checks, properties, positive/negative controls
                             (fast default, well under one second)

  wilson(x, n, conf)                       the Wilson interval for a proportion (detection precision, recall)
  cluster_bootstrap(clusters, stat, ...)   a bootstrap interval with clusters (blocks) as the resampling unit
  bland_altman(a, b)                       bias and limits of agreement (dwell_ms, the L2 measures)
  cohen_kappa(table)                       Cohen's kappa for a k x k contingency table (abandon)
  icc(matrix, kind)                        the intraclass correlation ICC(1,1) / (2,1) / (3,1) (absolute agreement for L2 is (2,1))
  median_abs_error(a, b)                   the median absolute error of a set of pairs

Written as estimation, not significance testing: an interval is the width visible from this sample,
not a claim about how far the true value could lie.
A quantity that cannot be computed is returned as None (zero items, one cluster, zero variance, and
so on). It is never rounded to 0 or nan -- a 0 in a published table would be indistinguishable from
an estimate that happened to be zero.
Randomness uses only an instance of random.Random(seed); the global random state is never read or
written.

Sources (author, year, and title only; citation numbering belongs to the central bibliography
maintained elsewhere, outside this file's scope):
  - Wilson (1927) Probable inference, the law of succession, and statistical inference
  - Bland & Altman (1986) Statistical methods for assessing agreement between two methods of clinical measurement
  - Cohen (1960) A coefficient of agreement for nominal scales
  - Shrout & Fleiss (1979) Intraclass correlations: uses in assessing rater reliability
  - Efron (1979) Bootstrap methods: another look at the jackknife (the percentile method)
"""
import argparse
import math
import random
import statistics
import sys

# The coefficient for Bland-Altman's limits of agreement. The convention is 1.96 (a rounding of the
# normal distribution's 97.5th percentile).
BA_Z = 1.96


def _check_conf(conf):
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 < conf < 1.0):
        raise ValueError("conf must be a number strictly between 0 and 1")


def _z(conf):
    """The two-sided critical value of the normal distribution at confidence conf (1.959964 when
    conf = 0.95)."""
    return statistics.NormalDist().inv_cdf((1.0 + conf) / 2.0)


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def wilson(x, n, conf=0.95):
    """The Wilson interval (lo, hi) for the proportion x / n. (None, None) when n = 0.

    The interval stays within [0, 1] even at x = 0 or x = n, and does not collapse to zero width at
    either end (unlike the Wald interval). The interval always contains x / n.
    """
    _check_conf(conf)
    if not (_is_count(x) and _is_count(n)) or n < 0 or x < 0 or x > n:
        raise ValueError("x and n must be integers with 0 <= x <= n")
    if n == 0:
        return (None, None)
    z = _z(conf)
    z2 = z * z
    p = x / n
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    # The lower bound at x = 0 and the upper bound at x = n are mathematically exactly 0 and 1
    # (center and half cancel exactly). Both are set explicitly to guard against a floating-point
    # error (e.g. 2.8e-17) that would otherwise exclude x/n.
    lo = 0.0 if x == 0 else max(0.0, center - half)
    hi = 1.0 if x == n else min(1.0, center + half)
    return (lo, hi)


def _percentile(sorted_vals, q):
    """The q-th quantile of a sorted list, by linear interpolation (q ranges over 0..1)."""
    pos = q * (len(sorted_vals) - 1)
    i = int(math.floor(pos))
    j = min(i + 1, len(sorted_vals) - 1)
    return sorted_vals[i] + (sorted_vals[j] - sorted_vals[i]) * (pos - i)


def cluster_bootstrap(clusters, stat, B, seed, conf=0.95):
    """A percentile bootstrap interval (lo, hi), resampling with replacement at the cluster level.

    clusters: an array of per-cluster value lists (for example, per-block trial values).
    stat: a function that takes a resampled list of clusters (each cluster passed as a tuple) and
          returns a single finite number. Each cluster is passed through with its original contents
          intact (elements are never mixed across clusters), which is what preserves within-cluster
          correlation.
    B: the number of resampling iterations. seed: an integer (only random.Random(seed) is used).

    Returns (None, None) when fewer than two clusters are given (with only one cluster, the interval
    would collapse to a single point, which looks narrow but is misleading). Raises ValueError if
    stat returns None or a non-finite value (silently discarding it would bias the interval; the
    caller must handle that case in stat itself).
    Only Random.random() is used to draw resample positions, because Python's documentation
    guarantees that the sequence produced by a given seed and random() alone is stable across
    versions (methods such as randrange can change between versions).
    """
    _check_conf(conf)
    if not _is_count(B) or B < 1:
        raise ValueError("B must be an integer of at least 1")
    if not _is_count(seed):
        raise ValueError("seed must be an integer")
    cl = [tuple(c) for c in clusters]
    k = len(cl)
    if k < 2:
        return (None, None)
    rng = random.Random(seed)
    reps = []
    for _ in range(B):
        sample = [cl[min(k - 1, int(rng.random() * k))] for _ in range(k)]
        v = stat(sample)
        if v is None or not math.isfinite(v):
            raise ValueError("stat returned a non-finite value (None / nan / inf)")
        reps.append(v)
    reps.sort()
    a = (1.0 - conf) / 2.0
    return (_percentile(reps, a), _percentile(reps, 1.0 - a))


def _paired(a, b):
    a, b = list(a), list(b)
    if len(a) != len(b):
        raise ValueError("a and b must be paired lists of equal length (%d and %d)" % (len(a), len(b)))
    return a, b


def bland_altman(a, b):
    """The bias and limits of agreement of the differences a - b. Returns {"bias", "sd", "loa_lo",
    "loa_hi", "n"}.

    sd is the sample standard deviation (divided by n - 1). The limits of agreement are bias +/-
    1.96 * sd.
    At n = 0, bias is also None; at n = 1, sd and the limits of agreement are None (the variance is
    undefined).
    """
    a, b = _paired(a, b)
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n == 0:
        return {"bias": None, "sd": None, "loa_lo": None, "loa_hi": None, "n": 0}
    bias = statistics.fmean(d)
    if n == 1:
        return {"bias": bias, "sd": None, "loa_lo": None, "loa_hi": None, "n": 1}
    sd = statistics.stdev(d)
    return {"bias": bias, "sd": sd, "loa_lo": bias - BA_Z * sd, "loa_hi": bias + BA_Z * sd, "n": n}


def cohen_kappa(table):
    """Cohen's kappa for a k x k contingency table (rows = rater 1, columns = rater 2, cell values =
    counts).

    kappa = (po - pe) / (1 - pe), where po is the observed agreement rate and pe is the chance
    agreement rate implied by the marginal counts.
    Undefined, and so None, when the total count is 0 or pe = 1 (both raters used only the same
    single category).
    """
    t = [list(r) for r in table]
    k = len(t)
    if k == 0 or any(len(r) != k for r in t):
        raise ValueError("table must be k x k (k >= 1)")
    if any((not isinstance(v, (int, float))) or isinstance(v, bool) or v < 0 or not math.isfinite(v)
           for r in t for v in r):
        raise ValueError("table's counts must be finite, non-negative numbers")
    n = float(sum(sum(r) for r in t))
    if n == 0:
        return None
    po = sum(t[i][i] for i in range(k)) / n
    rows = [sum(t[i]) for i in range(k)]
    cols = [sum(t[i][j] for i in range(k)) for j in range(k)]
    pe = sum(rows[i] * cols[i] for i in range(k)) / (n * n)
    if pe == 1.0:
        return None
    return (po - pe) / (1.0 - pe)


def icc(matrix, kind="2,1"):
    """The intraclass correlation. matrix has n rows (subjects) by k columns (raters or methods),
    computed from the mean squares of a two-way analysis of variance.

      "1,1"  ICC(1,1)  one-way, single rater (does not separate out a rater effect)
      "2,1"  ICC(2,1)  two-way model, single rater, absolute agreement (a systematic difference
                        between raters also counts as disagreement); this is the primary L2 metric. Its
                        value is the same whether the raters are random (Shrout & Fleiss's ICC(2,1)) or the
                        only raters of interest (McGraw & Wong's mixed-model ICC(A,1)), as for the two
                        methods compared here
      "3,1"  ICC(3,1)  two-way mixed, single rater, consistency (a rater's constant offset does not
                        count as disagreement)

    Uses the formulas of Shrout & Fleiss (1979): BMS is the subjects' mean square, JMS the raters'
    mean square, EMS the residual, and WMS the within-subjects mean square.
    None when the denominator is 0 (every value identical across the board). Requires n >= 2 rows
    and k >= 2 columns; raises ValueError otherwise (a different case from "cannot be computed",
    since the caller should treat this as a malformed input).
    The whole matrix is first translated by a single constant before the computation (ICC is
    invariant under translation). This matters because when every value shares something like 0.1,
    rounding the grand mean can otherwise leave the denominator nonzero for the wrong reason and
    produce a meaningless value (whereas an exactly identical matrix gives deviations of exactly
    zero).
    """
    if kind not in ("1,1", "2,1", "3,1"):
        raise ValueError('kind must be "1,1", "2,1", or "3,1"')
    m = [list(r) for r in matrix]
    n = len(m)
    k = len(m[0]) if n else 0
    if n < 2 or k < 2 or any(len(r) != k for r in m):
        raise ValueError("matrix must be rectangular with n >= 2 rows and k >= 2 columns")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for r in m for v in r):
        raise ValueError("matrix's values must be finite numbers")
    base = m[0][0]
    m = [[v - base for v in r] for r in m]
    grand = sum(sum(r) for r in m) / (n * k)
    row_mean = [sum(r) / k for r in m]
    col_mean = [sum(m[i][j] for i in range(n)) / n for j in range(k)]
    ss_total = sum((v - grand) ** 2 for r in m for v in r)
    ss_rows = k * sum((rm - grand) ** 2 for rm in row_mean)
    ss_cols = n * sum((cm - grand) ** 2 for cm in col_mean)
    ss_err = ss_total - ss_rows - ss_cols
    ss_within = ss_total - ss_rows
    bms = ss_rows / (n - 1)
    jms = ss_cols / (k - 1)
    ems = ss_err / ((n - 1) * (k - 1))
    wms = ss_within / (n * (k - 1))
    if kind == "1,1":
        num, den = bms - wms, bms + (k - 1) * wms
    elif kind == "2,1":
        num, den = bms - ems, bms + (k - 1) * ems + k * (jms - ems) / n
    else:
        num, den = bms - ems, bms + (k - 1) * ems
    # The denominator is mathematically non-negative (n >= 2, k >= 2). If rounding makes it zero or
    # negative, treat the value as not computable.
    if den <= 0:
        return None
    return num / den


def median_abs_error(a, b):
    """The median absolute error of a set of pairs, median(|a - b|). None when there are zero
    pairs."""
    a, b = _paired(a, b)
    if not a:
        return None
    return statistics.median(abs(x - y) for x, y in zip(a, b))


# ---------------------------------------------------------------- selftest
# Shrout & Fleiss's (1979) worked example: 6 subjects (rows) by 4 raters (columns).
SF_MATRIX = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]
Z95 = 1.959963984540054


def _mean_of_clusters(cs):
    return sum(sum(c) for c in cs) / sum(len(c) for c in cs)


def selftest():
    fails = []
    count = [0]

    def check(name, cond, detail=""):
        count[0] += 1
        if not cond:
            fails.append("%s %s" % (name, detail))

    def near(name, got, want, tol):
        check(name, got is not None and abs(got - want) <= tol, "got %r want %r (tolerance %g)" % (got, want, tol))

    def raises(name, fn, exc=ValueError):
        count[0] += 1
        try:
            fn()
        except exc:
            return
        except Exception as e:  # a different exception type does not count as a rejection
            fails.append("%s: a different exception: %r" % (name, e))
            return
        fails.append("%s: no exception was raised" % name)

    # --- ICC: values from the literature (4 significant figures)
    near("ICC(1,1) = 0.1657 (Shrout & Fleiss)", icc(SF_MATRIX, kind="1,1"), 0.1657, 0.0005)
    near("ICC(2,1) = 0.2898 (Shrout & Fleiss)", icc(SF_MATRIX, kind="2,1"), 0.2898, 0.0005)
    near("ICC(3,1) = 0.7148 (Shrout & Fleiss)", icc(SF_MATRIX, kind="3,1"), 0.7148, 0.0005)
    near("ICC's default is 2,1", icc(SF_MATRIX), icc(SF_MATRIX, kind="2,1"), 0.0)
    # property: absolute agreement (2,1) counts a rater's systematic bias as disagreement, while
    # consistency (3,1) does not (this distinguishes the two types)
    shifted = [[r[0], r[1], r[2], r[3] + 5] for r in SF_MATRIX]
    near("ICC(3,1) is unchanged by adding a constant to one column", icc(shifted, kind="3,1"), icc(SF_MATRIX, kind="3,1"), 1e-9)
    check("ICC(2,1) drops when a constant is added to one column", icc(shifted, kind="2,1") < icc(SF_MATRIX, kind="2,1") - 0.05,
          "%r -> %r" % (icc(SF_MATRIX, kind="2,1"), icc(shifted, kind="2,1")))
    ident = [[1, 1], [3, 3], [2, 2], [5, 5]]
    for kd in ("1,1", "2,1", "3,1"):
        near("ICC(%s) is 1 under perfect agreement" % kd, icc(ident, kind=kd), 1.0, 1e-12)
    near("ICC is invariant under reordering the subjects (rows)", icc(list(reversed(SF_MATRIX)), kind="2,1"), icc(SF_MATRIX, kind="2,1"), 1e-12)
    near("ICC is invariant under reordering the raters (columns)", icc([list(reversed(r)) for r in SF_MATRIX], kind="2,1"),
         icc(SF_MATRIX, kind="2,1"), 1e-12)
    check("ICC is None when every value is identical", icc([[3, 3], [3, 3], [3, 3]]) is None)
    for kd in ("1,1", "2,1", "3,1"):
        # a value (0.1) for which rounding the grand mean does not make the denominator exactly zero
        # should still yield None
        check("ICC(%s) is None when every value is 0.1" % kd, icc([[0.1, 0.1]] * 3, kind=kd) is None,
              repr(icc([[0.1, 0.1]] * 3, kind=kd)))
        check("ICC(%s) is None when all three columns are 0.1" % kd, icc([[0.1] * 3] * 3, kind=kd) is None,
              repr(icc([[0.1] * 3] * 3, kind=kd)))
    raises("an unknown ICC kind", lambda: icc(SF_MATRIX, kind="2,k"))
    raises("a non-rectangular ICC matrix", lambda: icc([[1, 2], [3]]))
    raises("an ICC matrix with only one row", lambda: icc([[1, 2]]))

    # --- Wilson: closed-form checks
    near("Wilson's upper bound for 0/10 = z^2/(n+z^2) = 0.2775", wilson(0, 10)[1], 0.2775, 0.0005)
    near("Wilson's lower bound for 10/10 = n/(n+z^2) = 0.7225", wilson(10, 10)[0], 0.7225, 0.0005)
    near("Wilson's lower bound for 0/10 is 0", wilson(0, 10)[0], 0.0, 0.0)
    near("Wilson's upper bound for 10/10 is 1", wilson(10, 10)[1], 1.0, 0.0)
    near("Wilson's upper bound for 0/1 = z^2/(1+z^2)", wilson(0, 1)[1], Z95 ** 2 / (1 + Z95 ** 2), 1e-12)
    near("Wilson's upper bound for 0/10 at conf=0.90 (z = 1.6448536)", wilson(0, 10, conf=0.90)[1],
         1.6448536 ** 2 / (10 + 1.6448536 ** 2), 1e-6)
    check("Wilson at n = 0 is (None, None)", wilson(0, 0) == (None, None))
    inside, mirror, defining = True, True, True
    for n in list(range(1, 61)) + [1000]:
        for x in range(0, n + 1):
            lo, hi = wilson(x, n)
            if not (0.0 <= lo <= x / n <= hi <= 1.0):
                inside = False
            lo2, hi2 = wilson(n - x, n)
            if abs((1.0 - hi) - lo2) > 1e-12 or abs((1.0 - lo) - hi2) > 1e-12:
                mirror = False
            # defining equation: each endpoint p of the interval satisfies
            # (x/n - p)^2 = z^2 p (1 - p) / n (the inverted score test). Skip an endpoint that was
            # rounded to 0 or 1.
            for p in (lo, hi):
                if 0.0 < p < 1.0 and abs((x / n - p) ** 2 - Z95 ** 2 * p * (1 - p) / n) > 1e-12:
                    defining = False
    check("Wilson's interval always stays within [0, 1] and contains x/n (every x for n <= 60 and n = 1000)", inside)
    check("Wilson is mirror-symmetric between x and n - x", mirror)
    check("Wilson's endpoints satisfy the score-test defining equation", defining)
    raises("Wilson with x > n", lambda: wilson(5, 3))
    raises("Wilson with x < 0", lambda: wilson(-1, 3))
    raises("Wilson with conf = 1", lambda: wilson(1, 3, conf=1.0))

    # --- Cohen's kappa
    near("kappa([[20,5],[10,15]]) = 0.40 (po 0.70, pe 0.50)", cohen_kappa([[20, 5], [10, 15]]), 0.40, 0.0005)
    near("kappa 3x3 [[5,1,0],[1,5,1],[0,1,5]] = 41/60", cohen_kappa([[5, 1, 0], [1, 5, 1], [0, 1, 5]]), 41.0 / 60.0, 1e-12)
    # a table with asymmetric marginal counts: po = 0.60, pe = (60*70 + 40*30) / 100^2 = 0.54, so
    # kappa = 0.06 / 0.46 = 3/23.
    # [[20,5],[10,15]] has equal row marginals, so it cannot distinguish a rows-by-columns transpose
    # from a rows-by-rows mistake.
    near("kappa([[45,15],[25,15]]) = 3/23 (asymmetric marginal counts)", cohen_kappa([[45, 15], [25, 15]]), 3.0 / 23.0, 1e-12)
    near("kappa is invariant under swapping the raters (transposing the table)", cohen_kappa([[45, 25], [15, 15]]), cohen_kappa([[45, 15], [25, 15]]), 1e-12)
    near("kappa is 1 under perfect agreement", cohen_kappa([[10, 0], [0, 10]]), 1.0, 1e-12)
    near("kappa is 0 when the marginals are independent", cohen_kappa([[10, 10], [10, 10]]), 0.0, 1e-12)
    near("kappa is -1 under complete disagreement", cohen_kappa([[0, 10], [10, 0]]), -1.0, 1e-12)
    check("kappa is None when only one category is used (pe = 1)", cohen_kappa([[10, 0], [0, 0]]) is None)
    check("kappa is None when the total count is 0", cohen_kappa([[0, 0], [0, 0]]) is None)
    raises("a kappa table that is not k x k", lambda: cohen_kappa([[1, 2, 3], [4, 5, 6]]))
    raises("a kappa table with a negative count", lambda: cohen_kappa([[1, -1], [1, 1]]))

    # --- Bland-Altman
    ba = bland_altman([2, 3, 4, 5, 6], [1, 1, 1, 1, 1])
    near("BA bias = 3.0 (differences 1..5)", ba["bias"], 3.0, 1e-12)
    near("BA sd = 1.5811 (sample standard deviation)", ba["sd"], 1.5811, 0.0005)
    near("BA's upper limit of agreement = 3.0 + 1.96 * 1.5811", ba["loa_hi"], 3.0 + 1.96 * 1.5811388300841898, 1e-9)
    near("BA's lower limit of agreement = 3.0 - 1.96 * 1.5811", ba["loa_lo"], 3.0 - 1.96 * 1.5811388300841898, 1e-9)
    check("BA's n", ba["n"] == 5)
    rev = bland_altman([1, 1, 1, 1, 1], [2, 3, 4, 5, 6])
    near("swapping a and b in BA flips the sign of the bias", rev["bias"], -3.0, 1e-12)
    near("swapping a and b in BA leaves sd unchanged", rev["sd"], ba["sd"], 1e-12)
    one = bland_altman([4], [1])
    check("BA at n = 1 has sd and the limits of agreement as None", one["bias"] == 3 and one["sd"] is None and one["loa_lo"] is None)
    check("BA at n = 0 is entirely None", bland_altman([], [])["bias"] is None)
    raises("BA with mismatched lengths", lambda: bland_altman([1, 2], [1]))

    # --- median absolute error
    near("MAE with an odd number of pairs", median_abs_error([1, 2, 3], [2, 2, 5]), 1.0, 1e-12)
    near("MAE with an even number of pairs is the average of the middle two", median_abs_error([0, 1, 2, 3], [0, 0, 0, 0]), 1.5, 1e-12)
    near("MAE ignores sign", median_abs_error([5, 5, 5], [1, 9, 1]), 4.0, 1e-12)
    check("MAE with zero pairs is None", median_abs_error([], []) is None)
    raises("MAE with mismatched lengths", lambda: median_abs_error([1], [1, 2]))

    # --- cluster bootstrap
    cl = [[1.0, 1.2, 0.9], [2.0, 2.1], [0.5, 0.4, 0.6, 0.5], [1.5]]
    a = cluster_bootstrap(cl, _mean_of_clusters, B=500, seed=7)
    b = cluster_bootstrap(cl, _mean_of_clusters, B=500, seed=7)
    check("cluster_bootstrap gives an identical interval for the same seed", a == b, "%r %r" % (a, b))
    # Checking that changing the seed changes the result requires a sample with continuous values
    # and enough clusters. With only 4 clusters, the mean takes at most 256 discrete values, so the
    # 2.5% / 97.5% points can land on the same extreme value for different seeds, making the
    # intervals coincide (observed in practice).
    rich = [[0.31 * i % 1.7 + 0.11 * j for j in range(1 + i % 3)] for i in range(12)]
    s1 = cluster_bootstrap(rich, _mean_of_clusters, B=500, seed=1)
    s1b = cluster_bootstrap(rich, _mean_of_clusters, B=500, seed=1)
    s2 = cluster_bootstrap(rich, _mean_of_clusters, B=500, seed=2)
    check("cluster_bootstrap gives the same interval for the same seed on a continuous-valued sample", s1 == s1b, "%r %r" % (s1, s1b))
    check("cluster_bootstrap's interval changes when the seed changes", s1 != s2, "%r %r" % (s1, s2))
    g0 = random.getstate()
    cluster_bootstrap(cl, _mean_of_clusters, B=200, seed=1)
    check("cluster_bootstrap does not touch the global random state", random.getstate() == g0)
    check("cluster_bootstrap's interval satisfies lo <= hi", a[0] <= a[1])
    # closed form: with two clusters [1.0] and [3.0], the resampled mean takes {1, 2, 3} with
    # probability 1/4, 1/2, 1/4. The 2.5th percentile is 1.0 and the 97.5th is 3.0, so the percentile
    # interval is exactly (1.0, 3.0).
    two = cluster_bootstrap([[1.0], [3.0]], _mean_of_clusters, B=4000, seed=3)
    check("cluster_bootstrap matches the percentile method's closed form (1.0, 3.0)", two == (1.0, 3.0), repr(two))
    # pin down the quantile definition (linear interpolation): if stat returns 0, 1, ..., 1000
    # regardless of the sample, then with B = 1001 the replicate values are exactly 0..1000, so the
    # 95% interval is (25.0, 975.0) and the 90% interval is (50.0, 950.0).
    def counter():
        state = [-1]

        def f(cs):
            state[0] += 1
            return float(state[0])
        return f

    # (1 - conf) / 2 carries a floating-point error such as 0.025000000000000022, so compare with a
    # tolerance rather than ==.
    q95 = cluster_bootstrap(cl, counter(), B=1001, seed=1)
    near("cluster_bootstrap's 95% interval's lower quantile = 25.0", q95[0], 25.0, 1e-9)
    near("cluster_bootstrap's 95% interval's upper quantile = 975.0", q95[1], 975.0, 1e-9)
    q90 = cluster_bootstrap(cl, counter(), B=1001, seed=1, conf=0.90)
    near("cluster_bootstrap's 90% interval's lower quantile = 50.0", q90[0], 50.0, 1e-9)
    near("cluster_bootstrap's 90% interval's upper quantile = 950.0", q90[1], 950.0, 1e-9)
    # sanity check against the standard error: for 10 clusters of one element each with values 0..9,
    # the mean's SE = sqrt(8.25 / 10) = 0.9083, giving an interval width of about 3.56.
    ten = cluster_bootstrap([[float(v)] for v in range(10)], _mean_of_clusters, B=4000, seed=11)
    check("cluster_bootstrap's interval width for the mean of 10 clusters matches the standard error", 3.2 <= ten[1] - ten[0] <= 3.9, repr(ten))
    check("cluster_bootstrap's interval brackets the sample mean", ten[0] < 4.5 < ten[1], repr(ten))
    # clusters are never broken apart: each cluster passed to stat is one of the originals intact,
    # and the count matches the original. Duplication and omission actually occur.
    originals = [(1.0, 1.5), (2.0, 2.5, 2.75), (3.0,), (4.0, 4.5), (5.0, 5.25, 5.5, 5.75)]
    seen = {"foreign": 0, "wrong_k": 0, "dup": 0, "missing": 0}

    def spy(cs):
        if len(cs) != len(originals):
            seen["wrong_k"] += 1
        if any(c not in originals for c in cs):
            seen["foreign"] += 1
        if len(set(cs)) < len(cs):
            seen["dup"] += 1
        if len(set(cs)) < len(originals):
            seen["missing"] += 1
        return 0.0

    cluster_bootstrap([list(o) for o in originals], spy, B=300, seed=5)
    check("cluster_bootstrap draws only the original clusters, in the original count", seen["foreign"] == 0 and seen["wrong_k"] == 0, repr(seen))
    check("cluster_bootstrap samples with replacement (duplication and omission actually occur)", seen["dup"] > 0 and seen["missing"] > 0, repr(seen))
    # positive control: when each cluster is constant (within-cluster correlation of 1), the
    # interval is wider than resampling at the element level. 10 clusters x 20 elements: the
    # cluster-level SE ~ sigma / sqrt(10), the element-level SE ~ sigma / sqrt(200) (about 4.5 times
    # smaller).
    const_clusters = [[float(v)] * 20 for v in range(10)]
    flat_as_clusters = [[x] for c in const_clusters for x in c]
    w_cluster = cluster_bootstrap(const_clusters, _mean_of_clusters, B=2000, seed=21)
    w_element = cluster_bootstrap(flat_as_clusters, _mean_of_clusters, B=2000, seed=21)
    wc, we = w_cluster[1] - w_cluster[0], w_element[1] - w_element[0]
    check("within-cluster correlation widens the interval (cluster-level is more than twice element-level)", wc > 2.0 * we, "cluster %.4f element %.4f" % (wc, we))
    check("within-cluster correlation widens the interval: the element-level interval comes out narrow (a negative-side check)", we < 1.0, "element %.4f" % we)
    check("cluster_bootstrap with a single cluster is (None, None)", cluster_bootstrap([[1.0, 2.0]], _mean_of_clusters, 10, 1) == (None, None))
    check("cluster_bootstrap with zero clusters is (None, None)", cluster_bootstrap([], _mean_of_clusters, 10, 1) == (None, None))
    raises("cluster_bootstrap when stat returns None", lambda: cluster_bootstrap(cl, lambda cs: None, 10, 1))
    raises("cluster_bootstrap when stat returns nan", lambda: cluster_bootstrap(cl, lambda cs: float("nan"), 10, 1))
    raises("cluster_bootstrap with B = 0", lambda: cluster_bootstrap(cl, _mean_of_clusters, 0, 1))
    raises("cluster_bootstrap with a non-integer seed", lambda: cluster_bootstrap(cl, _mean_of_clusters, 10, "a"))
    raises("cluster_bootstrap with conf = 0", lambda: cluster_bootstrap(cl, _mean_of_clusters, 10, 1, conf=0.0))

    if fails:
        for f in fails:
            print("NG: " + f)
        print("selftest failed %d / %d" % (len(fails), count[0]))
        return 1
    print("selftest OK (%d checks)" % count[0])
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Statistical building blocks for evaluations E1 / E2 "
                                              "(a library module; --selftest is its only standalone entry point)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
