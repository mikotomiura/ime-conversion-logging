#!/usr/bin/env python3
# kit: selftest
"""End-to-end analysis of the first experiment: from run directories to the level-1 and level-2
agreement results and the detection results, in one command. Uses only the Python standard library.
Every stage is an existing module of this directory and is only called here, never reimplemented:
gt_sessions (ground-truth sessions), match (pairing and detection metrics), l1 (per-session
agreement) and l2 (aggregate agreement).

  pipeline.py --runs DIR [DIR ...] --config CFG.json --seed N --out RESULTS_DIR [--prereg PATH --seed-file SEED]
  pipeline.py --runs-from LIST.md --config CFG.json --seed N --out RESULTS_DIR [--prereg PATH --seed-file SEED]
  pipeline.py --freeze-hash --config CFG.json --seed-file SEED
  pipeline.py --check-prereg PATH --config CFG.json --seed-file SEED
  pipeline.py --synthetic DIR --seed N            write two synthetic run directories under DIR
  pipeline.py --check-deposit DIR [DIR ...]       refuse a run directory that is not safe to deposit
  pipeline.py --pack DIR --out ARCHIVE.zip        bundle the five deposit files into a checked archive
  pipeline.py --selftest [--full]                 default: a representative variant of every case
                                                  (one positive and the most important negatives of
                                                  each gate); --full: every variant, about a minute

A run directory (written by the run-cell tool) holds plan.jsonl, oracle.jsonl, gt.jsonl, obs.jsonl and
run.json (the manifest, format run/1). The manifest records the SHA-256 of every log; a log that no
longer matches its recorded hash stops the run. A run whose manifest says aborted is left out of the
matching and is counted (its cell is listed under "runs" in every result file).

Cell labels. The label of a cell is the "label" of the cell recorded in run.json (the cell file of the
run-cell tool carries it). A run without a label is refused; the operating system is never used in its
place. Among the runs that were not aborted, two runs must not share a label; an aborted run may share
the label of the run that replaced it and is counted.

Step 0 (checks that run before any statistic). The gt and obs logs must pass the content-free
validator (kit/formats/validate_logs.py), the plan must pass the plan validator, the oracle must pass
the oracle validator, and the header of the observation log must declare the "key" and "ime_shadow"
channels among its capabilities. When the header places "ime_shadow" among the gated channels
(threshold_gated or config_gated), the channel may legitimately produce no row; a run that has
ground-truth sessions but no "ime_shadow" row at all is then refused, because agreement cannot be
judged from an empty channel. Any violation is listed and the run stops with exit status 1.

Stages, per run directory (one cell): ground-truth sessions from gt, oracle and plan; for a macOS plan,
the observer's specified rows predicted from the oracle and the plan (spec_predict.py, with the emitter's
key map kit/emit/keymap_macos.json); then, for each of two session definitions (the one that follows the
operating system in the plan header, macos_def or windows_def, and the common definition "all"),
matching, level 1 and level 2 (run_stages). Three comparisons are matched: gt_obs (ground truth against
the observation), gt_pred (ground truth against the predicted rows: the validity of the observer's rules)
and pred_obs (the predicted rows against the observation: whether the observer carries out its rules).
Each is matched under three scopes, which set aside, at the level of a trial, the trials whose presses
went into a session the observer is specified to cut off at 20 seconds (match.py --scope): under_limit,
all_trials and over_limit. Level 1 is computed for every comparison under under_limit and all_trials,
level 2 for gt_obs only, under under_limit and all_trials. The primary estimates are named in e1_l1.json,
e1_l2.json and e1_detection.json ("primary"): gt_obs under under_limit. A plan for another system has no prediction: only
gt_obs under all_trials is run.
Every result file holds cells[label][definition][comparison][scope]; a comparison that needs the
prediction is null when there is none. e1_detection.json also holds, per cell, a summary of the
prediction ("predictions": its hash, counts, the long trials per stratum, and per stratum and scope the
number of letter keys and the think time of the trials, from the plan) and whether the two definitions
hold the same sessions ("same_sessions_as_all"). Every detection view carries focus_loss: the rows
assigned to the trials in which the reference input field lost focus, according to the delivery report
(a loss that is not regained covers the later trials too; the planned switch of S15 that returns inside
its trial is not counted there), and how many of them are false positives, as totals and per speed.

Configuration CFG (JSON; keys starting with "_" are annotations and are not read):
  {"iou_min": 0.5, "obs_end_offset_ms": 0, "theta": 0.5, "drop_start_while_open": false,
   "text_size_trials": 8, "bootstrap_B": 2000, "limit_ms": 20000, "mixtures": {...},
   "gt_rules": "path/to/rules.json"}
Every key is optional except gt_rules, which is resolved relative to the configuration file. The
clock offsets between the two logs are not passed to the matcher (all offsets are zero).

Seed. The seed is a command-line argument or the content of the seed file (one non-negative integer
without spaces; the experiment keeps it in one file). It is passed unchanged to every bootstrap of
level 1 and level 2, and each stage derives its own streams from it. A synthetic run directory number i
(i = 0, 1) uses the seed 1000 * seed + i to generate the ground truth and that value xor 0x5eed to
inject observation errors, so the two random streams are independent.

Freeze marks. Real data is analysed only after the analysis is frozen. The pre-registration file holds,
outside HTML comments and code fences, exactly one line "frozen_at: YYYY-MM-DD..." and exactly one line
"sha256: <64 lowercase hex digits>"; placeholders and empty values are not marks. The sha256 is the
SHA-256 of the text made of, in this order, the SHA-256 (lowercase hex) of the configuration file, of
the seed file and of the ground-truth rule file named by gt_rules, each followed by a line feed; --freeze-hash
prints it. Every analysis of real data needs --prereg and --seed-file, recomputes the value and stops with
exit status 3 when a mark is missing, malformed, duplicated or different. Only runs that carry the mark
"synthetic": true in their manifest (written by --synthetic) are exempt; the mark is a convenience for
rehearsals, not a defence against a person who edits a manifest. A synthetic result also carries
"synthetic": true and is never written under experiment/results.

Registered plans and cells. Section 2.4 of the pre-registration gives each cell its plan: one for live conversion
on, one for live conversion off. In an analysis of real data, a run that is not aborted is analysed only when the
cell recorded in its run.json says ime_settings.live_conversion is "on" or "off" and the SHA-256 of its plan log
(checked against run.json) equals the value REGISTERED_PLAN_SHA256 holds for that setting (a dictionary keyed by
"on" and "off"). A run whose cell has no such setting, or whose plan is the plan of the other setting or any other
plan, counts as aborted under section 3.4 and the analysis stops with exit status 2; the message names the cell
and says that its plan is not the plan that section 2.4 gives its cell, and holds no value and no hash. The
dictionary is recorded in e1_delivery.json ("abort_rule", registered_plan_sha256). Synthetic runs are not checked,
and neither are --pack and --check-deposit (runs that used other plans are deposited with the same commands).

Abort rule for the reference input field (pre-registration section 3.4). The delivery report divides the
reference log into segments at the block_begin rows of the oracle (delivery.py, Definitions) and counts in
each the key_down rows that no press of the measured trials explains (n_unexplained), in two versions that
differ in whether the mode keys count among the presses sent. REFERENCE_RECEIVES_MODE_KEYS chooses the version:
true uses "all_presses", false uses "excluding_mode_keys". A run is refused (exit status 2, the run counts as
aborted and is not analysed) when, in any segment, that count exceeds the number of marker presses the plan
puts at the head of the block, plus RUNNER_KANA_PRESSES_PER_BLOCK for every segment except the segment of the
last block (the runner selects the input method with one key before each block; the segment of the last block
has no such key after it, and the segment before the first block has the key of the first block; when the
oracle has no block_begin the one segment counts as the segment before the first block). A run is also
refused when the reference input field lost focus, for any reason (the planned switches of scenario S15 are
losses too), and the observation log holds, within that loss widened by delivery.FOCUS_MARGIN_MS on both sides
(merged where the widened losses overlap), a key row that no key sent by the executor explains
(reference_focus_loss.n_loss_spans_with_unexplained_presses is above 0). Two spans count as losses besides the
losses of the log: the time from the start of the logs to the first focus_in of the reference log (the whole
log when the reference log has no focus row at all), and the time after the last key_down or focus row of the
reference log; so a reference log of a faithful run starts with a focus_in, as the real reference input field
writes one when the runner brings it to the front, and the synthetic runs write one. The focus rows are read in
the order of the log. The rows are matched one to one, in time order: each row takes the earliest key not yet
taken that was sent from delivery.OBS_MATCH_AFTER_MS before the row to delivery.OBS_MATCH_BEFORE_MS after it (the
row comes after its key, by at most OBS_MATCH_AFTER_MS); a row left without a key is unmatched. The unmatched
rows of a span are counted in a window that extends forward past its end while keys sent follow each other
closely (delivery._extended_end), because the matching is global and a shortage can move to a later row. To
type into another application a person must take the focus from the reference, and the observation records the
keys it observes in every application, but the executor goes on sending keys during a loss, so only the rows
beyond the keys sent refuse the run. The count has blind spots (a key whose row the observation misses can take
one press of a person, a row stamped far from its key stays unmatched, a duplicated row counts); they are
listed in the docstring of delivery.py. Outside a loss of focus (widened, with the stretch counted after it), extra rows of
the observation log never refuse a run. The message names the cell and the block, never a value of a log. The rule and its constants are
recorded in e1_delivery.json ("abort_rule").

Outputs (RESULTS_DIR): e1_l1.json, e1_l2.json, e1_detection.json and e1_delivery.json (the per-press delivery
report of delivery.py, per cell: cells[label]). Each one carries the seed, the
SHA-256 of the configuration, of the rule file, of the key map, of every manifest and of every input log,
and the per-cell results; e1_l1.json, e1_l2.json and e1_detection.json also carry the primary comparison
and scope. No path is written to any of them, and identical inputs always give identical bytes
(sorted keys, LF, UTF-8, no BOM). The files are written to a temporary directory and moved into place; when
the move fails, no result of an earlier run is left behind.

Runs list. --runs-from reads lines "- run_dir: PATH  manifest_sha256: <64 hex digits>" (a relative PATH is
relative to the list file), outside HTML comments and code fences. The SHA-256 of run.json must match;
a line without it, or with a different one, stops the analysis. The labels that were used are printed.

Deposit check. Only run.json and the four logs are meant to be deposited. --check-deposit exits 1 when a
run directory is not safe to deposit and reports the file and line (never the value). It refuses a file
that contains (a) any absolute path: a drive letter, a UNC path, /Users/, /home/, /Volumes/, /root/,
/mnt/, an MSYS drive path such as /c/, a file: URI (any case, file: followed by a slash), in the plain spelling and in the spelling of a JSON string (only
paths that start with ~ or <repo>, and relative paths, are allowed); (b) the home directory of the
current user or of --also-home PATH (any number), in every separator spelling and in the MSYS spelling;
(c) the absolute path of the repository; (d) the login name of the current user or a name given with
--also-user NAME (any number), as a whole word; (e) content in the ground-truth or observation log (the
content-free validator), for every run, aborted by the runner or not (a log that an aborted run recorded with a
null hash and that is absent passes; a log that cannot be validated, for example because its last line is cut
off, refuses the run). For every run with a reference log, aborted by the
runner or not, it also applies the abort rule above (both parts) to the delivery report of the run: a run that
the rule would refuse is not safe to deposit, since a person may have typed elsewhere in it, and a run whose
delivery report cannot be computed (no plan or oracle log, or logs that do not fit) is reported as not
checkable, and so is a run with an observation log but no reference log (a loss of focus is seen only in the
reference log). A run aborted with neither log (null hashes) has nothing to check; a missing observation log
counts as empty. Each finding names the file and the reason, never a value of a log. It first checks every
recorded hash, so a changed log or a missing log whose hash is recorded exits 2; a log that an aborted run
recorded with a null hash need not exist. The repository path is found as the run-cell tool finds it. The standard-error
files are not deposited and are not read.
Known misses (this is a safeguard against slips, not proof of anonymity): a login name of at most two
characters is not searched for; a name is not found when it touches a letter, digit, underscore, dot or
hyphen; a bare short (8.3) name such as YAMADA~1 is found only inside a path; a path that does not begin
with one of the listed roots (for example /tmp/) is not recognised; host names, e-mail addresses and
other personal text are not searched; the current user is found only when the standard library can name
it. Pass the values of the measuring machine with --also-user and --also-home when the check runs on
another machine.

--pack reads the five files, runs the deposit check, writes them to a ZIP archive in a fixed order, with
fixed times (1980-01-01), fixed attributes and no compression (so the bytes do not depend on the
compression library), checks the contents of the archive again with the same check and prints its
SHA-256. An existing output file is never overwritten.

Self-test. The default --selftest runs, for every case, a positive variant and the most important negative
variants of each gate, calls functions directly where it can and starts a child process only for the shell
entry point; it is meant to finish in under 20 seconds. --selftest --full runs every variant (all freeze-mark
spellings, every shell-entry variant, every usage and configuration error, every spelling of a path, and the
rarer input errors) and takes about a minute. Files that have not changed are read from disk once during a
self-test.

Exit status: 0 on success; 1 when a check finds a violation; 2 when an input cannot be read, is
malformed or does not match its recorded hash, when usage is incorrect, or on an unexpected error (the
message then names the type of the error only); 3 when the analysis is not frozen.
"""
import argparse
import contextlib
import getpass
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import delivery  # noqa: E402  per-press delivery of the scripted input
import gt_sessions  # noqa: E402  ground-truth sessions
import inject  # noqa: E402  synthetic observation rows with known errors
import l1  # noqa: E402  level 1
import l2  # noqa: E402  level 2
import match  # noqa: E402  pairing, detection metrics, file helpers
import spec_predict  # noqa: E402  the observer's specified rows (the reference of the two-stage comparison)

InputError = gt_sessions.InputError
KIT = os.path.dirname(_HERE)
EXPERIMENT_RESULTS = os.path.join(os.path.dirname(KIT), "experiment", "results")
LOG_NAMES = ("plan", "oracle", "gt", "obs")
LOG_FILES = {"plan": "plan.jsonl", "oracle": "oracle.jsonl", "gt": "gt.jsonl", "obs": "obs.jsonl"}
MANIFEST = "run.json"
RUN_FORMAT = "run/1"
DEPOSIT_FILES = (MANIFEST,) + tuple(LOG_FILES[n] for n in LOG_NAMES)
DEFINITION_OF_OS = {"macos": "macos_def", "windows": "windows_def"}
REQUIRED_CHANNELS = ("key", "ime_shadow")
GATED_KEYS = ("threshold_gated", "config_gated")
CONFIG_KEYS = ("iou_min", "obs_end_offset_ms", "theta", "drop_start_while_open", "text_size_trials",
               "bootstrap_B", "limit_ms", "mixtures", "gt_rules")
MIN_NAME_LEN = 3
MAX_FILE_BYTES = 512 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FROZEN_RE = re.compile(r"^frozen_at:[ \t]*[0-9]{4}-[0-9]{2}-[0-9]{2}")
SHA_LINE_RE = re.compile(r"^sha256:[ \t]*([0-9a-f]{64})[ \t]*$")
RUN_LINE_RE = re.compile(r"^- run_dir:[ \t]*(\S.*?)[ \t]+manifest_sha256:[ \t]*([0-9a-f]{64})[ \t]*$")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
RESULT_FILES = (("l1", "e1_l1.json"), ("l2", "e1_l2.json"), ("detection", "e1_detection.json"),
                ("delivery", "e1_delivery.json"))
COMPARISONS = ("gt_obs", "gt_pred", "pred_obs")
L1_SCOPES = ("under_limit", "all_trials")
L2_SCOPES = ("under_limit", "all_trials")
PRIMARY = {"detection": {"comparison": "gt_obs", "scope": "under_limit"},
           "l1": {"comparison": "gt_obs", "scope": "under_limit"},
           "l2": {"comparison": "gt_obs", "scope": "under_limit"}}
# The number of key_down rows that the input-method key the runner's focus command presses before each block leaves
# in the reference log. Fixed before the freeze from the pilot (pre-registration section 3.1): in the three pilot
# runs, none of the 15 such presses left a key_down row in the reference log.
RUNNER_KANA_PRESSES_PER_BLOCK = 0
# Whether the reference receives the mode keys (ime_on, ime_off) of the runner as key_down rows. True uses the
# version "all_presses" of the delivery report in the abort rule, false the version "excluding_mode_keys". Fixed
# before the freeze from the pilot (section 3.1): in the three pilot runs, neither mode key of any S14 trial left a
# key_down row in the reference log.
REFERENCE_RECEIVES_MODE_KEYS = False
DELIVERY_PRE_MS = 20
DELIVERY_TAIL_MS = 300
# SHA-256 of the plan logs of the two measurement plans of section 2.4 of the pre-registration, by the setting of live
# conversion of the cell ("on", "off") that section 2.4 gives each plan to. An analysis of real data accepts a run that
# is not aborted only when its cell records one of the two settings and its plan log is the plan of that setting.
REGISTERED_PLAN_SHA256 = {"on": "c7a5469de0febbbc2e90259e477900ab0eb68c74b86f420b1e9491799a1210ba",
                          "off": "3759ebf7832ba561820abab504929dbadd7e775a33e22ba0e89b92e4800009a2"}
LIVE_CONVERSION_KEY = "live_conversion"
PRIMARY_NOTE = ("the primary estimates are the comparison and scope named here, per stratum; every other comparison "
                "and scope is secondary. A cell without a prediction (the prediction models the macOS observer only) "
                "has the comparison gt_obs and the scope all_trials only. Values under all_trials depend on this "
                "design's fraction of long trials (see predictions)")
DETECTION_KEYS = ("definition", "config", "inputs_sha256", "input", "excluded", "used", "diagnostics", "metrics")


class CheckFailed(Exception):
    """One or more checks found a violation (exit status 1)."""

    def __init__(self, findings):
        Exception.__init__(self, "%d violation(s)" % len(findings))
        self.findings = findings


class NotFrozen(Exception):
    """The analysis is not frozen (exit status 3)."""


# ---------------------------------------------------------------- helpers

_TOOLS = {}


def _tool(name, *parts):
    """Load a module of the kit by file path (cached)."""
    if name not in _TOOLS:
        spec = importlib.util.spec_from_file_location("kit_" + name, os.path.join(KIT, *parts))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TOOLS[name] = mod
    return _TOOLS[name]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _jl(rows):
    return ("\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n").encode("utf-8")


