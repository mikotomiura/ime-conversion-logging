#!/usr/bin/env python3
# kit: selftest
"""Scenario generator: builds plan/1 from corpus/1 and recipes (OS-specific key procedures). Python
standard library only.

  generate_plan.py --corpus F --recipes F --config F --os macos|windows --seed N --out F
  generate_plan.py --validate PLAN                                    validate against plan/1 (exit 1 on violation)
  generate_plan.py --estimate --config F --recipes F --os OS [--corpus F]   duration (seconds) for one cell
  generate_plan.py --selftest                                         positive and negative cases (fast default)

Exit codes: 0 = success / 1 = --validate found a violation / 2 = usage error, or an input (config, corpus,
or recipes) could not be read.

The procedure (which keys are pressed, in what order) lives in recipes.json as data, not code; only that
data should be edited after a calibration pilot.
Duration (key-interval distribution), think time, and n are read from the config; this module only reads
them, it does not decide their values.
Determinism: randomness uses only random() (never shuffle / choice / lognormvariate, whose behavior can
change across Python versions). Each trial's random state is derived from (seed, trial_id) (trial_rng), so
calling build_trial directly with the same arguments reproduces the matching row of a generated plan.
Floating-point arithmetic (log / exp / cos) depends on the platform's math library, so byte-identical
output is only guaranteed when regenerating on the same machine and the same Python version. A plan built
on one OS is distributed as a file rather than rebuilt on another.
Public functions: build_trial(...), load_recipes(path), trial_rng(seed, trial_id), load_corpus(path),
  normalize_config(config), plan_rows(...), render_plan(rows), validate_plan(data), typed_reading(trial, recipes).
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import romaji  # noqa: E402

GENERATOR_VERSION = "1.2.0"
SPEEDS = ("slow", "mid", "fast")
SCENARIOS = tuple("S%d" % i for i in range(1, 16))
OS_NAMES = ("macos", "windows")
KEYNAMES = frozenset(
    [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(d) for d in range(10)]
    + ["space", "enter", "esc", "backspace", "tab", "left", "right", "up", "down", "minus", "comma", "period",
       "ime_on", "ime_off"] + ["f%d" % i for i in range(13, 20)]
    + ["shift", "mouse_click", "focus_away", "focus_back"]
)
ARROW_KEYS = ("left", "up", "right", "down")
# Keys the observation is assumed not to record (the key table in ../e2/README.md: the IME toggles, F13 to F19, and
# the runner's pseudo keys produce no row).
UNOBSERVED_KEYS = frozenset(["ime_on", "ime_off", "mouse_click", "focus_away", "focus_back"]
                            + ["f%d" % i for i in range(13, 20)])
COMMITS = ("enter", "digit", "mouse", "none")
INTENT_KEYS = ("commit", "cand_index", "nav_count", "abandon", "predictive", "ime_toggle_mid", "focus_switch_mid",
               "expect_session")
INTENT_BOOLS = ("abandon", "predictive", "ime_toggle_mid", "focus_switch_mid", "expect_session")
TRIAL_KEYS = ("kind", "trial_id", "block_id", "scenario", "speed", "gap_before_ms", "think_ms", "phrase_id",
              "reading_len", "steps", "intent")
BLOCK_KEYS = ("kind", "block_id", "speed", "reset_ime", "marker", "marker_gap_ms")
HEADER_KEYS = ("kind", "format", "os", "seed", "generator_version", "config_sha256", "recipes_sha256",
               "corpus_sha256")

VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TRIAL_ID_RE = re.compile(r"^t[0-9]{5,}$")
PHRASE_ID_RE = re.compile(r"^(?:[a-z0-9][a-z0-9_]{0,31}-[0-9]+-[0-9]+|kana-[0-9]+)$")
WORK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
DICT_RE = re.compile(r"^[0-9A-Za-z._+-]{1,64}$")
READING_RE = re.compile(r"^[ぁ-ゖー]+$")
SEED_MAX = 2 ** 63 - 1

# Time expressions (eval is not used; only these three and integer literals are allowed)
TIMES = {
    "cand_index+1": lambda v: v["cand_index"] + 1,
    "nav_count": lambda v: v["nav_count"],
    "reading_len": lambda v: v["reading_len"],
}
CONFIG_DEFAULTS = {"n_per_cell_scenario_speed": 75, "block_size": 25, "assumed_reading_len": 12,
                   "kana_len_min": 4, "kana_len_max": 8}
CONFIG_REQUIRED = ("speeds", "key_hold_ms", "think_ms", "cand_index_max", "nav_count_max", "inter_trial_ms",
                   "marker", "marker_gap_ms", "reset_ime_every_block")
CONFIG_KNOWN = frozenset(CONFIG_REQUIRED) | frozenset(CONFIG_DEFAULTS) | {"scenarios"}
ESTIMATE_BASE = "かんじへんかんのそくどをはかる"


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- randomness (random() only)

def _derive(seed, tag):
    h = hashlib.sha256(("%d:%s" % (seed, tag)).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def trial_rng(seed, trial_id):
    """Per-trial random state, derived from (seed, trial_id) so that calling build_trial directly
    reproduces the matching row of a generated plan."""
    return random.Random(_derive(seed, trial_id))


def _below(rng, k):
    return min(int(rng.random() * k), k - 1)


def _randint(rng, lo, hi):
    return lo + _below(rng, hi - lo + 1)


def _shuffle(rng, xs):
    for i in range(len(xs) - 1, 0, -1):
        j = _below(rng, i + 1)
        xs[i], xs[j] = xs[j], xs[i]


def _normal(rng):
    u1 = 1.0 - rng.random()
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _draw_iki(rng, speed_cfg):
    """Draws a key interval (log-normal, median iki_median_ms), in whole milliseconds."""
    v = speed_cfg["iki_median_ms"] * math.exp(speed_cfg["iki_sigma"] * _normal(rng))
    return max(1, int(round(v)))


def _draw_think(rng, think):
    """Draws a think-time pause (log-uniform)."""
    lo, hi = think["min"], think["max"]
    v = math.exp(math.log(lo) + rng.random() * (math.log(hi) - math.log(lo)))
    return min(hi, max(lo, int(round(v))))


# ---------------------------------------------------------------- configuration

def unknown_config_keys(config):
    return sorted(k for k in config if k not in CONFIG_KNOWN and not str(k).startswith("_"))


def normalize_config(config):
    """Fills in defaults and checks types and ranges (idempotent). Raises ValueError on anything invalid."""
    if not isinstance(config, dict):
        raise ValueError("config is not an object")
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(config)
    missing = [k for k in CONFIG_REQUIRED if k not in cfg]
    if missing:
        raise ValueError("config is missing keys: " + ", ".join(missing))

    def need_int(key, lo):
        if not _is_int(cfg[key]) or cfg[key] < lo:
            raise ValueError("config %s must be an integer >= %d" % (key, lo))

    need_int("n_per_cell_scenario_speed", 1)
    need_int("block_size", 1)
    need_int("assumed_reading_len", 2)
    need_int("kana_len_min", 1)
    need_int("kana_len_max", cfg["kana_len_min"])
    need_int("key_hold_ms", 1)
    need_int("cand_index_max", 0)
    need_int("nav_count_max", 0)
    need_int("inter_trial_ms", 0)
    need_int("marker_gap_ms", 0)
    if not isinstance(cfg["reset_ime_every_block"], bool):
        raise ValueError("config reset_ime_every_block must be a boolean")
    sp = cfg["speeds"]
    if not isinstance(sp, dict):
        raise ValueError("config speeds must be an object")
    for name in SPEEDS:
        s = sp.get(name)
        if not isinstance(s, dict) or not _is_num(s.get("iki_median_ms")) or s["iki_median_ms"] <= 0 \
                or not _is_num(s.get("iki_sigma")) or s["iki_sigma"] < 0:
            raise ValueError("config speeds.%s must have iki_median_ms (> 0) and iki_sigma (>= 0)" % name)
    th = cfg["think_ms"]
    if not isinstance(th, dict) or not _is_int(th.get("min")) or not _is_int(th.get("max")) \
            or th["min"] < 1 or th["max"] < th["min"]:
        raise ValueError("config think_ms must be integers with 1 <= min <= max")
    mk = cfg["marker"]
    if not isinstance(mk, list) or any(k not in KEYNAMES for k in mk):
        raise ValueError("config marker must be an array of keynames")
    if cfg.get("scenarios") is not None:
        sc = cfg["scenarios"]
        if not isinstance(sc, list) or not sc or len(set(sc)) != len(sc) or any(s not in SCENARIOS for s in sc):
            raise ValueError("config scenarios must be a non-empty array of distinct values from S1-S15")
    return cfg


# ---------------------------------------------------------------- recipes

def _check_step_element(where, el, params):
    if not isinstance(el, dict):
        raise ValueError("%s: a step element is not an object" % where)
    if "type_reading" in el:
        extra = set(el) - {"type_reading", "part", "input"}
        if el["type_reading"] is not True or extra:
            raise ValueError("%s: malformed type_reading" % where)
        if el.get("part", "all") not in ("all", "first_half"):
            raise ValueError("%s: part must be all or first_half" % where)
        if el.get("input", "romaji") not in ("romaji", "kana"):
            raise ValueError("%s: input must be romaji or kana" % where)
    elif "key" in el:
        if set(el) != {"key"} or el["key"] not in KEYNAMES:
            raise ValueError("%s: malformed key, or an unknown keyname" % where)
    elif "repeat" in el:
        if set(el) != {"repeat", "times"} or el["repeat"] not in KEYNAMES:
            raise ValueError("%s: malformed repeat, or an unknown keyname" % where)
        t = el["times"]
        if not (_is_int(t) and t >= 0) and t not in TIMES:
            raise ValueError("%s: times must be an integer >= 0 or one of %s" % (where, "/".join(sorted(TIMES))))
        for need in ("cand_index", "nav_count"):
            if t == need or t == need + "+1":
                if need not in params:
                    raise ValueError("%s: times refers to %s, which is not in params" % (where, need))
    elif "digit" in el:
        if set(el) != {"digit"} or el["digit"] != "cand_index+1" or "cand_index" not in params:
            raise ValueError("%s: digit must be cand_index+1 only (params must define cand_index)" % where)
    elif "pause" in el:
        if set(el) != {"pause"} or el["pause"] != "think":
            raise ValueError("%s: pause must be think only" % where)
    elif "pause_ms" in el:
        if set(el) != {"pause_ms"} or not _is_int(el["pause_ms"]) or el["pause_ms"] < 0:
            raise ValueError("%s: pause_ms must be an integer >= 0" % where)
    else:
        raise ValueError("%s: unknown step element" % where)


def check_recipes(data):
    if not isinstance(data, dict):
        raise ValueError("recipes is not an object")
    layout = data.get("kana_layout")
    if not isinstance(layout, dict) or not layout:
        raise ValueError("recipes.kana_layout is missing")
    for k, v in layout.items():
        if k not in KEYNAMES or not isinstance(v, str) or len(v) != 1 or not READING_RE.fullmatch(v):
            raise ValueError("an entry in recipes.kana_layout is malformed")
    if len(set(layout.values())) != len(layout):
        raise ValueError("recipes.kana_layout maps the same kana from two different keys")
    table = data.get("os")
    if not isinstance(table, dict) or set(table) != set(OS_NAMES):
        raise ValueError("recipes.os must have entries for both macos and windows")
    for os_name in OS_NAMES:
        for sc, entry in table[os_name].items():
            where = "recipes.os.%s.%s" % (os_name, sc)
            if sc not in SCENARIOS or not isinstance(entry, dict):
                raise ValueError("%s: bad scenario name or malformed entry" % where)
            extra = set(entry) - {"steps", "intent", "params", "default_enabled", "reading_len"}
            if extra or "steps" not in entry or "intent" not in entry:
                raise ValueError("%s: bad keys (steps and intent are required; no unknown keys allowed)" % where)
            params = entry.get("params", {})
            for name, spec in params.items():
                if name not in ("cand_index", "nav_count") or not isinstance(spec, dict) \
                        or not _is_int(spec.get("min")) or set(spec) - {"min", "max", "cap"} \
                        or not (_is_int(spec.get("max")) or spec.get("max") in ("cand_index_max", "nav_count_max")):
                    raise ValueError("%s: malformed params" % where)
            if not isinstance(entry["steps"], list):
                raise ValueError("%s: steps is not an array" % where)
            for i, el in enumerate(entry["steps"]):
                _check_step_element("%s.steps[%d]" % (where, i), el, params)
            it = entry["intent"]
            if not isinstance(it, dict) or set(it) != set(INTENT_KEYS):
                raise ValueError("%s: intent must set all 8 keys" % where)
            for k, v in it.items():
                if isinstance(v, dict):
                    if set(v) != {"$param"} or v["$param"] not in params:
                        raise ValueError("%s: malformed $param in intent.%s" % (where, k))
                    continue
                if k == "commit":
                    if v not in COMMITS:
                        raise ValueError("%s: intent.commit is not one of the allowed values" % where)
                elif k == "cand_index":
                    if v is not None and not (_is_int(v) and v >= 0):
                        raise ValueError("%s: intent.cand_index must be an integer >= 0 or null" % where)
                elif k == "nav_count":
                    if not (_is_int(v) and v >= 0):
                        raise ValueError("%s: intent.nav_count must be an integer >= 0" % where)
                elif k in INTENT_BOOLS:
                    if not isinstance(v, bool):
                        raise ValueError("%s: intent.%s must be a boolean" % (where, k))
            if "default_enabled" in entry and not isinstance(entry["default_enabled"], bool):
                raise ValueError("%s: default_enabled must be a boolean" % where)
            rl = entry.get("reading_len")
            if rl is not None:
                rl_bad = not isinstance(rl, dict) or not rl or set(rl) - {"min", "max"} \
                    or ("min" in rl and not (_is_int(rl["min"]) and rl["min"] >= 1)) \
                    or ("max" in rl and not (_is_int(rl["max"]) and rl["max"] >= 1)) \
                    or ("min" in rl and "max" in rl and rl["min"] > rl["max"])
                if rl_bad:
                    raise ValueError("%s: reading_len must be {min?, max?} (integers >= 1, min<=max)" % where)
    return data


def load_recipes(path):
    with open(path, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))
    return check_recipes(data)


def _entry(recipes, os_name, scenario):
    if os_name not in OS_NAMES:
        raise ValueError("os must be macos or windows")
    entry = recipes["os"][os_name].get(scenario)
    if entry is None:
        raise ValueError("%s has no procedure for %s" % (os_name, scenario))
    if not entry["steps"]:
        raise ValueError("%s has no defined procedure (recipes' steps is empty)" % scenario)
    return entry


def _uses_kana(entry):
    return any(el.get("input") == "kana" for el in entry["steps"] if "type_reading" in el)


def _uses_think(entry):
    return any(el.get("pause") == "think" for el in entry["steps"])


# ---------------------------------------------------------------- one trial

def build_trial(phrase, scenario, speed, os_name, recipes, config, rng, trial_id, block_id):
    """Returns one plan/1 trial row (dict). Uses only rng.random()."""
    cfg = normalize_config(config)
    if speed not in SPEEDS:
        raise ValueError("speed must be slow / mid / fast")
    entry = _entry(recipes, os_name, scenario)
    reading = phrase["reading"]
    if not reading or not READING_RE.fullmatch(reading):
        raise ValueError("phrase.reading must be hiragana only")
    if phrase.get("reading_len", len(reading)) != len(reading):
        raise ValueError("phrase.reading_len does not match the length of reading")
    speed_cfg = cfg["speeds"][speed]
    hold = cfg["key_hold_ms"]
    v = {"reading_len": len(reading)}
    for name in sorted(entry.get("params", {})):
        spec = entry["params"][name]
        hi = spec["max"] if _is_int(spec["max"]) else cfg[spec["max"]]
        if "cap" in spec:
            hi = min(hi, spec["cap"])
        if hi < spec["min"]:
            raise ValueError("the range for %s's %s is empty (the config's upper bound is too low)" % (scenario, name))
        v[name] = _randint(rng, spec["min"], hi)
    think = _draw_think(rng, cfg["think_ms"]) if _uses_think(entry) else 0

    # Expand the procedure into "keys" and "the pause before the next key"
    seq = []
    for el in entry["steps"]:
        if "type_reading" in el:
            part = reading
            if el.get("part") == "first_half":
                part = reading[:max(1, len(reading) // 2)]
            if el.get("input") == "kana":
                inv = dict((c, k) for k, c in recipes["kana_layout"].items())
                if any(c not in inv for c in part):
                    raise ValueError("a character in the phrase cannot be typed on the kana layout (phrase %s)" % phrase.get("phrase_id"))
                keys = [inv[c] for c in part]
            else:
                keys = romaji.to_keys(part)
            seq.extend(("key", k) for k in keys)
        elif "key" in el:
            seq.append(("key", el["key"]))
        elif "repeat" in el:
            t = el["times"]
            n = t if _is_int(t) else TIMES[t](v)
            seq.extend(("key", el["repeat"]) for _ in range(n))
        elif "digit" in el:
            d = TIMES[el["digit"]](v)
            if not 1 <= d <= 9:
                raise ValueError("a digit key can only select candidate 1-9")
            seq.append(("key", str(d)))
        elif el.get("pause") == "think":
            seq.append(("pause", think))
        elif "pause_ms" in el:
            seq.append(("pause", el["pause_ms"]))

    steps = []
    t = 0
    override = None
    first = True
    for kind, val in seq:
        if kind == "pause":
            if not first:
                override = val
            continue
        if not first:
            gap = override if override is not None else _draw_iki(rng, speed_cfg)
            t += max(gap, hold + 1)
        override = None
        first = False
        steps.append({"key": val, "at_ms": t, "down": True})
        steps.append({"key": val, "at_ms": t + hold, "down": False})
    if not steps:
        raise ValueError("the procedure sends no keys at all")

    intent = {}
    for k in INTENT_KEYS:
        val = entry["intent"][k]
        intent[k] = v[val["$param"]] if isinstance(val, dict) else val
    return {"kind": "trial", "trial_id": trial_id, "block_id": block_id, "scenario": scenario, "speed": speed,
            "gap_before_ms": cfg["inter_trial_ms"], "think_ms": think if _uses_think(entry) else 0,
            "phrase_id": phrase["phrase_id"], "reading_len": len(reading), "steps": steps, "intent": intent}


_ROMAJI_READING_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz") | {"minus"}


def _trial_uses_kana(scenario, recipes):
    """Whether the given scenario is kana input (on either OS), read from recipes. A trial row itself
    does not carry os_name."""
    for os_name in OS_NAMES:
        entry = recipes["os"].get(os_name, {}).get(scenario)
        if entry is not None:
            return _uses_kana(entry)
    return False


def typed_reading(trial, recipes):
    """Returns, in hiragana, the reading actually typed for one plan/1 trial row (reading only the down
    steps). Romaji input is reconstructed through the inverse of romaji.from_keys; kana input (one
    scenario) through the inverse of recipes.kana_layout. The typed reading ends at the first key that
    scenario does not use for typing the reading (for romaji input, anything outside a-z / minus; for
    kana input, anything outside the kana_layout keys). Ordinarily this is the phrase's full reading; one
    scenario types only the first half, and another returns its synthetic kana string as-is."""
    downs = [s["key"] for s in trial["steps"] if s["down"]]
    if _trial_uses_kana(trial["scenario"], recipes):
        layout = recipes["kana_layout"]
        keys = []
        for k in downs:
            if k not in layout:
                break
            keys.append(k)
        return "".join(layout[k] for k in keys)
    keys = []
    for k in downs:
        if k not in _ROMAJI_READING_KEYS:
            break
        keys.append(k)
    return romaji.from_keys(keys)


# ---------------------------------------------------------------- corpus

def load_corpus(path):
    """Reads corpus/1 (violations raise ValueError without echoing the offending value)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raise ValueError("corpus has a BOM")
    lines = raw.decode("utf-8").split("\n")
    phrases = []
    seen = set()
    for ln, line in enumerate(lines, start=1):
        line = line.rstrip("\r")
        if not line:
            if ln == len(lines):
                continue
            raise ValueError("corpus line %d: blank line" % ln)
        try:
            obj = json.loads(line)
        except ValueError:
            raise ValueError("corpus line %d: not readable as JSON" % ln)
        if ln == 1:
            if not isinstance(obj, dict) or obj.get("kind") != "header" or obj.get("format") != "corpus/1" \
                    or not isinstance(obj.get("tokenizer"), str) or not DICT_RE.fullmatch(obj["tokenizer"]) \
                    or not isinstance(obj.get("dict"), str) or not DICT_RE.fullmatch(obj["dict"]):
                raise ValueError("corpus header does not match corpus/1")
            continue
        ok = isinstance(obj, dict) and obj.get("kind") == "phrase" and isinstance(obj.get("phrase_id"), str) \
            and isinstance(obj.get("work_id"), str) and isinstance(obj.get("reading"), str)
        if ok:
            ok = PHRASE_ID_RE.fullmatch(obj["phrase_id"]) and WORK_ID_RE.fullmatch(obj["work_id"]) \
                and obj["phrase_id"].startswith(obj["work_id"] + "-") and READING_RE.fullmatch(obj["reading"]) \
                and _is_int(obj.get("reading_len")) and obj["reading_len"] == len(obj["reading"]) \
                and isinstance(obj.get("text"), str) and not obj["phrase_id"].startswith("kana-")
        if not ok:
            raise ValueError("corpus line %d: phrase row does not match the corpus/1 schema" % ln)
        if obj["phrase_id"] in seen:
            raise ValueError("corpus line %d: duplicate phrase_id" % ln)
        seen.add(obj["phrase_id"])
        phrases.append(obj)
    if not phrases:
        raise ValueError("corpus has no phrases")
    return phrases


