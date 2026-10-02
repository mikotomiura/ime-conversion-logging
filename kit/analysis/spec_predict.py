#!/usr/bin/env python3
# kit: selftest
"""The observer's specified behaviour applied to what was sent: the session rows the macOS observer
should write if it follows its own rules exactly, computed from the executor's record (the oracle)
and the plan. The observation is not read. Uses only the Python standard library.

  spec_predict.py --oracle ORACLE --plan PLAN [--keymap KEYMAP.json] --out OUT.json
  spec_predict.py --from-plan --plan PLAN [--block-gap-ms N] [--text-size N] [--keymap KEYMAP.json] --out OUT.json
  spec_predict.py --selftest

The output (format spec_predict/2) holds every predicted session (its start and end on the oracle's
wall clock, nav, abandon, how it ended: row, truncated or open, the trials in which it started and
ended, and the trials whose presses entered it), the counts, and per trial: whether a press of the
trial entered a session that is cut off (long; the session may have started in the trial or have been
carried over from an earlier one), whether a session it started ends after the trial (carried_out),
whether a press of the trial entered a session started by an earlier trial (received_carry), how many
sessions it started (n_started), and whether the trial sends the minus, comma or period key
(closing_key_in_reading; the observer's rules close a session at these keys, which type a long-vowel
mark, a comma or a full stop in Romaji input and ho, ne or ru in kana input, so a reading that contains
one is split). The same flags are counted per scenario and speed (by_stratum). Two adapters put the
prediction into the shapes the matcher reads: as_obs_bytes (the predicted rows as an observation log,
obs/1) and as_sessions_bytes (the predicted rows as reference sessions, in the form of gt_sessions.py's
output, with the exclusions of the ground truth).

--from-plan predicts from an oracle built from the plan's own times (oracle_from_plan: every key sent
exactly on schedule, block_gap_ms between blocks). It is how the values that the pre-registration
discloses for the measurement plans were computed before any measurement; the analysis predicts from
the oracle of the measurement. With --from-plan the output also has "plan_figures" (plan_figures), the
design figures that the pre-registration quotes from the plan (sampling plan, design effect; the texts):
per layer (scenario, speed), for the planned trials and for the trials of the primary scope (those whose
presses do not enter a session that is cut off), the number of trials, the number of blocks that hold one
and the sum of m_j squared over the sum of m_j (m_j: the layer's trials in block j); the range of that
quantity over the layers (with the layer of its largest value); the number of blocks per speed; and, per
speed, the number of texts, the mean and the histogram of the number of primary-scope trials in a text. A
text is --text-size (default 8) consecutive trials of a block in plan order, a shorter remainder is not
used, as in l2.py. The output format (spec_predict/2) is unchanged: readers ignore the added key.

Why this exists. On macOS the observer infers each conversion session from key presses, so some
disagreements with the ground truth follow from its rules alone and can be predicted before any
observation is read (for example, Backspace keeps a session open, and Tab or a punctuation key
closes it). The prediction is the reference of a two-stage comparison:
  - ground truth against prediction: how well the observer's rules describe what the input method
    did (the validity of the inference rules);
  - prediction against observation: whether the observer carries out its rules faithfully (missed
    presses, timing, implementation).
It also marks, for every trial, whether its presses went into a session that reaches the 20-second
cut-off, so that the primary agreement estimates can be computed over the trials whose sessions the
observer is specified to report.

The rules mirror the observer's source (the selftest compares them with it, so a change on either
side turns the selftest red):
  - kit/logger/src/driver_macos/keymap.rs (macos_vk_to_vk): the macOS key code becomes a virtual-key
    code; a code with no explicit entry passes through unchanged;
  - kit/logger/src/driver_macos.rs (handle_key): an input-mode key first updates the open flag; only
    presses drive the session machine;
  - kit/logger/src/input/ime_mode.rs: Eisu sets the flag to closed, Kana to open;
  - kit/logger/src/input/ime_compose.rs (step / apply / finish):

    | virtual key                  | effect (while Japanese input is on)                              |
    |------------------------------|------------------------------------------------------------------|
    | 0x30-0x39, 0x41-0x5A         | candidate window off; opens a session if none is open            |
    | 0x20 (Space)                 | candidate window on, if a session is open                        |
    | 0x0D (Return)                | closes the session, committed                                    |
    | 0x1B (Escape)                | closes the session, abandoned                                    |
    | 0x08 (Backspace)             | candidate window off; the session stays open                     |
    | 0x25-0x28 (arrows)           | nav + 1, if a session is open                                    |
    | >= 0xF0, or 0x19 (mode keys) | nothing                                                          |
    | anything else                | closes the session, committed                                    |

    While Japanese input is off, every press closes an open session as committed. Before any of
    this, a press that comes 20,000 ms or more after the session opened cuts the session off without
    a row, and the press is then applied to an empty state (so a letter or digit opens a new
    session).

What the oracle does not record, and how it is modelled:
  - The runner switches the input method to Japanese before every block (its focus command presses
    the Kana key). This is modelled as a Kana press at the block's block_begin time.
  - A marker row is a press of the block's marker key (the plan's "marker" list, by "seq").
  - Pseudo-keys that send no key event (focus_away, focus_back) are skipped.
  - Modifier keys are not delivered to the observer's event tap and are not modelled: a plan that
    sends one as a key (for example "shift") is refused.
Times are the oracle's wall-clock times, taken immediately before each dispatch. A predicted row's
interval is [start, end] on that clock; the observer's own rows carry its clock and the time the row
was collected, which the comparison with the observation has to allow for.

Exit: 0 = written / 1 = selftest failure / 2 = usage error, unreadable or invalid input
"""
import argparse
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import gt_sessions  # noqa: E402  oracle parser
import match  # noqa: E402  trial attribution (the same rule as for the ground truth and the observation)

FORMAT = "spec_predict/2"
LIMIT_MS = 20000
BLOCK_GAP_MS = 2000
TEXT_SIZE_TRIALS = 8  # the pre-registered text_size_trials (l2.py reads the same default)
DEFAULT_KEYMAP = os.path.join(_HERE, "..", "emit", "keymap_macos.json")
PSEUDO_KEYS = ("focus_away", "focus_back")
MODIFIER_KEYS = ("shift",)
# The minus, comma and period keys: a long-vowel mark, a comma and a full stop in Romaji input, and ho,
# ne and ru in kana input (the kana layout of kit/gen/recipes.json). They are the only keys of a typed
# reading that the observer's rules treat as closing a session (the selftest checks their class).
READING_CLOSERS = ("minus", "comma", "period")

# macos_vk_to_vk's explicit entries (macOS key code -> virtual-key code). Any other code passes
# through unchanged. The selftest compares this table with kit/logger/src/driver_macos/keymap.rs.
MAC_TO_VK = {
    0x00: 0x41, 0x01: 0x53, 0x02: 0x44, 0x03: 0x46, 0x04: 0x48, 0x05: 0x47, 0x06: 0x5A, 0x07: 0x58,
    0x08: 0x43, 0x09: 0x56, 0x0B: 0x42, 0x0C: 0x51, 0x0D: 0x57, 0x0E: 0x45, 0x0F: 0x52, 0x10: 0x59,
    0x11: 0x54, 0x12: 0x31, 0x13: 0x32, 0x14: 0x33, 0x15: 0x34, 0x16: 0x36, 0x17: 0x35, 0x18: 0xBB,
    0x19: 0x39, 0x1A: 0x37, 0x1B: 0xBD, 0x1C: 0x38, 0x1D: 0x30, 0x1E: 0xDD, 0x1F: 0x4F, 0x20: 0x55,
    0x21: 0xDB, 0x22: 0x49, 0x23: 0x50, 0x24: 0x0D, 0x25: 0x4C, 0x26: 0x4A, 0x27: 0xDE, 0x28: 0x4B,
    0x29: 0xBA, 0x2A: 0xDC, 0x2B: 0xBC, 0x2C: 0xBF, 0x2D: 0x4E, 0x2E: 0x4D, 0x2F: 0xBE, 0x30: 0x09,
    0x31: 0x20, 0x32: 0xC0, 0x33: 0x08, 0x35: 0x1B, 0x60: 0x74, 0x61: 0x75, 0x62: 0x76, 0x63: 0x72,
    0x64: 0x77, 0x65: 0x78, 0x66: 0xF0, 0x67: 0x7A, 0x68: 0xF2, 0x6D: 0x79, 0x6F: 0x7B, 0x75: 0x2E,
    0x76: 0x73, 0x78: 0x71, 0x7A: 0x70, 0x7B: 0x25, 0x7C: 0x27, 0x7D: 0x28, 0x7E: 0x26, 0x90: 0xF2,
    0x91: 0xF1,
}
MAC_KANA = 0x68
VK_KANJI = 0x19
VK_DBE_ALPHANUMERIC, VK_DBE_KATAKANA, VK_DBE_HIRAGANA, VK_DBE_SBCSCHAR, VK_DBE_DBCSCHAR = 0xF0, 0xF1, 0xF2, 0xF3, 0xF4

ALNUM, SPACE, ENTER, ESC, BACK, ARROW, MODE, OTHER = "alnum", "space", "enter", "esc", "back", "arrow", "mode", "other"


class InputError(ValueError):
    """An input the prediction cannot use (exit 2)."""


def mac_to_vk(code):
    """macos_vk_to_vk: an explicit entry, otherwise the code itself."""
    return MAC_TO_VK.get(code, code)