def _json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _is_int(v):
    return type(v) is int


# The selftest sets a dict here: a file that has not changed is read from disk once. Only a file whose
# modification time is at least CACHE_SETTLE_NS old is cached, so a file that is rewritten within the
# resolution of the clock is never mistaken for the earlier one.
_READ_CACHE = None
CACHE_SETTLE_NS = 300000000


def _read_regular(path, what):
    """Read a regular file of at most MAX_FILE_BYTES; a link or a special file is refused."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise InputError("%s: cannot be opened (%s)" % (what, type(e).__name__))
    if not stat.S_ISREG(st.st_mode):
        raise InputError("%s: not a regular file (a link or a special file is refused)" % what)
    if st.st_size > MAX_FILE_BYTES:
        raise InputError("%s: larger than %d MiB" % (what, MAX_FILE_BYTES // (1024 * 1024)))
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size, st.st_ino)
    if _READ_CACHE is not None and key in _READ_CACHE:
        return _READ_CACHE[key]
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        raise InputError("%s: cannot be read (%s)" % (what, type(e).__name__))
    if _READ_CACHE is not None and time.time_ns() - st.st_mtime_ns > CACHE_SETTLE_NS:
        _READ_CACHE[key] = data
    if len(data) > MAX_FILE_BYTES:
        raise InputError("%s: larger than %d MiB" % (what, MAX_FILE_BYTES // (1024 * 1024)))
    return data


def _git_top():
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=_HERE, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def repo_root():
    """The root of the repository as the run-cell tool defines it: the top level reported by git, or,
    when git cannot tell, the directory three levels above kit/."""
    top = _git_top()
    if top is not None and top.strip():
        return os.path.normpath(top.strip())
    return os.path.dirname(os.path.dirname(os.path.dirname(KIT)))


def _is_under(path, base):
    a = os.path.normcase(os.path.realpath(path))
    b = os.path.normcase(os.path.realpath(base))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _visible_lines(text):
    """The lines of a Markdown text that lie outside HTML comments (single or multiple lines) and code
    fences. Text that follows the end of a comment on the same line is kept but no longer starts the line."""
    out = []
    in_comment = in_fence = False
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if in_comment:
            end = line.find("-->")
            if end < 0:
                continue
            in_comment = False
            line = " " + line[end + 3:]
        elif re.match(r"^ {0,3}(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        while True:
            start = line.find("<!--")
            if start < 0:
                break
            end = line.find("-->", start + 4)
            if end < 0:
                in_comment = True
                line = line[:start]
                break
            line = line[:start] + " " + line[end + 3:]
        out.append(line)
    return out


# ---------------------------------------------------------------- configuration

def parse_config(data, base_dir):
    """Parse the configuration bytes into a dict; gt_rules is resolved against base_dir."""
    obj = match._json_loads(match._decode(data, "config"), "config", True)
    if not isinstance(obj, dict):
        raise InputError("config: the top level must be an object")
    cfg = {"iou_min": 0.5, "obs_end_offset_ms": 0, "theta": 0.5, "drop_start_while_open": False,
           "text_size_trials": 8, "bootstrap_B": 2000, "limit_ms": 20000, "mixtures": None, "gt_rules": None}
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        if k not in CONFIG_KEYS:
            raise InputError("config: unknown key '%s'" % k)
        cfg[k] = v
    if not isinstance(cfg["gt_rules"], str) or not cfg["gt_rules"]:
        raise InputError("config: gt_rules (the path of the rule file) is required")
    cfg["gt_rules"] = os.path.normpath(os.path.join(base_dir, cfg["gt_rules"]))
    match.parse_config(_enc({"iou_min": cfg["iou_min"], "obs_end_offset_ms": cfg["obs_end_offset_ms"],
                             "theta": cfg["theta"], "drop_start_while_open": cfg["drop_start_while_open"]}))
    return cfg


def _enc(d):
    return json.dumps(d, sort_keys=True).encode("utf-8")


def _stage_configs(cfg, seed, label):
    """The configuration bytes handed to each stage (the stages validate their own keys)."""
    l2c = {"text_size_trials": cfg["text_size_trials"], "B": cfg["bootstrap_B"], "seed": seed}
    if cfg["mixtures"] is not None:
        l2c["mixtures"] = cfg["mixtures"]
    return {"match": _enc({"iou_min": cfg["iou_min"], "obs_end_offset_ms": cfg["obs_end_offset_ms"],
                           "theta": cfg["theta"], "drop_start_while_open": cfg["drop_start_while_open"],
                           "cell": label}),
            "l1": _enc({"B": cfg["bootstrap_B"], "seed": seed, "limit_ms": cfg["limit_ms"]}),
            "l2": _enc(l2c)}


def read_seed_file(path):
    """The seed in a seed file: one non-negative integer without spaces, optionally followed by a line end."""
    text = match._decode(_read_regular(path, "seed file"), "seed file")
    if not re.fullmatch(r"[0-9]{1,18}(\r?\n)?", text):
        raise InputError("seed file: it must hold one non-negative integer and nothing else")
    return int(text.strip())


# ---------------------------------------------------------------- freeze marks

def freeze_hash_of(config_bytes, seed_bytes, rules_bytes):
    """The value recorded as sha256 in the pre-registration."""
    text = "".join(_sha(b) + "\n" for b in (config_bytes, seed_bytes, rules_bytes))
    return _sha(text.encode("ascii"))


def freeze_hash(config_path, seed_path):
    cfg_bytes = _read_regular(config_path, "config")
    cfg = parse_config(cfg_bytes, os.path.dirname(os.path.abspath(config_path)))
    return freeze_hash_of(cfg_bytes, _read_regular(seed_path, "seed file"), _read_regular(cfg["gt_rules"], "rules"))


def check_prereg_text(text, want):
    """Raise NotFrozen unless the visible lines of the text hold exactly one well-formed frozen_at line
    and exactly one sha256 line, and that sha256 equals want."""
    lines = _visible_lines(text)
    frozen = [ln for ln in lines if FROZEN_RE.match(ln)]
    shas = [SHA_LINE_RE.match(ln).group(1) for ln in lines if SHA_LINE_RE.match(ln)]
    if len(frozen) != 1:
        raise NotFrozen("the pre-registration needs exactly one 'frozen_at: YYYY-MM-DD' line (found %d)" % len(frozen))
    if len(shas) != 1:
        raise NotFrozen("the pre-registration needs exactly one 'sha256: <64 hex digits>' line (found %d)" % len(shas))
    if shas[0] != want:
        raise NotFrozen("the recorded sha256 does not match the configuration, seed and rule files "
                        "(they changed after the freeze, or the mark is wrong)")


def check_prereg(prereg_path, config_path, seed_path):
    """Raise NotFrozen (exit 3) or InputError (exit 2)."""
    if not os.path.isfile(prereg_path):
        raise NotFrozen("the pre-registration file does not exist; the analysis is not frozen")
    text = match._decode(_read_regular(prereg_path, "pre-registration"), "pre-registration")
    check_prereg_text(text, freeze_hash(config_path, seed_path))


# ---------------------------------------------------------------- run directories

def read_run_files(run_dir, names=DEPOSIT_FILES):
    """{file name: bytes} for the named files of a run directory that exist. run.json must exist."""
    out = {}
    for fname in names:
        p = os.path.join(run_dir, fname)
        if fname != MANIFEST and not os.path.lexists(p):
            continue
        out[fname] = _read_regular(p, fname)
    return out


def _parse_manifest(mbytes):
    m = match._json_loads(match._decode(mbytes, "run.json"), "run.json", True)
    if not isinstance(m, dict) or m.get("format") != RUN_FORMAT:
        raise InputError("run.json: not a %s manifest" % RUN_FORMAT)
    if type(m.get("aborted")) is not bool:
        raise InputError("run.json: aborted must be a boolean")
    cell = m.get("cell")
    if not isinstance(cell, dict):
        raise InputError("run.json: cell must be an object")
    label = cell.get("label")
    if not isinstance(label, str) or not match.CELL_RE.fullmatch(label):
        raise InputError("run.json: the cell has no label of the form %s; add a \"label\" to the cell file "
                         "and record the run again" % match.CELL_RE.pattern)
    return m, label


def parse_run(files, run_dir, verify_all=False):
    """Parse a run from {file name: bytes}. Returns {"dir", "manifest", "manifest_sha", "label", "aborted",
    "synthetic", "files", "sha"}. The logs are checked against the recorded hashes when the run was not
    aborted, or always when verify_all is true. A log recorded with a null hash (an aborted run) need not
    exist; a log whose hash is recorded must."""
    mbytes = files.get(MANIFEST)
    if mbytes is None:
        raise InputError("run.json: cannot be opened")
    m, label = _parse_manifest(mbytes)
    run = {"dir": run_dir, "manifest": m, "manifest_sha": _sha(mbytes), "label": label, "aborted": m["aborted"],
           "synthetic": m.get("synthetic") is True, "files": {}, "sha": {}}
    outs = m.get("outputs")
    if not isinstance(outs, dict):
        raise InputError("run.json: outputs must be an object")
    for name in LOG_NAMES:
        rec = outs.get(name)
        if not isinstance(rec, dict):
            raise InputError("run.json: outputs.%s is missing" % name)
        sha = rec.get("sha256")
        if sha is not None and not (isinstance(sha, str) and SHA_RE.fullmatch(sha)):
            raise InputError("run.json: outputs.%s.sha256 must be null or 64 lowercase hex digits" % name)
        run["sha"][name] = sha
        if m["aborted"] and not verify_all:
            continue
        data = files.get(LOG_FILES[name])
        if data is None:
            if sha is None and m["aborted"]:
                continue
            raise InputError("%s: is missing although run.json records its hash" % LOG_FILES[name])
        if sha is None and m["aborted"] and not data:
            continue
        if sha != _sha(data):
            raise InputError("%s: SHA-256 does not match run.json (the log changed after the run)" % LOG_FILES[name])
        run["files"][name] = data
    return run


def load_run(run_dir):
    """Read a run directory for the analysis (the logs of an aborted run are not read)."""
    mbytes = _read_regular(os.path.join(run_dir, MANIFEST), "run.json")
    m, _label = _parse_manifest(mbytes)
    files = {MANIFEST: mbytes}
    if not m["aborted"]:
        for name in LOG_NAMES:
            files[LOG_FILES[name]] = _read_regular(os.path.join(run_dir, LOG_FILES[name]), LOG_FILES[name])
    return parse_run(files, run_dir)


def read_runs_list(path):
    """[(run directory, manifest SHA-256)] from the lines '- run_dir: PATH  manifest_sha256: HEX' (relative
    paths are relative to the list file); lines inside comments and code fences are not read."""
    text = match._decode(_read_regular(path, "runs list"), "runs list")
    base = os.path.dirname(os.path.abspath(path))
    out = []
    for line in _visible_lines(text):
        if not line.startswith("- run_dir:"):
            continue
        m = RUN_LINE_RE.match(line)
        if not m:
            raise InputError("runs list: a run_dir line has no valid manifest_sha256 (write "
                             "'- run_dir: PATH  manifest_sha256: <64 hex digits>')")
        out.append((os.path.normpath(os.path.join(base, m.group(1))), m.group(2)))
    return out


# ---------------------------------------------------------------- step 0

def _content_free(run, name):
    """Findings of the content-free validator for the gt and obs logs of a run (an absent log passes)."""
    vl = _tool("validate_logs", "formats", "validate_logs.py")
    out = []
    for kind in ("gt", "obs"):
        if kind not in run["files"]:
            continue
        try:
            for ln, code, msg in vl.validate(kind, run["files"][kind]):
                out.append("%s/%s:%d: [%s] %s" % (name, LOG_FILES[kind], ln, code, msg))
        except RecursionError:
            out.append("%s/%s:1: [nesting] the log is nested too deeply to be checked" % (name, LOG_FILES[kind]))
    return out


def _oracle_findings(em, data):
    """Findings of the oracle validator for verified bytes (the validator reads a path, so the bytes that were
    checked against the recorded hash are written to a temporary file)."""
    fd, tmp = tempfile.mkstemp(prefix="oracle_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            return em.validate_oracle(tmp)
        except RecursionError:
            return ["nested too deeply to be checked"]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _capabilities(first_line):
    try:
        caps = json.loads(first_line.decode("utf-8")).get("capabilities")
    except (ValueError, AttributeError, RecursionError):
        caps = None
    return caps if isinstance(caps, dict) else {}


def _has_ime_rows(obs):
    for line in obs.split(b"\n"):
        if b"ime_shadow" not in line:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict) and row.get("type") == "ime_shadow":
            return True
    return False


def check_run(run, rules_bytes):
    """Step 0 for one run. Returns a list of 'file:line: message' findings (empty when clean)."""
    gp = _tool("generate_plan", "gen", "generate_plan.py")
    em = _tool("emit", "emit", "emit.py")
    name = os.path.basename(os.path.normpath(run["dir"]))
    out = _content_free(run, name)
    try:
        for ln, code, msg in gp.validate_plan(run["files"]["plan"]):
            out.append("%s/%s:%d: [%s] %s" % (name, LOG_FILES["plan"], ln, code, msg))
    except RecursionError:
        out.append("%s/%s:1: [nesting] the plan is nested too deeply to be checked" % (name, LOG_FILES["plan"]))
    for msg in _oracle_findings(em, run["files"]["oracle"]):
        out.append("%s/%s: %s" % (name, LOG_FILES["oracle"], msg))
    caps = _capabilities(run["files"]["obs"].split(b"\n", 1)[0])
    declared, gated = set(), set()
    for k in ("unconditional",) + GATED_KEYS:
        if isinstance(caps.get(k), list):
            declared.update(x for x in caps[k] if isinstance(x, str))
    for k in GATED_KEYS:
        if isinstance(caps.get(k), list):
            gated.update(x for x in caps[k] if isinstance(x, str))
    for ch in REQUIRED_CHANNELS:
        if ch not in declared:
            out.append("%s/%s:1: the header does not declare the '%s' channel in meta.capabilities" % (name, LOG_FILES["obs"], ch))
    if "ime_shadow" in gated and not out and not _has_ime_rows(run["files"]["obs"]):
        sess = gt_sessions.assemble(run["files"]["gt"], run["files"]["oracle"], rules_bytes, run["files"]["plan"])
        if sess["definitions"]["all"]:
            out.append("%s/%s:1: cannot be judged: the channel is gated and produced no row although the ground truth "
                       "holds sessions" % (name, LOG_FILES["obs"]))
    return out


# ---------------------------------------------------------------- stages

def _spread(values):
    """{"n", "median", "min", "max"} of a list of numbers (median None for an empty list)."""
    v = sorted(values)
    return {"n": len(v), "median": statistics.median(v) if v else None, "min": v[0] if v else None,
            "max": v[-1] if v else None}


def _composition(plan_bytes, pred):
    """Per stratum (scenario, speed) and scope (under_limit, over_limit): the number of trials and the spread
    of the number of letter keys a trial types and of its think time (think_ms, the trials that have one),
    from the plan alone. Selecting the trials by the cut-off selects them by these quantities, so the
    composition of the scopes differs between speeds."""
    by = pred["trials"]["by_trial"]
    acc = {}
    for line in plan_bytes.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("kind") != "trial" or row.get("trial_id") not in by:
            continue
        scope = "over_limit" if by[row["trial_id"]]["long"] else "under_limit"
        a = acc.setdefault((row.get("scenario"), row.get("speed"), scope), {"letters": [], "think": []})
        a["letters"].append(sum(1 for s in row.get("steps") or ()
                                if s.get("down") is True and isinstance(s.get("key"), str)
                                and len(s["key"]) == 1 and "a" <= s["key"] <= "z"))
        think = row.get("think_ms")
        if isinstance(think, int) and not isinstance(think, bool) and think > 0:
            a["think"].append(think)
    order = sorted(acc, key=lambda k: (l2._scenario_key(k[0]), match.SPEEDS.index(k[1]) if k[1] in match.SPEEDS else 9,
                                       k[2] != "under_limit"))
    return [{"scenario": k[0], "speed": k[1], "scope": k[2], "n_trials": len(acc[k]["letters"]),
             "letter_keys": _spread(acc[k]["letters"]), "think_ms": _spread(acc[k]["think"])} for k in order]


def _prediction_summary(pred, pred_bytes, plan, plan_bytes):
    """What the result files keep of the prediction: its hash, counts, the long trials per stratum and the
    composition of the scopes (_composition)."""
    if pred is None:
        return {"available": False, "reason": "the prediction models the macOS observer only"}
    per = {}
    for tid, d in pred["trials"]["by_trial"].items():
        info = plan["trials"].get(tid)
        key = (info["scenario"], info["speed"]) if info else (None, None)
        c = per.setdefault(key, [0, 0])
        c[0] += 1
        c[1] += 1 if d["long"] else 0
    order = sorted(per, key=lambda k: (l2._scenario_key(k[0]), match.SPEEDS.index(k[1]) if k[1] in match.SPEEDS else 9))
    return {"available": True, "sha256": _sha(pred_bytes), "limit_ms": pred["limit_ms"], "counts": pred["counts"],
            "trials": dict((k, v) for k, v in pred["trials"].items() if k != "by_trial"),
            "long_by_stratum": [{"scenario": k[0], "speed": k[1], "n_trials": per[k][0], "n_long": per[k][1]} for k in order],
            "composition": _composition(plan_bytes, pred)}


def _focus_loss(m_out, oracle, focus_trials, strata):
    """The rows of a match output that are assigned to the trials in focus_trials (the trials in which the
    reference input field lost focus, from the delivery report), and how many of them are false positives: in
    such a trial the reference receives no call, so its rows show a failure of the reference, not of the
    observer. Given as totals and, in by_speed, per speed ({speed: {n_trials, n_rows, n_false_pos}}, only the
    speeds that occur among focus_trials; strata is the plan's {trial_id: {scenario, speed}})."""
    m = m_out["matching"]
    rows = [p["obs"] for p in m["pairs"]] + [o for p in m["split"] for o in p["obs"]] + [p["obs"] for p in m["merge"]]
    fps = list(m["false_pos"])
    off = m_out["config"]["obs_end_offset_ms"]
    tid = lambda rs: [r.get("trial_id") for r in l2.attach_trials(rs, oracle, off)]
    speed = lambda t: strata.get(t, {}).get("speed")
    by = {}
    def cell(sp):
        return by.setdefault(sp, {"n_trials": 0, "n_rows": 0, "n_false_pos": 0})
    for t in focus_trials:
        if speed(t) is not None:
            cell(speed(t))["n_trials"] += 1
    in_rows = in_fp = 0
    for t in tid(rows):
        if t in focus_trials:
            in_rows += 1
            if speed(t) is not None:
                cell(speed(t))["n_rows"] += 1
    for t in tid(fps):
        if t in focus_trials:
            in_fp += 1
            if speed(t) is not None:
                cell(speed(t))["n_rows"] += 1
                cell(speed(t))["n_false_pos"] += 1
    return {"n_trials": len(focus_trials), "n_rows": in_rows + in_fp, "n_false_pos": in_fp,
            "by_speed": dict((sp, by[sp]) for sp in sorted(by, key=match.SPEEDS.index))}


def _detection_view(m_out, focus):
    """The part of a match output that the detection result keeps (the matching itself is not written;
    the lists of long trials and of the trials of the layer none are reduced to their numbers), with the
    rows in the trials that lost focus (focus, from _focus_loss)."""
    d = dict((k, m_out[k]) for k in DETECTION_KEYS)
    s = m_out["scope"]
    d["scope"] = {"name": s["name"], "set_aside": s["set_aside"],
                  "n_long_trials": None if s["long_trials"] is None else len(s["long_trials"]),
                  "n_layer_none_trials": None if s.get("layer_none_trials") is None else len(s["layer_none_trials"])}
    d["focus_loss"] = focus
    return d


def run_stages(run, cfg, rules_bytes, seed, keymap, focus_trials=()):
    """The stages for one run, for the definition of its operating system and for "all". Returns
    {"prediction": summary, "same_sessions_as_all": bool, "definitions": {definition: {"detection",
    "l1", "l2"}}}; each of those maps a comparison to {scope: JSON-ready output} (None when the
    comparison needs a prediction and there is none).

    Comparisons: gt_obs (the ground truth against the observation), gt_pred (the ground truth against
    the observer's specified rows, predicted from what was sent: the validity of the observer's rules)
    and pred_obs (the predicted rows against the observation: whether the observer carries out its
    rules). Scopes (match.py): under_limit, all_trials and over_limit for the detection; under_limit and
    all_trials for level 1 and for level 2, which is run on gt_obs only. Without a prediction (a Windows
    plan) only gt_obs over all_trials is run. focus_trials: the trials in which the reference input
    field lost focus, from the delivery report (it leaves out a planned S15 switch that returns inside
    its trial); every detection view counts their rows, in total and per speed of the plan."""
    f = run["files"]
    plan = match.parse_plan_strata(f["plan"])
    oracle = gt_sessions.parse_oracle(f["oracle"])
    focus_trials = set(focus_trials)
    if plan["os"] not in DEFINITION_OF_OS:
        raise InputError("plan: the operating system has no session definition")
    sc = _stage_configs(cfg, seed, run["label"])
    sessions = gt_sessions.assemble(f["gt"], f["oracle"], rules_bytes, f["plan"])
    sess_bytes = gt_sessions.render(sessions)
    pred = pred_bytes = None
    comps = {"gt_obs": (sess_bytes, f["obs"])}
    if plan["os"] == "macos":
        pred = spec_predict.predict(f["oracle"], f["plan"], keymap, cfg["limit_ms"])
        pred_bytes = spec_predict.render(pred)
        comps["gt_pred"] = (sess_bytes, spec_predict.as_obs_bytes(pred))
        comps["pred_obs"] = (spec_predict.as_sessions_bytes(pred, sess_bytes, f["oracle"]), f["obs"])
    scopes = match.SCOPES if pred is not None else ("all_trials",)
    def_os = DEFINITION_OF_OS[plan["os"]]
    out = {"prediction": _prediction_summary(pred, pred_bytes, plan, f["plan"]),
           "same_sessions_as_all": sessions["definitions"][def_os] == sessions["definitions"]["all"],
           "definitions": {}}
    for definition in (def_os, "all"):
        det, l1o, l2o = {}, {}, {}
        for comp in COMPARISONS:
            if comp not in comps:
                det[comp] = l1o[comp] = None
                continue
            sb, ob = comps[comp]
            det[comp], l1o[comp] = {}, {}
            for scope in scopes:
                m_out = match.evaluate(sb, definition, ob, f["plan"], f["oracle"], sc["match"], pred_bytes, scope)
                det[comp][scope] = _detection_view(m_out, _focus_loss(m_out, oracle, focus_trials, plan["trials"]))
                if scope in L1_SCOPES:
                    m_bytes = match.render(m_out)
                    l1o[comp][scope] = l1.evaluate(m_bytes, sb, f["plan"], f["oracle"], sc["l1"])
                    if comp == "gt_obs" and scope in L2_SCOPES:
                        l2o.setdefault(comp, {})[scope] = l2.evaluate(m_bytes, sb, ob, f["plan"], f["oracle"], sc["l2"])
        out["definitions"][definition] = {"detection": det, "l1": l1o, "l2": l2o}
    return out


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _write_results(out_dir, blobs):
    """Write the result files to a temporary directory and move them into out_dir. When anything fails, the
    result files of an earlier run are removed as well, so that no stale result is left next to a failure."""
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".pipeline_tmp_", dir=out_dir)
    try:
        for fname, data in blobs:
            _write(os.path.join(tmp, fname), data)
        for fname, _data in blobs:
            os.replace(os.path.join(tmp, fname), os.path.join(out_dir, fname))
    except BaseException:
        for fname, _data in blobs:
            try:
                os.remove(os.path.join(out_dir, fname))
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reference_abort_segments(dlv):
    """The block_ids (None for the segment before the first block) of the segments of the reference log in which
    the key_down rows that no sent press explains exceed the allowance (see the abort rule in the module
    description), counted in the version that REFERENCE_RECEIVES_MODE_KEYS names. The segment of the last block
    (the last segment, when it belongs to a block) has no allowance for a key of the runner. Empty when no
    segment is over."""
    version = "all_presses" if REFERENCE_RECEIVES_MODE_KEYS else "excluding_mode_keys"
    segs = dlv["reference"]["segments"][version]["by_segment"]
    over = []
    for i, seg in enumerate(segs):
        last_block = i == len(segs) - 1 and seg["block_id"] is not None
        allowance = seg["n_marker_presses"] + (0 if last_block else RUNNER_KANA_PRESSES_PER_BLOCK)
        if seg["n_unexplained"] > allowance:
            over.append(seg["block_id"])
    return over


