#!/usr/bin/env python3
"""Generate the result tables and the values quoted in the text of the manuscript from the result files.

  python3 manuscript/tools/results_tables.py --results experiment/results --out manuscript/generated
  python3 manuscript/tools/results_tables.py --results experiment/results --out manuscript/generated --check
  python3 manuscript/tools/results_tables.py --selftest

Every number of the results that the manuscript states is copied by this script from the result files of
`kit/analysis/pipeline.py` (questions 1 to 3 and the delivery report), `kit/e2/closed_set.py` (question 4),
`kit/e3/summarize_e3.py` (question 5) and `kit/pilot/clock_sync.py` (the marker times of section 6.9, one file
`clock_sync_<cell>.json` per cell); none is typed by hand. The script reads the cells of the macOS analysis from the
top of the results directory and the Windows cell from its subdirectory `windows`, and the corrected analysis of
deviation D10 from `d10/macos` and `d10/windows`; a cell whose files are absent is reported as not measured, and a cell
without marker times as not computed. It writes:

- values.json: every value with its full precision and its source (file and path inside the file);
- values.tex: the formatted values, read in the text with the macro \\rv{<key>} that main.tex defines;
- table-reference.tex: what the reference establishes (Table 7: plan against ground truth by type, delivery of the
  presses, losses of focus with the session rows of the trials concerned, marker times), one column per cell;
- table-key-timing.tex: the timing of the key rows of presses (KT1) per cell and speed;
- reference-strata.tex: for the supplement, the plan against the ground truth per stratum, the marker times per
  block and the timing of the key-downs of the reference;
- table-detection.tex: detection per cell, comparison (three on macOS, the primary one on Windows) and speed (the
  range of the per-scenario intervals);
- detection-layers.tex: for the supplement, detection per layer of the prediction (macOS) for every comparison and
  scope, with the corrected merge rate of deviation D10;
- table-session.tex: per-session agreement per cell and speed;
- table-text-agreement.tex: per-text agreement of the four measures of section 4.6 per cell and speed (primary scope);
- text-agreement.tex: for the supplement, per-text agreement over all trials, pooled over speeds and in the three
  mixtures of section 6.7 (whose weights are checked), and the per-trial differences per scenario and speed;
- table-closed-set.tex: the near-chance judgement of section 6.8 per cell, set and K, and the recommendation;
- table-open-set.tex: the open-set reconstruction per cell, attacker (K0, K1, K1+) and knowledge of the length;
- table-cost.tex: runtime cost per system and condition (sections 6.10 and 10.9);
- detection-strata.tex: the per-stratum detection of every cell, with the merge rate of the corrected analysis of
  deviation D10 next to the registered one, for the supplement (a cell that was not measured is stated as such);
- closed-set-all.tex: every feature set at every K per cell, with the raw accuracy per speed and scenario, for the
  supplement;
- fig-detection.tex, fig-dwell.tex, fig-text-agreement.tex and fig-closed-set.tex: Figures 2 to 5 (recall and
  precision per stratum; the difference of dwell_ms per stratum; the per-text agreement; the closed-set accuracy
  against K), as pgfplots code with the coordinates inline, drawn with the styles defined in main.tex.

Question 4 (section 6.8, and section 10.7 for Windows): a feature set is near chance at K when the upper end of the
Wilson interval of its reported accuracy (the maximum over the set and the sets derivable from it) is at most 2/K; it
is near chance for the rule when it is so at K = 10 and K = 100. The recommendation is the most detailed of no_time and
counts_only that is near chance in both macOS cells, else none; without both macOS cells it is not decided. The
Windows judgement of the same set is its qualification and never changes it. The closed-set files must be those of
deviation D8 (every trial of S12 left out). Question 5: a condition is quoted only when its status is measured, and on
Windows a working condition whose every CPU sample is zero is insufficient (section 10.9).

The values of the abstract follow the rule fixed in outline.md before the results ("Rule for the abstract"): for
recall and precision, the range from the lowest lower end to the highest upper end of the Wilson intervals of the
strata that keep at least MIN_TRIALS trials in the scope, with the number of strata left out, the fractions of trials
set aside by the scope and by carry-over, and the same range over all trials; for the dwell error, the range of the
per-speed intervals of the bias. Proportions are printed with three decimals and durations in whole milliseconds
(Python's rounding of the binary value); values.json keeps the unrounded values.

With --check nothing is written: the files that would be written are compared with those in the output directory and
the script exits 1 when any differs, so that a table cannot drift from the result files.

Standard library only.
"""
import argparse
import collections
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile

MIN_TRIALS = 30
PRIMARY_COMPARISON = "gt_obs"
PRIMARY_SCOPE = "under_limit"
SCOPES = ("under_limit", "all_trials")
SPEEDS = ("slow", "mid", "fast")
FILES = ("e1_detection.json", "e1_l1.json", "e1_l2.json")
# The labels of the three cells of the measurement (experiment/data.md and experiment/data.windows.md).
EXPECTED_CELLS = ("macos-standard-live-on", "macos-standard-live-off", "windows-msime-tsf")
MACOS_CELLS = EXPECTED_CELLS[:2]
# The names under which the tables and figures show the cells (the labels stay the keys of the values).
CELL_NAMES = {"macos-standard-live-on": "macOS, live conversion on",
              "macos-standard-live-off": "macOS, live conversion off", "windows-msime-tsf": "Windows"}
WINDOWS_CELL = EXPECTED_CELLS[2]
# Question 4 (section 6.8 of the pre-registration). The feature sets of the closed set, from the most detailed; the
# rule considers only the two sets that keep no time, the most detailed first (counts_only is a coarsening of
# no_time), and only K = 10 and K = 100 (K = 1,000 is descriptive).
CLOSED_SET_SETS = ("all", "no_runs", "bin1s", "bin1s_count", "no_time", "counts_only")
RULE_SETS = ("no_time", "counts_only")
RULE_KS = (10, 100)
CLOSED_SET_KS = (10, 100, 1000)
# Deviation D8: the closed set leaves out every trial of this scenario in every cell.
CLOSED_SET_SKIPPED = "S12"
# The open set (section 6.8): the attackers as (split, model, label); K1+ is K1 with the corpus split by phrase.
OPEN_SET_ATTACKERS = (("work", "K0", "K0"), ("work", "K1", "K1"), ("phrase", "K1", "K1+"),
                      ("phrase", "K0", "K0 (split by phrase)"))
OPEN_SET_LENGTHS = (("count", "number of segments"), ("true", "true length"))
# Question 5 (sections 6.10 and 10.9): one summary file per system, one block per condition.
E3_CONDITIONS = ("idle", "working")
E3_FILES = (("", "macos", "e3_macos.txt"), ("windows", "windows", "e3_windows.txt"))
# Question 3 (sections 4.6 and 6.7): the four per-text measures in the order of section 4.6, with their labels and
# whether the measure is a duration in milliseconds; the three mixtures of speeds with their weights (slow, mid, fast).
L2_MEASURES = (("conversions_per_trial", "Conversions per trial", False),
               ("mean_dwell_ms", "Mean conversion time (ms)", True),
               ("abandon_rate", "Abandonment rate", False),
               ("dwell_fraction", "Fraction of time converting", False))
L2_MIXTURES = (("slow_heavy", "0.6/0.3/0.1", (0.6, 0.3, 0.1)), ("uniform", "equal", (1 / 3.0, 1 / 3.0, 1 / 3.0)),
               ("fast_heavy", "0.1/0.3/0.6", (0.1, 0.3, 0.6)))
# The three views of the per-trial differences (section 6.7); the last two need the layers of the prediction (macOS).
L2_VIEWS = (("per_trial", "every trial of the scope"),
            ("per_trial_uncontaminated", "trials in neither carry layer (for reweighting)"),
            ("per_trial_layer_none", "trials of the layer none (diagnostic)"))
L2_TRIAL_MEASURES = (("n_conversions", "Conversions", False), ("dwell_sum_ms", "Summed conversion time (ms)", True),
                     ("n_cancelled", "Cancelled conversions", False))
# Section 6.2: the only types of excluded trials (kit/analysis/match.py).
EXCLUSION_TYPES = ("clock_jump", "log_truncated", "carry_over")
# Deviation D10: the corrected analysis of questions 1 to 3, per system.
D10_DIRS = {"macos": os.path.join("d10", "macos"), "windows": os.path.join("d10", "windows")}
# Section 6.4: the three comparisons (the two with the prediction exist on macOS only, section 10.7) and the four
# layers of the prediction, in the order of the registration.
COMPARISONS = (("gt_obs", "Reference vs. logger (primary)"), ("gt_pred", "Reference vs. specified rows"),
               ("pred_obs", "Specified rows vs. logger"))
COMPARISONS_OF = {"macos": ("gt_obs", "gt_pred", "pred_obs"), "windows": ("gt_obs",)}
LAYERS = (("carried_out", "carried out"), ("received_carry", "received carry"),
          ("closing_key_in_reading", "closing key in reading"), ("none", "none of the three"))
LAYER_SCOPES = ("under_limit", "over_limit", "all_trials")
# Section 6.3: the types of disagreement between the plan and the ground truth, in the order of the registration
# (kit/analysis/gt_sessions.py, MISMATCH_TYPES).
PLAN_TYPES = (("no_session", "No session"), ("unexpected_session", "An unexpected session"),
              ("multiple_sessions", "Several sessions"),
              ("expected_cancel_got_commit", "A commit where a cancellation was planned"),
              ("expected_commit_got_cancel", "A cancellation where a commit was planned"),
              ("open_at_end", "A session open at the end"), ("unknown_trial", "A trial not in the plan"))
# The delivery report (section 6.3, kit/analysis/delivery.py) and the timing of the key rows (KT1).
DELIVERY_FILE = "e1_delivery.json"
DELIVERY_SIDES = (("observation", "Observation"), ("reference", "Reference"))
REACH_VERSIONS = (("all_presses", "all presses"), ("excluding_mode_keys", "input-mode keys left out"))
TIMING_MEASURES = (("delay_ms", "Delay"), ("interval_error_ms", "Interval error"))
TIMING_STATS = ("n", "mean", "min", "p05", "median", "p95", "max")
# Section 6.9: the differences at the block-start markers (kit/pilot/clock_sync.py), one file per cell.
CLOCK_DIFFS = (("obs_minus_oracle_wall", "Observation minus executor"),
               ("gt_minus_oracle_wall", "Reference minus executor"),
               ("gt_minus_oracle_mono", "Reference minus executor, monotonic clocks"),
               ("obs_minus_gt_wall", "Observation minus reference"))
GENERATED_NOTE = "% Generated by manuscript/tools/results_tables.py from the result files; do not edit by hand.\n"


# ---------------------------------------------------------------- reading


def load(path):
    with open(path, "rb") as f:
        doc = json.loads(f.read().decode("utf-8"))
    check_names(doc, path)
    return doc


# The scenario names of the plans (S1 to S15). A scenario name and a speed are printed into the generated TeX (table
# rows, figure ticks, the diagnostics in the text), so a result file that holds any other name or speed stops the
# script, as a cell label outside CELL_RE does.
SCENARIO_RE = re.compile(r"^S(?:[1-9]|1[0-5])$")
# The keys under which a result file names scenarios by the keys of a mapping.
SCENARIO_KEYED = ("by_scenario", "tsf_end_without_result_len")


def check_names(doc, path):
    """Stop when a stratum of the result file names a scenario or a speed that the plans do not use, or when a mapping
    keyed by scenario has another key."""
    stack = [doc]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not isinstance(x, dict):
            continue
        st = x.get("stratum")
        if isinstance(st, dict):
            sc, sp = st.get("scenario"), st.get("speed")
            if sc is not None and not (isinstance(sc, str) and SCENARIO_RE.fullmatch(sc)):
                raise ValueError("%s: the scenario name %r is not one of the plans" % (path, sc))
            if sp is not None and sp not in SPEEDS:
                raise ValueError("%s: the speed %r is not one of the plans" % (path, sp))
        for k in SCENARIO_KEYED:
            if isinstance(x.get(k), dict):
                bad = [n for n in x[k] if not SCENARIO_RE.fullmatch(n)]
                if bad:
                    raise ValueError("%s: the scenario names %r are not those of the plans" % (path, bad))
        stack.extend(x.values())


def is_count(v):
    """A non-negative integer (a JSON true or false is not one)."""
    return type(v) is int and v >= 0


# The cell labels that the analysis accepts (kit/analysis/match.py, CELL_RE); a label is also used in file names, TeX
# labels and macro names, so any other label stops the script.
CELL_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")


def find_cells(results_dir):
    """[(directory, file prefix for the source, cell label, system)] for every cell whose three files exist."""
    found = []
    for sub, system in (("", "macos"), ("windows", "windows")):
        d = os.path.join(results_dir, sub) if sub else results_dir
        if not all(os.path.isfile(os.path.join(d, f)) for f in FILES):
            continue
        det = load(os.path.join(d, "e1_detection.json"))
        for label in sorted(det["cells"]):
            if not CELL_RE.fullmatch(label):
                raise ValueError("%se1_detection.json: the cell label %r is not one the analysis accepts" % (
                    (sub + "/") if sub else "", label))
            found.append((d, (sub + "/") if sub else "", label, system))
    return found


def definition_of(system):
    """The session definition that the analysis uses for a cell of this system (experiment/notes.md)."""
    return "windows_def" if system == "windows" else "macos_def"


# The definition common to both systems (every session).
COMMON_DEFINITION = "all"


def stratum_key(st):
    return (st["scenario"], st["speed"])


def strata_of(block):
    """{(scenario, speed): stratum entry} of a detection block of one comparison and scope (the row of presses outside
    the trials is left out)."""
    out = {}
    for st in block["metrics"]["by_stratum"]:
        if st["stratum"] in (None, "all") or st["stratum"].get("scenario") is None:
            continue
        out[stratum_key(st["stratum"])] = st
    return out


def scenario_order(name):
    return int(name[1:]) if name[1:].isdigit() else 10 ** 6


class Cell(object):
    """The values of one cell, read from its three result files, with the source of each value."""

    def __init__(self, directory, prefix, label, system):
        self.label, self.system = label, system
        self.directory, self.prefix = directory, prefix
        self.definition = definition_of(system)
        self.src = {}
        docs = {}
        for f in FILES:
            docs[f] = load(os.path.join(directory, f))
            self.src[f] = prefix + f
        for f in FILES:
            prim = docs[f].get("primary") or {}
            if prim.get("comparison") != PRIMARY_COMPARISON or prim.get("scope") != PRIMARY_SCOPE:
                raise ValueError("%s: the primary comparison and scope are not %s under %s" % (
                    self.src[f], PRIMARY_COMPARISON, PRIMARY_SCOPE))
        self.det, self.l1, self.l2 = (docs[f]["cells"][label][self.definition][PRIMARY_COMPARISON] for f in FILES)
        for name, block in (("e1_detection.json", self.det), ("e1_l1.json", self.l1), ("e1_l2.json", self.l2)):
            for scope in SCOPES:
                if not block.get(scope):
                    raise ValueError("%s: no %s under %s for cell %s" % (self.src[name], scope, self.definition, label))
        # Every comparison of the detection results; the system decides which exist (section 10.7).
        definition_block = docs["e1_detection.json"]["cells"][label][self.definition]
        self.comparisons = tuple(c for c, _ in COMPARISONS if definition_block.get(c))
        if self.comparisons != COMPARISONS_OF[system]:
            raise ValueError("%s: cell %s holds the comparisons %s, not those of %s" % (
                self.src["e1_detection.json"], label, ", ".join(self.comparisons), system))
        self.det_by = dict((c, definition_block[c]) for c in self.comparisons)
        # The detection of the primary comparison under the definition common to both systems, which the analysis
        # also runs (sections 5 and 10.7 of the registration); deviation D12 reports it where the two differ.
        common = (docs["e1_detection.json"]["cells"][label].get(COMMON_DEFINITION) or {}).get(PRIMARY_COMPARISON) or {}
        for scope in SCOPES:
            if not common.get(scope):
                raise ValueError("%s: no %s under %s for cell %s" % (self.src["e1_detection.json"], scope,
                                                                      COMMON_DEFINITION, label))
        self.det_common = common
        l1_block = docs["e1_l1.json"]["cells"][label][self.definition]
        if tuple(c for c, _ in COMPARISONS if l1_block.get(c)) != self.comparisons:
            raise ValueError("%s: cell %s does not hold the comparisons of the detection results" % (
                self.src["e1_l1.json"], label))
        self.l1_by = dict((c, l1_block[c]) for c in self.comparisons)
        # The summaries of the detection file outside the cells (the prediction, the two definitions, and on Windows
        # the diagnostics and the long trials of both sources).
        self.det_top = dict((k, (v or {}).get(label)) for k, v in docs["e1_detection.json"].items()
                            if k in ("predictions", "same_sessions_as_all", "windows_diagnostics", "long_trials_from"))
        # The delivery report of the same analysis (it has no primary comparison or scope).
        p = os.path.join(directory, DELIVERY_FILE)
        self.src[DELIVERY_FILE] = prefix + DELIVERY_FILE
        if not os.path.isfile(p):
            raise ValueError("%s: missing" % self.src[DELIVERY_FILE])
        dl = load(p)
        if label not in (dl.get("cells") or {}):
            raise ValueError("%s: no cell %s" % (self.src[DELIVERY_FILE], label))
        self.dlv = dl["cells"][label]

    def path(self, f, *keys):
        return "%s:cells/%s/%s/%s/%s" % (self.src[f], self.label, self.definition, PRIMARY_COMPARISON,
                                         "/".join(str(k) for k in keys))

    def dpath(self, *keys):
        return "%s:cells/%s/%s" % (self.src[DELIVERY_FILE], self.label, "/".join(str(k) for k in keys))

    def plan_agreement(self):
        """The plan-versus-ground-truth report (section 6.3). It covers every trial of the oracle whatever the scope
        (kit/analysis/l1.py, oracle_report), so it must be the same in both scopes."""
        o = self.l1[PRIMARY_SCOPE]["oracle"]
        if any(self.l1[s]["oracle"] != o for s in SCOPES):
            raise ValueError("%s: the plan-versus-ground-truth report differs between scopes" % self.path(
                "e1_l1.json", "*", "oracle"))
        if not o.get("plan_available"):
            raise ValueError("%s: the plan-versus-ground-truth report has no plan" % self.path(
                "e1_l1.json", PRIMARY_SCOPE, "oracle"))
        return o

    def kept_trials(self, scope):
        """{(scenario, speed): trials kept in the scope}, from the per-trial records of level 2, and the totals."""
        pt = self.l2[scope]["per_trial"]
        kept, total = {}, 0
        for st in pt["by_stratum"]:
            k = stratum_key(st["stratum"])
            kept[k] = st["n_trials"]
            tnu = st["trials_not_used"]
            total += st["n_trials"] + tnu["excluded"] + tnu["not_run"] + tnu["out_of_scope"]
        if sum(kept.values()) != pt["n_trials"]:
            raise ValueError("%s: the per-stratum trials do not add up to n_trials" % self.path("e1_l2.json", scope))
        return kept, total, pt["trials_not_used"]

    def detection_strata(self, scope, comparison=PRIMARY_COMPARISON):
        """{(scenario, speed): stratum entry of e1_detection.json} (the row of presses outside the trials is left out)."""
        return strata_of(self.det_by[comparison][scope])

    def speed_sessions(self, scope):
        return dict((st["stratum"]["speed"], st) for st in self.l1[scope]["metrics"]["by_speed"])


# ---------------------------------------------------------------- values


NO_VALUE = "--"


def fmt_p(x):
    """A proportion; NO_VALUE when the result file holds no value (no denominator, or deviation D10)."""
    return NO_VALUE if x is None else "%.3f" % x


def est_ci(value, lo, hi, fmt=None):
    """An estimate with its interval; the estimate alone when the interval has no value, NO_VALUE without estimate."""
    fmt = fmt or fmt_p
    if value is None:
        return NO_VALUE
    if lo is None or hi is None:
        return fmt(value)
    return "%s [%s, %s]" % (fmt(value), fmt(lo), fmt(hi))


def fmt_ms(x):
    """Whole milliseconds with a typeset minus sign. A half is rounded to the even neighbour (Python's round), so 178.5
    is written 178."""
    if x is None:
        return NO_VALUE
    s = "{:,}".format(int(round(x)))
    return "$-$" + s[1:] if s.startswith("-") else s


def fmt_pct(x):
    return "%.1f\\%%" % (100.0 * x)


def fmt_int(n):
    return "{:,}".format(n).replace(",", "{,}")


def interval_range(entries):
    """(lowest lower end, highest upper end) of entries {"lo", "hi"}."""
    return min(e["lo"] for e in entries), max(e["hi"] for e in entries)


SPEED_WORDS = {"slow": "slow", "mid": "middle", "fast": "fast"}


def strata_names(keys):
    """Strata (scenario, speed) in the given order, grouped by scenario, with the speeds named as the text names them:
    "S6 (slow), S13 (slow, middle, fast)"; "none"."""
    groups = []
    for sc, sp in keys:
        if groups and groups[-1][0] == sc:
            groups[-1][1].append(SPEED_WORDS[sp])
        else:
            groups.append((sc, [SPEED_WORDS[sp]]))
    return ", ".join("%s (%s)" % (sc, ", ".join(sps)) for sc, sps in groups) if groups else "none"


def strata_in_range(kept, strata, keys, measure):
    """The strata of keys that enter the range of a measure: at least MIN_TRIALS trials kept in the scope and an
    interval of the measure in the result file. The rule leaves out, per measure, the strata with fewer trials or with
    no value, so a stratum without a value of precision (no session row) still enters the range of recall."""
    return [k for k in keys if kept[k] >= MIN_TRIALS and k in strata and strata[k][measure]["lo"] is not None
            and strata[k][measure]["hi"] is not None]


def abstract_values(cell):
    """The values of items (i) and (ii) of the rule for the abstract, with their sources."""
    vals = {}
    for scope in SCOPES:
        kept, total, not_used = cell.kept_trials(scope)
        strata = cell.detection_strata(scope)
        design = sorted(kept, key=lambda k: (scenario_order(k[0]), SPEEDS.index(k[1])))
        tag = "primary" if scope == PRIMARY_SCOPE else "all"
        for m in ("recall", "precision"):
            used = strata_in_range(kept, strata, design, m)
            # The key exists also when no stratum enters the range, so that the text never loses it silently.
            lo, hi = interval_range([strata[k][m] for k in used]) if used else (None, None)
            vals["%s.%s.%s.range" % (cell.label, tag, m)] = {
                "text": "%s--%s" % (fmt_p(lo), fmt_p(hi)) if used else NO_VALUE, "value": [lo, hi],
                "source": cell.path("e1_detection.json", scope, "metrics/by_stratum/*", m, "lo|hi")}
            vals["%s.%s.%s.strata.used" % (cell.label, tag, m)] = {
                "text": str(len(used)), "value": len(used),
                "source": cell.path("e1_l2.json", scope, "per_trial/by_stratum")}
            vals["%s.%s.%s.strata.left" % (cell.label, tag, m)] = {
                "text": str(len(design) - len(used)), "value": len(design) - len(used),
                "source": cell.path("e1_l2.json", scope, "per_trial/by_stratum")}
            left = [k for k in design if k not in used]
            vals["%s.%s.%s.strata.leftnames" % (cell.label, tag, m)] = {
                "text": strata_names(left), "value": [list(k) for k in left],
                "source": cell.path("e1_l2.json", scope, "per_trial/by_stratum")}
        if scope == PRIMARY_SCOPE:
            out_of_scope = not_used["out_of_scope"]
            ex = cell.det[scope]["excluded"]["trials"]
            # A trial can be excluded for two types (kit/analysis/match.py counts distinct trials per type), and every
            # excluded trial has a type: the largest count is at most the total and the sum at least the total.
            counts = list(ex["by_type"].values())
            if set(ex["by_type"]) - set(EXCLUSION_TYPES) or not is_count(ex["n"]) \
                    or not all(is_count(v) for v in counts) or max(counts + [0]) > ex["n"] or sum(counts) < ex["n"]:
                raise ValueError("%s: the excluded trials are not counted by the types of section 6.2" % cell.path(
                    "e1_detection.json", scope, "excluded/trials"))
            carry = ex["by_type"].get("carry_over", 0)
            vals["%s.trials.total" % cell.label] = {
                "text": fmt_int(total), "value": total, "source": cell.path("e1_l2.json", scope, "per_trial")}
            vals["%s.scope.setaside" % cell.label] = {
                "text": "%s of %s trials (%s)" % (fmt_int(out_of_scope), fmt_int(total),
                                                  fmt_pct(float(out_of_scope) / total)),
                "value": [out_of_scope, total],
                "source": cell.path("e1_l2.json", scope, "per_trial/trials_not_used/out_of_scope")}
            vals["%s.carryover" % cell.label] = {
                "text": "%s of %s trials (%s)" % (fmt_int(carry), fmt_int(total), fmt_pct(float(carry) / total)),
                "value": [carry, total],
                "source": cell.path("e1_detection.json", scope, "excluded/trials/by_type/carry_over")}
            # The same two fractions as percentages alone, for the abstract.
            for key in ("scope.setaside", "carryover"):
                full = vals["%s.%s" % (cell.label, key)]
                vals["%s.%s.pct" % (cell.label, key)] = {
                    "text": fmt_pct(float(full["value"][0]) / full["value"][1]), "value": full["value"],
                    "source": full["source"]}
        sp = cell.speed_sessions(scope)
        cis = [sp[s]["dwell"]["bias_ci"] for s in SPEEDS if s in sp and sp[s]["dwell"]["bias_ci"][0] is not None
               and sp[s]["dwell"]["bias_ci"][1] is not None]
        lo, hi = (min(c[0] for c in cis), max(c[1] for c in cis)) if cis else (None, None)
        src = cell.path("e1_l1.json", scope, "metrics/by_speed/*/dwell/bias_ci")
        vals["%s.%s.dwellbias.range" % (cell.label, tag)] = {
            "text": "%s to %s~ms" % (fmt_ms(lo), fmt_ms(hi)) if cis else NO_VALUE, "value": [lo, hi], "source": src}
        # The speeds whose interval enters the range, and those left out (no pair, or the interval withheld).
        vals["%s.%s.dwellbias.speeds.used" % (cell.label, tag)] = {"text": str(len(cis)), "value": len(cis),
                                                                   "source": src}
        vals["%s.%s.dwellbias.speeds.left" % (cell.label, tag)] = {"text": str(len(SPEEDS) - len(cis)),
                                                                   "value": len(SPEEDS) - len(cis), "source": src}
    return vals


# ---------------------------------------------------------------- question 4: closed set and the recommendation


def closed_set_name(label):
    return "e2_closed_set_%s.json" % label


def read_closed_set(cell):
    """The closed-set result of a cell, or None when its file is absent. The file must be the analysis of deviation
    D8 (every trial of S12 left out) and hold every feature set at every K with the chance level 1/K."""
    p = os.path.join(cell.directory, closed_set_name(cell.label))
    if not os.path.isfile(p):
        return None
    doc = load(p)
    src = cell.prefix + closed_set_name(cell.label)
    skipped = doc.get("skipped_scenarios") or {}
    # Deviation D8 leaves out every trial of S12 and nothing else.
    if sorted(skipped) != [CLOSED_SET_SKIPPED] or not skipped[CLOSED_SET_SKIPPED] > 0:
        raise ValueError("%s: the trials left out are not those of %s alone (deviation D8): %r" % (
            src, CLOSED_SET_SKIPPED, skipped))
    for K in CLOSED_SET_KS:
        for f in CLOSED_SET_SETS:
            r = (doc.get("results") or {}).get(str(K), {}).get(f)
            if r is None:
                raise ValueError("%s: no result for K = %d and the feature set %s" % (src, K, f))
            if abs(r["chance"] - 1.0 / K) > 1e-12:
                raise ValueError("%s: the chance level of K = %d is not 1/K" % (src, K))
            if CLOSED_SET_SKIPPED in (r.get("by_scenario") or {}):
                raise ValueError("%s: %s still has trials at K = %d, feature set %s (deviation D8)" % (
                    src, CLOSED_SET_SKIPPED, K, f))
    al = doc.get("alignment") or {}
    if not all(is_count(al.get(k)) for k in ("n_blocks_unscored", "n_trials_unscored")):
        raise ValueError("%s: the unscored blocks and trials are not counted" % src)
    return doc


def near_chance_at(r, K):
    """Section 6.8: the accuracy (the maximum over the set and its coarsenings) is near chance at K when the upper end
    of its 95% Wilson interval is at most 2/K. None when the result has no interval (no scored trial)."""
    hi = r["acc_ci"][1] if r.get("n") else None
    if hi is None:
        return None
    return hi <= 2.0 / K


def near_chance(doc, f):
    """Near chance at both K = 10 and K = 100 (True / False), or None when either has no value."""
    judged = [near_chance_at(doc["results"][str(K)][f], K) for K in RULE_KS]
    if any(j is None for j in judged):
        return None
    return all(judged)


def recommend(docs):
    """The recommendation of section 6.8, from the two macOS cells only: the most detailed of RULE_SETS that is near
    chance in both cells. {"status": "recommended" | "none" | "not decided", "set": name or None}. Without both macOS
    cells the condition cannot be checked and nothing is recommended."""
    if any(docs.get(c) is None for c in MACOS_CELLS):
        return {"status": "not decided", "set": None}
    for f in RULE_SETS:
        if all(near_chance(docs[c], f) is True for c in MACOS_CELLS):
            return {"status": "recommended", "set": f}
    return {"status": "none", "set": None}


def yes_no(j):
    return NO_VALUE if j is None else ("yes" if j else "no")


def windows_sets_clause(judged):
    """The judgement of each set of the rule on Windows as an English clause: "neither A nor B was near chance", "both
    A and B were near chance", or the sets named one by one when they differ or have no value."""
    names = dict((f, "\\texttt{%s}" % tex_escape(f)) for f in RULE_SETS)
    vals = [judged[f] for f in RULE_SETS]
    if len(RULE_SETS) == 2 and all(v is False for v in vals):
        return "neither %s nor %s was near chance" % tuple(names[f] for f in RULE_SETS)
    if len(RULE_SETS) == 2 and all(v is True for v in vals):
        return "both %s and %s were near chance" % tuple(names[f] for f in RULE_SETS)
    part = {True: "was near chance", False: "was not near chance", None: "has no value"}
    return ", and ".join("%s %s" % (names[f], part[judged[f]]) for f in RULE_SETS)


def closed_set_values(cells, docs):
    """Values of question 4: the recommendation (item iii of the rule for the abstract), the Windows judgement as its
    qualification, and per cell the accuracy and the judgement of the two sets of the rule."""
    vals = {}
    rec = recommend(docs)
    macos_src = ", ".join(closed_set_name(c) for c in MACOS_CELLS)
    text = {"recommended": "\\texttt{%s}" % tex_escape(rec["set"] or ""), "none": "none",
            "not decided": "not decided (a macOS cell was not measured)"}[rec["status"]]
    vals["q4.recommendation"] = {"text": text, "value": rec, "source": macos_src + ":results/10|100/*/acc_ci"}
    # The Windows judgement as a clause that the text places after "On Windows," (it names no system itself).
    win = docs.get(WINDOWS_CELL)
    if win is None:
        q = "the cell was not measured"
        qv = None
    elif rec["status"] == "recommended":
        qv = near_chance(win, rec["set"])
        q = {True: "the recommended set was also near chance", False: "the recommended set was not near chance",
             None: "the recommended set has no value"}[qv]
    else:
        qv = dict((f, near_chance(win, f)) for f in RULE_SETS)
        q = windows_sets_clause(qv)
    vals["q4.windows.qualification"] = {"text": q, "value": qv,
                                        "source": "windows/" + closed_set_name(WINDOWS_CELL) + ":results/10|100/*/acc_ci"}
    for cell in cells:
        doc = docs.get(cell.label)
        if doc is None:
            continue
        src = cell.prefix + closed_set_name(cell.label)
        al = doc.get("alignment") or {}
        vals["%s.q4.unscored" % cell.label] = {
            "text": "%s blocks, %s trials" % (fmt_int(al["n_blocks_unscored"]), fmt_int(al["n_trials_unscored"])),
            "value": [al.get("n_blocks_unscored"), al.get("n_trials_unscored")], "source": src + ":alignment"}
        for f in RULE_SETS:
            for K in RULE_KS:
                r = doc["results"][str(K)][f]
                vals["%s.q4.%s.k%d.acc" % (cell.label, f, K)] = {
                    "text": est_ci(r["acc"], r["acc_ci"][0], r["acc_ci"][1]), "value": [r["acc"]] + list(r["acc_ci"]),
                    "source": "%s:results/%d/%s/acc|acc_ci" % (src, K, f)}
            vals["%s.q4.%s.near" % (cell.label, f)] = {
                "text": yes_no(near_chance(doc, f)), "value": near_chance(doc, f),
                "source": "%s:results/10|100/%s/acc_ci" % (src, f)}
    return vals


def open_set_name(label):
    return "e2_open_set_%s.json" % label


def read_open_set(cell):
    """The open-set result of a cell, or None when its file is absent; both splits and both length levels must be
    present with K0 and K1."""
    p = os.path.join(cell.directory, open_set_name(cell.label))
    if not os.path.isfile(p):
        return None
    doc = load(p)
    for split, model, _ in OPEN_SET_ATTACKERS:
        for li, _ in OPEN_SET_LENGTHS:
            r = (doc.get("splits") or {}).get(split, {}).get(li)
            if r is None or model not in r or model not in (r.get("shuffled") or {}):
                raise ValueError("%s: no result for the split %s, length %s and model %s" % (
                    cell.prefix + open_set_name(cell.label), split, li, model))
    return doc


def open_set_values(cell, doc):
    """Values of the open set for the text: per attacker and length level, the attack's CER, the gain over the
    baseline and the gain over the shuffled control, each with its interval."""
    vals = {}
    if doc is None:
        return vals
    src = cell.prefix + open_set_name(cell.label)
    for split, model, label in OPEN_SET_ATTACKERS:
        key = label.split(" ")[0].replace("+", "plus") + ("" if split == "work" or model == "K1" else ".phrase")
        for li, _ in OPEN_SET_LENGTHS:
            r = doc["splits"][split][li]
            sh = r["shuffled"][model]
            base = "%s.q4.open.%s.%s" % (cell.label, key, li)
            vals[base + ".cer"] = {"text": est_ci(r[model]["cer"], r[model]["cer_ci"][0], r[model]["cer_ci"][1]),
                                   "value": [r[model]["cer"]] + list(r[model]["cer_ci"]),
                                   "source": "%s:splits/%s/%s/%s/cer" % (src, split, li, model)}
            vals[base + ".gain"] = {"text": est_ci(r["gain"][model]["gain"], r["gain"][model]["gain_ci"][0],
                                                   r["gain"][model]["gain_ci"][1], fmt_signed),
                                    "value": [r["gain"][model]["gain"]] + list(r["gain"][model]["gain_ci"]),
                                    "source": "%s:splits/%s/%s/gain/%s" % (src, split, li, model)}
            vals[base + ".gainvsshuffled"] = {
                "text": est_ci(sh["gain_vs_shuffled"], sh["gain_vs_shuffled_ci"][0], sh["gain_vs_shuffled_ci"][1],
                               fmt_signed),
                "value": [sh["gain_vs_shuffled"]] + list(sh["gain_vs_shuffled_ci"]),
                "source": "%s:splits/%s/%s/shuffled/%s/gain_vs_shuffled" % (src, split, li, model)}
    return vals


