#!/usr/bin/env python3
# kit: selftest
"""Open-set reconstruction of the typed reading from a published-form log (Python standard library only).

  open_set.py --obs OBS... --plan PLAN... --corpus CORPUS --split-seed N --out OUT.json
              [--recipes JSON] [--eval-fraction 0.2] [--n 3] [--smoothing wb|add] [--beam 8]
              [--length-info count|true|both] [--split work|phrase|both] [--bootstrap 1000] [--seed 0]
              [--markers-in-obs] [--ime-toggle-in-obs]
  open_set.py --selftest        artificial-corpus checks, including a negative control (fast)

Attack A1 (open set). The attacker sees only the published form of the log: for every key press, its category
(`alphabetic`, `space`, `enter`, `punctuation`, ...). No key identity and no text is available. From the run of
key presses that spells the reading, the attacker recovers the number of romanised keys and the position of
punctuation keys, and searches for the kana string that (i) is spelled by exactly that many keys with the
romanisation table of the stimulus generator and (ii) maximises the probability under a character language
model. The ground truth is the reading that was typed (not the original sentence).

Knowledge levels (fixed here, frozen with the evaluation protocol).
  K0   no knowledge of the genre: a character unigram model estimated from the training phrases.
  K1   knowledge of the genre, not of the individual work (the default, `--split work`): a character n-gram
       model (default n = 3, Witten-Bell smoothing) estimated from the same corpus, with the evaluation
       phrases' whole works held out of training (`split_phrases`, `unit="work"`). No training phrase shares
       a work with an evaluation phrase, and a training phrase whose reading equals an evaluation phrase's
       reading is removed as well (a work can repeat a sentence).
  K1+  a stronger, optional attacker (`--split phrase`): the corpus is split phrase by phrase instead of work
       by work, so training phrases of the same work as an evaluation phrase are kept; only exact reading
       duplicates are removed. This over-states what an attacker without knowledge of the individual work
       could achieve, and is reported alongside K1, not in place of it (`--split both` reports both).

Decoding. The observed runs are segments; a segment is a string over `a` (alphabetic key), `p` (punctuation key)
and `s` (sentence-punctuation key). A beam search (stack decoding by consumed key count, hypotheses with the
same language-model context and pending state recombined) emits kana tokens; a token consumes the keys given
by `romaji` and must match the observed key categories. The doubled-consonant kana is resolved on the next
token (one key when the next token starts with a doubling consonant, otherwise three), and a kana pair that
the table romanises as one unit is emitted as one token. The search maximises the log-probability of the
kana string followed by the end symbol; ties are broken by the smaller string. If no string fits, the
decoder returns the empty string (counted as undecodable and scored as a total error).
The token table is built from `romaji` (imported, not re-implemented), so the attacker's model uses the same
romanisation as the stimulus. The timing of the key presses is not used by this attack.

Baseline. The same language model without the log. A string is drawn from the model prior (`baseline`).
The attacker's knowledge of the length is a choice, fixed by `--length-info` (both are reported by default):
  count  the attacker knows only the number of observed segments; the length of each segment is drawn
         from the empirical distribution of the training-phrase lengths;
  true   the attacker is given the true length of the reading (a stronger prior-only attacker).
The baseline of each level uses the prior of that level's model.

Metric. Character error rate (CER): edit distance (insertion, deletion, substitution) divided by the length
of the typed reading, per trial; it can exceed 1. Reported values are means over trials. Intervals are
percentile intervals from `stats.cluster_bootstrap` with the plan block as the cluster (a trial's cluster is
`(observation index, block_id)`: trials of the same phrase that recur in different blocks are not pooled into
one cluster, so dependence across blocks that share a phrase is not modelled by the interval; the plan does
not currently cluster by phrase). `gain` is the paired per-trial difference (baseline CER minus attack CER):
how much the log improves on the prior. `gain` conflates two things: the log carrying information, and the
attack decoder being maximum-a-posteriori while the baseline is a single draw from the prior. `gain_vs_shuffled`
(below) isolates the first.

Negative control (`shuffled`). Every trial is paired with the decoded reading of another trial whose reading
differs (a seeded draw), and scored against its own typed reading, so the observation carries no information
about the trial. Its `gain` (paired against the baseline) is the level that arises without any information in
the log. `gain_vs_shuffled` is the paired difference between the shuffled decode's CER and the attack's own
CER on the same trial: both are produced by the same maximum-a-posteriori decoder, so this isolates how much
of the advantage comes from the trial's own observation, without the MAP-versus-draw difference that `gain`
carries.

Inputs of the command line. Each `--obs` file (obs/1) and `--plan` file (plan/1) is checked against its format
(`validate_logs.validate` for obs/1, `generate_plan.validate_plan` for plan/1, both imported, not
re-implemented) before it is read; an unknown key, a category outside the fourteen-value enum, a header whose
format is not obs/1, a repeated key on one line and JSON nested past the recursion limit are all refused as
input errors, not read. The key presses of the observation are assigned to the plan in order (block markers
first, then the keys of each trial) by `closed_set.align_plan`, comparing the whole category sequence of every
trial. The block marker keys and the mid-conversion IME toggle keys are not typing keys; by default no row is
expected for them in the observation (`--markers-in-obs` / `--ime-toggle-in-obs` opt in to an observation that does
record them). With `--markers-in-obs` the markers serve as anchors: a block whose presses do not match the plan (a
missing, extra or different press) is left unscored and counted, the blocks after it are found again by their own
markers, and the other blocks are scored, so one dropped press loses one block, never the run and never the
alignment of later blocks. Presses that no scored block covers are counted as unassigned. The report holds an
`alignment` object (blocks, unscored blocks and trials by scenario and by speed, unassigned presses); n, the error
rates and the intervals are computed over the scored trials only. Without `--markers-in-obs` there is no anchor to
resynchronise to, so any mismatch, or any press left over, is refused as an input error, never guessed. The typed reading of a trial comes from the plan (`typed_reading`)
and is used only for scoring; a trial without a typed reading is dropped and counted (`n_no_typed_reading`).
Trials whose scenario types the reading with the kana layout are outside the attack model (the key count does
not follow the romanisation) and are excluded. Only trials of evaluation phrases are scored. The output file
is created, never overwritten (`--out` must not already exist).

The strength of this attack is not claimed to be an upper bound: results hold for this attacker only.
Exit: 0 = success / 1 = selftest failed / 2 = usage error or unreadable / inconsistent input.
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
for _d in (_HERE, os.path.join(_KIT, "gen"), os.path.join(_KIT, "analysis"), os.path.join(_KIT, "formats")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import charlm  # noqa: E402
import closed_set  # noqa: E402  (align_plan, merge_alignments: the assignment of presses to the plan)
import generate_plan  # noqa: E402
import romaji  # noqa: E402
import stats  # noqa: E402
import validate_logs  # noqa: E402

DEFAULT_BEAM = 8
DEFAULT_BOOTSTRAP = 1000
LENGTH_INFO = ("count", "true")
SPLIT_UNITS = ("work", "phrase")
# heuristic only: below this many distinct works, a work-level split may put almost all phrases of a small
# corpus on one side of the split (a warning, not a refusal)
_MIN_WORKS_FOR_SPLIT = 5
_SMALL = "ぁぃぅぇぉゃゅょ"
_PATTERN_RE = re.compile(r"[aps]+")
# categories that make up a run of romanised reading keys, and their symbol in a segment
_RUN_SYMBOL = {"alphabetic": "a", "punctuation": "p", "sentence_punctuation": "s"}
# plan keys that are not keyboard presses (they switch the foreground application or click)
_PSEUDO_KEYS = frozenset(["mouse_click", "focus_away", "focus_back"])
# mid-conversion IME toggle keys: not typing keys either, kept separate from _PSEUDO_KEYS because whether they
# reach the observation is a modelling choice (`--ime-toggle-in-obs`), not a structural fact of the plan
_IME_TOGGLE_KEYS = frozenset(["ime_on", "ime_off"])


class InputError(Exception):
    """The input files are unreadable or inconsistent (exit code 2)."""


# ---------------------------------------------------------------- token table (from romaji)

class _Token(object):
    __slots__ = ("text", "length", "cats", "doublable")

    def __init__(self, text, length, cats, doublable):
        self.text = text
        self.length = length
        self.cats = cats
        self.doublable = doublable


def _key_symbol(key):
    if key == "minus":
        return "p"
    if key in ("comma", "period"):
        return "s"
    return "a"


class TokenTable(object):
    """Kana tokens with their key count and key categories, derived from `romaji` only.

    A token is one kana or a kana pair that the table romanises as one unit (for example a consonant kana
    followed by a small kana). `doublable` tells whether the doubled-consonant kana before the token is
    written by repeating the token's first consonant (one key) instead of the three-key spelling."""

    def __init__(self):
        singles = list(romaji.ALPHABET)
        pairs = set()
        for a in singles:
            if a == "っ":
                continue
            for b in _SMALL:
                if romaji.to_romaji(a + b) != romaji.to_romaji(a) + romaji.to_romaji(b):
                    pairs.add(a + b)
        self.digraphs = frozenset(pairs)
        self.tokens = []
        for text in singles + sorted(pairs):
            rom = romaji.to_romaji(text)
            cats = "".join(_key_symbol(k) for k in romaji.to_keys(text))
            if len(cats) != len(rom):
                raise ValueError("romaji keys and spelling differ in length")
            if text == "っ":
                length, doublable = 1, False   # provisional: one key, resolved by the next token
            else:
                length, doublable = len(rom), len(romaji.to_romaji("っ" + text)) == len(rom) + 1
            self.tokens.append(_Token(text, length, cats[:length] if text == "っ" else cats, doublable))