def vk_class(vk):
    """The branch of the session machine (apply) that a virtual-key code takes."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return ALNUM
    if vk == 0x20:
        return SPACE
    if vk == 0x0D:
        return ENTER
    if vk == 0x1B:
        return ESC
    if vk == 0x08:
        return BACK
    if 0x25 <= vk <= 0x28:
        return ARROW
    if vk >= 0xF0 or vk == VK_KANJI:
        return MODE
    return OTHER


def mode_after(vk, press, is_open):
    """The open flag after a key transition (ime_open_after_press / ime_open_after_release)."""
    if press:
        if vk in (VK_DBE_ALPHANUMERIC, VK_DBE_SBCSCHAR):
            return False
        if vk in (VK_DBE_KATAKANA, VK_DBE_HIRAGANA, VK_DBE_DBCSCHAR):
            return True
        if vk == VK_KANJI:
            return not is_open
        return is_open
    if vk in (VK_DBE_KATAKANA, VK_DBE_HIRAGANA, VK_DBE_DBCSCHAR):
        return True
    if vk in (VK_DBE_ALPHANUMERIC, VK_DBE_SBCSCHAR):
        return False
    return is_open


class _Machine(object):
    """The session machine and the open flag, fed one key transition at a time."""

    def __init__(self, limit_ms=LIMIT_MS):
        self.limit_ms = limit_ms
        self.is_open = False
        self.start = None
        self.start_trial = None
        self.active = False
        self.nav = 0
        self.trials = set()
        self.sessions = []

    def _close(self, t, trial, end, abandon=False):
        if self.start is None:
            return
        self.sessions.append({"start_wall_ms": self.start, "end_wall_ms": t, "dwell_ms": t - self.start,
                              "nav": self.nav, "abandon": abandon, "end": end,
                              "start_trial": self.start_trial, "end_trial": trial,
                              "trials": sorted(self.trials, key=str)})
        self.start, self.start_trial, self.active, self.nav, self.trials = None, None, False, 0, set()

    def _apply(self, vk, t, trial):
        # Every press applied while a session is open enters it (the press that closes it included);
        # the press that cuts a session off does not, because the cut-off comes before the transition.
        if self.start is not None and trial is not None:
            self.trials.add(trial)
        if not self.is_open:
            self._close(t, trial, "row")
            return
        c = vk_class(vk)
        if c == ALNUM:
            self.active = False
            if self.start is None:
                self.start, self.start_trial, self.nav = t, trial, 0
                if trial is not None:
                    self.trials.add(trial)
        elif c == SPACE:
            if self.start is not None and not self.active:
                self.active = True
        elif c == ENTER:
            self._close(t, trial, "row")
        elif c == ESC:
            self._close(t, trial, "row", abandon=True)
        elif c == BACK:
            self.active = False
        elif c == ARROW:
            if self.start is not None:
                self.nav += 1
        elif c == MODE:
            pass
        else:
            self._close(t, trial, "row")

    def key(self, vk, press, t, trial):
        """handle_key: the open flag first, then (presses only) the cut-off and the transition."""
        self.is_open = mode_after(vk, press, self.is_open)
        if not press:
            return
        if self.start is not None and t - self.start >= self.limit_ms:
            self._close(t, trial, "truncated")
        self._apply(vk, t, trial)

    def finish(self):
        """A session still open when the record ends."""
        if self.start is not None:
            self.sessions.append({"start_wall_ms": self.start, "end_wall_ms": None, "dwell_ms": None,
                                  "nav": self.nav, "abandon": False, "end": "open",
                                  "start_trial": self.start_trial, "end_trial": None,
                                  "trials": sorted(self.trials, key=str)})


def load_keymap(path):
    """The emitter's key map (key name -> macOS key code)."""
    try:
        with open(path, "rb") as f:
            km = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise InputError("keymap: cannot read %s (%s)" % (os.path.basename(str(path)), type(e).__name__))
    if not isinstance(km, dict):
        raise InputError("keymap: not an object")
    out = {}
    for k, v in km.items():
        if k.startswith("_"):
            continue
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise InputError("keymap: %r is not a key code" % k)
        out[k] = v
    return out