# ---------------------------------------------------------------- question 5: runtime cost


def _e3_number(s):
    return None if s == "n/a" else float(s)


def read_e3(path):
    """{condition: summary} from the output of kit/e3/summarize_e3.py (one block per condition)."""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("== "):
            name = line[3:].split(" (", 1)[0]
            if name in out:
                raise ValueError("%s: the condition %s appears twice" % (path, name))
            cur = out[name] = {"status": None, "cpu": None, "cpu_all_zero": None, "rss_final_mb": None,
                               "rss_slope_mb_per_h": None, "hours": None, "usable": None}
            continue
        if cur is None:
            continue
        if line.startswith("rows: "):
            cur["usable"] = int(line.split()[1])
        elif line.startswith("cpu % (one core = 100): "):
            p = line.split()
            cur["cpu"] = dict((p[i], _e3_number(p[i + 1])) for i in range(6, len(p) - 1, 2))
        elif line.startswith("cpu_all_zero: "):
            cur["cpu_all_zero"] = line.split()[1] == "true"
        elif line.startswith("resident memory: final "):
            p = line.split()
            cur["rss_final_mb"], cur["rss_slope_mb_per_h"], cur["hours"] = (
                _e3_number(p[3]), _e3_number(p[6]), _e3_number(p[9]))
        elif line.startswith("status: "):
            cur["status"] = line[len("status: "):].split(" -- ", 1)[0]
    for name, s in out.items():
        if name not in E3_CONDITIONS:
            raise ValueError("%s: unknown condition %s" % (path, name))
        if s["status"] not in ("measured", "insufficient", "not measured"):
            raise ValueError("%s: the condition %s has no status" % (path, name))
        if s["status"] != "not measured" and (s["cpu"] is None or set(s["cpu"]) != {"mean", "median", "p95", "max"}):
            raise ValueError("%s: the CPU line of the condition %s cannot be read" % (path, name))
        if s["status"] == "measured" and s["cpu_all_zero"] is None:
            raise ValueError("%s: the condition %s has no cpu_all_zero line (section 10.9)" % (path, name))
    return out


def e3_quotable(system, condition, s):
    """Whether the figures of a condition may be quoted (section 6.10): measured, and on Windows not a working
    condition whose every CPU sample is zero (section 10.9 takes that as a counter that could not be read)."""
    if s is None or s["status"] != "measured":
        return False
    if system == "windows" and condition == "working" and s["cpu_all_zero"]:
        return False
    return True


def read_e3_all(results_dir):
    """{system: (source, {condition: summary})} for every system whose summary file exists."""
    out = {}
    for sub, system, name in E3_FILES:
        p = os.path.join(results_dir, sub, name) if sub else os.path.join(results_dir, name)
        if os.path.isfile(p):
            out[system] = ((sub + "/" if sub else "") + name, read_e3(p))
    return out


def fmt_cpu(x):
    return NO_VALUE if x is None else "%.2f" % x


def e3_values(e3):
    """Item (iv) of the rule for the abstract: the mean CPU of each condition per system, without an interval."""
    vals = {}
    for system in ("macos", "windows"):
        src, conds = e3.get(system, (None, {}))
        for c in E3_CONDITIONS:
            s = conds.get(c)
            ok = e3_quotable(system, c, s)
            vals["%s.e3.%s.cpu.mean" % (system, c)] = {
                "text": fmt_cpu(s["cpu"]["mean"]) if ok else NO_VALUE,
                "value": s["cpu"]["mean"] if ok else None,
                "source": ("%s:%s/cpu mean" % (src, c)) if src else "not measured"}
    return vals


# ---------------------------------------------------------------- question 3: per-text agreement


def fmt_signed(x, ms=False):
    """A difference: whole milliseconds for a duration, three decimals otherwise, with a typeset minus sign."""
    if x is None:
        return NO_VALUE
    if ms:
        return fmt_ms(x)
    s = "%.3f" % x
    if s == "-0.000":  # a value that rounds to zero carries no sign
        return "0.000"
    return "$-$" + s[1:] if s.startswith("-") else s


def l2_mixtures(cell, scope):
    """The mixtures of speeds of a scope, checked against the weights of section 6.7."""
    mixes = cell.l2[scope]["mixture_bias"]["mixtures"]
    for name, _, w in L2_MIXTURES:
        got = mixes.get(name, {}).get("weights") or {}
        if any(abs(got.get(s, -1.0) - x) > 1e-9 for s, x in zip(SPEEDS, w)):
            raise ValueError("%s: the weights of the mixture %s are not those of section 6.7" % (
                cell.path("e1_l2.json", scope, "mixture_bias/mixtures"), name))
    return mixes


def l2_values(cell):
    """Per-speed bias and ICC of the four per-text measures in the primary scope, for the text."""
    vals = {}
    by = cell.l2[PRIMARY_SCOPE]["agreement_by_stratum"]
    for m, _, ms in L2_MEASURES:
        for s in SPEEDS:
            e = (by.get(s) or {}).get(m)
            if e is None:
                continue
            ba = e["bland_altman"]
            vals["%s.l2.%s.%s.bias" % (cell.label, m, s)] = {
                "text": est_ci(ba["bias"], e["bias_ci"][0], e["bias_ci"][1], lambda x: fmt_signed(x, ms)),
                "value": [ba["bias"]] + list(e["bias_ci"]),
                "source": cell.path("e1_l2.json", PRIMARY_SCOPE, "agreement_by_stratum", s, m, "bias_ci")}
            spread = checked_spread(cell, PRIMARY_SCOPE, m, s, e)
            constant = spread["constant"]
            vals["%s.l2.%s.%s.icc" % (cell.label, m, s)] = {
                "text": REFERENCE_CONSTANT_TEXT if constant else est_ci(
                    e["icc21"], e["icc21_ci"][0], e["icc21_ci"][1], lambda x: fmt_signed(x)),
                "value": [None, None, None] if constant else [e["icc21"]] + list(e["icc21_ci"]),
                "source": cell.path("e1_l2.json", PRIMARY_SCOPE, "agreement_by_stratum", s, m, "icc21_ci")}
            if constant:
                # Deviation D11 reports the registered result next to the corrected one (section 8 of the registration).
                vals["%s.l2.%s.%s.icc" % (cell.label, m, s)]["registered"] = [e["icc21"]] + list(e["icc21_ci"])
            vals["%s.l2.%s.%s.refsd" % (cell.label, m, s)] = {
                "text": fmt_sd(spread["sd"], ms), "value": spread["sd"],
                "source": cell.path("e1_l2.json", PRIMARY_SCOPE, "aggregate", "gt")}
    return vals


def l2_constant_values(cells):
    """Per per-text measure, over the strata of speed and cell of the primary scope that have an agreement entry: in
    how many the reference's value is the same in every text (deviation D11), out of how many, and in how many it
    differs from the most frequent value in a single text, so that the text states these counts from the result files.
    The texts are those of the ICC, with a value on both sides (reference_spread()); a stratum of one text is neither."""
    vals = {}
    for m, _, _ in L2_MEASURES:
        n_of = n_const = n_one = 0
        srcs = []
        for cell in cells:
            by = cell.l2[PRIMARY_SCOPE]["agreement_by_stratum"]
            for s in SPEEDS:
                e = (by.get(s) or {}).get(m)
                if e is None:
                    continue
                spread = checked_spread(cell, PRIMARY_SCOPE, m, s, e)
                n_of += 1
                n_const += spread["constant"]
                n_one += (not spread["constant"]) and spread["off_mode"] == 1
            srcs.append(cell.path("e1_l2.json", PRIMARY_SCOPE, "aggregate", "gt"))
        src = ", ".join(srcs)
        for key, n in (("constant.strata", n_const), ("constant.of", n_of), ("onetext.strata", n_one)):
            vals["q3.%s.%s" % (m, key)] = {"text": number_word(n), "value": n, "source": src}
    return vals


NUMBER_WORDS = ("none", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def number_word(n):
    """A count as a word up to twelve, as the text writes small counts; larger counts as digits."""
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else fmt_int(n)


# ---------------------------------------------------------------- exploratory: the distribution of the dwell error

DWELL_EXPLORE_DIR = "exploratory"
DWELL_EXPLORE_PCTS = (("p2.5", "2.5th percentile"), ("p97.5", "97.5th percentile"))
DWELL_EXPLORE_WITHIN = (("abs_le_100_ms", "100"), ("abs_le_1000_ms", "1000"))


def read_dwell_explore(results_dir, cell):
    """The exploratory percentiles of the dwell error of a cell (kit/analysis/explore_dwell_quantiles.py), or None when
    the file has not been made. The file must hold this cell, both scopes and the speeds of the registered results."""
    name = "dwell_quantiles_%s.json" % cell.label
    p = os.path.join(results_dir, DWELL_EXPLORE_DIR, name)
    if not os.path.isfile(p):
        return None
    doc = load(p)
    src = "%s/%s" % (DWELL_EXPLORE_DIR, name)
    if doc.get("exploratory") is not True:
        raise ValueError("%s: not marked as exploratory" % src)
    # The file was made against the registered result file that this script reads, and from the matching outputs that
    # the registered result file records.
    l1_path = os.path.join(cell.directory, "e1_l1.json")
    if (doc.get("inputs_sha256") or {}).get("e1_l1_registered") != sha256_of(l1_path):
        raise ValueError("%s: made against another e1_l1.json than %s" % (src, cell.src["e1_l1.json"]))
    block = (doc.get("cells") or {}).get(cell.label)
    if not block or sorted(block) != sorted(SCOPES):
        raise ValueError("%s: cell %s with both scopes is missing" % (src, cell.label))
    for scope in SCOPES:
        if block[scope].get("match_sha256") != cell.l1[scope]["inputs_sha256"]["match"]:
            raise ValueError("%s: %s was not taken from the registered matching output" % (src, scope))
        speeds = cell.speed_sessions(scope)
        for sp in SPEEDS:
            e = block[scope].get(sp)
            reg = (speeds.get(sp) or {}).get("dwell")
            if (e is None) != (reg is None):
                raise ValueError("%s: %s, %s does not match the registered results" % (src, scope, sp))
            if e is None:
                continue
            if e["n"] != reg["n"]:
                raise ValueError("%s: %s, %s has %d pairs, the registered results %d" % (src, scope, sp, e["n"],
                                                                                          reg["n"]))
            check_dwell_entry(e, "%s: %s, %s" % (src, scope, sp))
    return {"src": src, "cells": block}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_dwell_entry(e, where):
    """The figures of one speed: none for a speed without pairs; otherwise finite percentiles in order and fractions with
    0 <= within 100 ms <= within 1 s <= 1."""
    pc = [e["percentiles"].get(k) for k, _ in DWELL_EXPLORE_PCTS]
    wi = [e["within"].get(k) for k, _ in DWELL_EXPLORE_WITHIN]
    if e["n"] == 0:
        if any(x is not None for x in pc + wi):
            raise ValueError("%s: figures for a speed without pairs" % where)
        return
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in pc + wi):
        raise ValueError("%s: a figure is missing or not a finite number" % where)
    if not pc[0] <= pc[1]:
        raise ValueError("%s: the percentiles are out of order" % where)
    if not 0.0 <= wi[0] <= wi[1] <= 1.0:
        raise ValueError("%s: the fractions are not 0 <= within 100 ms <= within 1 s <= 1" % where)


def dwell_explore_values(cell, doc):
    """Values for the text: per speed, the percentiles and the fractions within 100 ms and 1 s, primary scope."""
    vals = {}
    if doc is None:
        return vals
    for sp in SPEEDS:
        e = doc["cells"][PRIMARY_SCOPE].get(sp)
        if e is None or e["n"] == 0:
            continue
        for key, _ in DWELL_EXPLORE_PCTS:
            vals["%s.dwellx.%s.%s" % (cell.label, sp, key.replace(".", ""))] = {
                "text": fmt_signed(e["percentiles"][key], True), "value": e["percentiles"][key],
                "source": "%s:cells/%s/%s/%s/percentiles/%s" % (doc["src"], cell.label, PRIMARY_SCOPE, sp, key)}
        for key, ms in DWELL_EXPLORE_WITHIN:
            vals["%s.dwellx.%s.within%s" % (cell.label, sp, ms)] = {
                "text": fmt_pct(e["within"][key]), "value": e["within"][key],
                "source": "%s:cells/%s/%s/%s/within/%s" % (doc["src"], cell.label, PRIMARY_SCOPE, sp, key)}
    # The same figures as ranges over the speeds, for the text.
    present = [sp for sp in SPEEDS if (doc["cells"][PRIMARY_SCOPE].get(sp) or {}).get("n")]
    if present:
        src = "%s:cells/%s/%s/*" % (doc["src"], cell.label, PRIMARY_SCOPE)
        for key, _ in DWELL_EXPLORE_PCTS:
            xs = [doc["cells"][PRIMARY_SCOPE][sp]["percentiles"][key] for sp in present]
            lo, hi = fmt_signed(min(xs), True), fmt_signed(max(xs), True)
            vals["%s.dwellx.%s.range" % (cell.label, key.replace(".", ""))] = {
                "text": "%s~ms" % lo if lo == hi else "%s to %s~ms" % (lo, hi), "value": [min(xs), max(xs)],
                "source": src}
        for key, ms in DWELL_EXPLORE_WITHIN:
            xs = [doc["cells"][PRIMARY_SCOPE][sp]["within"][key] for sp in present]
            lo, hi = fmt_pct(min(xs)), fmt_pct(max(xs))
            vals["%s.dwellx.within%s.range" % (cell.label, ms)] = {
                "text": lo if lo == hi else "%s--%s" % (lo, hi), "value": [min(xs), max(xs)], "source": src}
    return vals