def reference_abort_reasons(dlv):
    """The reasons, as strings that hold no value of a log, why section 3.4 of the pre-registration counts the run
    as aborted; empty when it does not (see the abort rule in the module description)."""
    out = []
    for bid in reference_abort_segments(dlv):
        out.append("the reference log holds key presses that the measured trials and the procedure do not explain, %s"
                   % ("before the first block" if bid is None else "in the segment of block %s" % bid))
    if dlv["reference_focus_loss"]["n_loss_spans_with_unexplained_presses"] > 0:
        out.append("the observation log holds key presses that no key sent by the executor explains within a loss of "
                   "focus of the reference input field")
    return out


def _plan_matches_cell(run):
    """True when the cell of the run records live conversion "on" or "off" and the SHA-256 of its plan log is the
    registered plan of that setting (section 2.4)."""
    settings = run["manifest"]["cell"].get("ime_settings")
    setting = settings.get(LIVE_CONVERSION_KEY) if isinstance(settings, dict) else None
    return (isinstance(setting, str) and setting in REGISTERED_PLAN_SHA256
            and run["sha"]["plan"] == REGISTERED_PLAN_SHA256[setting])


def analyse(run_dirs, config_path, seed, out_dir, prereg=None, seed_file=None, manifest_shas=None):
    """The whole pipeline. Writes the three result files; raises CheckFailed / NotFrozen / InputError."""
    if not _is_int(seed):
        raise InputError("seed must be an integer")
    cfg_bytes = _read_regular(config_path, "config")
    cfg = parse_config(cfg_bytes, os.path.dirname(os.path.abspath(config_path)))
    rules_bytes = _read_regular(cfg["gt_rules"], "rules")
    if not run_dirs:
        raise InputError("no run directory was given")
    runs = [load_run(d) for d in run_dirs]
    if manifest_shas is not None:
        for r, want in zip(runs, manifest_shas):
            if r["manifest_sha"] != want:
                raise InputError("run.json of a listed run does not match the manifest_sha256 in the runs list")
    n_syn = sum(1 for r in runs if r["synthetic"])
    if 0 < n_syn < len(runs):
        raise InputError("synthetic and real runs cannot be analysed together")
    synthetic = n_syn == len(runs)
    if synthetic:
        if _is_under(out_dir, EXPERIMENT_RESULTS):
            raise InputError("synthetic runs must not be written to the results directory of the experiment")
    else:
        if prereg is None or seed_file is None:
            raise NotFrozen("real data needs --prereg and --seed-file; the analysis is not frozen")
        if not os.path.isfile(prereg):
            raise NotFrozen("the pre-registration file does not exist; the analysis is not frozen")
        if read_seed_file(seed_file) != seed:
            raise InputError("--seed differs from the seed file")
        want = freeze_hash_of(cfg_bytes, _read_regular(seed_file, "seed file"), rules_bytes)
        check_prereg_text(match._decode(_read_regular(prereg, "pre-registration"), "pre-registration"), want)
    used = sorted((r for r in runs if not r["aborted"]), key=lambda r: r["label"])
    if not synthetic:
        for r in used:
            if not _plan_matches_cell(r):
                raise InputError("run %s: under section 3.4 of the pre-registration the run counts as aborted and is "
                                 "not analysed (its plan is not the plan that section 2.4 gives its cell)" % r["label"])
    labels = [r["label"] for r in used]
    if len(set(labels)) != len(labels):
        raise InputError("two runs that were not aborted share a cell label (give each cell a distinct label)")
    findings = []
    for r in used:
        findings += check_run(r, rules_bytes)
    if findings:
        raise CheckFailed(findings)
    listing = [{"cell": r["label"], "aborted": r["aborted"], "logs_sha256": r["sha"], "manifest_sha256": r["manifest_sha"]}
               for r in sorted(runs, key=lambda r: (r["label"], r["aborted"], r["manifest_sha"]))]
    keymap_bytes = _read_regular(spec_predict.DEFAULT_KEYMAP, "keymap")
    keymap = spec_predict.load_keymap(spec_predict.DEFAULT_KEYMAP)
    head = {"seed": seed, "synthetic": synthetic, "config_sha256": _sha(cfg_bytes), "rules_sha256": _sha(rules_bytes),
            "keymap_sha256": _sha(keymap_bytes),
            "runs": {"n": len(runs), "n_aborted": sum(1 for r in runs if r["aborted"]),
                     "n_used": len(used), "list": listing}}
    res = dict((key, dict(head, primary=dict(PRIMARY[key], note=PRIMARY_NOTE), cells={})) for key in PRIMARY)
    res["detection"]["predictions"] = {}
    res["detection"]["same_sessions_as_all"] = {}
    res["delivery"] = dict(head, abort_rule={"runner_kana_presses_per_block": RUNNER_KANA_PRESSES_PER_BLOCK,
                                             "reference_receives_mode_keys": REFERENCE_RECEIVES_MODE_KEYS,
                                             "focus_loss_with_unexplained_presses_aborts": True,
                                             "focus_margin_ms": delivery.FOCUS_MARGIN_MS,
                                             "obs_match_before_ms": delivery.OBS_MATCH_BEFORE_MS,
                                             "obs_match_after_ms": delivery.OBS_MATCH_AFTER_MS,
                                             "registered_plan_sha256": dict(REGISTERED_PLAN_SHA256),
                                             "pre_ms": DELIVERY_PRE_MS, "tail_ms": DELIVERY_TAIL_MS}, cells={})
    for r in used:
        f = r["files"]
        try:
            dlv = delivery.evaluate(f["plan"], f["oracle"], f["obs"], f["gt"], DELIVERY_PRE_MS, DELIVERY_TAIL_MS)
        except delivery.InputError as e:
            raise InputError("delivery: %s" % e)
        reasons = reference_abort_reasons(dlv)
        if reasons:
            raise InputError("run %s: under section 3.4 of the pre-registration the run counts as aborted and is not "
                             "analysed (%s)" % (r["label"], "; ".join(reasons)))
        res["delivery"]["cells"][r["label"]] = dlv
        st = run_stages(r, cfg, rules_bytes, seed, keymap, dlv["reference_focus_loss"]["trials_with_focus_loss"])
        res["detection"]["predictions"][r["label"]] = st["prediction"]
        res["detection"]["same_sessions_as_all"][r["label"]] = st["same_sessions_as_all"]
        for key in PRIMARY:
            res[key]["cells"][r["label"]] = dict((definition, d[key]) for definition, d in st["definitions"].items())
    _write_results(out_dir, [(fname, match.render(res[key])) for key, fname in RESULT_FILES])
    return res


# ---------------------------------------------------------------- deposit check

_NAME_EDGE = r"[\w~>.\-/\\]"
ABSOLUTE_PATH_RES = (
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]",                               # a drive letter
    r"(?<!%s)\\{2,4}[\w$.-]" % _NAME_EDGE,                            # a UNC path (plain or as a JSON string)
    r"(?<![\w~>.:\-/\\])//[\w$.-]+/",                                 # a UNC path with slashes
    r"(?<!%s)/(?:Users|home|Volumes|root|mnt)/" % _NAME_EDGE,         # a home-like root
    r"(?<!%s)/[A-Za-z](?=/)" % _NAME_EDGE,                            # an MSYS drive path such as /c/
    r"(?<![A-Za-z0-9_])file:\\?/",                                    # a file: URI (plain, or with the slash escaped as a JSON string does)
)
_ESC_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)
_SIMPLE_ESC = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _unescape(line):
    """The line with the escapes of a JSON string decoded (so that C:\\\\Users and \\u005c are seen as they are)."""
    def rep(m):
        g = m.group(1)
        if g[0] == "u" and len(g) == 5:
            return chr(int(g[1:], 16))
        return _SIMPLE_ESC.get(g, g)
    return _ESC_RE.sub(rep, line)


def _forms(path):
    """The spellings of a directory path to look for: as given, with '/', with the backslash, with the
    backslash doubled (how a JSON string spells it), and the MSYS spelling of a drive path (/c/Users/name)."""
    p = path.rstrip("/\\")
    back = p.replace("/", "\\")
    forms = set([p, p.replace("\\", "/"), back, back.replace("\\", "\\\\")])
    if re.match(r"^[A-Za-z]:[\\/]", p):
        forms.add("/" + p[0].lower() + p[2:].replace("\\", "/"))
    return sorted(forms, key=len, reverse=True)


def build_patterns(home=None, user=None, repo=None, also_users=(), also_homes=()):
    """[(kind, compiled pattern)] for the deposit check."""
    home = os.path.expanduser("~") if home is None else home
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:
            user = None
    repo = repo_root() if repo is None else repo
    flags = re.IGNORECASE if os.name == "nt" else 0
    pats = [("absolute path", re.compile(rx, re.IGNORECASE)) for rx in ABSOLUTE_PATH_RES]
    paths = [("repository path", repo), ("home directory", home)] + [("home directory", h) for h in also_homes]
    for kind, path in paths:
        if path and len(path.rstrip("/\\")) >= 2 and path not in ("~", "/", "\\"):
            pats.append((kind, re.compile("|".join(re.escape(f) for f in _forms(path)), flags)))
    for name in also_users:
        if len(name) < MIN_NAME_LEN:
            raise InputError("--also-user: a name of fewer than %d characters cannot be searched for reliably" % MIN_NAME_LEN)
    names = ([user] if user and len(user) >= MIN_NAME_LEN else []) + list(also_users)
    for name in names:
        pats.append(("login name", re.compile(r"(?<![\w.-])%s(?![\w.-])" % re.escape(name), flags)))
    return pats


def scan_text(fname, text, pats):
    """[(file, line number, kind)] for the text of one file."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        dec = _unescape(line) if "\\" in line else line
        seen = set()
        for kind, pat in pats:
            if kind not in seen and (pat.search(line) or (dec is not line and pat.search(dec))):
                seen.add(kind)
                out.append((fname, n, kind))
    return out


def scan_files(files, pats):
    out = []
    for fname in DEPOSIT_FILES:
        if fname in files:
            out += scan_text(fname, match._decode(files[fname], fname), pats)
    return out


def _abort_rule_findings(run, name):
    """Findings of the abort rule (section 3.4) for the reference log of a run, aborted by the runner or not. A run
    with neither a reference log nor an observation log has nothing to check; an observation log without a
    reference log cannot be checked (the loss of focus that typing elsewhere causes is seen only in the reference
    log). No finding holds a value of a log."""
    files = run["files"]
    if not files.get("gt"):
        if files.get("obs"):
            return ["%s/%s: section 3.4 cannot be checked (an observation log without a reference log)"
                    % (name, LOG_FILES["obs"])]
        return []
    where = "%s/%s" % (name, LOG_FILES["gt"])
    if "plan" not in files or "oracle" not in files:
        return ["%s: section 3.4 cannot be checked (the plan or the oracle log is missing)" % where]
    try:
        dlv = delivery.evaluate(files["plan"], files["oracle"], files.get("obs", b""), files["gt"],
                                DELIVERY_PRE_MS, DELIVERY_TAIL_MS)
    except delivery.InputError as e:
        return ["%s: section 3.4 cannot be checked (delivery: %s)" % (where, e)]
    return ["%s: not safe to deposit under section 3.4: %s" % (where, why) for why in reference_abort_reasons(dlv)]


def deposit_check_files(files, name, pats):
    """Findings ('file:line: message') for the five files of a run given as {file name: bytes}. Raises InputError
    when a hash does not match or a log whose hash is recorded is missing."""
    run = parse_run(files, name, verify_all=True)
    out = ["%s/%s:%d: contains %s" % (name, f, n, kind) for f, n, kind in scan_files(files, pats)]
    out += _content_free(run, name)
    out += _abort_rule_findings(run, name)
    return out


def check_deposit(run_dirs, pats):
    """Exit status of --check-deposit (prints the findings to standard error)."""
    bad = 0
    for d in run_dirs:
        name = os.path.basename(os.path.normpath(d))
        for line in deposit_check_files(read_run_files(d), name, pats):
            print(line, file=sys.stderr)
            bad += 1
    if bad:
        print("pipeline: %d finding(s); do not deposit these run directories" % bad, file=sys.stderr)
        return 1
    print("pipeline: deposit check passed for %d run director%s" % (len(run_dirs), "y" if len(run_dirs) == 1 else "ies"))
    return 0


def build_archive(files):
    """The bytes of a ZIP archive that holds the deposit files in a fixed order, with fixed times and attributes and
    no compression."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for fname in DEPOSIT_FILES:
            if fname in files:
                zi = zipfile.ZipInfo(fname, date_time=ZIP_TIME)
                zi.compress_type = zipfile.ZIP_STORED
                zi.create_system = 3
                zi.external_attr = 0o100644 << 16
                zf.writestr(zi, files[fname])
    return buf.getvalue()