def _plan_rows(data):
    if not isinstance(data, bytes):
        raise InputError("plan: not bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("plan: not UTF-8")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise InputError("plan: empty file")
    try:
        rows = [json.loads(ln) for ln in lines]
    except ValueError as e:
        raise InputError("plan: a line is not JSON (%s)" % e)
    hdr = rows[0]
    if not isinstance(hdr, dict) or hdr.get("kind") != "header" or hdr.get("format") != "plan/1":
        raise InputError("plan: line 1 is not a plan/1 header")
    return rows


def parse_plan(data):
    """The plan's header (os), each block's marker keys and each trial's stratum: {"os", "sha256",
    "markers": {block_id: [name]}, "strata": {trial_id: (scenario, speed)}}."""
    rows = _plan_rows(data)
    markers, strata = {}, {}
    for r in rows[1:]:
        if isinstance(r, dict) and r.get("kind") == "block":
            m = r.get("marker")
            if not isinstance(m, list) or not all(isinstance(x, str) for x in m):
                raise InputError("plan: block %r has no marker list" % r.get("block_id"))
            markers[r.get("block_id")] = m
        elif isinstance(r, dict) and r.get("kind") == "trial":
            strata[r.get("trial_id")] = (r.get("scenario"), r.get("speed"))
    return {"os": rows[0].get("os"), "sha256": hashlib.sha256(data).hexdigest(), "markers": markers,
            "strata": strata}


def oracle_from_plan(plan_bytes, block_gap_ms=BLOCK_GAP_MS):
    """An oracle (oracle/1) built from the plan's own times, as the executor would write it if every key
    were sent exactly on schedule: a block begins, its marker keys follow marker_gap_ms apart, each trial
    begins gap_before_ms after the previous press and sends its steps at at_ms, the block ends at its last
    press, and the next block begins block_gap_ms later (the time the runner takes to bring the reference
    input field to the front and press the Kana key). Wall-clock times start at 0. It is used to compute,
    before any measurement, what the prediction implies for the measurement plans (the values the
    pre-registration discloses); the analysis predicts from the oracle of the measurement instead."""
    if not _is_ms(block_gap_ms):
        raise InputError("block_gap_ms must be a non-negative integer")
    rows = _plan_rows(plan_bytes)
    out = [{"kind": "header", "format": "oracle/1", "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "os": rows[0].get("os"), "emitter_version": "spec_predict.oracle_from_plan", "backend": "none"}]

    def row(t, **kw):
        out.append(dict(kw, t_wall_ms=t, t_mono_ns=t * 1000000))

    blocks, order = {}, []
    for r in rows[1:]:
        if not isinstance(r, dict):
            raise InputError("plan: a row is not an object")
        if r.get("kind") == "block":
            blocks[r.get("block_id")] = (r, [])
            order.append(r.get("block_id"))
        elif r.get("kind") == "trial":
            if r.get("block_id") not in blocks:
                raise InputError("plan: trial %r comes before its block" % r.get("trial_id"))
            blocks[r.get("block_id")][1].append(r)
    t = 0
    for n, bid in enumerate(order):
        block, trials = blocks[bid]
        gap = block.get("marker_gap_ms", 0)
        if not _is_ms(gap):
            raise InputError("plan: block %r has no valid marker_gap_ms" % bid)
        if n:
            t += block_gap_ms
        row(t, kind="block_begin", block_id=bid)
        for seq in range(len(block.get("marker") or [])):
            if seq:
                t += gap
            row(t, kind="marker", block_id=bid, seq=seq)
        for tr in trials:
            before = tr.get("gap_before_ms", 0)
            steps = tr.get("steps")
            if not _is_ms(before) or not isinstance(steps, list):
                raise InputError("plan: trial %r has no valid gap_before_ms or steps" % tr.get("trial_id"))
            t += before
            begin = t
            row(begin, kind="trial_begin", trial_id=tr["trial_id"])
            for i, st in enumerate(steps):
                if not isinstance(st, dict) or not _is_ms(st.get("at_ms")) or st["at_ms"] + begin < t:
                    raise InputError("plan: trial %r step %d has no valid at_ms" % (tr["trial_id"], i))
                t = begin + st["at_ms"]
                row(t, kind="sent", trial_id=tr["trial_id"], step=i, key=st.get("key"), down=st.get("down"))
            row(t, kind="trial_end", trial_id=tr["trial_id"])
        row(t, kind="block_end", block_id=bid)
    return _lines(out)


def _is_ms(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _code(name, keymap, where):
    if name in MODIFIER_KEYS:
        raise InputError("%s: modifier key %r sent as a key is not modelled" % (where, name))
    if name not in keymap:
        raise InputError("%s: key %r is not in the emitter's key map" % (where, name))
    return keymap[name]


def predict_rows(oracle, plan, keymap, limit_ms=LIMIT_MS):
    """Feeds the oracle's key transitions to the machine. Returns the list of predicted sessions in
    time order. oracle is gt_sessions.parse_oracle's output; plan is parse_plan's output."""
    m = _Machine(limit_ms)
    for n, row in enumerate(oracle["rows"]):
        kind, t = row["kind"], row["t_wall_ms"]
        where = "oracle row %d" % (n + 1)
        if kind == "block_begin":
            m.key(MAC_TO_VK[MAC_KANA], True, t, None)
            m.key(MAC_TO_VK[MAC_KANA], False, t, None)
        elif kind == "marker":
            names = plan["markers"].get(row.get("block_id"))
            seq = row.get("seq")
            if names is None or not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < len(names):
                raise InputError("%s: marker seq %r has no key in the plan's block %r" % (where, seq, row.get("block_id")))
            vk = mac_to_vk(_code(names[seq], keymap, where))
            m.key(vk, True, t, None)
            m.key(vk, False, t, None)
        elif kind == "sent":
            name = row.get("key")
            if not isinstance(name, str) or not isinstance(row.get("down"), bool):
                raise InputError("%s: a sent row needs key and down" % where)
            if name in PSEUDO_KEYS:
                continue
            m.key(mac_to_vk(_code(name, keymap, where)), row["down"], t, row.get("trial_id"))
    m.finish()
    return m.sessions


def closer_trials(oracle):
    """The trials that send a press of one of READING_CLOSERS."""
    return set(r.get("trial_id") for r in oracle["rows"]
               if r["kind"] == "sent" and r.get("down") is True and r.get("key") in READING_CLOSERS)


def summarise_trials(sessions, trial_ids, closers=()):
    """Per trial: the predicted sessions it started; whether a press of the trial entered a session that
    is cut off (long: the session may have started in this trial or in an earlier one); whether a session
    it started ends after the trial (carried_out); whether a press of the trial entered a session started
    by an earlier trial (received_carry); how many sessions it started (n_started, split when > 1); and
    whether it is in closers (closing_key_in_reading)."""
    closers = set(closers)
    out = dict((tid, {"sessions": [], "long": False, "carried_out": False, "received_carry": False,
                      "closing_key_in_reading": tid in closers}) for tid in trial_ids)
    for i, s in enumerate(sessions):
        for tid in s["trials"]:
            if tid not in out:
                continue
            if s["end"] == "truncated":
                out[tid]["long"] = True
            if tid != s["start_trial"]:
                out[tid]["received_carry"] = True
        tid = s["start_trial"]
        if tid not in out:
            continue
        d = out[tid]
        d["sessions"].append(i)
        if s["end_trial"] != tid:
            d["carried_out"] = True
    for d in out.values():
        d["n_started"] = len(d["sessions"])
    return out


def by_stratum(trials, strata):
    """The per-trial flags counted per (scenario, speed): the number of trials and of those long,
    carried_out, received_carry and closing_key_in_reading, and the number not long (the trials of the
    scope under_limit). Strata are sorted by scenario number and speed."""
    order = {"slow": 0, "mid": 1, "fast": 2}
    acc = {}
    for tid, d in trials.items():
        sc, sp = strata.get(tid, (None, None))
        a = acc.setdefault((sc, sp), {"scenario": sc, "speed": sp, "n": 0, "long": 0, "under_limit": 0,
                                      "carried_out": 0, "received_carry": 0, "closing_key_in_reading": 0})
        a["n"] += 1
        a["long" if d["long"] else "under_limit"] += 1
        for k in ("carried_out", "received_carry", "closing_key_in_reading"):
            a[k] += 1 if d[k] else 0

    def key(k):
        sc, sp = k
        num = int(sc[1:]) if isinstance(sc, str) and sc[1:].isdigit() else 0
        return (num, str(sc), order.get(sp, 9), str(sp))
    return [acc[k] for k in sorted(acc, key=key)]


def as_obs_rows(sessions):
    """The predicted rows in the observation's row shape (t = end, dwell_ms, nav, abandon): only the
    sessions the observer is specified to report (not cut off, not open at the end)."""
    return [{"type": "ime_shadow", "t": s["end_wall_ms"], "dwell_ms": s["dwell_ms"], "nav": s["nav"],
             "abandon": s["abandon"]} for s in sessions if s["end"] == "row"]


def _lines(rows):
    return ("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n").encode("utf-8")


def as_obs_bytes(pred):
    """The predicted rows as an observation log (obs/1: a header, then the ime_shadow rows in time
    order). The matcher reads only the rows; the header names the source."""
    hdr = {"type": "meta", "format": "obs/1", "os": "macos", "source": "spec_predict",
           "inputs_sha256": pred["inputs_sha256"]}
    rows = sorted(as_obs_rows(pred["sessions"]), key=lambda r: (r["t"], r["dwell_ms"], r["nav"], r["abandon"]))
    return _lines([hdr] + rows)


def as_sessions_bytes(pred, sessions_bytes, oracle_bytes):
    """The predicted rows as reference sessions, in the form of gt_sessions.py's output, so that the
    observation can be matched against the prediction. Each row becomes a session over [start, end]
    (outcome cancel when abandon is set, otherwise commit; nav_true = the predicted nav), attributed to
    the trial it overlaps most, as the ground truth is. The exclusions of the ground truth
    (sessions_bytes) are carried over unchanged, so that every comparison covers the same trials. The
    oracle must be the one the prediction was made from."""
    if hashlib.sha256(oracle_bytes).hexdigest() != pred["inputs_sha256"]["oracle"]:
        raise InputError("oracle: not the oracle the prediction was made from")
    try:
        gt = json.loads(sessions_bytes.decode("utf-8"))
        oracle = gt_sessions.parse_oracle(oracle_bytes)
    except (ValueError, UnicodeDecodeError, gt_sessions.InputError) as e:
        raise InputError("sessions / oracle: %s" % e)
    if not isinstance(gt, dict) or not isinstance(gt.get("excluded"), list):
        raise InputError("sessions: not gt_sessions.py's output (no excluded list)")
    trials = sorted(oracle["trials"], key=lambda t: (t["begin_wall_ms"], t["end_wall_ms"]))
    begins = [t["begin_wall_ms"] for t in trials]
    rows = []
    for s in pred["sessions"]:
        if s["end"] != "row":
            continue
        lo, hi = s["start_wall_ms"], s["end_wall_ms"]
        rows.append({"trial_id": match._assign_trial(lo, hi, trials, begins), "start_wall_ms": lo, "end_wall_ms": hi,
                     "start_mono_ns": None, "end_mono_ns": None,
                     "outcome": "cancel" if s["abandon"] else "commit", "candidate_shown": None,
                     "nav_true": s["nav"], "nav_true_cand": None, "excluded_by": [], "n_start_while_open": 0})
    rows.sort(key=lambda r: (r["start_wall_ms"], r["end_wall_ms"], json.dumps(r, sort_keys=True)))
    out = {"src": "spec_predict", "rules_sha256": None,
           "definitions": {"all": rows, "macos_def": rows, "windows_def": None},
           "oracle_agreement": {"plan_available": False, "n_trials": None},
           "excluded": gt["excluded"]}
    return (json.dumps(out, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def predict(oracle_bytes, plan_bytes, keymap, limit_ms=LIMIT_MS):
    """The CLI's core (bytes in, an output dict out). Identical input always yields an identical dict."""
    plan = parse_plan(plan_bytes)
    if plan["os"] != "macos":
        raise InputError("plan: the prediction models the macOS observer only (plan os is %r)" % (plan["os"],))
    try:
        oracle = gt_sessions.parse_oracle(oracle_bytes)
    except gt_sessions.InputError as e:
        raise InputError(str(e))
    want = oracle["plan_sha256"]
    if not isinstance(want, str) or want.lower() != plan["sha256"]:
        raise InputError("plan: SHA-256 does not match the oracle header's plan_sha256 (unrelated plan)")
    sessions = predict_rows(oracle, plan, keymap, limit_ms)
    trials = summarise_trials(sessions, [t["trial_id"] for t in oracle["trials"]], closer_trials(oracle))
    counts = {"row": 0, "truncated": 0, "open": 0}
    for s in sessions:
        counts[s["end"]] += 1
    return {"format": FORMAT, "limit_ms": limit_ms,
            "inputs_sha256": {"oracle": hashlib.sha256(oracle_bytes).hexdigest(), "plan": plan["sha256"]},
            "counts": counts,
            "trials": {"n": len(trials), "n_long": sum(1 for d in trials.values() if d["long"]),
                       "n_carried_out": sum(1 for d in trials.values() if d["carried_out"]),
                       "n_received_carry": sum(1 for d in trials.values() if d["received_carry"]),
                       "n_split": sum(1 for d in trials.values() if d["n_started"] > 1),
                       "n_none": sum(1 for d in trials.values() if d["n_started"] == 0),
                       "n_closing_key_in_reading": sum(1 for d in trials.values() if d["closing_key_in_reading"]),
                       "by_stratum": by_stratum(trials, plan["strata"]),
                       "by_trial": trials},
            "sessions": sessions}


def _round6(v):
    return None if v is None else round(v, 6)


def _layer_figures(blocks):
    """{"n_trials", "n_blocks", "sum_m2_over_sum_m"} of one layer; blocks maps block_id to m_j, the number
    of the layer's trials in block j (only blocks with at least one trial)."""
    n = sum(blocks.values())
    return {"n_trials": n, "n_blocks": len(blocks),
            "sum_m2_over_sum_m": _round6(sum(m * m for m in blocks.values()) / n) if n else None}


def _range_figures(layers):
    """The smallest and the largest sum_m2_over_sum_m, and of n_blocks, over the layers with a trial;
    max_layer names the layer of the largest sum_m2_over_sum_m ("S14/mid"; when several layers share it, the first
    in the order of the layers). All null without such a layer."""
    have = [(k, v) for k, v in layers if v["n_trials"] > 0]
    if not have:
        return {"min": None, "max": None, "max_layer": None, "n_blocks_min": None, "n_blocks_max": None}
    top = max(have, key=lambda kv: kv[1]["sum_m2_over_sum_m"])
    return {"min": min(v["sum_m2_over_sum_m"] for _k, v in have), "max": top[1]["sum_m2_over_sum_m"],
            "max_layer": "%s/%s" % top[0],
            "n_blocks_min": min(v["n_blocks"] for _k, v in have), "n_blocks_max": max(v["n_blocks"] for _k, v in have)}


FIRST_RULE_LOSS_MS = 31
FIRST_RULE_MARGINS_MS = (100, 500)
_PSEUDO_KEYS = ("focus_away", "focus_back", "mouse_click")


def _time_near_presses(oracle_bytes, loss_ms, margin_ms):
    """The fraction of the time from the first row after the header to the last row of the oracle at which
    a loss of focus of loss_ms, widened by margin_ms on each side, would hold a press: a loss that begins
    at s spans [s - margin, s + loss + margin] and holds the press p when s lies in [p - loss - margin,
    p + margin]. The presses are the sent rows with down true whose key is not focus_away, focus_back or
    mouse_click (the input-mode keys are counted), and the marker rows. The length of the union of those
    ranges of s, clipped to the first and the last row, is summed in whole milliseconds. 0.0 without a
    press; null when the first and the last row share one time."""
    rows = [json.loads(ln) for ln in oracle_bytes.decode("utf-8").splitlines() if ln.strip()]
    if len(rows) < 2:
        return None
    t0, t1 = rows[1]["t_wall_ms"], rows[-1]["t_wall_ms"]
    if t1 == t0:
        return None
    presses = [r["t_wall_ms"] for r in rows[1:]
               if r.get("kind") == "marker"
               or (r.get("kind") == "sent" and r.get("down") is True and r.get("key") not in _PSEUDO_KEYS)]
    total, cur = 0, None
    for a, b in sorted((max(t0, p - loss_ms - margin_ms), min(t1, p + margin_ms)) for p in presses):
        if cur is None or a > cur[1]:
            if cur is not None:
                total += cur[1] - cur[0]
            cur = [a, b]
        elif b > cur[1]:
            cur[1] = b
    if cur is not None:
        total += cur[1] - cur[0]
    return _round6(total / (t1 - t0))


def plan_figures(plan_bytes, long_by_trial, text_size=TEXT_SIZE_TRIALS):
    """The figures of the design that the pre-registration quotes from the measurement plans, from the
    plan alone and the prediction's per-trial "long" flag (long_by_trial: {trial_id: bool}).

    "planned" counts every trial of the plan and "under_limit" the trials whose presses do not enter a
    session that is cut off (long is false), the trials of the primary scope. Per layer (scenario, speed):
    n_trials, n_blocks (blocks that hold a trial of the layer) and sum_m2_over_sum_m, the sum of m_j squared
    over the sum of m_j with m_j the number of the layer's trials in block j (null without a trial);
    n_blocks_by_speed counts the blocks of the plan per speed. The texts follow l2.py's text_layout:
    inside a block, in plan order, every consecutive run of text_size trials is a text and a shorter
    remainder is not used; per speed, r is the number of under_limit trials of a text, and the output has
    the number of texts, the mean of r (null without a text) and the histogram of r = 0 .. text_size.

    "focus_rule_first_version" is the figure of the first version of the rule for a loss of focus, which
    counted every key row within a loss: {"loss_ms": 31, "fraction_of_time_by_margin_ms": {"100": f, "500": f}}.
    f is the fraction of the time from the first to the last row of the oracle built from the plan
    (oracle_from_plan, the default time between blocks) at which a loss of focus of 31 ms, widened by the
    margin on each side, would hold a sent press. The presses counted are the sent presses (down true)
    except focus_away, focus_back and mouse_click (the input-mode keys are counted), and the marker presses
    (see _time_near_presses)."""
    if type(text_size) is not int or text_size < 1:
        raise InputError("text size: not a positive integer")
    rows = _plan_rows(plan_bytes)
    order, block_speed = {}, {}
    for r in rows[1:]:
        if not isinstance(r, dict):
            continue
        if r.get("kind") == "block":
            if r.get("speed") not in match.SPEEDS:
                raise InputError("plan: block %r has no speed or a speed that is not one of %s"
                                 % (r.get("block_id"), "/".join(match.SPEEDS)))
            block_speed[r.get("block_id")] = r.get("speed")
        elif r.get("kind") == "trial":
            tid = r.get("trial_id")
            if tid not in long_by_trial:
                raise InputError("prediction: trial %r of the plan has no flag" % (tid,))
            order.setdefault(r.get("block_id"), []).append((tid, r.get("scenario"), r.get("speed")))
    per = {"planned": {}, "under_limit": {}}
    for bid, items in order.items():
        for tid, sc, sp in items:
            block_speed.setdefault(bid, sp)
            per["planned"].setdefault((sc, sp), {}).setdefault(bid, 0)
            per["planned"][(sc, sp)][bid] += 1
            if not long_by_trial[tid]:
                per["under_limit"].setdefault((sc, sp), {}).setdefault(bid, 0)
                per["under_limit"][(sc, sp)][bid] += 1
    keys = sorted(per["planned"], key=lambda k: (int(k[0][1:]) if isinstance(k[0], str) and k[0][1:].isdigit() else 0, str(k[0]),
                                       match.SPEEDS.index(k[1]) if k[1] in match.SPEEDS else 9))
    layers = []
    for k in keys:
        layers.append({"scenario": k[0], "speed": k[1],
                       "planned": _layer_figures(per["planned"][k]),
                       "under_limit": _layer_figures(per["under_limit"].get(k, {}))})
    ks = dict((sp, []) for sp in match.SPEEDS)
    for bid, items in order.items():
        for j in range(len(items) // text_size):
            chunk = items[j * text_size:(j + 1) * text_size]
            speeds = sorted(set(sp for _t, _s, sp in chunk))
            if len(speeds) != 1:
                raise InputError("plan: block %r text %d mixes speeds" % (bid, j))
            ks.setdefault(speeds[0], []).append(sum(1 for t, _s, _p in chunk if not long_by_trial[t]))
    texts = {}
    for sp, vals in ks.items():
        texts[sp] = {"n_texts": len(vals), "mean": _round6(sum(vals) / len(vals)) if vals else None,
                     "histogram": [sum(1 for v in vals if v == i) for i in range(text_size + 1)]}
    oracle = oracle_from_plan(plan_bytes)
    return {"text_size_trials": text_size,
            "focus_rule_first_version": {
                "loss_ms": FIRST_RULE_LOSS_MS,
                "fraction_of_time_by_margin_ms": dict(
                    (str(m), _time_near_presses(oracle, FIRST_RULE_LOSS_MS, m)) for m in FIRST_RULE_MARGINS_MS)},
            "n_blocks_by_speed": dict((sp, sum(1 for b in block_speed.values() if b == sp)) for sp in match.SPEEDS),
            "layers": layers,
            "ranges": {w: _range_figures([((x["scenario"], x["speed"]), x[w]) for x in layers])
                       for w in ("planned", "under_limit")},
            "texts_by_speed": texts}


def render(out):
    return (json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------- selftest

def _run(events, limit_ms=LIMIT_MS, open_at=0):
    """events: [(t, name_or_vk, press_or_None)]; a name is a key class shortcut for readability."""
    names = {"a": 0x41, "k": 0x4B, "1": 0x31, "space": 0x20, "enter": 0x0D, "esc": 0x1B, "bs": 0x08,
             "left": 0x25, "right": 0x27, "tab": 0x09, "minus": 0xBD, "kana": 0xF2, "eisu": 0xF0, "f1": 0x70}
    m = _Machine(limit_ms)
    if open_at is not None:
        m.key(0xF2, True, open_at, None)
    for t, k, trial in events:
        vk = names[k] if isinstance(k, str) else k
        m.key(vk, True, t, trial)
        m.key(vk, False, t, trial)
    m.finish()
    return m.sessions


def _one(sessions):
    return [(s["start_wall_ms"], s["end_wall_ms"], s["nav"], s["abandon"], s["end"]) for s in sessions]


def _case_rules():
    f = []
    s = _one(_run([(10, "k", "t1"), (20, "a", "t1"), (30, "space", "t1"), (40, "enter", "t1")]))
    if s != [(10, 40, 0, False, "row")]:
        f.append("Return should commit a session that starts at the first letter: %r" % s)
    s = _one(_run([(10, "k", "t1"), (30, "space", "t1"), (40, "esc", "t1")]))
    if s != [(10, 40, 0, True, "row")]:
        f.append("Escape should abandon: %r" % s)
    s = _one(_run([(10, "k", "t1"), (20, "space", "t1"), (25, "a", "t1"), (30, "space", "t1"), (40, "enter", "t1")]))
    if s != [(10, 40, 0, False, "row")]:
        f.append("typing during candidates should keep one session: %r" % s)
    s = _one(_run([(10, "k", "t1"), (20, "bs", "t1"), (30, "bs", "t1"), (5000, "a", "t2"), (5100, "enter", "t2")]))
    if s != [(10, 5100, 0, False, "row")]:
        f.append("Backspace should keep the session open into the next trial: %r" % s)
    for other in ("tab", "minus", "f1"):
        s = _one(_run([(10, "k", "t1"), (20, other, "t1"), (30, "a", "t1"), (40, "enter", "t1")]))
        if s != [(10, 20, 0, False, "row"), (30, 40, 0, False, "row")]:
            f.append("%s should close as committed and the next letter should open a new session: %r" % (other, s))
    s = _one(_run([(10, "k", "t1"), (20, "eisu", "t1"), (30, "a", "t1"), (40, "enter", "t1")]))
    if s != [(10, 20, 0, False, "row")]:
        f.append("leaving Japanese input should close as committed and nothing should open while off: %r" % s)
    s = _one(_run([(10, "enter", None), (20, "esc", None), (30, "tab", None)]))
    if s:
        f.append("terminating keys outside a session should produce nothing: %r" % s)
    s = _one(_run([(10, "k", "t1"), (20, "enter", "t1")], open_at=None))
    if s:
        f.append("nothing should be produced while Japanese input is off: %r" % s)
    s = _one(_run([(5, "left", None), (10, "k", "t1"), (20, "right", "t1"), (25, "right", "t1"), (30, "enter", "t1"),
                   (40, "left", None), (50, "a", "t2"), (60, "enter", "t2")]))
    if s != [(10, 30, 2, False, "row"), (50, 60, 0, False, "row")]:
        f.append("arrows should count only inside a session and not leak into the next: %r" % s)
    s = _one(_run([(10, "k", "t1"), (20, "kana", "t1"), (30, "enter", "t1")]))
    if s != [(10, 30, 0, False, "row")]:
        f.append("an input-mode key should be transparent while Japanese input is on: %r" % s)
    s = _one(_run([(10, "space", "t1"), (20, "enter", "t1")]))
    if s:
        f.append("Space alone should not open a session: %r" % s)
    return f


def _case_cutoff():
    f = []
    s = _one(_run([(100, "k", "t1"), (100 + LIMIT_MS - 1, "enter", "t1")]))
    if s != [(100, 100 + LIMIT_MS - 1, 0, False, "row")]:
        f.append("a session just under the cut-off should still produce a row: %r" % s)
    s = _one(_run([(100, "k", "t1"), (100 + LIMIT_MS, "enter", "t1")]))
    if s != [(100, 100 + LIMIT_MS, 0, False, "truncated")]:
        f.append("a session that reaches the cut-off should be cut off and the Return applied to an empty state: %r" % s)
    s = _one(_run([(100, "k", "t1"), (100 + LIMIT_MS + 5, "1", "t1"), (100 + LIMIT_MS + 50, "enter", "t1")]))
    if s != [(100, 100 + LIMIT_MS + 5, 0, False, "truncated"), (100 + LIMIT_MS + 5, 100 + LIMIT_MS + 50, 0, False, "row")]:
        f.append("a digit after the cut-off should open a fresh session (a fragment row): %r" % s)
    s = _one(_run([(100, "k", "t1"), (100 + LIMIT_MS + 5, "eisu", "t1")]))
    if s != [(100, 100 + LIMIT_MS + 5, 0, False, "truncated")]:
        f.append("a mode key after the cut-off should cut off and produce nothing more: %r" % s)
    return f


def _case_keymap():
    f = []
    if vk_class(mac_to_vk(0x4F)) != ALNUM or vk_class(mac_to_vk(0x50)) != ALNUM:
        f.append("an unmapped code that passes through into the letter range should count as a letter")
    if vk_class(mac_to_vk(0x69)) != OTHER:
        f.append("F13 (passes through as 0x69) should close a session")
    if vk_class(mac_to_vk(0x30)) != OTHER or vk_class(mac_to_vk(0x1B)) != OTHER:
        f.append("Tab and Minus should close a session")
    if mode_after(0xF0, True, True) is not False or mode_after(0xF2, True, False) is not True:
        f.append("Eisu should close and Kana should open the input mode")
    if mode_after(VK_KANJI, True, False) is not True or mode_after(VK_KANJI, False, False) is not False:
        f.append("the Kanji key should toggle on press only")
    km = load_keymap(DEFAULT_KEYMAP)
    for name in READING_CLOSERS:
        if vk_class(mac_to_vk(km[name])) != OTHER:
            f.append("%s (in READING_CLOSERS) should close a session under the emitter's key map" % name)
    return f


def _source(rel):
    with open(os.path.join(_HERE, "..", rel), "rb") as fh:
        return fh.read().decode("utf-8")


def _case_source():
    """The rules above against the observer's source (a change on either side turns this red)."""
    import re
    f = []
    try:
        km = _source(os.path.join("logger", "src", "driver_macos", "keymap.rs"))
        comp = _source(os.path.join("logger", "src", "input", "ime_compose.rs"))
        cloak = _source(os.path.join("logger", "src", "input", "ime_cloak.rs"))
        hook = _source(os.path.join("logger", "src", "input", "hook.rs"))
        drv = _source(os.path.join("logger", "src", "driver_macos.rs"))
    except OSError as e:
        return ["cannot read the observer's source: %s" % e]
    body = km[km.index("fn macos_vk_to_vk"):]
    body = body[:body.index("other => other as u32")]
    consts = {}
    for name, val in re.findall(r"pub const (VK_[A-Z_]+): u32 = (0x[0-9A-Fa-f]+);", hook):
        consts[name] = int(val, 16)
    arms = re.findall(r"^\s*(0x[0-9A-Fa-f]+)\s*=>\s*(0x[0-9A-Fa-f]+|VK_[A-Z_]+)\s*,", body, re.M)
    src_map = dict((int(a, 16), int(b, 16) if b.startswith("0x") else consts.get(b)) for a, b in arms)
    if src_map != MAC_TO_VK:
        f.append("MAC_TO_VK differs from keymap.rs: %r" % sorted(set(src_map.items()) ^ set(MAC_TO_VK.items())))
    if consts.get("VK_KANJI") != VK_KANJI or consts.get("VK_DBE_ALPHANUMERIC") != VK_DBE_ALPHANUMERIC \
            or consts.get("VK_DBE_HIRAGANA") != VK_DBE_HIRAGANA:
        f.append("the mode-key constants differ from hook.rs")
    if not re.search(r"pub const MAX_SESSION_MS: u64 = %s;" % ("{:,}".format(LIMIT_MS).replace(",", "_")), cloak):
        f.append("LIMIT_MS differs from MAX_SESSION_MS in ime_cloak.rs")
    for needle in ("now_ms.saturating_sub(start) >= MAX_SESSION_MS",
                   "let restarted = apply(ComposeState::default(), vk, ime_open, chord, now_ms);",
                   "if !ime_open {\n        return finish(prev, false, now_ms);",
                   "0x30..=0x39 | 0x41..=0x5A =>",
                   "0x0D => finish(prev, false, now_ms),",
                   "0x1B => finish(prev, true, now_ms),",
                   "0x08 => {\n            let mut next = prev;\n            next.active = false;",
                   "0x25..=0x28 => {",
                   "_ if vk >= 0xF0 || vk == VK_KANJI => ComposeStep {",
                   "_ => finish(prev, false, now_ms),"):
        if needle not in comp:
            f.append("ime_compose.rs no longer contains %r" % needle)
    for needle in ("apply_mode_key(vk, press, &SHARED_MODE_CELLS);\n    if drives_compose(press) {",
                   "fn drives_compose(press: bool) -> bool {\n    press\n}"):
        if needle not in drv:
            f.append("driver_macos.rs no longer contains %r" % needle)
    return f


def _jl(rows):
    return ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode("utf-8")


def _fixture():
    """A two-block plan and its oracle: S1-like, S6-like (carried into the next trial), a trial with a
    Tab, a trial cut off at 20 s, and a marker between blocks."""
    plan = _jl([{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1},
                {"kind": "block", "block_id": 0, "speed": "slow", "reset_ime": False, "marker": ["left", "right"], "marker_gap_ms": 150},
                {"kind": "block", "block_id": 1, "speed": "fast", "reset_ime": False, "marker": ["left", "right"], "marker_gap_ms": 150}])
    sha = hashlib.sha256(plan).hexdigest()
    rows = [{"kind": "header", "format": "oracle/1", "plan_sha256": sha, "os": "macos", "emitter_version": "x", "backend": "dryrun"}]
    clock = [0]

    def at(t, **kw):
        clock[0] += 1
        r = dict(kw, t_wall_ms=t, t_mono_ns=clock[0])
        rows.append(r)

    def press(tid, key, t):
        at(t, kind="sent", trial_id=tid, step=0, key=key, down=True)
        at(t + 60, kind="sent", trial_id=tid, step=0, key=key, down=False)

    at(1000, kind="block_begin", block_id=0)
    at(1000, kind="marker", block_id=0, seq=0)
    at(1150, kind="marker", block_id=0, seq=1)
    at(2000, kind="trial_begin", trial_id="t1")
    for i, k in enumerate(["k", "a", "space"]):
        press("t1", k, 2000 + 200 * i)
    press("t1", "enter", 3000)
    press("t1", "enter", 3500)
    at(3600, kind="trial_end", trial_id="t1")
    at(5000, kind="trial_begin", trial_id="t2")
    press("t2", "k", 5000)
    press("t2", "a", 5200)
    press("t2", "backspace", 5400)
    press("t2", "backspace", 5600)
    at(5700, kind="trial_end", trial_id="t2")
    at(7000, kind="trial_begin", trial_id="t3")
    press("t3", "k", 7000)
    press("t3", "tab", 7200)
    press("t3", "enter", 9000)
    at(9100, kind="trial_end", trial_id="t3")
    at(9200, kind="block_end", block_id=0)
    at(10000, kind="block_begin", block_id=1)
    at(10000, kind="marker", block_id=1, seq=0)
    at(10150, kind="marker", block_id=1, seq=1)
    at(11000, kind="trial_begin", trial_id="t4")
    press("t4", "k", 11000)
    press("t4", "space", 11200)
    at(11300, kind="sent", trial_id="t4", step=2, key="focus_away", down=True)
    press("t4", "enter", 11000 + LIMIT_MS + 10)
    press("t4", "enter", 11000 + LIMIT_MS + 500)
    at(11000 + LIMIT_MS + 600, kind="trial_end", trial_id="t4")
    at(11000 + LIMIT_MS + 700, kind="block_end", block_id=1)
    return plan, _jl(rows)


def _keymap_fixture():
    return {"k": 0x28, "a": 0x00, "space": 0x31, "enter": 0x24, "backspace": 0x33, "tab": 0x30,
            "left": 0x7B, "right": 0x7C, "shift": 0x38, "minus": 0x1B}


def _case_end_to_end():
    f = []
    plan, oracle = _fixture()
    out = predict(oracle, plan, _keymap_fixture())
    got = [(s["start_wall_ms"], s["end_wall_ms"], s["end"], s["start_trial"], s["end_trial"]) for s in out["sessions"]]
    want = [(2000, 3000, "row", "t1", "t1"),          # first Return closes (the second has no session)
            (5000, 7200, "row", "t2", "t3"),          # Backspace keeps it open into t3, Tab closes it
            (11000, 11000 + LIMIT_MS + 10, "truncated", "t4", "t4")]
    if got != want:
        f.append("predicted sessions: %r != %r" % (got, want))
    by = out["trials"]["by_trial"]
    if not by["t2"]["carried_out"] or by["t3"]["n_started"] != 0 or not by["t4"]["long"] or by["t1"]["long"]:
        f.append("per-trial summary wrong: %r" % by)
    if [tid for tid in sorted(by) if by[tid]["received_carry"]] != ["t3"] or out["trials"]["n_received_carry"] != 1:
        f.append("received_carry should mark only t3 (its presses went into t2's session): %r" % by)
    if [s["trials"] for s in out["sessions"]] != [["t1"], ["t2", "t3"], ["t4"]]:
        f.append("the trials whose presses entered each session: %r" % [s["trials"] for s in out["sessions"]])
    want_strata = [{"scenario": None, "speed": None, "n": 4, "long": 1, "under_limit": 3, "carried_out": 1,
                    "received_carry": 1, "closing_key_in_reading": 0}]
    if out["trials"]["by_stratum"] != want_strata:
        f.append("by_stratum (the fixture's plan has no trial rows): %r" % out["trials"]["by_stratum"])
    if out["trials"]["n_long"] != 1 or out["counts"] != {"row": 2, "truncated": 1, "open": 0}:
        f.append("counts wrong: %r / %r" % (out["trials"], out["counts"]))
    if len(as_obs_rows(out["sessions"])) != 2:
        f.append("only reported sessions should become rows")
    if render(predict(oracle, plan, _keymap_fixture())) != render(out):
        f.append("two runs on the same input should give the same bytes")
    return f


def _case_marker_inside_session():
    """A session left open at the end of a block (S6 last in its block) collects the next block's
    marker arrows as nav, the Kana press at block_begin does not close it, and the next trial's
    letters continue it until the cut-off."""
    f = []
    plan, oracle = _fixture()
    rows = [json.loads(x) for x in oracle.decode("utf-8").splitlines()]
    # drop t3 so that t2's session is still open at block 1's start
    rows = [r for r in rows if r.get("trial_id") != "t3"]
    out = predict(_jl(rows), plan, _keymap_fixture())
    s = out["sessions"][1]
    if not (s["start_trial"] == "t2" and s["nav"] == 2 and s["end"] == "truncated" and s["end_trial"] == "t4"):
        f.append("a session open across the block boundary: %r" % s)
    return f


def _case_long_across_trials():
    """long marks every trial whose presses entered a session that is cut off, also when the session was
    carried over from an earlier trial; the press that cuts a session off does not enter it, so a trial
    whose first press comes after the cut-off is neither long nor received_carry."""
    f = []
    names = {"k": 0x4B, "a": 0x41, "bs": 0x08, "enter": 0x0D}
    m = _Machine()
    m.key(0xF2, True, 0, None)
    for t, k, tid in ((100, "k", "t1"), (200, "bs", "t1"),          # t1: S6-like, the session stays open
                      (5000, "a", "t2"), (5100, "a", "t2"),         # t2: its presses continue t1's session
                      (100 + LIMIT_MS + 1, "a", "t2"),              # ... until a press of t2 cuts it off and opens a fragment
                      (100 + LIMIT_MS + 50, "enter", "t2"),
                      (100 + LIMIT_MS + 900, "k", "t3"), (100 + LIMIT_MS + 950, "enter", "t3")):
        m.key(names[k], True, t, tid)
        m.key(names[k], False, t, tid)
    m.finish()
    by = summarise_trials(m.sessions, ["t1", "t2", "t3"])
    got = dict((t, (d["long"], d["received_carry"], d["carried_out"], d["n_started"])) for t, d in by.items())
    want = {"t1": (True, False, True, 1), "t2": (True, True, False, 1), "t3": (False, False, False, 1)}
    if got != want:
        f.append("long / received_carry / carried_out / n_started: %r != %r" % (got, want))
    m = _Machine()
    m.key(0xF2, True, 0, None)
    for t, k, tid in ((100, "k", "t1"), (200, "bs", "t1"), (100 + LIMIT_MS, "a", "t2"), (100 + LIMIT_MS + 50, "enter", "t2")):
        m.key(names[k], True, t, tid)
        m.key(names[k], False, t, tid)
    m.finish()
    by = summarise_trials(m.sessions, ["t1", "t2"])
    if (by["t2"]["long"], by["t2"]["received_carry"], by["t1"]["long"]) != (False, False, True):
        f.append("a trial whose first press cuts the carried session off should be neither long nor received_carry: %r" % by)
    return f


def _case_oracle_from_plan():
    """oracle_from_plan follows the executor's timing: markers marker_gap_ms apart from block_begin, a trial
    gap_before_ms after the previous press, its steps at at_ms, the block's end at its last press and the
    next block block_gap_ms later; the result is a valid oracle for the plan, and predict reads it."""
    f = []
    rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1},
            {"kind": "block", "block_id": 0, "speed": "slow", "reset_ime": False, "marker": ["left", "right"], "marker_gap_ms": 150},
            {"kind": "trial", "trial_id": "t1", "block_id": 0, "scenario": "S1", "speed": "slow", "gap_before_ms": 1000,
             "steps": [{"key": "k", "at_ms": 0, "down": True}, {"key": "k", "at_ms": 60, "down": False},
                       {"key": "enter", "at_ms": 500, "down": True}, {"key": "enter", "at_ms": 560, "down": False}]},
            {"kind": "block", "block_id": 1, "speed": "fast", "reset_ime": False, "marker": ["left"], "marker_gap_ms": 150},
            {"kind": "trial", "trial_id": "t2", "block_id": 1, "scenario": "S3", "speed": "fast", "gap_before_ms": 1000,
             "steps": [{"key": "a", "at_ms": 0, "down": True}, {"key": "a", "at_ms": 60, "down": False}]}]
    plan = _jl(rows)
    ob = oracle_from_plan(plan, 2000)
    got = [(r["kind"], r["t_wall_ms"]) for r in (json.loads(x) for x in ob.decode("utf-8").splitlines()[1:])]
    want = [("block_begin", 0), ("marker", 0), ("marker", 150), ("trial_begin", 1150), ("sent", 1150), ("sent", 1210),
            ("sent", 1650), ("sent", 1710), ("trial_end", 1710), ("block_end", 1710),
            ("block_begin", 3710), ("marker", 3710), ("trial_begin", 4710), ("sent", 4710), ("sent", 4770),
            ("trial_end", 4770), ("block_end", 4770)]
    if got != want:
        f.append("oracle_from_plan times: %r" % got)
    oracle = gt_sessions.parse_oracle(ob)
    if [t["trial_id"] for t in oracle["trials"]] != ["t1", "t2"]:
        f.append("the oracle's trials: %r" % oracle["trials"])
    out = predict(ob, plan, _keymap_fixture())
    st = [(s["scenario"], s["speed"], s["n"], s["under_limit"]) for s in out["trials"]["by_stratum"]]
    if st != [("S1", "slow", 1, 1), ("S3", "fast", 1, 1)]:
        f.append("by_stratum from the plan's strata: %r" % st)
    if oracle_from_plan(plan, 2000) != ob or oracle_from_plan(plan, 0) == ob:
        f.append("oracle_from_plan should be deterministic and use block_gap_ms")
    for label, bad in (("negative gap", lambda: oracle_from_plan(plan, -1)),
                       ("steps out of order", lambda: oracle_from_plan(plan.replace(b'"at_ms": 500', b'"at_ms": 5')))):
        try:
            bad()
            f.append("%s was not refused" % label)
        except InputError:
            pass
    return f


def _case_closers_and_adapters():
    """closing_key_in_reading marks only the trial that sends a closer; the two adapters give what the
    matcher reads (rows in time order; sessions attributed by overlap, with the ground truth's
    exclusions), and a foreign oracle is refused."""
    f = []
    plan, oracle = _fixture()
    if predict(oracle, plan, _keymap_fixture())["trials"]["n_closing_key_in_reading"] != 0:
        f.append("Tab is not one of the reading's closers (t3 sends Tab only)")
    oracle = oracle.replace(b'"key": "tab"', b'"key": "minus"')
    pred = predict(oracle, plan, _keymap_fixture())
    flags = dict((t, d["closing_key_in_reading"]) for t, d in pred["trials"]["by_trial"].items())
    if flags != {"t1": False, "t2": False, "t3": True, "t4": False} or pred["trials"]["n_closing_key_in_reading"] != 1:
        f.append("closing_key_in_reading: %r" % flags)
    rows = match.parse_obs(as_obs_bytes(pred))
    if [(r["t"], r["dwell_ms"]) for r in rows] != [(3000, 1000), (7200, 2200)]:
        f.append("as_obs_bytes rows: %r" % rows)
    excluded = [{"type": "clock_jump", "log": "gt", "trial_ids": ["t4"]}]
    gt = json.dumps({"definitions": {"all": []}, "excluded": excluded}).encode("utf-8")
    sb = as_sessions_bytes(pred, gt, oracle)
    _src, sess, exc = match.parse_sessions(sb, "macos_def")
    got = [(s["trial_id"], s["start_wall_ms"], s["end_wall_ms"], s["outcome"], s["nav_true"]) for s in sess]
    # the second row spans t2 (700 ms of overlap) and t3 (200 ms): it belongs to t2
    if got != [("t1", 2000, 3000, "commit", 0), ("t2", 5000, 7200, "commit", 0)] or exc != excluded:
        f.append("as_sessions_bytes: %r / %r" % (got, exc))
    if as_sessions_bytes(pred, gt, oracle) != sb or as_obs_bytes(pred) != as_obs_bytes(predict(oracle, plan, _keymap_fixture())):
        f.append("the adapters should give the same bytes for the same input")
    # attribution is by overlap, not by the trial the session started in: a row from the end of t1
    # (2000-3600) into t2 (5000-5700) overlaps t1 by 100 ms and t2 by 600 ms
    moved = dict(pred, sessions=[{"start_wall_ms": 3500, "end_wall_ms": 5600, "dwell_ms": 2100, "nav": 1, "abandon": True,
                                  "end": "row", "start_trial": "t1", "end_trial": "t2"},
                                 {"start_wall_ms": 100, "end_wall_ms": 200, "dwell_ms": 100, "nav": 0, "abandon": False,
                                  "end": "truncated", "start_trial": None, "end_trial": None}])
    _src, sess2, _exc = match.parse_sessions(as_sessions_bytes(moved, gt, oracle), "all")
    if [(s["trial_id"], s["outcome"], s["nav_true"]) for s in sess2] != [("t2", "cancel", 1)]:
        f.append("as_sessions_bytes should attribute by overlap and leave out sessions that are not rows: %r" % sess2)
    try:
        as_sessions_bytes(pred, gt, oracle + b"\n")
        f.append("a foreign oracle was not refused")
    except InputError:
        pass
    return f


def _case_refusals():
    f = []
    plan, oracle = _fixture()
    for label, call in (
            ("windows plan", lambda: predict(oracle, plan.replace(b'"os": "macos"', b'"os": "windows"', 1), _keymap_fixture())),
            ("unrelated plan", lambda: predict(oracle, plan + _jl([{"kind": "block", "block_id": 9, "marker": []}]), _keymap_fixture())),
            ("unknown key", lambda: predict(oracle, plan, dict((k, v) for k, v in _keymap_fixture().items() if k != "tab"))),
            ("modifier sent as a key", lambda: predict(oracle.replace(b'"key": "tab"', b'"key": "shift"'), plan, _keymap_fixture()))):
        try:
            call()
            f.append("%s was not refused" % label)
        except InputError:
            pass
    return f


def _case_cli():
    import contextlib
    import io
    import tempfile
    f = []
    plan, oracle = _fixture()
    with tempfile.TemporaryDirectory() as d:
        p, o, k, out = (os.path.join(d, n) for n in ("plan.jsonl", "oracle.jsonl", "km.json", "out.json"))
        for path, data in ((p, plan), (o, oracle), (k, json.dumps(_keymap_fixture()).encode("utf-8"))):
            with open(path, "wb") as fh:
                fh.write(data)
        if main(["--oracle", o, "--plan", p, "--keymap", k, "--out", out]) != 0:
            f.append("the CLI did not exit 0")
        elif json.loads(open(out, encoding="utf-8").read())["counts"]["truncated"] != 1:
            f.append("the CLI output is not the prediction")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--oracle", o, "--plan", o, "--keymap", k, "--out", out + "2"])
        if rc != 2:
            f.append("a bad plan should exit 2")
        if main(["--from-plan", "--plan", p, "--keymap", k, "--out", out + "3"]) != 0:
            f.append("--from-plan did not exit 0")
        else:
            got = json.loads(open(out + "3", encoding="utf-8").read())
            if got.get("oracle_from_plan") != {"block_gap_ms": BLOCK_GAP_MS} \
                    or got["inputs_sha256"]["oracle"] != hashlib.sha256(oracle_from_plan(plan)).hexdigest():
                f.append("--from-plan should predict from oracle_from_plan(plan) and say so")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--from-plan", "--oracle", o, "--plan", p, "--keymap", k, "--out", out + "4"])
        if rc != 2:
            f.append("--from-plan with --oracle should exit 2")
        # error messages hold no directory part: an unreadable keymap, an unreadable plan, an unwritable --out
        for label, args in (("keymap", ["--from-plan", "--plan", p, "--keymap", os.path.join(d, "nodir", "nokm.json"), "--out", out + "5"]),
                            ("plan", ["--from-plan", "--plan", os.path.join(d, "nodir", "noplan.jsonl"), "--keymap", k, "--out", out + "6"]),
                            ("out", ["--from-plan", "--plan", p, "--keymap", k, "--out", os.path.join(d, "nodir", "o.json")])):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = main(args)
            msg = err.getvalue()
            if rc != 2 or not msg or d in msg or "nodir" in msg:
                f.append("an unreadable/unwritable %s should exit 2 with no directory in the message: %r" % (label, msg))
    return f


def _figures_fixture():
    """A plan of 4 blocks (the trials of block 1 are not contiguous in the file) and the trials whose
    presses enter a cut-off session (long)."""
    def trial(n, bid, sc, sp):
        return {"kind": "trial", "trial_id": "t%d" % n, "block_id": bid, "scenario": sc, "speed": sp,
                "gap_before_ms": 1000, "steps": [{"at_ms": 0, "key": "a", "down": True}]}
    rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1},
            {"kind": "block", "block_id": 0, "speed": "slow"}, {"kind": "block", "block_id": 1, "speed": "slow"},
            {"kind": "block", "block_id": 2, "speed": "fast"}, {"kind": "block", "block_id": 3, "speed": "mid"}]
    for n, sc in enumerate(("S1", "S1", "S2", "S1", "S1", "S2", "S1", "S1", "S1", "S2"), 1):
        rows.append(trial(n, 0, sc, "slow"))
    rows += [trial(11, 1, "S1", "slow"), trial(12, 1, "S2", "slow")]
    rows += [trial(n, 2, "S1", "fast") for n in range(14, 19)]
    rows.append(trial(13, 1, "S2", "slow"))
    longs = dict(("t%d" % n, n in (2, 6, 12, 15)) for n in range(1, 19))
    return _jl(rows), longs


def _case_plan_figures():
    f = []
    plan, longs = _figures_fixture()
    pf = plan_figures(plan, longs, 4)
    lay = dict(((x["scenario"], x["speed"]), x) for x in pf["layers"])
    # planned: slow S1 has 7 trials in block 0 and 1 in block 1 -> (49 + 1) / 8; S2 3 + 2 -> 13 / 5; fast S1 5 in
    # one block -> 25 / 5. Under the limit: S1 slow 6 + 1 -> 37 / 7; S2 slow 2 + 1 -> 5 / 3; fast S1 4 -> 16 / 4
    want = {("S1", "slow"): (("planned", 8, 2, 6.25), ("under_limit", 7, 2, 5.285714)),
            ("S2", "slow"): (("planned", 5, 2, 2.6), ("under_limit", 3, 2, 1.666667)),
            ("S1", "fast"): (("planned", 5, 1, 5.0), ("under_limit", 4, 1, 4.0))}
    if [(x["scenario"], x["speed"]) for x in pf["layers"]] != [("S1", "slow"), ("S1", "fast"), ("S2", "slow")]:
        f.append("the layers or their order: %r" % ([(x["scenario"], x["speed"]) for x in pf["layers"]],))
    for k, ws in want.items():
        for w, n, nb, r in ws:
            got = lay[k][w]
            if got != {"n_trials": n, "n_blocks": nb, "sum_m2_over_sum_m": r}:
                f.append("%s %s: %r, want %r" % (k, w, got, (n, nb, r)))
    rg = pf["ranges"]
    if rg["planned"] != {"min": 2.6, "max": 6.25, "max_layer": "S1/slow", "n_blocks_min": 1, "n_blocks_max": 2} or \
            rg["under_limit"] != {"min": 1.666667, "max": 5.285714, "max_layer": "S1/slow", "n_blocks_min": 1,
                                  "n_blocks_max": 2}:
        f.append("the ranges: %r" % (rg,))
    if pf["n_blocks_by_speed"] != {"slow": 2, "mid": 1, "fast": 1}:
        f.append("blocks per speed: %r" % (pf["n_blocks_by_speed"],))
    # the first rule's figure: a hand-built oracle, first row after the header t0 = 0, last row t1 = 10000, L = 31.
    # Presses: key a down 50, key a down 1000, marker 1100, ime_on down 5000, ime_off down 9990. Not presses: an up
    # of b at 7000, focus_away down 1200, mouse_click down 8000. Each press p gives s in [p - 31 - M, p + M] clipped.
    # M = 100: [0,150] (50 clipped at t0) + [869,1100] and [969,1200] -> [869,1200] (331) + [4869,5100] (231)
    #   + [9859,10000] (9990 clipped at t1: 141) = 150 + 331 + 231 + 141 = 853 -> 0.0853
    # M = 500: [0,550] (clipped), [469,1500], [569,1600] -> [0,1600] (1600) + [4469,5500] (1031) + [9459,10000]
    #   (541) = 3172 -> 0.3172
    ob = _jl([{"kind": "header", "format": "oracle/1"},
              {"kind": "block_begin", "t_wall_ms": 0},
              {"kind": "sent", "t_wall_ms": 50, "key": "a", "down": True},
              {"kind": "sent", "t_wall_ms": 1000, "key": "a", "down": True},
              {"kind": "marker", "t_wall_ms": 1100},
              {"kind": "sent", "t_wall_ms": 1200, "key": "focus_away", "down": True},
              {"kind": "sent", "t_wall_ms": 5000, "key": "ime_on", "down": True},
              {"kind": "sent", "t_wall_ms": 7000, "key": "b", "down": False},
              {"kind": "sent", "t_wall_ms": 8000, "key": "mouse_click", "down": True},
              {"kind": "sent", "t_wall_ms": 9990, "key": "ime_off", "down": True},
              {"kind": "block_end", "t_wall_ms": 10000}])
    got = (_time_near_presses(ob, 31, 100), _time_near_presses(ob, 31, 500))
    if got != (0.0853, 0.3172):
        f.append("the time near presses: %r, want (0.0853, 0.3172)" % (got,))
    if _time_near_presses(_jl([{"kind": "header"}, {"kind": "block_begin", "t_wall_ms": 0},
                               {"kind": "block_end", "t_wall_ms": 10}]), 31, 100) != 0.0 or \
            _time_near_presses(_jl([{"kind": "header"}, {"kind": "marker", "t_wall_ms": 5}]), 31, 100) is not None:
        f.append("the time near presses: no press should give 0.0 and one time should give null")
    ff = pf["focus_rule_first_version"]
    if ff != {"loss_ms": 31, "fraction_of_time_by_margin_ms": {
            "100": _time_near_presses(oracle_from_plan(plan), 31, 100),
            "500": _time_near_presses(oracle_from_plan(plan), 31, 500)}} or \
            not all(0 < v < 1 for v in ff["fraction_of_time_by_margin_ms"].values()):
        f.append("focus_rule_first_version: %r" % (ff,))
    # texts of 4: block 0 -> t1..t4 (t2 long: k 3), t5..t8 (t6 long: k 3), remainder t9 t10; block 1 has 3 trials
    # (no text); block 2 -> t14..t17 (t15 long: k 3), remainder t18
    want_t = {"slow": {"n_texts": 2, "mean": 3.0, "histogram": [0, 0, 0, 2, 0]},
              "mid": {"n_texts": 0, "mean": None, "histogram": [0, 0, 0, 0, 0]},
              "fast": {"n_texts": 1, "mean": 3.0, "histogram": [0, 0, 0, 1, 0]}}
    if pf["texts_by_speed"] != want_t:
        f.append("the texts: %r" % (pf["texts_by_speed"],))
    if json.dumps(pf) != json.dumps(plan_figures(plan, longs, 4)):
        f.append("plan_figures should be deterministic")
    # the same texts as l2.py's text_layout (every trial run, none excluded, none out of scope)
    import l2
    pl = match.parse_plan_strata(plan)
    orc = {"trials": [{"trial_id": t, "begin_wall_ms": 1000 * i, "end_wall_ms": 1000 * i + 500}
                      for i, t in enumerate(pl["trials"])]}
    for size in (1, 3, 4, 8):
        sizes = dict((sp, []) for sp in match.SPEEDS)
        for t in l2.text_layout(pl, orc, size)["texts"]:
            sizes[t["speed"]].append(sum(1 for x in t["trial_ids"] if not longs[x]))
        got = plan_figures(plan, longs, size)["texts_by_speed"]
        if any(got[sp]["n_texts"] != len(v) or got[sp]["histogram"] != [v.count(i) for i in range(size + 1)]
               for sp, v in sizes.items()):
            f.append("the texts of size %d differ from l2.text_layout" % size)
    # no long trial: the two views agree; all long: nothing under the limit, null and not 0
    pn = plan_figures(plan, dict((t, False) for t in longs), 4)
    if [x["planned"] for x in pn["layers"]] != [x["under_limit"] for x in pn["layers"]]:
        f.append("without a long trial under_limit should equal planned")
    pa = plan_figures(plan, dict((t, True) for t in longs), 4)
    if any(x["under_limit"] != {"n_trials": 0, "n_blocks": 0, "sum_m2_over_sum_m": None} for x in pa["layers"]) or \
            pa["ranges"]["under_limit"] != {"min": None, "max": None, "max_layer": None, "n_blocks_min": None,
                                            "n_blocks_max": None} or pa["texts_by_speed"]["slow"]["mean"] != 0.0:
        f.append("with every trial long: %r" % (pa["ranges"]["under_limit"],))
    for label, bad_row in (("a block without a speed", {"kind": "block", "block_id": 9}),
                           ("a block with a speed that is not one of the speeds", {"kind": "block", "block_id": 9, "speed": "warp"})):
        try:
            plan_figures(plan + _jl([bad_row]), longs, 4)
            f.append("%s was not refused" % label)
        except InputError:
            pass
    for label, call in (("a text size of 0", lambda: plan_figures(plan, longs, 0)),
                        ("a text size that is not an int", lambda: plan_figures(plan, longs, 2.5)),
                        ("a trial without a flag", lambda: plan_figures(plan, {}, 4))):
        try:
            call()
            f.append("%s was not refused" % label)
        except InputError:
            pass
    # through the CLI: the key is added only with --from-plan, and a bad text size exits 2
    import contextlib
    import io
    import tempfile
    plan2, _oracle2 = _fixture()
    with tempfile.TemporaryDirectory() as d:
        pp, kk, o1, o2 = (os.path.join(d, n) for n in ("p.jsonl", "k.json", "o1.json", "o2.json"))
        with open(pp, "wb") as fh:
            fh.write(plan2)
        with open(kk, "wb") as fh:
            fh.write(json.dumps(_keymap_fixture()).encode("utf-8"))
        if main(["--from-plan", "--plan", pp, "--keymap", kk, "--text-size", "1", "--out", o1]) != 0:
            f.append("--from-plan with --text-size did not exit 0")
        else:
            got = json.loads(open(o1, encoding="utf-8").read())
            if got["format"] != FORMAT or got["plan_figures"]["text_size_trials"] != 1 or \
                    sorted(got["plan_figures"]) != ["focus_rule_first_version", "layers", "n_blocks_by_speed",
                                                    "ranges", "text_size_trials", "texts_by_speed"]:
                f.append("plan_figures in the --from-plan output: %r" % (sorted(got.get("plan_figures", {})),))
        with contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--from-plan", "--plan", pp, "--keymap", kk, "--text-size", "0", "--out", o2])
        if rc != 2:
            f.append("--text-size 0 should exit 2")
    return f


def selftest():
    cases = [("rules", _case_rules), ("cutoff", _case_cutoff), ("keymap", _case_keymap),
             ("source", _case_source), ("end_to_end", _case_end_to_end),
             ("marker_inside_session", _case_marker_inside_session),
             ("long_across_trials", _case_long_across_trials), ("oracle_from_plan", _case_oracle_from_plan),
             ("closers_and_adapters", _case_closers_and_adapters), ("refusals", _case_refusals),
             ("cli", _case_cli), ("plan_figures", _case_plan_figures)]
    bad = 0
    for name, fn in cases:
        try:
            fails = fn()
        except Exception as e:  # a crash in a case is a failure of that case, not of the run
            fails = ["raised %s: %s" % (type(e).__name__, e)]
        print("%s %s" % ("ok  " if not fails else "FAIL", name))
        for x in fails:
            print("     " + x)
        bad += 1 if fails else 0
    print("%d of %d cases passed" % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser(prog="spec_predict.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--oracle")
    ap.add_argument("--plan")
    ap.add_argument("--from-plan", action="store_true",
                    help="build the oracle from the plan's own times (oracle_from_plan) instead of reading --oracle")
    ap.add_argument("--block-gap-ms", type=int, default=BLOCK_GAP_MS,
                    help="with --from-plan, the time between blocks (default %d)" % BLOCK_GAP_MS)
    ap.add_argument("--text-size", type=int, default=TEXT_SIZE_TRIALS,
                    help="with --from-plan, the trials of a text for plan_figures (default %d)" % TEXT_SIZE_TRIALS)
    ap.add_argument("--keymap", default=DEFAULT_KEYMAP)
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.plan and a.out and (a.oracle or a.from_plan)) or (a.oracle and a.from_plan):
        sys.stderr.write("spec_predict.py: --plan, --out and either --oracle or --from-plan are required\n")
        return 2
    try:
        with open(a.plan, "rb") as fh:
            pb = fh.read()
        if a.from_plan:
            ob = oracle_from_plan(pb, a.block_gap_ms)
        else:
            with open(a.oracle, "rb") as fh:
                ob = fh.read()
        out = predict(ob, pb, load_keymap(a.keymap))
        if a.from_plan:
            out["oracle_from_plan"] = {"block_gap_ms": a.block_gap_ms}
            out["plan_figures"] = plan_figures(pb, dict((t, d["long"]) for t, d in out["trials"]["by_trial"].items()),
                                               a.text_size)
    except OSError as e:
        sys.stderr.write("spec_predict.py: cannot read %s (%s)\n"
                         % (os.path.basename(str(e.filename or "an input")), type(e).__name__))
        return 2
    except InputError as e:
        sys.stderr.write("spec_predict.py: %s\n" % e)
        return 2
    if os.path.exists(a.out):
        sys.stderr.write("spec_predict.py: %s exists (not overwritten)\n" % os.path.basename(a.out))
        return 2
    try:
        with open(a.out, "wb") as fh:
            fh.write(render(out))
    except OSError as e:
        sys.stderr.write("spec_predict.py: cannot write %s (%s)\n" % (os.path.basename(a.out), type(e).__name__))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