def dwell_explore_supplement(cells, docs):
    """Supplement: the exploratory distribution of the dwell error per cell, scope and speed."""
    lines = [GENERATED_NOTE, "\\section*{Exploratory: the distribution of the dwell error}\n\\label{sec:supp-dwell}\n\n",
             "Not registered. For the one-to-one pairs of the primary comparison, the 2.5th and 97.5th percentiles of "
             "the difference of \\texttt{dwell\\_ms} from the duration of the reference session (observation minus "
             "reference, ms; linear interpolation between order statistics) and the fractions of pairs whose absolute "
             "difference is at most 100~ms and at most 1~s, without intervals. The pairs are those of the registered "
             "analysis: the script runs the frozen analysis and reproduces its number of pairs, bias and median "
             "absolute difference at every speed before it writes these figures.\n\n"]
    for cell in cells:
        doc = docs.get(cell.label)
        name = cell_name(cell.label)
        if doc is None:
            lines.append("Cell %s: not computed when this file was generated.\n\n" % name)
            continue
        lines += ["{\\footnotesize\n", "\\begin{longtable}{L{0.16}L{0.10}L{0.08}L{0.15}L{0.15}L{0.15}L{0.15}}\n",
                  "\\caption{Exploratory distribution of the dwell error, cell %s}\\label{tab:dwell-explore-%s}\\\\\n"
                  % (name, cell.label),
                  "\\toprule\n",
                  "Scope & Speed & Pairs & 2.5th percentile (ms) & 97.5th percentile (ms) & Within 100~ms & "
                  "Within 1~s \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for scope in SCOPES:
            for i, sp in enumerate(SPEEDS):
                e = doc["cells"][scope].get(sp)
                head = ["\\texttt{%s}" % tex_escape(scope) if i == 0 else "", sp]
                if e is None or e["n"] == 0:
                    lines.append(" & ".join(head) + " & \\multicolumn{5}{l}{no pair} \\\\\n")
                    continue
                lines.append(" & ".join(head + [fmt_int(e["n"])]
                                        + [fmt_signed(e["percentiles"][k], True) for k, _ in DWELL_EXPLORE_PCTS]
                                        + [fmt_pct(e["within"][k]) for k, _ in DWELL_EXPLORE_WITHIN]) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n\n"]
    return "".join(lines)


# ---------------------------------------------------------------- deviation D12: the definition common to both systems

# The registration reports, under the common definition, only whether it holds the same sessions as the definition of
# the cell's system (sections 5 and 10.7). Deviation D12 also reports the pooled detection of the primary comparison
# under both definitions for a cell in which they do not.
DEFINITION_MEASURES = (("recall", "Recall"), ("precision", "Precision"), ("split_rate", "Split rate"),
                       ("merge_rate", "Merge rate"))


def definition_pooled(cell, scope):
    """The pooled detection of the primary comparison in a scope under the definition of the analysis and under the
    common one. The definition of the analysis keeps some of the sessions of the common one, so it cannot hold more;
    and where the analysis reports that the two hold the same sessions, they must hold as many in every scope."""
    own, common = cell.det[scope]["metrics"]["pooled"], cell.det_common[scope]["metrics"]["pooled"]
    where = "%s:cells/%s/{%s,%s}/%s/%s/metrics/pooled/n_gt" % (cell.src["e1_detection.json"], cell.label,
                                                               cell.definition, COMMON_DEFINITION, PRIMARY_COMPARISON,
                                                               scope)
    # Without the report of whether the two definitions hold the same sessions, the supplement could not say which
    # cells it leaves out (the registration asks for the report per cell).
    if not isinstance(cell.det_top.get("same_sessions_as_all"), bool):
        raise ValueError("%s:same_sessions_as_all/%s: no report of whether the two definitions hold the same sessions"
                         % (cell.src["e1_detection.json"], cell.label))
    if own["n_gt"] > common["n_gt"]:
        raise ValueError("%s: the definition of the analysis holds more sessions than the common one" % where)
    if cell.det_top.get("same_sessions_as_all") and own["n_gt"] != common["n_gt"]:
        raise ValueError("%s: the two definitions are reported to hold the same sessions but differ in number" % where)
    return own, common


def definition_values(cell):
    """Deviation D12, for the text: per scope, the sessions of the common definition, how many of them the definition
    of the analysis leaves out, and the pooled measures under the common definition, for a cell in which the two
    definitions do not hold the same sessions."""
    vals = {}
    for scope in SCOPES:
        own, common = definition_pooled(cell, scope)
        if cell.det_top.get("same_sessions_as_all") is not False:
            continue
        base = "%s.defs.%s" % (cell.label, "primary" if scope == PRIMARY_SCOPE else "all")
        src = "%s:cells/%s/%s/%s/%s/metrics/pooled" % (cell.src["e1_detection.json"], cell.label, COMMON_DEFINITION,
                                                       PRIMARY_COMPARISON, scope)
        vals[base + ".common.sessions"] = {"text": fmt_int(common["n_gt"]), "value": common["n_gt"],
                                           "source": src + "/n_gt"}
        vals[base + ".common.rows"] = {"text": fmt_int(common["n_obs"]), "value": common["n_obs"],
                                       "source": src + "/n_obs"}
        for m, _ in DEFINITION_MEASURES:
            vals["%s.common.%s" % (base, m.replace("_", ""))] = {
                "text": ci_text(common[m]), "value": [common[m]["value"], common[m]["lo"], common[m]["hi"]],
                "source": src + "/" + m}
        vals[base + ".leftout"] = {"text": fmt_int(common["n_gt"] - own["n_gt"]), "value": common["n_gt"] - own["n_gt"],
                                   "source": "%s/n_gt minus %s" % (src, cell.path("e1_detection.json", scope,
                                                                                    "metrics/pooled/n_gt"))}
        for m in ("recall", "precision"):
            text, value, left = definition_range(cell, scope, cell.det_common[scope], m)
            ssrc = "%s:cells/%s/%s/%s/%s/metrics/by_stratum/*/%s" % (
                cell.src["e1_detection.json"], cell.label, COMMON_DEFINITION, PRIMARY_COMPARISON, scope, m)
            vals["%s.common.%s.range" % (base, m)] = {"text": text, "value": value, "source": ssrc}
            vals["%s.common.%s.strata.left" % (base, m)] = {"text": str(left), "value": left, "source": ssrc}
            changed, compared, largest, onlyone = definition_changes(cell, scope, m)
            vals["%s.change.%s.strata" % (base, m)] = {"text": str(changed), "value": changed, "source": ssrc}
            vals["%s.change.%s.of" % (base, m)] = {"text": str(compared), "value": compared, "source": ssrc}
            vals["%s.change.%s.max" % (base, m)] = {"text": fmt_p(largest), "value": largest, "source": ssrc}
            vals["%s.change.%s.onlyone" % (base, m)] = {"text": str(onlyone), "value": onlyone, "source": ssrc}
        # For the text: the strata that enter a range under one definition only, which the counts above leave out.
        only = [vals["%s.change.%s.onlyone" % (base, m)]["value"] for m in ("recall", "precision")]
        vals[base + ".change.onlyone.note"] = {
            "text": "no stratum entered a range under one definition only" if not any(only) else
            "%d strata entered the range of recall and %d that of precision under one definition only" % tuple(only),
            "value": only, "source": "%s:cells/%s/{%s,%s}/%s/%s/metrics/by_stratum" % (
                cell.src["e1_detection.json"], cell.label, cell.definition, COMMON_DEFINITION, PRIMARY_COMPARISON,
                scope)}
    return vals


def definition_changes(cell, scope, m):
    """Within the ranges: over the strata that enter the range of measure m under both definitions, how many have a
    different estimate under the common definition, how many were compared, the largest absolute difference (None
    when no stratum is compared), and how many strata enter the range under one definition only (a stratum that gains
    or loses a value with the definition, which the comparison cannot count as a change)."""
    kept = cell.kept_trials(scope)[0]
    own, common = strata_of(cell.det[scope]), strata_of(cell.det_common[scope])
    own_used = set(strata_in_range(kept, own, list(kept), m))
    common_used = set(strata_in_range(kept, common, list(kept), m))
    keys = sorted(own_used & common_used)
    diffs = [abs(own[k][m]["value"] - common[k][m]["value"]) for k in keys
             if own[k][m]["value"] is not None and common[k][m]["value"] is not None]
    changed = sum(1 for x in diffs if x > 0)
    return changed, len(diffs), (max(diffs) if diffs else None), len(own_used ^ common_used)


def definition_range(cell, scope, block, m):
    """The range of the Wilson intervals of measure m over the strata that enter it under the rule of the abstract
    (abstract_values), for a detection block of the primary comparison in a scope: (text, [lo, hi], strata left out).
    The trials kept per stratum are those of the cell's analysis; the two definitions differ in the reference's
    sessions, not in the trials."""
    kept = cell.kept_trials(scope)[0]
    strata = strata_of(block)
    design = sorted(kept, key=lambda k: (scenario_order(k[0]), SPEEDS.index(k[1])))
    used = strata_in_range(kept, strata, design, m)
    lo, hi = interval_range([strata[k][m] for k in used]) if used else (None, None)
    return ("%s--%s" % (fmt_p(lo), fmt_p(hi)) if used else NO_VALUE), [lo, hi], len(design) - len(used)


def definitions_supplement(cells):
    """Supplement: the pooled detection of the primary comparison under both definitions, for the cells in which they
    do not hold the same sessions (deviation D12)."""
    lines = [GENERATED_NOTE, "\\section*{Detection under the definition common to both systems}\n"
             "\\label{sec:supp-definitions}\n\n",
             "Reported under deviation D12. The registration reports under the definition of a session common to both "
             "systems (\\texttt{all}, every session) only whether it holds the same sessions as the definition of the "
             "cell's system. For a cell in which it did not, the detection of the primary comparison pooled over the "
             "strata is given under both definitions, with Wilson intervals, which for pooled proportions are lower "
             "bounds of the uncertainty, together with the range of the per-stratum Wilson intervals of recall and "
             "precision over the strata that keep at least %d trials in the scope and have a value, as in the main text. "
             "A pooled value is not an estimate for natural typing.\n\n" % MIN_TRIALS]
    shown = [c for c in cells if c.det_top.get("same_sessions_as_all") is False]
    same = [c.label for c in cells if c.det_top.get("same_sessions_as_all")]
    if same:
        lines.append("The two definitions held the same sessions in %s.\n\n" % " and in ".join(
            "the cell %s" % cell_name(c) for c in same))
    if not shown:
        lines.append("No cell had sessions that only one of the two definitions held.\n\n")
    for cell in shown:
        name = cell_name(cell.label)
        # One column per scope and definition, the definition of the analysis first; an estimate above its interval.
        cols = []
        for scope in SCOPES:
            own, common = definition_pooled(cell, scope)
            cols += [own, common]
        defs = [tex_escape(cell.definition), COMMON_DEFINITION] * len(SCOPES)
        lines += ["{\\footnotesize\n",
                  "\\begin{longtable}{L{0.20}%s}\n" % ("L{%.2f}" % (0.80 / len(cols)) * len(cols)),
                  "\\caption{Detection of the primary comparison under the definition of the analysis (\\texttt{%s}) "
                  "and the common definition (\\texttt{%s}), pooled over the strata and as ranges over the strata, cell "
                  "%s}"
                  "\\label{tab:definitions-%s}\\\\\n" % (tex_escape(cell.definition), COMMON_DEFINITION, name,
                                                         cell.label),
                  "\\toprule\n",
                  " & %s \\\\\n" % " & ".join("\\multicolumn{2}{l}{\\texttt{%s}}" % tex_escape(s) for s in SCOPES),
                  "".join("\\cmidrule(lr){%d-%d}" % (2 + 2 * i, 3 + 2 * i) for i in range(len(SCOPES))) + "\n",
                  "Measure & %s \\\\\n" % " & ".join("\\texttt{%s}" % d for d in defs),
                  "\\midrule\n\\endhead\n",
                  "Reference sessions & %s \\\\\n" % " & ".join(fmt_int(p["n_gt"]) for p in cols),
                  "Session rows & %s \\\\\n" % " & ".join(fmt_int(p["n_obs"]) for p in cols)]
        for m, label in DEFINITION_MEASURES:
            lines.append("%s & %s \\\\\n" % (label, " & ".join(ci_text(p[m]).replace(" [", "\\newline [", 1)
                                                               for p in cols)))
        # The ranges over the strata, under the rule of the main text, in the order of the columns.
        for m, label in (("recall", "Recall, range over the strata"), ("precision", "Precision, range over the strata")):
            texts = []
            for scope in SCOPES:
                for block in (cell.det[scope], cell.det_common[scope]):
                    text, _, left = definition_range(cell, scope, block, m)
                    texts.append("%s\\newline (%d left out)" % (text, left))
            lines.append("%s & %s \\\\\n" % (label, " & ".join(texts)))
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n\n"]
    return "".join(lines)


# ---------------------------------------------------------------- the comparisons side by side, for the discussion


def ms_span(lo, hi):
    """A span of milliseconds for the text; one value when both ends are the same."""
    return "%s~ms" % fmt_ms(lo) if fmt_ms(lo) == fmt_ms(hi) else "%s to %s~ms" % (fmt_ms(lo), fmt_ms(hi))


def decomposition_values(cell):
    """Values that the discussion reads from the registered results, in the primary scope. For every comparison of the
    cell: the span over the speeds of the limits of agreement of dwell_ms (lowest lower limit to highest upper limit)
    and of its median absolute difference (point estimates), with the number of speeds that enter them. For the three
    comparisons with the prediction (macOS): the pooled recall and precision of each layer of the prediction in the
    primary scope, and the number of trials of the plan in the layer, which the analysis counts over the whole plan
    whatever the scope. The comparison of the specified rows with the logger shows, per layer, how far the logger's rows
    were the specified ones there."""
    vals = {}
    for comp in cell.comparisons:
        tag = comp.replace("_", "")
        src = "%s:cells/%s/%s/%s/%s/metrics/by_speed/*/dwell" % (cell.src["e1_l1.json"], cell.label, cell.definition,
                                                                 comp, PRIMARY_SCOPE)
        sp = [e["dwell"] for e in cell.l1_by[comp][PRIMARY_SCOPE]["metrics"]["by_speed"]
              if (e.get("stratum") or {}).get("speed") in SPEEDS and e["dwell"]["n"]]
        # One pair has a bias but no limits of agreement (kit/analysis/stats.py), so the limits and the median absolute
        # difference are taken from the speeds that have them; speeds.used counts the speeds with limits.
        with_loa = [d["bland_altman"] for d in sp if (d.get("bland_altman") or {}).get("loa_lo") is not None
                    and d["bland_altman"].get("loa_hi") is not None]
        with_mae = [d["mae"] for d in sp if d.get("mae") is not None]
        vals["%s.%s.primary.dwell.speeds.used" % (cell.label, tag)] = {"text": str(len(with_loa)),
                                                                      "value": len(with_loa), "source": src}
        lo, hi = (min(b["loa_lo"] for b in with_loa), max(b["loa_hi"] for b in with_loa)) if with_loa else (None, None)
        mlo, mhi = (min(with_mae), max(with_mae)) if with_mae else (None, None)
        loa = {"text": ms_span(lo, hi) if with_loa else NO_VALUE, "value": [lo, hi]}
        mae = {"text": ms_span(mlo, mhi) if with_mae else NO_VALUE, "value": [mlo, mhi]}
        vals["%s.%s.primary.dwell.loa.span" % (cell.label, tag)] = dict(loa, source=src + "/bland_altman/loa_lo|loa_hi")
        vals["%s.%s.primary.dwell.mae.span" % (cell.label, tag)] = dict(mae, source=src + "/mae")
        if comp not in ("gt_obs", "gt_pred", "pred_obs"):
            continue
        layers = cell.det_by[comp][PRIMARY_SCOPE]["diagnostics"].get("prediction_layers")
        if not layers:
            continue
        for name, _ in LAYERS:
            lsrc = "%s:cells/%s/%s/%s/%s/diagnostics/prediction_layers/%s" % (
                cell.src["e1_detection.json"], cell.label, cell.definition, comp, PRIMARY_SCOPE, name)
            key = "%s.layer.%s.%s.primary" % (cell.label, name.replace("_", ""), tag)
            p = layers[name]["pooled"]
            for m in ("recall", "precision"):
                vals["%s.%s" % (key, m)] = {"text": ci_text(p[m]), "value": [p[m]["value"], p[m]["lo"], p[m]["hi"]],
                                            "source": "%s/pooled/%s" % (lsrc, m)}
            vals["%s.layer.%s.%s.plan.trials" % (cell.label, name.replace("_", ""), tag)] = {
                "text": fmt_int(layers[name]["n_trials"]), "value": layers[name]["n_trials"],
                "source": lsrc + "/n_trials"}
    return vals


# ---------------------------------------------------------------- deviation D10: the corrected merge rate


# Deviation D10 changes, by its definition, only the merge rate and its recounted numerator, and adds the switch to
# the configuration; everything else of a corrected detection block must equal the registered one.
D10_CHANGES = ("merge_rate", "n_merge_obs_recount", "merge_recount")


def _without(x, keys):
    """A copy of a JSON value without the members named in keys, at any depth."""
    if isinstance(x, dict):
        return dict((k, _without(v, keys)) for k, v in x.items() if k not in keys)
    if isinstance(x, list):
        return [_without(v, keys) for v in x]
    return x


def _no_value_outside_merge(x, key=None):
    """The members that hold a rate without a value because of the defence of D10, other than merge rates."""
    found = []
    if isinstance(x, dict):
        if key != "merge_rate" and d10_no_value(x):
            found.append(key)
        for k, v in x.items():
            found += _no_value_outside_merge(v, k)
    elif isinstance(x, list):
        for v in x:
            found += _no_value_outside_merge(v, key)
    return found


def read_d10(results_dir, cell):
    """The detection results of the corrected analysis (deviation D10) for the cell, {comparison: {scope: block}},
    or None when it was not run. No registered block may carry the switch, and the defence of D10 (a rate whose
    numerator exceeds its denominator has no value) may have acted on merge rates only, the one rate that D10 concerns;
    every corrected block must carry the switch and equal the registered one apart from D10_CHANGES, so that it comes
    from the same inputs and settings."""
    p = os.path.join(results_dir, D10_DIRS[cell.system], "e1_detection.json")
    for comp in cell.comparisons:
        for scope in cell.det_by[comp]:
            if "merge_recount" in cell.det_by[comp][scope]["config"]:
                raise ValueError("%s: the registered results carry the switch of deviation D10" %
                                 cell.src["e1_detection.json"])
            other = _no_value_outside_merge(cell.det_by[comp][scope])
            if other:
                raise ValueError("%s: a rate other than the merge rate has a numerator above its denominator (%s, %s "
                                 "under %s)" % (cell.src["e1_detection.json"], ", ".join(sorted(set(other))), comp,
                                               scope))
    if not os.path.isfile(p):
        return None
    blocks = load(p)["cells"][cell.label][cell.definition]
    out = {}
    for comp in cell.comparisons:
        if not blocks.get(comp) or sorted(blocks[comp]) != sorted(cell.det_by[comp]):
            raise ValueError("%s: the corrected results do not hold the comparison %s in the scopes of the registered "
                             "ones" % (p, comp))
        for scope in blocks[comp]:
            if blocks[comp][scope]["config"].get("merge_recount") != "obs":
                raise ValueError("%s: the corrected results do not record merge_recount obs" % p)
            if _without(blocks[comp][scope], D10_CHANGES) != _without(cell.det_by[comp][scope], D10_CHANGES):
                raise ValueError("%s: the corrected results of %s under %s differ from the registered ones in more than "
                                 "the merge rate (other inputs or settings)" % (p, comp, scope))
        out[comp] = blocks[comp]
    return out


def d10_no_value(e):
    """A rate without a value because its numerator exceeds its denominator (the defence of deviation D10)."""
    return e.get("value") is None and e.get("n") is not None and e.get("x") is not None and e["x"] > e["n"]


def layer_entries(block):
    """Every metrics entry of the layers of the prediction in a detection block (pooled and per speed), or none."""
    layers = (block.get("diagnostics") or {}).get("prediction_layers")
    if not layers:
        return []
    return [e for name, _ in LAYERS for e in [layers[name]["pooled"]] + list(layers[name]["by_speed"])]


def d10_strata(block, scope):
    out = {}
    for st in block[scope]["metrics"]["by_stratum"]:
        if st["stratum"] in (None, "all") or st["stratum"].get("scenario") is None:
            continue
        out[stratum_key(st["stratum"])] = st
    return out


def d10_values(cell, d10):
    """The number of strata whose merge rate the corrected analysis changes, per scope (primary comparison), and the
    number of merge rates of the layers of the prediction that have no value in the registered analysis because the
    numerator exceeds the denominator."""
    vals = {}
    if d10 is None:
        return vals
    block = d10[PRIMARY_COMPARISON]
    if "gt_pred" in cell.comparisons:
        reg = [e for comp in cell.comparisons for scope in cell.det_by[comp]
               for e in layer_entries(cell.det_by[comp][scope])]
        n = sum(1 for e in reg if d10_no_value(e["merge_rate"]))
        vals["%s.d10.layers.novalue" % cell.label] = {
            "text": str(n), "value": n,
            "source": "%s:*/*/diagnostics/prediction_layers/*/merge_rate" % cell.src["e1_detection.json"]}
    for scope in SCOPES:
        reg, cor = cell.detection_strata(scope), d10_strata(block, scope)
        if sorted(reg) != sorted(cor):
            raise ValueError("%s: the strata of the corrected results differ from the registered ones" % cell.label)
        n = sum(1 for k in reg if reg[k]["merge_rate"] != cor[k]["merge_rate"])
        tag = "primary" if scope == PRIMARY_SCOPE else "all"
        vals["%s.d10.%s.merge.differ" % (cell.label, tag)] = {
            "text": "%d of %d" % (n, len(reg)), "value": [n, len(reg)],
            "source": "%s and %s/e1_detection.json:%s/metrics/by_stratum/*/merge_rate" % (
                cell.src["e1_detection.json"], D10_DIRS[cell.system].replace(os.sep, "/"), scope)}
    return vals


# ---------------------------------------------------------------- what the reference establishes (RF1) and KT1


def fmt_num1(x):
    """Milliseconds: an integer of the result file as it is, any other number with one decimal; a typeset minus."""
    if x is None:
        return NO_VALUE
    s = "{:,}".format(x) if isinstance(x, int) and not isinstance(x, bool) else "{:,.1f}".format(x)
    return "$-$" + s[1:] if s.startswith("-") else s


def fmt_ms2(x):
    """Milliseconds with two decimals, for the lateness of the executor's presses (a fraction of a millisecond)."""
    if x is None:
        return NO_VALUE
    s = "{:,.2f}".format(x)
    return "$-$" + s[1:] if s.startswith("-") else s


def count_ci(n, rate, ci):
    """A count with its rate and the Wilson interval of the rate."""
    return "%s (%s)" % (fmt_int(n), est_ci(rate, ci[0], ci[1]))


def clock_sync_name(label):
    return "clock_sync_%s.json" % label


def read_clock_sync(cell):
    """The differences at the block-start markers of the cell (section 6.9), or None when they were not computed. The
    file must hold the four differences."""
    p = os.path.join(cell.directory, clock_sync_name(cell.label))
    if not os.path.isfile(p):
        return None
    doc = load(p)
    for k, _ in CLOCK_DIFFS:
        if k not in (doc.get("differences") or {}):
            raise ValueError("%s: no difference %s" % (cell.prefix + clock_sync_name(cell.label), k))
    return doc


def reach_versions(r, f):
    """One figure of the reach of a side in the two versions, all presses / the input-mode keys left out."""
    return " / ".join(f(r[v]) for v, _ in REACH_VERSIONS)


def reference_rows(cell, cs):
    """[(group, figure, text)] of Table 7 for one cell; the groups and figures are the same for every cell."""
    o = cell.plan_agreement()
    d = cell.dlv
    rows = [("plan", "Trials compared", fmt_int(o["n_trials"])),
            ("plan", "Trials that disagree with the plan's intent",
             count_ci(o["n_disagree"], o["disagree_rate"], o["disagree_ci"]))]
    for t, label in PLAN_TYPES:
        e = o["by_type"].get(t)
        if e is None:
            raise ValueError("%s: no count of the type %s" % (cell.path("e1_l1.json", PRIMARY_SCOPE, "oracle"), t))
        rows.append(("plan", "\\quad " + label, count_ci(e["n"], e["rate"], e["ci"])))
    ua = o.get("unassigned_sessions")
    rows.append(("plan", "Sessions assigned to no trial", NO_VALUE if ua is None else fmt_int(ua)))
    tr, la = d["trials"], d["send_lateness_ms"]["overall"]
    rows += [("delivery", "Trials measured of those planned (incomplete)", "%s of %s (%s)" % (
                 fmt_int(tr["measured"]), fmt_int(tr["planned"]), fmt_int(tr["incomplete"]))),
             ("delivery", "Presses sent", "%s / %s" % (fmt_int(d["presses"]["all"]),
                                                     fmt_int(d["presses"]["excluding_mode_keys"]))),
             ("delivery", "Lateness of the presses against the schedule (ms): median, 95th percentile, maximum",
              "%s, %s, %s" % (fmt_ms2(la["median"]), fmt_ms2(la["p95"]), fmt_ms2(la["max"])))]
    for side, name in DELIVERY_SIDES:
        r = d[side]["reach"]
        rows += [("delivery", "%s: presses that arrived (rate)" % name,
                  reach_versions(r, lambda e: "%s (%s)" % (fmt_int(e["reached"]), fmt_p(e["reach_rate"])))),
                 ("delivery", "%s: trials with fewer rows than presses" % name,
                  reach_versions(r, lambda e: fmt_int(e["trials_with_loss"]))),
                 ("delivery", "%s: rows beyond the presses sent in a trial (trials)" % name,
                  reach_versions(r, lambda e: "%s (%s)" % (fmt_int(e["excess_rows"]), fmt_int(e["trials_with_excess"])))),
                 ("delivery", "%s: rows outside every trial" % name,
                  reach_versions(r, lambda e: fmt_int(e["rows_outside_trials"])))]
    f = d["reference_focus_loss"]
    rows += [("focus", "Losses: planned switches of S15, others", "%s, %s" % (
                 fmt_int(f["n_loss_intervals_planned"]), fmt_int(f["n_loss_intervals_unplanned"]))),
             ("focus", "Other losses with key rows in the observation",
              fmt_int(f["n_loss_intervals_unplanned_with_presses"])),
             ("focus", "Trials that overlap a loss", fmt_int(f["n_trials_with_focus_loss"]))]
    for scope in SCOPES:
        fl = cell.det[scope]["focus_loss"]
        if fl["n_trials"] != f["n_trials_with_focus_loss"]:
            raise ValueError("%s: the trials with a loss of focus differ from the delivery report" % cell.path(
                "e1_detection.json", scope, "focus_loss"))
        rows.append(("focus", "Session rows of these trials (false positives), %s" % (
            "primary scope" if scope == PRIMARY_SCOPE else "all trials"),
            "%s (%s)" % (fmt_int(fl["n_rows"]), fmt_int(fl["n_false_pos"]))))
    if cs is None:
        rows.append(("markers", "Blocks with the marker found: executor, reference, observation", "not computed"))
    else:
        rows.append(("markers", "Blocks with the marker found: executor, reference, observation", "%s of %s, %s, %s" % (
            fmt_int(len(cs["oracle"]["with_marker"])), fmt_int(cs["oracle"]["blocks"]),
            fmt_int(len(cs["gt"]["matched"])), fmt_int(len(cs["obs"]["matched"])))))
    for k, label in CLOCK_DIFFS:
        e = None if cs is None else cs["differences"][k]
        rows.append(("markers", "%s (ms): median (range); drift per hour" % label, "not computed" if e is None else
                     "%s (%s to %s); %s" % (fmt_num1(e["median_ms"]), fmt_num1(e["min_ms"]), fmt_num1(e["max_ms"]),
                                            fmt_num1(e["drift_ms_per_hour"]))))
    return rows


REFERENCE_GROUPS = (
    ("plan", "Plan against ground truth, every trial of the plan",
     "misses in detection (Table~\\ref{tab:detection}) and per-text agreement (Table~\\ref{tab:text-agreement})"),
    ("delivery", "Delivery of the presses",
     "the timing of the key rows (Table~\\ref{tab:key-timing}) and misses in detection"),
    ("focus", "Losses of focus of the reference input field", "false positives in detection"),
    ("markers", "Times at which the block-start markers were recorded",
     "misses of short sessions in detection and the difference of \\texttt{dwell\\_ms} (Table~\\ref{tab:session})"))


def table_reference(cells, clocks):
    """Table 7: what the reference establishes, one column per cell, with the estimates each check is used to read."""
    per = [reference_rows(c, clocks.get(c.label)) for c in cells]
    if any([(g, f) for g, f, _ in p] != [(g, f) for g, f, _ in per[0]] for p in per):
        raise ValueError("the rows of the checks differ between cells")
    width = (0.66 / len(cells)) if cells else 0.66
    # The legend of the marker times is given only when a cell has none.
    not_computed = ("not computed: the marker times of the cell were not computed; "
                    if any(clocks.get(c.label) is None for c in cells) else "")
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "{\\footnotesize\n",
             "\\caption{What the reference establishes, per cell: the agreement of the ground truth with the intent of "
             "the plan (count, and rate with its Wilson interval), the delivery of the presses that the executor sent "
             "(a / b: all presses / the input-mode keys left out, which the logger does not record by design), the "
             "losses of focus of the reference input field (a planned switch of S15 that returns within its trial is "
             "not counted as a trial with a loss) with the session rows assigned to the trials concerned, and the "
             "differences between the times at which the executor, the reference and the observation recorded the "
             "block-start markers, which combine the offsets of the clocks with the delays of the paths. Each group "
             "names the estimates it is used to read; no check excludes a trial or changes a value. Figures without an "
             "interval are descriptive; %s%s: no value}\n" % (not_computed, NO_VALUE),
             "\\label{tab:reference}\n",
             "\\begin{tabular}{L{0.34}" + ("L{%.3f}" % width) * len(cells) + "}\n",
             "\\toprule\n",
             "Figure & " + " & ".join(cell_name(c.label) for c in cells) + " \\\\\n"]
    for key, title, read in REFERENCE_GROUPS:
        lines += ["\\midrule\n",
                  "\\multicolumn{%d}{L{%.3f}}{\\textit{%s}; read with %s} \\\\\n" % (
                      1 + len(cells), 0.34 + width * len(cells), title, read)]
        for i, (g, fig, _) in enumerate(per[0] if per else []):
            if g != key:
                continue
            lines.append(" & ".join([fig] + [p[i][2] for p in per]) + " \\\\\n")
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def reference_values(cell, cs):
    """Values of the checks of the instruments for the text, with their sources."""
    lab, vals = cell.label, {}
    o = cell.plan_agreement()
    src = cell.path("e1_l1.json", PRIMARY_SCOPE, "oracle")
    vals[lab + ".plan.trials"] = {"text": fmt_int(o["n_trials"]), "value": o["n_trials"], "source": src + "/n_trials"}
    vals[lab + ".plan.disagree"] = {"text": count_ci(o["n_disagree"], o["disagree_rate"], o["disagree_ci"]),
                                    "value": [o["n_disagree"], o["disagree_rate"]] + list(o["disagree_ci"]),
                                    "source": src + "/n_disagree|disagree_rate|disagree_ci"}
    for t, _ in PLAN_TYPES:
        e = o["by_type"][t]
        vals["%s.plan.%s" % (lab, t)] = {"text": count_ci(e["n"], e["rate"], e["ci"]),
                                         "value": [e["n"], e["rate"]] + list(e["ci"]), "source": src + "/by_type/" + t}
    d = cell.dlv
    la = d["send_lateness_ms"]["overall"]
    vals[lab + ".delivery.lateness"] = {
        "text": "%s~ms (median), %s~ms (95th percentile) and %s~ms (maximum)" % (
            fmt_ms2(la["median"]), fmt_ms2(la["p95"]), fmt_ms2(la["max"])),
        "value": [la["median"], la["p95"], la["max"]], "source": cell.dpath("send_lateness_ms/overall")}
    for side, _ in DELIVERY_SIDES:
        tag = "obs" if side == "observation" else "ref"
        for v, _ in REACH_VERSIONS:
            e = d[side]["reach"][v]
            vk = "%s.delivery.%s.%s" % (lab, tag, "all" if v == "all_presses" else "nomode")
            vals[vk + ".reach"] = {"text": "%s of %s (%s)" % (fmt_int(e["reached"]), fmt_int(e["sent_presses"]),
                                                              fmt_p(e["reach_rate"])),
                                   "value": [e["reached"], e["sent_presses"], e["reach_rate"]],
                                   "source": cell.dpath(side, "reach", v)}
            for k in ("excess_rows", "trials_with_excess", "trials_with_loss", "rows_outside_trials"):
                vals["%s.%s" % (vk, k.replace("_", ""))] = {"text": fmt_int(e[k]), "value": e[k],
                                                           "source": cell.dpath(side, "reach", v, k)}
    f = d["reference_focus_loss"]
    for k in ("n_loss_intervals_planned", "n_loss_intervals_unplanned", "n_loss_intervals_unplanned_with_presses",
              "n_trials_with_focus_loss"):
        vals["%s.focus.%s" % (lab, k[2:].replace("_", ""))] = {"text": fmt_int(f[k]), "value": f[k],
                                                              "source": cell.dpath("reference_focus_loss", k)}
    for scope in SCOPES:
        fl = cell.det[scope]["focus_loss"]
        vals["%s.focus.rows.%s" % (lab, "primary" if scope == PRIMARY_SCOPE else "all")] = {
            "text": "%s session rows, %s of them false positives" % (fmt_int(fl["n_rows"]), fmt_int(fl["n_false_pos"])),
            "value": [fl["n_rows"], fl["n_false_pos"]], "source": cell.path("e1_detection.json", scope, "focus_loss")}
    if cs is not None:
        src = cell.prefix + clock_sync_name(lab)
        vals[lab + ".markers.blocks"] = {
            "text": "%s of %s blocks" % (fmt_int(len(cs["oracle"]["with_marker"])), fmt_int(cs["oracle"]["blocks"])),
            "value": [len(cs["oracle"]["with_marker"]), cs["oracle"]["blocks"], len(cs["gt"]["matched"]),
                      len(cs["obs"]["matched"])], "source": src + ":oracle|gt/matched|obs/matched"}
        for k, _ in CLOCK_DIFFS:
            e = cs["differences"][k]
            short = k.replace("_minus_", "").replace("_wall", "").replace("_", "")
            vals["%s.markers.%s.median" % (lab, short)] = {"text": fmt_num1(e["median_ms"]) + "~ms",
                                                          "value": e["median_ms"], "source": src + ":differences/" + k}
            vals["%s.markers.%s.range" % (lab, short)] = {
                "text": "%s to %s~ms" % (fmt_num1(e["min_ms"]), fmt_num1(e["max_ms"])),
                "value": [e["min_ms"], e["max_ms"]], "source": src + ":differences/" + k}
            vals["%s.markers.%s.drift" % (lab, short)] = {"text": fmt_num1(e["drift_ms_per_hour"]) + "~ms per hour",
                                                         "value": e["drift_ms_per_hour"],
                                                         "source": src + ":differences/" + k + "/drift_ms_per_hour"}
    return vals


def timing_entry(t, measure, speed):
    """The summary of one measure of KT1 for a speed, or for every speed (speed None); None when absent."""
    m = t[measure]
    return m["overall"] if speed is None else (m.get("by_speed") or {}).get(speed)


def timing_values(cell):
    """KT1: every summary of the delay and the interval error per side and speed, and the trials paired."""
    vals = {}
    for side, _ in DELIVERY_SIDES:
        tag = "obs" if side == "observation" else "ref"
        t = cell.dlv[side]["timing"]
        for k in ("n_trials_paired", "n_trials_not_paired"):
            vals["%s.kt.%s.%s" % (cell.label, tag, k.replace("n_trials_", "").replace("_", ""))] = {
                "text": fmt_int(t[k]), "value": t[k], "source": cell.dpath(side, "timing", k)}
        for measure, _ in TIMING_MEASURES:
            for sp in SPEEDS + (None,):
                e = timing_entry(t, measure, sp)
                if e is None:
                    continue
                for stat in TIMING_STATS:
                    vals["%s.kt.%s.%s.%s.%s" % (cell.label, tag, measure.split("_")[0], sp or "all", stat)] = {
                        "text": fmt_int(e[stat]) if stat == "n" else fmt_num1(e[stat]) + "~ms", "value": e[stat],
                        "source": cell.dpath(side, "timing", measure, "overall" if sp is None else "by_speed/" + sp,
                                             stat)}
    return vals


def table_key_timing(cells, side, label, long_table=False):
    """KT1 for one side: the delay and the interval error per cell and speed (n, mean, minimum, 5th percentile,
    median, 95th percentile, maximum), with the trials paired and not paired per cell. The main text takes a
    sideways table, the supplement (which does not load rotating) a landscape long table."""
    who = "key rows of presses in the log" if side == "observation" else "key-downs of the reference log"
    caption = ("\\caption{Timing of the %s on the software path, per cell and speed, in milliseconds, as the delivery "
               "report defines it: in the trials whose rows equal the presses sent other than the input-mode keys, "
               "the i-th row is paired with the i-th press; the delay is the time of the row minus the executor's "
               "time of dispatch, and the interval error is the difference between consecutive rows minus that "
               "between the consecutive presses. The times are whole milliseconds, so the minimum and the maximum are "
               "whole. The trials paired and not paired are given per cell, over all speeds, as the report gives "
               "them; %s: no value}" % (who, NO_VALUE))
    spec = "{L{0.14}L{0.08}L{0.11}L{0.07}" + "L{0.09}" * 6 + "}\n"
    head = "Cell & Speed & Measure & n & Mean & Minimum & 5th percentile & Median & 95th percentile & Maximum \\\\\n"
    if long_table:
        lines = [GENERATED_NOTE, "\\begin{landscape}\n", "{\\footnotesize\n", "\\begin{longtable}" + spec,
                 caption + "\\label{%s}\\\\\n" % label, "\\toprule\n", head, "\\midrule\n\\endhead\n"]
    else:
        lines = [GENERATED_NOTE, "\\begin{sidewaystable}[p]\n", "{\\footnotesize\n", caption + "\n",
                 "\\label{%s}\n" % label, "\\begin{tabular}" + spec, "\\toprule\n", head, "\\midrule\n"]
    for cell in cells:
        t = cell.dlv[side]["timing"]
        first = True
        for sp in SPEEDS + (None,):
            for j, (measure, mlabel) in enumerate(TIMING_MEASURES):
                e = timing_entry(t, measure, sp)
                head = [cell_name(cell.label) if first else "", (sp or "all speeds") if j == 0 else "", mlabel]
                first = False
                if e is None:
                    lines.append(" & ".join(head) + " & \\multicolumn{7}{l}{not in the result file} \\\\\n")
                    continue
                lines.append(" & ".join(head + [fmt_int(e["n"])] + [fmt_num1(e[s]) for s in TIMING_STATS[1:]])
                             + " \\\\\n")
        lines.append(" & \\multicolumn{9}{l}{Trials paired: %s; not paired: %s} \\\\\n" % (
            fmt_int(t["n_trials_paired"]), fmt_int(t["n_trials_not_paired"])))
    if long_table:
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n"]
    else:
        lines += ["\\bottomrule\n", "\\end{tabular}\n", "}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def reference_supplement(cells, clocks):
    """Supplement: the plan against the ground truth per stratum (with the trials that overlap a loss of focus), the
    differences at the markers per block, and the timing of the key-downs of the reference."""
    lines = [GENERATED_NOTE, "\\section*{What the reference establishes, per stratum and per block}\n"
             "\\label{sec:supp-reference}\n\n",
             "Generated from the result files of questions 1 to 3 and of the marker times. Disagreeing trials stay in "
             "the evaluation. The trials with a loss of focus leave out a planned switch of S15 that returns within its "
             "trial.\n\n"]
    short = ("No session", "Unex\\-pected", "Several", "Commit for cancel", "Cancel for commit", "Open at end",
             "Not in plan")
    for cell in cells:
        o = cell.plan_agreement()
        focus = dict(((st["scenario"], st["speed"]), st) for st in cell.dlv["reference_focus_loss"]["by_stratum"])
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.08}L{0.06}L{0.06}L{0.16}" + "L{0.08}" * len(PLAN_TYPES) + "L{0.08}}\n",
                  "\\caption{Plan against ground truth per stratum, cell %s: trials of the plan, disagreeing trials "
                  "(count, and rate with its Wilson interval), the count of each type, and the trials that overlap a "
                  "loss of focus of the reference}\\label{tab:plan-strata-%s}\\\\\n" % (cell_name(cell.label),
                                                                                       cell.label),
                  "\\toprule\n",
                  "Scenario & Speed & Trials & Disagreeing & " + " & ".join(short) + " & Loss of focus \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for st in sorted(o["by_stratum"], key=lambda st: (scenario_order(st["stratum"].get("scenario") or ""),
                                                          SPEEDS.index(st["stratum"]["speed"])
                                                          if st["stratum"].get("speed") in SPEEDS else len(SPEEDS))):
            sc, sp = st["stratum"].get("scenario"), st["stratum"].get("speed")
            fo = focus.get((sc, sp))
            lines.append(" & ".join(
                [sc or "not in the plan", sp or "", fmt_int(st["n_trials"]),
                 count_ci(st["n_disagree"], st["disagree_rate"], st["disagree_ci"])]
                + [fmt_int((st["by_type"].get(t) or {}).get("n", 0)) for t, _ in PLAN_TYPES]
                + [NO_VALUE if fo is None else fmt_int(fo["n_trials_with_focus_loss"])]) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    for cell in cells:
        cs = clocks.get(cell.label)
        if cs is None:
            lines.append("Cell %s: the marker times were not computed.\n\n" % cell_name(cell.label))
            continue
        per = [dict((b["block_id"], b["diff_ms"]) for b in cs["differences"][k]["per_block"]) for k, _ in CLOCK_DIFFS]
        blocks = sorted(set(b for p in per for b in p))
        lines += ["{\\footnotesize\n",
                  "\\begin{longtable}{L{0.10}" + "L{0.22}" * len(CLOCK_DIFFS) + "}\n",
                  "\\caption{Differences at the block-start markers per block, cell %s, in milliseconds (the first key "
                  "of the marker on each side; wall clocks unless stated); %s: the marker was not found on a side}"
                  "\\label{tab:markers-%s}\\\\\n" % (cell_name(cell.label), NO_VALUE, cell.label),
                  "\\toprule\n",
                  "Block & " + " & ".join(label for _, label in CLOCK_DIFFS) + " \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for b in blocks:
            lines.append(" & ".join([fmt_int(b)] + [fmt_num1(p.get(b)) for p in per]) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n\n"]
    lines.append(table_key_timing(cells, "reference", "tab:key-timing-reference", True).replace(GENERATED_NOTE, ""))
    return "".join(lines)


# ---------------------------------------------------------------- tables


TEX_SPECIAL = {"\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "_": "\\_", "%": "\\%", "&": "\\&", "#": "\\#",
               "$": "\\$", "^": "\\^{}", "~": "\\~{}"}


def tex_escape(s):
    """A string of the result files as text: every character that TeX treats specially is escaped."""
    return "".join(TEX_SPECIAL.get(c, c) for c in s)


def cell_name(label):
    """The name under which tables and figures show a cell; the escaped label for a cell not in CELL_NAMES."""
    return CELL_NAMES.get(label) or tex_escape(label)


def ci_text(e):
    return est_ci(e.get("value"), e.get("lo"), e.get("hi"))


def speed_range(kept, strata, sp, m):
    """The range of the Wilson intervals of measure m over the scenarios of speed sp that enter it, as text, and
    (lo, hi, used, scenarios); lo and hi are None when no scenario enters."""
    keys = [k for k in kept if k[1] == sp]
    used = strata_in_range(kept, strata, keys, m)
    if not used:
        return "%s (0/%d)" % (NO_VALUE, len(keys)), (None, None, 0, len(keys))
    lo, hi = interval_range([strata[k][m] for k in used])
    return "%s--%s (%d/%d)" % (fmt_p(lo), fmt_p(hi), len(used), len(keys)), (lo, hi, len(used), len(keys))


def table_detection(cells):
    """Table 8: detection per cell, comparison and speed. The trials kept per scenario are those of the primary
    comparison: the three comparisons cover the same trials (section 6.4)."""
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "\\caption{Detection per cell, comparison and speed: the range from the lowest lower end to the highest "
             "upper end of the Wilson intervals of the scenarios that keep at least %d trials and have a value of the "
             "measure, with the number of such scenarios and of all scenarios in parentheses, in the primary scope and "
             "over all trials. The comparisons with the prediction exist for the macOS cells only; %s: no scenario in "
             "the range}\n" % (MIN_TRIALS, NO_VALUE),
             "\\label{tab:detection}\n",
             "\\begin{tabular}{L{0.17}L{0.12}L{0.07}L{0.16}L{0.16}L{0.16}L{0.16}}\n",
             "\\toprule\n",
             "Cell & Comparison & Speed & Recall & Precision & Recall, all trials & Precision, all trials \\\\\n",
             "\\midrule\n"]
    for cell in cells:
        kept = dict((scope, cell.kept_trials(scope)[0]) for scope in SCOPES)
        for i, (comp, comp_label) in enumerate((c, l) for c, l in COMPARISONS if c in cell.comparisons):
            strata = dict((scope, cell.detection_strata(scope, comp)) for scope in SCOPES)
            for j, sp in enumerate(SPEEDS):
                row = [cell_name(cell.label) if i == 0 and j == 0 else "", comp_label if j == 0 else "", sp]
                for scope in SCOPES:
                    for m in ("recall", "precision"):
                        row.append(speed_range(kept[scope], strata[scope], sp, m)[0])
                lines.append(" & ".join(row) + " \\\\\n")
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def comparison_values(cell):
    """The ranges of recall and precision over the scenarios (both scopes) for the comparisons with the prediction,
    per speed, and over all speeds by the rule of the abstract, for the text."""
    vals = {}
    for comp in cell.comparisons:
        if comp == PRIMARY_COMPARISON:
            continue
        for scope in SCOPES:
            kept = cell.kept_trials(scope)[0]
            strata = cell.detection_strata(scope, comp)
            tag = "primary" if scope == PRIMARY_SCOPE else "all"
            design = sorted(kept, key=lambda k: (scenario_order(k[0]), SPEEDS.index(k[1])))
            for m in ("recall", "precision"):
                src = "%s:cells/%s/%s/%s/%s/metrics/by_stratum/*/%s" % (
                    cell.src["e1_detection.json"], cell.label, cell.definition, comp, scope, m)
                used = strata_in_range(kept, strata, design, m)
                lo, hi = interval_range([strata[k][m] for k in used]) if used else (None, None)
                vals["%s.%s.%s.%s.range" % (cell.label, comp.replace("_", ""), tag, m)] = {
                    "text": "%s--%s" % (fmt_p(lo), fmt_p(hi)) if used else NO_VALUE, "value": [lo, hi],
                    "source": src}
                vals["%s.%s.%s.%s.strata.left" % (cell.label, comp.replace("_", ""), tag, m)] = {
                    "text": str(len(design) - len(used)), "value": len(design) - len(used), "source": src}
    return vals


def table_layers(cells, d10s):
    """Supplement: detection per layer of the prediction (macOS), for every comparison and scope, pooled and per
    speed, with the merge rate of the corrected analysis of deviation D10 next to the registered one."""
    lines = [GENERATED_NOTE, "\\section*{Detection per layer of the prediction}\n\\label{sec:supp-layers}\n\n",
             "A diagnostic of the macOS cells (section 6.4 of the registration): the detection measures for the trials "
             "of four layers marked by the prediction, carried out, received carry, closing key in reading, and the "
             "trials in none of the three. A trial with several marks is counted in each of its layers. Merge rate: as "
             "registered; Corrected: the merge rate of the corrected analysis of deviation D10; %s: no value (no "
             "denominator, or a numerator above the denominator, deviation D10). The Windows cell has no "
             "prediction.\n\n" % NO_VALUE]
    for cell in cells:
        if "gt_pred" not in cell.comparisons:
            continue
        d10 = d10s.get(cell.label)
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.10}L{0.10}L{0.11}L{0.07}L{0.08}L{0.07}L{0.12}L{0.12}L{0.07}L{0.07}L{0.09}}\n",
                  "\\caption{Detection per layer of the prediction, cell %s (definition \\texttt{%s}), with Wilson "
                  "intervals%s}\\label{tab:layers-%s}\\\\\n" % (
                      cell_name(cell.label), tex_escape(cell.definition),
                      "" if d10 is not None else "; the corrected analysis was not computed for this cell", cell.label),
                  "\\toprule\n",
                  "Comparison & Scope & Layer (trials) & Speed & Sessions & Rows & Recall & Precision & Split rate & "
                  "Merge rate & Corrected \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for comp in cell.comparisons:
            for scope in LAYER_SCOPES:
                block = cell.det_by[comp].get(scope)
                layers = ((block or {}).get("diagnostics") or {}).get("prediction_layers")
                if not layers:
                    raise ValueError("%s: no layers of the prediction in %s under %s" % (
                        cell.src["e1_detection.json"], comp, scope))
                cor_layers = None if d10 is None else d10[comp][scope]["diagnostics"]["prediction_layers"]
                for li, (name, label) in enumerate(LAYERS):
                    by = dict((e["stratum"]["speed"], e) for e in layers[name]["by_speed"])
                    cor_by = {} if cor_layers is None else dict(
                        (e["stratum"]["speed"], e) for e in cor_layers[name]["by_speed"])
                    for si, sp in enumerate(SPEEDS + (None,)):
                        e = layers[name]["pooled"] if sp is None else by.get(sp)
                        ce = (None if cor_layers is None else cor_layers[name]["pooled"]) if sp is None else cor_by.get(sp)
                        head = [COMPARISONS[[c for c, _ in COMPARISONS].index(comp)][1] if li == 0 and si == 0 else "",
                                "\\texttt{%s}" % tex_escape(scope) if li == 0 and si == 0 else "",
                                "%s (%s)" % (label, fmt_int(layers[name]["n_trials"])) if si == 0 else "",
                                sp or "pooled"]
                        if e is None:
                            lines.append(" & ".join(head) + " & \\multicolumn{7}{l}{not in the result file} \\\\\n")
                            continue
                        lines.append(" & ".join(head + [
                            fmt_int(e["n_gt"]), fmt_int(e["n_obs"]), ci_text(e["recall"]), ci_text(e["precision"]),
                            fmt_p(e["split_rate"]["value"]), fmt_p(e["merge_rate"]["value"]),
                            NO_VALUE if ce is None else fmt_p(ce["merge_rate"]["value"])]) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    return "".join(lines)


def session_cells(e):
    """Pairs, bias, lower and upper limit of agreement and median absolute difference of dwell_ms (each with its
    interval), and the agreement of abandon (raw and kappa) and of nav (exact), each with its interval."""
    dw, ba, ab, nav = e["dwell"], e["dwell"]["bland_altman"], e["abandon"], e["nav"]
    ci = lambda key: dw.get(key) or [None, None]
    return [fmt_int(dw["n"]),
            est_ci(ba["bias"], ci("bias_ci")[0], ci("bias_ci")[1], fmt_ms),
            est_ci(ba["loa_lo"], ci("loa_lo_ci")[0], ci("loa_lo_ci")[1], fmt_ms),
            est_ci(ba["loa_hi"], ci("loa_hi_ci")[0], ci("loa_hi_ci")[1], fmt_ms),
            est_ci(dw["mae"], ci("mae_ci")[0], ci("mae_ci")[1], fmt_ms),
            est_ci(ab["agree"], ab["agree_ci"][0], ab["agree_ci"][1]),
            est_ci(ab["kappa"], ab["kappa_ci"][0], ab["kappa_ci"][1]),
            est_ci(nav["exact"], nav["exact_ci"][0], nav["exact_ci"][1])]


SESSION_HEAD = ("Pairs & Bias (ms) & Lower limit (ms) & Upper limit (ms) & Median absolute difference (ms) & "
                "\\texttt{abandon} agreement & \\texttt{abandon} kappa & \\texttt{nav} exact \\\\\n")
SESSION_CAPTION = ("the difference of \\texttt{dwell\\_ms} from the duration of the reference session (bias, limits of "
                   "agreement and median absolute difference, each with a 95\\% interval that resamples blocks), the "
                   "agreement of \\texttt{abandon} with the outcome of the session (raw agreement with its Wilson "
                   "interval and kappa with its block interval), and the exact agreement of \\texttt{nav} with the "
                   "arrow presses sent (Wilson interval); Pairs is the number of matched sessions with a duration on "
                   "both sides. An estimate printed without an interval has its interval withheld (the reason is in "
                   "the result file); " + NO_VALUE + ": no value")


def table_session(cells):
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "{\\footnotesize\n",
             "\\caption{Per-session agreement per cell and speed, primary comparison and scope: " + SESSION_CAPTION + "}\n",
             "\\label{tab:session}\n",
             "\\begin{tabular}{L{0.12}L{0.06}L{0.06}L{0.11}L{0.11}L{0.11}L{0.11}L{0.11}L{0.11}L{0.10}}\n",
             "\\toprule\n",
             "Cell & Speed & " + SESSION_HEAD,
             "\\midrule\n"]
    for cell in cells:
        sp = cell.speed_sessions(PRIMARY_SCOPE)
        for i, s in enumerate(SPEEDS):
            if s not in sp:
                continue
            lines.append(" & ".join([cell_name(cell.label) if i == 0 else "", s] + session_cells(sp[s])) + " \\\\\n")
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def session_supplement(cells):
    """Supplement: per-session agreement per stratum, per speed and pooled, for every comparison and both scopes."""
    lines = [GENERATED_NOTE, "\\section*{Per-session agreement per stratum}\n\\label{sec:supp-session}\n\n",
             "Generated from the result files of question 2, for every comparison (three on macOS, the primary one on "
             "Windows) and both scopes of question 2. The rows of a speed and the pooled row collect the pairs of their "
             "strata; a pooled value is not an estimate for natural typing.\n\n"]
    for cell in cells:
        for comp in cell.comparisons:
            for scope in SCOPES:
                m = cell.l1_by[comp][scope]["metrics"]
                lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                          "\\begin{longtable}{L{0.08}L{0.06}L{0.07}L{0.12}L{0.12}L{0.12}L{0.11}L{0.11}L{0.11}L{0.10}}\n",
                          "\\caption{Per-session agreement per stratum, cell %s, comparison %s, %s: %s}"
                          "\\label{tab:session-%s-%s-%s}\\\\\n" % (
                              cell_name(cell.label), dict(COMPARISONS)[comp], scope_name(scope), SESSION_CAPTION, cell.label,
                              comp.replace("_", ""), scope.replace("_", "")),
                          "\\toprule\n",
                          "Scenario & Speed & " + SESSION_HEAD,
                          "\\midrule\n\\endhead\n"]
                for st in sorted(m["by_stratum"], key=lambda st: (scenario_order(st["stratum"].get("scenario") or ""),
                                                                  SPEEDS.index(st["stratum"]["speed"]))):
                    lines.append(" & ".join([st["stratum"].get("scenario") or "", st["stratum"]["speed"]]
                                            + session_cells(st)) + " \\\\\n")
                lines.append("\\midrule\n")
                by = dict((st["stratum"]["speed"], st) for st in m["by_speed"])
                for s in SPEEDS:
                    if s in by:
                        lines.append(" & ".join(["all", s] + session_cells(by[s])) + " \\\\\n")
                lines.append(" & ".join(["pooled", ""] + session_cells(m["pooled"])) + " \\\\\n")
                lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    return "".join(lines)