def pack(run_dir, out_path, pats):
    """Exit status of --pack."""
    if os.path.lexists(out_path):
        raise InputError("the output file already exists and is not overwritten")
    name = os.path.basename(os.path.normpath(run_dir))
    files = read_run_files(run_dir)
    findings = deposit_check_files(files, name, pats)
    if not findings:
        data = build_archive(files)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            back = dict((n, zf.read(n)) for n in zf.namelist())
        if back != files:
            raise InputError("the archive does not hold the files that were read")
        findings = deposit_check_files(back, name + ".zip", pats)
    if findings:
        for line in findings:
            print(line, file=sys.stderr)
        print("pipeline: %d finding(s); no archive was written" % len(findings), file=sys.stderr)
        return 1
    try:
        with open(out_path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise InputError("the output file already exists and is not overwritten")
    print("pipeline: archive sha256 %s (%d files, %d bytes)" % (_sha(data), len(files), len(data)))
    return 0


# ---------------------------------------------------------------- synthetic runs

WALL0 = 1700000000000
MONO0 = 50000000
SYN_TRIALS_PER_BLOCK = 32
SYN_SPECS = (("synthetic-a", ("slow", "mid")), ("synthetic-b", ("mid", "fast")))
INJECT_XOR = 0x5eed


SYN_KINDS = {3: "closer", 7: "long", 11: "backspace"}   # k % 16 -> a trial kind other than "plain"
SYN_LONG_MS = 21000


def _syn_presses(kind, lo, length, cancel, arrow):
    """The presses of one synthetic trial as (time, key): a letter just before the composition opens,
    and the key that closes it just before it closes. A "closer" trial also types a long-vowel mark and a
    second letter (the observer's rules split its session), a "long" trial a digit after 20 s (its session
    is cut off and the digit opens a fragment), and a "backspace" trial cancels with Backspace (the
    observer's rules keep its session open into the next trial)."""
    presses = [(lo - 2, "a")]
    if kind == "closer":
        presses += [(lo + 200, "minus"), (lo + 400, "a")]
    if arrow:
        presses.append((lo + 300, "left"))
    if kind == "long":
        presses += [(lo + 20500, "1"), (lo + length - 2, "enter")]
    elif kind == "backspace":
        presses.append((lo + length - 2, "backspace"))
    else:
        presses.append((lo + length - 2, "esc" if cancel else "enter"))
    return sorted(presses)


def make_synthetic_run(run_dir, seed, label, speeds):
    """A complete synthetic run directory in the form the run-cell tool writes: two blocks of
    SYN_TRIALS_PER_BLOCK trials, one composition per trial (a quarter of them cancelled), a few arrow
    presses, and an observation log made by inject.py from the assembled ground truth. The oracle records
    the presses each composition needs (_syn_presses), so that the observer's specified rows can be
    predicted from it; in every 16 trials one types a long-vowel mark, one lasts SYN_LONG_MS (beyond the
    observer's 20-second cut-off) and one is cancelled with Backspace. The observation is made from the
    sessions shorter than 20 s (the observer reports no longer session); it does not depend on the
    prediction. The ground truth is generated from the seed and the observation errors from the seed xor
    INJECT_XOR."""
    rng = random.Random(seed)
    os.mkdir(run_dir)
    plan_rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": seed, "generator_version": "0.1",
                  "config_sha256": "0" * 64, "recipes_sha256": "0" * 64, "corpus_sha256": "0" * 64}]
    trials = []                                   # (trial_id, block, begin_rel, end_rel, lo_rel, length, cancel, arrow, events)
    idx, t = 0, 0
    for b, speed in enumerate(speeds):
        plan_rows.append({"kind": "block", "block_id": b, "speed": speed, "reset_ime": False, "marker": [],
                          "marker_gap_ms": 0})
        t += 5000
        for k in range(SYN_TRIALS_PER_BLOCK):
            idx += 1
            tid = "t%05d" % idx
            kind = SYN_KINDS.get(k % 16, "plain")
            length = 800 + int(rng.random() * 1500)
            cancel = rng.random() < 0.25
            if kind == "long":
                length, cancel = SYN_LONG_MS, False
            elif kind == "backspace":
                cancel = True
            arrow = (idx % 5 == 0)
            begin = t
            end = begin + 500 + length + 1000
            # the presses, as (time, release?, key) in time order: the plan's steps and the oracle's sent rows
            events = sorted((at + (40 if up else 0), up, key)
                            for at, key in _syn_presses(kind, begin + 500, length, cancel, arrow) for up in (False, True))
            trials.append((tid, b, begin, end, begin + 500, length, cancel, arrow, events))
            t = end + 3000
            steps = [{"key": key, "at_ms": at - begin, "down": not up} for at, up, key in events]
            plan_rows.append({"kind": "trial", "trial_id": tid, "block_id": b, "scenario": "S%d" % (k % 3 + 1),
                              "speed": speed, "gap_before_ms": 1000, "think_ms": 500, "phrase_id": "kana-%d" % idx,
                              "reading_len": 4, "steps": steps,
                              "intent": {"commit": "enter", "cand_index": None, "nav_count": 1 if arrow else 0,
                                         "abandon": cancel, "predictive": False, "ime_toggle_mid": False,
                                         "focus_switch_mid": False, "expect_session": True}})
    plan_bytes = _jl(plan_rows)

    def orow(kind, t, **kw):
        return dict(kw, kind=kind, t_wall_ms=WALL0 + t, t_mono_ns=(MONO0 + t) * 1000000)

    orows = [{"kind": "header", "format": "oracle/1", "plan_sha256": _sha(plan_bytes), "os": "macos",
              "emitter_version": "0.1", "backend": "dryrun"}]
    grows = [{"kind": "header", "format": "gt/1", "src": "mac_nstic", "app_version": "0.1.0", "os": "macos"},
             orow("ev", 100, ev="focus_in")]     # the reference field has the focus before the first block, as the runner brings it
    for b in range(len(speeds)):
        mine = [x for x in trials if x[1] == b]
        orows.append(orow("block_begin", mine[0][2] - 100, block_id=b))
        for tid, _b, begin, end, lo, length, cancel, arrow, events in mine:
            orows.append(orow("trial_begin", begin, trial_id=tid))
            for step, (at, up, key) in enumerate(events):
                orows.append(orow("sent", at, trial_id=tid, step=step, key=key, down=not up))
            orows.append(orow("trial_end", end, trial_id=tid))
            grows.append(orow("ev", lo, ev="set_marked", len=3, sel_loc=3, sel_len=0))
            if cancel:
                grows.append(orow("ev", lo + length, ev="set_marked", len=0, sel_loc=0, sel_len=0))
            else:
                grows.append(orow("ev", lo + length, ev="insert_text", len=3))
        orows.append(orow("block_end", mine[-1][3] + 100, block_id=b))
    oracle_bytes, gt_bytes = _jl(orows), _jl(grows)
    # the keys sent: the reference field sees each of them as a key_down row 3 ms later, the observation as a key row
    # 5 ms later (a run without a person has no other row, and the rows of the delivery report are all explained).
    # The key_down rows are added after the sessions are assembled, so that they are not part of the ground truth rules.
    sent = sorted(WALL0 + at for x in trials for at, up, _key in x[8] if not up)
    gt_down = [orow("ev", s - WALL0 + 3, ev="key_down", vk=0) for s in sent]

    rules = _read_regular(os.path.join(_HERE, "gt_rules.example.json"), "rules")
    sess = gt_sessions.assemble(gt_bytes, oracle_bytes, rules, plan_bytes)["definitions"]["macos_def"]
    reported = [s for s in sess if s["end_wall_ms"] is not None and s["end_wall_ms"] - s["start_wall_ms"] < spec_predict.LIMIT_MS]
    obs_ime, _truth = inject.inject(reported, {"miss": 0.05, "split": 0.04, "merge": 0.04, "false_pos": 0.05},
                                    {"bias_ms": 20, "sd_ms": 10, "end_lag_ms_max": 30}, seed ^ INJECT_XOR)
    obs_rows = [{"type": "meta", "format": "obs/1", "os": "macos", "logger_version": "0.1.0",
                 "manifest_sha256": "0" * 64,
                 "capabilities": {"unconditional": ["key"], "threshold_gated": [], "config_gated": ["ime_shadow"]}}]
    keys = [{"type": "key", "t": s + 5, "category": "alphabetic", "press": True} for s in sent]
    obs_rows += sorted(keys + obs_ime, key=lambda r: (r["t"], r["type"]))
    obs_bytes = _jl(obs_rows)

    gt_bytes = _jl(grows[:1] + sorted(grows[1:] + gt_down, key=lambda r: r["t_wall_ms"]))
    logs = {"plan": plan_bytes, "oracle": oracle_bytes, "gt": gt_bytes, "obs": obs_bytes}
    outputs = {}
    for name in LOG_NAMES:
        _write_new(os.path.join(run_dir, LOG_FILES[name]), logs[name])
        outputs[name] = {"file": LOG_FILES[name], "sha256": _sha(logs[name]), "lines": logs[name].count(b"\n")}
    cell = {"os": "macos", "label": label, "ime": "synthetic", "ime_settings": {}, "refinput_cmd": ["synthetic-input", "{out}"],
            "logger_cmd": ["synthetic-logger", "{out}"], "reset_cmd": [], "focus_cmd": [], "ready_wait_ms": 0,
            "backend": "dryrun", "clock_sync_disabled": False, "auto_update_disabled": False}
    manifest = {"format": RUN_FORMAT, "runner_version": "synthetic", "emitter_version": "synthetic", "synthetic": True,
                "cell": cell, "plan_sha256": _sha(plan_bytes),
                "git_commit": None, "git_dirty": None, "host": {}, "started": None, "ended": None,
                "outputs": outputs, "children": {}, "resets": [], "focus": [], "aborted": False, "abort_reason": None}
    _write_new(os.path.join(run_dir, MANIFEST), _json_bytes(manifest))


def _write_new(path, data):
    with open(path, "xb") as fh:
        fh.write(data)


def make_synthetic(out_dir, seed):
    """Two synthetic run directories (run-a, run-b) under out_dir; returns their paths. An existing directory
    of the same name is never overwritten, and nothing is written under the results directory of the experiment."""
    if _is_under(out_dir, EXPERIMENT_RESULTS):
        raise InputError("synthetic runs must not be written under the results directory of the experiment")
    paths = [os.path.join(out_dir, "run-" + label[-1]) for label, _s in SYN_SPECS]
    for p in paths:
        if os.path.lexists(p):
            raise InputError("a run directory of that name already exists and is not overwritten")
    os.makedirs(out_dir, exist_ok=True)
    for i, ((label, speeds), p) in enumerate(zip(SYN_SPECS, paths)):
        make_synthetic_run(p, seed * 1000 + i, label, speeds)
    return paths


# ---------------------------------------------------------------- CLI