_TABLE = []


def token_table():
    """The shared token table (built once)."""
    if not _TABLE:
        _TABLE.append(TokenTable())
    return _TABLE[0]


# ---------------------------------------------------------------- observation

def segment_categories(categories):
    """Category sequence of one trial -> the observed reading segments (runs of reading keys)."""
    segments, cur = [], []
    for c in categories:
        sym = _RUN_SYMBOL.get(c)
        if sym is None:
            if cur:
                segments.append("".join(cur))
                cur = []
        else:
            cur.append(sym)
    if cur:
        segments.append("".join(cur))
    return segments


def _pattern(seg):
    if isinstance(seg, int) and not isinstance(seg, bool):
        if seg < 1:
            raise ValueError("a segment length must be >= 1")
        return "a" * seg
    if isinstance(seg, str) and _PATTERN_RE.fullmatch(seg):
        return seg
    raise ValueError("a segment is a positive int (alphabetic keys) or a string over a, p, s")


# ---------------------------------------------------------------- decoding

def _order(h):
    return (-h[0], h[1])


def _put(stack, lm, score, text):
    m = lm.n - 1
    key = (text[-m:] if m else "", text[-1:])
    cur = stack.get(key)
    if cur is None or _order((score, text)) < _order(cur):
        stack[key] = (score, text)


def _decode_segment(pattern, hyps, lm, beam, table):
    """Extend every hypothesis (score, text) over one segment; returns the best `beam` complete ones."""
    length = len(pattern)
    stacks = [dict() for _ in range(length + 1)]
    kept = [None] * (length + 1)
    for score, text in hyps:
        _put(stacks[0], lm, score, text)
    for pos in range(length):
        if not stacks[pos]:
            continue
        items = sorted(stacks[pos].values(), key=_order)[:beam]
        kept[pos] = items
        for score, text in items:
            last = text[-1:]
            pending = last == "っ"
            dist = lm.logprobs(text)
            for tok in table.tokens:
                if len(tok.text) == 1 and (last + tok.text) in table.digraphs:
                    continue   # the table would read this pair as one unit
                base = pos
                if pending and not tok.doublable:
                    if pattern[pos:pos + 2] != "aa":
                        continue   # the pending kana takes three keys: two more
                    base = pos + 2
                end = base + tok.length
                if end > length or pattern[base:end] != tok.cats:
                    continue
                s = score + dist[tok.text[0]]
                if len(tok.text) == 2:
                    s += lm.logprobs(text + tok.text[0])[tok.text[1]]
                _put(stacks[end], lm, s, text + tok.text)
    done = {}
    for score, text in stacks[length].values():
        if text[-1:] != "っ":
            _put(done, lm, score, text)
    if length >= 2 and kept[length - 2] is not None and pattern[length - 2:] == "aa":
        for score, text in kept[length - 2]:
            if text[-1:] == "っ":
                _put(done, lm, score, text)
    return sorted(done.values(), key=_order)[:beam]


def decode(observed_segments, lm, beam=DEFAULT_BEAM, romaji_len=None):
    """The most probable kana string whose romanised keys match the observed segments.

    observed_segments: a list of segments; a segment is a string over a / p / s (`segment_categories`) or a
    positive int (that many alphabetic keys). romaji_len: the token / key-count table (default: the shared
    table built from `romaji`). Returns "" when no string fits."""
    if not isinstance(beam, int) or isinstance(beam, bool) or beam < 1:
        raise ValueError("beam must be an integer >= 1")
    table = romaji_len if romaji_len is not None else token_table()
    hyps = [(0.0, "")]
    for seg in observed_segments:
        hyps = _decode_segment(_pattern(seg), hyps, lm, beam, table)
        if not hyps:
            return ""
    best = None
    for score, text in hyps:
        total = (score + lm.logp(text, charlm.EOS), text)
        if best is None or _order(total) < _order(best):
            best = total
    return best[1] if best is not None else ""


def baseline(lengths, lm, seed):
    """A reading drawn from the model prior, without looking at the log: one draw of sum(lengths) kana."""
    if any((not isinstance(n, int)) or isinstance(n, bool) or n < 0 for n in lengths):
        raise ValueError("lengths must be integers >= 0")
    return lm.sample_prior(sum(lengths), seed)


# ---------------------------------------------------------------- metric and split

def cer(hyp, ref):
    """Character error rate: edit distance / len(ref)."""
    if not ref:
        raise ValueError("the reference is empty")
    prev = list(range(len(ref) + 1))
    for i, hc in enumerate(hyp, start=1):
        cur = [i]
        for j, rc in enumerate(ref, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (hc != rc)))
        prev = cur
    return prev[-1] / float(len(ref))


def _derive(seed, tag, i):
    """A 64-bit seed from (seed, tag, i), stable across Python versions and platforms."""
    h = hashlib.sha256(("%d:%s:%d" % (seed, tag, i)).encode("ascii")).digest()
    return int.from_bytes(h[:8], "big")


def split_phrases(phrases, seed, eval_fraction=0.2, unit="work", warnings=None):
    """Split phrases (dicts with `phrase_id`, `work_id` and `reading`) into (train, evaluation, n_dropped).

    unit="work" (default, the K1 attacker): phrases are grouped by `work_id`, and whole works are assigned to
    the evaluation set by a seeded shuffle over the works, so no evaluation phrase's work contributes a
    training phrase.
    unit="phrase" (the stronger K1+ attacker): phrases are shuffled and split individually; phrases of the
    same work can fall on both sides.
    Either way, the evaluation set holds ceil(eval_fraction * N) phrases (at least one, and at least one whole
    unit is left for training), and a training phrase whose reading equals an evaluation phrase's reading is
    removed afterwards (`n_dropped` counts them; a work can repeat a sentence).
    `warnings`, if a list, receives a message when unit="work" and fewer than `_MIN_WORKS_FOR_SPLIT` distinct
    works are available (the split still runs; this is a warning, not a refusal)."""
    if unit not in SPLIT_UNITS:
        raise ValueError("unit must be one of %s" % ", ".join(SPLIT_UNITS))
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if isinstance(eval_fraction, bool) or not isinstance(eval_fraction, (int, float)) or not 0.0 < eval_fraction < 1.0:
        raise ValueError("eval_fraction must be a number between 0 and 1")
    ordered = sorted(phrases, key=lambda ph: ph["phrase_id"])
    if len(ordered) < 2:
        raise ValueError("at least two phrases are needed to split")
    if unit == "phrase":
        units = [[ph] for ph in ordered]
    else:
        groups, order = {}, []
        for ph in ordered:
            wid = ph["work_id"]
            if wid not in groups:
                groups[wid] = []
                order.append(wid)
            groups[wid].append(ph)
        units = [groups[w] for w in order]
        if len(units) < 2:
            raise ValueError("at least two works are needed for a work-level split")
        if warnings is not None and len(units) < _MIN_WORKS_FOR_SPLIT:
            warnings.append("only %d distinct works are available for a work-level split (fewer than %d)"
                            % (len(units), _MIN_WORKS_FOR_SPLIT))
    n_eval_phrases = min(len(ordered) - 1, max(1, int(math.ceil(eval_fraction * len(ordered)))))
    idx = list(range(len(units)))
    rng = random.Random(_derive(seed, "split", 0))
    for i in range(len(idx) - 1, 0, -1):
        j = min(i, int(rng.random() * (i + 1)))
        idx[i], idx[j] = idx[j], idx[i]
    eval_idx, count = [], 0
    for pos, k in enumerate(idx):
        if count >= n_eval_phrases or pos == len(idx) - 1:
            break
        eval_idx.append(k)
        count += len(units[k])
    if not eval_idx:
        eval_idx.append(idx[0])
    eval_set = set(eval_idx)
    evaluation = [ph for k in eval_idx for ph in units[k]]
    rest = [ph for k in idx if k not in eval_set for ph in units[k]]
    held = set(ph["reading"] for ph in evaluation)
    train = [ph for ph in rest if ph["reading"] not in held]
    if not train:
        raise ValueError("no training phrase is left after removing the evaluation readings")
    return train, evaluation, len(rest) - len(train)