SCOPE_NAMES = {
    "under_limit": "the primary scope (\\texttt{under\\_limit})",
    "over_limit": "the long trials (\\texttt{over\\_limit})",
    "all_trials": "all trials (\\texttt{all\\_trials})",
    "under_limit_gt_duration": "the primary scope with the long trials taken from the reference (\\texttt{under\\_limit"
                               "\\_gt\\_duration}, sensitivity analysis)"}
STRATA_SCOPES = ("under_limit", "over_limit", "all_trials", "under_limit_gt_duration")


def scope_name(scope):
    return SCOPE_NAMES.get(scope, "\\texttt{%s}" % tex_escape(scope))


def table_composition(cell):
    """Section 6.1: the number of letter keys and the think time of the trials of each scope, per stratum, from the plan
    (the summary of the prediction; the Windows analysis computes no prediction)."""
    pred = cell.det_top.get("predictions") or {}
    if not pred.get("available"):
        return ("The number of letter keys and the think time per stratum and scope are computed with the prediction, "
                "which the analysis of cell %s does not compute.\n\n" % cell_name(cell.label))
    lines = ["\\begin{landscape}\n", "{\\footnotesize\n",
             "\\begin{longtable}{L{0.08}L{0.06}L{0.14}L{0.07}L{0.22}L{0.22}}\n",
             "\\caption{Composition of the scopes per stratum, cell %s, from the plan: trials, letter keys and think "
             "time of the trials, as median (minimum to maximum)}\\label{tab:composition-%s}\\\\\n" % (
                 cell_name(cell.label), cell.label),
             "\\toprule\n",
             "Scenario & Speed & Scope & Trials & Letter keys & Think time (ms) \\\\\n",
             "\\midrule\n\\endhead\n"]
    order = dict((s, i) for i, s in enumerate(STRATA_SCOPES))
    for e in sorted(pred["composition"], key=lambda e: (scenario_order(e["scenario"]), SPEEDS.index(e["speed"]),
                                                        order.get(e["scope"], len(order)))):
        mm = lambda s: NO_VALUE if s["n"] == 0 else "%s (%s to %s)" % (fmt_int(s["median"]), fmt_int(s["min"]),
                                                                      fmt_int(s["max"]))
        lines.append(" & ".join([e["scenario"], e["speed"], "\\texttt{%s}" % tex_escape(e["scope"]),
                                 fmt_int(e["n_trials"]), mm(e["letter_keys"]), mm(e["think_ms"])]) + " \\\\\n")
    lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    return "".join(lines)


def diagnostic_values(cell):
    """Descriptive values of sections 6.2, 6.4, 6.6 and 10.7 for the text: exclusions by reason, whether the two
    definitions hold the same sessions, starts while a session was open, rows of zero duration, the cut-off, the
    agreement of abandon split by continued commits, the pooled detection measures, and the Windows diagnostics."""
    lab, vals = cell.label, {}
    src_det = lambda *k: cell.path("e1_detection.json", *k)
    ex = cell.det[PRIMARY_SCOPE]["excluded"]
    # The exclusions are made before the scopes, and the comparisons cover the same trials (section 6.4); the first
    # side of pred_obs and the second side of gt_pred are the prediction, so only the sides that are the same records
    # are compared.
    same_side = {"gt_obs": ("trials", "gt", "obs"), "gt_pred": ("trials", "gt"), "pred_obs": ("trials", "obs")}
    for comp in cell.comparisons:
        first = cell.det_by[comp][PRIMARY_SCOPE]["excluded"]
        for scope in cell.det_by[comp]:
            if cell.det_by[comp][scope]["excluded"] != first:
                raise ValueError("%s: the exclusions of %s differ between scopes" % (cell.src["e1_detection.json"], comp))
        if any(first[k] != ex[k] for k in same_side[comp]):
            raise ValueError("%s: the exclusions of %s differ from those of the primary comparison" % (
                cell.src["e1_detection.json"], comp))
    for kind, field in (("trials", "by_type"), ("gt", "by_reason"), ("obs", "by_reason")):
        vals["%s.excluded.%s.n" % (lab, kind)] = {"text": fmt_int(ex[kind]["n"]), "value": ex[kind]["n"],
                                                 "source": src_det(PRIMARY_SCOPE, "excluded", kind, "n")}
        for reason, n in sorted(ex[kind][field].items()):
            vals["%s.excluded.%s.%s" % (lab, kind, reason.replace("_", ""))] = {
                "text": fmt_int(n), "value": n, "source": src_det(PRIMARY_SCOPE, "excluded", kind, field, reason)}
    same = cell.det_top.get("same_sessions_as_all")
    vals[lab + ".samesessions"] = {"text": NO_VALUE if same is None else ("yes" if same else "no"), "value": same,
                                   "source": "%s:same_sessions_as_all/%s" % (cell.src["e1_detection.json"], lab)}
    for scope in SCOPES:
        tag = "primary" if scope == PRIMARY_SCOPE else "all"
        sw = cell.det[scope]["diagnostics"]["start_while_open"]
        vals["%s.startwhileopen.%s" % (lab, tag)] = {"text": fmt_int(sw["n"]), "value": sw["n"],
                                                     "source": src_det(scope, "diagnostics/start_while_open/n")}
        z = cell.l1[scope]["zero_dwell_rows"]
        vals["%s.zerodwell.%s" % (lab, tag)] = {
            "text": "%s (%s of them false positives)" % (fmt_int(z["n"]), fmt_int(z["in_false_pos"])),
            "value": [z["n"], z["in_pairs"], z["in_split"], z["in_merge"], z["in_false_pos"]],
            "source": cell.path("e1_l1.json", scope, "zero_dwell_rows")}
        tr = cell.l1[scope]["truncation"]
        for k in ("n_long", "n_long_with_row", "n_long_excluded", "n_obs_over_limit", "n_obs_over_limit_false_pos"):
            vals["%s.cutoff.%s.%s" % (lab, tag, k[2:].replace("_", ""))] = {
                "text": fmt_int(tr[k]), "value": tr[k], "source": cell.path("e1_l1.json", scope, "truncation", k)}
        cc = cell.l1[scope]["metrics"]["pooled"]["abandon_by_continued_commit"]
        for side in ("with", "without"):
            e = cc[side]
            vals["%s.abandon.continued.%s.%s" % (lab, side, tag)] = {
                "text": "%s (%s pairs)" % (est_ci(e["agree"], e["agree_ci"][0], e["agree_ci"][1]), fmt_int(e["n"])),
                "value": [e["agree"]] + list(e["agree_ci"]) + [e["n"]],
                "source": cell.path("e1_l1.json", scope, "metrics/pooled/abandon_by_continued_commit", side)}
        vals["%s.abandon.continued.unknown.%s" % (lab, tag)] = {
            "text": fmt_int(cc["n_unknown"]), "value": cc["n_unknown"],
            "source": cell.path("e1_l1.json", scope, "metrics/pooled/abandon_by_continued_commit/n_unknown")}
    for comp in cell.comparisons:
        for scope in [s for s in STRATA_SCOPES if s in cell.det_by[comp]]:
            p = cell.det_by[comp][scope]["metrics"]["pooled"]
            base = "%s.pooled.%s.%s" % (lab, comp.replace("_", ""), scope.replace("_", ""))
            src = "%s:cells/%s/%s/%s/%s/metrics/pooled" % (cell.src["e1_detection.json"], lab, cell.definition, comp,
                                                           scope)
            for m in ("recall", "precision", "split_rate", "merge_rate", "miss_rate", "false_pos_rate"):
                vals["%s.%s" % (base, m.replace("_", ""))] = {"text": ci_text(p[m]),
                                                              "value": [p[m]["value"], p[m]["lo"], p[m]["hi"]],
                                                              "source": src + "/" + m}
            vals[base + ".f1"] = {"text": fmt_p(p["f1"]["value"]), "value": p["f1"]["value"], "source": src + "/f1"}
            vals[base + ".sessions"] = {"text": fmt_int(p["n_gt"]), "value": p["n_gt"], "source": src + "/n_gt"}
            vals[base + ".rows"] = {"text": fmt_int(p["n_obs"]), "value": p["n_obs"], "source": src + "/n_obs"}
    pred = cell.det_top.get("predictions") or {}
    if pred.get("available"):
        psrc = "%s:predictions/%s" % (cell.src["e1_detection.json"], lab)
        for k, v in sorted(pred["counts"].items()):
            vals["%s.pred.rows.%s" % (lab, k)] = {"text": fmt_int(v), "value": v, "source": psrc + "/counts/" + k}
        for k, v in sorted(pred["trials"].items()):
            if k != "by_stratum":
                vals["%s.pred.trials.%s" % (lab, k.replace("n_", "", 1).replace("_", ""))] = {
                    "text": fmt_int(v), "value": v, "source": psrc + "/trials/" + k}
    wd = cell.det_top.get("windows_diagnostics")
    if wd is not None:
        wsrc = "%s:windows_diagnostics/%s" % (cell.src["e1_detection.json"], lab)
        for k, v in sorted(wd["observed_rows_dwell_ms"].items()):
            vals["%s.win.rows.%s" % (lab, k.replace("_", ""))] = {"text": fmt_int(v), "value": v,
                                                                 "source": wsrc + "/observed_rows_dwell_ms/" + k}
        for k, v in sorted(wd["s4_session_end_after_escape"].items()):
            vals["%s.win.s4.escape%s" % (lab, k)] = {"text": fmt_int(v), "value": v,
                                                    "source": wsrc + "/s4_session_end_after_escape/" + k}
        nolen = wd["tsf_end_without_result_len"]
        vals[lab + ".win.endsnolength"] = {
            "text": ", ".join("%s in %s" % (fmt_int(nolen[s]), s) for s in sorted(nolen, key=scenario_order)) or "none",
            "value": nolen, "source": wsrc + "/tsf_end_without_result_len"}
    lt = cell.det_top.get("long_trials_from")
    if lt is not None:
        lsrc = "%s:long_trials_from/%s" % (cell.src["e1_detection.json"], lab)
        for k, v in sorted(lt["total"].items()):
            vals["%s.win.long.%s" % (lab, k.replace("n_", "", 1).replace("_", ""))] = {
                "text": fmt_int(v), "value": v, "source": lsrc + "/total/" + k}
        vals[lab + ".win.long.bandms"] = {"text": fmt_int(lt["band_ms"]), "value": lt["band_ms"],
                                          "source": lsrc + "/band_ms"}
    return vals


def table_strata(cell, d10=None):
    """One landscape long table per comparison and scope of the detection results (the supplement loads pdflscape and
    longtable). The last column is the merge rate of the corrected analysis of deviation D10, next to the registered
    one. The trials kept per stratum are known for the two scopes of question 3 (from its per-trial records); in the
    other scopes the strata are those of the detection results."""
    lines = [GENERATED_NOTE]
    for comp in cell.comparisons:
        for scope in [s for s in STRATA_SCOPES if s in cell.det_by[comp]]:
            lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                      "\\begin{longtable}{L{0.08}L{0.06}L{0.07}L{0.08}L{0.06}L{0.17}L{0.17}L{0.10}L{0.10}L{0.10}}\n",
                      "\\caption{Detection per stratum in %s, cell %s (definition \\texttt{%s}, comparison %s), "
                      "with Wilson intervals. Trials: trials kept in the scope (%s); Sessions and Rows: the reference "
                      "sessions (or predicted rows) and the session rows of the stratum that entered the matching; Merge "
                      "rate: as registered; Corrected: the merge rate of the corrected analysis of deviation D10%s; %s: "
                      "no value (no denominator, or a numerator above the denominator, deviation D10)}"
                      "\\label{tab:strata-%s-%s-%s}\\\\\n" % (
                          scope_name(scope), cell_name(cell.label), tex_escape(cell.definition), dict(COMPARISONS)[comp],
                          "from the per-trial records of question 3" if scope in SCOPES else
                          "not given for this scope", "" if d10 is not None else " (not computed for this cell)",
                          NO_VALUE, cell.label, comp.replace("_", ""), scope.replace("_", "")),
                      "\\toprule\n",
                      "Scenario & Speed & Trials & Sessions & Rows & Recall & Precision & Split rate & Merge rate & "
                      "Corrected \\\\\n",
                      "\\midrule\n\\endhead\n"]
            strata = cell.detection_strata(scope, comp)
            kept = cell.kept_trials(scope)[0] if scope in SCOPES else dict((k, None) for k in strata)
            corrected = d10_strata(d10[comp], scope) if d10 is not None else {}
            for k in sorted(kept, key=lambda k: (scenario_order(k[0]), SPEEDS.index(k[1]))):
                trials = NO_VALUE if kept[k] is None else fmt_int(kept[k])
                if k not in strata:
                    why = "no trial in the scope" if kept[k] == 0 else "no session in the scope"
                    lines.append("%s & %s & %s & \\multicolumn{7}{l}{%s} \\\\\n" % (k[0], k[1], trials, why))
                    continue
                st = strata[k]
                cor = fmt_p(corrected[k]["merge_rate"]["value"]) if k in corrected else NO_VALUE
                lines.append(" & ".join([k[0], k[1], trials, fmt_int(st["n_gt"]), fmt_int(st["n_obs"]),
                                         ci_text(st["recall"]), ci_text(st["precision"]),
                                         fmt_p(st["split_rate"]["value"]), fmt_p(st["merge_rate"]["value"]), cor])
                             + " \\\\\n")
            lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n"]
    return "".join(lines)


def table_closed_set(cells, docs):
    """Table 11: the judgement of section 6.8 on the two sets of the rule, per cell and K, and the recommendation."""
    rec = recommend(docs)
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "\\caption{Closed-set identification for the two feature sets that keep no time: the accuracy (the maximum "
             "over the set and the sets derivable from it) with its Wilson interval and, descriptively, the interval "
             "that resamples blocks; a set is near chance at K when the upper end of the Wilson interval is at most "
             "2/K, and near chance for the rule when it is so at both K = 10 and K = 100. The recommendation is "
             "decided from the two macOS cells; the Windows judgement is reported as its qualification; %s: no value}\n"
             % NO_VALUE,
             "\\label{tab:closed-set}\n",
             "\\begin{tabular}{L{0.20}L{0.12}L{0.06}L{0.07}L{0.22}L{0.15}L{0.07}L{0.10}}\n",
             "\\toprule\n",
             "Cell & Feature set & K & Trials & Accuracy & Block interval & 2/K & Near chance \\\\\n",
             "\\midrule\n"]
    for cell in cells:
        doc = docs.get(cell.label)
        if doc is None:
            lines.append("%s & \\multicolumn{7}{l}{not measured} \\\\\n" % cell_name(cell.label))
            continue
        first = True
        for f in RULE_SETS:
            for K in RULE_KS:
                r = doc["results"][str(K)][f]
                blk = r.get("acc_ci_block") or [None, None]
                lines.append(" & ".join([
                    cell_name(cell.label) if first else "", "\\texttt{%s}" % tex_escape(f), fmt_int(K), fmt_int(r["n"]),
                    est_ci(r["acc"], r["acc_ci"][0], r["acc_ci"][1]),
                    "%s, %s" % (fmt_p(blk[0]), fmt_p(blk[1])) if blk[0] is not None else NO_VALUE,
                    fmt_p(2.0 / K), yes_no(near_chance_at(r, K))]) + " \\\\\n")
                first = False
    lines += ["\\midrule\n",
              "\\multicolumn{8}{l}{Recommendation from the two macOS cells: %s} \\\\\n" % (
                  {"recommended": "\\texttt{%s}" % tex_escape(rec["set"] or ""), "none": "none",
                   "not decided": "not decided (a macOS cell was not measured)"}[rec["status"]]),
              "\\bottomrule\n", "\\end{tabular}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def table_closed_set_all(cells, docs):
    """Supplement: every feature set at every K per cell, and the raw accuracy per speed and per scenario."""
    lines = [GENERATED_NOTE, "\\section*{Closed-set identification}\n\\label{sec:supp-closed-set}\n\n",
             "Every feature set at every K, per cell, from the closed-set result files (deviation D8: every trial of "
             "%s left out). Accuracy is the maximum over the set and the sets derivable from it, with its source and "
             "its Wilson interval; Raw is the accuracy of the set alone with its Wilson interval; the speed and "
             "scenario columns give the raw accuracy, descriptively and without an interval. The feature sets that "
             "keep time are estimates only (section 6.8 of the registration).\n\n" % CLOSED_SET_SKIPPED]
    for cell in cells:
        doc = docs.get(cell.label)
        if doc is None:
            lines.append("Cell %s: not measured.\n\n" % cell_name(cell.label))
            continue
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.13}L{0.06}L{0.06}L{0.20}L{0.12}L{0.18}L{0.07}L{0.06}L{0.06}L{0.06}}\n",
                  "\\caption{Closed set, cell %s: accuracy per feature set and K}\\label{tab:closed-set-%s}\\\\\n" % (
                      cell_name(cell.label), cell.label),
                  "\\toprule\n",
                  "Feature set & K & Trials & Accuracy & Source & Raw & Mean rank & Slow & Mid & Fast \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for f in CLOSED_SET_SETS:
            for K in CLOSED_SET_KS:
                r = doc["results"][str(K)][f]
                sp = r.get("by_speed") or {}
                lines.append(" & ".join(
                    ["\\texttt{%s}" % tex_escape(f), fmt_int(K), fmt_int(r["n"]),
                     est_ci(r["acc"], r["acc_ci"][0], r["acc_ci"][1]), "\\texttt{%s}" % tex_escape(r["acc_source"]),
                     est_ci(r["acc_raw"], r["acc_ci_raw"][0], r["acc_ci_raw"][1]),
                     NO_VALUE if r.get("mean_rank") is None else "%.2f" % r["mean_rank"]]
                    + [fmt_p((sp.get(s) or {}).get("acc")) for s in SPEEDS]) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n"]
        scen = sorted(set(s for K in CLOSED_SET_KS for f in CLOSED_SET_SETS
                          for s in (doc["results"][str(K)][f].get("by_scenario") or {})), key=scenario_order)
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.08}L{0.06}" + "L{0.14}" * len(CLOSED_SET_SETS) + "}\n",
                  "\\caption{Closed set, cell %s: raw accuracy per scenario}\\label{tab:closed-set-scenario-%s}\\\\\n" % (
                      cell_name(cell.label), cell.label),
                  "\\toprule\n",
                  "Scenario & K & " + " & ".join("\\texttt{%s}" % tex_escape(f) for f in CLOSED_SET_SETS) + " \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for K in CLOSED_SET_KS:
            for s in scen:
                row = [s, fmt_int(K)]
                for f in CLOSED_SET_SETS:
                    e = (doc["results"][str(K)][f].get("by_scenario") or {}).get(s) or {}
                    row.append("%s (%s)" % (fmt_p(e.get("acc")), fmt_int(e.get("n", 0))))
                lines.append(" & ".join(row) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    return "".join(lines)


def table_open_set(cells, docs):
    """The open set (section 6.8) per cell, attacker and knowledge of the length."""
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "{\\footnotesize\n",
             "\\caption{Open-set reconstruction per cell and attacker: character error rate (CER) of the attack and of "
             "the prior-only baseline, the gain (baseline CER minus attack CER) and the gain over the shuffled control "
             "(the CER of another trial's decoding minus the attack's), each with a block-clustered 95\\%% percentile "
             "interval. K1 holds out whole works, K1+ splits the corpus by phrase; the baseline knows the number of "
             "segments or the true length. Undecodable: trials for which no string fits; %s: no value}\n" % NO_VALUE,
             "\\label{tab:open-set}\n",
             "\\begin{tabular}{L{0.14}L{0.11}L{0.10}L{0.06}L{0.13}L{0.13}L{0.12}L{0.11}L{0.10}}\n",
             "\\toprule\n",
             "Cell & Attacker & Length known & Trials & CER & Baseline CER & Gain & Gain over shuffled & "
             "Undecod\\-able \\\\\n",
             "\\midrule\n"]
    for cell in cells:
        doc = docs.get(cell.label)
        if doc is None:
            lines.append("%s & \\multicolumn{8}{l}{not measured} \\\\\n" % cell_name(cell.label))
            continue
        first = True
        for split, model, label in OPEN_SET_ATTACKERS:
            for j, (li, li_label) in enumerate(OPEN_SET_LENGTHS):
                r = doc["splits"][split][li]
                a, b, g, sh = r[model], r["baseline"][model], r["gain"][model], r["shuffled"][model]
                lines.append(" & ".join([
                    cell_name(cell.label) if first else "", label if j == 0 else "", li_label, fmt_int(r["n"]),
                    est_ci(a["cer"], a["cer_ci"][0], a["cer_ci"][1]), est_ci(b["cer"], b["cer_ci"][0], b["cer_ci"][1]),
                    est_ci(g["gain"], g["gain_ci"][0], g["gain_ci"][1], fmt_signed),
                    est_ci(sh["gain_vs_shuffled"], sh["gain_vs_shuffled_ci"][0], sh["gain_vs_shuffled_ci"][1],
                           fmt_signed),
                    fmt_int(a["n_undecodable"])]) + " \\\\\n")
                first = False
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def table_cost(e3):
    """Table 12: runtime cost per system and condition, never reduced to one figure; the systems are not compared."""
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "\\caption{Runtime cost of the logger per system and condition: CPU of the logger (one core is 100) over "
             "the samples, its final resident memory (macOS) or working set (Windows) and the least-squares slope of "
             "it. The samplers of the two systems measure differently, so the values are read within each system and "
             "not compared between systems; %s: not measured, or not to be quoted (section 6.10)}\n" % NO_VALUE,
             "\\label{tab:cost}\n",
             "\\begin{tabular}{L{0.10}L{0.09}L{0.18}L{0.07}L{0.09}L{0.10}L{0.10}L{0.07}L{0.10}L{0.10}}\n",
             "\\toprule\n",
             "System & Condition & Status & Mean & Median & 95th percentile & Maximum & Hours & Memory (MB) & "
             "Slope (MB/h) \\\\\n",
             "\\midrule\n"]
    for system in ("macos", "windows"):
        src, conds = e3.get(system, (None, {}))
        for i, c in enumerate(E3_CONDITIONS):
            s = conds.get(c)
            head = [{"macos": "macOS", "windows": "Windows"}[system] if i == 0 else "", c]
            if s is None:
                lines.append(" & ".join(head + ["not measured"] + [NO_VALUE] * 7) + " \\\\\n")
                continue
            ok = e3_quotable(system, c, s)
            status = s["status"] if ok or s["status"] != "measured" else "insufficient (every CPU sample is zero)"
            if ok and s["cpu_all_zero"]:
                status += " (every CPU sample is zero)"
            cpu = s["cpu"] or {}
            row = head + [status] + [fmt_cpu(cpu.get(m)) if ok else NO_VALUE for m in ("mean", "median", "p95", "max")]
            row += ["%.2f" % s["hours"] if ok and s["hours"] is not None else NO_VALUE,
                    "%.1f" % s["rss_final_mb"] if ok and s["rss_final_mb"] is not None else NO_VALUE,
                    fmt_cpu(s["rss_slope_mb_per_h"]) if ok else NO_VALUE]
            lines.append(" & ".join(row) + " \\\\\n")
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


REFERENCE_CONSTANT = "%s (reference constant" % NO_VALUE
REFERENCE_CONSTANT_TEXT = "no value: the reference's value is the same in every text (deviation D11)"
REFERENCE_CONSTANT_NOTE = ("%s; registered \\ldots): no ICC(A,1), because the reference's value is the same in every "
                           "text, so the ICC is zero whatever the logger writes, or undefined if the logger writes that "
                           "same value in every text too (section 6.11 of the registration; deviation D11); the value "
                           "that the frozen code returns, the registered result, follows \\emph{registered}"
                           % REFERENCE_CONSTANT)


def reference_spread(cell, scope, m, speed=None):
    """The reference's values of a per-text measure over the texts on which agreement() was computed (both sides have
    the measure), at one speed or (speed None) over all: {"n", "sd", "constant"}. sd is the sample standard deviation
    (None with fewer than two texts). constant: two or more texts and every value the same; then ICC(A,1) is zero
    whatever the observation (the subjects' and the residual mean squares are equal), which section 6.11 of the
    registration reports as no value (zero variance), and the article does so under deviation D11."""
    agg = cell.l2[scope]["aggregate"]
    gts, obs = agg["gt"], agg["obs"]
    if len(gts) != len(obs):
        raise ValueError("%s: the two sides of the per-text aggregate differ in length" % cell.path(
            "e1_l2.json", scope, "aggregate"))
    vals = []
    for g, o in zip(gts, obs):
        if g["text_id"] != o["text_id"] or g["speed"] != o["speed"]:
            raise ValueError("%s: the two sides of the per-text aggregate are not aligned" % cell.path(
                "e1_l2.json", scope, "aggregate"))
        if speed is not None and g["speed"] != speed:
            continue
        if g[m] is None or o[m] is None:
            continue
        vals.append(g[m])
    n = len(vals)
    if n >= 2:
        # math.fsum, not sum: sum() adds floats with compensation from Python 3.12 on, so the last digits written to
        # values.json would depend on the version of Python that made them.
        mean = math.fsum(vals) / float(n)
        sd = math.sqrt(math.fsum((v - mean) ** 2 for v in vals) / (n - 1))
    else:
        sd = None
    # The number of texts whose value differs from the most frequent one (ties go to the smaller value), so that a
    # reference that differs in a single text can be told from one that varies more.
    counts = collections.Counter(vals)
    mode = min(counts, key=lambda v: (-counts[v], v)) if vals else None
    off_mode = sum(1 for v in vals if v != mode)
    return {"n": n, "sd": sd, "constant": n >= 2 and max(vals) == min(vals), "off_mode": off_mode}


def checked_spread(cell, scope, m, speed, e):
    """reference_spread(), required to cover the same texts as the agreement entry e."""
    sp = reference_spread(cell, scope, m, speed)
    if sp["n"] != e["n"]:
        raise ValueError("%s: the reference's texts (%d) differ from the agreement's (%d)" % (
            cell.path("e1_l2.json", scope, "aggregate"), sp["n"], e["n"]))
    return sp


def icc_text(e, spread):
    """ICC(A,1) with its interval; under deviation D11 no value, with the registered result (the frozen code's value)
    next to it, as section 8 of the registration asks of a correction (next to the registered result, not in its
    place)."""
    value = est_ci(e["icc21"], e["icc21_ci"][0], e["icc21_ci"][1], fmt_signed)
    if spread["constant"]:
        return "%s; registered %s)" % (REFERENCE_CONSTANT, value)
    return value


def fmt_sd(x, ms):
    if x is None:
        return NO_VALUE
    return fmt_ms(x) if ms else "%.3f" % x


def _agreement_row(e, ms, spread):
    """Texts, the reference's standard deviation between texts, bias, limits of agreement and ICC(A,1) of one per-text
    measure, each estimate with its interval."""
    sg = lambda x: fmt_signed(x, ms)
    ba = e["bland_altman"]
    return [fmt_int(e["n"]), fmt_sd(spread["sd"], ms), est_ci(ba["bias"], e["bias_ci"][0], e["bias_ci"][1], sg),
            est_ci(ba["loa_lo"], e["loa_lo_ci"][0], e["loa_lo_ci"][1], sg),
            est_ci(ba["loa_hi"], e["loa_hi_ci"][0], e["loa_hi_ci"][1], sg),
            icc_text(e, spread)]


def _agreement_rows(cell, scope, first_col=None):
    """Rows of measure and speed; with first_col, a first column that names the cell on the first row."""
    lines = []
    by = cell.l2[scope]["agreement_by_stratum"]
    first = True
    for m, label, ms in L2_MEASURES:
        for i, s in enumerate(SPEEDS):
            e = (by.get(s) or {}).get(m)
            head = ([first_col if first else ""] if first_col is not None else []) + [label if i == 0 else "", s]
            first = False
            if e is None:
                lines.append(" & ".join(head) + " & \\multicolumn{6}{l}{no text in the scope} \\\\\n")
                continue
            lines.append(" & ".join(head + _agreement_row(e, ms, checked_spread(cell, scope, m, s, e))) + " \\\\\n")
    return lines


AGREEMENT_HEAD = ("Speed & Texts & Reference SD & Bias & Lower limit & Upper limit & ICC(A,1) \\\\\n")


def table_text_agreement(cells):
    """Table 10: per-text agreement of the four measures per cell and speed, primary scope."""
    lines = [GENERATED_NOTE,
             "\\begin{sidewaystable}[p]\n",
             "{\\footnotesize\n",
             "\\caption{Per-text agreement per cell, measure and speed, primary scope: bias and limits of agreement of the "
             "per-text difference (observation minus reference) and ICC(A,1), each with a 95\\%% interval that resamples "
             "blocks; Texts is the number of texts with a value on both sides, and Reference SD the standard deviation "
             "of the reference's values between those texts. An estimate printed without an interval has its interval "
             "withheld (the reason is in the result file); %s: no value; %s. The values hold for this design's numbers "
             "of trials per text and are not compared between speeds}\n" % (NO_VALUE, REFERENCE_CONSTANT_NOTE),
             "\\label{tab:text-agreement}\n",
             "\\begin{tabular}{L{0.13}L{0.13}L{0.06}L{0.06}L{0.09}L{0.13}L{0.13}L{0.13}L{0.13}}\n",
             "\\toprule\n",
             "Cell & Measure & " + AGREEMENT_HEAD,
             "\\midrule\n"]
    for cell in cells:
        lines += _agreement_rows(cell, PRIMARY_SCOPE, cell_name(cell.label))
    lines += ["\\bottomrule\n", "\\end{tabular}\n", "}\n", "\\end{sidewaystable}\n"]
    return "".join(lines)


def text_agreement_supplement(cells):
    """Supplement: per-text agreement over all trials, the values pooled over speeds and the mixtures of speeds in
    both scopes, and the per-trial differences per scenario and speed in the primary scope."""
    lines = [GENERATED_NOTE, "\\section*{Per-text agreement and per-trial differences}\n\\label{sec:supp-text}\n\n",
             "Generated from the result files of question 3. The values pooled over speeds are given for comparison "
             "only, because a difference in level between speeds inflates a pooled ICC. The mixtures weight the speeds "
             "(slow, mid, fast) as stated. The per-trial differences (observation minus reference) are given per "
             "scenario and speed with the mean on each side, so that the scenarios can be weighted differently; the "
             "views that leave out the trials of the carry layers need the layers of the prediction and are not "
             "computed for the Windows cell.\n\n"]
    for cell in cells:
        name = cell_name(cell.label)
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.14}L{0.11}L{0.06}L{0.12}L{0.14}L{0.14}L{0.14}L{0.14}}\n",
                  "\\caption{Per-text agreement pooled over speeds and the bias in three mixtures of speeds (weights "
                  "of slow, mid and fast), cell %s; %s}\\label{tab:text-pooled-%s}\\\\\n" % (
                      name, REFERENCE_CONSTANT_NOTE, cell.label),
                  "\\toprule\n",
                  "Measure & Scope & Texts & ICC(A,1), pooled & Bias, pooled & "
                  + " & ".join("Bias, %s" % w for _, w, _ in L2_MIXTURES) + " \\\\\n",
                  "\\midrule\n\\endhead\n"]
        for m, label, ms in L2_MEASURES:
            sg = lambda x, ms=ms: fmt_signed(x, ms)
            for j, scope in enumerate(SCOPES):
                e = cell.l2[scope]["agreement"][m]
                mixes = l2_mixtures(cell, scope)
                mix = [est_ci(mixes[key]["bias"][m], mixes[key]["bias_ci"][m][0], mixes[key]["bias_ci"][m][1], sg)
                       for key, _, _ in L2_MIXTURES]
                lines.append(" & ".join([label if j == 0 else "", "\\texttt{%s}" % tex_escape(scope), fmt_int(e["n"]),
                                         icc_text(e, checked_spread(cell, scope, m, None, e)),
                                         est_ci(e["bland_altman"]["bias"], e["bias_ci"][0], e["bias_ci"][1], sg)]
                                        + mix) + " \\\\\n")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
        lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                  "\\begin{longtable}{L{0.18}L{0.06}L{0.06}L{0.08}L{0.15}L{0.15}L{0.15}L{0.15}}\n",
                  "\\caption{Per-text agreement per measure and speed over all trials (\\texttt{all\\_trials}), cell "
                  "%s; Reference SD: the standard deviation of the reference's values between the texts; %s}"
                  "\\label{tab:text-all-%s}\\\\\n" % (name, REFERENCE_CONSTANT_NOTE, cell.label),
                  "\\toprule\n",
                  "Measure & " + AGREEMENT_HEAD,
                  "\\midrule\n\\endhead\n"]
        lines += _agreement_rows(cell, "all_trials")
        lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
        for view, title in L2_VIEWS:
            v = cell.l2[PRIMARY_SCOPE][view]
            if v is None:
                lines.append("Cell %s, %s: not computed for this cell (no layers of the prediction).\n\n" % (name, title))
                continue
            lines += ["\\begin{landscape}\n", "{\\footnotesize\n",
                      "\\begin{longtable}{L{0.08}L{0.06}L{0.06}" + "L{0.26}" * len(L2_TRIAL_MEASURES) + "}\n",
                      "\\caption{Per-trial differences per scenario and speed, primary scope, %s, cell %s: mean "
                      "difference with a block-bootstrap interval (mean of the observation; mean of the reference)}"
                      "\\label{tab:text-%s-%s}\\\\\n" % (title, name, view.replace("_", "-"), cell.label),
                      "\\toprule\n",
                      "Scenario & Speed & Trials & " + " & ".join(t for _, t, _ in L2_TRIAL_MEASURES) + " \\\\\n",
                      "\\midrule\n\\endhead\n"]
            for st in sorted(v["by_stratum"], key=lambda st: (scenario_order(st["stratum"]["scenario"]),
                                                             SPEEDS.index(st["stratum"]["speed"]))):
                k = stratum_key(st["stratum"])
                if st["status"] != "ok":
                    lines.append("%s & %s & %s & \\multicolumn{%d}{l}{%s} \\\\\n" % (
                        k[0], k[1], fmt_int(st["n_trials"]), len(L2_TRIAL_MEASURES),
                        "no trial" if st["status"] == "no_trial" else tex_escape(st["status"])))
                    continue
                row = [k[0], k[1], fmt_int(st["n_trials"])]
                for m, _, ms in L2_TRIAL_MEASURES:
                    e = st[m]
                    row.append("%s (%s; %s)" % (
                        est_ci(e["diff_mean"], e["diff_ci"][0], e["diff_ci"][1], lambda x: fmt_signed(x, ms)),
                        fmt_signed(e["obs_mean"], ms), fmt_signed(e["gt_mean"], ms)))
                lines.append(" & ".join(row) + " \\\\\n")
            lines += ["\\bottomrule\n", "\\end{longtable}\n", "}\n", "\\end{landscape}\n\n"]
    return "".join(lines)