def _resolve_seed(args, ap):
    seed = args.seed
    if args.seed_file:
        from_file = read_seed_file(args.seed_file)
        if seed is not None and seed != from_file:
            ap.error("--seed differs from the seed file")
        seed = from_file
    return seed


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="From run directories to the level-1, level-2 and detection results")
    ap.add_argument("--runs", nargs="+")
    ap.add_argument("--runs-from")
    ap.add_argument("--config")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--seed-file")
    ap.add_argument("--out")
    ap.add_argument("--prereg", help="the pre-registration file (required for real data)")
    ap.add_argument("--freeze-hash", action="store_true", dest="freeze_hash",
                    help="print the value that the pre-registration records as sha256")
    ap.add_argument("--check-prereg", dest="check_prereg", metavar="PATH", help="check the freeze marks of a pre-registration")
    ap.add_argument("--synthetic")
    ap.add_argument("--check-deposit", nargs="+", dest="deposit")
    ap.add_argument("--pack", metavar="RUN_DIR")
    ap.add_argument("--also-user", action="append", default=[], dest="also_users", metavar="NAME")
    ap.add_argument("--also-home", action="append", default=[], dest="also_homes", metavar="PATH")
    ap.add_argument("--selftest", action="store_true",
                    help="fast default: a representative variant of every case (about 20 seconds)")
    ap.add_argument("--full", action="store_true",
                    help="with --selftest: run every variant of every case (about a minute)")
    args = ap.parse_args(argv)
    if args.full and not args.selftest:
        ap.error("--full is used together with --selftest")
    if args.selftest:
        global FULL
        FULL = args.full
        return selftest()
    res = None
    try:
        if args.freeze_hash or args.check_prereg:
            if not (args.config and args.seed_file):
                ap.error("--freeze-hash and --check-prereg need --config and --seed-file")
            if args.freeze_hash:
                print(freeze_hash(args.config, args.seed_file))
            else:
                check_prereg(args.check_prereg, args.config, args.seed_file)
                print("pipeline: the freeze marks are well formed and match the configuration, seed and rule files")
            return 0
        if args.deposit:
            return check_deposit(args.deposit, build_patterns(also_users=args.also_users, also_homes=args.also_homes))
        if args.pack:
            if not args.out:
                ap.error("--pack needs --out")
            return pack(args.pack, args.out, build_patterns(also_users=args.also_users, also_homes=args.also_homes))
        if args.synthetic:
            seed = _resolve_seed(args, ap)
            if seed is None:
                ap.error("--synthetic needs --seed")
            for p in make_synthetic(args.synthetic, seed):
                print("pipeline: wrote a synthetic run directory (%s)" % os.path.basename(p))
            return 0
        seed = _resolve_seed(args, ap)
        if not (args.config and args.out and seed is not None and bool(args.runs) != bool(args.runs_from)):
            ap.error("--config, --seed (or --seed-file), --out and exactly one of --runs / --runs-from are required")
        shas = None
        if args.runs:
            dirs = args.runs
        else:
            listed = read_runs_list(args.runs_from)
            dirs, shas = [d for d, _s in listed], [s for _d, s in listed]
        res = analyse(dirs, args.config, seed, args.out, args.prereg, args.seed_file, shas)
    except CheckFailed as e:
        for line in e.findings[:50]:
            print("pipeline: " + line, file=sys.stderr)
        if len(e.findings) > 50:
            print("pipeline: ...and %d more" % (len(e.findings) - 50), file=sys.stderr)
        print("pipeline: step 0 found %d violation(s); nothing was analysed" % len(e.findings), file=sys.stderr)
        return 1
    except NotFrozen as e:
        print("pipeline: not frozen: %s" % e, file=sys.stderr)
        return 3
    except (InputError, ValueError) as e:
        print("pipeline: %s" % e, file=sys.stderr)
        return 2
    except RecursionError:
        print("pipeline: an input is nested too deeply", file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        print("pipeline: an input has an unexpected shape (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    except OSError as e:
        print("pipeline: could not read or write a file (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    except Exception as e:
        print("pipeline: unexpected internal error (%s); this is not a problem with the input's form" % type(e).__name__,
              file=sys.stderr)
        return 2
    r = res["l1"]["runs"]
    print("pipeline: %d run(s), %d aborted, %d analysed; results written" % (r["n"], r["n_aborted"], r["n_used"]))
    print("pipeline: cells used: %s" % ", ".join(sorted(res["l1"]["cells"])))
    return 0


# ---------------------------------------------------------------- selftest

FULL = False   # --selftest --full runs every variant of every case; the default runs a representative subset

def _quiet(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as e:  # argparse reports a usage error this way
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def _config_file(d, extra=None):
    cfg = {"bootstrap_B": 100, "gt_rules": os.path.join(_HERE, "gt_rules.example.json")}
    cfg.update(extra or {})
    path = os.path.join(d, "config.json")
    _write(path, json.dumps(cfg).encode("utf-8"))
    return path


def _seed_file(d, text="7\n"):
    path = os.path.join(d, "SEED")
    _write(path, text.encode("utf-8"))
    return path


def _read_results(d):
    return dict((n, open(os.path.join(d, n), "rb").read()) for _k, n in RESULT_FILES)


def _rewrite_log(run_dir, name, data):
    """Replace a log and record its new hash in the manifest (so only step 0 can object)."""
    _write(os.path.join(run_dir, LOG_FILES[name]), data)
    mp = os.path.join(run_dir, MANIFEST)
    m = json.loads(open(mp, "rb").read().decode("utf-8"))
    m["outputs"][name]["sha256"] = _sha(data)
    _write(mp, _json_bytes(m))


def _set_manifest(run_dir, **kw):
    mp = os.path.join(run_dir, MANIFEST)
    m = json.loads(open(mp, "rb").read().decode("utf-8"))
    m.update(kw)
    for k in [k for k, v in kw.items() if v is _DROP]:
        del m[k]
    _write(mp, _json_bytes(m))


_DROP = object()


def _real_runs(d, name, seed=2):
    """Synthetic runs without the synthetic mark (they stand in for real runs); the cell records live conversion on."""
    runs = make_synthetic(os.path.join(d, name), seed)
    for r in runs:
        m = json.loads(open(os.path.join(r, MANIFEST), "rb").read().decode("utf-8"))
        _set_manifest(r, synthetic=_DROP, cell=dict(m["cell"], ime_settings={LIVE_CONVERSION_KEY: "on"}))
    return runs


def _case_synthetic_form(d):
    f = []
    runs = make_synthetic(os.path.join(d, "form"), 5)
    cell_tool = _tool("run_cell", "run", "run_cell.py")
    for p in runs:
        if sorted(os.listdir(p)) != sorted(DEPOSIT_FILES):
            f.append("a synthetic run directory does not hold exactly run.json and the four logs: %r" % sorted(os.listdir(p)))
        r = load_run(p)
        if r["aborted"] or not r["synthetic"] or r["label"] not in ("synthetic-a", "synthetic-b"):
            f.append("the manifest of a synthetic run is wrong: %r" % (r["label"],))
        try:
            cell_tool.check_cell(r["manifest"]["cell"])
        except Exception as e:
            f.append("the cell of a synthetic manifest is refused by the run-cell tool: %s" % type(e).__name__)
        bad = check_run(r, match._read(os.path.join(_HERE, "gt_rules.example.json"), "rules"))
        if bad:
            f.append("a synthetic run fails step 0: %r" % (bad[:2],))
        if match.parse_plan_strata(r["files"]["plan"])["trials"].__len__() != 2 * SYN_TRIALS_PER_BLOCK:
            f.append("a synthetic run does not have two blocks of %d trials" % SYN_TRIALS_PER_BLOCK)
    again = make_synthetic(os.path.join(d, "form2"), 5)
    if any(open(os.path.join(a, n), "rb").read() != open(os.path.join(b, n), "rb").read()
           for a, b in zip(runs, again) for n in DEPOSIT_FILES):
        f.append("the same seed gives different synthetic runs")
    other = make_synthetic(os.path.join(d, "form3"), 6)
    if open(os.path.join(other[0], "obs.jsonl"), "rb").read() == open(os.path.join(runs[0], "obs.jsonl"), "rb").read():
        f.append("the seed has no effect on the synthetic runs")
    before = open(os.path.join(runs[0], MANIFEST), "rb").read()
    rc, _o, _e = _quiet(["--synthetic", os.path.join(d, "form"), "--seed", "9"])
    if rc != 2 or open(os.path.join(runs[0], MANIFEST), "rb").read() != before:
        f.append("an existing synthetic run directory was overwritten or not refused (rc %s)" % (rc,))
    target = os.path.join(EXPERIMENT_RESULTS, "selftest-refused")
    if _quiet(["--synthetic", target, "--seed", "1"])[0] != 2 or os.path.exists(target):
        f.append("a synthetic output under the results directory of the experiment was not refused")
    return f


def _case_reproduces(d):
    f = []
    runs = make_synthetic(os.path.join(d, "rep"), 3)
    cfg = _config_file(d)
    outs = []
    for i in (1, 2):
        o = os.path.join(d, "out%d" % i)
        rc, _o, err = _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "7", "--out", o])
        if rc != 0:
            return ["the pipeline did not exit 0 (%d): %s" % (rc, err[-300:])]
        outs.append(_read_results(o))
    if outs[0] != outs[1]:
        f.append("two runs on the same input give different bytes")
    for name, b in outs[0].items():
        if d.encode("utf-8") in b or b"\r" in b or b.startswith(b"\xef\xbb\xbf") or not b.endswith(b"\n"):
            f.append("%s is not LF / no BOM / free of paths" % name)
    l1o, l2o, deto = (json.loads(outs[0][n].decode("utf-8")) for n in ("e1_l1.json", "e1_l2.json", "e1_detection.json"))
    for name, o in (("e1_l1", l1o), ("e1_l2", l2o), ("e1_detection", deto)):
        if sorted(o["cells"]) != ["synthetic-a", "synthetic-b"] or o["seed"] != 7 or o["runs"]["n_used"] != 2:
            f.append("%s does not hold both cells, the seed and the run count" % name)
        if o.get("synthetic") is not True:
            f.append("%s of synthetic runs is not marked synthetic" % name)
        if any(sorted(c) != ["all", "macos_def"] for c in o["cells"].values()):
            f.append("%s does not hold both the operating-system definition and the common definition per cell" % name)
        if any(not re.fullmatch(r"[0-9a-f]{64}", x.get("manifest_sha256", "")) for x in o["runs"]["list"]):
            f.append("%s lists a run without its manifest hash" % name)
    dlv = json.loads(outs[0]["e1_delivery.json"].decode("utf-8"))
    if sorted(dlv["cells"]) != ["synthetic-a", "synthetic-b"] or any(
            c.get("format") != delivery.FORMAT or c["trials"]["measured"] != 2 * SYN_TRIALS_PER_BLOCK for c in dlv["cells"].values()):
        f.append("e1_delivery.json does not hold the delivery report of both cells")
    # the synthetic observation holds a key row, and the synthetic reference log a key_down row, for every press sent
    elif any(c["observation"]["reach"]["all_presses"]["sent_presses"] == 0
             or c["observation"]["reach"]["all_presses"]["reached"] != c["observation"]["reach"]["all_presses"]["sent_presses"]
             or c["reference"]["reach"]["all_presses"]["reached"] != c["reference"]["reach"]["all_presses"]["sent_presses"]
             for c in dlv["cells"].values()):
        f.append("the delivery report does not read the observation and the reference from their own logs")
    # the synthetic oracle sends every press at its planned time (the plan's steps are the oracle's presses)
    elif any((c["send_lateness_ms"]["overall"]["min"], c["send_lateness_ms"]["overall"]["max"]) != (0, 0)
             for c in dlv["cells"].values()):
        f.append("the synthetic plan's schedule and the synthetic oracle disagree")
    for name, o in (("e1_l1", l1o), ("e1_l2", l2o), ("e1_detection", deto)):
        key = name[3:]
        if o.get("primary", {}).get("comparison") != PRIMARY[key]["comparison"] or o["primary"].get("scope") != PRIMARY[key]["scope"]:
            f.append("%s does not name its primary comparison and scope" % name)
    cell = dict((k, o["cells"]["synthetic-a"]["macos_def"]) for k, o in (("l1", l1o), ("l2", l2o), ("detection", deto)))
    if sorted(cell["detection"]) != sorted(COMPARISONS) or any(sorted(v) != sorted(match.SCOPES) for v in cell["detection"].values()):
        f.append("the detection result does not hold every comparison and scope")
    if sorted(cell["l1"]) != sorted(COMPARISONS) or any(sorted(v) != sorted(L1_SCOPES) for v in cell["l1"].values()):
        f.append("level 1 does not hold every comparison over under_limit and all_trials")
    if sorted(cell["l2"]) != ["gt_obs"] or sorted(cell["l2"]["gt_obs"]) != sorted(L2_SCOPES):
        f.append("level 2 does not hold gt_obs over all_trials and under_limit")
    pooled = cell["l1"]["gt_obs"]["under_limit"]["metrics"]["pooled"]
    if not pooled["dwell"]["n"] > 30 or pooled["dwell"]["mae"] is None:
        f.append("level 1 has too few pairs in a synthetic cell: %r" % (pooled["dwell"]["n"],))
    l2p = cell["l2"]["gt_obs"]
    if not (l2p["all_trials"]["texts"]["n"] == l2p["under_limit"]["texts"]["n"] == 8):
        f.append("level 2 should hold 8 texts per synthetic cell in both scopes")
    pred = deto["predictions"]["synthetic-a"]
    n_special = 2 * SYN_TRIALS_PER_BLOCK // 16
    if not pred.get("available") or pred["trials"]["n_long"] != n_special or pred["trials"]["n_carried_out"] != n_special \
            or pred["trials"]["n_none"] != n_special or pred["trials"]["n_closing_key_in_reading"] != n_special:
        f.append("the prediction does not mark the synthetic run's long, Backspace and long-vowel trials: %r" % (pred.get("trials"),))
    if l2p["under_limit"]["per_trial"]["trials_not_used"]["out_of_scope"] != n_special:
        f.append("level 2's per-trial table under under_limit does not leave out the long trials")
    if any(o["primary"].get("scope") != "under_limit" for o in (l1o, l2o, deto)):
        f.append("every primary scope, level 2 included, should be under_limit")
    if pred["trials"].get("n_received_carry") != n_special:
        f.append("the trial after each Backspace trial should be marked received_carry: %r" % (pred["trials"],))
    comp = pred.get("composition") or []
    if sum(c["n_trials"] for c in comp if c["scope"] == "over_limit") != pred["trials"]["n_long"] \
            or sum(c["n_trials"] for c in comp) != 2 * SYN_TRIALS_PER_BLOCK \
            or any(c["letter_keys"]["n"] != c["n_trials"] for c in comp):
        f.append("the composition of the scopes does not cover every trial once: %r" % (comp,))
    dg = cell["detection"]["gt_obs"]["under_limit"]
    nl = dg["scope"]
    if not (isinstance(nl.get("n_layer_none_trials"), int) and 0 < nl["n_layer_none_trials"] < 2 * SYN_TRIALS_PER_BLOCK):
        f.append("the detection view should carry the number of trials of the layer none: %r" % (nl,))
    if dg.get("focus_loss") != {"n_trials": 0, "n_rows": 0, "n_false_pos": 0, "by_speed": {}}:
        f.append("the synthetic reference loses no focus: %r" % (dg.get("focus_loss"),))
    pl = l2p["under_limit"].get("per_trial_layer_none")
    if not (isinstance(pl, dict) and pl["trials_not_used"].get("outside_layer", 0) > 0
            and pl["n_trials"] < l2p["under_limit"]["per_trial"]["n_trials"]):
        f.append("level 2 should give the per-trial table restricted to the layer none: %r" % (pl and pl["trials_not_used"],))
    pu = l2p["under_limit"].get("per_trial_uncontaminated")
    if not (isinstance(pu, dict) and isinstance(pl, dict) and pu["trials_not_used"].get("outside_layer", 0) > 0
            and pl["n_trials"] < pu["n_trials"] < l2p["under_limit"]["per_trial"]["n_trials"]):
        f.append("level 2 should give the per-trial table without the contaminated trials, between the layer none and "
                 "every trial: %r" % (pu and pu["trials_not_used"],))
    if deto.get("same_sessions_as_all") != {"synthetic-a": True, "synthetic-b": True}:
        f.append("the check that the two definitions hold the same sessions is missing")
    d_ = cell["detection"]
    for comp in COMPARISONS:
        u, a, o_ = (d_[comp][s]["used"] for s in ("under_limit", "all_trials", "over_limit"))
        if u["n_gt"] + o_["n_gt"] != a["n_gt"] or u["n_obs"] + o_["n_obs"] != a["n_obs"]:
            f.append("%s: under_limit and over_limit do not partition all_trials" % comp)
    # a long trial's session has no predicted row; the digit after the cut-off opens a fragment row
    gp = d_["gt_pred"]["over_limit"]["metrics"]["pooled"]
    if (gp["n_gt"], gp["n_pairs"], gp["n_false_pos"]) != (n_special, 0, n_special):
        f.append("gt_pred over_limit should hold the long sessions unpaired and one fragment each: %r" % (gp,))
    # pred_obs takes the predicted rows as the reference: every predicted row, and the fragments of the long trials
    po = d_["pred_obs"]
    if po["all_trials"]["input"]["n_gt"] != pred["counts"]["row"] or po["over_limit"]["used"]["n_gt"] != n_special:
        f.append("pred_obs does not take the predicted rows as its reference: %r" % (po["all_trials"]["input"],))
    sha = dict((c, d_[c]["all_trials"]["inputs_sha256"]) for c in COMPARISONS)
    if sha["pred_obs"]["sessions"] == sha["gt_obs"]["sessions"] or sha["pred_obs"]["obs"] != sha["gt_obs"]["obs"] \
            or sha["gt_pred"]["obs"] == sha["gt_obs"]["obs"] or sha["gt_pred"]["sessions"] != sha["gt_obs"]["sessions"]:
        f.append("the comparisons do not take their reference and their observed side from the right inputs")
    # the synthetic observation, like the observer, reports no session of 20 s or more
    if cell["l1"]["gt_obs"]["all_trials"]["truncation"]["n_obs_over_limit"] != 0:
        f.append("the synthetic observation holds a row of 20 s or more")
    if any(cell["l1"][c][s]["scope"] != s for c in COMPARISONS for s in L1_SCOPES):
        f.append("a level-1 result is not computed over the scope it is filed under")
    lay = d_["gt_pred"]["all_trials"]["diagnostics"]["prediction_layers"]
    if lay["closing_key_in_reading"]["pooled"]["n_false_pos"] != n_special or lay["carried_out"]["pooled"]["n_pairs"] != 0:
        f.append("the layers do not show the split and the carried-over sessions of the prediction")
    det = d_["gt_obs"]["under_limit"]["metrics"]["pooled"]
    if det is None or not det["recall"]["value"] > 0.7 or not det["precision"]["value"] > 0.7:
        f.append("detection on a synthetic cell is implausibly low: %r" % (det and det["recall"]["value"],))
    # the seed matters, and the injected errors are visible (a run with no misses would not show them)
    if det is not None and det["n_miss"] + det["n_false_pos"] + det["n_split_gt"] + det["n_merge_obs"] == 0:
        f.append("no injected error reached the detection counts")
    if not FULL:
        return f
    o3 = os.path.join(d, "out3")
    if _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "8", "--out", o3])[0] != 0 or _read_results(o3) == outs[0]:
        f.append("a different seed gives the same results")
    # the two matcher settings that used to be fixed reach the matcher
    o4 = os.path.join(d, "out4")
    cfg4 = _config_file(d, {"theta": 0.9, "drop_start_while_open": True})
    if _quiet(["--runs"] + runs + ["--config", cfg4, "--seed", "7", "--out", o4])[0] != 0:
        f.append("a configuration with theta and drop_start_while_open was refused")
    elif json.loads(_read_results(o4)["e1_detection.json"].decode("utf-8"))["cells"]["synthetic-a"]["macos_def"]["gt_obs"]["under_limit"]["config"].get("theta") != 0.9:
        f.append("theta from the configuration did not reach the matcher")
    return f


def _case_labels_and_hashes(d):
    f = []
    cfg = _config_file(d)
    runs = make_synthetic(os.path.join(d, "abt"), 4)
    _set_manifest(runs[1], aborted=True, abort_reason="synthetic")
    o = os.path.join(d, "outa")
    rc, _o, err = _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "1", "--out", o])
    res = json.loads(open(os.path.join(o, "e1_l1.json"), "rb").read().decode("utf-8")) if rc == 0 else None
    if rc != 0 or sorted(res["cells"]) != ["synthetic-a"] or res["runs"]["n_aborted"] != 1 or res["runs"]["n_used"] != 1:
        f.append("an aborted run is not left out and counted (rc %d): %s" % (rc, err[-200:]))
    if res and [x["aborted"] for x in res["runs"]["list"]] != [False, True]:
        f.append("the run list does not say which run was aborted")
    if FULL:
        # an aborted run may share the label of the run that replaced it, and is counted
        runs1 = make_synthetic(os.path.join(d, "abt1"), 4)
        m0 = json.loads(open(os.path.join(runs1[0], MANIFEST), "rb").read().decode("utf-8"))
        _set_manifest(runs1[1], cell=dict(m0["cell"]), aborted=True, abort_reason="synthetic")
        rc, _o, err = _quiet(["--runs"] + runs1 + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "outa1")])
        if rc != 0:
            f.append("an aborted run with the label of a finished run was refused (rc %d): %s" % (rc, err[-200:]))
    # a log that no longer matches its recorded hash stops the run (exit 2)
    runs2 = make_synthetic(os.path.join(d, "abt2"), 4)
    with open(os.path.join(runs2[0], "obs.jsonl"), "ab") as fh:
        fh.write(b"\n")
    rc = _quiet(["--runs"] + runs2 + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "outb")])[0]
    if rc != 2:
        f.append("a log that changed after the run was accepted (rc %d)" % rc)
    if os.path.exists(os.path.join(d, "outb", "e1_l1.json")):
        f.append("a result was written although the run stopped")
    # two runs that were not aborted with one label are refused
    runs3 = make_synthetic(os.path.join(d, "abt3"), 4)
    m = json.loads(open(os.path.join(runs3[1], MANIFEST), "rb").read().decode("utf-8"))
    _set_manifest(runs3[1], cell=dict(m["cell"], label="synthetic-a"))
    if _quiet(["--runs"] + runs3 + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "outc")])[0] != 2:
        f.append("two runs with the same cell label were accepted")
    # a cell without a label is refused, and the operating system is not used in its place
    runs4 = make_synthetic(os.path.join(d, "abt4"), 4)
    m = json.loads(open(os.path.join(runs4[0], MANIFEST), "rb").read().decode("utf-8"))
    nolabel = dict((k, v) for k, v in m["cell"].items() if k != "label")
    _set_manifest(runs4[0], cell=nolabel)
    rc, _o, err = _quiet(["--runs"] + runs4 + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "outd")])
    if rc != 2 or "label" not in err:
        f.append("a cell without a label was accepted or not explained (rc %d)" % rc)
    if FULL:
        # a recorded hash that is not 64 lowercase hex digits is refused, also for an aborted run
        runs5 = make_synthetic(os.path.join(d, "abt5"), 4)
        m = json.loads(open(os.path.join(runs5[1], MANIFEST), "rb").read().decode("utf-8"))
        m["outputs"]["obs"]["sha256"] = "ABC"
        _set_manifest(runs5[1], outputs=m["outputs"], aborted=True)
        if _quiet(["--runs"] + runs5 + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "oute")])[0] != 2:
            f.append("a malformed recorded hash of an aborted run was accepted")
    return f


def _case_step_zero(d):
    f = []
    cfg = _config_file(d)

    def rc_of(run_dirs, out):
        return _quiet(["--runs"] + run_dirs + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, out)])

    base = make_synthetic(os.path.join(d, "s0"), 2)[:1]
    if rc_of(base, "o0")[0] != 0:
        return ["the unmodified synthetic runs do not pass"]
    # content in the observation log (a typed character) is refused
    obs = open(os.path.join(base[0], "obs.jsonl"), "rb").read()
    _rewrite_log(base[0], "obs", obs + b'{"type":"key","t":1700000000001,"category":"alphabetic","press":true,"char":"a"}\n')
    rc, _o, err = rc_of(base, "o1")
    if rc != 1 or "obs.jsonl" not in err:
        f.append("an observation row with content was accepted (rc %d)" % rc)
    if os.path.exists(os.path.join(d, "o1", "e1_l1.json")):
        f.append("a result was written although step 0 found a violation")
    if FULL:
        # a header that does not declare ime_shadow is refused
        base2 = make_synthetic(os.path.join(d, "s0b"), 2)
        lines = open(os.path.join(base2[0], "obs.jsonl"), "rb").read().split(b"\n", 1)
        hdr = json.loads(lines[0].decode("utf-8"))
        hdr["capabilities"]["config_gated"] = []
        _rewrite_log(base2[0], "obs", json.dumps(hdr, separators=(",", ":")).encode("utf-8") + b"\n" + lines[1])
        rc, _o, err = rc_of(base2, "o2")
        if rc != 1 or "ime_shadow" not in err:
            f.append("a header without the ime_shadow channel was accepted (rc %d)" % rc)
        # a plan that breaks the plan format is refused
        base3 = make_synthetic(os.path.join(d, "s0c"), 2)
        plan = open(os.path.join(base3[0], "plan.jsonl"), "rb").read().replace(b'"speed":"slow"', b'"speed":"sluggish"', 1)
        _rewrite_log(base3[0], "plan", plan)
        rc = rc_of(base3, "o3")[0]
        if rc != 1:
            f.append("an invalid plan was accepted (rc %d)" % rc)
    # a gated channel that produced no row, although the ground truth holds sessions, cannot be judged
    base4 = make_synthetic(os.path.join(d, "s0d"), 2)
    rows = [ln for ln in open(os.path.join(base4[0], "obs.jsonl"), "rb").read().split(b"\n") if ln and b'"type":"ime_shadow"' not in ln]
    _rewrite_log(base4[0], "obs", b"\n".join(rows) + b"\n")
    rc, _o, err = rc_of(base4, "o4")
    if rc != 1 or "gated and produced no row" not in err:
        f.append("a gated channel with no row was accepted or not explained (rc %d)" % rc)
    if FULL:
        # a header that names the channel as unconditional is not held to that rule
        hdr2 = json.loads(rows[0].decode("utf-8"))
        hdr2["capabilities"] = {"unconditional": ["key", "ime_shadow"], "threshold_gated": [], "config_gated": []}
        rows[0] = json.dumps(hdr2, separators=(",", ":")).encode("utf-8")
        _rewrite_log(base4[0], "obs", b"\n".join(rows) + b"\n")
        if rc_of(base4, "o5")[0] != 0:
            f.append("an unconditional channel with no row was refused")
        # a first line nested too deeply is reported, not a crash
        base5 = make_synthetic(os.path.join(d, "s0e"), 2)
        lines = open(os.path.join(base5[0], "obs.jsonl"), "rb").read().split(b"\n", 1)
        _rewrite_log(base5[0], "obs", b"[" * 100000 + b"]" * 100000 + b"\n" + lines[1])
        rc, _o, err = rc_of(base5, "o6")
        if rc not in (1, 2) or "Traceback" in err:
            f.append("a deeply nested first line was not reported cleanly (rc %s)" % (rc,))
    return f