# ---------------------------------------------------------------- plan

class _Pool(object):
    """A draw pool of phrases. When cycle is set, the pool reshuffles and repeats once exhausted;
    otherwise it returns None once exhausted."""

    def __init__(self, phrases, rng, cycle):
        self.phrases = list(phrases)
        self.rng = rng
        self.cycle = cycle
        self.order = []
        self.pos = 0
        self._refill()

    def _refill(self):
        self.order = list(self.phrases)
        _shuffle(self.rng, self.order)
        self.pos = 0

    def next(self):
        if self.pos >= len(self.order):
            if not self.cycle or not self.phrases:
                return None
            self._refill()
        p = self.order[self.pos]
        self.pos += 1
        return p


def enabled_scenarios(recipes, cfg, os_name):
    """Returns the list of enabled scenarios and a list of warnings (not printed here; the caller prints
    them)."""
    table = recipes["os"][os_name]
    warnings = []
    if cfg.get("scenarios") is None:
        return [s for s in SCENARIOS if s in table and table[s].get("default_enabled", True)], warnings
    out = []
    for s in cfg["scenarios"]:
        if s in table:
            out.append(s)
        else:
            warnings.append("%s has no procedure for %s; skipping" % (os_name, s))
    return out, warnings


def plan_rows(corpus, recipes, config, os_name, seed, config_sha256, recipes_sha256, corpus_sha256, warnings=None):
    """Returns the plan rows (a list of dicts) and {trial_id: phrase}. Nothing is printed to standard
    error here. Passing a list as `warnings` appends notices such as skipped scenarios (the caller prints
    them)."""
    cfg = normalize_config(config)
    if os_name not in OS_NAMES:
        raise ValueError("os must be macos or windows")
    if not _is_int(seed) or not 0 <= seed <= SEED_MAX:
        raise ValueError("seed must be an integer in 0..2^63-1")
    scen, scen_warnings = enabled_scenarios(recipes, cfg, os_name)
    if warnings is not None:
        warnings.extend(scen_warnings)
    if not scen:
        raise ValueError("no scenario is enabled")
    for s in scen:
        _entry(recipes, os_name, s)
    layout = recipes["kana_layout"]
    inv = dict((c, k) for k, c in layout.items())
    typable = [p for p in corpus if all(c in inv for c in p["reading"])]
    prng = random.Random(_derive(seed, "plan"))
    n = cfg["n_per_cell_scenario_speed"]
    synth_ids = {}
    layout_chars = sorted(inv)

    def synth():
        length = _randint(prng, cfg["kana_len_min"], cfg["kana_len_max"])
        reading = "".join(layout_chars[_below(prng, len(layout_chars))] for _ in range(length))
        if reading not in synth_ids:
            synth_ids[reading] = len(synth_ids) + 1
        return {"phrase_id": "kana-%d" % synth_ids[reading], "work_id": "kana", "text": "", "reading": reading,
                "reading_len": length}

    specs = dict((sp, []) for sp in SPEEDS)
    for s in scen:
        entry = recipes["os"][os_name][s]
        kana = _uses_kana(entry)
        rl = entry.get("reading_len", {})
        lo, hi = rl.get("min", 1), rl.get("max", 10 ** 9)
        eligible = [p for p in (typable if kana else corpus) if lo <= p["reading_len"] <= hi]
        if not eligible and not kana:
            raise ValueError("no phrase in the corpus matches %s's reading-length requirement" % s)
        pool = _Pool(eligible, prng, cycle=not kana)
        for sp in SPEEDS:
            for _ in range(n):
                ph = pool.next()
                if ph is None:
                    ph = synth()
                specs[sp].append((s, ph))
    blocks = []
    for sp in SPEEDS:
        lst = specs[sp]
        _shuffle(prng, lst)
        for i in range(0, len(lst), cfg["block_size"]):
            blocks.append((sp, lst[i:i + cfg["block_size"]]))
    _shuffle(prng, blocks)

    rows = [{"kind": "header", "format": "plan/1", "os": os_name, "seed": seed,
             "generator_version": GENERATOR_VERSION, "config_sha256": config_sha256,
             "recipes_sha256": recipes_sha256, "corpus_sha256": corpus_sha256}]
    phrases = {}
    n_trial = 0
    for bid, (sp, items) in enumerate(blocks):
        rows.append({"kind": "block", "block_id": bid, "speed": sp, "reset_ime": cfg["reset_ime_every_block"],
                     "marker": list(cfg["marker"]), "marker_gap_ms": cfg["marker_gap_ms"]})
        for s, ph in items:
            n_trial += 1
            tid = "t%05d" % n_trial
            rows.append(build_trial(ph, s, sp, os_name, recipes, cfg, trial_rng(seed, tid), tid, bid))
            phrases[tid] = ph
    _check_marker(cfg["marker"], rows)
    return rows, phrases