# ---------------------------------------------------------------- figures

# Figures 2 to 5 are drawn by pgfplots from the coordinates written here; the styles that they name (the marks and
# colours of the speeds and of the feature sets, the intervals, the open marks, the panels) are defined in the preamble
# of main.tex. An interval is drawn from its two ends as the result file gives them, so an estimate that lies outside
# its percentile interval is drawn as such, and an interval without both ends is not drawn. A stratum that keeps fewer
# than MIN_TRIALS trials in the primary scope is drawn with an open mark, and a stratum without a value is not drawn.

FIG_SPEEDS = (("slow", -0.25), ("mid", 0.0), ("fast", 0.25))
# The six feature sets are set apart along the axis of K by these shifts, in decades.
FIG_SET_SHIFTS = (-0.1, -0.06, -0.02, 0.02, 0.06, 0.1)
FIG_NO_CELL = "No cell was measured when the figure was generated."
# The disclosure that the publisher asks for in the caption of a figure made with the help of an AI tool; the AI
# declaration of the manuscript gives the tools and the extent of their use.
FIG_AI_NOTE = (". The figure is drawn from the result files by code written and reviewed with an AI tool (Claude) and "
               "checked by its tests; see the AI declaration")
# The lower edge of the logarithmic axis of Figure 5: an accuracy or a lower end of an interval below it (zero among
# them, which a logarithmic axis cannot show) is drawn at the edge, and the caption says so.
FIG_LOG_FLOOR = 0.0005


def fig_num(x):
    """A coordinate; six significant digits are enough to draw."""
    return "%.6g" % x


def fig_points(style, points):
    """An \\addplot of marks at the points (x, y); nothing without a point, because pgfplots cannot plot an empty
    table."""
    if not points:
        return ""
    rows = "".join("%s %s\\\\" % (fig_num(x), fig_num(y)) for x, y in points)
    return "\\addplot[forget plot, %s] table[row sep=\\\\] {x y\\\\%s};\n" % (style, rows)


def fig_intervals(style, intervals):
    """An \\addplot of vertical segments from (x, lo) to (x, hi), separated by an undefined coordinate."""
    rows = "".join("%s %s\\\\%s %s\\\\%s nan\\\\" % (fig_num(x), fig_num(lo), fig_num(x), fig_num(hi), fig_num(x))
                   for x, lo, hi in intervals if lo is not None and hi is not None)
    if not rows:
        return ""
    return "\\addplot[forget plot, fig interval, %s] table[row sep=\\\\] {x y\\\\%s};\n" % (style, rows)


def fig_legend(name, entries, columns=None):
    """The options that send the legend of the first panel to a name, and its images and entries (in one row unless
    the number of columns is given)."""
    opts = "legend to name=%s, legend columns=%d" % (name, columns or len(entries))
    body = "".join("\\addlegendimage{%s}\n\\addlegendentry{%s}\n" % (style, text) for style, text in entries)
    return opts, body


def fig_wrap(label, caption, legend, body, env="figure", placement="[p]"):
    """A figure whose legend stands above its panels; without panels, a figure that says that no cell was measured.
    The sizes of the panels are given in centimetres, because the text width of a sideways figure is the text
    height."""
    if body is None:
        return GENERATED_NOTE + "\\begin{figure}[t]\n\\centering\n%s\n\\caption{%s}\n\\label{%s}\n\\end{figure}\n" % (
            FIG_NO_CELL, caption, label)
    return (GENERATED_NOTE + "\\begin{%s}%s\n\\centering\n\\pgfplotslegendfromname{%s}\\par\\medskip\n"
            "\\begin{tikzpicture}\n%s\\end{tikzpicture}\n\\caption{%s}\n\\label{%s}\n\\end{%s}\n" % (
                env, placement, legend, body, caption + FIG_AI_NOTE, label, env))


def fig_scenario_axis(keys):
    """The options of an axis of scenarios: a tick at the number of every scenario of the design, and the limits; none
    when the design has no stratum."""
    names = sorted(set(k[0] for k in keys), key=scenario_order)
    if not names:
        return ""
    nums = [scenario_order(n) for n in names]
    return "xtick={%s}, xticklabels={%s}, xmin=%s, xmax=%s" % (
        ",".join(str(n) for n in nums), ",".join(names), fig_num(nums[0] - 0.6), fig_num(nums[-1] + 0.6))


def fig_speed_marks(kept, entries, value_of, point="fig point"):
    """Per speed, the intervals and the marks of the strata of entries {(scenario, speed): entry}, at the number of
    the scenario shifted by the speed; value_of(entry) gives (value, lo, hi), with a value of None when there is none.
    A stratum that keeps fewer than MIN_TRIALS trials in the primary scope gets an open mark."""
    out = []
    for sp, shift in FIG_SPEEDS:
        full, hollow, intervals = [], [], []
        for k in sorted((k for k in entries if k[1] == sp), key=lambda k: scenario_order(k[0])):
            v, lo, hi = value_of(entries[k])
            if v is None:
                continue
            x = scenario_order(k[0]) + shift
            (full if kept.get(k, 0) >= MIN_TRIALS else hollow).append((x, v))
            intervals.append((x, lo, hi))
        out += [fig_intervals("fig %s" % sp, intervals), fig_points("fig %s, %s" % (sp, point), full),
                fig_points("fig %s, %s, fig open" % (sp, point), hollow)]
    return "".join(out)


FIG_SPEED_LEGEND = [("fig %s, fig point" % sp, sp) for sp, _ in FIG_SPEEDS] + [
    ("black, mark=*, fig point, fig open", "fewer than %d trials" % MIN_TRIALS)]


def figure_detection(cells):
    """Figure 2: recall and precision per scenario and speed with their Wilson intervals, per cell, in the primary
    comparison and scope."""
    caption = ("Recall and precision of the session rows against the reference per scenario and speed, with 95\\%% "
               "Wilson intervals, per cell, in the primary comparison (reference vs. logger) and the primary scope. "
               "Open marks: strata that keep fewer than %d trials in the scope, which the "
               "ranges given in the text leave out. A stratum with no trial or no value in the scope is not drawn; "
               "Table~\\ref{tab:detection} and the supplement give every stratum" % MIN_TRIALS)
    if not cells:
        return fig_wrap("fig:detection", caption, None, None)
    body = ["\\begin{groupplot}[group style={group size=2 by %d, horizontal sep=1.1cm, vertical sep=1.2cm}, "
            "fig panel, fig proportion, scale only axis, width=8.2cm, height=2.2cm]\n" % len(cells)]
    legend_opts, legend_body = fig_legend("fig:detection:legend", FIG_SPEED_LEGEND)
    first = True
    for cell in cells:
        kept = cell.kept_trials(PRIMARY_SCOPE)[0]
        strata = cell.detection_strata(PRIMARY_SCOPE)
        for m in ("recall", "precision"):
            opts = [fig_scenario_axis(kept), "title={%s: %s}" % (cell_name(cell.label), m)]
            if first:
                opts.append(legend_opts)
            body.append("\\nextgroupplot[%s]\n" % ", ".join(o for o in opts if o))
            if first:
                body.append(legend_body)
                first = False
            body.append(fig_speed_marks(kept, strata, lambda e, m=m: (e[m].get("value"), e[m].get("lo"),
                                                                       e[m].get("hi"))))
    body.append("\\end{groupplot}\n")
    return fig_wrap("fig:detection", caption, "fig:detection:legend", "".join(body), env="sidewaysfigure",
                    placement="")


def figure_dwell(cells):
    """Figure 3: the difference of dwell_ms from the duration of the reference session per scenario and speed (the
    bias and both limits of agreement, each with its interval), per cell, in the primary comparison and scope."""
    caption = ("Difference of \\texttt{dwell\\_ms} from the duration of the reference session per scenario and speed, "
               "per cell, in the primary comparison and scope: the bias (marks) and the lower and upper limits of "
               "agreement (dashes), each with a 95\\%% interval that resamples blocks; a negative difference means that "
               "the logger's duration was shorter. Open marks and thin dashes: strata that keep fewer than %d trials "
               "in the scope. A "
               "stratum without a matched session is not drawn, and an estimate whose interval is withheld is drawn "
               "without it; the supplement gives every stratum" % MIN_TRIALS)
    if not cells:
        return fig_wrap("fig:dwell", caption, None, None)
    body = ["\\begin{groupplot}[group style={group size=1 by %d, vertical sep=1.4cm}, fig panel, fig zero, "
            "scale only axis, width=11cm, height=3.6cm, ylabel={Difference (ms)}]\n" % len(cells)]
    legend_opts, legend_body = fig_legend("fig:dwell:legend", FIG_SPEED_LEGEND + [("black, fig limit",
                                                                                    "limit of agreement")])

    def part(key, ci_key):
        def value_of(e):
            dw = e["dwell"]
            ci = dw.get(ci_key) or [None, None]
            return dw["bland_altman"].get(key), ci[0], ci[1]
        return value_of

    first = True
    for cell in cells:
        kept = cell.kept_trials(PRIMARY_SCOPE)[0]
        entries = dict((stratum_key(st["stratum"]), st) for st in cell.l1[PRIMARY_SCOPE]["metrics"]["by_stratum"]
                       if st["stratum"].get("scenario") is not None)
        opts = [fig_scenario_axis(kept), "title={%s}" % cell_name(cell.label)]
        if first:
            opts.append(legend_opts)
        body.append("\\nextgroupplot[%s]\n" % ", ".join(o for o in opts if o))
        if first:
            body.append(legend_body)
            first = False
        body.append(fig_speed_marks(kept, entries, part("loa_lo", "loa_lo_ci"), "fig limit"))
        body.append(fig_speed_marks(kept, entries, part("loa_hi", "loa_hi_ci"), "fig limit"))
        body.append(fig_speed_marks(kept, entries, part("bias", "bias_ci")))
    body.append("\\end{groupplot}\n")
    return fig_wrap("fig:dwell", caption, "fig:dwell:legend", "".join(body))


def figure_text(cells):
    """Figure 4: the per-text bias and limits of agreement of the four measures per cell and speed, each with its
    interval, in the primary scope."""
    caption = ("Per-text agreement of the four measures per cell and speed in the primary scope: the bias (marks) and "
               "the lower and upper limits of agreement (dashes) of the per-text difference (observation minus "
               "reference), each with a 95\\% interval that resamples blocks. The values hold for this design's numbers "
               "of trials per text and are not compared between speeds, and the values of the Windows cell estimate the "
               "agreement with a different construct and are not compared with those of the macOS cells; a measure "
               "with no text in the scope is not drawn. Table~\\ref{tab:text-agreement} gives the values with ICC(A,1)")
    if not cells:
        return fig_wrap("fig:text-agreement", caption, None, None)
    ticks = "xtick={%s}, xticklabels={%s}, xmin=0.5, xmax=%s" % (
        ",".join(str(i) for i in range(1, len(cells) + 1)),
        ",".join("{%s}" % cell_name(c.label) for c in cells), fig_num(len(cells) + 0.5))
    body = ["\\begin{groupplot}[group style={group size=1 by %d, vertical sep=1.4cm}, fig panel, fig zero, "
            "scale only axis, width=11cm, height=2.5cm, %s]\n" % (len(L2_MEASURES), ticks)]
    legend_opts, legend_body = fig_legend("fig:text-agreement:legend",
                                          [("fig %s, fig point" % sp, sp) for sp, _ in FIG_SPEEDS]
                                          + [("black, fig limit", "limit of agreement")])
    for j, (m, label, _) in enumerate(L2_MEASURES):
        opts = ["title={%s}" % label]
        if j == 0:
            opts.append(legend_opts)
        body.append("\\nextgroupplot[%s]\n" % ", ".join(o for o in opts if o))
        if j == 0:
            body.append(legend_body)
        for sp, shift in FIG_SPEEDS:
            marks = {"bias": [], "loa_lo": [], "loa_hi": []}
            intervals = []
            for i, cell in enumerate(cells, 1):
                e = (cell.l2[PRIMARY_SCOPE]["agreement_by_stratum"].get(sp) or {}).get(m)
                if e is None:
                    continue
                x = i + shift
                for key in marks:
                    v = e["bland_altman"].get(key)
                    if v is None:
                        continue
                    ci = e.get(key + "_ci") or [None, None]
                    marks[key].append((x, v))
                    intervals.append((x, ci[0], ci[1]))
            body += [fig_intervals("fig %s" % sp, intervals),
                     fig_points("fig %s, fig limit" % sp, marks["loa_lo"] + marks["loa_hi"]),
                     fig_points("fig %s, fig point" % sp, marks["bias"])]
    body.append("\\end{groupplot}\n")
    return fig_wrap("fig:text-agreement", caption, "fig:text-agreement:legend", "".join(body))


def figure_closed_set(cells, docs):
    """Figure 5: the closed-set accuracy against K for the six feature sets, with the chance level and the margin of
    the near-chance rule, per cell."""
    caption = ("Closed-set identification accuracy against the number of candidates $K$ for the six feature sets, per "
               "cell, with 95\\% Wilson intervals; the accuracy of a set is the maximum over the set and the sets "
               "derivable from it. Solid line: the chance level $1/K$; dashed line: the margin $2/K$ of the near-chance "
               "rule, which judges \\texttt{no\\_time} and \\texttt{counts\\_only} at $K = 10$ and $100$; the sets that "
               "keep time are estimates only. The sets are set apart along $K$ for legibility; every point lies at "
               "$K = 10$, $100$ or $1{,}000$. Both axes are logarithmic. Table~\\ref{tab:closed-set} gives the "
               "judgement")
    measured = [c for c in cells if docs.get(c.label) is not None]
    if not measured:
        return fig_wrap("fig:closed-set", caption, None, None)
    body = ["\\begin{groupplot}[group style={group size=%d by 1, horizontal sep=0.45cm, yticklabels at=edge left, "
            "ylabels at=edge left}, fig panel, xmode=log, ymode=log, "
            "xtick={10,100,1000}, xmin=6, xmax=1700, ymin=%s, ymax=1.5, scale only axis, width=3.3cm, height=4.5cm, "
            "xlabel={$K$}, ylabel={Accuracy}]\n" % (len(measured), fig_num(FIG_LOG_FLOOR))]
    legend_opts, legend_body = fig_legend(
        "fig:closed-set:legend", [("fig set%d, fig point" % (j + 1), "\\texttt{%s}" % tex_escape(f))
                                  for j, f in enumerate(CLOSED_SET_SETS)]
        + [("fig chance", "$1/K$"), ("fig margin", "$2/K$")], columns=4)
    clamped = False
    for i, cell in enumerate(measured):
        doc = docs[cell.label]
        opts = ["title={%s}" % cell_name(cell.label)]
        if i == 0:
            opts.append(legend_opts)
        body.append("\\nextgroupplot[%s]\n" % ", ".join(o for o in opts if o))
        if i == 0:
            body.append(legend_body)
        body.append("\\addplot[forget plot, fig chance, domain=7:1500] {1/x};\n")
        body.append("\\addplot[forget plot, fig margin, domain=7:1500] {2/x};\n")
        for j, (f, shift) in enumerate(zip(CLOSED_SET_SETS, FIG_SET_SHIFTS)):
            points, intervals = [], []
            for K in CLOSED_SET_KS:
                r = doc["results"][str(K)][f]
                if r.get("acc") is None:
                    continue
                x = K * 10 ** shift
                # Both ends are drawn at the edge when they lie below it, so an interval below the edge stays in order.
                ends = [None if e is None else max(e, FIG_LOG_FLOOR) for e in r["acc_ci"]]
                clamped = clamped or any(v is not None and v < FIG_LOG_FLOOR for v in [r["acc"]] + list(r["acc_ci"]))
                points.append((x, max(r["acc"], FIG_LOG_FLOOR)))
                intervals.append((x, ends[0], ends[1]))
            body += [fig_intervals("fig set%d" % (j + 1), intervals), fig_points("fig set%d, fig point" % (j + 1),
                                                                                  points)]
    body.append("\\end{groupplot}\n")
    if clamped:
        caption += ("; an accuracy or a lower end of an interval below %s is drawn at the lower edge of the axis" %
                    fig_num(FIG_LOG_FLOOR))
    return fig_wrap("fig:closed-set", caption, "fig:closed-set:legend", "".join(body), placement="[t]")


def values_tex(values):
    lines = [GENERATED_NOTE, "% Read with \\rv{<key>}; the source of each value is in values.json.\n"]
    for k in sorted(values):
        lines.append("\\expandafter\\def\\csname rv:%s\\endcsname{%s}\n" % (k, values[k]["text"]))
    return "".join(lines)