def _prereg_text(sha, extra=""):
    return "# Pre-registration\n\nfrozen_at: 2026-01-01\nsha256: %s\n%s" % (sha, extra)


def _plan_sha_of(run_dir):
    return json.loads(open(os.path.join(run_dir, MANIFEST), "rb").read().decode("utf-8"))["outputs"]["plan"]["sha256"]


def _case_freeze_gate(d):
    """The freeze gate, with the plan of the stand-in runs registered (the registered-plan check is tested after the
    positive cases; the constant is restored on every path)."""
    old = REGISTERED_PLAN_SHA256
    try:
        return _freeze_gate_body(d)
    finally:
        _set_constant("REGISTERED_PLAN_SHA256", old)


def _freeze_gate_body(d):
    f = []
    cfg = _config_file(d)
    seedf = _seed_file(d)
    good = freeze_hash(cfg, seedf)
    if not re.fullmatch(r"[0-9a-f]{64}", good):
        return ["the freeze hash is not 64 hex digits"]
    rc, out, _e = _quiet(["--freeze-hash", "--config", cfg, "--seed-file", seedf])
    if rc != 0 or out.strip() != good:
        f.append("--freeze-hash does not print the value")
    rules = open(os.path.join(_HERE, "gt_rules.example.json"), "rb").read()
    want = _sha(("%s\n%s\n%s\n" % (_sha(open(cfg, "rb").read()), _sha(open(seedf, "rb").read()), _sha(rules))).encode("ascii"))
    if want != good:
        f.append("the freeze hash is not defined as the hash of the three file hashes, each followed by a line feed")
    runs = _real_runs(d, "gate")[:1]
    plan_sha = _plan_sha_of(runs[0])
    _set_constant("REGISTERED_PLAN_SHA256", {"on": plan_sha, "off": "1" * 64})
    pr = os.path.join(d, "preregistration.md")

    def analyse_rc(name, with_prereg=True):
        argv = ["--runs"] + runs + ["--config", cfg, "--seed-file", seedf, "--out", os.path.join(d, name)]
        if with_prereg:
            argv += ["--prereg", pr]
        return _quiet(argv)[0]

    def check_rc(cli=FULL):
        if cli:
            return _quiet(["--check-prereg", pr, "--config", cfg, "--seed-file", seedf])[0]
        try:   # the default runs the check in-process; the command line is exercised by the other calls
            check_prereg_text(open(pr, "rb").read().decode("utf-8"), good)
            return 0
        except NotFrozen:
            return 3

    if analyse_rc("g0", False) != 3:
        f.append("real data was analysed without --prereg")
    if FULL and (analyse_rc("g1") != 3 or check_rc() != 3):
        f.append("real data was analysed although the pre-registration file does not exist")
    zero = "0" * 64
    negatives = (
        ("no marks", "# Pre-registration\n"),
        ("placeholders", "frozen_at: (to be filled in)\nsha256: (to be filled in)\n"),
        ("empty values", "frozen_at:\nsha256:\n"),
        ("marks inside a multi-line comment", "<!--\nfrozen_at: 2026-01-01\nsha256: %s\n-->\n" % good),
        ("marks inside a code fence", "```\nfrozen_at: 2026-01-01\nsha256: %s\n```\n" % good),
        ("marks inside a tilde fence", "~~~\nfrozen_at: 2026-01-01\nsha256: %s\n~~~\n" % good),
        ("a second frozen_at line", _prereg_text(good, "frozen_at: 2026-02-02\n")),
        ("a second sha256 line", _prereg_text(good, "sha256: %s\n" % good)),
        ("a sha256 that does not match", _prereg_text(zero)),
        ("an upper-case sha256", _prereg_text(good.upper())),
        ("a frozen_at without a date", "frozen_at: soon\nsha256: %s\n" % good),
        ("an unclosed comment", "<!-- start\nfrozen_at: 2026-01-01\nsha256: %s\n" % good),
    )
    representative = ("placeholders", "marks inside a multi-line comment", "marks inside a code fence",
                      "a second frozen_at line", "a sha256 that does not match")
    for i, (name, text) in enumerate(negatives):
        if not FULL and name not in representative:
            continue
        _write(pr, text.encode("utf-8"))
        if check_rc() != 3 or ((i % 3 == 0 if FULL else name == "a sha256 that does not match") and analyse_rc("g2") != 3):
            f.append("the freeze gate accepted: %s" % name)
        if os.path.exists(os.path.join(d, "g2", "e1_l1.json")):
            f.append("a result was written when the gate refused: %s" % name)
    positives = (("well-formed marks", _prereg_text(good)),
                 ("marks with a comment and a fence elsewhere", "<!-- note -->\n```\nx\n```\n" + _prereg_text(good)),
                 ("marks with CRLF line ends", _prereg_text(good).replace("\n", "\r\n")))
    for i, (name, text) in enumerate(positives):
        _write(pr, text.encode("utf-8"))
        if i > 0 and not FULL:
            break
        rc = analyse_rc("g3") if i == 0 else 0
        if rc != 0 or (FULL and check_rc() != 0):
            f.append("the freeze gate refused: %s (rc %s)" % (name, rc))
    # a run whose plan is not one of the registered plans counts as aborted: exit 2, no result, no value in the message
    _write(pr, _prereg_text(good).encode("utf-8"))
    for other in ({}, {"on": "0" * 64, "off": "1" * 64}, {"on": plan_sha.upper(), "off": "1" * 64},
                  {"on": "0" * 64, "off": plan_sha}):    # the last: the right plan, registered for the other setting
        _set_constant("REGISTERED_PLAN_SHA256", other)
        o = os.path.join(d, "g3x")
        rc, _o, err = _quiet(["--runs"] + runs + ["--config", cfg, "--seed-file", seedf, "--out", o, "--prereg", pr])
        if rc != 2 or plan_sha in err or os.path.exists(os.path.join(o, "e1_l1.json")):
            f.append("a run whose plan is not registered was analysed, or the message holds a hash (rc %s)" % (rc,))
        if rc == 2 and ("section 3.4" not in err or "section 2.4" not in err or runs and "synthetic-" not in err):
            f.append("the message about an unregistered plan does not name the cell and the sections")
    # a cell without the live-conversion setting, or with another value, is refused even with its plan registered
    _set_constant("REGISTERED_PLAN_SHA256", {"on": plan_sha, "off": plan_sha})
    for tag, settings in (("none", {}), ("other", {LIVE_CONVERSION_KEY: "auto"}), ("upper", {LIVE_CONVERSION_KEY: "ON"}),
                          ("listed", {LIVE_CONVERSION_KEY: ["on"]}), ("wrongkey", {"live": "on"})):
        v = os.path.join(d, "g3s" + tag)
        shutil.copytree(runs[0], v)
        _set_manifest(v, cell=dict(json.loads(open(os.path.join(v, MANIFEST), "rb").read().decode("utf-8"))["cell"],
                                   ime_settings=settings))
        o = os.path.join(d, "g3so" + tag)
        rc, _o, err = _quiet(["--runs", v, "--config", cfg, "--seed-file", seedf, "--out", o, "--prereg", pr])
        if rc != 2 or plan_sha in err or os.path.exists(os.path.join(o, "e1_l1.json")) or "section 2.4" not in err:
            f.append("a run whose cell has no valid live-conversion setting (%s) was analysed (rc %s)" % (tag, rc))
    # both settings, each with its own plan, pass; the plans swapped between the settings are refused
    both = _real_runs(d, "gate-both")
    sha_a, sha_b = _plan_sha_of(both[0]), _plan_sha_of(both[1])
    if sha_a == sha_b:
        f.append("the two synthetic runs have the same plan")
    for k, (i, setting) in enumerate(((0, "on"), (1, "off"))):
        m = json.loads(open(os.path.join(both[i], MANIFEST), "rb").read().decode("utf-8"))
        _set_manifest(both[i], cell=dict(m["cell"], ime_settings={LIVE_CONVERSION_KEY: setting}))
    for reg, want in (({"on": sha_a, "off": sha_b}, 0), ({"on": sha_b, "off": sha_a}, 2)):
        _set_constant("REGISTERED_PLAN_SHA256", reg)
        o = os.path.join(d, "g3b%d" % want)
        rc = _quiet(["--runs"] + both + ["--config", cfg, "--seed-file", seedf, "--out", o, "--prereg", pr])[0]
        if rc != want:
            f.append("live conversion on and off with their own plans: expected exit %d, got %s" % (want, rc))
    # the same runs, marked synthetic, are not subject to the check; neither are --check-deposit and --pack
    _set_constant("REGISTERED_PLAN_SHA256", {})
    syn = make_synthetic(os.path.join(d, "gate-syn"), 2)[:1]
    if _quiet(["--runs"] + syn + ["--config", cfg, "--seed", "7", "--out", os.path.join(d, "g3y")])[0] != 0:
        f.append("a synthetic run was refused for its plan")
    if _quiet(["--check-deposit"] + runs)[0] != 0:
        f.append("--check-deposit applied the registered-plan check")
    if _quiet(["--pack", runs[0], "--out", os.path.join(d, "g3z.zip")])[0] != 0:
        f.append("--pack applied the registered-plan check")
    _set_constant("REGISTERED_PLAN_SHA256", {"on": plan_sha, "off": "1" * 64})
    # the analysis settings changed by one byte after the freeze
    _write(pr, _prereg_text(good).encode("utf-8"))
    with open(cfg, "ab") as fh:
        fh.write(b" ")
    if check_rc(True) != 3 or (FULL and analyse_rc("g4") != 3):
        f.append("a configuration that changed by one byte after the freeze was accepted")
    if FULL:
        _write(cfg, open(cfg, "rb").read()[:-1])
        _write(seedf, b"8\n")
        if analyse_rc("g5") not in (2, 3):
            f.append("a seed that changed after the freeze was accepted")
    return f


def _case_run_sh(d):
    f = []
    bash = shutil.which("bash")
    if bash is None or not os.path.isfile(os.path.join(os.path.dirname(KIT), "experiment", "run.sh")):
        return f
    root = os.path.join(d, "tree")
    shutil.copytree(KIT, os.path.join(root, "kit"), ignore=shutil.ignore_patterns("target", ".build", "__pycache__", "logger", "e2", "refinput-macos", "refinput-windows", "pilot", "corpus"))
    exp = os.path.join(root, "experiment")
    os.makedirs(exp)
    shutil.copyfile(os.path.join(os.path.dirname(KIT), "experiment", "run.sh"), os.path.join(exp, "run.sh"))
    _write(os.path.join(exp, "config.json"), json.dumps({"bootstrap_B": 100, "gt_rules": "../kit/analysis/gt_rules.example.json"}).encode("utf-8"))
    _write(os.path.join(exp, "SEED"), b"7\n")
    runs = _real_runs(root, "runs")[:1]
    # the copy of this file in the tree registers the plan of the stand-in run
    cp = os.path.join(root, "kit", "analysis", "pipeline.py")
    src = open(cp, "rb").read().decode("utf-8")
    reg = src.index("REGISTERED_PLAN_SHA256 = {")
    end = src.index("}" + chr(10), reg)
    _write(cp, (src[:reg] + 'REGISTERED_PLAN_SHA256 = {"on": "%s", "off": "%s"' % (_plan_sha_of(runs[0]), "1" * 64)
                + src[end:]).encode("utf-8"))
    lines = "".join("- run_dir: ../runs/%s  manifest_sha256: %s\n" % (os.path.basename(r), _sha(open(os.path.join(r, MANIFEST), "rb").read()))
                    for r in runs)
    _write(os.path.join(exp, "data.md"), ("# Data\n\n" + lines).encode("utf-8"))
    env = dict(os.environ, IME_RESULTS_DIR=os.path.join(root, "res"), PYTHONUTF8="1")

    def sh(*args):
        try:
            return subprocess.run([bash, "experiment/run.sh"] + list(args), cwd=root, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=170).returncode
        except (OSError, subprocess.SubprocessError):
            return None

    pr = os.path.join(exp, "preregistration.md")
    if sh() != 3:
        f.append("run.sh ran without a pre-registration file")
    good = freeze_hash(os.path.join(exp, "config.json"), os.path.join(exp, "SEED"))
    variants = (("no marks", "# Pre-registration\n"), ("placeholders", "frozen_at: (to be filled in)\nsha256: (to be filled in)\n"),
                       ("empty values", "frozen_at:\nsha256:\n"), ("marks in a comment", "<!--\nfrozen_at: 2026-01-01\nsha256: %s\n-->\n" % good),
                       ("marks in a fence", "```\nfrozen_at: 2026-01-01\nsha256: %s\n```\n" % good),
                       ("two frozen_at lines", _prereg_text(good, "frozen_at: 2026-02-02\n")),
                       ("a sha256 that does not match", _prereg_text("0" * 64)))
    for name, text in variants:
        if not FULL and name != "a sha256 that does not match":
            continue
        _write(pr, text.encode("utf-8"))
        if sh() != 3:
            f.append("run.sh accepted: %s" % name)
    _write(pr, _prereg_text(good).encode("utf-8"))
    rc = sh()
    if rc != 0 or not os.path.isfile(os.path.join(root, "res", "e1_l1.json")):
        f.append("run.sh refused well-formed marks (rc %s)" % (rc,))
    if not FULL:
        return f
    with open(os.path.join(exp, "config.json"), "ab") as fh:
        fh.write(b" ")
    if sh() != 3:
        f.append("run.sh accepted a configuration that changed after the freeze")
    _write(os.path.join(exp, "SEED"), b"1 2\n")
    if sh("--synthetic") != 2:
        f.append("run.sh accepted a seed file with two integers")
    return f


def _case_deposit_paths(d):
    f = []
    runs = make_synthetic(os.path.join(d, "dep"), 2)
    home, repo, user = "C:\\Users\\alice", "/work/ime-repo", "alice"
    pats = build_patterns(home, user, repo)
    files = read_run_files(runs[0])
    if scan_files(files, pats):
        f.append("a clean synthetic run directory has findings")
    m = json.loads(files[MANIFEST].decode("utf-8"))

    def kinds_of(text, patterns=pats):
        mm = dict(files)
        mm[MANIFEST] = _json_bytes(dict(m, note=text))
        return sorted(set(k for _f, _n, k in scan_files(mm, patterns)))

    ap_, hd, rp, ln = "absolute path", "home directory", "repository path", "login name"
    cases = (("the home directory (backslashes, as a JSON string spells them)", "started at C:\\Users\\alice\\work", [ap_, hd, ln]),
             ("the home directory (slashes)", "started at C:/Users/alice/work", [ap_, hd, ln]),
             ("the home directory (MSYS spelling)", "started at /c/Users/alice/work", [ap_, hd, ln]),
             ("the repository path", "cwd /work/ime-repo/papers", [rp]),
             ("the login name", "written by alice on this machine", [ln]),
             ("a drive path of another user", "C:\\Users\\bob\\x", [ap_]),
             ("a short (8.3) name in a drive path", "C:\\Users\\YAMADA~1\\x", [ap_]),
             ("a short (8.3) name in an MSYS path", "/c/Users/YAMADA~1/x", [ap_]),
             ("a drive path with slashes", "D:/data/x", [ap_]),
             ("a UNC path", "\\\\server\\share\\x", [ap_]),
             ("a UNC path with slashes", "//server/share/x", [ap_]),
             ("a macOS home", "/Users/carol/x", [ap_]),
             ("a Linux home", "/home/dave/x", [ap_]),
             ("a mounted volume", "/Volumes/DISK/x", [ap_]),
             ("a file URI", "file:///Users/gina/y", [ap_]),
             ("a file URI in capitals", "FILE:/home/harry/x", [ap_]),
             ("a file URI of another root", "see file:/tmp/x", [ap_]),
             ("an MSYS drive", "/d/work/x", [ap_]),
             ("a path after a colon", "PATH=/usr/bin:/home/erin/bin", [ap_]))
    for name, text, want in cases:
        got = kinds_of(text)
        if got != sorted(want):
            f.append("%s is reported as %r, expected %r" % (name, got, sorted(want)))
    # an escape that a JSON string uses for a separator is decoded before the search
    mm = dict(files)
    for name, spelling in (("unicode escapes", b"\\u002fhome\\u002fharry"), ("escaped slashes", b"\\/home\\/harry"),
                           ("a drive with a unicode escape", b"C:\\u005cx"),
                           ("a file URI with escaped slashes", b"file:\\/\\/\\/tmp\\/x"),
                           ("a file URI with a unicode escape", b"file:\\u002f\\u002ftmp")):
        mm[MANIFEST] = files[MANIFEST].replace(b'"format"', b'"note":"' + spelling + b'","format"', 1)
        if [k for _f, _n, k in scan_files(mm, pats)] != [ap_]:
            f.append("a path spelled with %s was not found" % name)
    for name, text in (("replaced paths", "started at ~/work in <repo>/papers"), ("replaced paths with backslashes", "~\\\\work <repo>\\\\kit"),
                       ("a name inside a longer word", "alicesprings"), ("a relative path", "kit/analysis/x kit\\\\analysis"),
                       ("an address", "see http://example.org/a/b"), ("a fraction", "and/or a/b 1/2/3"),
                       ("a relative path that contains a root name", "kit/home/x ./Users/y ../mnt/z"),
                       ("the word profile:", "profile:/x and a profile: x/y"), ("file: without a slash", "file: x or file:kit/y"),
                       ("a name that ends in file", "tmpfile:/x")):
        if kinds_of(text):
            f.append("%s was reported: %r" % (name, kinds_of(text)))
    if kinds_of("alice", build_patterns(home, "al", repo)):
        f.append("a login name shorter than %d characters was searched for" % MIN_NAME_LEN)
    # names and homes of the measuring machine
    extra = build_patterns(home, user, repo, ["frank"], ["E:\\Users\\gina"])
    if kinds_of("by frank", extra) != [ln] or kinds_of("E:\\Users\\gina\\x", extra) != [ap_, hd]:
        f.append("--also-user / --also-home were not searched for")
    try:
        build_patterns(home, user, repo, ["zz"])
        f.append("a two-character --also-user name was accepted")
    except InputError:
        pass
    # a finding in a log is reported with its line, and the standard-error files are not read
    _write(os.path.join(runs[0], "reset.stderr.txt"), b"/work/ime-repo alice\n")
    if scan_files(read_run_files(runs[0]), pats):
        f.append("a standard-error file was read")
    with open(os.path.join(runs[0], "gt.jsonl"), "ab") as fh:
        fh.write(b'{"note":"/work/ime-repo/x"}\n')
    n_lines = open(os.path.join(runs[0], "gt.jsonl"), "rb").read().count(b"\n")
    if scan_files(read_run_files(runs[0]), pats) != [("gt.jsonl", n_lines, "repository path")]:
        f.append("a finding in a log is not reported with its file and line")
    return f