def _arrow_run(keys):
    """The longest run of arrow keys among the observed keys of a key sequence (unobserved keys are skipped)."""
    best = cur = 0
    for k in keys:
        if k in UNOBSERVED_KEYS:
            continue
        cur = cur + 1 if k in ARROW_KEYS else 0
        best = max(best, cur)
    return best


def _check_marker(marker, rows):
    """The observation records only a key's category, so a marker whose observed keys are all arrow keys is found
    there as a run of navigation rows. It stays unique only when it is longer than every run of arrow presses inside
    a trial of the plan (see ../pilot/README.md); otherwise the plan is refused."""
    seen = [k for k in marker if k not in UNOBSERVED_KEYS]
    if not seen or any(k not in ARROW_KEYS for k in seen):
        return
    longest = max([_arrow_run([s["key"] for s in r["steps"] if s["down"]]) for r in rows if r["kind"] == "trial"]
                  or [0])
    if len(seen) <= longest:
        raise ValueError("the marker leaves %d arrow keys in the observation, but a trial of this plan presses %d arrow "
                         "keys in a row, so the two cannot be told apart; make the marker longer or lower "
                         "nav_count_max" % (len(seen), longest))


def render_plan(rows):
    """The plan's byte string (UTF-8, LF, no BOM)."""
    return ("\n".join(dumps(r) for r in rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------- estimate

def estimate(config, recipes, os_name, corpus=None):
    """Returns the duration of one cell (every step, plus inter-trial gaps, plus markers) as
    (seconds, description lines)."""
    cfg = normalize_config(config)
    assumed = cfg["assumed_reading_len"]
    note = "reading length: corpus phrases"
    if corpus is None:
        reading = (ESTIMATE_BASE * (assumed // len(ESTIMATE_BASE) + 1))[:assumed]
        corpus = [{"phrase_id": "est-1-1", "work_id": "est", "text": "", "reading": reading, "reading_len": assumed}]
        cfg = dict(cfg, kana_len_min=assumed, kana_len_max=assumed)
        note = "reading length: assumed %d characters (config assumed_reading_len)" % assumed
    rows, _ = plan_rows(corpus, recipes, cfg, os_name, 0, "0" * 64, "0" * 64, "0" * 64)
    trial_ms = gap_ms = marker_ms = n_trial = n_block = 0
    for r in rows:
        if r["kind"] == "trial":
            n_trial += 1
            trial_ms += r["steps"][-1]["at_ms"]
            gap_ms += r["gap_before_ms"]
        elif r["kind"] == "block":
            n_block += 1
            marker_ms += len(r["marker"]) * r["marker_gap_ms"]
    total = (trial_ms + gap_ms + marker_ms) / 1000.0
    lines = ["%d trial(s), %d block(s) (%s)" % (n_trial, n_block, note),
             "%.1fs of trial steps + %.1fs of inter-trial gaps + %.1fs of markers (IME reset and window activation are not included)"
             % (trial_ms / 1000.0, gap_ms / 1000.0, marker_ms / 1000.0),
             "%.2fs per trial" % (total / n_trial if n_trial else 0.0),
             "Total (seconds):"]
    return total, lines


# ---------------------------------------------------------------- validation

class _Reject(ValueError):
    pass


def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _Reject("dup_key")
        d[k] = v
    return d


def _reject_number(_s):
    raise _Reject("float")


def validate_plan(data):
    """Validates plan/1. Returns [(line number, code, description)] (empty if valid). The description
    never echoes a value."""
    found = []

    def bad(ln, code, msg):
        found.append((ln, code, msg))

    if data[:3] == b"\xef\xbb\xbf":
        bad(1, "bom", "UTF-8 BOM present")
        return found
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        bad(1, "encoding", "not readable as UTF-8")
        return found
    if not text:
        bad(1, "empty", "empty file")
        return found
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()

    def keys_ok(ln, obj, expected, what):
        miss = [k for k in expected if k not in obj]
        extra = [k for k in obj if k not in expected]
        if miss:
            bad(ln, "missing_key", "%s is missing keys: %s" % (what, ",".join(miss)))
        if extra:
            bad(ln, "extra_key", "%s has unknown keys (%d)" % (what, len(extra)))
        return not miss and not extra

    blocks = []          # (block_id value, speed, line number, trial-count holder)
    n_trials = 0
    for ln, raw in enumerate(lines, start=1):
        line = raw[:-1] if raw.endswith("\r") else raw
        if not line:
            bad(ln, "blank", "blank line present")
            continue
        if chr(92) in line:
            bad(ln, "escape", "line contains a backslash (escaping is not used)")
            continue
        try:
            obj = json.loads(line, object_pairs_hook=_no_dups, parse_float=_reject_number,
                             parse_constant=_reject_number)
        except _Reject as e:
            bad(ln, str(e), "duplicate key, or a non-integer number")
            continue
        except (ValueError, RecursionError):
            bad(ln, "json", "not readable as JSON")
            continue
        if not isinstance(obj, dict):
            bad(ln, "type", "not an object")
            continue
        if dumps(obj) != line:
            bad(ln, "format", "separators, escaping, or number formatting is not canonical (no whitespace, no escaping)")
        if ln == 1:
            if not keys_ok(ln, obj, HEADER_KEYS, "header"):
                continue
            if obj["kind"] != "header" or obj["format"] != "plan/1":
                bad(ln, "header", "line 1 is not a plan/1 header")
            if obj["os"] not in OS_NAMES:
                bad(ln, "header", "os is not one of the allowed values")
            if not _is_int(obj["seed"]) or not 0 <= obj["seed"] <= SEED_MAX:
                bad(ln, "header", "seed is not an integer in 0..2^63-1")
            if not isinstance(obj["generator_version"], str) or not VERSION_RE.fullmatch(obj["generator_version"]):
                bad(ln, "header", "generator_version has a malformed value")
            if not isinstance(obj["config_sha256"], str) or not SHA_RE.fullmatch(obj["config_sha256"]):
                bad(ln, "header", "config_sha256 is not 64 lowercase hex digits")
            if not isinstance(obj["recipes_sha256"], str) or not SHA_RE.fullmatch(obj["recipes_sha256"]):
                bad(ln, "header", "recipes_sha256 is not 64 lowercase hex digits")
            if not isinstance(obj["corpus_sha256"], str) or not SHA_RE.fullmatch(obj["corpus_sha256"]):
                bad(ln, "header", "corpus_sha256 is not 64 lowercase hex digits")
            continue
        kind = obj.get("kind")
        if kind == "block":
            if not keys_ok(ln, obj, BLOCK_KEYS, "block"):
                continue
            if obj["block_id"] != len(blocks) or not _is_int(obj["block_id"]):
                bad(ln, "block_id", "block_id is not a 0-based running count")
            if obj["speed"] not in SPEEDS:
                bad(ln, "speed", "block speed is not one of the allowed values")
            if not isinstance(obj["reset_ime"], bool):
                bad(ln, "type", "reset_ime is not a boolean")
            if not isinstance(obj["marker"], list) or any(
                    not isinstance(k, str) or k not in KEYNAMES for k in obj["marker"]):
                bad(ln, "keyname", "marker has an element that is not a keyname")
            if not _is_int(obj["marker_gap_ms"]) or obj["marker_gap_ms"] < 0:
                bad(ln, "type", "marker_gap_ms is not an integer >= 0")
            blocks.append([obj["block_id"], obj["speed"], ln, 0])
        elif kind == "trial":
            n_trials += 1
            if not keys_ok(ln, obj, TRIAL_KEYS, "trial"):
                continue
            if not isinstance(obj["trial_id"], str) or not TRIAL_ID_RE.fullmatch(obj["trial_id"]) \
                    or obj["trial_id"] != "t%05d" % n_trials:
                bad(ln, "trial_id", "trial_id is not t followed by a 1-based running count of at least 5 digits")
            blk = None
            if blocks and _is_int(obj["block_id"]) and obj["block_id"] == blocks[-1][0]:
                blk = blocks[-1]
                blk[3] += 1
            else:
                bad(ln, "block_ref", "block_id does not match the immediately preceding block row's block_id")
            if obj["scenario"] not in SCENARIOS:
                bad(ln, "scenario", "scenario is not one of S1-S15")
            if obj["speed"] not in SPEEDS:
                bad(ln, "speed", "speed is not one of the allowed values")
            elif blk is not None and obj["speed"] != blk[1]:
                bad(ln, "speed_mismatch", "trial speed does not match the block's speed")
            for k in ("gap_before_ms", "think_ms"):
                if not _is_int(obj[k]) or obj[k] < 0:
                    bad(ln, "type", "%s is not an integer >= 0" % k)
            if not isinstance(obj["phrase_id"], str) or not PHRASE_ID_RE.fullmatch(obj["phrase_id"]):
                bad(ln, "phrase_id", "phrase_id is malformed")
            if not _is_int(obj["reading_len"]) or obj["reading_len"] < 1:
                bad(ln, "type", "reading_len is not an integer >= 1")
            _check_steps(ln, obj["steps"], bad)
            _check_intent(ln, obj["intent"], bad)
        else:
            bad(ln, "kind", "kind is none of header / block / trial (or a header appears after line 1)")
    if n_trials == 0:
        bad(1, "no_trial", "no trial is present")
    for blk in blocks:
        if blk[3] == 0:
            bad(blk[2], "empty_block", "a block has no trial")
    return found


def _check_steps(ln, steps, bad):
    if not isinstance(steps, list) or not steps:
        bad(ln, "steps", "steps is not a non-empty array")
        return
    held = set()
    prev = -1
    order_bad = pair_bad = key_bad = shape_bad = False
    for st in steps:
        if not isinstance(st, dict) or set(st) != {"key", "at_ms", "down"}:
            shape_bad = True
            continue
        if not isinstance(st["key"], str) or st["key"] not in KEYNAMES:
            key_bad = True
            continue
        if not _is_int(st["at_ms"]) or st["at_ms"] < 0 or not isinstance(st["down"], bool):
            shape_bad = True
            continue
        if st["at_ms"] < prev:
            order_bad = True
        prev = max(prev, st["at_ms"])
        if st["down"]:
            if st["key"] in held:
                pair_bad = True
            held.add(st["key"])
        else:
            if st["key"] not in held:
                pair_bad = True
            held.discard(st["key"])
    if held:
        pair_bad = True
    if shape_bad:
        bad(ln, "step_shape", "a steps element is not {key, at_ms (integer >= 0), down (boolean)}")
    if key_bad:
        bad(ln, "keyname", "steps has a key outside the keyname enumeration")
    if order_bad:
        bad(ln, "at_ms_order", "at_ms is not monotonically non-decreasing")
    if pair_bad:
        bad(ln, "pair", "a down and an up are not paired")


def _check_intent(ln, intent, bad):
    if not isinstance(intent, dict) or set(intent) != set(INTENT_KEYS):
        bad(ln, "intent_keys", "intent does not have exactly the 8 required keys")
        return
    if intent["commit"] not in COMMITS:
        bad(ln, "intent_type", "intent.commit is not one of the allowed values")
    ci = intent["cand_index"]
    if ci is not None and (not _is_int(ci) or ci < 0):
        bad(ln, "intent_type", "intent.cand_index is not an integer >= 0 or null")
    if not _is_int(intent["nav_count"]) or intent["nav_count"] < 0:
        bad(ln, "intent_type", "intent.nav_count is not an integer >= 0")
    for k in INTENT_BOOLS:
        if not isinstance(intent[k], bool):
            bad(ln, "intent_type", "intent.%s is not a boolean" % k)


# ---------------------------------------------------------------- selftest

def _selftest_env(tmp):
    readings = ["あおぞら", "にほん", "がっこう", "みなさん", "たのしい", "きょうは", "ふぁんたじー", "てんき", "ゆめ",
                "ちょっと", "はなし", "ほんとう", "おおきな", "ながれる"]
    lines = ['{"kind":"header","format":"corpus/1","source":"aozora","tokenizer":"table","dict":"none"}']
    for i, r in enumerate(readings, start=1):
        lines.append(dumps({"kind": "phrase", "phrase_id": "w1-%d-1" % i, "work_id": "w1", "text": "x",
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
    return corpus_path, cfg, cfg_path


def _sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# Keys each procedure profile ends a scenario with (see the README's Limitations). "commit" follows a conversion
# after one Space (and, in S7 / S15, the arrows or the focus switch that come after it), "commit_cands" follows the
# candidate window opened by two or more Spaces, "after_digit" follows a digit pick, "cancel" cancels an
# unconverted reading and "cancel_converted" cancels a reading converted by one Space (S4). A commit that follows no
# Space (S3, S10, S11) takes one Return under every profile and is not listed here.
# - macos-live-on (recipes.json): one Space already opens the candidate window, where a Return only picks the
#   candidate, so every commit after a Space takes a second Return, and so does a digit pick; each Escape steps the
#   live conversion back one stage (kanji, kana, spelling), so the cancel presses four (extra presses only reach the
#   field as a command).
# - macos-live-off (recipes.macos-live-off.json): one Space shows the conversion in place and one Return commits
#   it; a second Space opens the candidate window, which still takes two Returns, and a digit pick is followed by one
#   Return; one Escape cancels.
# - both macOS profiles cancel a converted reading with two Escapes.
# - windows (either file): one Return for each commit and one Escape for the cancel; a digit pick only selects the
#   candidate and takes one Return to commit; after a conversion into several segments the first Escape reverts the
#   focused segment and the second returns to kana, so the cancel of a converted reading presses three (a reading in
#   one segment is cancelled by the second, and the third then falls outside the composition); this is a deviation
#   from the macOS freeze, recorded in experiment/deviations.md (D1).
_PROFILES = {
    "windows": {"commit": ["enter"], "commit_cands": ["enter"], "after_digit": ["enter"], "cancel": ["esc"],
                "cancel_converted": ["esc"] * 3},
    "macos-live-on": {"commit": ["enter", "enter"], "commit_cands": ["enter", "enter"], "after_digit": ["enter"],
                      "cancel": ["esc"] * 4, "cancel_converted": ["esc", "esc"]},
    "macos-live-off": {"commit": ["enter"], "commit_cands": ["enter", "enter"], "after_digit": ["enter"],
                       "cancel": ["esc"], "cancel_converted": ["esc", "esc"]},
}


def _scenario_checks(rows, phrases, recipes, cfg, fails, profile):
    """Checks that each scenario's procedure matches recipes' intent (a positive content check).

    ``profile`` is a key of ``_PROFILES`` and must match the recipes file and the OS the plan was built for.
    """
    tail = _PROFILES[profile]
    enter = tail["commit"]
    seen = set()
    for r in rows:
        if r["kind"] != "trial":
            continue
        sc, ph, it = r["scenario"], phrases[r["trial_id"]], r["intent"]
        downs = [s["key"] for s in r["steps"] if s["down"]]
        at = [s["at_ms"] for s in r["steps"] if s["down"]]
        rd = ph["reading"]
        full = romaji.to_keys(rd)
        seen.add(sc)
        ok = True
        if sc == "S1":
            ok = downs == full + ["space"] + enter and cfg["think_ms"]["min"] <= r["think_ms"] <= cfg["think_ms"]["max"]
        elif sc == "S2":
            k = it["cand_index"] + 1
            ok = 1 <= it["cand_index"] <= cfg["cand_index_max"] and downs == full + ["space"] * k + tail["commit_cands"]
        elif sc == "S3":
            ok = downs == full + ["enter"] and r["think_ms"] == 0
        elif sc == "S4":
            ok = downs == full + ["space"] + tail["cancel_converted"] and it["abandon"] is True
        elif sc == "S5":
            ok = downs == full + tail["cancel"]
        elif sc == "S6":
            ok = downs == full + ["backspace"] * len(rd)
        elif sc == "S7":
            ok = downs == full + ["space"] + ["right"] * it["nav_count"] + enter \
                and 1 <= it["nav_count"] <= cfg["nav_count_max"]
        elif sc == "S8":
            ok = downs == full + ["space", "space", str(it["cand_index"] + 1)] + tail["after_digit"] and it["commit"] == "digit"
        elif sc == "S10":
            ok = downs == romaji.to_keys(rd[:max(1, len(rd) // 2)]) + ["tab", "enter"] and it["predictive"] is True
        elif sc == "S11":
            ok = downs == full + ["enter"]
        elif sc == "S12":
            m = 1 + len(enter)
            ok = downs[-m:] == ["space"] + enter and all(k in recipes["kana_layout"] for k in downs[:-m]) \
                and "".join(recipes["kana_layout"][k] for k in downs[:-m]) == rd
        elif sc == "S13":
            m = 1 + len(enter)
            ok = downs[-m:] == ["space"] + enter and at[-m + 1] - at[-m] == 21000
        elif sc == "S14":
            ok = downs == full + ["space", "ime_off", "ime_on"] and it["ime_toggle_mid"] is True
        elif sc == "S15":
            i = downs.index("focus_away")
            ok = downs[i:] == ["focus_away", "focus_back"] + enter and at[i + 1] - at[i] == 1000
        if not ok:
            fails.append("scenario %s's procedure does not match recipes' intent under the %s profile (%s)"
                         % (sc, profile, r["trial_id"]))
            return
    return seen


def _median(xs):
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def selftest():
    fails = []
    n = [0]

    def check(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append(name + (": " + detail if detail else ""))

    recipes_path = os.path.join(_HERE, "recipes.json")
    tmp = tempfile.mkdtemp(prefix="generate_plan_selftest_")
    try:
        recipes = load_recipes(recipes_path)
        corpus_path, cfg, cfg_path = _selftest_env(tmp)
        corpus = load_corpus(corpus_path)
        sha = _sha_file(cfg_path)
        rsha = _sha_file(recipes_path)
        csha = _sha_file(corpus_path)
        ncfg = normalize_config(cfg)

        def gen(os_name="macos", seed=7, config=None):
            rows, ph = plan_rows(corpus, recipes, config if config is not None else cfg, os_name, seed, sha,
                                  rsha, csha)
            return rows, ph

        rows1, ph1 = gen()
        bytes1 = render_plan(rows1)
        check("recipes.json passes validation", True)

        # --- determinism and the effect of the seed (case names checked by the acceptance script)
        check("same seed yields byte-identical output", render_plan(gen()[0]) == bytes1)
        rows2, _ = gen(seed=8)
        order = lambda rs: [(r["scenario"], r["phrase_id"]) for r in rs if r["kind"] == "trial"]
        check("a different seed changes the trial order (negative control)", order(rows1) != order(rows2) and render_plan(rows2) != bytes1)
        check("no BOM, LF only, ends with a newline", not bytes1.startswith(b"\xef\xbb\xbf") and b"\r" not in bytes1
              and bytes1.endswith(b"\n"))

        # --- counts and blocks
        trials = [r for r in rows1 if r["kind"] == "trial"]
        blocks = [r for r in rows1 if r["kind"] == "block"]
        scen_mac = [s for s in SCENARIOS if s in recipes["os"]["macos"] and recipes["os"]["macos"][s].get("default_enabled", True)]
        n_cell = ncfg["n_per_cell_scenario_speed"]
        check("trial count = number of scenarios x 3 x n", len(trials) == len(scen_mac) * 3 * n_cell, "%d" % len(trials))
        per_speed = len(scen_mac) * n_cell
        check("block count = sum over speeds of ceil(trial count/block_size)",
              len(blocks) == 3 * -(-per_speed // ncfg["block_size"]), "%d" % len(blocks))
        check("matches ceil(trial count / block_size) when it divides evenly",
              per_speed % ncfg["block_size"] != 0 or len(blocks) == -(-len(trials) // ncfg["block_size"]))
        rows_w, ph_w = gen("windows")
        blocks_w = [r for r in rows_w if r["kind"] == "block"]
        trials_w = [r for r in rows_w if r["kind"] == "trial"]
        check("windows block count (matches the formula even when it does not divide evenly)",
              len(blocks_w) == 3 * -(-((len(scen_mac) - 1) * n_cell) // ncfg["block_size"]), "%d" % len(blocks_w))
        check("blocks are homogeneous in speed (trial speed = its block row's speed)",
              all(t["speed"] == blocks[t["block_id"]]["speed"] for t in trials))
        check("block rows carry marker, marker_gap_ms, and reset_ime from the config",
              all(b["marker"] == cfg["marker"] and b["marker_gap_ms"] == 150 and b["reset_ime"] is True for b in blocks))
        check("trial rows carry gap_before_ms from the config", all(t["gap_before_ms"] == 1000 for t in trials))
        check("trial_id and block_id are running counts", [t["trial_id"] for t in trials] == ["t%05d" % i for i in range(1, len(trials) + 1)]
              and [b["block_id"] for b in blocks] == list(range(len(blocks))))
        check("block order is permuted by the seed (the three speeds are not grouped together)",
              [b["speed"] for b in blocks] != sorted(b["speed"] for b in blocks))

        # --- two scenarios with OS-conditional behavior
        check("one scenario does not appear by default", "S9" not in set(t["scenario"] for t in trials))
        check("one live-conversion scenario appears on macos", "S11" in set(t["scenario"] for t in trials))
        check("that scenario does not appear on windows", "S11" not in set(t["scenario"] for t in trials_w))
        for os_name in OS_NAMES:
            try:
                gen(os_name, config=dict(cfg, scenarios=["S9"]))
                check("naming an undefined scenario explicitly still stops the run (%s)" % os_name, False, "did not stop")
            except ValueError:
                check("naming an undefined scenario explicitly still stops the run (%s)" % os_name, True)
        rows_s11, _ = gen("windows", config=dict(cfg, scenarios=["S1", "S11"]))
        check("naming the unsupported scenario explicitly on windows still builds the rest, skipping it",
              set(t["scenario"] for t in rows_s11 if t["kind"] == "trial") == {"S1"})

        # --- speed medians are ordered
        rows_s3, _ = gen(config=dict(cfg, scenarios=["S3"], n_per_cell_scenario_speed=40, block_size=40))
        med = {}
        for sp in SPEEDS:
            gaps = []
            for t in rows_s3:
                if t["kind"] == "trial" and t["speed"] == sp:
                    ds = [s["at_ms"] for s in t["steps"] if s["down"]]
                    gaps.extend(b - a for a, b in zip(ds, ds[1:]))
            med[sp] = _median(gaps)
        check("key-interval medians order as slow > mid > fast", med["slow"] > med["mid"] > med["fast"], str(med))
        check("key-interval medians are close to the configured medians (within 10%)",
              all(abs(med[sp] - cfg["speeds"][sp]["iki_median_ms"]) <= 0.1 * cfg["speeds"][sp]["iki_median_ms"]
                  for sp in SPEEDS), str(med))

        # --- per-scenario procedures (recipes' intent)
        seen = _scenario_checks(rows1, ph1, recipes, ncfg, fails, "macos-live-on")
        n[0] += 1
        check("all 14 scenarios appear under the macos default", seen == set(scen_mac), str(sorted(seen or [])))
        seen_w = _scenario_checks(rows_w, ph_w, recipes, ncfg, fails, "windows")
        n[0] += 1
        scen_win = [s for s in SCENARIOS
                    if s in recipes["os"]["windows"] and recipes["os"]["windows"][s].get("default_enabled", True)]
        check("every scenario enabled by default appears under the windows default", seen_w == set(scen_win),
              str(sorted(seen_w or [])))
        check("one scenario falls back to a synthetic kana string (kana-) when typable phrases run out",
              any(t["phrase_id"].startswith("kana-") for t in trials if t["scenario"] == "S12"))
        check("no other scenario uses a kana- phrase_id", all(not t["phrase_id"].startswith("kana-") for t in trials if t["scenario"] != "S12"))
        check("one scenario only draws phrases with a reading length of 2 or more", all(t["reading_len"] >= 2 for t in trials if t["scenario"] == "S10"))
        check("every key in steps has a paired down and up, and each key is in the enumeration",
              all(len(t["steps"]) % 2 == 0 and all(s["key"] in KEYNAMES for s in t["steps"]) for t in trials))
        check("at_ms is relative to the start of the trial (the first is 0)", all(t["steps"][0]["at_ms"] == 0 for t in trials))

        # --- typed_reading: matches expectations across every scenario and both operating systems
        def _typed_mismatches(rows, phrases, rec=recipes):
            bad = []
            for t in rows:
                if t["kind"] != "trial":
                    continue
                rd = phrases[t["trial_id"]]["reading"]
                want = rd[:max(1, len(rd) // 2)] if t["scenario"] == "S10" else rd
                got = typed_reading(t, rec)
                if got != want:
                    bad.append((t["trial_id"], t["scenario"], want, got))
            return bad

        bad_mac = _typed_mismatches(rows1, ph1)
        check("typed_reading matches expectations for every scenario (macos: ordinarily the full reading, one scenario the first half, one scenario the synthetic kana string)",
              not bad_mac, str(bad_mac[:3]))
        bad_win = _typed_mismatches(rows_w, ph_w)
        check("typed_reading matches expectations for every scenario (windows)", not bad_win, str(bad_win[:3]))

        # --- the recipes file for a macOS cell with live conversion off (the README's Limitations)
        off_path = os.path.join(_HERE, "recipes.macos-live-off.json")
        recipes_off = load_recipes(off_path)
        check("the live-off recipes keep the windows procedures and the kana layout of recipes.json",
              recipes_off["os"]["windows"] == recipes["os"]["windows"]
              and recipes_off["kana_layout"] == recipes["kana_layout"])
        check("both macOS files type the reading through the kana layout in the same scenarios (the open-set attack "
              "reads only this and the kana layout from the recipes)",
              sorted(recipes["os"]["macos"]) == sorted(recipes_off["os"]["macos"])
              and all(_uses_kana(recipes["os"]["macos"][s]) == _uses_kana(recipes_off["os"]["macos"][s])
                      for s in recipes["os"]["macos"]))
        rows_off, ph_off = plan_rows(corpus, recipes_off, cfg, "macos", 7, sha, _sha_file(off_path), csha)
        scen_off = [s for s in SCENARIOS
                    if s in recipes_off["os"]["macos"] and recipes_off["os"]["macos"][s].get("default_enabled", True)]
        check("the live-off recipes leave the live-conversion scenario out by default",
              "S11" not in scen_off and "S11" not in set(t["scenario"] for t in rows_off if t["kind"] == "trial"))
        try:
            plan_rows(corpus, recipes_off, dict(cfg, scenarios=["S1", "S11"]), "macos", 7, sha, _sha_file(off_path), csha)
            s11_named = "built"
        except ValueError as e:
            s11_named = str(e)
        check("naming the live-conversion scenario explicitly with the live-off recipes stops the build",
              "S11" in s11_named and "no defined procedure" in s11_named, s11_named)
        seen_off = _scenario_checks(rows_off, ph_off, recipes_off, ncfg, fails, "macos-live-off")
        n[0] += 1
        check("all 13 scenarios appear under the live-off macos default", seen_off == set(scen_off),
              str(sorted(seen_off or [])))
        cross_on, cross_off = [], []
        _scenario_checks(rows_off, ph_off, recipes_off, ncfg, cross_on, "macos-live-on")
        _scenario_checks(rows1, ph1, recipes, ncfg, cross_off, "macos-live-off")
        check("each macos profile rejects the plan built from the other recipes file (negative control)",
              bool(cross_on) and bool(cross_off))
        check("the generated plan (macos, live off) passes --validate", validate_plan(render_plan(rows_off)) == [])
        bad_off = _typed_mismatches(rows_off, ph_off, recipes_off)
        check("typed_reading matches expectations for every scenario (macos, live off)", not bad_off, str(bad_off[:3]))

        # --- public functions do not write to standard error; warnings are returned instead
        _buf = io.StringIO()
        with contextlib.redirect_stderr(_buf):
            _warns = []
            plan_rows(corpus, recipes, dict(cfg, scenarios=["S1", "S11"]), "windows", 1, sha, rsha, csha,
                      warnings=_warns)
        check("plan_rows / enabled_scenarios do not write to standard error; a warning about the windows-unsupported scenario is returned instead",
              _buf.getvalue() == "" and any("S11" in w for w in _warns), repr(_buf.getvalue()))

        # --- check_recipes: type checks on intent values and on the reading_len specification
        def _bad_recipes(mutate_fn):
            data = json.loads(json.dumps(recipes))
            mutate_fn(data)
            return data

        for name, fn in [
            ("intent.commit outside the enumeration", lambda d: d["os"]["macos"]["S1"]["intent"].__setitem__("commit", "click")),
            ("intent.cand_index negative", lambda d: d["os"]["macos"]["S1"]["intent"].__setitem__("cand_index", -1)),
            ("intent.nav_count negative", lambda d: d["os"]["macos"]["S1"]["intent"].__setitem__("nav_count", -1)),
            ("a boolean intent field holding an integer", lambda d: d["os"]["macos"]["S1"]["intent"].__setitem__("abandon", 0)),
            ("reading_len.min is 0", lambda d: d["os"]["macos"]["S10"].__setitem__("reading_len", {"min": 0})),
            ("reading_len has an unknown key", lambda d: d["os"]["macos"]["S10"].__setitem__("reading_len", {"min": 2, "zz": 1})),
        ]:
            try:
                check_recipes(_bad_recipes(fn))
                check("check_recipes rejects (type): %s" % name, False, "was not rejected")
            except ValueError:
                check("check_recipes rejects (type): %s" % name, True)

        # --- load_corpus: rejects a row whose phrase_id starts with kana-
        kana_corpus = os.path.join(tmp, "kana_corpus.jsonl")
        with open(kana_corpus, "wb") as f:
            f.write(('{"kind":"header","format":"corpus/1","source":"aozora","tokenizer":"table","dict":"none"}\n'
                     + dumps({"kind": "phrase", "phrase_id": "kana-1", "work_id": "kana", "text": "x",
                              "reading": "あい", "reading_len": 2}) + "\n").encode("utf-8"))
        try:
            load_corpus(kana_corpus)
            check("load_corpus rejects a phrase_id starting with kana-", False, "was not rejected")
        except ValueError:
            check("load_corpus rejects a phrase_id starting with kana-", True)

        # --- direct confirmation of fullmatch (a value with a trailing newline would pass re.match's $)
        check("the patterns reject a trailing newline (fullmatch, negative control)",
              PHRASE_ID_RE.fullmatch("w1-1-1\n") is None and VERSION_RE.fullmatch("1.0.0\n") is None
              and SHA_RE.fullmatch("0" * 64 + "\n") is None and TRIAL_ID_RE.fullmatch("t00001\n") is None)
        check("the patterns accept a value with no trailing newline (positive control)",
              PHRASE_ID_RE.fullmatch("w1-1-1") is not None and VERSION_RE.fullmatch("1.0.0") is not None)

        # --- an end-to-end confirmation: load_corpus does not reject a backslash outright, so a header
        #     tokenizer value with a trailing newline (legal JSON via a \n escape) would pass a .match
        #     whose $ matches right before the newline. fullmatch rejects it.
        bad_tok_corpus = os.path.join(tmp, "bad_tokenizer_corpus.jsonl")
        with open(bad_tok_corpus, "wb") as f:
            bad_header = dumps({"kind": "header", "format": "corpus/1", "source": "aozora",
                                 "tokenizer": "table" + chr(10), "dict": "none"})
            f.write((bad_header + "\n" + dumps({"kind": "phrase", "phrase_id": "w1-1-1", "work_id": "w1",
                                                "text": "x", "reading": "あい", "reading_len": 2}) + "\n")
                    .encode("utf-8"))
        try:
            load_corpus(bad_tok_corpus)
            check("load_corpus rejects a tokenizer value with a trailing newline (fullmatch)", False, "was not rejected")
        except ValueError:
            check("load_corpus rejects a tokenizer value with a trailing newline (fullmatch)", True)

        # --- validator: positive controls
        check("the generated plan (macos) passes --validate", validate_plan(bytes1) == [], str(validate_plan(bytes1)[:2]))
        check("the generated plan (windows) passes --validate", validate_plan(render_plan(rows_w)) == [])
        check("a CRLF plan also passes", validate_plan(bytes1.replace(b"\n", b"\r\n")) == [])

        # --- validator: negative controls (break one thing at a time, and check the specific reason)
        def mutate(kind_row, fn, block_kind=None):
            rs = json.loads(json.dumps(rows1))
            for r in rs:
                if r["kind"] == kind_row and fn(r, rs):
                    return render_plan(rs)
            raise AssertionError("could not inject the mutation")

        def first_trial(fn):
            return mutate("trial", lambda r, rs: (fn(r), True)[1])

        muts = [
            ("unknown keyname", first_trial(lambda r: r["steps"][0].__setitem__("key", "hyper")), "keyname"),
            ("at_ms goes backward", first_trial(lambda r: r["steps"][0].__setitem__("at_ms", r["steps"][-1]["at_ms"] + 1000)),
             "at_ms_order"),
            ("block and speed mismatch", first_trial(lambda r: r.__setitem__(
                "speed", {"slow": "fast", "mid": "slow", "fast": "mid"}[r["speed"]])), "speed_mismatch"),
            ("down appears twice in a row", first_trial(lambda r: r["steps"].__setitem__(1, dict(r["steps"][1], down=True))), "pair"),
            ("up is missing", first_trial(lambda r: r["steps"].pop()), "pair"),
            ("trial_id skips a number", first_trial(lambda r: r.__setitem__("trial_id", "t99999")), "trial_id"),
            ("block_id does not exist", first_trial(lambda r: r.__setitem__("block_id", 9999)), "block_ref"),
            ("scenario is S99", first_trial(lambda r: r.__setitem__("scenario", "S99")), "scenario"),
            ("intent is missing a key", first_trial(lambda r: r["intent"].pop("commit")), "intent_keys"),
            ("intent has an unknown key", first_trial(lambda r: r["intent"].__setitem__("zz", 1)), "intent_keys"),
            ("intent.commit outside the enumeration", first_trial(lambda r: r["intent"].__setitem__("commit", "click")), "intent_type"),
            ("a boolean intent field holding an integer", first_trial(lambda r: r["intent"].__setitem__("abandon", 0)), "intent_type"),
            ("trial has an unknown key", first_trial(lambda r: r.__setitem__("zz", 1)), "extra_key"),
            ("trial is missing a key", first_trial(lambda r: r.pop("think_ms")), "missing_key"),
            ("malformed phrase_id (uppercase)", first_trial(lambda r: r.__setitem__("phrase_id", "Xray1")), "phrase_id"),
            ("reading_len is 0", first_trial(lambda r: r.__setitem__("reading_len", 0)), "type"),
            ("at_ms is a boolean", first_trial(lambda r: r["steps"][0].__setitem__("at_ms", True)), "step_shape"),
            ("block marker has an unknown keyname", mutate("block", lambda r, rs: (r["marker"].append("hyper"), True)[1]), "keyname"),
            ("block speed outside the enumeration", mutate("block", lambda r, rs: (r.__setitem__("speed", "warp"), True)[1]), "speed"),
            ("header format", mutate("header", lambda r, rs: (r.__setitem__("format", "plan/2"), True)[1]), "header"),
            ("header seed is negative", mutate("header", lambda r, rs: (r.__setitem__("seed", -1), True)[1]), "header"),
            ("header config_sha256 is uppercase", mutate("header", lambda r, rs: (r.__setitem__("config_sha256", "A" * 64), True)[1]),
             "header"),
            ("header recipes_sha256 is too short", mutate("header", lambda r, rs: (r.__setitem__("recipes_sha256", "ab"), True)[1]),
             "header"),
            ("header corpus_sha256 is uppercase", mutate("header", lambda r, rs: (r.__setitem__("corpus_sha256", "B" * 64), True)[1]),
             "header"),
            ("header is missing recipes_sha256", mutate("header", lambda r, rs: (r.pop("recipes_sha256"), True)[1]),
             "missing_key"),
            ("block_id refers to an earlier block rather than the immediately preceding one",
             mutate("trial", lambda r, rs: (r.__setitem__("block_id", 0), True)[1] if r["block_id"] > 0 else False),
             "block_ref"),
        ]
        text = bytes1.decode("utf-8")
        muts += [
            ("extra whitespace in a separator", text.replace('"kind":"block"', '"kind": "block"', 1).encode("utf-8"), "format"),
            ("escaping (a line containing a backslash)",
             text.replace('"kind":"block"', '"kind":"bl' + chr(92) + 'u006fck"', 1).encode("utf-8"), "escape"),
            ("BOM", b"\xef\xbb\xbf" + bytes1, "bom"),
            ("blank line", text.replace("\n", "\n\n", 1).encode("utf-8"), "blank"),
            ("a decimal number", text.replace('"at_ms":0,', '"at_ms":0.0,', 1).encode("utf-8"), "float"),
            ("duplicate key", text.replace('"kind":"trial"', '"kind":"trial","kind":"trial"', 1).encode("utf-8"), "dup_key"),
            ("no trial present", "\n".join(text.split("\n")[:2]).encode("utf-8") + b"\n", "no_trial"),
            ("empty file", b"", "empty"),
        ]
        for name, body, want in muts:
            found = validate_plan(body)
            codes = set(c for _, c, _ in found)
            check("the validator rejects: %s" % name, want in codes, "expected %s, got %s" % (want, sorted(codes)))
        leak_found = []
        for leak_fn in (lambda r: r["steps"][0].__setitem__("key", "leakykey9"),
                        lambda r: r.__setitem__("phrase_id", "leakyvalue1"),
                        lambda r: r.__setitem__("leakyextra", 1)):
            one = validate_plan(first_trial(leak_fn))
            check("the negative-control material is rejected (a precondition for the leak check)", len(one) >= 1)
            leak_found.extend(one)
        check("the validator's description echoes neither the value nor the key name", all("leaky" not in m for _, _, m in leak_found))

        # --- agreement between the public functions and the CLI
        out1 = os.path.join(tmp, "plan1.jsonl")
        out2 = os.path.join(tmp, "plan2.jsonl")
        me = os.path.abspath(__file__)
        env = dict(os.environ, PYTHONUTF8="1")

        def run(*args):
            p = subprocess.run([sys.executable, me] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=env)
            return p.returncode, p.stdout.decode("utf-8", "replace")

        base_args = ["--corpus", corpus_path, "--recipes", recipes_path, "--config", cfg_path, "--os", "macos"]
        rc, msg = run(*(base_args + ["--seed", "7", "--out", out1]))
        check("the CLI builds a plan", rc == 0, msg[-200:])
        rc, msg = run(*(base_args + ["--seed", "7", "--out", out2]))
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            cli1, cli2 = f1.read(), f2.read()
        check("running the CLI twice yields byte-identical output (same seed)", cli1 == cli2 and len(cli1) > 100)
        check("the CLI output equals the function output (config_sha256 is the SHA-256 of the config file's bytes)", cli1 == bytes1)
        check("the CLI output passes --validate", run("--validate", out1)[0] == 0)
        by_id = dict((p["phrase_id"], p) for p in corpus)
        mism = 0
        checked = 0
        for line in cli1.decode("utf-8").split("\n"):
            if not line.startswith('{"kind":"trial"'):
                continue
            row = json.loads(line)
            if row["phrase_id"] not in by_id:
                continue
            got = build_trial(by_id[row["phrase_id"]], row["scenario"], row["speed"], "macos", recipes, cfg,
                              trial_rng(7, row["trial_id"]), row["trial_id"], row["block_id"])
            checked += 1
            if dumps(got) != line:
                mism += 1
        check("calling build_trial directly matches the corresponding CLI row", checked > 100 and mism == 0,
              "checked %d rows, %d mismatched" % (checked, mism))
        check("load_recipes / build_trial are exported", callable(load_recipes) and callable(build_trial))

        # --- CLI entry point
        bad_cfg = os.path.join(tmp, "bad_cfg.json")
        with open(bad_cfg, "wb") as f:
            f.write(json.dumps({"key_hold_ms": 60}).encode("utf-8"))
        rc, msg = run("--corpus", corpus_path, "--recipes", recipes_path, "--config", bad_cfg, "--os", "macos",
                      "--seed", "1", "--out", os.path.join(tmp, "x.jsonl"))
        check("a config missing keys exits 2", rc == 2, "exit %d" % rc)

        # --- a marker whose observed keys are all arrows must be longer than every arrow run inside a trial
        def _marker_ok(marker, nav, rec=recipes, scenarios=None):
            c = dict(cfg, marker=marker, nav_count_max=nav)
            if scenarios is not None:
                c["scenarios"] = scenarios
            try:
                plan_rows(corpus, rec, c, "macos", 7, sha, rsha, csha)
                return True
            except ValueError:
                return False
        runs3 = max(_arrow_run([s["key"] for s in t["steps"] if s["down"]])
                    for t in gen(config=dict(cfg, nav_count_max=3))[0] if t["kind"] == "trial")
        check("precondition: with nav_count_max 3 some trial presses three arrow keys in a row", runs3 == 3, str(runs3))
        check("a marker leaving no more arrows in the observation than the longest arrow run of a trial is refused",
              not _marker_ok(["left", "right", "left"], 3) and not _marker_ok(["left", "f13", "right", "left"], 3)
              and not _marker_ok(["left", "f13"], 3))
        check("a longer arrow marker, a lower nav_count_max, a marker with an observed non-arrow key, a marker the "
              "observation never records, and a plan without the arrow scenario are accepted",
              _marker_ok(["left", "right", "left", "right"], 3) and _marker_ok(["left", "right", "left"], 2)
              and _marker_ok(["left", "space", "left"], 3) and _marker_ok(["f13", "f14", "f13"], 3)
              and _marker_ok([], 3) and _marker_ok(["left", "right", "left"], 3, scenarios=["S1", "S2"]))
        rec5 = json.loads(json.dumps(recipes))
        rec5["os"]["macos"]["S7"]["params"]["nav_count"]["max"] = 5
        check("the limit follows the procedures: an S7 that allows five arrows refuses a three-arrow marker even "
              "with nav_count_max 2", not _marker_ok(["left", "right", "left"], 2, rec=rec5))
        check("normalize_config does not judge the marker (the attack reads the configs of plans already run)",
              normalize_config(dict(cfg, marker=["left", "right", "left"], nav_count_max=3))["marker"]
              == ["left", "right", "left"])
        bad_mk = os.path.join(tmp, "bad_marker.json")
        with open(bad_mk, "wb") as f:
            f.write(json.dumps(dict(cfg, marker=["left", "right", "left"], nav_count_max=3)).encode("utf-8"))
        rc, msg = run("--corpus", corpus_path, "--recipes", recipes_path, "--config", bad_mk, "--os", "macos",
                      "--seed", "1", "--out", os.path.join(tmp, "x.jsonl"))
        check("a config whose arrow-key marker is too short exits 2", rc == 2 and "nav_count_max" in msg,
              "exit %d" % rc)
        rc, msg = run("--validate", os.path.join(tmp, "no-such-file.jsonl"))
        check("--validate on a nonexistent plan exits 2", rc == 2, "exit %d" % rc)
        mut_path = os.path.join(tmp, "mut.jsonl")
        with open(mut_path, "wb") as f:
            f.write(muts[0][1])
        rc, msg = run("--validate", mut_path)
        check("--validate on an invalid plan exits 1 (with a line number)", rc == 1 and ":" in msg, "exit %d" % rc)
        rc, msg = run("--seed", "1")
        check("no mode selected exits 2", rc == 2, "exit %d" % rc)
        rc, msg = run(*(base_args + ["--seed", "-3", "--out", os.path.join(tmp, "y.jsonl")]))
        check("a negative seed exits 2", rc == 2, "exit %d" % rc)

        # --- estimate
        secs, lines = estimate(cfg, recipes, "macos")
        check("--estimate (no corpus) is positive", secs > 0 and lines[-1].endswith(":"))
        secs2, _ = estimate(cfg, recipes, "macos", corpus)
        check("--estimate (with a corpus) is positive", secs2 > 0)
        rc, msg = run("--estimate", "--config", cfg_path, "--recipes", recipes_path, "--os", "macos")
        last = msg.strip().split("\n")[-1]
        check("the last line of --estimate is a single positive number", rc == 0 and re.fullmatch(r"[0-9]+(\.[0-9]+)?", last) is not None
              and float(last) > 0, "exit %d, last line %s" % (rc, last))
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)

    if fails:
        for m in fails:
            print("FAIL " + m)
        print("selftest failed: %d / %d" % (len(fails), n[0]))
        return 1
    print("selftest OK (%d cases)" % n[0])
    return 0


# ---------------------------------------------------------------- CLI

def _read_json(path, what):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError("could not open %s (%s)" % (what, type(e).__name__))
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ValueError("%s is not readable as JSON" % what)


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="plan/1 generator, validator, and duration estimator")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--validate", metavar="PLAN")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--corpus")
    ap.add_argument("--recipes")
    ap.add_argument("--config")
    ap.add_argument("--os", dest="os_name", choices=OS_NAMES)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.validate is not None:
        try:
            with open(args.validate, "rb") as f:
                data = f.read()
        except OSError as e:
            print("%s: could not open (%s)" % (args.validate, type(e).__name__), file=sys.stderr)
            return 2
        found = validate_plan(data)
        for ln, code, msg in found[:50]:
            print("%s:%d: [%s] %s" % (args.validate, ln, code, msg), file=sys.stderr)
        if len(found) > 50:
            print("%s: ...and %d more" % (args.validate, len(found) - 50), file=sys.stderr)
        return 1 if found else 0

    generating = args.out is not None or args.seed is not None or args.corpus is not None
    if not args.estimate and not generating:
        print("generate_plan.py: specify one of --selftest / --validate PLAN / --estimate / "
              "generation (--corpus --recipes --config --os --seed --out)", file=sys.stderr)
        return 2
    try:
        if not args.recipes or not args.config or not args.os_name:
            raise ValueError("--recipes, --config, and --os are required")
        recipes = load_recipes(args.recipes)
        recipes_sha256 = _sha_file(args.recipes)
        raw_cfg, config = _read_json(args.config, "config")
        normalize_config(config)
        unknown = unknown_config_keys(config)
        if unknown:
            print("generate_plan.py: ignoring unknown config keys: %s" % ", ".join(str(k) for k in unknown),
                  file=sys.stderr)
        corpus = load_corpus(args.corpus) if args.corpus else None
        corpus_sha256 = _sha_file(args.corpus) if args.corpus else None
        if args.estimate:
            secs, lines = estimate(config, recipes, args.os_name, corpus)
            for line in lines:
                print(line)
            print("%.1f" % secs)
            sys.stdout.flush()
            return 0
        if corpus is None or args.seed is None or not args.out:
            raise ValueError("generation requires --corpus, --seed, and --out")
        if not 0 <= args.seed <= SEED_MAX:
            raise ValueError("--seed must be an integer in 0..2^63-1")
        gen_warnings = []
        rows, _ = plan_rows(corpus, recipes, config, args.os_name, args.seed, hashlib.sha256(raw_cfg).hexdigest(),
                             recipes_sha256, corpus_sha256, warnings=gen_warnings)
        for w in gen_warnings:
            print("generate_plan.py: %s" % w, file=sys.stderr)
    except (ValueError, OSError) as e:
        print("generate_plan.py: %s" % e, file=sys.stderr)
        return 2
    body = render_plan(rows)
    problems = validate_plan(body)
    if problems:  # the generator's own output failing its own validation is an implementation defect
        for ln, code, msg in problems[:10]:
            print("generate_plan.py: the generated plan fails validation at line %d [%s] %s" % (ln, code, msg), file=sys.stderr)
        return 2
    with open(args.out, "wb") as f:
        f.write(body)
    n_trial = sum(1 for r in rows if r["kind"] == "trial")
    n_block = sum(1 for r in rows if r["kind"] == "block")
    print("wrote plan: %d trial(s), %d block(s) (os=%s, seed=%d)" % (n_trial, n_block, args.os_name, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