def build(results_dir):
    """{file name: bytes} of every generated file."""
    found = find_cells(results_dir)
    cells = [Cell(*f) for f in found]
    docs = dict((c.label, read_closed_set(c)) for c in cells)
    open_docs = dict((c.label, read_open_set(c)) for c in cells)
    e3 = read_e3_all(results_dir)
    d10 = dict((c.label, read_d10(results_dir, c)) for c in cells)
    clocks = dict((c.label, read_clock_sync(c)) for c in cells)
    dwellx = dict((c.label, read_dwell_explore(results_dir, c)) for c in cells)
    values = {}
    for cell in cells:
        values.update(abstract_values(cell))
        values.update(comparison_values(cell))
        values.update(reference_values(cell, clocks[cell.label]))
        values.update(timing_values(cell))
        values.update(diagnostic_values(cell))
        values.update(definition_values(cell))
        values.update(decomposition_values(cell))
        values.update(d10_values(cell, d10[cell.label]))
        values.update(l2_values(cell))
        values.update(dwell_explore_values(cell, dwellx[cell.label]))
        values.update(open_set_values(cell, open_docs[cell.label]))
    values.update(l2_constant_values(cells))
    values.update(closed_set_values(cells, docs))
    values.update(e3_values(e3))
    files = {
        "values.json": (json.dumps({"min_trials": MIN_TRIALS, "cells": [c.label for c in cells], "values": values},
                                   ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"),
        "values.tex": values_tex(values).encode("utf-8"),
        "table-detection.tex": table_detection(cells).encode("utf-8"),
        "table-session.tex": table_session(cells).encode("utf-8"),
        "table-closed-set.tex": table_closed_set(cells, docs).encode("utf-8"),
        "table-open-set.tex": table_open_set(cells, open_docs).encode("utf-8"),
        "table-cost.tex": table_cost(e3).encode("utf-8"),
        "closed-set-all.tex": table_closed_set_all(cells, docs).encode("utf-8"),
        "table-text-agreement.tex": table_text_agreement(cells).encode("utf-8"),
        "text-agreement.tex": text_agreement_supplement(cells).encode("utf-8"),
        "table-reference.tex": table_reference(cells, clocks).encode("utf-8"),
        "table-key-timing.tex": table_key_timing(cells, "observation", "tab:key-timing").encode("utf-8"),
        "reference-strata.tex": reference_supplement(cells, clocks).encode("utf-8"),
        "detection-layers.tex": table_layers(cells, d10).encode("utf-8"),
        "session-strata.tex": session_supplement(cells).encode("utf-8"),
        "dwell-explore.tex": dwell_explore_supplement(cells, dwellx).encode("utf-8"),
        "detection-definitions.tex": definitions_supplement(cells).encode("utf-8"),
        "fig-detection.tex": figure_detection(cells).encode("utf-8"),
        "fig-dwell.tex": figure_dwell(cells).encode("utf-8"),
        "fig-text-agreement.tex": figure_text(cells).encode("utf-8"),
        "fig-closed-set.tex": figure_closed_set(cells, docs).encode("utf-8"),
    }
    strata = [GENERATED_NOTE, "\\section*{Detection per stratum}\n\\label{sec:supp-strata}\n\n",
              "The tables below are generated from the result files of the analysis. The cells whose result files were "
              "not available when the tables were generated are: %s.\n\n" % (
                  ", ".join(cell_name(c) for c in EXPECTED_CELLS if c not in [x.label for x in cells]) or "none")]
    for cell in cells:
        strata.append(table_composition(cell))
        strata.append(table_strata(cell, d10[cell.label]).replace(GENERATED_NOTE, ""))
    files["detection-strata.tex"] = "".join(strata).encode("utf-8")
    return files


def write_or_check(files, out_dir, check):
    if check:
        differ = []
        # A file that the script no longer writes but that is still in the output directory could still be read by
        # the manuscript.
        if os.path.isdir(out_dir):
            differ += ["%s (no longer generated)" % n for n in sorted(os.listdir(out_dir))
                       if n not in files and os.path.isfile(os.path.join(out_dir, n))]
        for name, data in sorted(files.items()):
            p = os.path.join(out_dir, name)
            if not os.path.isfile(p):
                differ.append(name + " (missing)")
                continue
            with open(p, "rb") as f:
                if f.read() != data:
                    differ.append(name)
        if differ:
            print("results_tables.py: out of date with the result files: %s" % ", ".join(differ), file=sys.stderr)
            return 1
        print("results_tables.py: %d files up to date" % len(files))
        return 0
    os.makedirs(out_dir, exist_ok=True)
    for name, data in sorted(files.items()):
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
    print("results_tables.py: wrote %d files to %s" % (len(files), out_dir))
    return 0


# ---------------------------------------------------------------- selftest


def _stratum(scenario, speed, n_gt, x, lo, hi):
    e = lambda: {"value": float(x) / n_gt if n_gt else None, "lo": lo, "hi": hi, "n": n_gt, "x": x}
    zero = {"value": 0.0, "lo": 0.0, "hi": 0.1, "n": n_gt, "x": 0}
    return {"stratum": {"cell": "c", "scenario": scenario, "speed": speed}, "n_gt": n_gt, "n_obs": n_gt,
            "recall": e(), "precision": e(), "split_rate": zero, "merge_rate": zero}


def _agreement(bias, ms):
    """A per-text agreement entry with distinct lower and upper limits (so that swapping them shows)."""
    lo, hi = (bias - 900.0, bias + 700.0) if ms else (bias - 0.25, bias + 0.125)
    return {"n": 40, "bland_altman": {"bias": bias, "loa_lo": lo, "loa_hi": hi, "n": 40, "sd": 1.0},
            "bias_ci": [bias - 1.0, bias + 1.0] if ms else [bias - 0.01, bias + 0.01], "bias_ci_status": "ok",
            "loa_lo_ci": [lo - 1.0, lo + 1.0] if ms else [lo - 0.01, lo + 0.01], "loa_lo_ci_status": "ok",
            "loa_hi_ci": [hi - 1.0, hi + 1.0] if ms else [hi - 0.01, hi + 0.01], "loa_hi_ci_status": "ok",
            "icc21": 0.75, "icc21_ci": [0.5, 0.875], "icc21_ci_status": "ok"}


def _oracle_report():
    """A plan-versus-ground-truth report whose types have distinct counts, so that a mix-up of types shows."""
    counts = {"no_session": 4, "unexpected_session": 1, "multiple_sessions": 2, "expected_cancel_got_commit": 0,
              "expected_commit_got_cancel": 3, "open_at_end": 0, "unknown_trial": 0}

    def rate(x, n):
        return {"n": x, "rate": float(x) / n, "ci": [max(0.0, float(x) / n - 0.004), float(x) / n + 0.02]}
    strata = []
    for sc in ("S1", "S2"):
        for sp in SPEEDS:
            by = {"no_session": rate(2, 75), "expected_commit_got_cancel": rate(1, 75)} if (sc, sp) == ("S1", "fast") \
                else {}
            n_dis = sum(e["n"] for e in by.values())
            strata.append({"stratum": {"cell": "c", "scenario": sc, "speed": sp}, "n_trials": 75, "n_disagree": n_dis,
                           "disagree_rate": n_dis / 75.0, "disagree_ci": [0.0, n_dis / 75.0 + 0.05], "by_type": by})
    return {"plan_available": True, "n_agree": 440, "unassigned_sessions": 2, "removed_from_evaluation": False,
            "n_trials": 450, "n_disagree": 10, "disagree_rate": 10 / 450.0, "disagree_ci": [0.012, 0.04],
            "by_type": dict((t, rate(x, 450)) for t, x in counts.items()), "by_stratum": strata}


def _summary(base):
    """A KT1 summary: integer minimum and maximum, the other figures with decimals (distinct per base)."""
    return {"n": 100, "mean": base + 0.3, "min": int(base) - 1, "p05": base - 0.5, "median": base, "p95": base + 2.0,
            "max": int(base) + 7}


def _delivery():
    """A delivery report whose two sides, two versions and speeds differ, so that a mix-up shows."""
    def reach(reached, sent, outside, loss, excess_rows, excess_trials):
        return {"reached": reached, "sent_presses": sent, "reach_rate": float(reached) / sent,
                "excess_rows": excess_rows, "trials_with_excess": excess_trials, "rows_outside_trials": outside,
                "trials_with_loss": loss}

    def timing(d0, i0):
        return {"delay_ms": {"overall": _summary(d0), "by_speed": dict(
                    (sp, _summary(d0 + 10 * (k + 1))) for k, sp in enumerate(SPEEDS))},
                "interval_error_ms": {"overall": _summary(i0), "by_speed": dict(
                    (sp, _summary(i0 - 10 * (k + 1))) for k, sp in enumerate(SPEEDS))},
                "n_trials_paired": 440, "n_trials_not_paired": 10}
    late = {"n": 1000, "mean": 0.012, "min": 0.001, "p05": 0.002, "median": 0.011, "p95": 0.034, "max": 0.567}
    focus = {"n_focus_in": 4, "n_focus_out": 4, "n_loss_intervals": 4, "n_loss_intervals_outside_trials": 0,
             "n_loss_intervals_planned": 3, "n_loss_intervals_unplanned": 1, "n_loss_intervals_unplanned_with_presses": 1,
             "n_loss_spans": 6, "n_loss_spans_with_unexplained_presses": 0, "n_presses_in_unplanned_losses": 2,
             "n_presses_unexplained_in_losses": 0, "n_trials_with_focus_loss": 2, "trials_with_focus_loss": ["t7", "t8"],
             "by_stratum": [{"scenario": sc, "speed": sp, "n_trials": 75,
                             "n_trials_with_focus_loss": 2 if (sc, sp) == ("S1", "mid") else 0}
                            for sc in ("S1", "S2") for sp in SPEEDS]}
    return {"presses": {"all": 1000, "excluding_mode_keys": 990},
            "trials": {"planned": 450, "begun": 450, "measured": 450, "incomplete": 0},
            "send_lateness_ms": {"overall": late, "by_speed": dict((sp, late) for sp in SPEEDS)},
            "observation": {"reach": {"all_presses": reach(990, 1000, 8, 10, 0, 0),
                                      "excluding_mode_keys": reach(990, 990, 8, 0, 0, 0)},
                            "timing": timing(3.0, -0.5)},
            "reference": {"reach": {"all_presses": reach(1000, 1000, 9, 0, 1, 1),
                                    "excluding_mode_keys": reach(990, 990, 9, 0, 11, 6)},
                          "timing": timing(5.0, 1.5)},
            "reference_focus_loss": focus}


def _clock_doc():
    """Marker times: three blocks, the observation's marker missing in block 2."""
    diffs = {}
    for i, (k, _) in enumerate(CLOCK_DIFFS):
        mono = k.endswith("mono")
        blocks = (0, 1) if k.startswith("obs") else (0, 1, 2)
        pb = [{"block_id": b, "diff_ms": (i + b + 0.5) if mono else (i + b)} for b in blocks]
        vs = [p["diff_ms"] for p in pb]
        diffs[k] = {"n": len(vs), "median_ms": sorted(vs)[len(vs) // 2], "min_ms": min(vs), "max_ms": max(vs),
                    "offset_at_start_ms": float(i), "drift_ms_per_hour": 10.0 * (i + 1) + 0.25, "residual_max_ms": 0.5,
                    "per_block": pb}
    return {"params": {"window_ms": 3000}, "oracle": {"blocks": 3, "with_marker": [0, 1, 2], "missing": []},
            "differences": diffs, "gt": {"occurrences": 3, "matched": [0, 1, 2], "missing": [], "unmatched_occurrences": 0},
            "obs": {"occurrences": 2, "matched": [0, 1], "missing": [2], "unmatched_occurrences": 0}}


def _layers(shift, novalue=False, drop=None):
    """Layers of the prediction with distinct values per layer; novalue puts a rate of deviation D10 (numerator above
    the denominator) into received carry; drop removes one speed of one layer."""
    out = {}
    for i, (name, _) in enumerate(LAYERS):
        def ent(stratum, n, x):
            e = _stratum("S1", "fast", n, x, 0.30 + 0.01 * i + shift, 0.70 + shift)
            e["stratum"] = stratum
            return e
        by = [ent({"speed": sp}, 40, 20 + i) for sp in SPEEDS if (name, sp) != drop]
        if novalue and name == "received_carry":
            by[0]["merge_rate"] = {"value": None, "lo": None, "hi": None, "n": 2, "x": 3,
                                   "note": "numerator above the denominator"}
        if novalue and name == "closing_key_in_reading":  # no value because there is no denominator: not D10
            by[1]["merge_rate"] = {"value": None, "lo": None, "hi": None, "n": 0, "x": 0}
        out[name] = {"n_trials": 10 + i, "pooled": ent("layer", 120, 60 + i), "by_speed": by}
    return out


def _fixture(tmp, label="windows-test", sub="windows"):
    """A synthetic cell (Windows under windows/, macOS at the top with the comparisons with the prediction and its
    layers): S1 keeps 75, 30 and 29 trials (mid is at the minimum, slow under it), S2 keeps 0."""
    d = os.path.join(tmp, sub) if sub else tmp
    os.makedirs(d, exist_ok=True)
    prim = {"comparison": PRIMARY_COMPARISON, "scope": PRIMARY_SCOPE}
    defn = "windows_def" if sub else "macos_def"
    focus_u = {"n_trials": 2, "n_rows": 3, "n_false_pos": 1, "by_speed": {"mid": {"n_trials": 2, "n_rows": 3,
                                                                                    "n_false_pos": 1}}}
    focus_a = {"n_trials": 2, "n_rows": 5, "n_false_pos": 2, "by_speed": {"mid": {"n_trials": 2, "n_rows": 5,
                                                                                    "n_false_pos": 2}}}

    def pooled(r):
        """Pooled detection measures around r, distinct per comparison and scope."""
        e = _stratum("S1", "fast", 200, int(200 * r), r - 0.05, r + 0.05)
        e["stratum"] = "all"
        for m in ("miss_rate", "false_pos_rate"):
            e[m] = {"value": 1 - r, "lo": 1 - r - 0.05, "hi": 1 - r + 0.05, "n": 200, "x": 200 - int(200 * r)}
        e["f1"] = {"value": r, "lo": None, "hi": None, "n": 200, "x": int(200 * r), "note": "no interval"}
        return e

    def det_scope(strata, carry, focus=None, layers=None, r=0.9):
        return {"metrics": {"by_stratum": strata + [{"stratum": {"cell": "c", "scenario": None, "speed": None},
                                                      "n_gt": 0, "n_obs": 3}], "pooled": pooled(r)},
                "excluded": {"trials": {"by_type": {"carry_over": 1}, "n": 1},
                             "gt": {"by_reason": {"carry_over": 2}, "n": 2},
                             "obs": {"by_reason": {"carry_over": 1}, "n": 1}},
                "config": {"theta": 0.5}, "focus_loss": focus or focus_u,
                "diagnostics": {"prediction_layers": layers, "start_while_open": {"n": 3 + carry, "by_stratum": []}}}

    det_u = [_stratum("S1", "fast", 75, 70, 0.85, 0.97), _stratum("S1", "mid", 30, 22, 0.60, 0.86),
             _stratum("S1", "slow", 29, 20, 0.10, 0.99)]
    det_a = [_stratum("S1", "fast", 75, 70, 0.84, 0.96), _stratum("S1", "mid", 75, 60, 0.70, 0.88),
             _stratum("S1", "slow", 75, 50, 0.55, 0.77), _stratum("S2", "fast", 75, 75, 0.95, 1.0),
             _stratum("S2", "mid", 75, 75, 0.95, 1.0), _stratum("S2", "slow", 75, 0, 0.0, 0.049)]
    # S2 slow over all trials: no session row, so precision and the merge rate have no value; recall is 0.
    none = {"value": None, "lo": None, "hi": None, "n": 0, "x": 0}
    det_a[-1]["precision"], det_a[-1]["merge_rate"], det_a[-1]["n_obs"] = dict(none), dict(none), 0

    def l2_scope(kept, oos, excl):
        bs = []
        diff = {"diff_mean": -0.5, "diff_ci": [-0.75, -0.25], "diff_ci_status": "ok", "diff_sd": 1.0, "gt_mean": 2.0,
                "obs_mean": 1.5}
        for (sc, sp), n in kept.items():
            st = {"stratum": {"scenario": sc, "speed": sp}, "n_trials": n, "status": "ok" if n else "no_trial",
                  "trials_not_used": {"excluded": excl.get((sc, sp), 0), "not_run": 0,
                                      "out_of_scope": oos.get((sc, sp), 0)}}
            if n:
                st.update({"n_conversions": dict(diff), "dwell_sum_ms": dict(diff, diff_mean=-1234.4,
                                                                             diff_ci=[-1500.0, -900.0]),
                           "n_cancelled": dict(diff)})
            bs.append(st)
        agr = dict((m, _agreement(-1234.4 if ms else -0.0125, ms)) for m, _, ms in L2_MEASURES)
        mixes = dict((name, {"weights": dict(zip(SPEEDS, w)), "bias": dict((m, -0.01) for m, _, _ in L2_MEASURES),
                             "bias_ci": dict((m, [-0.02, 0.0]) for m, _, _ in L2_MEASURES),
                             "bias_ci_status": dict((m, "ok") for m, _, _ in L2_MEASURES)})
                     for name, _, w in L2_MIXTURES)
        by_speed = dict((s, dict(agr)) for s in ("mid", "fast"))
        # slow: the interval of the ICC withheld, and no text for the abandonment rate
        slow = dict(agr)
        slow["conversions_per_trial"] = dict(agr["conversions_per_trial"], icc21_ci=[None, None],
                                             icc21_ci_status="undefined in a resample")
        del slow["abandon_rate"]
        by_speed["slow"] = slow
        # The texts behind the agreement entries: 40 per speed. The reference's conversions per trial are the same in
        # every text at the mid speed (so its ICC has no value), and the slow texts have no abandonment rate.
        agg = {"gt": [], "obs": []}
        for s in SPEEDS:
            for k in range(40):
                t = {"text_id": "b%d.%s" % (k, s), "block_id": k, "speed": s}
                g = dict(t, conversions_per_trial=1.0 if s == "mid" else 1.0 + 0.01 * k,
                         mean_dwell_ms=1000.0 + 10.0 * k, abandon_rate=None if s == "slow" else 0.1 + 0.001 * k,
                         dwell_fraction=0.2 + 0.001 * k)
                o = dict(t, conversions_per_trial=1.0 + 0.02 * (k % 3), mean_dwell_ms=990.0 + 10.0 * k,
                         abandon_rate=0.1, dwell_fraction=0.19 + 0.001 * k)
                agg["gt"].append(g)
                agg["obs"].append(o)
        pooled_n = dict((m, sum(1 for g, o in zip(agg["gt"], agg["obs"]) if g[m] is not None and o[m] is not None))
                        for m, _, _ in L2_MEASURES)
        pooled = dict((m, dict(agr[m], n=pooled_n[m])) for m, _, _ in L2_MEASURES)
        return {"per_trial": {"by_stratum": bs, "n_trials": sum(kept.values()),
                              "trials_not_used": {"excluded": sum(excl.values()), "not_run": 0,
                                                  "out_of_scope": sum(oos.values())}},
                "per_trial_uncontaminated": None, "per_trial_layer_none": None,
                "agreement": pooled, "agreement_by_stratum": by_speed, "aggregate": agg,
                "mixture_bias": {"mixtures": mixes, "by_stratum": {}}}

    keys = [("S1", "fast"), ("S1", "mid"), ("S1", "slow"), ("S2", "fast"), ("S2", "mid"), ("S2", "slow")]
    excl = {("S1", "mid"): 1}
    kept_u = {("S1", "fast"): 75, ("S1", "mid"): 30, ("S1", "slow"): 29, ("S2", "fast"): 0, ("S2", "mid"): 0,
              ("S2", "slow"): 0}
    oos_u = {("S1", "mid"): 44, ("S1", "slow"): 46, ("S2", "fast"): 75, ("S2", "mid"): 75, ("S2", "slow"): 75}
    kept_a = dict((k, 75) for k in keys)
    kept_a[("S1", "mid")] = 74

    def l1_entry(stratum, b, lo, hi, mae=300.0):
        """One entry of question 2, with intervals on the bias, both limits and the median absolute difference."""
        return {"stratum": stratum,
                "dwell": {"bias_ci": [lo, hi], "bland_altman": {"bias": b, "loa_lo": b - 900, "loa_hi": b + 900,
                                                                  "n": 50, "sd": 450},
                          "loa_lo_ci": [b - 950, b - 850], "loa_hi_ci": [b + 850, b + 950], "mae": mae,
                          "mae_ci": [250.0, 350.0], "n": 50},
                "abandon": {"agree": 0.9, "agree_ci": [0.8, 0.95], "kappa": None, "kappa_ci": [None, None]},
                "nav": {"exact": 1.0, "exact_ci": [0.93, 1.0], "n": 50},
                "abandon_by_continued_commit": {
                    "with": {"agree": 0.75, "agree_ci": [0.6, 0.85], "kappa": None, "kappa_ci": [None, None], "n": 20},
                    "without": {"agree": 0.95, "agree_ci": [0.9, 0.98], "kappa": None, "kappa_ci": [None, None],
                                "n": 30},
                    "n_unknown": 1}}

    def l1_scope(biases, zero=0, maes=None):
        """maes: the median absolute difference per speed (300 at every speed when absent)."""
        by_speed = [l1_entry({"cell": "c", "speed": sp}, b, lo, hi, (maes or {}).get(sp, 300.0))
                    for sp, (b, lo, hi) in biases.items()]
        strata = [l1_entry({"cell": "c", "scenario": "S1", "speed": sp}, b + 1.0, lo, hi)
                  for sp, (b, lo, hi) in biases.items()]
        return {"metrics": {"by_speed": by_speed, "by_stratum": strata,
                            "pooled": l1_entry("all", -400.0 - zero, -450.0, -350.0)},
                "oracle": _oracle_report(),
                "zero_dwell_rows": {"n": 2 + zero, "in_pairs": 0, "in_split": 0, "in_merge": 0, "in_false_pos": 2 + zero,
                                    "by_stratum": []},
                "truncation": {"limit_ms": 20000, "n_long": 4 + zero, "n_long_with_row": 1, "n_long_excluded": 0,
                               "n_obs_over_limit": 2 + zero, "n_obs_over_limit_false_pos": 1, "by_stratum": [],
                               "with_row_gt_dwell_ms": []}}

    cells_det = {defn: {PRIMARY_COMPARISON: {"under_limit": det_scope(det_u, 1),
                                             "all_trials": det_scope(det_a, 1, focus_a, r=0.8)}}}
    l1_u = {"slow": (-900.4, -1050.6, -800.2), "mid": (-500.0, -600.0, -400.0), "fast": (-100.0, -150.0, -49.5)}
    l1_a = {"slow": (-2000.0, -2500.0, -1500.0), "mid": (-500.0, -600.0, -400.0), "fast": (-100.0, -150.0, -50.0)}
    cells_l1 = {defn: {PRIMARY_COMPARISON: {"under_limit": l1_scope(l1_u), "all_trials": l1_scope(l1_a, 5)}}}
    top = {"same_sessions_as_all": {label: not sub}}
    if not sub:
        # macOS: the primary comparison also under over_limit, the comparisons with the prediction with other
        # intervals, and the layers of the prediction in every comparison and scope.
        over = [_stratum("S1", "slow", 46, 10, 0.1, 0.4)]
        pred = {"gt_pred": ([_stratum("S1", "fast", 75, 60, 0.70, 0.80), _stratum("S1", "mid", 30, 25, 0.50, 0.95),
                             _stratum("S1", "slow", 29, 20, 0.20, 0.30)], 0.1),
                "pred_obs": ([_stratum("S1", "fast", 75, 74, 0.88, 0.99), _stratum("S1", "mid", 30, 29, 0.81, 0.99),
                              _stratum("S1", "slow", 29, 28, 0.80, 0.99)], 0.2)}
        prim_blk = cells_det[defn][PRIMARY_COMPARISON]
        prim_blk["under_limit"]["diagnostics"]["prediction_layers"] = _layers(0.0, novalue=True)
        prim_blk["all_trials"]["diagnostics"]["prediction_layers"] = _layers(0.01)
        prim_blk["over_limit"] = det_scope(over, 0, None, _layers(0.02), r=0.3)
        for comp, (strata_u, shift) in pred.items():
            cells_det[defn][comp] = {
                "under_limit": det_scope(strata_u, 1, None, _layers(shift), r=0.7 + shift),
                "all_trials": det_scope([dict(s, stratum=dict(s["stratum"])) for s in det_a], 1, focus_a,
                                        _layers(shift + 0.01), r=0.6 + shift),
                "over_limit": det_scope(over, 0, None, _layers(shift + 0.02, drop=("none", "fast")
                                                               if comp == "pred_obs" else None), r=0.2 + shift)}
            # Medians of the absolute differences that differ per comparison and speed (20 to 40 ms for gt_pred and
            # 30 to 50 ms for pred_obs; 300 ms at every speed for gt_obs), so that a mix-up of either shows.
            maes = dict((s, x + 100 * shift) for s, x in (("slow", 10.0), ("mid", 30.0), ("fast", 20.0)))
            cells_l1[defn][comp] = {"under_limit": l1_scope(dict((s, (b + 10 * shift, lo, hi))
                                                                  for s, (b, lo, hi) in l1_u.items()), maes=maes),
                                    "all_trials": l1_scope(l1_a, 7)}
        top["predictions"] = {label: {
            "available": True, "counts": {"open": 1, "row": 300, "truncated": 4},
            "trials": {"n": 450, "n_long": 6, "n_carried_out": 3, "n_received_carry": 3, "n_closing_key_in_reading": 9,
                       "n_none": 430, "n_split": 2, "by_stratum": []},
            "composition": [{"scenario": sc, "speed": sp, "scope": scope, "n_trials": 75,
                             "letter_keys": {"n": 75, "min": 4, "median": 10 + i, "max": 20},
                             "think_ms": {"n": 75, "min": 200, "median": 1500 + 100 * i, "max": 19000}}
                            for sc in ("S1", "S2") for sp in SPEEDS
                            for i, scope in enumerate(("all_trials", "over_limit", "under_limit"))]}}
    else:
        cells_det[defn][PRIMARY_COMPARISON]["over_limit"] = det_scope([_stratum("S1", "slow", 46, 10, 0.1, 0.4)], 0,
                                                                      r=0.3)
        cells_det[defn][PRIMARY_COMPARISON]["under_limit_gt_duration"] = det_scope(det_u, 1, r=0.85)
        top["predictions"] = {label: {"available": False, "reason": "no prediction for this system"}}
        top["windows_diagnostics"] = {label: {"observed_rows_dwell_ms": {"20000_to_24999": 3, "25000_or_more": 1},
                                              "s4_session_end_after_escape": {"2": 5, "3": 70},
                                              "tsf_end_without_result_len": {"S10": 4, "S2": 1}}}
        top["long_trials_from"] = {label: {"band_ms": 1000, "limit_ms": 20000, "by_stratum": {},
                                           "total": {"n_band": 6, "n_differ": 2, "n_long": 617, "n_long_gt": 615,
                                                     "n_trials": 2925}}}
    # The definition common to both systems: on macOS the same sessions (a copy); on Windows 10 more reference sessions
    # in the primary scope and 12 more over all trials, with other measures, so that a mix-up of the two shows.
    if sub:
        # In the primary scope its strata have other intervals than those of the analysis (S1 slow stays under the
        # minimum of trials), so that reading the wrong block shows in the ranges.
        common_u = [_stratum("S1", "fast", 75, 68, 0.80, 0.95), _stratum("S1", "mid", 30, 21, 0.55, 0.85),
                    _stratum("S1", "slow", 29, 20, 0.05, 0.99)]
        # Over all trials two strata (S1 mid and slow) differ from the analysis's, in their estimates and intervals; the
        # lower end of S1 slow moves the range of precision (recall starts at zero in S2 slow under both).
        common_a = [dict(s, stratum=dict(s["stratum"])) for s in det_a]
        common_a[1] = _stratum("S1", "mid", 75, 54, 0.62, 0.82)
        common_a[2] = _stratum("S1", "slow", 75, 45, 0.50, 0.70)
        # S2 slow has a value of precision under the common definition only (three rows, none paired), so it enters
        # that range alone and moves its lower end to zero.
        common_a[5] = dict(common_a[5], n_obs=3, precision={"value": 0.0, "lo": 0.0, "hi": 0.2, "n": 3, "x": 0})
        cells_det[COMMON_DEFINITION] = {PRIMARY_COMPARISON: {"under_limit": det_scope(common_u, 1, r=0.86),
                                                             "all_trials": det_scope(common_a, 1, focus_a, r=0.76)}}
        for scope, n in (("under_limit", 210), ("all_trials", 212)):
            cells_det[COMMON_DEFINITION][PRIMARY_COMPARISON][scope]["metrics"]["pooled"]["n_gt"] = n
    else:
        cells_det[COMMON_DEFINITION] = json.loads(json.dumps(cells_det[defn]))
    cells_l2 = {defn: {PRIMARY_COMPARISON: {"under_limit": l2_scope(kept_u, oos_u, excl),
                                            "all_trials": l2_scope(kept_a, {}, excl)}}}
    # over all trials another value, so that reading the wrong scope shows
    cells_l2[defn][PRIMARY_COMPARISON]["all_trials"]["agreement_by_stratum"]["slow"]["mean_dwell_ms"] = \
        _agreement(-4321.0, True)
    for name, cells in (("e1_detection.json", cells_det), ("e1_l1.json", cells_l1), ("e1_l2.json", cells_l2)):
        doc = {"primary": prim, "cells": {label: cells}}
        if name == "e1_detection.json":
            doc.update(top)
        with open(os.path.join(d, name), "wb") as f:
            f.write(json.dumps(doc).encode("utf-8"))
    with open(os.path.join(d, DELIVERY_FILE), "wb") as f:
        f.write(json.dumps({"cells": {label: _delivery()}}).encode("utf-8"))
    return tmp


def _cs_doc(his=None, raw_his=None, n=100, skipped=True):
    """A closed-set result: the upper end of the Wilson interval of acc is his[(K, set)] (0.9 when absent, far from
    chance); that of acc_raw is raw_his[(K, set)] (the same as acc when absent)."""
    his, raw_his = his or {}, raw_his or {}
    res = {}
    for K in CLOSED_SET_KS:
        res[str(K)] = {}
        for f in CLOSED_SET_SETS:
            hi = his.get((K, f), 0.9)
            rhi = raw_his.get((K, f), hi)
            lo = 0.0 if hi is None else max(0.0, hi - 0.01)
            res[str(K)][f] = {"acc": None if hi is None else lo, "acc_ci": [None if hi is None else lo, hi],
                              "acc_source": f, "acc_raw": None if rhi is None else max(0.0, rhi - 0.01),
                              "acc_ci_raw": [None if rhi is None else max(0.0, rhi - 0.01), rhi],
                              "acc_ci_block": [lo, hi], "acc_ci_raw_block": [lo, rhi], "ci_block_status": "ok",
                              "chance": 1.0 / K, "n": 0 if hi is None else n, "n_skipped": 0, "mean_rank": 1.5,
                              "n_blocks": 3, "by_scenario": {"S1": {"acc": 0.5, "n": n // 2}, "S2": {"acc": 0.5, "n": n // 2}},
                              "by_speed": dict((s, {"acc": 0.5, "n": n // 3}) for s in SPEEDS)}
    doc = {"results": res, "alignment": {"n_blocks": 3, "n_blocks_unscored": 0, "n_trials": n, "n_trials_unscored": 0},
           "n_trials": n}
    if skipped:
        doc["skipped_scenarios"] = {CLOSED_SET_SKIPPED: 225}
    return doc


def _os_doc(drop=None):
    """An open-set result whose values differ per split and model, so that a mix-up of attackers shows."""
    cer = {("work", "K0"): 0.9, ("work", "K1"): 0.8, ("phrase", "K1"): 0.6, ("phrase", "K0"): 0.95}
    splits = {}
    for split in ("work", "phrase"):
        splits[split] = {}
        for li, _ in OPEN_SET_LENGTHS:
            r = {"n": 50, "baseline": {}, "gain": {}, "shuffled": {}}
            for model in ("K0", "K1"):
                c = cer[(split, model)]
                g = 1.0 - c
                r[model] = {"cer": c, "cer_ci": [c - 0.05, c + 0.05], "n_undecodable": 2}
                r["baseline"][model] = {"cer": 1.0, "cer_ci": [0.95, 1.05]}
                r["gain"][model] = {"gain": g, "gain_ci": [g - 0.05, g + 0.05]}
                r["shuffled"][model] = {"cer": 1.0, "cer_ci": [0.95, 1.05], "gain": 0.0, "gain_ci": [-0.05, 0.05],
                                        "gain_vs_shuffled": g + 0.01, "gain_vs_shuffled_ci": [g - 0.04, g + 0.06]}
            splits[split][li] = r
    if drop:
        del splits[drop[0]][drop[1]]
    return {"splits": splits, "alignment": {"n_blocks_unscored": 0, "n_trials_unscored": 0}}


def _near(sets, k10=0.2, k100=0.02):
    """The upper ends that put the sets near chance at K = 10 and K = 100, exactly at the bound by default."""
    out = {}
    for f in sets:
        out[(10, f)], out[(100, f)] = k10, k100
    return out


E3_FIXTURE = """== idle (samples.tsv)
rows: 960 usable, 0 with the process absent, 0 unreadable
floors used: at least 480 usable rows and 480 rows with a CPU value; window of at least 4.0 h
sampling interval (measured): median 30.0 s  max 31 s
cpu % (one core = 100): mean 1.23  median 1.00  p95 2.50  max 9.75
cpu_all_zero: {idle_zero}
presence transitions (present, absent, present): 0
resident memory: final 41.2 MB  slope 0.05 MB/h  over 8.00 h
status: measured
== working (samples.tsv)
rows: 300 usable, 0 with the process absent, 0 unreadable
floors used: at least 480 usable rows and 480 rows with a CPU value; window of at least 4.0 h
sampling interval (measured): median 30.0 s  max 31 s
cpu % (one core = 100): mean 3.21  median 3.00  p95 6.50  max 12.00
cpu_all_zero: {working_zero}
presence transitions (present, absent, present): 0
resident memory: final 45.0 MB  slope n/a MB/h  over 2.50 h
status: {working_status}
"""


def _selftest_rules(check):
    """Question 4 and question 5 on constructed inputs (no files)."""
    on_off_near = _near(RULE_SETS)
    docs = {MACOS_CELLS[0]: _cs_doc(on_off_near), MACOS_CELLS[1]: _cs_doc(on_off_near), WINDOWS_CELL: _cs_doc()}
    check("q4: the bound 2/K itself is near chance (at most), and of two sets near chance the more detailed "
          "(no_time) is recommended",
          recommend(docs) == {"status": "recommended", "set": "no_time"}, str(recommend(docs)))
    off = _near(RULE_SETS)
    off[(100, "no_time")] = 0.0201
    docs2 = {MACOS_CELLS[0]: _cs_doc(on_off_near), MACOS_CELLS[1]: _cs_doc(off)}
    check("q4: a set that is not near chance at K = 100 in one macOS cell falls to the coarser set",
          recommend(docs2) == {"status": "recommended", "set": "counts_only"}, str(recommend(docs2)))
    docs3 = {MACOS_CELLS[0]: _cs_doc(), MACOS_CELLS[1]: _cs_doc(on_off_near)}
    check("q4: near chance in only one macOS cell recommends nothing",
          recommend(docs3) == {"status": "none", "set": None}, str(recommend(docs3)))
    k1000 = _near(RULE_SETS)
    k1000[(1000, "no_time")] = 0.9
    docs4 = {MACOS_CELLS[0]: _cs_doc(k1000), MACOS_CELLS[1]: _cs_doc(k1000)}
    check("q4: K = 1,000 is descriptive and does not enter the rule",
          recommend(docs4)["set"] == "no_time", str(recommend(docs4)))
    raw_low = dict(((K, f), 0.001) for K in RULE_KS for f in RULE_SETS)
    docs5 = {MACOS_CELLS[0]: _cs_doc(None, raw_low), MACOS_CELLS[1]: _cs_doc(None, raw_low)}
    check("q4: the rule reads the interval of the reported accuracy (the maximum over coarser sets), not of the "
          "set alone", recommend(docs5)["status"] == "none", str(recommend(docs5)))
    docs6 = dict(docs)
    docs6[WINDOWS_CELL] = _cs_doc(on_off_near)
    docs6[MACOS_CELLS[1]] = _cs_doc()
    check("q4: the Windows cell does not decide the recommendation", recommend(docs6)["status"] == "none")
    docs7 = {MACOS_CELLS[0]: _cs_doc(on_off_near), WINDOWS_CELL: _cs_doc(on_off_near)}
    try:
        rec7 = recommend(docs7)
    except Exception as e:  # a missing cell must give "not decided", not an error
        rec7 = repr(e)
    check("q4: without both macOS cells nothing is recommended (not decided)",
          rec7 == {"status": "not decided", "set": None}, str(rec7))
    nan = _near(RULE_SETS)
    nan[(10, "no_time")] = None
    docs8 = {MACOS_CELLS[0]: _cs_doc(nan), MACOS_CELLS[1]: _cs_doc(on_off_near)}
    check("q4: a set without a value (no scored trial) is not near chance",
          near_chance(docs8[MACOS_CELLS[0]], "no_time") is None and recommend(docs8)["set"] == "counts_only",
          str(recommend(docs8)))
    v = closed_set_values([], docs)
    check("q4: the Windows judgement is the qualification of the recommended set",
          v["q4.windows.qualification"]["text"] == "the recommended set was not near chance"
          and v["q4.recommendation"]["text"] == "\\texttt{no\\_time}", str(v["q4.windows.qualification"]))
    v6 = closed_set_values([], docs6)
    check("q4: with nothing recommended, the Windows judgement of each set is reported as a clause",
          v6["q4.windows.qualification"]["text"]
          == "both \\texttt{no\\_time} and \\texttt{counts\\_only} were near chance",
          v6["q4.windows.qualification"]["text"])
    clauses = (windows_sets_clause({"no_time": False, "counts_only": False}),
               windows_sets_clause({"no_time": True, "counts_only": False}),
               windows_sets_clause({"no_time": None, "counts_only": False}))
    check("q4: the Windows clause says neither, or names each set when they differ or one has no value",
          clauses == ("neither \\texttt{no\\_time} nor \\texttt{counts\\_only} was near chance",
                      "\\texttt{no\\_time} was near chance, and \\texttt{counts\\_only} was not near chance",
                      "\\texttt{no\\_time} has no value, and \\texttt{counts\\_only} was not near chance"), str(clauses))
    tmp = tempfile.mkdtemp(prefix="results_tables_e3_")
    try:
        p = os.path.join(tmp, "e3.txt")
        with open(p, "wb") as f:
            f.write(E3_FIXTURE.format(idle_zero="false", working_zero="false",
                                      working_status="insufficient -- fewer than 480 usable rows").encode("utf-8"))
        e3 = {"macos": ("e3_macos.txt", read_e3(p))}
        vals = e3_values(e3)
        check("q5: a measured condition gives its mean CPU, an insufficient one is not quoted",
              vals["macos.e3.idle.cpu.mean"]["text"] == "1.23" and vals["macos.e3.working.cpu.mean"]["text"] == NO_VALUE
              and vals["windows.e3.idle.cpu.mean"]["text"] == NO_VALUE, str(vals))
        cost = table_cost(e3)
        check("q5: the table shows an insufficient condition without figures and a missing system as not measured",
              "working & insufficient & --" in cost and "Windows & idle & not measured" in cost, cost[-600:])
        with open(p, "wb") as f:
            f.write(E3_FIXTURE.format(idle_zero="true", working_zero="true", working_status="measured").encode("utf-8"))
        e3w = {"windows": ("windows/e3_windows.txt", read_e3(p))}
        vw = e3_values(e3w)
        cost_w = table_cost(e3w)
        check("q5: on Windows a working condition whose every CPU sample is zero is insufficient (section 10.9); "
              "an idle one is reported with the flag",
              vw["windows.e3.working.cpu.mean"]["text"] == NO_VALUE and vw["windows.e3.idle.cpu.mean"]["text"] == "1.23"
              and "insufficient (every CPU sample is zero)" in cost_w
              and "measured (every CPU sample is zero)" in cost_w, cost_w[-700:])
        with open(p, "wb") as f:
            f.write(E3_FIXTURE.replace("cpu_all_zero: {idle_zero}\n", "").format(
                working_zero="false", working_status="measured").encode("utf-8"))
        try:
            read_e3(p)
            check("q5: negative: a measured condition without its cpu_all_zero line stops the script", False)
        except ValueError:
            check("q5: negative: a measured condition without its cpu_all_zero line stops the script", True)
        with open(p, "wb") as f:
            f.write(E3_FIXTURE.replace("== working", "== busy").format(
                idle_zero="false", working_zero="false", working_status="measured").encode("utf-8"))
        try:
            read_e3(p)
            check("q5: negative: an unknown condition stops the script", False)
        except ValueError:
            check("q5: negative: an unknown condition stops the script", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _expect_stop(check, name, tmp):
    """The script must stop with its own message (ValueError), not run through and not fail elsewhere."""
    try:
        build(tmp)
        check(name, False, "(ran through)")
    except ValueError:
        check(name, True)
    except Exception as e:  # noqa: BLE001 - another error is a failure of this check, not of the selftest
        check(name, False, repr(e))


def _rewrite(path, edit):
    doc = load(path)
    edit(doc)
    with open(path, "wb") as f:
        f.write(json.dumps(doc).encode("utf-8"))


def _selftest_reference(check):
    """Table 7 (RF1), KT1 and the marker times on a synthetic Windows cell."""
    tmp = tempfile.mkdtemp(prefix="results_tables_rf1_")
    try:
        _fixture(tmp)
        files = build(tmp)
        t7 = files["table-reference.tex"].decode("utf-8")
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        check("rf1: the disagreements with the plan are a count with the rate and its Wilson interval",
              "Trials that disagree with the plan's intent & 10 (0.022 [0.012, 0.040]) \\\\" in t7, t7[:1500])
        order = [t7.find("\\quad " + label + " & ") for _, label in PLAN_TYPES]
        check("rf1: the types of disagreement keep the order of the registration and their own counts",
              all(o > 0 for o in order) and order == sorted(order)
              and "\\quad An unexpected session & 1 (" in t7 and "\\quad Several sessions & 2 (" in t7
              and "\\quad A cancellation where a commit was planned & 3 (" in t7, str(order))
        check("rf1: the delivery gives both versions in the stated order (all presses / input-mode keys left out) and "
              "the two sides apart",
              "Observation: presses that arrived (rate) & 990 (0.990) / 990 (1.000) \\\\" in t7
              and "Reference: rows beyond the presses sent in a trial (trials) & 1 (1) / 11 (6) \\\\" in t7
              and "Presses sent & 1{,}000 / 990 \\\\" in t7, t7[1500:4000])
        check("rf1: the lateness of the executor is the median, the 95th percentile and the maximum",
              "(ms): median, 95th percentile, maximum & 0.01, 0.03, 0.57 \\\\" in t7)
        check("rf1: the session rows of the trials with a loss of focus are given per scope",
              "false positives), primary scope & 3 (1) \\\\" in t7 and "false positives), all trials & 5 (2) \\\\" in t7)
        check("rf1: a cell without marker times says so", "not computed" in t7 and "windows-test.markers.blocks" not in vals)
        check("rf1: the values for the text name their sources",
              vals["windows-test.plan.expected_commit_got_cancel"]["text"].startswith("3 (")
              and vals["windows-test.delivery.ref.nomode.excessrows"]["value"] == 11
              and vals["windows-test.focus.trialswithfocusloss"]["value"] == 2
              and vals["windows-test.focus.rows.all"]["value"] == [5, 2]
              and vals["windows-test.plan.trials"]["source"].endswith("under_limit/oracle/n_trials"),
              str(vals.get("windows-test.plan.expected_commit_got_cancel")))
        kt = files["table-key-timing.tex"].decode("utf-8")
        check("kt1: the main table is the observation's key rows, per speed, with the seven figures in order",
              "& slow & Delay & 100 & 13.3 & 12 & 12.5 & 13.0 & 15.0 & 20 \\\\" in kt
              and "&  & Interval error & 100 & $-$10.2 & $-$11 & $-$11.0 & $-$10.5 & $-$8.5 & $-$3 \\\\" in kt
              and "& all speeds & Delay & 100 & 3.3 & 2 & 2.5 & 3.0 & 5.0 & 10 \\\\" in kt
              and "Trials paired: 440; not paired: 10" in kt and "key rows of presses in the log" in kt, kt[-2500:])
        sup = files["reference-strata.tex"].decode("utf-8")
        check("kt1: the supplement gives the reference's key-downs in a landscape long table",
              "& slow & Delay & 100 & 15.3 & 14 & 14.5 & 15.0 & 17.0 & 22 \\\\" in sup
              and "key-downs of the reference log" in sup and "\\begin{longtable}" in sup
              and "sidewaystable" not in sup, sup[-1500:])
        check("kt1: the values give every figure per side, measure and speed",
              vals["windows-test.kt.obs.delay.mid.p95"]["text"] == "25.0~ms"
              and vals["windows-test.kt.ref.interval.all.min"]["value"] == 0
              and vals["windows-test.kt.obs.notpaired"]["value"] == 10, str(vals.get("windows-test.kt.obs.delay.mid.p95")))
        check("rf1: the supplement gives the types per stratum in the order of the registration and the trials with a "
              "loss of focus",
              "S1 & fast & 75 & 3 (0.040 [0.000, 0.090]) & 2 & 0 & 0 & 0 & 1 & 0 & 0 & 0 \\\\" in sup
              and "S1 & mid & 75 & 0 (0.000 [0.000, 0.050]) & 0 & 0 & 0 & 0 & 0 & 0 & 0 & 2 \\\\" in sup, sup[:2500])
        t9 = files["table-session.tex"].decode("utf-8")
        check("table 9: the bias, both limits and the median absolute difference each carry their own interval",
              "windows-test & slow & 50 & $-$900 [$-$1,051, $-$800] & $-$1,800 [$-$1,850, $-$1,750] & 0 [$-$50, 50] & "
              "300 [250, 350] & 0.900 [0.800, 0.950] & %s & 1.000 [0.930, 1.000] \\\\" % NO_VALUE in t9, t9[-1200:])
        st = files["detection-strata.tex"].decode("utf-8")
        check("strata: every scope of the detection results has its table, and the trials are not given where question "
              "3 does not count them",
              "\\label{tab:strata-windows-test-gtobs-underlimitgtduration}" in st
              and "\\label{tab:strata-windows-test-gtobs-overlimit}" in st
              and "S1 & slow & %s & 46 & 46 & " % NO_VALUE in st
              and "which the analysis of cell windows-test does not compute" in st, st[:1500])
        check("diagnostics: exclusions by reason, the two definitions, rows of zero duration and the cut-off per scope",
              vals["windows-test.excluded.gt.carryover"]["value"] == 2 and vals["windows-test.excluded.obs.n"]["value"] == 1
              and vals["windows-test.samesessions"]["text"] == "no"
              and vals["windows-test.zerodwell.all"]["value"][0] == 7 and vals["windows-test.zerodwell.primary"]["value"][0] == 2
              and vals["windows-test.cutoff.all.obsoverlimit"]["value"] == 7
              and vals["windows-test.cutoff.primary.longwithrow"]["value"] == 1,
              str([k for k in vals if ".excluded." in k or ".cutoff." in k][:8]))
        check("diagnostics: the agreement of abandon split by continued commits, and the pooled measures per scope",
              vals["windows-test.abandon.continued.with.primary"]["text"] == "0.750 [0.600, 0.850] (20 pairs)"
              and vals["windows-test.abandon.continued.unknown.all"]["value"] == 1
              and vals["windows-test.pooled.gtobs.underlimit.recall"]["value"][0] == 0.9
              and vals["windows-test.pooled.gtobs.alltrials.recall"]["value"][0] == 0.8
              and vals["windows-test.pooled.gtobs.underlimitgtduration.f1"]["text"] == "0.850",
              str(vals.get("windows-test.pooled.gtobs.underlimit.recall")))
        check("diagnostics: the Windows rows of 20 to 25 and of 25 seconds or more, the Escapes of S4, the ends without a "
              "length by scenario, and the long trials of the two sources",
              vals["windows-test.win.rows.20000to24999"]["value"] == 3 and vals["windows-test.win.rows.25000ormore"]["value"] == 1
              and vals["windows-test.win.s4.escape3"]["value"] == 70
              and vals["windows-test.win.endsnolength"]["text"] == "1 in S2, 4 in S10"
              and vals["windows-test.win.long.long"]["value"] == 617 and vals["windows-test.win.long.longgt"]["value"] == 615
              and vals["windows-test.win.long.differ"]["value"] == 2,
              str([k for k in vals if ".win." in k]))
        cs = os.path.join(tmp, "windows", clock_sync_name("windows-test"))
        with open(cs, "wb") as f:
            f.write(json.dumps(_clock_doc()).encode("utf-8"))
        files = build(tmp)
        t7 = files["table-reference.tex"].decode("utf-8")
        sup = files["reference-strata.tex"].decode("utf-8")
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        check("markers: the median, range and drift of each difference, whole milliseconds kept whole",
              "Observation minus executor (ms): median (range); drift per hour & 1 (0 to 1); 10.2 \\\\" in t7
              and "Reference minus executor, monotonic clocks (ms): median (range); drift per hour & 3.5 (2.5 to 4.5); "
              "30.2 \\\\" in t7 and "& 3 of 3, 3, 2 \\\\" in t7, t7[-1800:])
        check("markers: with marker times for every cell, the legend of cells without them is not given",
              "not computed" not in t7, t7[:900])
        check("markers: the supplement gives every block and marks a block not found on a side",
              "2 & %s & 3 & 4.5 & %s \\\\" % (NO_VALUE, NO_VALUE) in sup and "0 & 0 & 1 & 2.5 & 3 \\\\" in sup, sup[-3000:])
        check("markers: the values for the text",
              vals["windows-test.markers.obsoracle.median"]["text"] == "1~ms"
              and vals["windows-test.markers.gtoraclemono.range"]["text"] == "2.5 to 4.5~ms"
              and vals["windows-test.markers.obsgt.drift"]["value"] == 40.25,
              str(sorted(k for k in vals if ".markers." in k)))
        detp0 = os.path.join(tmp, "windows", "e1_detection.json")
        l1p0 = os.path.join(tmp, "windows", "e1_l1.json")
        det0, l10 = load(detp0), load(l1p0)

        def blk(doc, scope=PRIMARY_SCOPE):
            return doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON][scope]

        def no_recall_interval(doc):
            for st in blk(doc)["metrics"]["by_stratum"]:
                if "recall" in st:
                    st["recall"]["lo"] = st["recall"]["hi"] = None
        _rewrite(detp0, no_recall_interval)
        v = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
        check("review: a range without any stratum is still a value (no value), so the text cannot lose it",
              (v.get("windows-test.primary.recall.range") or {}).get("text") == NO_VALUE
              and (v.get("windows-test.primary.recall.strata.left") or {}).get("value") == 6,
              str(v.get("windows-test.primary.recall.range")))
        with open(detp0, "wb") as f:
            f.write(json.dumps(det0).encode("utf-8"))
        _rewrite(l1p0, lambda doc: [e["dwell"].update({"bias_ci": [None, None]}) for e in blk(doc)["metrics"]["by_speed"]
                                    if e["stratum"]["speed"] == "slow"])
        v = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
        check("review: the dwell-bias range names the speeds it uses and leaves out",
              v["windows-test.primary.dwellbias.range"]["value"] == [-600.0, -49.5]
              and v["windows-test.primary.dwellbias.speeds.used"]["value"] == 2
              and v["windows-test.primary.dwellbias.speeds.left"]["value"] == 1,
              str(v.get("windows-test.primary.dwellbias.range")))
        with open(l1p0, "wb") as f:
            f.write(json.dumps(l10).encode("utf-8"))
        # In every scope, so that only the check of the types (not that of the scopes) can stop it.
        _rewrite(detp0, lambda doc: [b["excluded"]["trials"]["by_type"].update({"carryover": 1}) for b in
                                     doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON].values()])
        _expect_stop(check, "review: negative: an excluded trial of a type outside section 6.2 stops", tmp)
        with open(detp0, "wb") as f:
            f.write(json.dumps(det0).encode("utf-8"))
        _rewrite(detp0, lambda doc: [b["excluded"]["trials"].update({"n": 10}) for b in
                                     doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON].values()])
        _expect_stop(check, "review: negative: more excluded trials than the types account for stop", tmp)
        with open(detp0, "wb") as f:
            f.write(json.dumps(det0).encode("utf-8"))
        _rewrite(detp0, lambda doc: blk(doc)["metrics"]["by_stratum"][0].update(
            {"recall": {"value": None, "lo": None, "hi": None, "n": 2, "x": 3, "note": "above"}}))
        _expect_stop(check, "review: negative: a rate other than the merge rate without a value (D10's defence) stops",
                     tmp)
        with open(detp0, "wb") as f:
            f.write(json.dumps(det0).encode("utf-8"))
        bad_label = load(detp0)
        bad_label["cells"]["windows-test}\\input{x"] = bad_label["cells"]["windows-test"]
        with open(detp0, "wb") as f:
            f.write(json.dumps(bad_label).encode("utf-8"))
        _expect_stop(check, "review: negative: a cell label that the analysis does not accept stops", tmp)
        bad_label = json.loads(json.dumps(det0))
        bad_label["cells"]["windows-test\n"] = bad_label["cells"]["windows-test"]
        with open(detp0, "wb") as f:
            f.write(json.dumps(bad_label).encode("utf-8"))
        _expect_stop(check, "review: negative: a cell label with a trailing line break stops", tmp)
        with open(detp0, "wb") as f:
            f.write(json.dumps(det0).encode("utf-8"))
        check("review: every character that TeX treats specially is escaped",
              tex_escape("a\\b{c}_%&#$^~") == "a\\textbackslash{}b\\{c\\}\\_\\%\\&\\#\\$\\^{}\\~{}")
        l1p = os.path.join(tmp, "windows", "e1_l1.json")
        good = load(l1p)
        _rewrite(l1p, lambda doc: doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["all_trials"][
            "oracle"].update({"n_agree": 439}))
        _expect_stop(check, "rf1: negative: a plan-versus-ground-truth report that differs between scopes stops", tmp)
        with open(l1p, "wb") as f:
            f.write(json.dumps(good).encode("utf-8"))
        detp = os.path.join(tmp, "windows", "e1_detection.json")
        good_det = load(detp)
        _rewrite(detp, lambda doc: doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["under_limit"][
            "focus_loss"].update({"n_trials": 3}))
        _expect_stop(check, "rf1: negative: trials with a loss of focus that differ from the delivery report stop", tmp)
        with open(detp, "wb") as f:
            f.write(json.dumps(good_det).encode("utf-8"))
        _rewrite(cs, lambda doc: doc["differences"].pop("obs_minus_gt_wall"))
        _expect_stop(check, "markers: negative: a file without one of the four differences stops", tmp)
        os.remove(cs)
        os.remove(os.path.join(tmp, "windows", DELIVERY_FILE))
        _expect_stop(check, "rf1: negative: a cell without its delivery report stops", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_figures(check):
    """Figures 2 to 5 on the synthetic Windows cell: S1 keeps 75, 30 and 29 trials (fast, mid, slow) in the primary
    scope and S2 none."""
    tmp = tempfile.mkdtemp(prefix="results_tables_figures_")
    try:
        _fixture(tmp)
        files = build(tmp)
        f2 = files["fig-detection.tex"].decode("utf-8")
        check("fig 2: a stratum at the minimum of trials (30) gets a filled mark and one under it (29) an open mark, "
              "at the number of the scenario shifted by the speed",
              "\\addplot[forget plot, fig mid, fig point] table[row sep=\\\\] {x y\\\\1 0.733333\\\\};" in f2
              and "\\addplot[forget plot, fig slow, fig point, fig open] table[row sep=\\\\] {x y\\\\0.75 0.689655\\\\};"
              in f2 and "\\addplot[forget plot, fig fast, fig point] table[row sep=\\\\] {x y\\\\1.25 0.933333\\\\};"
              in f2, f2[:1500])
        check("fig 2: an interval is drawn from its two ends as the result file gives them",
              "\\addplot[forget plot, fig interval, fig mid] table[row sep=\\\\] {x y\\\\1 0.6\\\\1 0.86\\\\1 nan\\\\};"
              in f2, f2[:1500])
        xs = [float(x) for x in re.findall(r"\\\\(-?[0-9.e+-]+) (?:-?[0-9.e+-]+|nan)(?=\\\\)", f2)]
        check("fig 2: every scenario of the design is a tick, and a scenario without a stratum in the scope has no mark",
              "xtick={1,2}, xticklabels={S1,S2}" in f2 and xs and all(0.7 < x < 1.3 for x in xs), str(xs[:12]))
        check("figures: every drawn figure says in its caption that its code was written with an AI tool",
              all(("\\caption{" in files[n].decode("utf-8")) and (FIG_AI_NOTE + "}") in files[n].decode("utf-8")
                  for n in ("fig-detection.tex", "fig-dwell.tex", "fig-text-agreement.tex")))
        check("fig 2: one panel per cell and measure, with the legend of the speeds and the open marks",
              f2.count("\\nextgroupplot[") == 2 and "\\addlegendentry{fewer than %d trials}" % MIN_TRIALS in f2
              and "\\pgfplotslegendfromname{fig:detection:legend}" in f2 and "\\label{fig:detection}" in f2)
        f3 = files["fig-dwell.tex"].decode("utf-8")
        check("fig 3: the bias of a stratum and each limit of agreement, each with its own interval",
              "\\addplot[forget plot, fig mid, fig point] table[row sep=\\\\] {x y\\\\1 -499\\\\};" in f3
              and "\\addplot[forget plot, fig mid, fig limit] table[row sep=\\\\] {x y\\\\1 -1399\\\\};" in f3
              and "\\addplot[forget plot, fig mid, fig limit] table[row sep=\\\\] {x y\\\\1 401\\\\};" in f3
              and "{x y\\\\1 -600\\\\1 -400\\\\1 nan\\\\}" in f3 and "{x y\\\\1 -1449\\\\1 -1349\\\\1 nan\\\\}" in f3
              and "{x y\\\\1 351\\\\1 451\\\\1 nan\\\\}" in f3, f3[:2500])
        check("fig 3: the marks of a stratum under the minimum of trials are open, the limits too",
              "\\addplot[forget plot, fig slow, fig limit, fig open] table[row sep=\\\\] {x y\\\\0.75 -1799.4\\\\};" in f3
              and "\\addplot[forget plot, fig slow, fig point, fig open] table[row sep=\\\\] {x y\\\\0.75 -899.4\\\\};"
              in f3, f3[:2500])
        f4 = files["fig-text-agreement.tex"].decode("utf-8")
        panels = f4.split("\\nextgroupplot[")[1:]
        check("fig 4: one panel per measure, in the order of the table",
              len(panels) == len(L2_MEASURES) and all(p.startswith("title={%s}" % label)
                                                      for p, (_, label, _) in zip(panels, L2_MEASURES)),
              str([p[:40] for p in panels]))
        check("fig 4: the bias and both limits at the position of the cell shifted by the speed, each with its interval",
              len(panels) == 4
              and "\\addplot[forget plot, fig mid, fig point] table[row sep=\\\\] {x y\\\\1 -1234.4\\\\};" in panels[1]
              and "\\addplot[forget plot, fig mid, fig limit] table[row sep=\\\\] {x y\\\\1 -2134.4\\\\1 -534.4\\\\};"
              in panels[1] and "1 -1235.4\\\\1 -1233.4\\\\1 nan\\\\1 -2135.4\\\\1 -2133.4\\\\1 nan\\\\1 -535.4\\\\1 "
              "-533.4\\\\1 nan\\\\}" in panels[1] and "{x y\\\\0.75 -1234.4\\\\}" in panels[1], f4[:2500])
        check("fig 4: a measure with no text at a speed is not drawn",
              len(panels) == 4 and "fig slow" not in panels[2] and "fig mid" in panels[2] and "fig slow" in panels[0],
              panels[2][:600] if len(panels) > 2 else f4[:600])
        check("fig 4: the per-text values are those of the primary scope", "-4321" not in f4)
        check("fig 4: the caption says that the values of the Windows cell are not compared with those of macOS",
              "the values of the Windows cell estimate the agreement with a different construct and are not compared "
              "with those of the macOS cells" in f4)
        check("fig 5: a cell without a closed-set file is not drawn", FIG_NO_CELL in files["fig-closed-set.tex"].decode(
            "utf-8") and "tikzpicture" not in files["fig-closed-set.tex"].decode("utf-8"))
        with open(os.path.join(tmp, "windows", closed_set_name("windows-test")), "wb") as f:
            f.write(json.dumps(_cs_doc(None, {(10, "no_time"): 0.5})).encode("utf-8"))
        f5 = build(tmp)["fig-closed-set.tex"].decode("utf-8")
        check("fig 5: every set at every K, set apart by its shift in decades, with the accuracy of the rule (not the "
              "raw accuracy) and its Wilson interval",
              "\\addplot[forget plot, fig set5, fig point] table[row sep=\\\\] {x y\\\\11.4815 0.89\\\\114.815 0.89\\\\"
              "1148.15 0.89\\\\};" in f5 and "{x y\\\\11.4815 0.89\\\\11.4815 0.9\\\\11.4815 nan\\\\" in f5
              and "\\addplot[forget plot, fig set1, fig point] table[row sep=\\\\] {x y\\\\7.94328 0.89\\\\" in f5
              and "0.49" not in f5, f5[:2500])
        check("figures: the drawn figure 5 also says in its caption that its code was written with an AI tool",
              (FIG_AI_NOTE + "}") in f5)
        check("fig 5: the chance level 1/K and the margin 2/K are drawn",
              "\\addplot[forget plot, fig chance, domain=7:1500] {1/x};" in f5
              and "\\addplot[forget plot, fig margin, domain=7:1500] {2/x};" in f5)
        check("fig 5: without a value below the lower edge of the axis the caption says nothing of it",
              "lower edge of the axis" not in f5 and "ymin=0.0005" in f5)
        zero = _cs_doc(None, None)
        zero["results"]["1000"]["counts_only"].update({"acc": 0.0, "acc_ci": [0.0, 0.004]})
        with open(os.path.join(tmp, "windows", closed_set_name("windows-test")), "wb") as f:
            f.write(json.dumps(zero).encode("utf-8"))
        f5z = build(tmp)["fig-closed-set.tex"].decode("utf-8")
        check("fig 5: an accuracy of zero and a lower end of zero are drawn at the lower edge of the logarithmic axis, "
              "and the caption says so",
              "nan\\\\1258.93 0.0005\\\\1258.93 0.004\\\\1258.93 nan\\\\}" in f5z
              and "\\\\1258.93 0.0005\\\\};" in f5z
              and "below 0.0005 is drawn at the lower edge of the axis" in f5z, f5z[-2500:])
        zero["results"]["1000"]["counts_only"].update({"acc": 0.0, "acc_ci": [0.0, 0.0002]})
        with open(os.path.join(tmp, "windows", closed_set_name("windows-test")), "wb") as f:
            f.write(json.dumps(zero).encode("utf-8"))
        f5b = build(tmp)["fig-closed-set.tex"].decode("utf-8")
        check("fig 5: an interval that lies wholly below the lower edge is drawn at the edge, its ends in order",
              "nan\\\\1258.93 0.0005\\\\1258.93 0.0005\\\\1258.93 nan\\\\}" in f5b
              and "below 0.0005 is drawn at the lower edge of the axis" in f5b, f5b[-2500:])
        check("names: the scenario names of the plans are S1 to S15, without leading zeros",
              all(SCENARIO_RE.fullmatch("S%d" % i) for i in range(1, 16))
              and not any(SCENARIO_RE.fullmatch(n) for n in ("S0", "S00", "S01", "S16", "S99", "s1", "S1 ")))
        try:
            empty_axis = fig_scenario_axis([])
        except IndexError as e:
            empty_axis = repr(e)
        check("figures: a design without strata gives an axis without ticks", empty_axis == "", str(empty_axis))
        # A scenario name or a speed that the plans do not use stops the script, since names are printed into TeX.
        for name, fname, edit in (
                ("a scenario name that is not one of the plans", "e1_l2.json",
                 lambda d: d["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["under_limit"]["per_trial"][
                     "by_stratum"][0]["stratum"].update({"scenario": "S1}, xticklabel style={red"})),
                ("a speed that is not one of the plans", "e1_detection.json",
                 lambda d: d["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["under_limit"]["metrics"][
                     "by_stratum"][0]["stratum"].update({"speed": "slow\\input{x}"})),
                ("a scenario key that is not one of the plans", closed_set_name("windows-test"),
                 lambda d: d["results"]["10"]["no_time"]["by_scenario"].update({"S1\\relax": {"acc": 0.5, "n": 1}}))):
            p = os.path.join(tmp, "windows", fname)
            with open(p, "rb") as f:
                original = f.read()
            doc = json.loads(original.decode("utf-8"))
            edit(doc)
            with open(p, "wb") as f:
                f.write(json.dumps(doc).encode("utf-8"))
            _expect_stop(check, "names: negative: %s stops" % name, tmp)
            with open(p, "wb") as f:
                f.write(original)
        # An estimate outside its percentile interval keeps the interval as the result file gives it.
        p = os.path.join(tmp, "windows", "e1_l1.json")
        doc = load(p)
        for st in doc["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["under_limit"]["metrics"]["by_stratum"]:
            if st["stratum"]["speed"] == "fast":
                st["dwell"]["bias_ci"] = [-90.0, -10.0]
        with open(p, "wb") as f:
            f.write(json.dumps(doc).encode("utf-8"))
        f3b = build(tmp)["fig-dwell.tex"].decode("utf-8")
        check("fig 3: an estimate outside its interval is drawn with the interval of the result file",
              "{x y\\\\1.25 -90\\\\1.25 -10\\\\1.25 nan\\\\}" in f3b and "{x y\\\\1.25 -99\\\\}" in f3b, f3b[:2500])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    empty = tempfile.mkdtemp(prefix="results_tables_figures_empty_")
    try:
        files = build(empty)
        check("figures: without a measured cell every figure says so and draws nothing",
              all(FIG_NO_CELL in files[n].decode("utf-8") and "tikzpicture" not in files[n].decode("utf-8")
                  for n in ("fig-detection.tex", "fig-dwell.tex", "fig-text-agreement.tex", "fig-closed-set.tex")))
    finally:
        shutil.rmtree(empty, ignore_errors=True)


class _FakeCell(object):
    """Enough of a Cell for reference_spread() and read_dwell_explore()."""

    def __init__(self, label, gt, obs, speeds=None, directory=None, match=None):
        self.label = label
        self.l2 = {PRIMARY_SCOPE: {"aggregate": {"gt": gt, "obs": obs}}}
        self._speeds = speeds or {}
        self.directory = directory
        self.src = {"e1_l1.json": "e1_l1.json"}
        self.l1 = dict((s, {"inputs_sha256": {"match": (match or {}).get(s)}}) for s in SCOPES)

    def path(self, f, *keys):
        return "%s:%s" % (f, "/".join(str(k) for k in keys))

    def speed_sessions(self, scope):
        return self._speeds.get(scope, {})


def _selftest_spread(check):
    t = lambda k, s: {"text_id": "b%d" % k, "block_id": k, "speed": s}
    gt = [dict(t(0, "slow"), m=1.0), dict(t(1, "slow"), m=1.0), dict(t(2, "mid"), m=2.0), dict(t(3, "mid"), m=None)]
    obs = [dict(t(0, "slow"), m=1.5), dict(t(1, "slow"), m=0.5), dict(t(2, "mid"), m=2.5), dict(t(3, "mid"), m=3.0)]
    sp = reference_spread(_FakeCell("x", gt, obs), PRIMARY_SCOPE, "m", "slow")
    check("spread: a reference that is the same in every text is constant, with SD 0",
          sp == {"n": 2, "sd": 0.0, "constant": True, "off_mode": 0}, sp)
    sp = reference_spread(_FakeCell("x", gt, obs), PRIMARY_SCOPE, "m", None)
    check("spread: over all speeds the texts without a value on a side are left out",
          sp["n"] == 3 and not sp["constant"] and abs(sp["sd"] - math.sqrt(1.0 / 3.0)) < 1e-12, sp)
    sp = reference_spread(_FakeCell("x", gt, obs), PRIMARY_SCOPE, "m", "mid")
    check("spread: one text has no SD and is not constant", sp == {"n": 1, "sd": None, "constant": False, "off_mode": 0}, sp)
    # Ten texts of 0.1 (not exact in binary): a sum from left to right gives a mean below 0.1 and an SD above zero, so
    # the value would differ between versions of Python; the correctly rounded sum gives exactly zero.
    tens = [dict(t(k, "slow"), m=0.1) for k in range(10)]
    sp = reference_spread(_FakeCell("x", tens, tens), PRIMARY_SCOPE, "m", "slow")
    check("spread: the sums are correctly rounded, so the SD does not depend on the version of Python",
          sp == {"n": 10, "sd": 0.0, "constant": True, "off_mode": 0}, sp)
    one = [dict(t(k, "slow"), m=(2.0 if k == 3 else 1.0)) for k in range(6)]
    two = [dict(t(k, "slow"), m=(2.0 if k in (3, 4) else 1.0)) for k in range(6)]
    sp1 = reference_spread(_FakeCell("x", one, one), PRIMARY_SCOPE, "m", "slow")
    sp2 = reference_spread(_FakeCell("x", two, two), PRIMARY_SCOPE, "m", "slow")
    check("spread: a reference that differs from the most frequent value in a single text is counted as such",
          sp1["off_mode"] == 1 and not sp1["constant"] and sp2["off_mode"] == 2, (sp1, sp2))
    # The counts of the text over the strata of a cell: slow is constant, mid differs in a single text, fast in two.
    cv = {"slow": (1.0, 1.0, 1.0, 1.0), "mid": (1.0, 1.0, 1.0, 2.0), "fast": (1.0, 1.0, 2.0, 2.0)}
    gt3 = [dict(t(10 * i + k, s), conversions_per_trial=v) for i, s in enumerate(SPEEDS) for k, v in enumerate(cv[s])]
    fake = _FakeCell("x", gt3, gt3)
    fake.l2[PRIMARY_SCOPE]["agreement_by_stratum"] = dict((s, {"conversions_per_trial": {"n": 4}}) for s in SPEEDS)
    cst = l2_constant_values([fake])
    got = tuple(cst["q3.conversions_per_trial.%s" % k]["value"] for k in ("constant.strata", "constant.of",
                                                                         "onetext.strata"))
    check("q3: the counts of the text tell a constant reference, one that differs in a single text and one that "
          "differs in more", got == (1, 3, 1), str(got))
    # A second cell: slow constant, mid with a single text, no agreement at fast; and a text whose logger side is
    # missing, which leaves the ICC and so the count (the reference differs only there).
    gt4 = [dict(t(100 + k, "slow"), conversions_per_trial=1.0) for k in range(3)] + [
        dict(t(110, "mid"), conversions_per_trial=1.0), dict(t(120, "slow"), conversions_per_trial=2.0)]
    ob4 = [dict(g) for g in gt4]
    ob4[-1]["conversions_per_trial"] = None
    fake2 = _FakeCell("y", gt4, ob4)
    fake2.l2[PRIMARY_SCOPE]["agreement_by_stratum"] = {"slow": {"conversions_per_trial": {"n": 3}},
                                                       "mid": {"conversions_per_trial": {"n": 1}}}
    cst2 = l2_constant_values([fake, fake2])
    got2 = tuple(cst2["q3.conversions_per_trial.%s" % k]["value"] for k in ("constant.strata", "constant.of",
                                                                           "onetext.strata"))
    check("q3: over cells, a stratum without agreement is not counted, a stratum of one text is neither constant nor "
          "single-text, and a text without the logger's value is not one of the ICC's texts", got2 == (2, 5, 1),
          str(got2))
    words = (number_word(0), number_word(2), number_word(6), number_word(9), number_word(12), number_word(13))
    check("a small count is written as a word, a larger one as digits",
          words == ("none", "two", "six", "nine", "twelve", "13"), str(words))
    for name, bad in (("in length", obs[:3]), ("in alignment", [obs[1], obs[0]] + obs[2:])):
        try:
            reference_spread(_FakeCell("x", gt, bad), PRIMARY_SCOPE, "m", "slow")
            check("spread: sides that differ %s are refused" % name, False)
        except ValueError:
            check("spread: sides that differ %s are refused" % name, True)
        except Exception as ex:  # a crash is a failure of this check, not of the run
            check("spread: sides that differ %s are refused" % name, False, repr(ex))
    e = {"n": 3}
    try:
        checked_spread(_FakeCell("x", gt, obs), PRIMARY_SCOPE, "m", "slow", e)
        check("spread: texts that differ from the agreement's are refused", False)
    except ValueError:
        check("spread: texts that differ from the agreement's are refused", True)
    except Exception as ex:
        check("spread: texts that differ from the agreement's are refused", False, repr(ex))


def _selftest_dwell_explore(check):
    tmp = tempfile.mkdtemp(prefix="results_tables_dwellx_")
    try:
        speeds = {"under_limit": {"slow": {"dwell": {"n": 10}}, "mid": {"dwell": {"n": 0}}, "fast": {"dwell": {"n": 4}}},
                  "all_trials": {"slow": {"dwell": {"n": 12}}, "fast": {"dwell": {"n": 4}}}}
        cell_dir = os.path.join(tmp, "cell")
        os.makedirs(cell_dir)
        with open(os.path.join(cell_dir, "e1_l1.json"), "wb") as f:
            f.write(b"{}\n")
        l1_sha = sha256_of(os.path.join(cell_dir, "e1_l1.json"))
        match = {"under_limit": "a" * 64, "all_trials": "b" * 64}
        cell = _FakeCell("cell-x", [], [], speeds, cell_dir, match)
        check("dwellx: a cell without the file has no exploratory values",
              read_dwell_explore(tmp, cell) is None and dwell_explore_values(cell, None) == {})
        entry = lambda n, p=-1500.4, w=0.375: {"n": n, "percentiles": {"p2.5": p, "p97.5": 40.0},
                                               "within": {"abs_le_100_ms": w, "abs_le_1000_ms": 0.8}}
        empty = {"n": 0, "percentiles": {"p2.5": None, "p97.5": None},
                 "within": {"abs_le_100_ms": None, "abs_le_1000_ms": None}}
        doc = {"exploratory": True, "inputs_sha256": {"e1_l1_registered": l1_sha},
               "cells": {"cell-x": {"under_limit": {"slow": entry(10), "mid": empty, "fast": entry(4, -900.0, 0.25),
                                                    "match_sha256": match["under_limit"]},
                                    "all_trials": {"slow": entry(12, -2600.0), "fast": entry(4, -2600.0),
                                                   "match_sha256": match["all_trials"]}}}}
        os.makedirs(os.path.join(tmp, DWELL_EXPLORE_DIR))
        p = os.path.join(tmp, DWELL_EXPLORE_DIR, "dwell_quantiles_cell-x.json")

        def write(d):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f)

        write(doc)
        got = read_dwell_explore(tmp, cell)
        vals = dwell_explore_values(cell, got)
        check("dwellx: the ranges over the speeds run from the smallest to the largest, and one value stands alone",
              vals.get("cell-x.dwellx.p25.range", {}).get("text") == "$-$1,500 to $-$900~ms"
              and vals.get("cell-x.dwellx.within100.range", {}).get("text") == "25.0\\%--37.5\\%"
              and vals.get("cell-x.dwellx.within1000.range", {}).get("text") == "80.0\\%"
              and vals.get("cell-x.dwellx.p975.range", {}).get("text") == "40~ms", sorted(vals))
        check("dwellx: the values of the primary scope, signed whole milliseconds and percentages",
              vals.get("cell-x.dwellx.slow.p25", {}).get("text") == "$-$1,500"
              and vals.get("cell-x.dwellx.fast.within100", {}).get("text") == "25.0\\%"
              and "cell-x.dwellx.mid.p25" not in vals
              and vals["cell-x.dwellx.slow.within1000"]["source"].endswith("under_limit/slow/within/abs_le_1000_ms"),
              sorted(vals))
        sup = dwell_explore_supplement([cell], {"cell-x": got})
        check("dwellx: the supplement table is labelled as not registered and holds both scopes",
              "Not registered" in sup and sup.count("\\texttt{all\\_trials}") == 1 and "& 12 &" in sup
              and "mid & \\multicolumn{5}{l}{no pair}" in sup, sup[-600:])

        def with_cell(cells_x, **top):
            d = {"exploratory": True, "inputs_sha256": {"e1_l1_registered": l1_sha}, "cells": {"cell-x": cells_x}}
            d.update(top)
            return d

        ul = doc["cells"]["cell-x"]["under_limit"]
        at = doc["cells"]["cell-x"]["all_trials"]
        for name, bad in (("not marked as exploratory", dict(doc, exploratory=False)),
                          ("made against another registered result file",
                           with_cell(doc["cells"]["cell-x"], inputs_sha256={"e1_l1_registered": "0" * 64})),
                          ("taken from another matching output", with_cell({"under_limit": dict(
                              ul, match_sha256="c" * 64), "all_trials": at})),
                          ("with a number of pairs that differs from the registered results", with_cell(
                              {"under_limit": ul, "all_trials": dict(at, slow=entry(11))})),
                          ("without a scope", with_cell({"under_limit": ul})),
                          ("with a speed the registered results lack", with_cell(
                              {"under_limit": ul, "all_trials": dict(at, mid=entry(3))})),
                          ("with percentiles out of order", with_cell(
                              {"under_limit": dict(ul, slow=entry(10, 50.0)), "all_trials": at})),
                          ("with fractions out of order", with_cell(
                              {"under_limit": dict(ul, slow=entry(10, -1500.4, 0.9)), "all_trials": at})),
                          ("with a figure that is not a number", with_cell(
                              {"under_limit": dict(ul, slow=dict(entry(10), within={"abs_le_100_ms": None,
                                                                                    "abs_le_1000_ms": 0.8})),
                               "all_trials": at})),
                          ("with figures for a speed without pairs", with_cell(
                              {"under_limit": dict(ul, mid=dict(entry(0))), "all_trials": at}))):
            write(bad)
            try:
                read_dwell_explore(tmp, cell)
                check("dwellx: a file %s is refused" % name, False)
            except ValueError:
                check("dwellx: a file %s is refused" % name, True)
            except Exception as ex:
                check("dwellx: a file %s is refused" % name, False, repr(ex))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_definitions(check):
    """Deviation D12: the detection under the common definition, for a cell whose two definitions differ, on a synthetic
    Windows cell (they differ) and a synthetic macOS cell (they hold the same sessions)."""
    close = lambda a, b: a is not None and len(a) == len(b) and all(abs(x - y) < 1e-9 for x, y in zip(a, b))
    tmp = tempfile.mkdtemp(prefix="results_tables_defs_")
    try:
        _fixture(tmp)
        files = build(tmp)
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        sup = files["detection-definitions.tex"].decode("utf-8")
        g = lambda k: (vals.get("windows-test.defs." + k) or {}).get("value")
        check("definitions: the sessions of the common definition and those that the definition of the analysis "
              "leaves out, per scope",
              (g("primary.common.sessions"), g("primary.leftout"), g("all.common.sessions"), g("all.leftout"))
              == (210, 10, 212, 12), str([g(k) for k in ("primary.common.sessions", "primary.leftout")]))
        check("definitions: the measures are those of the common definition in each scope, not of the analysis",
              close(g("primary.common.recall"), [0.86, 0.81, 0.91]) and close(g("all.common.precision"),
                                                                              [0.76, 0.71, 0.81])
              and close(g("primary.common.mergerate"), [0.0, 0.0, 0.1])
              and close(vals["windows-test.pooled.gtobs.underlimit.recall"]["value"], [0.9, 0.85, 0.95]),
              str((g("primary.common.recall"), g("all.common.precision"))))
        check("definitions: the ranges over the strata under the common definition follow the rule of the abstract "
              "(at least 30 trials) and are read from the common definition's strata",
              g("primary.common.recall.range") is not None
              and close(g("primary.common.recall.range"), [0.55, 0.95]) and g("primary.common.recall.strata.left") == 4
              and close(vals["windows-test.primary.recall.range"]["value"], [0.60, 0.97])
              and close(g("all.common.precision.range"), [0.0, 1.0])
              and close(vals["windows-test.all.precision.range"]["value"], [0.55, 1.0])
              and g("all.common.precision.strata.left") == 0,
              str((g("primary.common.recall.range"), g("all.common.precision.range"))))
        check("definitions: the supplement gives the ranges over the strata of both definitions with the strata left "
              "out, in the order of the columns, for both measures",
              "Recall, range over the strata & 0.600--0.970\\newline (4 left out) & 0.550--0.950\\newline (4 left out) & "
              "0.000--1.000\\newline (0 left out) & 0.000--1.000\\newline (0 left out) \\\\" in sup
              and "Precision, range over the strata & 0.600--0.970\\newline (4 left out) & 0.550--0.950\\newline "
              "(4 left out) & 0.550--1.000\\newline (1 left out) & 0.000--1.000\\newline (0 left out) \\\\" in sup,
              sup[-900:])
        # Within the ranges: S1 fast and mid differ in the primary scope (by 0.027 and 0.033), S1 mid and slow over all
        # trials (by 0.080 and 0.067); precision has no value in S2 slow, so one stratum fewer is compared.
        check("definitions: the strata whose estimate changes with the definition are counted, with the largest change",
              (g("primary.change.recall.strata"), g("primary.change.recall.of")) == (2, 2)
              and abs(g("primary.change.recall.max") - (22 / 30.0 - 21 / 30.0)) < 1e-9
              and (g("all.change.precision.strata"), g("all.change.precision.of")) == (2, 5)
              and (g("all.change.precision.onlyone"), g("all.change.recall.onlyone"),
                   g("primary.change.recall.onlyone")) == (1, 0, 0)
              and abs(g("all.change.precision.max") - (60 / 75.0 - 54 / 75.0)) < 1e-9,
              str([g(k) for k in ("primary.change.recall.strata", "primary.change.recall.of",
                                  "primary.change.recall.max", "all.change.precision.strata",
                                  "all.change.precision.of", "all.change.precision.max")]))
        check("definitions: the supplement gives both definitions in both scopes, the analysis's first",
              "Measure & \\texttt{windows\\_def} & \\texttt{all} & \\texttt{windows\\_def} & \\texttt{all} \\\\" in sup
              and sup.find("\\multicolumn{2}{l}{\\texttt{under\\_limit}}")
              < sup.find("\\multicolumn{2}{l}{\\texttt{all\\_trials}}")
              and "Reference sessions & 200 & 210 & 200 & 212 \\\\" in sup
              and "Recall & 0.900\\newline [0.850, 0.950] & 0.860\\newline [0.810, 0.910] & 0.800\\newline "
                  "[0.750, 0.850] & 0.760\\newline [0.710, 0.810] \\\\" in sup, sup[-1500:])
        # The common definition cannot hold fewer sessions than the definition of the analysis.
        detp = os.path.join(tmp, "windows", "e1_detection.json")
        with open(detp, "rb") as f:
            original = f.read()
        _rewrite(detp, lambda d: d["cells"]["windows-test"][COMMON_DEFINITION][PRIMARY_COMPARISON]["all_trials"][
            "metrics"]["pooled"].update({"n_gt": 199}))
        _expect_stop(check, "definitions: negative: fewer sessions under the common definition stops", tmp)
        with open(detp, "wb") as f:
            f.write(original)
        _rewrite(detp, lambda d: d["cells"]["windows-test"].pop(COMMON_DEFINITION))
        _expect_stop(check, "definitions: negative: no result under the common definition stops", tmp)
        with open(detp, "wb") as f:
            f.write(original)
        # No stratum of the common definition in the primary scope: nothing is compared, the largest change has no
        # value, the strata of the analysis enter one range only, and nothing stops.
        _rewrite(detp, lambda d: d["cells"]["windows-test"][COMMON_DEFINITION][PRIMARY_COMPARISON]["under_limit"][
            "metrics"].update({"by_stratum": []}))
        try:
            v1 = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
            gv = lambda k: (v1.get("windows-test.defs." + k) or {}).get("text")
            got = (gv("primary.change.recall.of"), gv("primary.change.recall.max"),
                   gv("primary.change.recall.onlyone"), gv("primary.common.recall.range"),
                   gv("primary.change.onlyone.note"))
        except Exception as ex:  # a crash is a failure of this check, not of the selftest
            got = repr(ex)
        check("definitions: with no stratum to compare, the counts are zero and the largest change has no value",
              got == ("0", NO_VALUE, "2", NO_VALUE,
                      "2 strata entered the range of recall and 2 that of precision under one definition only"),
              str(got))
        with open(detp, "wb") as f:
            f.write(original)
        # Without the report of whether the definitions hold the same sessions, the supplement cannot place the cell.
        _rewrite(detp, lambda d: d.pop("same_sessions_as_all"))
        _expect_stop(check, "definitions: negative: no report of whether the definitions hold the same sessions stops",
                     tmp)
        with open(detp, "wb") as f:
            f.write(original)
        # 0 is false but not a report of the analysis, which writes true or false.
        _rewrite(detp, lambda d: d["same_sessions_as_all"].update({"windows-test": 0}))
        _expect_stop(check, "definitions: negative: a report that is not true or false (0) stops", tmp)
        with open(detp, "wb") as f:
            f.write(original)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="results_tables_defs_macos_")
    try:
        _fixture(tmp, "macos-test", "")
        files = build(tmp)
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        sup = files["detection-definitions.tex"].decode("utf-8")
        check("definitions: a cell whose two definitions hold the same sessions has no value and no table, and the "
              "supplement says so",
              not any(".defs." in k for k in vals) and "longtable" not in sup
              and "held the same sessions in the cell macos-test." in sup
              and "No cell had sessions that only one of the two definitions held." in sup, sup[-800:])
        detp = os.path.join(tmp, "e1_detection.json")
        with open(detp, "rb") as f:
            original = f.read()
        # A cell that gets no table is checked too.
        _rewrite(detp, lambda d: d.pop("same_sessions_as_all"))
        _expect_stop(check, "definitions: negative: a cell without a table and without the report stops", tmp)
        with open(detp, "wb") as f:
            f.write(original)
        _rewrite(detp, lambda d: d["cells"]["macos-test"][COMMON_DEFINITION][PRIMARY_COMPARISON]["under_limit"][
            "metrics"]["pooled"].update({"n_gt": 201}))
        _expect_stop(check, "definitions: negative: the same sessions reported with different numbers stops", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_decomposition(check):
    """The values of the comparisons side by side, on a synthetic macOS cell (three comparisons, layers) and a
    synthetic Windows cell (the primary comparison only, no layers)."""
    tmp = tempfile.mkdtemp(prefix="results_tables_decomp_")
    try:
        _fixture(tmp, "macos-test", "")
        vals = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
        t = lambda k: (vals.get("macos-test." + k) or {}).get("text")
        check("decomposition: the span of the limits of agreement over the speeds is read per comparison",
              (t("gtobs.primary.dwell.loa.span"), t("gtpred.primary.dwell.loa.span"), t("predobs.primary.dwell.loa.span"))
              == ("$-$1,800 to 800~ms", "$-$1,799 to 801~ms", "$-$1,798 to 802~ms"),
              str((t("gtobs.primary.dwell.loa.span"), t("gtpred.primary.dwell.loa.span"))))
        check("decomposition: the span of the median absolute differences is read per comparison over every speed, "
              "and a span whose two ends are the same is one value",
              (t("gtobs.primary.dwell.mae.span"), t("gtpred.primary.dwell.mae.span"),
               t("predobs.primary.dwell.mae.span"), t("predobs.primary.dwell.speeds.used"))
              == ("300~ms", "20 to 40~ms", "30 to 50~ms", "3"),
              str((t("gtobs.primary.dwell.mae.span"), t("gtpred.primary.dwell.mae.span"),
                   t("predobs.primary.dwell.mae.span"))))
        # A speed with one pair has a bias but no limits: the limits come from the other speeds, and nothing stops.
        l1p = os.path.join(tmp, "e1_l1.json")
        with open(l1p, "rb") as f:
            original = f.read()

        def one_pair(doc):
            for e in doc["cells"]["macos-test"]["macos_def"]["pred_obs"][PRIMARY_SCOPE]["metrics"]["by_speed"]:
                if e["stratum"]["speed"] == "slow":
                    e["dwell"]["n"] = 1
                    e["dwell"]["bland_altman"].update({"n": 1, "sd": None, "loa_lo": None, "loa_hi": None})
        _rewrite(l1p, one_pair)
        try:
            v1 = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
            got = ((v1.get("macos-test.predobs.primary.dwell.loa.span") or {}).get("text"),
                   (v1.get("macos-test.predobs.primary.dwell.speeds.used") or {}).get("text"),
                   (v1.get("macos-test.predobs.primary.dwell.mae.span") or {}).get("text"))
        except Exception as ex:  # a crash is a failure of this check, not of the selftest
            got = repr(ex)
        check("decomposition: a speed without limits of agreement is left out of their span and counted",
              got == ("$-$1,398 to 802~ms", "2", "30 to 50~ms"), str(got))
        with open(l1p, "wb") as f:
            f.write(original)

        def all_one_pair(doc):
            for e in doc["cells"]["macos-test"]["macos_def"]["pred_obs"][PRIMARY_SCOPE]["metrics"]["by_speed"]:
                e["dwell"]["n"] = 1
                e["dwell"]["bland_altman"].update({"n": 1, "sd": None, "loa_lo": None, "loa_hi": None})
        _rewrite(l1p, all_one_pair)
        try:
            v1 = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
            got = ((v1.get("macos-test.predobs.primary.dwell.loa.span") or {}).get("text"),
                   (v1.get("macos-test.predobs.primary.dwell.speeds.used") or {}).get("text"),
                   (v1.get("macos-test.predobs.primary.dwell.mae.span") or {}).get("text"))
        except Exception as ex:  # a crash is a failure of this check, not of the selftest
            got = repr(ex)
        check("decomposition: with no speed that has limits of agreement, the span has no value and the median "
              "absolute differences are still given", got == (NO_VALUE, "0", "30 to 50~ms"), str(got))
        with open(l1p, "wb") as f:
            f.write(original)
        check("decomposition: each layer is read from its own comparison, in the primary scope, with the trials of the "
              "plan in the layer",
              t("layer.none.gtobs.primary.recall") == "0.525 [0.330, 0.700]"
              and t("layer.none.gtpred.primary.recall") == "0.525 [0.430, 0.800]"
              and t("layer.carriedout.gtobs.primary.precision") == "0.500 [0.300, 0.700]"
              and t("layer.none.gtobs.plan.trials") == "13" and t("layer.none.gtobs.primary.trials") is None
              and t("layer.none.predobs.primary.recall") == "0.525 [0.530, 0.900]",
              str((t("layer.none.gtobs.primary.recall"), t("layer.none.gtpred.primary.recall"),
                   t("layer.none.predobs.primary.recall"))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="results_tables_decomp_win_")
    try:
        _fixture(tmp)
        vals = json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
        check("decomposition: a cell without the prediction has the span of its primary comparison and no layer",
              (vals.get("windows-test.gtobs.primary.dwell.loa.span") or {}).get("text") == "$-$1,800 to 800~ms"
              and not any(".layer." in k or ".predobs." in k for k in vals if k.startswith("windows-test.")),
              str((vals.get("windows-test.gtobs.primary.dwell.loa.span") or {}).get("text")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_macos(check):
    """Table 8 with the three comparisons, the layers of the prediction and the corrected merge rate of D10 on a
    synthetic macOS cell."""
    tmp = tempfile.mkdtemp(prefix="results_tables_macos_")
    try:
        _fixture(tmp, "macos-test", "")
        files = build(tmp)
        t8 = files["table-detection.tex"].decode("utf-8")
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        check("table 8: a macOS cell has the three comparisons in order, each from its own results",
              t8.find("Reference vs. logger (primary)") < t8.find("Reference vs. specified rows")
              < t8.find("Specified rows vs. logger")
              and "& Reference vs. specified rows & slow & " in t8
              and " & mid & 0.810--0.990 (1/2) & 0.810--0.990 (1/2) & " in t8
              and " & mid & 0.500--0.950 (1/2) & 0.500--0.950 (1/2) & " in t8, t8[-2500:])
        check("table 8: the values of the comparisons with the prediction follow the rule of the abstract",
              vals["macos-test.gtpred.primary.recall.range"]["value"] == [0.5, 0.95]
              and vals["macos-test.gtpred.primary.recall.strata.left"]["value"] == 4
              and vals["macos-test.predobs.primary.precision.range"]["value"] == [0.81, 0.99]
              and "macos-test.gtobs.primary.recall.range" not in vals,
              str(vals.get("macos-test.gtpred.primary.recall.range")))
        ss = files["session-strata.tex"].decode("utf-8")
        check("session strata: every comparison and both scopes, each from its own results, with the rows per speed "
              "and the pooled row",
              ss.count("\\begin{longtable}") == 6 and "\\label{tab:session-macos-test-gtpred-underlimit}" in ss
              and ss.find("tab:session-macos-test-gtobs-underlimit") < ss.find("S1 & slow & 50 & $-$899 [")
              < ss.find("tab:session-macos-test-gtpred-underlimit") < ss.find("S1 & slow & 50 & $-$898 [")
              and "all & slow & 50 & $-$900 [" in ss and "pooled &  & 50 & $-$400 [" in ss
              and "pooled &  & 50 & $-$405 [" in ss, ss[:2000])
        st = files["detection-strata.tex"].decode("utf-8")
        check("strata: the composition of the scopes from the plan, in the order of the scopes, and the comparisons "
              "with the prediction in every scope",
              st.find("S1 & slow & \\texttt{under\\_limit} & 75 & 12 (4 to 20) & 1{,}700 (200 to 19{,}000)")
              < st.find("S1 & slow & \\texttt{over\\_limit} & 75 & 11 (4 to 20)") and "tab:composition-macos-test" in st
              and "\\label{tab:strata-macos-test-predobs-overlimit}" in st
              and st.find("tab:strata-macos-test-gtpred-underlimit") < st.find("S1 & fast & 75 & 75 & 75 & 0.800 [0.700, "
                                                                               "0.800]"), st[:1500])
        check("diagnostics: the summary of the prediction and the pooled measures of every comparison and scope",
              vals["macos-test.pred.trials.long"]["value"] == 6 and vals["macos-test.pred.rows.truncated"]["value"] == 4
              and vals["macos-test.samesessions"]["text"] == "yes"
              and abs(vals["macos-test.pooled.gtpred.overlimit.recall"]["value"][0] - 0.3) < 1e-9
              and "macos-test.win.rows.25000ormore" not in vals,
              str(vals.get("macos-test.pooled.gtpred.overlimit.recall")))
        lay = files["detection-layers.tex"].decode("utf-8")
        check("layers: every comparison and scope, the four layers in the order of the registration with their "
              "trials, per speed and pooled, and the corrected column says that it was not computed",
              lay.count("\\texttt{over\\_limit}") == 3 and lay.count("received carry (11)") == 9
              and lay.find("carried out (10)") < lay.find("received carry (11)") < lay.find("closing key in reading (12)")
              < lay.find("none of the three (13)")
              and "& fast & \\multicolumn{7}{l}{not in the result file}" in lay
              and "the corrected analysis was not computed for this cell" in lay, lay[:1500])
        check("layers: a merge rate of deviation D10 is printed as no value",
              "received carry (11) & slow & 40 & 40 & 0.525 [0.310, 0.700] & 0.525 [0.310, 0.700] & 0.000 & %s & %s" % (
                  NO_VALUE, NO_VALUE) in lay, lay[:3000])
        d10d = os.path.join(tmp, "d10", "macos")
        os.makedirs(d10d)
        cor = load(os.path.join(tmp, "e1_detection.json"))
        blocks = cor["cells"]["macos-test"]["macos_def"]
        for comp in blocks:
            for scope in blocks[comp]:
                blocks[comp][scope]["config"]["merge_recount"] = "obs"
        blocks[PRIMARY_COMPARISON]["under_limit"]["diagnostics"]["prediction_layers"]["received_carry"]["by_speed"][0][
            "merge_rate"] = {"value": 0.5, "lo": 0.1, "hi": 0.9, "n": 4, "x": 2}
        blocks["gt_pred"]["under_limit"]["metrics"]["by_stratum"][0]["merge_rate"] = {"value": 0.75, "lo": 0.3, "hi": 0.9,
                                                                                       "n": 4, "x": 3}
        with open(os.path.join(d10d, "e1_detection.json"), "wb") as f:
            f.write(json.dumps(cor).encode("utf-8"))
        files = build(tmp)
        lay = files["detection-layers.tex"].decode("utf-8")
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        st = files["detection-strata.tex"].decode("utf-8")
        i_pred = st.find("tab:strata-macos-test-gtpred-underlimit")
        check("strata: the corrected merge rate is that of the same comparison",
              st.count("& 0.000 & 0.750 \\\\") == 1 and i_pred < st.find("& 0.000 & 0.750 \\\\")
              < st.find("tab:strata-macos-test-gtpred-alltrials"), st[i_pred:i_pred + 1200])
        check("layers: the corrected merge rate stands next to the registered one, from the same comparison, scope, "
              "layer and speed",
              "received carry (11) & slow & 40 & 40 & 0.525 [0.310, 0.700] & 0.525 [0.310, 0.700] & 0.000 & %s & "
              "0.500 \\\\" % NO_VALUE in lay and lay.count("& 0.500 \\\\") == 1, lay[:3000])
        check("d10: the merge rates of the layers without a value in the registered analysis are counted",
              vals["macos-test.d10.layers.novalue"]["value"] == 1, str(vals.get("macos-test.d10.layers.novalue")))
        del blocks["pred_obs"]["over_limit"]["config"]["merge_recount"]
        with open(os.path.join(d10d, "e1_detection.json"), "wb") as f:
            f.write(json.dumps(cor).encode("utf-8"))
        _expect_stop(check, "d10: negative: a corrected comparison or scope without the switch stops", tmp)
        shutil.rmtree(d10d)
        detp = os.path.join(tmp, "e1_detection.json")
        good_det = load(detp)
        _rewrite(detp, lambda doc: doc["cells"]["macos-test"]["macos_def"]["gt_pred"]["over_limit"]["excluded"][
            "obs"].update({"n": 9}))
        _expect_stop(check, "diagnostics: negative: exclusions that differ between comparisons or scopes stop", tmp)

        def side(kind, n):
            def edit(doc):
                for blk in doc["cells"]["macos-test"]["macos_def"]["pred_obs"].values():
                    blk["excluded"][kind]["n"] = n
            return edit
        with open(detp, "wb") as f:
            f.write(json.dumps(good_det).encode("utf-8"))
        _rewrite(detp, side("gt", 5))
        try:
            ok = "macos-test.excluded.gt.n" in json.loads(build(tmp)["values.json"].decode("utf-8"))["values"]
        except ValueError as e:
            ok = repr(e)
        check("diagnostics: the first side of pred_obs (the prediction) may exclude other rows than the reference",
              ok is True, str(ok))
        with open(detp, "wb") as f:
            f.write(json.dumps(good_det).encode("utf-8"))
        _rewrite(detp, side("trials", 9))
        _expect_stop(check, "diagnostics: negative: trials excluded differently in another comparison stop", tmp)
        with open(detp, "wb") as f:
            f.write(json.dumps(good_det).encode("utf-8"))
        l1p = os.path.join(tmp, "e1_l1.json")
        good_l1 = load(l1p)
        _rewrite(l1p, lambda doc: doc["cells"]["macos-test"]["macos_def"].pop("pred_obs"))
        _expect_stop(check, "session strata: negative: per-session results without a comparison of the detection "
                            "results stop", tmp)
        # Both files without pred_obs: only the check of the comparisons of the system can stop it.
        _rewrite(detp, lambda doc: doc["cells"]["macos-test"]["macos_def"].pop("pred_obs"))
        _expect_stop(check, "table 8: negative: a macOS cell without a comparison of the registration stops", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def selftest():
    fails, ran = [], []

    def check(name, cond, detail=""):
        ran.append(name)
        if not cond:
            fails.append(name)
            print("FAIL: %s %s" % (name, detail))

    _selftest_rules(check)
    _selftest_reference(check)
    _selftest_macos(check)
    _selftest_figures(check)
    _selftest_spread(check)
    _selftest_dwell_explore(check)
    _selftest_definitions(check)
    _selftest_decomposition(check)

    tmp = tempfile.mkdtemp(prefix="results_tables_selftest_")
    try:
        _fixture(tmp)
        files = build(tmp)
        vals = json.loads(files["values.json"].decode("utf-8"))["values"]
        check("the cell under windows/ is found and read with windows_def", "windows-test.trials.total" in vals,
              str(sorted(vals)[:3]))
        check("the total counts every trial of the design (kept, excluded, out of scope)",
              vals["windows-test.trials.total"]["value"] == 75 * 6, str(vals["windows-test.trials.total"]))
        check("a stratum at the minimum of trials (30) is in the range; one under it (29) and strata without trials "
              "are left out",
              vals["windows-test.primary.recall.strata.used"]["value"] == 2
              and vals["windows-test.primary.recall.strata.left"]["value"] == 4,
              str((vals["windows-test.primary.recall.strata.used"], vals["windows-test.primary.recall.strata.left"])))
        check("the primary range is the lowest lower end to the highest upper end of the strata used",
              vals["windows-test.primary.recall.range"]["value"] == [0.60, 0.97]
              and vals["windows-test.primary.recall.range"]["text"] == "0.600--0.970",
              str(vals["windows-test.primary.recall.range"]))
        check("the range over all trials uses the strata of all trials, per measure: a stratum without a value of "
              "precision (no session row) stays in the range of recall (its recall of 0) and is left out of precision",
              vals["windows-test.all.recall.range"]["value"] == [0.0, 1.0]
              and vals["windows-test.all.precision.range"]["value"] == [0.55, 1.0]
              and vals["windows-test.all.recall.strata.left"]["value"] == 0
              and vals["windows-test.all.precision.strata.left"]["value"] == 1,
              str((vals["windows-test.all.recall.range"], vals["windows-test.all.precision.range"])))
        names = dict((k, (vals.get("windows-test.%s.strata.leftnames" % k) or {}).get("text"))
                     for k in ("primary.recall", "all.recall", "all.precision"))
        check("the strata left out of a range are named in the order of the design, grouped by scenario, as many as "
              "are counted",
              names["primary.recall"] == "S1 (slow), S2 (slow, middle, fast)" and names["all.recall"] == "none"
              and re.match(r"^S\d+ \((slow|middle|fast)\)$", names["all.precision"] or "") is not None, str(names))
        order = strata_names([("S6", "slow"), ("S10", "fast"), ("S13", "slow"), ("S13", "mid")])
        check("the names keep the order given (the order of the design, not of the strings)",
              order == "S6 (slow), S10 (fast), S13 (slow, middle)", order)
        check("a stratum whose rates have no value is printed with the mark of no value, not dropped",
              "S2 & slow & 75 & 75 & 0 & 0.000 [0.000, 0.049] & %s" % NO_VALUE in files["detection-strata.tex"].decode("utf-8"))
        check("the fractions set aside by the scope and by carry-over are counts over the total",
              vals["windows-test.scope.setaside"]["value"] == [315, 450]
              and vals["windows-test.scope.setaside"]["text"] == "315 of 450 trials (70.0\\%)"
              and vals["windows-test.carryover"]["value"] == [1, 450], str(vals["windows-test.scope.setaside"]))
        check("the same fractions as percentages alone, for the abstract",
              vals.get("windows-test.scope.setaside.pct", {}).get("text") == "70.0\\%"
              and vals.get("windows-test.carryover.pct", {}).get("text") == "0.2\\%"
              and vals.get("windows-test.carryover.pct", {}).get("source") == vals["windows-test.carryover"]["source"],
              str((vals.get("windows-test.scope.setaside.pct"), vals.get("windows-test.carryover.pct"))))
        check("the dwell-bias range spans the per-speed intervals, in whole milliseconds with a minus sign",
              vals["windows-test.primary.dwellbias.range"]["value"] == [-1050.6, -49.5]
              and vals["windows-test.primary.dwellbias.range"]["text"] == "$-$1,051 to $-$50~ms",
              str(vals["windows-test.primary.dwellbias.range"]))
        check("every value of questions 1 to 3 names its source file, and every value has a source",
              all(v["source"].startswith("windows/e1_") for k, v in vals.items()
                  if k.startswith("windows-test.") and ".q4." not in k and ".d10." not in k)
              and all(v["source"] for v in vals.values()))
        strata_tex = files["detection-strata.tex"].decode("utf-8")
        check("the supplement names the expected cells that were not available, by their names",
              all(cell_name(c) in strata_tex for c in EXPECTED_CELLS) and "macos-standard" not in strata_tex,
              strata_tex[:300])
        check("the per-stratum table states a stratum without sessions instead of leaving it out",
              "no trial in the scope" in strata_tex and "S2 & fast" in strata_tex)
        check("the generated files say that they are generated", all(
            data.decode("utf-8").startswith("% Generated") for n, data in files.items() if n.endswith(".tex")))
        t10 = files["table-text-agreement.tex"].decode("utf-8")
        check("q3: a duration is in whole milliseconds with a minus sign, and the bias, the lower and the upper limit "
              "and the ICC each come with their own interval",
              "Mean conversion time (ms) & slow & 40 & 117 & $-$1,234 [$-$1,235, $-$1,233] & $-$2,134 [$-$2,135, $-$2,133] "
              "& $-$534 [$-$535, $-$533] & 0.750 [0.500, 0.875] \\\\" in t10, t10[-1500:])
        row = [l for l in t10.splitlines() if "Conversions per trial & slow" in l]
        check("q3: an estimate whose interval is withheld is printed alone, not dropped",
              len(row) == 1 and row[0].endswith("& 0.750 \\\\"), str(row))
        check("q3: a measure with no text in the scope is stated",
              "Abandonment rate & slow & \\multicolumn{6}{l}{no text in the scope}" in t10)
        check("q3: proportions keep three decimals with a minus sign, after the reference's SD",
              "Fraction of time converting & slow & 40 & 0.012 & $-$0.01" in t10)
        mid = [l for l in t10.splitlines() if " & mid & " in l]
        d11_cell = "& %s; registered 0.750 [0.500, 0.875]) \\\\" % REFERENCE_CONSTANT
        check("q3: an ICC whose reference is the same in every text has no value, with the reason, the registered "
              "result next to it (deviation D11; section 8 of the registration), and its SD is zero",
              len(mid) >= 1 and mid[0].startswith(" &  & mid & 40 & 0.000 & ") and mid[0].endswith(d11_cell)
              and REFERENCE_CONSTANT_NOTE in t10
              and sum(1 for l in t10.splitlines() if l.endswith(d11_cell)) == len(row), str(mid[:2]))
        cst = dict((k, vals.get("q3.conversions_per_trial.%s" % k) or {}) for k in
                   ("constant.strata", "constant.of", "onetext.strata"))
        check("q3: the text's count of strata whose reference is the same in every text is the number of such ICCs "
              "in the table, out of the strata of conversions per trial that have a value, as a word",
              cst["constant.strata"].get("value") == len(row) and cst["constant.strata"].get("text") == number_word(
                  len(row))
              and cst["constant.of"].get("value") == sum(1 for k in vals if re.match(
                  r"^[a-z-]+\.l2\.conversions_per_trial\.(slow|mid|fast)\.icc$", k))
              and cst["onetext.strata"].get("value") == 0, str(cst))
        check("figure 2 names the scope in words, not by the identifier of the result files",
              "under\\_limit" not in files["fig-detection.tex"].decode("utf-8")
              and "primary scope" in files["fig-detection.tex"].decode("utf-8"))
        icc_mid = [v for k, v in vals.items() if k.endswith(".l2.conversions_per_trial.mid.icc")]
        check("q3: the value of such an ICC for the text says why it has none, holds no number, and keeps the "
              "registered result next to it",
              icc_mid and all(v["text"] == REFERENCE_CONSTANT_TEXT and v["value"] == [None, None, None]
                              and v.get("registered") == [0.75, 0.5, 0.875] for v in icc_mid), str(icc_mid[:1]))
        sup_q3 = files["text-agreement.tex"].decode("utf-8")
        check("q3: every supplementary table of per-text agreement explains a reference-constant ICC",
              sup_q3.count(REFERENCE_CONSTANT_NOTE) == 2 * len(row), sup_q3.count(REFERENCE_CONSTANT_NOTE))
        check("q3: a value that rounds to zero is printed without a sign",
              fmt_signed(-1.6e-16) == "0.000" and fmt_signed(-0.0004) == "0.000" and fmt_signed(-0.0006) == "$-$0.001"
              and fmt_signed(-0.4, True) == "0", (fmt_signed(-1.6e-16), fmt_signed(-0.0006), fmt_signed(-0.4, True)))
        icc_slow = [v for k, v in vals.items() if k.endswith(".l2.conversions_per_trial.slow.icc")]
        check("q3: an ICC whose reference varies keeps its value",
              icc_slow and all(v["value"][0] == 0.75 for v in icc_slow), str(icc_slow[:1]))
        sup = files["text-agreement.tex"].decode("utf-8")
        check("q3: the views that need the layers are stated as not computed for a Windows cell",
              sup.count("not computed for this cell (no layers of the prediction)") == 2, sup[:400])
        check("q3: the per-trial differences give the mean difference with its interval and the mean on each side, "
              "and a stratum without trials is stated",
              "$-$0.500 [$-$0.750, $-$0.250] (1.500; 2.000)" in sup and "\\multicolumn{3}{l}{no trial}" in sup)
        check("q3: the pooled values and the three mixtures are given for both scopes",
              sup.count("\\texttt{under\\_limit}") >= 4 and sup.count("\\texttt{all\\_trials}") >= 4
              and "Bias, 0.6/0.3/0.1" in sup and "Bias, equal" in sup)
        check("q3: the values for the text name their source",
              vals["windows-test.l2.mean_dwell_ms.slow.bias"]["text"] == "$-$1,234 [$-$1,235, $-$1,233]"
              and "agreement_by_stratum/slow/mean_dwell_ms" in vals["windows-test.l2.mean_dwell_ms.slow.bias"]["source"],
              str(vals.get("windows-test.l2.mean_dwell_ms.slow.bias")))
        p2 = os.path.join(tmp, "windows", "e1_l2.json")
        good = load(p2)
        bad = json.loads(json.dumps(good))
        bad["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]["all_trials"]["mixture_bias"]["mixtures"][
            "uniform"]["weights"]["slow"] = 0.5
        with open(p2, "wb") as f:
            f.write(json.dumps(bad).encode("utf-8"))
        try:
            build(tmp)
            check("q3: negative: a mixture whose weights are not those of section 6.7 stops the script", False)
        except ValueError:
            check("q3: negative: a mixture whose weights are not those of section 6.7 stops the script", True)
        with open(p2, "wb") as f:
            f.write(json.dumps(good).encode("utf-8"))
        check("the same input gives byte-identical output", build(tmp) == files)
        out = os.path.join(tmp, "out")
        check("writing exits 0", write_or_check(files, out, False) == 0)
        check("--check passes on the files just written", write_or_check(files, out, True) == 0)
        with open(os.path.join(out, "table-detection.tex"), "ab") as f:
            f.write(b"% edited\n")
        check("negative: --check fails when a table was edited by hand", write_or_check(files, out, True) == 1)
        os.remove(os.path.join(out, "values.tex"))
        check("negative: --check fails when a file is missing", write_or_check(files, out, True) == 1)
        write_or_check(files, out, False)
        with open(os.path.join(out, "table-old.tex"), "wb") as f:
            f.write(b"% a table that is no longer generated\n")
        check("negative: --check fails when a file that is no longer generated is still there",
              write_or_check(files, out, True) == 1)
        os.remove(os.path.join(out, "table-old.tex"))
        check("--check passes again once that file is gone", write_or_check(files, out, True) == 0)
        check("q4: a cell without a closed-set file is reported as not measured",
              "windows-test & \\multicolumn{7}{l}{not measured}" in files["table-closed-set.tex"].decode("utf-8"))
        check("d10: without the corrected analysis the column says so",
              "(not computed for this cell)" in files["detection-strata.tex"].decode("utf-8"))
        cs = os.path.join(tmp, "windows", closed_set_name("windows-test"))
        with open(cs, "wb") as f:
            f.write(json.dumps(_cs_doc(skipped=False)).encode("utf-8"))
        try:
            build(tmp)
            check("q4: negative: a closed-set file that keeps S12 (not deviation D8) stops the script", False)
        except ValueError:
            check("q4: negative: a closed-set file that keeps S12 (not deviation D8) stops the script", True)
        for name, edit in (("a closed-set file that also leaves out another scenario",
                            lambda d: d["skipped_scenarios"].update({"S2": 10})),
                           ("a closed-set file whose S12 count is zero", lambda d: d["skipped_scenarios"].update({"S12": 0})),
                           ("a closed-set file with trials of S12 in a result",
                            lambda d: d["results"]["100"]["no_time"]["by_scenario"].update({"S12": {"acc": 0.5, "n": 3}})),
                           ("a closed-set file without the count of unscored blocks",
                            lambda d: d["alignment"].pop("n_blocks_unscored")),
                           ("a closed-set file whose count of unscored blocks is true",
                            lambda d: d["alignment"].update({"n_blocks_unscored": True})),
                           ("a closed-set file whose count of unscored trials is negative",
                            lambda d: d["alignment"].update({"n_trials_unscored": -1}))):
            doc = _cs_doc(_near(RULE_SETS))
            edit(doc)
            with open(cs, "wb") as f:
                f.write(json.dumps(doc).encode("utf-8"))
            _expect_stop(check, "q4: negative: %s stops" % name, tmp)
        with open(cs, "wb") as f:
            f.write(json.dumps(_cs_doc(_near(RULE_SETS))).encode("utf-8"))
        files_cs = build(tmp)
        t11 = files_cs["table-closed-set.tex"].decode("utf-8")
        check("q4: the table gives the judgement per set and K, and the recommendation is not decided without the "
              "macOS cells", "\\texttt{no\\_time} & 10 & 100 & " in t11 and t11.count("& yes \\\\") == 4
              and "not decided (a macOS cell was not measured)" in t11, t11[-900:])
        check("open set: a cell without a file is reported as not measured",
              "windows-test & \\multicolumn{8}{l}{not measured}" in files["table-open-set.tex"].decode("utf-8"))
        osp = os.path.join(tmp, "windows", open_set_name("windows-test"))
        with open(osp, "wb") as f:
            f.write(json.dumps(_os_doc(drop=("phrase", "true"))).encode("utf-8"))
        try:
            build(tmp)
            check("open set: negative: a missing split or length level stops the script", False)
        except ValueError:
            check("open set: negative: a missing split or length level stops the script", True)
        with open(osp, "wb") as f:
            f.write(json.dumps(_os_doc()).encode("utf-8"))
        files_os = build(tmp)
        tos = files_os["table-open-set.tex"].decode("utf-8")
        vos = json.loads(files_os["values.json"].decode("utf-8"))["values"]
        check("open set: K1+ is K1 with the corpus split by phrase, and the gain and the gain over the shuffled "
              "control are not interchanged",
              "& K1+ & number of segments & 50 & 0.600 [0.550, 0.650] & 1.000 [0.950, 1.050] & 0.400 [0.350, 0.450] "
              "& 0.410 [0.360, 0.460] & 2 \\\\" in tos
              and vos["windows-test.q4.open.K1plus.count.cer"]["text"] == "0.600 [0.550, 0.650]"
              and vos["windows-test.q4.open.K1.true.gainvsshuffled"]["text"] == "0.210 [0.160, 0.260]"
              and vos["windows-test.q4.open.K0.phrase.count.cer"]["text"] == "0.950 [0.900, 1.000]", tos[-1200:])
        os.remove(osp)
        d10d = os.path.join(tmp, "d10", "windows")
        os.makedirs(d10d)
        reg = load(os.path.join(tmp, "windows", "e1_detection.json"))
        cor = json.loads(json.dumps(reg))
        blk = cor["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]
        for scope in blk:
            blk[scope]["config"]["merge_recount"] = "obs"
        blk["under_limit"]["metrics"]["by_stratum"][0]["merge_rate"] = {"value": 0.25, "lo": 0.1, "hi": 0.5, "n": 4,
                                                                         "x": 1}
        with open(os.path.join(d10d, "e1_detection.json"), "wb") as f:
            f.write(json.dumps(cor).encode("utf-8"))
        files_d = build(tmp)
        vd = json.loads(files_d["values.json"].decode("utf-8"))["values"]
        check("d10: the strata whose merge rate the corrected analysis changes are counted per scope, and the "
              "supplement shows the corrected rate next to the registered one",
              vd["windows-test.d10.primary.merge.differ"]["value"] == [1, 3]
              and vd["windows-test.d10.all.merge.differ"]["value"] == [0, 6]
              and "& 0.000 & 0.250 \\\\" in files_d["detection-strata.tex"].decode("utf-8"),
              str((vd.get("windows-test.d10.primary.merge.differ"), vd.get("windows-test.d10.all.merge.differ"))))
        saved = json.loads(json.dumps(blk["under_limit"]["metrics"]["by_stratum"][1]))
        blk["under_limit"]["metrics"]["by_stratum"][1]["recall"]["lo"] = 0.01
        with open(os.path.join(d10d, "e1_detection.json"), "wb") as f:
            f.write(json.dumps(cor).encode("utf-8"))
        _expect_stop(check, "d10: negative: corrected results that differ from the registered ones in more than the "
                            "merge rate stop", tmp)
        blk["under_limit"]["metrics"]["by_stratum"][1] = saved
        del blk["all_trials"]["config"]["merge_recount"]
        with open(os.path.join(d10d, "e1_detection.json"), "wb") as f:
            f.write(json.dumps(cor).encode("utf-8"))
        try:
            build(tmp)
            check("d10: negative: corrected results without the switch stop the script", False)
        except ValueError:
            check("d10: negative: corrected results without the switch stop the script", True)
        shutil.rmtree(d10d)
        reg_blk = reg["cells"]["windows-test"]["windows_def"][PRIMARY_COMPARISON]
        reg_blk["under_limit"]["config"]["merge_recount"] = "obs"
        with open(os.path.join(tmp, "windows", "e1_detection.json"), "wb") as f:
            f.write(json.dumps(reg).encode("utf-8"))
        try:
            build(tmp)
            check("d10: negative: registered results that carry the switch stop the script", False)
        except ValueError:
            check("d10: negative: registered results that carry the switch stop the script", True)
        del reg_blk["under_limit"]["config"]["merge_recount"]
        with open(os.path.join(tmp, "windows", "e1_detection.json"), "wb") as f:
            f.write(json.dumps(reg).encode("utf-8"))
        # a primary that is not the registered one stops the script
        p = os.path.join(tmp, "windows", "e1_l1.json")
        doc = load(p)
        doc["primary"]["scope"] = "all_trials"
        with open(p, "wb") as f:
            f.write(json.dumps(doc).encode("utf-8"))
        try:
            build(tmp)
            check("negative: a result file whose primary scope differs stops the script", False)
        except ValueError:
            check("negative: a result file whose primary scope differs stops the script", True)
        empty = tempfile.mkdtemp(prefix="results_tables_empty_")
        try:
            files_e = build(empty)
            check("no result files: no cell, and the tables are written without rows",
                  json.loads(files_e["values.json"].decode("utf-8"))["cells"] == [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(ran)
    if fails:
        print("selftest failed: %d of %d checks" % (len(fails), n))
        return 1
    print("selftest OK (%d checks)" % n)
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="Tables and values of the manuscript from the result files")
    ap.add_argument("--results", help="the results directory (experiment/results)")
    ap.add_argument("--out", help="the output directory (manuscript/generated)")
    ap.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 when any file differs")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.results and a.out):
        ap.print_usage(sys.stderr)
        return 2
    try:
        files = build(a.results)
    except (ValueError, KeyError, OSError) as e:
        print("results_tables.py: %s" % e, file=sys.stderr)
        return 2
    return write_or_check(files, a.out, a.check)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