def _case_deposit_runs(d):
    f = []
    runs = make_synthetic(os.path.join(d, "dr"), 2)
    m = json.loads(open(os.path.join(runs[1], MANIFEST), "rb").read().decode("utf-8"))
    # the command line: exit 1 with the real home directory, 0 once it is replaced
    real = os.path.expanduser("~")
    if len(real.rstrip("/\\")) >= 2 and real != "~":
        _write(os.path.join(runs[1], MANIFEST), _json_bytes(dict(m, note="in " + real + "/x")))
        rc, _o, err = _quiet(["--check-deposit", runs[1]])
        if rc != 1 or real in err:
            f.append("the real home directory was not refused, or was echoed (rc %d)" % rc)
    # a path directly under the default repository root is refused, and the value is not echoed
    root = repo_root()
    _write(os.path.join(runs[1], MANIFEST), _json_bytes(dict(m, note="cwd " + os.path.join(root, "papers"))))
    rc, _o, err = _quiet(["--check-deposit", runs[1]])
    if rc != 1 or root in err:
        f.append("an absolute path under the default repository root was not refused (rc %d)" % rc)
    if "repository path" not in [k for _f, _n, k in scan_files(read_run_files(runs[1]), build_patterns(home="~", user=""))]:
        f.append("the default repository root is not searched for")
    _write(os.path.join(runs[1], MANIFEST), _json_bytes(dict(m, note="in ~/x and <repo>/y")))
    if _quiet(["--check-deposit", runs[1]])[0] != 0:
        f.append("a manifest with the paths replaced by ~ and <repo> was refused")
    _write(os.path.join(runs[1], MANIFEST), _json_bytes(m))
    if FULL:
        if _quiet(["--check-deposit", runs[1], "--also-user", "synthetic"])[0] != 1:
            f.append("--also-user was not applied on the command line")
        if _quiet(["--check-deposit", os.path.join(d, "no-such-dir")])[0] != 2:
            f.append("a missing run directory should exit 2")
    # a log that changed after the run exits 2; a missing log whose hash is recorded exits 2
    with open(os.path.join(runs[1], "gt.jsonl"), "ab") as fh:
        fh.write(b"\n")
    if _quiet(["--check-deposit", runs[1]])[0] != 2:
        f.append("a log that changed after the run was not refused by the deposit check")
    os.remove(os.path.join(runs[1], "gt.jsonl"))
    if _quiet(["--check-deposit", runs[1]])[0] != 2:
        f.append("a missing log with a recorded hash was accepted")
    # the archive (the second run directory is used up by now, so the first one is packed)
    good = runs[0]
    m0 = json.loads(open(os.path.join(good, MANIFEST), "rb").read().decode("utf-8"))
    arch1, arch2 = os.path.join(d, "a1.zip"), os.path.join(d, "a2.zip")
    rc1, out1, _e = _quiet(["--pack", good, "--out", arch1])
    rc2, out2, _e = _quiet(["--pack", good, "--out", arch2])
    if rc1 != 0 or rc2 != 0 or open(arch1, "rb").read() != open(arch2, "rb").read():
        f.append("packing the same run twice does not give the same bytes (rc %s, %s)" % (rc1, rc2))
    else:
        data = open(arch1, "rb").read()
        if _sha(data) not in out1:
            f.append("--pack does not print the SHA-256 of the archive")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if [i.filename for i in infos] != list(DEPOSIT_FILES) or any(i.date_time != ZIP_TIME for i in infos):
                f.append("the archive does not hold the five files in the fixed order with fixed times")
            if any(zf.read(i.filename) != open(os.path.join(good, i.filename), "rb").read() for i in infos):
                f.append("the archive holds different bytes from the run directory")
    before = open(arch1, "rb").read()
    if _quiet(["--pack", good, "--out", arch1])[0] != 2 or open(arch1, "rb").read() != before:
        f.append("an existing archive was overwritten or not refused")
    _write(os.path.join(good, MANIFEST), _json_bytes(dict(m0, note="/home/harry/x")))
    arch3 = os.path.join(d, "a3.zip")
    if _quiet(["--pack", good, "--out", arch3])[0] != 1 or os.path.exists(arch3):
        f.append("a run with an absolute path was packed")
    _write(os.path.join(good, "reset.stderr.txt"), b"x\n")
    _write(os.path.join(good, MANIFEST), _json_bytes(m0))
    if _quiet(["--pack", good, "--out", arch3])[0] != 0:
        f.append("a clean run was not packed")
    elif "reset.stderr.txt" in zipfile.ZipFile(arch3).namelist():
        f.append("a standard-error file was packed")
    # content in a log of a run that was not aborted is refused (with the hash updated, so only content is at fault)
    obs = open(os.path.join(good, "obs.jsonl"), "rb").read()
    _rewrite_log(good, "obs", obs + b'{"type":"key","t":1700000000001,"category":"alphabetic","press":true,"char":"a"}\n')
    rc, _o, err = _quiet(["--check-deposit", good])
    if rc != 1 or "obs.jsonl" not in err:
        f.append("content in an observation log passed the deposit check (rc %d)" % rc)
    # an aborted run whose manifest recorded no hash for a log need not have it
    ma = json.loads(open(os.path.join(good, MANIFEST), "rb").read().decode("utf-8"))
    for name in ("gt", "obs"):
        ma["outputs"][name]["sha256"] = None
        os.remove(os.path.join(good, LOG_FILES[name]))
    _set_manifest(good, outputs=ma["outputs"], aborted=True)
    if _quiet(["--check-deposit", good])[0] != 0:
        f.append("an aborted run without the logs that it recorded as absent was refused")
    if FULL:
        os.remove(os.path.join(good, MANIFEST))
        if _quiet(["--check-deposit", good])[0] != 2:
            f.append("a run directory without run.json was accepted")
    return f


def _case_lists_seeds_files(d):
    f = []
    if _quiet([])[0] != 2:
        f.append("no arguments should exit 2")
    runs = make_synthetic(os.path.join(d, "use"), 2)
    for name, body in (("an unknown key", {"nope": 1}), ("a bad bootstrap size", {"bootstrap_B": 0}),
                       ("a bad overlap threshold", {"iou_min": 2}), ("a bad theta", {"theta": "x"}),
                       ("a bad drop flag", {"drop_start_while_open": 1})):
        cfg = _config_file(d, body)
        rc = _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, "outu")])[0]
        if rc != 2:
            f.append("a configuration with %s was accepted (rc %d)" % (name, rc))
    no_rules = os.path.join(d, "no-rules.json")
    _write(no_rules, b'{"bootstrap_B": 10}')
    if _quiet(["--runs"] + runs + ["--config", no_rules, "--seed", "1", "--out", os.path.join(d, "outu")])[0] != 2:
        f.append("a configuration without gt_rules was accepted")
    shas = [_sha(open(os.path.join(r, MANIFEST), "rb").read()) for r in runs]
    listing = os.path.join(d, "list.md")
    text = ("notes\n<!--\n- run_dir: %s\n-->\n```\n- run_dir: %s\n```\n- run_dir: %s  manifest_sha256: %s\n"
            "<!-- - run_dir: %s -->\n- run_dir: %s  manifest_sha256: %s\n" % (runs[0], runs[1], runs[0], shas[0], runs[1], runs[1], shas[1]))
    _write(listing, text.encode("utf-8"))
    cfg = _config_file(d)
    o = os.path.join(d, "outl")
    rc, out, err = _quiet(["--runs-from", listing, "--config", cfg, "--seed", "7", "--out", o])
    if rc != 0:
        f.append("--runs-from did not run (rc %s): %s" % (rc, err[-200:]))
    elif "synthetic-a, synthetic-b" not in out:
        f.append("--runs-from did not print the labels that were used")
    if FULL:
        o2 = os.path.join(d, "outl2")
        _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "7", "--out", o2])
        if _read_results(o) != _read_results(o2):
            f.append("--runs-from and --runs give different results")
        if _quiet(["--runs-from", listing, "--runs"] + runs + ["--config", cfg, "--seed", "7", "--out", o])[0] != 2:
            f.append("--runs together with --runs-from was accepted")
    for name, body in (("a line without the manifest hash", "- run_dir: %s\n" % runs[0]),
                       ("a different manifest hash", "- run_dir: %s  manifest_sha256: %s\n" % (runs[0], "0" * 64)),
                       ("a malformed manifest hash", "- run_dir: %s  manifest_sha256: xyz\n" % runs[0])):
        _write(listing, body.encode("utf-8"))
        if _quiet(["--runs-from", listing, "--config", cfg, "--seed", "7", "--out", os.path.join(d, "outl3")])[0] != 2:
            f.append("--runs-from accepted %s" % name)
    if FULL:
        # the seed file: exactly one integer, no spaces
        for name, body, ok in (("one integer", "5\n", True), ("no line end", "5", True), ("two integers", "1 2\n", False),
                               ("spaces around", " 5\n", False), ("two lines", "5\n6\n", False), ("empty", "", False),
                               ("a sign", "-5\n", False), ("a word", "five\n", False)):
            p = _seed_file(d, body)
            try:
                read_seed_file(p)
                got = True
            except InputError:
                got = False
            if got != ok:
                f.append("the seed file check is wrong for %s" % name)
        if _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "5", "--seed-file", _seed_file(d, "6\n"), "--out", os.path.join(d, "outs")])[0] != 2:
            f.append("--seed that differs from --seed-file was accepted")
    # a failure to write a result leaves no result of an earlier run
    of = o
    if not os.path.isfile(os.path.join(of, "e1_l1.json")):
        f.append("the first analysis failed")
    else:
        os.remove(os.path.join(of, "e1_detection.json"))
        os.mkdir(os.path.join(of, "e1_detection.json"))
        rc = _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "7", "--out", of])[0]
        if rc != 2 or os.path.exists(os.path.join(of, "e1_l1.json")) or os.path.exists(os.path.join(of, "e1_l2.json")):
            f.append("a stale result was left after a failed write (rc %s)" % (rc,))
        if [n for n in os.listdir(of) if n.startswith(".pipeline_tmp_")]:
            f.append("a temporary directory was left in the results directory")
    if FULL:
        # synthetic results are not written under the results directory of the experiment
        if _quiet(["--runs"] + runs + ["--config", cfg, "--seed", "7", "--out", os.path.join(EXPERIMENT_RESULTS, "selftest-refused")])[0] != 2 \
                or os.path.exists(os.path.join(EXPERIMENT_RESULTS, "selftest-refused")):
            f.append("synthetic results were written under the results directory of the experiment")
    # input files: a link is refused, and there is an upper limit on the size
    target = os.path.join(d, "target.jsonl")
    _write(target, b"x\n")
    if FULL:
        link = os.path.join(d, "link.jsonl")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            link = None
        if link is not None:
            try:
                _read_regular(link, "link")
                f.append("a symbolic link was read")
            except InputError:
                pass
    global MAX_FILE_BYTES
    saved, MAX_FILE_BYTES = MAX_FILE_BYTES, 1
    try:
        _read_regular(target, "big")
        f.append("a file above the size limit was read")
    except InputError:
        pass
    finally:
        MAX_FILE_BYTES = saved
    try:
        _read_regular(d, "directory")
        f.append("a directory was read as a file")
    except InputError:
        pass
    return f


def _case_focus_and_composition(d):
    """_focus_loss counts the rows (and false positives) assigned to the trials that lost focus and no other;
    _composition splits each stratum by scope and counts letter keys and think times from the plan."""
    f = []
    rows = [{"kind": "header", "format": "oracle/1", "plan_sha256": "0" * 64, "os": "macos", "emitter_version": "x",
             "backend": "dryrun"}]
    for i, (tid, b, e) in enumerate((("t1", 1000, 2000), ("t2", 3000, 4000))):
        rows.append({"kind": "trial_begin", "trial_id": tid, "t_wall_ms": b, "t_mono_ns": 10 * i + 1})
        rows.append({"kind": "trial_end", "trial_id": tid, "t_wall_ms": e, "t_mono_ns": 10 * i + 2})
    oracle = gt_sessions.parse_oracle(_jl(rows))
    row = lambda t, dw: {"type": "ime_shadow", "t": t, "dwell_ms": dw, "nav": 0, "abandon": False}
    m_out = {"config": {"obs_end_offset_ms": 0},
             "matching": {"pairs": [{"gt": {}, "obs": row(1800, 500)}, {"gt": {}, "obs": row(3800, 500)}],
                          "split": [{"gt": {}, "obs": [row(3500, 100), row(3700, 100)]}], "merge": [],
                          "miss": [], "false_pos": [row(3900, 50), row(1500, 50)]}}
    strata = {"t1": {"scenario": "S1", "speed": "slow"}, "t2": {"scenario": "S1", "speed": "fast"}}
    got = _focus_loss(m_out, oracle, {"t2"}, strata)
    if got != {"n_trials": 1, "n_rows": 4, "n_false_pos": 1,
               "by_speed": {"fast": {"n_trials": 1, "n_rows": 4, "n_false_pos": 1}}}:
        f.append("rows of the trial that lost focus: %r" % (got,))
    got = _focus_loss(m_out, oracle, {"t1", "t2"}, strata)
    if got != {"n_trials": 2, "n_rows": 6, "n_false_pos": 2,
               "by_speed": {"slow": {"n_trials": 1, "n_rows": 2, "n_false_pos": 1},
                            "fast": {"n_trials": 1, "n_rows": 4, "n_false_pos": 1}}}:
        f.append("rows of two trials that lost focus, per speed: %r" % (got,))
    if _focus_loss(m_out, oracle, set(), strata) != {"n_trials": 0, "n_rows": 0, "n_false_pos": 0, "by_speed": {}}:
        f.append("no trial lost focus, no row should be counted")
    plan = _jl([{"kind": "header", "format": "plan/1"},
                {"kind": "trial", "trial_id": "t1", "scenario": "S1", "speed": "slow", "think_ms": 900,
                 "steps": [{"key": "k", "down": True}, {"key": "k", "down": False}, {"key": "a", "down": True},
                           {"key": "enter", "down": True}]},
                {"kind": "trial", "trial_id": "t2", "scenario": "S1", "speed": "slow", "think_ms": 0,
                 "steps": [{"key": "k", "down": True}]},
                {"kind": "trial", "trial_id": "t3", "scenario": "S1", "speed": "slow", "think_ms": 300,
                 "steps": [{"key": "1", "down": True}, {"key": "b", "down": True}]}])
    pred = {"trials": {"by_trial": {"t1": {"long": True}, "t2": {"long": False}, "t3": {"long": False}}}}
    comp = _composition(plan, pred)
    want = [{"scenario": "S1", "speed": "slow", "scope": "under_limit", "n_trials": 2,
             "letter_keys": {"n": 2, "median": 1.0, "min": 1, "max": 1}, "think_ms": {"n": 1, "median": 300, "min": 300, "max": 300}},
            {"scenario": "S1", "speed": "slow", "scope": "over_limit", "n_trials": 1,
             "letter_keys": {"n": 1, "median": 2, "min": 2, "max": 2}, "think_ms": {"n": 1, "median": 900, "min": 900, "max": 900}}]
    if comp != want:
        f.append("composition: %r" % (comp,))
    return f


def _set_constant(name, value):
    """Set a module constant of this file and return the old value (the selftest restores it)."""
    old = globals()[name]
    globals()[name] = value
    return old