def _pooled_mean(clusters):
    return sum(sum(c) for c in clusters) / float(sum(len(c) for c in clusters))


def _r(x):
    return None if x is None else round(x, 6)


def _summary(values, cluster_ids, B, seed, conf):
    """(mean, (lo, hi)) with the trial values grouped by cluster for the bootstrap."""
    order, groups = [], {}
    for v, c in zip(values, cluster_ids):
        if c not in groups:
            groups[c] = []
            order.append(c)
        groups[c].append(v)
    lo, hi = stats.cluster_bootstrap([groups[c] for c in order], _pooled_mean, B, seed, conf)
    return _r(sum(values) / float(len(values))), [_r(lo), _r(hi)]


def _other_trial(truths, seed):
    """For each trial, the index of a random trial with a different typed reading (None if impossible)."""
    rng = random.Random(seed)
    out = []
    for i, t in enumerate(truths):
        cand = [j for j, u in enumerate(truths) if u != t]
        if not cand:
            return None
        out.append(cand[min(len(cand) - 1, int(rng.random() * len(cand)))])
    return out


def evaluate(trials, lm_k0, lm_k1, beam=DEFAULT_BEAM, B=DEFAULT_BOOTSTRAP, seed=0, length_info="count", conf=0.95):
    """Score the attack at both knowledge levels against the prior-only baseline.

    trials: list of {"segments": [...], "truth": typed reading (non-empty), "cluster": hashable block id}.
    Returns {"n", "params", "K0": {"cer", "cer_ci", "n_undecodable"}, "K1": {...},
             "baseline": {"K0": {"cer", "cer_ci"}, "K1": {...}},
             "gain": {"K0": {"gain", "gain_ci"}, "K1": {...}},
             "shuffled": {"K0": {"cer", "cer_ci", "gain", "gain_ci", "gain_vs_shuffled", "gain_vs_shuffled_ci"},
                          "K1": {...}} or None}.
    `gain_vs_shuffled` is the paired difference (shuffled CER minus attack CER), both decoded the same way;
    see the module docstring for why this differs from `gain`.
    Intervals are [None, None] when there are fewer than two clusters."""
    if length_info not in LENGTH_INFO:
        raise ValueError("length_info must be one of %s" % ", ".join(LENGTH_INFO))
    if not trials:
        raise ValueError("no trials")
    truths = [t["truth"] for t in trials]
    clusters = [t["cluster"] for t in trials]
    if any(not tr for tr in truths):
        raise ValueError("a typed reading is empty")
    pair = _other_trial(truths, _derive(seed, "shuffle", 0))
    out = {"n": len(trials), "baseline": {}, "gain": {}, "shuffled": {} if pair is not None else None,
           "params": {"beam": beam, "length_info": length_info, "bootstrap": B, "seed": seed, "conf": conf}}
    for level, lm in (("K0", lm_k0), ("K1", lm_k1)):
        decoded = [decode(t["segments"], lm, beam) for t in trials]
        attack = [cer(d, tr) for d, tr in zip(decoded, truths)]
        base = []
        for i, t in enumerate(trials):
            if length_info == "true":
                lengths = [len(truths[i])]
            else:
                lengths = [lm.sample_length(_derive(seed, "len:%s:%d" % (level, j), i))
                           for j in range(len(t["segments"]))]
            base.append(cer(baseline(lengths, lm, _derive(seed, "base:" + level, i)), truths[i]))
        gain = [b - a for a, b in zip(attack, base)]
        mean, ci = _summary(attack, clusters, B, _derive(seed, "ci:" + level, 0), conf)
        out[level] = {"cer": mean, "cer_ci": ci,
                      "n_undecodable": sum(1 for d, t in zip(decoded, trials) if t["segments"] and not d)}
        mean, ci = _summary(base, clusters, B, _derive(seed, "ci:base:" + level, 0), conf)
        out["baseline"][level] = {"cer": mean, "cer_ci": ci}
        mean, ci = _summary(gain, clusters, B, _derive(seed, "ci:gain:" + level, 0), conf)
        out["gain"][level] = {"gain": mean, "gain_ci": ci}
        if pair is not None:
            sh = [cer(decoded[pair[i]], truths[i]) for i in range(len(trials))]
            sh_gain = [b - s for s, b in zip(sh, base)]
            vs_shuffled = [s - a for s, a in zip(sh, attack)]
            entry = {}
            entry["cer"], entry["cer_ci"] = _summary(sh, clusters, B, _derive(seed, "ci:sh:" + level, 0), conf)
            entry["gain"], entry["gain_ci"] = _summary(sh_gain, clusters, B, _derive(seed, "ci:shg:" + level, 0), conf)
            entry["gain_vs_shuffled"], entry["gain_vs_shuffled_ci"] = _summary(
                vs_shuffled, clusters, B, _derive(seed, "ci:vsh:" + level, 0), conf)
            out["shuffled"][level] = entry
    return out


# ---------------------------------------------------------------- files

def _parse_json_lines(raw, what):
    """bytes (already format-checked) -> [dict, ...] (blank lines skipped). Deep nesting is refused rather
    than crashing, though a format check ahead of this call is expected to have caught it already."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("cannot read %s" % what)
    rows = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, RecursionError):
            raise InputError("%s is not well-formed JSON" % what)
    return rows


def _read_obs(path):
    """An obs/1 file, checked against the format (`validate_logs.validate`) before it is parsed."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raise InputError("cannot read observation file")
    violations = validate_logs.validate("obs", raw)
    if violations:
        ln, code, _msg = violations[0]
        raise InputError("observation file is not a well-formed obs/1 file (line %d, %s)" % (ln, code))
    return _parse_json_lines(raw, "observation file")


