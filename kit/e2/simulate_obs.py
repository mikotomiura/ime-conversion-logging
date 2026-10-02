#!/usr/bin/env python3
# kit: selftest
"""Predicted observation logs for the closed-set attack (Python standard library only).

  simulate_obs.py --selftest      key-table checks, feature transforms and known trials (fast, well under a second)

  simulate(plan_trial, keycat, timing="exact", runs="exact", t0=0) -> [(t, category)]
  apply_features(events, timing, runs)       the same transform is applied to real and to predicted observations

The attacker sees only the publishable form of a log: the category and the time of each key row. A predicted
log is built from the key steps of a planned trial (presses only) by mapping each key name to its category
through the table KEYCAT. A key whose entry is None produces no row in the logger output, so it is absent from
the prediction as well.

Feature transforms used by the removal experiments:
  timing "exact"  times are kept as they are
         "none"   times are removed (all set to 0)
         "bin1s"  times are coarsened to 1-second bins (each time becomes the start of its bin, optionally shifted
                  by phase; see below)
  runs   "exact"  runs of alphabetic keys are kept (one row per key press)
         "count"  the length of a run of alphabetic keys is removed: every maximal run of consecutive alphabetic
                  rows (a run ends at the next row of a different category, for example space or punctuation) is
                  stamped with the time of its first row, so that the run collapses to a single token further down
                  the pipeline. A run is not split at a 1-second boundary that happens to fall inside it: the
                  boundary is an artefact of the time grid, not a break in typing. Rows of other categories keep
                  their order and times.

phase (bin1s only) shifts the 1-second grid used to compute a bin, from the default of 0 up to just under one
window. Two logs of the same events can disagree on which second a time near a grid line falls into, because the
grid of an observed log is anchored to the (unknown) time its first row was written at, not to any candidate's
simulated log. The closed-set attack searches candidate phases and keeps the closest match; see PHASES in
closed_set.py.
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_KIT = os.path.dirname(_HERE)
for _sub in ("gen",):
    _p = os.path.join(_KIT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import generate_plan  # noqa: E402  (public functions of the scenario generator: key names, build_trial, load_recipes)

WINDOW_MS = 1000
TIMINGS = ("exact", "none", "bin1s")
RUNS = ("exact", "count")

# Key name -> category, copied from the key-category mapping of the logger (kit/logger/src/key_category.rs, from_vk).
# None means that the logger writes no row for the key. The selftest checks the table against the VK values in
# VK_OF (through the parsed mapping of the logger source) so that a change in the logger mapping turns it red.
KEYCAT = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEYCAT[_c] = "alphabetic"
for _d in "0123456789":
    KEYCAT[_d] = "numeric"
KEYCAT.update({
    "space": "space", "enter": "enter", "esc": "escape", "backspace": "backspace", "tab": "tab",
    "left": "navigation", "right": "navigation", "up": "navigation", "down": "navigation",
    "minus": "punctuation",             # OEM minus key (the long-vowel mark in romaji input)
    "comma": "sentence_punctuation",    # OEM comma key
    "period": "sentence_punctuation",   # OEM period key
    "shift": "modifier",                # left shift; no planned trial uses it
    # Outside the observed key set of the logger: no row is written.
    "ime_on": None, "ime_off": None,    # IME toggles
    "f13": None, "f14": None, "f15": None, "f16": None, "f17": None, "f18": None, "f19": None,
    "mouse_click": None, "focus_away": None, "focus_back": None,   # pseudo keys of the runner
})

# Windows virtual-key codes of the real keys; evidence for KEYCAT that the selftest checks.
VK_OF = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    VK_OF[_c] = 0x41 + _i
for _i, _d in enumerate("0123456789"):
    VK_OF[_d] = 0x30 + _i
VK_OF.update({"space": 0x20, "enter": 0x0D, "esc": 0x1B, "backspace": 0x08, "tab": 0x09,
              "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
              "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "shift": 0xA0})


def apply_features(events, timing="exact", runs="exact", phase=0):
    """Apply a feature transform to [(t, category)] (t in ms, ascending) and return [(t, category)].

    phase (0 <= phase < WINDOW_MS) shifts the grid used by timing="bin1s"; it has no effect for the other two
    timing values. See the module docstring.
    """
    if timing not in TIMINGS:
        raise ValueError("timing must be exact, none or bin1s")
    if runs not in RUNS:
        raise ValueError("runs must be exact or count")
    ev = []
    for t, c in events:
        if timing == "none":
            t = 0
        elif timing == "bin1s":
            t = ((t + phase) // WINDOW_MS) * WINDOW_MS - phase
        ev.append((t, c))
    if runs == "exact":
        return ev
    out = []
    run_t = None   # the (already timing-transformed) time stamped on the run of alphabetic rows in progress
    for t, c in ev:
        if c == "alphabetic":
            if run_t is None:
                run_t = t
            out.append((run_t, c))
        else:
            run_t = None
            out.append((t, c))
    return out


def simulate(plan_trial, keycat, timing="exact", runs="exact", t0=0, phase=0):
    """Return the predicted [(t, category)] of one planned trial (a trial row of a plan).

    t is t0 plus the step time, where t0 is the start time of the trial (default 0). Only key presses are mapped, and
    keys whose category is None are omitted. phase is passed to apply_features (see its docstring).
    """
    ev = []
    for st in plan_trial["steps"]:
        if not st["down"]:
            continue
        key = st["key"]
        if key not in keycat:
            raise ValueError("key name missing from the table: %s" % key)
        cat = keycat[key]
        if cat is None:
            continue
        ev.append((t0 + st["at_ms"], cat))
    return apply_features(ev, timing, runs, phase)


# ---------------------------------------------------------------- selftest
# The selftest reads the from_vk function of kit/logger/src/key_category.rs and rebuilds the VK -> category mapping
# from its match arms, to detect drift between KEYCAT and the logger.

def _camel_to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def rust_from_vk_table(path):
    """Read the match arms of from_vk and return [(set of VK codes, category)] in arm order."""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    i = text.index("pub fn from_vk")
    body = text[i:]
    j = body.index("\n    }\n")
    body = "\n".join(re.sub(r"//.*$", "", ln) for ln in body[:j].split("\n"))
    arms = []
    for m in re.finditer(r"([0-9A-Fa-fx_.=|\s]+?)=>\s*Self::(\w+)\s*,", body):
        pats, name = m.group(1), m.group(2)
        vks = set()
        for piece in pats.split("|"):
            piece = piece.strip()
            if not piece or piece == "_":
                continue
            if "..=" in piece:
                lo, hi = piece.split("..=")
                vks.update(range(int(lo, 16), int(hi, 16) + 1))
            else:
                vks.add(int(piece, 16))
        arms.append((vks, _camel_to_snake(name)))
    return arms


def rust_from_vk(arms, vk):
    for vks, cat in arms:
        if vk in vks:
            return cat
    return "other"


def selftest():
    fails = []
    n = [0]

    def check(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append(name + (": " + detail if detail else ""))

    check("KEYCAT holds every key name of the generator, and nothing else",
          set(KEYCAT) == set(generate_plan.KEYNAMES), "difference: %s" % sorted(set(KEYCAT) ^ set(generate_plan.KEYNAMES)))
    check("VK_OF covers exactly the keys whose category is not None",
          set(VK_OF) == set(k for k, v in KEYCAT.items() if v is not None))
    rs = os.path.join(_KIT, "logger", "src", "key_category.rs")
    arms = rust_from_vk_table(rs)
    check("the match arms of from_vk in the logger source are read (14 categories)",
          len(set(c for _, c in arms)) == 14, "%d categories" % len(set(c for _, c in arms)))
    bad = [k for k, vk in sorted(VK_OF.items()) if rust_from_vk(arms, vk) != KEYCAT[k]]
    check("KEYCAT agrees with the logger mapping for every key in VK_OF", not bad, "differences: %s" % bad)
    check("negative control: the logger mapping does not send minus to alphabetic",
          rust_from_vk(arms, VK_OF["minus"]) != "alphabetic")
    check("IME toggles and F13 to F19 are unobserved (None)",
          all(KEYCAT[k] is None for k in ["ime_on", "ime_off"] + ["f%d" % i for i in range(13, 20)]))
    check("the VK codes of IME on, IME off and F13 fall to other in the logger mapping",
          all(rust_from_vk(arms, vk) == "other" for vk in (0x16, 0x1A, 0x7C)))

    recipes = generate_plan.load_recipes(os.path.join(_KIT, "gen", "recipes.json"))
    cfg = {"speeds": {"slow": {"iki_median_ms": 300, "iki_sigma": 0.3}, "mid": {"iki_median_ms": 200, "iki_sigma": 0.3},
                      "fast": {"iki_median_ms": 120, "iki_sigma": 0.3}},
           "key_hold_ms": 60, "think_ms": {"min": 200, "max": 2000}, "cand_index_max": 4, "nav_count_max": 3,
           "inter_trial_ms": 1000, "marker": ["f13"], "marker_gap_ms": 150, "reset_ime_every_block": True}
    phrase = {"phrase_id": "w1-1-1", "work_id": "w1", "text": "x", "reading": "がっこう", "reading_len": 4}

    def trial(scenario, seed=1, speed="mid", os_name="macos"):
        return generate_plan.build_trial(phrase, scenario, speed, os_name, recipes, cfg,
                                         generate_plan.trial_rng(seed, "t00001"), "t00001", 0)

    t1 = trial("S1")
    ev = simulate(t1, KEYCAT)
    check("S1 categories are six alphabetic keys (g a k k o u), space and two enters (candidate pick, commit)",
          [c for _, c in ev] == ["alphabetic"] * 6 + ["space", "enter", "enter"], repr([c for _, c in ev]))
    downs = [s["at_ms"] for s in t1["steps"] if s["down"]]
    check("times are the step times of the presses (exact)", [t for t, _ in ev] == downs)
    check("t0 shifts every time", [t for t, _ in simulate(t1, KEYCAT, t0=5000)] == [d + 5000 for d in downs])
    check("timing none sets every time to 0 and keeps the category sequence",
          [t for t, _ in simulate(t1, KEYCAT, "none")] == [0] * len(ev)
          and [c for _, c in simulate(t1, KEYCAT, "none")] == [c for _, c in ev])
    b = simulate(t1, KEYCAT, "bin1s", t0=1234)
    check("timing bin1s gives multiples of 1000 equal to the floor of the original times",
          all(t % 1000 == 0 for t, _ in b) and [t for t, _ in b] == [((d + 1234) // 1000) * 1000 for d in downs])
    check("only presses are mapped (releases produce no row)", len(ev) == len(downs))

    s14 = trial("S14")
    check("S14: IME off and on are absent from the prediction",
          [c for _, c in simulate(s14, KEYCAT)] == ["alphabetic"] * 6 + ["space"])
    s15 = trial("S15")
    check("S15: focus switches are absent and enter is present",
          [c for _, c in simulate(s15, KEYCAT)] == ["alphabetic"] * 6 + ["space", "enter", "enter"])
    s6 = trial("S6")
    check("S6: one backspace per reading character", [c for _, c in simulate(s6, KEYCAT)][-4:] == ["backspace"] * 4)
    s8 = trial("S8")
    check("S8: the candidate digit key is numeric and the commit that follows it is enter",
          [c for _, c in simulate(s8, KEYCAT)][-2:] == ["numeric", "enter"])

    # runs="count" groups by delimiter (a row of a different category), not by a 1-second window:
    # a run of alphabetic rows that spans a window boundary is kept as one token.
    ev2 = [(0, "alphabetic"), (100, "alphabetic"), (900, "alphabetic"), (1100, "alphabetic"), (1500, "space"),
           (1700, "alphabetic"), (2500, "enter")]
    cnt = apply_features(ev2, "exact", "count")
    check("positive: a run that spans a 1-second boundary (times 0, 100, 900, 1100) is one token, "
          "stamped with the time of its first row",
          cnt == [(0, "alphabetic")] * 4 + [(1500, "space"), (1700, "alphabetic"), (2500, "enter")], repr(cnt))
    check("runs count keeps the number of alphabetic keys",
          sum(1 for _, c in cnt if c == "alphabetic") == sum(1 for _, c in ev2 if c == "alphabetic"))
    cn = apply_features(ev2, "none", "count")
    check("timing none with runs count: every alphabetic key gets time 0, and a delimiter still starts a new run "
          "(the run after the space is not merged with the run before it)",
          cn == [(0, "alphabetic")] * 4 + [(0, "space"), (0, "alphabetic"), (0, "enter")], repr(cn))
    cb = apply_features(ev2, "bin1s", "count")
    check("timing bin1s with runs count: the run across the boundary stays one token (time of its first row, "
          "bin 0), and the row after the space starts a new token at its own bin",
          cb == [(0, "alphabetic")] * 4 + [(1000, "space"), (1000, "alphabetic"), (2000, "enter")], repr(cb))

    ev3 = [(0, "alphabetic"), (100, "space"), (200, "alphabetic")]
    check("negative: two runs separated by a delimiter inside the same nominal window are not merged into one",
          apply_features(ev3, "exact", "count") == ev3)

    # positive: phase moves which bin a time near a boundary falls into (used by the attack to
    # search the candidate log's grid alignment against the observed log)
    near = [(950, "alphabetic"), (1050, "alphabetic")]
    check("positive: with phase=0 a pair of times either side of a bin edge fall in different bins",
          [t for t, _ in apply_features(near, "bin1s")] == [0, 1000])
    check("positive: shifting the grid by phase=100 puts both times in the same bin",
          [t for t, _ in apply_features(near, "bin1s", phase=100)] == [900, 900])
    check("negative: phase has no effect when timing is not bin1s",
          apply_features(near, "exact", phase=100) == near and apply_features(near, "none", phase=100) == [(0, "alphabetic")] * 2)

    for label, fn in (("unknown timing", lambda: apply_features(ev2, "fine", "exact")),
                      ("unknown runs", lambda: apply_features(ev2, "exact", "none")),
                      ("key name missing from the table", lambda: simulate(t1, {"a": "alphabetic"}))):
        n[0] += 1
        try:
            fn()
            fails.append("%s: no exception was raised" % label)
        except ValueError:
            pass

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
    ap = argparse.ArgumentParser(description="Predicted observation logs for the closed-set attack "
                                             "(a library; only --selftest runs standalone)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