def _case_reference_abort_rule(d):
    """The reference input field's abort rule: unexplained key_down rows per segment of the reference log, for
    every number of keys that the runner presses before a block."""
    f = []
    cfg = _config_file(d)
    base_run = make_synthetic(os.path.join(d, "base"), 2)[0]

    def run_with(name, rel_times):
        """A copy of a synthetic run (cell a) whose reference log gets key_down rows at WALL0 + the times given."""
        dst = os.path.join(d, name)
        shutil.copytree(base_run, dst)
        gt = open(os.path.join(dst, LOG_FILES["gt"]), "rb").read()
        rows = [json.loads(x.decode("utf-8")) for x in gt.splitlines() if x]
        # the reference log of a synthetic run already holds a key_down row for every press the executor sent
        rows += [{"kind": "ev", "t_wall_ms": WALL0 + t, "t_mono_ns": (MONO0 + t) * 1000000, "ev": "key_down", "vk": 65}
                 for t in rel_times]
        rows = rows[:1] + sorted(rows[1:], key=lambda r: r["t_wall_ms"])
        _rewrite_log(dst, "gt", _jl(rows))
        return [dst]

    def rc_of(run_dirs, out):
        return _quiet(["--runs"] + run_dirs + ["--config", cfg, "--seed", "1", "--out", os.path.join(d, out)])

    ora = [json.loads(x.decode("utf-8")) for x in open(os.path.join(base_run, LOG_FILES["oracle"]), "rb").read().split(b"\n") if x]
    begins = [x["t_wall_ms"] - WALL0 for x in ora if x.get("kind") == "block_begin"]
    ends0 = [x["t_wall_ms"] - WALL0 for x in ora if x.get("kind") == "trial_end" and x["t_wall_ms"] - WALL0 < begins[1]]
    last_end0, last_end1 = max(ends0), max(x["t_wall_ms"] - WALL0 for x in ora if x.get("kind") == "trial_end")
    first, second = begins[0] - 1000, begins[1] + 500

    for k in (0, 1):
        old = _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", k)
        try:
            tag = "k%d" % k
            before = [first - i for i in range(k)]                              # the allowance before the first block
            after_in = [last_end0 + 100 + 10 * i for i in range(k)]              # inside the last trial's window
            after_out = [last_end0 + 1000 + 10 * i for i in range(k)]            # outside it
            rc, _o, err = rc_of(run_with(tag + "n", []), tag + "on")
            if rc != 0:
                f.append("[%d] a run with no unexplained key_down was refused (rc %d)" % (k, rc))
            rc, _o, err = rc_of(run_with(tag + "a", before), tag + "oa")
            if rc != 0:
                f.append("[%d] %d key(s) before the first block (the runner's) were refused (rc %d)" % (k, k, rc))
            rc, _o, err = rc_of(run_with(tag + "b", before + [first - k]), tag + "ob")
            if rc != 2 or "before the first block" not in err or str(WALL0 + first) in err or str(first) in err:
                f.append("[%d] %d keys before the first block were accepted, or the message is wrong (rc %d)" % (k, k + 1, rc))
            if os.path.exists(os.path.join(d, tag + "ob", "e1_delivery.json")):
                f.append("[%d] a result was written although the run counts as aborted" % k)
            rc, _o, err = rc_of(run_with(tag + "c", [second]), tag + "oc")
            if rc != 2 or "block 1" not in err or str(second) in err:
                f.append("[%d] one key in the last block's segment was accepted, or the message is wrong (rc %d)" % (k, rc))
            for name, times, where in ((tag + "d", after_in, "inside"), (tag + "e", after_out, "outside")):
                rc = rc_of(run_with(name, times), "o" + name)[0]
                if rc != 0:
                    f.append("[%d] %d key(s) after a block (%s its last trial's window) were refused (rc %d)" % (k, k, where, rc))
            rc, _o, err = rc_of(run_with(tag + "f", [last_end0 + 100] + after_out), "o" + tag + "f")
            if rc != 2 or "block 0" not in err:
                f.append("[%d] %d unexplained keys after a block were accepted (rc %d)" % (k, k + 1, rc))
            rc = rc_of(run_with(tag + "g", [last_end1 + 100]), "o" + tag + "g")[0]
            if rc != 2:
                f.append("[%d] a key after the last trial of the last block was accepted (rc %d)" % (k, rc))
            rc = rc_of(run_with(tag + "h", before + after_in), "o" + tag + "h")[0]
            if rc != 0:
                f.append("[%d] the runner's keys in two different segments were refused (rc %d)" % (k, rc))
            rc = rc_of(run_with(tag + "i", []), "o" + tag + "i")[0]
            res = json.loads(open(os.path.join(d, "o" + tag + "i", "e1_delivery.json"), "rb").read().decode("utf-8")) if rc == 0 else {}
            if res.get("abort_rule") != {"runner_kana_presses_per_block": k, "reference_receives_mode_keys": REFERENCE_RECEIVES_MODE_KEYS,
                                         "focus_loss_with_unexplained_presses_aborts": True,
                                         "focus_margin_ms": delivery.FOCUS_MARGIN_MS,
                                         "obs_match_before_ms": delivery.OBS_MATCH_BEFORE_MS,
                                         "obs_match_after_ms": delivery.OBS_MATCH_AFTER_MS,
                                         "registered_plan_sha256": dict(REGISTERED_PLAN_SHA256),
                                         "pre_ms": 20, "tail_ms": 300}:
                f.append("[%d] the abort rule is not recorded in the delivery results" % k)
        finally:
            _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", old)

    # the rule applied to a delivery report made by hand (markers of 4 presses per block).
    def seg(bid, unexplained, marker):
        return {"block_id": bid, "n_unexplained": unexplained, "n_marker_presses": marker}

    def report(by_segment, excluding=None, losses=0):
        return {"reference": {"segments": {"all_presses": {"by_segment": by_segment},
                                           "excluding_mode_keys": {"by_segment": excluding or by_segment}}},
                "reference_focus_loss": {"n_loss_spans_with_unexplained_presses": losses}}

    for k in (0, 1):
        old = _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", k)
        try:
            def over(a, b, c, **kw):
                return reference_abort_segments(report([seg(None, a, 0), seg(0, b, 4), seg(1, c, 4)], **kw))
            if over(k, 4 + k, 4) != [] or over(k + 1, 0, 0) != [None] or over(0, 4 + k + 1, 0) != [0]:
                f.append("[%d] a segment that is not the last allows %d more than its markers, and no more" % (k, k))
            if over(0, 0, 4) != [] or over(0, 0, 5) != [1]:
                f.append("[%d] the segment of the last block allows its markers and nothing more" % k)
            one = [seg(None, k, 0)]
            if reference_abort_segments(report(one)) != [] or reference_abort_segments(report([seg(None, k + 1, 0)])) != [None]:
                f.append("[%d] a log with no block_begin is not treated as the segment before the first block" % k)
            if reference_abort_reasons(report([seg(None, 0, 0)], losses=0)) != []:
                f.append("[%d] a report with nothing to refuse has a reason" % k)
        finally:
            _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", old)
    old = _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", 1)
    old_mode = REFERENCE_RECEIVES_MODE_KEYS
    try:
        quiet, noisy = [seg(None, 0, 0), seg(0, 4, 4)], [seg(None, 0, 0), seg(0, 6, 4)]
        for receives, want in ((True, []), (False, [0])):
            _set_constant("REFERENCE_RECEIVES_MODE_KEYS", receives)
            if reference_abort_segments(report(quiet, excluding=noisy)) != want:
                f.append("the version of the report does not follow REFERENCE_RECEIVES_MODE_KEYS (%r)" % receives)
        _set_constant("REFERENCE_RECEIVES_MODE_KEYS", old_mode)
        why = reference_abort_reasons(report([seg(None, 0, 0)], losses=1))
        if len(why) != 1 or "loss of focus" not in why[0] or "S15" in why[0]:
            f.append("a focus loss with an unexplained key press is not a reason to refuse")
        if len(reference_abort_reasons(report([seg(None, 3, 0), seg(0, 0, 4)], losses=2))) != 2:
            f.append("the two parts of the rule are not reported separately")
    finally:
        _set_constant("RUNNER_KANA_PRESSES_PER_BLOCK", old)
        _set_constant("REFERENCE_RECEIVES_MODE_KEYS", old_mode)
    return f


def _case_section34_enforcement(d):
    """Section 3.4 before a deposit: a focus loss (the planned switches of S15 included) with an observation key row beyond the keys sent
    refuses the analysis (one with only the keys sent, or with none, does not), and the deposit check and
    the archive apply the abort rule to every run with a reference log, aborted by the runner or not."""
    f = []
    cfg = _config_file(d)
    base_run = make_synthetic(os.path.join(d, "base"), 2)[0]
    ora = [json.loads(x.decode("utf-8")) for x in open(os.path.join(base_run, LOG_FILES["oracle"]), "rb").read().split(b"\n") if x]
    t0 = min(x["t_wall_ms"] for x in ora if x.get("kind") == "trial_begin")
    last_end = max(x["t_wall_ms"] for x in ora if x.get("kind") == "trial_end")

    def variant(name, rows=(), aborted=False, drop=(), extra_obs=(), s15=False, obs_tail=b""):
        """A copy of the run with reference rows added, marked as aborted by the runner when asked; drop = logs left
        out (recorded with a null hash, as the runner does when it aborts); extra_obs = times of observation key
        rows that the executor did not send. The observation holds a key row 5 ms after every press sent."""
        dst = os.path.join(d, name)
        shutil.copytree(base_run, dst)
        # the reference log of a synthetic run already holds a key_down row for every press the executor sent
        gt = [json.loads(x.decode("utf-8")) for x in open(os.path.join(dst, LOG_FILES["gt"]), "rb").read().splitlines() if x]
        gt += [{"kind": "ev", "t_wall_ms": t, "t_mono_ns": (t - WALL0 + MONO0) * 1000000, "ev": ev, **kw} for t, ev, kw in rows]
        gt = gt[:1] + sorted(gt[1:], key=lambda r: r["t_wall_ms"])
        _rewrite_log(dst, "gt", _jl(gt))
        obs = [json.loads(x.decode("utf-8")) for x in open(os.path.join(dst, LOG_FILES["obs"]), "rb").read().splitlines() if x]
        keys = [x["t_wall_ms"] + 5 for x in ora if x.get("kind") == "sent" and x["down"]
                and x["key"] not in delivery.PSEUDO_KEYS and x["key"] not in delivery.MODE_KEYS] + list(extra_obs)
        obs = obs[:1] + sorted([x for x in obs[1:] if x.get("type") != "key"]
                               + [{"type": "key", "t": t, "category": "alphabetic", "press": True} for t in keys],
                               key=lambda r: (r["t"], r["type"]))
        _rewrite_log(dst, "obs", _jl(obs) + obs_tail)
        if s15:   # the first trial becomes a trial of scenario S15 (the plan, and the plan hash in the oracle header)
            plan = [json.loads(x.decode("utf-8")) for x in open(os.path.join(dst, LOG_FILES["plan"]), "rb").read().splitlines() if x]
            next(x for x in plan if x.get("kind") == "trial")["scenario"] = "S15"
            plan_bytes = _jl(plan)
            _rewrite_log(dst, "plan", plan_bytes)
            orc = [json.loads(x.decode("utf-8")) for x in open(os.path.join(dst, LOG_FILES["oracle"]), "rb").read().splitlines() if x]
            orc[0]["plan_sha256"] = _sha(plan_bytes)
            _rewrite_log(dst, "oracle", _jl(orc))
        if aborted:
            m = json.loads(open(os.path.join(dst, MANIFEST), "rb").read().decode("utf-8"))
            for n in drop:
                m["outputs"][n]["sha256"] = None
                os.remove(os.path.join(dst, LOG_FILES[n]))
            _set_manifest(dst, outputs=m["outputs"], aborted=True, abort_reason="synthetic")
        return dst

    loss = [(t0 + 100, "focus_out", {}), (t0 + 200, "focus_in", {})]
    key = [(last_end + 100, "key_down", {"vk": 65})]
    # a loss while the executor types, with the observation holding the keys it sent, refuses nothing
    dst = variant("loss0", loss)
    if _quiet(["--runs", dst, "--config", cfg, "--seed", "1", "--out", os.path.join(d, "o0")])[0] != 0:
        f.append("a run whose reference lost focus while the executor typed, the observation holding the keys sent, was not analysed")
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("a run whose reference lost focus while the executor typed, the observation holding the keys sent, was refused by the deposit check")
    # a key row of the observation that the executor did not send, within the loss, refuses the analysis (exit 2) and
    # the deposit check (exit 1), with no value in the message
    dst = variant("loss", loss, extra_obs=[t0 + 150])
    rc, _o, err = _quiet(["--runs", dst, "--config", cfg, "--seed", "1", "--out", os.path.join(d, "o1")])
    if rc != 2 or "loss of focus" not in err or str(t0 + 100) in err or os.path.exists(os.path.join(d, "o1", "e1_delivery.json")):
        f.append("a run whose reference lost focus was analysed, or the message is wrong (rc %d)" % rc)
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "gt.jsonl" not in err or str(t0 + 100) in err:
        f.append("a run whose reference lost focus passed the deposit check (rc %d)" % rc)
    arch = os.path.join(d, "loss.zip")
    if _quiet(["--pack", dst, "--out", arch])[0] != 1 or os.path.exists(arch):
        f.append("a run whose reference lost focus was packed")
    # a loss with no key press of the observation within it (and its margins) refuses nothing
    quiet_loss = [(last_end + 1000, "focus_out", {}), (last_end + 1031, "focus_in", {})]
    dst = variant("qloss", quiet_loss)
    if _quiet(["--runs", dst, "--config", cfg, "--seed", "1", "--out", os.path.join(d, "oq")])[0] != 0:
        f.append("a run whose reference lost focus with no key press near the loss was not analysed")
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("a run whose reference lost focus with no key press near the loss was refused by the deposit check")
    # a planned switch of S15 (a loss that begins and ends inside an S15 trial) is a loss like any other: a key row
    # of the observation that the executor did not send refuses the run; the same run without that row passes
    dst = variant("s15ok", loss, s15=True)
    if _quiet(["--runs", dst, "--config", cfg, "--seed", "1", "--out", os.path.join(d, "os0")])[0] != 0:
        f.append("a run with a planned S15 switch and no key row beyond the keys sent was not analysed")
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("a run with a planned S15 switch and no key row beyond the keys sent was refused by the deposit check")
    dst = variant("s15bad", loss, s15=True, extra_obs=[t0 + 150])
    rc, _o, err = _quiet(["--runs", dst, "--config", cfg, "--seed", "1", "--out", os.path.join(d, "os1")])
    if rc != 2 or "loss of focus" not in err or os.path.exists(os.path.join(d, "os1", "e1_delivery.json")):
        f.append("a key row beyond the keys sent inside a planned S15 switch did not refuse the analysis (rc %d)" % rc)
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "section 3.4" not in err or str(t0 + 150) in err:
        f.append("a key row beyond the keys sent inside a planned S15 switch passed the deposit check (rc %d)" % rc)
    # a run aborted by the runner is checked in the same way
    dst = variant("a1", key, aborted=True)
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "section 3.4" not in err or str(last_end + 100) in err:
        f.append("an aborted run with a key from outside in the reference log passed the deposit check (rc %d)" % rc)
    arch = os.path.join(d, "a1.zip")
    if _quiet(["--pack", dst, "--out", arch])[0] != 1 or os.path.exists(arch):
        f.append("an aborted run with a key from outside in the reference log was packed")
    dst = variant("a2", loss, aborted=True, extra_obs=[t0 + 150])
    if _quiet(["--check-deposit", dst])[0] != 1:
        f.append("an aborted run with a focus loss in the reference log passed the deposit check")
    # nothing from outside: the aborted run passes, with its observation log absent as well
    dst = variant("a3", aborted=True)
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("an aborted run with nothing from outside was refused by the deposit check")
    arch = os.path.join(d, "a3.zip")
    if _quiet(["--pack", dst, "--out", arch])[0] != 0 or not os.path.exists(arch):
        f.append("an aborted run with nothing from outside was not packed")
    dst = variant("a4", aborted=True, drop=("obs",))
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("an aborted run without an observation log was refused by the deposit check")
    # the content-free validator also applies to the logs of an aborted run; a log that cannot be validated refuses
    dst = variant("a8", aborted=True, obs_tail=b'{"type":"key","t":1700000005000,"category":"alphabetic","press":true,"char":"a"}\n')
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "obs.jsonl" not in err or "section 3.4" in err:
        f.append("an aborted run with content in the observation log passed the deposit check (rc %d)" % rc)
    dst = variant("a9", aborted=True, obs_tail=b'{"type":"key","t":')
    if _quiet(["--check-deposit", dst])[0] != 1:
        f.append("an aborted run whose observation log ends in a cut-off line passed the deposit check")
    # no reference log: nothing to check; no oracle log but a reference log: not checkable
    dst = variant("a5", aborted=True, drop=("gt", "obs"))
    if _quiet(["--check-deposit", dst])[0] != 0:
        f.append("an aborted run with no reference log was refused by the deposit check")
    dst = variant("a6", aborted=True, drop=("oracle",))
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "cannot be checked" not in err:
        f.append("an aborted run with a reference log and no oracle log was not reported as uncheckable (rc %d)" % rc)
    # an observation log without a reference log: the loss of focus cannot be seen, so the run is not checkable
    dst = variant("a7", aborted=True, drop=("gt",))
    rc, _o, err = _quiet(["--check-deposit", dst])
    if rc != 1 or "cannot be checked" not in err or "obs.jsonl" not in err:
        f.append("an aborted run with an observation log and no reference log was not reported as uncheckable (rc %d)" % rc)
    return f


CASES = [
    ("the abort rule of the reference input field (unexplained key presses per block)", _case_reference_abort_rule),
    ("section 3.4 before a deposit: focus loss, and the abort rule for aborted runs in the deposit check and the archive",
     _case_section34_enforcement),
    ("the rows of the trials that lost focus, and the composition of the scopes", _case_focus_and_composition),
    ("synthetic run directories (form, cell accepted by the run-cell tool, determinism, seed, no overwrite)", _case_synthetic_form),
    ("one command reproduces the results (byte-identical, no paths, both definitions, seed matters)", _case_reproduces),
    ("labels come from the cell; aborted runs are counted; a changed log or a shared label stops the run", _case_labels_and_hashes),
    ("step 0 refuses content, a missing channel, an invalid plan and a gated channel with no row", _case_step_zero),
    ("the freeze gate of the analysis (well-formed marks, comments, fences, duplicates, changed settings)", _case_freeze_gate),
    ("the freeze gate of the shell entry point", _case_run_sh),
    ("deposit check: absolute paths, home directories, login names, escapes, lines, stderr files", _case_deposit_paths),
    ("deposit check and archive: hashes, aborted runs, content, deterministic archive, no overwrite", _case_deposit_runs),
    ("usage, configuration, runs list, seed file, failed writes and input files", _case_lists_seeds_files),
]


def selftest():
    global _READ_CACHE
    _READ_CACHE = {}
    try:
        return _selftest()
    finally:
        _READ_CACHE = None


def _selftest():
    bad = 0
    for name, fn in CASES:
        d = tempfile.mkdtemp(prefix="pipeline_selftest_")
        try:
            fails = fn(d)
        except Exception as e:  # an unexpected exception counts as a failure
            fails = ["exception %s: %s" % (type(e).__name__, e)]
        finally:
            shutil.rmtree(d, ignore_errors=True)
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