def _read_plan(path):
    """A plan/1 file, checked against the format (`generate_plan.validate_plan`) before it is parsed."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raise InputError("cannot read plan file")
    violations = generate_plan.validate_plan(raw)
    if violations:
        ln, code, _msg = violations[0]
        raise InputError("plan file is not a well-formed plan/1 file (line %d, %s)" % (ln, code))
    return _parse_json_lines(raw, "plan file")


def _kana_scenarios(recipes):
    """Scenarios whose recipe types the reading with the kana layout (outside the romaji attack model)."""
    out = set()
    for table in recipes["os"].values():
        for scenario, entry in table.items():
            if any(isinstance(s, dict) and s.get("input") == "kana" for s in entry.get("steps", [])):
                out.add(scenario)
    return out


def load_trials(obs_paths, plan_paths, recipes, phrase_ids=None, markers_in_obs=False, ime_toggle_in_obs=False):
    """Trials from obs/1 + plan/1 pairs. Returns (trials, n_excluded_kana, n_other_phrases, n_no_typed_reading,
    alignment).

    phrase_ids: if given, only trials of these phrases are kept (the others count in n_other_phrases).
    markers_in_obs / ime_toggle_in_obs: whether the block marker keys (keys pressed only to mark block boundaries,
    arrow keys on macOS) and the mid-conversion IME toggle keys are expected to appear as key rows in the observation.
    Both default to False (neither is a typing key, and by default no row is consumed for them); passing a
    flag expects an observation that does record the matching keys.

    The key presses are assigned to the plan by `closed_set.align_plan`, comparing the whole category sequence of
    every trial (not only its count). With markers_in_obs, a block whose presses do not match the plan is left
    unscored and counted, and the blocks after it are found again by their markers; the other blocks are scored.
    Without markers_in_obs the observation holds no marker to resynchronise to, so any mismatch or any press left
    over is refused (InputError). `alignment` is the merged alignment object of all sessions (unscored blocks and
    trials, unassigned presses); the counts n_excluded_kana, n_other_phrases and n_no_typed_reading refer to the
    trials of the scored blocks."""
    if len(obs_paths) != len(plan_paths) or not obs_paths:
        raise InputError("--obs and --plan must be given the same (non-zero) number of files")
    kana = _kana_scenarios(recipes)
    trials, n_kana, n_other, n_no_truth = [], 0, 0, 0
    alignments = []
    for idx, (obs_path, plan_path) in enumerate(zip(obs_paths, plan_paths)):
        obs = _read_obs(obs_path)
        cats = [r.get("category") for r in obs[1:] if isinstance(r, dict) and r.get("type") == "key" and r.get("press") is True]
        plan = _read_plan(plan_path)
        # the os the observation's own header declares (validated above), so that the check against the plan's os can fail
        obs_os = obs[0].get("os") if obs and isinstance(obs[0], dict) else None
        try:
            scored, alignment = closed_set.align_plan(cats, plan, obs_os, markers_in_obs, ime_toggle_in_obs,
                                                      tag="s%d-" % idx)
        except ValueError as e:
            raise InputError("the observation does not line up with the plan: %s" % e)
        alignments.append(alignment)
        for s in scored:
            row = s["row"]
            chunk = cats[s["start"]:s["start"] + s["n"]]
            if row["scenario"] in kana:
                n_kana += 1
                continue
            if phrase_ids is not None and row["phrase_id"] not in phrase_ids:
                n_other += 1
                continue
            truth = generate_plan.typed_reading(row, recipes)
            if not truth:
                n_no_truth += 1
                continue
            trials.append({"segments": segment_categories(chunk), "truth": truth,
                           "cluster": (idx, row["block_id"]), "trial_id": row["trial_id"]})
    return trials, n_kana, n_other, n_no_truth, closed_set.merge_alignments(alignments)


def _dumps_report(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def run(args):
    try:
        corpus = generate_plan.load_corpus(args.corpus)
        recipes = generate_plan.load_recipes(args.recipes)
    except (OSError, ValueError) as e:
        raise InputError("cannot use the corpus or the recipes: %s" % e)
    units = SPLIT_UNITS if args.split == "both" else (args.split,)
    length_infos = LENGTH_INFO if args.length_info == "both" else (args.length_info,)
    splits, data = {}, {}
    for unit in units:
        split_warnings = []
        train, evaluation, dropped = split_phrases(corpus, args.split_seed, args.eval_fraction, unit=unit,
                                                    warnings=split_warnings)
        try:
            trials, n_kana, n_other, n_no_truth, alignment = load_trials(
                args.obs, args.plan, recipes, set(ph["phrase_id"] for ph in evaluation),
                markers_in_obs=args.markers_in_obs, ime_toggle_in_obs=args.ime_toggle_in_obs)
        except (KeyError, TypeError, AttributeError):
            raise InputError("a plan file is not a well-formed plan/1 file")
        if not trials:
            raise InputError("no trial of an evaluation phrase can be scored (split=%s)" % unit)
        texts = [ph["reading"] for ph in train]
        lm_k0 = charlm.CharLM(1, args.smoothing).fit(texts)
        lm_k1 = charlm.CharLM(args.n, args.smoothing).fit(texts)
        splits[unit] = dict(
            (li, evaluate(trials, lm_k0, lm_k1, beam=args.beam, B=args.bootstrap, seed=args.seed, length_info=li))
            for li in length_infos)
        data[unit] = {"n_train_phrases": len(train), "n_eval_phrases": len(evaluation),
                      "n_dropped_duplicate_readings": dropped, "n_excluded_kana_layout": n_kana,
                      "n_other_phrases": n_other, "n_no_typed_reading": n_no_truth,
                      "split_warnings": split_warnings}
    report = {"splits": splits, "data": data, "alignment": alignment,
             "params": {"split_seed": args.split_seed, "eval_fraction": args.eval_fraction, "n": args.n,
                        "smoothing": args.smoothing, "markers_in_obs": args.markers_in_obs,
                        "ime_toggle_in_obs": args.ime_toggle_in_obs}}
    try:
        with open(args.out, "xb") as f:
            f.write(_dumps_report(report))
    except FileExistsError:
        raise InputError("the output file already exists")
    except OSError:
        raise InputError("cannot write the output file")
    for unit in units:
        for li in length_infos:
            r = splits[unit][li]
            print("split=%s length_info=%s K0 CER %.3f gain %.3f | K1 CER %.3f gain %.3f (n = %d)" % (
                unit, li, r["K0"]["cer"], r["gain"]["K0"]["gain"], r["K1"]["cer"], r["gain"]["K1"]["gain"], r["n"]))
    return 0


# ---------------------------------------------------------------- selftest

# eight words whose romanised key counts are all different (2 to 9), with a doubled consonant, a long-vowel
# mark and a kana pair among them
_WORDS = ["あお", "あめ", "みず", "ふぁいる", "あした", "らーめん", "おかあさん", "ふぁんたじー"]
_SYNTH_CATEGORY = dict(
    [(chr(c), "alphabetic") for c in range(ord("a"), ord("z") + 1)] + [(str(d), "numeric") for d in range(10)]
    + [("space", "space"), ("enter", "enter"), ("tab", "tab"), ("backspace", "backspace"), ("esc", "escape"),
       ("left", "navigation"), ("right", "navigation"), ("up", "navigation"), ("down", "navigation"),
       ("minus", "punctuation"), ("comma", "sentence_punctuation"), ("period", "sentence_punctuation"),
       ("shift", "modifier"), ("ime_on", "other"), ("ime_off", "other")]
    # F13-F19 (0x7C and above) fall outside the function-key range of the key-category mapping (0x70-0x7B,
    # F1-F12) and are categorised "other", not "function"
    + [("f%d" % i, "other") for i in range(13, 20)])


def _artificial_corpus(n_phrases, seed, n_works=8):
    """Short repetitive sentences (word, particle, word) from a small vocabulary: 64 distinct sentences at most,
    drawn with replacement, so duplicates occur on purpose. Phrases are spread over `n_works` works (round
    robin over the generation order) so that a work-level split has something to hold out."""
    rng = random.Random(seed)
    phrases = []
    for i in range(n_phrases):
        w1 = _WORDS[int(rng.random() * len(_WORDS))]
        w2 = _WORDS[int(rng.random() * len(_WORDS))]
        reading = w1 + "は" + w2
        work_id = "w%d" % (i % n_works + 1)
        phrases.append({"phrase_id": "%s-%d-1" % (work_id, i + 1), "work_id": work_id, "text": "x",
                        "reading": reading, "reading_len": len(reading)})
    return phrases


def _reading_segments(reading):
    """The attacker's view of a typed reading: one segment of key categories."""
    return ["".join(_key_symbol(k) for k in romaji.to_keys(reading))]


def _synth_obs(rows, os_name, markers_in_obs=False, ime_toggle_in_obs=False):
    """obs/1 lines for a plan: every plan key inside the key-observation gate, as a press and a release.
    Block markers and the mid-conversion IME toggle keys are included only when the matching flag is set (by
    default the published log carries no row for them; see `load_trials`)."""
    out = [generate_plan.dumps({"type": "meta", "format": "obs/1", "os": os_name, "logger_version": "selftest",
                                "manifest_sha256": "0" * 64,
                                "capabilities": {"unconditional": ["key", "ime_shadow"], "threshold_gated": [],
                                                 "config_gated": []}})]
    t = 0

    def key(name, down):
        nonlocal t
        t += 1
        out.append(generate_plan.dumps({"type": "key", "t": t, "category": _SYNTH_CATEGORY[name], "press": down}))

    for r in rows[1:]:
        if r["kind"] == "block":
            if markers_in_obs:
                for name in r["marker"]:
                    key(name, True)
                    key(name, False)
        else:
            for s in r["steps"]:
                if s["key"] in _PSEUDO_KEYS:
                    continue
                if s["key"] in _IME_TOGGLE_KEYS and not ime_toggle_in_obs:
                    continue
                key(s["key"], s["down"])
    return "\n".join(out) + "\n"


def selftest():
    fails = []
    count = [0]

    def check(name, cond, detail=""):
        count[0] += 1
        if not cond:
            fails.append("%s %s" % (name, detail))

    def near(name, got, want, tol=1e-9):
        check(name, got is not None and abs(got - want) <= tol, "got %r want %r" % (got, want))

    def raises(name, fn, exc=ValueError):
        count[0] += 1
        try:
            fn()
        except exc:
            return
        except Exception as e:  # a different exception is not a refusal
            fails.append("%s different exception: %r" % (name, e))
            return
        fails.append("%s no exception" % name)

    table = token_table()

    # --- the token table is derived from romaji: one unit per romanisation, and keys add up
    check("token table has singles and pairs", len(table.tokens) == len(romaji.ALPHABET) + len(table.digraphs)
          and "きゃ" in table.digraphs and "ふぁ" in table.digraphs and "てぃ" not in table.digraphs)
    bad = [t.text for t in table.tokens if t.text != "っ" and t.length != len(romaji.to_romaji(t.text))]
    check("token key counts equal the romaji spelling", not bad, repr(bad))

    # --- the marker keys F13-F19 fall outside the function-key range (0x70-0x7B, F1-F12) of the key-category
    # mapping, and are categorised "other" in a real observation
    check("F13 is categorised other, not function", _SYNTH_CATEGORY["f13"] == "other")
    check("F19 is categorised other, not function", _SYNTH_CATEGORY["f19"] == "other")

    # --- decoding honours the key-count constraint, including doubled consonants and punctuation keys
    tricky = ["がっこう", "あっ", "っな", "っっか", "きょう", "ふぁんたじー", "ちょっと", "しゃしん", "てぃ", "んな",
              "らーめん", "いっしょ", "っ", "ちっち"]
    for reading in tricky:
        lm = charlm.CharLM(3, "wb").fit([reading, "あいうえお"])
        got = decode(_reading_segments(reading), lm, beam=8)
        check("decode recovers %s from its key pattern" % reading, got == reading, "got %r" % got)
    lm_flat = charlm.CharLM(1, "wb").fit(["あ"])
    for reading in tricky:
        pat = _reading_segments(reading)[0]
        got = decode([pat], lm_flat, beam=4)
        check("an untrained-like model still returns a string with the observed key pattern (%s)" % reading,
              got != "" and "".join(_key_symbol(k) for k in romaji.to_keys(got)) == pat, "got %r" % got)
    check("two segments are decoded in order", decode(["aaa", "aa"], charlm.CharLM(3, "wb").fit(["きょうは"]), 8) != "")
    check("an int segment means that many alphabetic keys", decode([4], charlm.CharLM(3).fit(["あいうえ"]), 4)
          == decode(["aaaa"], charlm.CharLM(3).fit(["あいうえ"]), 4))
    check("no segment gives the empty reading", decode([], charlm.CharLM(2).fit(["あ"])) == "")
    raises("a bad segment", lambda: decode(["ax"], lm_flat))
    raises("an empty segment", lambda: decode([""], lm_flat))
    raises("beam = 0", lambda: decode(["aa"], lm_flat, beam=0))
    check("segment_categories keeps runs apart", segment_categories(
        ["alphabetic", "alphabetic", "space", "alphabetic", "punctuation", "alphabetic", "enter", "space"])
        == ["aa", "apa"])
    check("segment_categories drops non-reading keys", segment_categories(["space", "enter", "numeric"]) == [])

    # --- metric
    near("cer identical", cer("あいう", "あいう"), 0.0)
    near("cer one substitution", cer("あえう", "あいう"), 1.0 / 3)
    near("cer insertions can exceed one", cer("ああああ", "あ"), 3.0)
    near("cer against a longer reference", cer("", "あいう"), 1.0)
    raises("cer with an empty reference", lambda: cer("あ", ""))

    # --- split: the evaluation phrases are not in the training set (unit="work" is the default, K1)
    corpus = _artificial_corpus(90, 3)
    train, held, dropped = split_phrases(corpus, 11, 0.25)
    train_readings = set(p["reading"] for p in train)
    held_readings = set(p["reading"] for p in held)
    check("training and evaluation do not overlap", not (set(p["phrase_id"] for p in train) & set(p["phrase_id"] for p in held))
          and not (train_readings & held_readings) and len(held) == 23, "ids or readings overlap")
    check("work-level split: no evaluation phrase's work contributes a training phrase (K1)",
          not (set(p["work_id"] for p in train) & set(p["work_id"] for p in held)), "works overlap")
    check("split: duplicated readings of held-out phrases were dropped from training (the check has teeth)",
          dropped > 0, "dropped %d" % dropped)
    check("split: nothing is lost except the dropped duplicates", len(train) + len(held) + dropped == len(corpus))
    check("split: same seed, same split", (train, held, dropped) == split_phrases(corpus, 11, 0.25))
    check("split: a different seed gives a different split",
          [p["phrase_id"] for p in held] != [p["phrase_id"] for p in split_phrases(corpus, 12, 0.25)[1]])
    raises("split: eval_fraction = 1", lambda: split_phrases(corpus, 1, 1.0))
    raises("split: a single phrase", lambda: split_phrases(corpus[:1], 1))
    raises("split: an unknown unit", lambda: split_phrases(corpus, 1, 0.25, unit="bogus"))
    raises("split: a single-work corpus cannot be split by work",
          lambda: split_phrases(_artificial_corpus(20, 1, n_works=1), 1, 0.25, unit="work"))

    # --- split: unit="phrase" is the stronger, optional K1+ attacker, and is not the default
    train_p, held_p, dropped_p = split_phrases(corpus, 11, 0.25, unit="phrase")
    check("phrase-level split: nothing is lost except the dropped duplicates",
          len(train_p) + len(held_p) + dropped_p == len(corpus))
    check("phrase-level split: evaluation and training readings still do not overlap",
          not (set(p["reading"] for p in train_p) & set(p["reading"] for p in held_p)))
    check("phrase-level split can share a work between evaluation and training (K1+, unlike K1)",
          bool(set(p["work_id"] for p in train_p) & set(p["work_id"] for p in held_p)))
    check("phrase-level split gives a different evaluation set than the work-level split (same seed)",
          [p["phrase_id"] for p in held_p] != [p["phrase_id"] for p in held])

    # --- split: too few distinct works only warns (the split still runs); enough works, no warning
    few_warnings = []
    split_phrases(_artificial_corpus(20, 21, n_works=2), 1, 0.25, unit="work", warnings=few_warnings)
    check("too few works for a stable work-level split: a warning is recorded",
          len(few_warnings) == 1 and "work" in few_warnings[0], repr(few_warnings))
    enough_warnings = []
    split_phrases(corpus, 11, 0.25, unit="work", warnings=enough_warnings)
    check("enough distinct works: no warning", enough_warnings == [], repr(enough_warnings))

    # --- the log carries information: decoding beats the prior-only baseline
    texts = [p["reading"] for p in train]
    lm0 = charlm.CharLM(1, "wb").fit(texts)
    lm1 = charlm.CharLM(3, "wb").fit(texts)
    ev = [{"segments": _reading_segments(p["reading"]), "truth": p["reading"], "cluster": i % 6}
          for i, p in enumerate(held)]   # the held-out phrases, each with its own observation
    res = evaluate(ev, lm0, lm1, beam=6, B=400, seed=5)
    g1, g0 = res["gain"]["K1"], res["gain"]["K0"]
    check("decoding beats the baseline", g1["gain"] > 0.25 and g1["gain_ci"][0] is not None and g1["gain_ci"][0] > 0.0
          and res["K1"]["cer"] < res["baseline"]["K1"]["cer"], "K1 gain %r CER %r baseline %r" % (
              g1, res["K1"]["cer"], res["baseline"]["K1"]["cer"]))
    check("K0 also gains from the log (the key count)", g0["gain"] > 0.0 and res["K0"]["cer"] < res["baseline"]["K0"]["cer"],
          "K0 %r" % (g0,))
    check("K1 (genre knowledge) reads better than K0", res["K1"]["cer"] < res["K0"]["cer"],
          "K1 %r K0 %r" % (res["K1"]["cer"], res["K0"]["cer"]))
    check("no trial is undecodable", res["K0"]["n_undecodable"] == 0 and res["K1"]["n_undecodable"] == 0)
    # every decoded string has the observed key pattern (the constraint holds on the whole set)
    okpat = all("".join(_key_symbol(k) for k in romaji.to_keys(decode(t["segments"], lm1, 6))) == t["segments"][0]
                for t in ev)
    check("every decoded reading has the observed key pattern", okpat)
    # negative control: the same trials with the observations exchanged between trials
    sh1 = res["shuffled"]["K1"]
    check("shuffling removes the difference", sh1["gain"] < 0.35 * g1["gain"] and abs(sh1["gain"]) < 0.15
          and sh1["gain_ci"][0] is not None and sh1["gain_ci"][0] <= 0.0
          and sh1["cer"] > res["K1"]["cer"] + 0.25, "shuffled %r real gain %r" % (sh1, g1["gain"]))
    # gain_vs_shuffled (paired against the shuffled decode, not the prior baseline) isolates the trial's own
    # observation from the MAP-versus-draw difference that `gain` conflates, and so is unaffected by shuffling
    # having removed most of `gain` above
    check("gain_vs_shuffled: K1's own observation carries information beyond MAP-versus-draw",
          sh1["gain_vs_shuffled"] > 0.25 and sh1["gain_vs_shuffled_ci"][0] is not None
          and sh1["gain_vs_shuffled_ci"][0] > 0.0, "shuffled %r" % (sh1,))
    sh0 = res["shuffled"]["K0"]
    check("gain_vs_shuffled: K0's own observation (key count) also carries information",
          sh0["gain_vs_shuffled"] > 0.05 and sh0["gain_vs_shuffled_ci"][0] is not None
          and sh0["gain_vs_shuffled_ci"][0] > 0.0, "shuffled %r" % (sh0,))
    check("gain_vs_shuffled differs from gain (they measure different things)",
          abs(sh1["gain_vs_shuffled"] - sh1["gain"]) > 0.1, "shuffled %r" % (sh1,))
    # a second control: the same observation for every trial carries no information either
    const = evaluate([dict(t, segments=["aaaaaaaa"]) for t in ev], lm0, lm1, beam=6, B=100, seed=5)
    check("the same observation for every trial gives little gain",
          const["gain"]["K1"]["gain"] < 0.4 * g1["gain"], "%r vs %r" % (const["gain"]["K1"], g1["gain"]))
    check("the exchanged observation gives about baseline-level K0 error too",
          res["shuffled"]["K0"]["gain"] < 0.5 * g0["gain"] + 0.05, "%r vs %r" % (res["shuffled"]["K0"], g0))
    # the exchange never pairs a trial with a trial of the same reading
    pair = _other_trial([t["truth"] for t in ev], 1)
    check("the exchange pairs only trials with different readings",
          all(ev[pair[i]]["truth"] != ev[i]["truth"] for i in range(len(ev))))
    check("the exchange is impossible when all readings agree", _other_trial(["あ", "あ"], 1) is None)
    # a strong prior-only attacker (true length) leaves less gain than the count-only baseline
    res_true = evaluate(ev, lm0, lm1, beam=6, B=100, seed=5, length_info="true")
    check("the true-length baseline is at least as strong for K0", res_true["baseline"]["K0"]["cer"]
          <= res["baseline"]["K0"]["cer"] + 0.05, "%r vs %r" % (res_true["baseline"]["K0"], res["baseline"]["K0"]))

    # --- determinism: same input and seed give byte-identical reports; the seed matters
    again = evaluate(ev, lm0, lm1, beam=6, B=400, seed=5)
    check("同じ seed でバイト一致", _dumps_report(again) == _dumps_report(res))
    other = evaluate(ev, lm0, lm1, beam=6, B=400, seed=6)
    check("a different seed changes the draws", _dumps_report(other) != _dumps_report(res))
    g0_state = random.getstate()
    evaluate(ev[:8], lm0, lm1, beam=3, B=20, seed=1)
    check("evaluate leaves the global random state alone", random.getstate() == g0_state)
    raises("evaluate without trials", lambda: evaluate([], lm0, lm1))
    raises("evaluate with an unknown length_info", lambda: evaluate(ev, lm0, lm1, length_info="x"))
    one_cluster = evaluate([dict(t, cluster=0) for t in ev[:6]], lm0, lm1, beam=3, B=20, seed=1)
    check("one cluster gives no interval (not zero)", one_cluster["gain"]["K1"]["gain_ci"] == [None, None])

    # --- the baseline
    check("baseline has the requested total length", len(baseline([3, 4], lm1, 1)) == 7)
    check("baseline is reproducible", baseline([5], lm1, 9) == baseline([5], lm1, 9))
    raises("baseline with a negative length", lambda: baseline([-1], lm1, 1))

    # --- files: obs/1 + plan/1 -> trials -> report (end to end through the command line)
    with tempfile.TemporaryDirectory() as tmp:
        recipes_path = os.path.join(_KIT, "gen", "recipes.json")
        recipes = generate_plan.load_recipes(recipes_path)
        corpus_path = os.path.join(tmp, "corpus.jsonl")
        lines = [generate_plan.dumps({"kind": "header", "format": "corpus/1", "source": "aozora", "tokenizer": "table",
                                      "dict": "none"})]
        seen, phrases = set(), []
        for ph in _artificial_corpus(60, 4):
            if ph["reading"] not in seen:
                seen.add(ph["reading"])
                phrases.append(dict(ph, kind="phrase"))
        for ph in phrases:
            lines.append(generate_plan.dumps({"kind": "phrase", "phrase_id": ph["phrase_id"], "work_id": ph["work_id"],
                                              "text": "x", "reading": ph["reading"], "reading_len": len(ph["reading"])}))
        with open(corpus_path, "wb") as f:
            f.write(("\n".join(lines) + "\n").encode("utf-8"))
        cfg = {"n_per_cell_scenario_speed": 2, "block_size": 7,
               "speeds": {"slow": {"iki_median_ms": 300, "iki_sigma": 0.3}, "mid": {"iki_median_ms": 200, "iki_sigma": 0.3},
                          "fast": {"iki_median_ms": 120, "iki_sigma": 0.3}},
               "key_hold_ms": 60, "think_ms": {"min": 200, "max": 2000}, "cand_index_max": 4, "nav_count_max": 3,
               "inter_trial_ms": 1000, "marker": ["f13", "f14", "f13"], "marker_gap_ms": 150,
               "reset_ime_every_block": True}
        rows, _ = generate_plan.plan_rows(generate_plan.load_corpus(corpus_path), recipes, cfg, "macos", 7,
                                          "0" * 64, "0" * 64, "0" * 64)
        plan_path = os.path.join(tmp, "plan.jsonl")
        obs_path = os.path.join(tmp, "obs.jsonl")
        with open(plan_path, "wb") as f:
            f.write(generate_plan.render_plan(rows))
        # the default observation carries no row for block markers or mid-conversion IME toggles
        obs_text = _synth_obs(rows, "macos")
        with open(obs_path, "wb") as f:
            f.write(obs_text.encode("utf-8"))
        common = ["--obs", obs_path, "--plan", plan_path, "--corpus", corpus_path, "--recipes", recipes_path,
                  "--split-seed", "3", "--eval-fraction", "0.3", "--bootstrap", "40", "--beam", "4"]
        out1, out2 = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
        rc1 = main(common + ["--out", out1])
        rc2 = main(common + ["--out", out2])
        check("the command line runs end to end", rc1 == 0 and rc2 == 0, "exit %r %r" % (rc1, rc2))
        if rc1 == 0 and rc2 == 0:
            with open(out1, "rb") as f1, open(out2, "rb") as f2:
                b1, b2 = f1.read(), f2.read()
            check("the command line output is byte-identical for the same seed", b1 == b2)
            rep = json.loads(b1.decode("utf-8"))
            check("the default split is work and the default length_info is both",
                  sorted(rep["splits"].keys()) == ["work"] and sorted(rep["splits"]["work"].keys()) == ["count", "true"])
            train2, held2, _d = split_phrases(generate_plan.load_corpus(corpus_path), 3, 0.3, unit="work")
            ids = set(p["phrase_id"] for p in held2)
            kana = _kana_scenarios(recipes)
            want = sum(1 for r in rows[1:] if r["kind"] == "trial" and r["scenario"] not in kana and r["phrase_id"] in ids)
            n_count = rep["splits"]["work"]["count"]["n"]
            check("only trials of evaluation phrases outside the kana layout are scored",
                  n_count == want and want > 0, "n %r want %r" % (n_count, want))
            check("count and true length_info score the same trials",
                  rep["splits"]["work"]["true"]["n"] == n_count)
            wdata = rep["data"]["work"]
            check("the report records the data used", wdata["n_eval_phrases"] == len(held2)
                  and wdata["n_train_phrases"] == len(train2) and wdata["n_excluded_kana_layout"] > 0)
            check("the report counts trials without a typed reading (even when zero)",
                  isinstance(wdata["n_no_typed_reading"], int) and wdata["n_no_typed_reading"] >= 0)
            # the output file is created, never overwritten
            rc1_again = main(common + ["--out", out1])
            with open(out1, "rb") as f1:
                b1_again = f1.read()
            check("--out does not overwrite an existing file", rc1_again == 2 and b1_again == b1,
                  "exit %r" % rc1_again)

        # --- --split both and --length-info true report both splits / the requested length_info only
        out_both = os.path.join(tmp, "both.json")
        rc_both = main(common + ["--out", out_both, "--split", "both", "--length-info", "true"])
        check("--split both reports the work and phrase splits together", rc_both == 0, "exit %r" % rc_both)
        if rc_both == 0:
            with open(out_both, "rb") as f:
                rep_both = json.loads(f.read().decode("utf-8"))
            check("--split both: both splits are present, only the requested length_info",
                  sorted(rep_both["splits"].keys()) == ["phrase", "work"]
                  and all(sorted(rep_both["splits"][u].keys()) == ["true"] for u in ("phrase", "work")))
            check("--split both: the two splits score a different, non-empty set of trials"
                  " (the coarser work-level grouping need not match the phrase-level target size)",
                  rep_both["splits"]["phrase"]["true"]["n"] > 0 and rep_both["splits"]["work"]["true"]["n"] > 0
                  and rep_both["splits"]["phrase"]["true"]["n"] != rep_both["splits"]["work"]["true"]["n"])

        # --- markers-in-obs / ime-toggle-in-obs: the assignment is refused when the flag and the observation
        # disagree, and succeeds (with the same result as the default) when they agree
        has_ime_toggle_trial = any(any(s["key"] in ("ime_on", "ime_off") for s in r["steps"])
                                   for r in rows[1:] if r["kind"] == "trial")
        check("the artificial plan includes a mid-conversion IME toggle trial (a precondition below)",
              has_ime_toggle_trial)
        obs_markers_path = os.path.join(tmp, "obs_markers.jsonl")
        with open(obs_markers_path, "wb") as f:
            f.write(_synth_obs(rows, "macos", markers_in_obs=True).encode("utf-8"))
        obs_ime_path = os.path.join(tmp, "obs_ime.jsonl")
        with open(obs_ime_path, "wb") as f:
            f.write(_synth_obs(rows, "macos", ime_toggle_in_obs=True).encode("utf-8"))
        bad_out = os.path.join(tmp, "bad.json")
        check("markers in the observation but --markers-in-obs not given is refused",
              main(["--obs", obs_markers_path] + common[2:] + ["--out", bad_out]) == 2)
        check("IME toggle keys in the observation but --ime-toggle-in-obs not given is refused",
              main(["--obs", obs_ime_path] + common[2:] + ["--out", bad_out]) == 2)
        out_markers = os.path.join(tmp, "markers_ok.json")
        rc_markers = main(["--obs", obs_markers_path] + common[2:] + ["--out", out_markers, "--markers-in-obs"])
        out_ime = os.path.join(tmp, "ime_ok.json")
        rc_ime = main(["--obs", obs_ime_path] + common[2:] + ["--out", out_ime, "--ime-toggle-in-obs"])
        check("markers-in-obs / ime-toggle-in-obs, given consistently, succeed",
              rc_markers == 0 and rc_ime == 0, "exit %r %r" % (rc_markers, rc_ime))
        if rc_markers == 0 and rc_ime == 0 and rc1 == 0:
            with open(out_markers, "rb") as fm, open(out_ime, "rb") as fi:
                rep_m, rep_i = json.loads(fm.read().decode("utf-8")), json.loads(fi.read().decode("utf-8"))
            check("marker / IME-toggle rows do not change the scored result (they are not reading keys)",
                  rep_m["splits"] == rep["splits"] and rep_m["data"] == rep["data"]
                  and rep_i["splits"] == rep["splits"] and rep_i["data"] == rep["data"])

        # --- blocks that do not match the plan are left unscored (--markers-in-obs) and counted; the others are scored
        layout = []    # [marker presses, trial presses, [trial ids], block_id] of every block
        for r in rows[1:]:
            if r["kind"] == "block":
                layout.append([len(r["marker"]), 0, [], r["block_id"]])
            else:
                layout[-1][1] += len(closed_set.expected_categories(r))
                layout[-1][2].append(r["trial_id"])
        blen = [m + k for m, k, _, _ in layout]
        bstart = [sum(blen[:i]) for i in range(len(blen))]
        mk_header = _synth_obs(rows, "macos", markers_in_obs=True).split("\n")[0]
        mk_cats = [json.loads(ln)["category"] for ln in _synth_obs(rows, "macos", markers_in_obs=True).split("\n")[1:]
                   if ln and json.loads(ln)["press"]]
        check("alignment setup: several blocks, and the category list is the plan's presses",
              len(layout) >= 3 and len(mk_cats) == sum(blen))

        def _cats_no_markers(cats, lay):
            """The same presses without the block markers (the default observation)."""
            out = []
            for i, (m, k, _, _) in enumerate(lay):
                st = sum(m2 + k2 for m2, k2, _, _ in lay[:i])
                out.extend(cats[st + m:st + m + k])
            return out

        def obs_of(cats, name):
            p = os.path.join(tmp, name)
            lines = [mk_header] + [generate_plan.dumps({"type": "key", "t": i + 1, "category": c, "press": True})
                                   for i, c in enumerate(cats)]
            with open(p, "wb") as fh:
                fh.write(("\n".join(lines) + "\n").encode("utf-8"))
            return p

        def load(cats, name="al.jsonl", markers=True):
            return load_trials([obs_of(cats, name)], [plan_path], recipes, None, markers_in_obs=markers)

        t_ok, k_ok, o_ok, nt_ok, al_ok = load(mk_cats)
        ids_ok = set(t["trial_id"] for t in t_ok)
        row_of = dict((r["trial_id"], r) for r in rows[1:] if r["kind"] == "trial")
        check("alignment: the segments of a scored trial are those of its own presses",
              all(t["segments"] == segment_categories(closed_set.expected_categories(row_of[t["trial_id"]]))
                  for t in t_ok))
        check("alignment: an intact observation scores every trial outside the kana layout and loses no block",
              al_ok["n_blocks"] == len(layout) and al_ok["n_blocks_unscored"] == 0 and al_ok["n_presses_unassigned"] == 0
              and al_ok["n_trials_unscored"] == 0 and len(ids_ok) > 0 and k_ok > 0)

        # the observation's header os must agree with the plan's os (the same refusal the closed set gives)
        os_other = "windows" if rows[0]["os"] != "windows" else "macos"
        obs_os_path = os.path.join(tmp, "obs_other_os.jsonl")
        with open(obs_os_path, "wb") as fh:
            fh.write(("\n".join([mk_header.replace('"os":"%s"' % rows[0]["os"], '"os":"%s"' % os_other)]
                                + [generate_plan.dumps({"type": "key", "t": i + 1, "category": c, "press": True})
                                   for i, c in enumerate(mk_cats)]) + "\n").encode("utf-8"))
        try:
            load_trials([obs_os_path], [plan_path], recipes, None, markers_in_obs=True)
            check("an observation header with another os than the plan is refused", False, "no exception")
        except InputError as e:
            check("an observation header with another os than the plan is refused",
                  "does not match the plan's os" in str(e) and os_other in str(e), repr(str(e)))

        def lost_ids(i):
            return set(layout[i][2])

        def only_block_lost(cats, i, name):
            t, k, o, nt, al = load(cats, name)
            ids = set(x["trial_id"] for x in t)
            return (al["unscored_block_ids"] == ["s0-" + str(layout[i][3])] and al["n_blocks_unscored"] == 1
                    and al["n_trials_unscored"] == len(layout[i][2]) and ids == ids_ok - lost_ids(i)
                    and sum(al["unscored_by_scenario"].values()) == len(layout[i][2])
                    and sum(al["unscored_by_speed"].values()) == len(layout[i][2])), al

        check("alignment: no trial of a kana-layout scenario is scored (as before)",
              all(next(r for r in rows[1:] if r.get("trial_id") == i)["scenario"] not in _kana_scenarios(recipes)
                  for i in ids_ok))
        for i in (0, 1, len(layout) - 1):
            c = bstart[i] + layout[i][0] + 2
            ok, al = only_block_lost(mk_cats[:c] + mk_cats[c + 1:], i, "al_miss.jsonl")
            check("alignment: one missing press inside block %d leaves only that block unscored" % i, ok, repr(al))
            check("alignment: that block's presses are counted as unassigned (block %d)" % i,
                  al["n_presses_unassigned"] == blen[i] - 1, repr(al))
        c = bstart[1] + layout[1][0] + 2
        ok, al = only_block_lost(mk_cats[:c] + ["enter"] + mk_cats[c:], 1, "al_extra.jsonl")
        check("alignment: one extra press inside a block leaves only that block unscored", ok, repr(al))
        check("alignment: the extra press and the block's presses are counted as unassigned",
              al["n_presses_unassigned"] == blen[1] + 1, repr(al))
        t_s, k_s, o_s, nt_s, al_s = load(mk_cats[:bstart[1]] + ["escape"] + mk_cats[bstart[1]:], "al_stray.jsonl")
        check("alignment: a stray press between two blocks loses no block and is counted as one unassigned press",
              set(x["trial_id"] for x in t_s) == ids_ok and al_s["n_blocks_unscored"] == 0
              and al_s["n_presses_unassigned"] == 1, repr(al_s))
        t_t, k_t, o_t, nt_t, al_t = load(mk_cats + ["escape"] * 4, "al_tail.jsonl")
        check("alignment: presses left after the last block are counted as unassigned, without an error",
              set(x["trial_id"] for x in t_t) == ids_ok and al_t["n_blocks_unscored"] == 0
              and al_t["n_presses_unassigned"] == 4, repr(al_t))
        for i in (0, 1, len(layout) - 1):
            ok, al = only_block_lost(mk_cats[:bstart[i]] + mk_cats[bstart[i] + 1:], i, "al_marker.jsonl")
            check("alignment: a press missing from the marker of block %d leaves that block unscored and no other" % i,
                  ok, repr(al))
        c = bstart[1] + layout[1][0] + 2
        d = bstart[2] + layout[2][0] + 2
        t_b, k_b, o_b, nt_b, al_b = load(mk_cats[:bstart[1]] + mk_cats[bstart[1] + 1:d] + mk_cats[d + 1:], "al_two.jsonl")
        check("alignment: a damaged marker and a missing press in the next block leave both unscored, the others scored",
              al_b["unscored_block_ids"] == ["s0-%s" % layout[1][3], "s0-%s" % layout[2][3]]
              and set(x["trial_id"] for x in t_b) == ids_ok - lost_ids(1) - lost_ids(2), repr(al_b))
        # the whole category sequence is compared (the count and the number of alphabetic keys are not enough)
        sw = next(i for i in range(bstart[1] + layout[1][0], bstart[1] + blen[1] - layout[1][0]) if mk_cats[i] == "space")
        swapped = mk_cats[:sw] + ["enter"] + mk_cats[sw + 1:]
        ok, al = only_block_lost(swapped, 1, "al_swap.jsonl")
        check("alignment: a press of another category with the same count and the same alphabetic keys leaves its "
              "block unscored", ok, repr(al))
        raises("without --markers-in-obs the same swapped category is refused (no anchor to resynchronise to)",
               lambda: load(_cats_no_markers(swapped, layout), "al_swap_nm.jsonl", markers=False), InputError)
        raises("without --markers-in-obs a missing press is refused",
               lambda: load(_cats_no_markers(mk_cats, layout)[:5] + _cats_no_markers(mk_cats, layout)[6:],
                            "al_nm_miss.jsonl", markers=False), InputError)
        raises("without --markers-in-obs presses left after the last trial are refused",
               lambda: load(_cats_no_markers(mk_cats, layout) + ["space"], "al_nm_tail.jsonl", markers=False), InputError)
        out_al = os.path.join(tmp, "al_out.json")
        rc_al = main(["--obs", obs_of(mk_cats[:bstart[1] + layout[1][0] + 2] + mk_cats[bstart[1] + layout[1][0] + 3:],
                                      "al_cli.jsonl")] + common[2:] + ["--out", out_al, "--markers-in-obs"])
        check("command line: with --markers-in-obs one missing press does not stop the run (exit 0)", rc_al == 0,
              "exit %r" % rc_al)
        if rc_al == 0:
            with open(out_al, "rb") as fh:
                rep_al = json.loads(fh.read().decode("utf-8"))
            check("command line: the report holds the alignment object and n counts the scored trials only",
                  rep_al["alignment"]["n_blocks_unscored"] == 1
                  and rep_al["alignment"]["unscored_block_ids"] == ["s0-%s" % layout[1][3]]
                  and rep_al["alignment"]["n_presses_unassigned"] == blen[1] - 1
                  and rep_al["splits"]["work"]["count"]["n"] <= rep["splits"]["work"]["count"]["n"], repr(rep_al["alignment"]))

        # --- the assignment is refused, not guessed, when the counts differ
        short = os.path.join(tmp, "short.jsonl")
        with open(short, "wb") as f:
            f.write("\n".join(obs_text.split("\n")[:-4]).encode("utf-8") + b"\n")
        check("an observation with missing key presses is refused", main(common[:1] + [short] + common[2:] + ["--out", bad_out]) == 2
              and not os.path.exists(bad_out))
        extra = os.path.join(tmp, "extra.jsonl")
        with open(extra, "wb") as f:
            f.write((obs_text + generate_plan.dumps({"type": "key", "t": 999999, "category": "space", "press": True})
                     + "\n").encode("utf-8"))
        check("an observation with extra key presses is refused", main(common[:1] + [extra] + common[2:] + ["--out", bad_out]) == 2)
        check("a missing input file is refused", main(common[:1] + [os.path.join(tmp, "none.jsonl")] + common[2:]
                                                      + ["--out", bad_out]) == 2)
        broken = os.path.join(tmp, "broken_plan.jsonl")
        with open(broken, "wb") as f:
            f.write(b'{"kind":"header"}\n{"kind":"trial"}\n')
        check("a plan without the plan/1 fields is refused (generate_plan.validate_plan), not a crash",
              main(common[:2] + ["--plan", broken] + common[4:] + ["--out", bad_out]) == 2)
        check("a different number of --obs and --plan files is refused",
              main(["--obs", obs_path, obs_path] + common[2:] + ["--out", bad_out]) == 2)

        # --- the observation is checked against obs/1 (validate_logs.validate) before it is read
        def _obs_header(**over):
            h = {"type": "meta", "format": "obs/1", "os": "macos", "logger_version": "x",
                 "manifest_sha256": "0" * 64,
                 "capabilities": {"unconditional": [], "threshold_gated": [], "config_gated": []}}
            h.update(over)
            return h

        bad_format = os.path.join(tmp, "bad_format.jsonl")
        with open(bad_format, "wb") as f:
            f.write((generate_plan.dumps(_obs_header(format="obs/2")) + "\n").encode("utf-8"))
        check("an observation whose header is not obs/1 is refused",
              main(common[:1] + [bad_format] + common[2:] + ["--out", bad_out]) == 2)
        bad_category = os.path.join(tmp, "bad_category.jsonl")
        with open(bad_category, "wb") as f:
            f.write((generate_plan.dumps(_obs_header()) + "\n"
                     + '{"type":"key","t":1,"category":"bogus","press":true}\n').encode("utf-8"))
        check("an observation key row with a category outside the fourteen-value enum is refused",
              main(common[:1] + [bad_category] + common[2:] + ["--out", bad_out]) == 2)
        dup_key = os.path.join(tmp, "dup_key.jsonl")
        with open(dup_key, "wb") as f:
            f.write((generate_plan.dumps(_obs_header()) + "\n"
                     + '{"type":"key","t":1,"t":2,"category":"alphabetic","press":true}\n').encode("utf-8"))
        check("an observation line with a repeated key is refused, not silently taking the last value",
              main(common[:1] + [dup_key] + common[2:] + ["--out", bad_out]) == 2)
        deep = os.path.join(tmp, "deep.jsonl")
        with open(deep, "wb") as f:
            f.write((generate_plan.dumps(_obs_header()) + "\n"
                     + '{"type":"key","t":1,"category":"alphabetic","press":' + "[" * 5000
                     + '}\n').encode("utf-8"))
        check("an observation with JSON nested past the recursion limit is refused (exit 2), not a crash",
              main(common[:1] + [deep] + common[2:] + ["--out", bad_out]) == 2)

    if fails:
        for f in fails:
            print("NG: " + f)
        print("selftest failed: %d of %d" % (len(fails), count[0]))
        return 1
    print("selftest OK (%d checks)" % count[0])
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Open-set reconstruction of the typed reading (attack A1)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--obs", nargs="+", metavar="OBS")
    ap.add_argument("--plan", nargs="+", metavar="PLAN")
    ap.add_argument("--corpus")
    ap.add_argument("--recipes", default=os.path.join(_KIT, "gen", "recipes.json"))
    ap.add_argument("--split-seed", type=int)
    ap.add_argument("--eval-fraction", type=float, default=0.2)
    ap.add_argument("--split", choices=SPLIT_UNITS + ("both",), default="work")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--smoothing", choices=charlm.SMOOTHINGS, default="wb")
    ap.add_argument("--beam", type=int, default=DEFAULT_BEAM)
    ap.add_argument("--length-info", choices=LENGTH_INFO + ("both",), default="both")
    ap.add_argument("--markers-in-obs", action="store_true",
                    help="the presses of the block-start markers appear in the observation (default: they do not). "
                         "With this flag a block whose presses do not match the plan is left unscored and counted "
                         "(alignment in the report) and the blocks after it are found again by their markers; "
                         "without it any mismatch is refused (exit 2)")
    ap.add_argument("--ime-toggle-in-obs", action="store_true",
                    help="the presses of the IME toggle keys (ime_on, ime_off) appear in the observation, as "
                         "category other (default: they do not)")
    ap.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.obs and args.plan and args.corpus and args.split_seed is not None and args.out):
        ap.error("--obs, --plan, --corpus, --split-seed and --out are required (or --selftest)")
    try:
        return run(args)
    except InputError as e:
        print("open_set.py: %s" % e, file=sys.stderr)
        return 2
    except ValueError as e:
        print("open_set.py: invalid argument: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
