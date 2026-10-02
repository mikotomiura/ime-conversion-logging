#!/usr/bin/env python3
# kit: selftest
"""Cell runner: measures one cell (operating system x input method x settings) with one command.

  run_cell.py --cell CELL.json --plan PLAN --run-dir DIR [--i-understand-this-sends-keys]
  run_cell.py --selftest

The runner starts the standalone observer (``logger_cmd``) and the reference input field (``refinput_cmd``) as
child processes, waits ``ready_wait_ms``, and then plays the plan block by block through the scenario emitter
(``kit/emit/emit.py``, used as a library). Before each block whose plan row asks for it (``reset_ime``), it runs
``reset_cmd`` and records the exit status in the oracle; before every block it runs ``focus_cmd`` (both commands
only with the send flag) and then, with
a real backend, confirms through the backend that the reference input field is in front (the backend's
``focus_back`` key, which brings the window forward, reads the front window back, and stops if it differs).
After the last block the children are stopped in the reverse of the order in which they were started (the
reference input field, then the observer). Everything is written to the run directory:

  plan.jsonl     a byte-for-byte copy of the plan that was played
  oracle.jsonl   what the emitter sent and when (``oracle/1``)
  gt.jsonl       the reference input field's log (``gt/1``)
  obs.jsonl      the observer's log (``obs/1``)
  run.json       the manifest (``run/1``): the cell, the commit and whether the checkout was modified, the host,
                 both clocks at start and end, the SHA-256 and line count of each log, the children's exit
                 statuses and how each was stopped, the reset and focus records, and ``aborted`` with its reason
  <child>.stderr.txt, reset.stderr.txt, focus.stderr.txt   what the children and the commands wrote to
                 standard error
  runner.error.txt   the traceback of an unexpected error of the runner itself (only in that case)

Only ``run.json`` and the four logs are meant to be deposited; the other files stay on the measuring machine.
The manifest is written with the home directory and the root of the checkout replaced by ``~`` and ``<repo>``
(in every string, in both spellings of the path separator), so that no user name or directory layout is
deposited.

The commands are not chosen here. How an input method is reset and how the reference input field is brought to
the front differ by system and input method; the cell file gives them and this runner executes them and
records the exit status. A cell file has these keys (all required except the last four):

  os, ime, ime_settings, refinput_cmd, logger_cmd, reset_cmd, focus_cmd, ready_wait_ms, backend,
  clock_sync_disabled, auto_update_disabled, backend_opts, stop_wait_ms, settle_ms, command_timeout_ms

``refinput_cmd`` and ``logger_cmd`` are argument arrays in which the text ``{out}`` is replaced by the absolute
path of the log that child writes; ``reset_cmd`` and ``focus_cmd`` are argument arrays (an empty array does
nothing). ``clock_sync_disabled`` and ``auto_update_disabled`` are statements by the person who prepared the
machine; the runner records them and cannot check them. A cell file is executable configuration: it names
programs that are run with the privileges of the person who runs the runner.

The flag named by ``SEND_FLAG`` is the consent to every operation that changes the state of the machine: it is
required for a backend other than ``dryrun`` (which sends keys), for ``reset_cmd`` (which changes the input
method's stored state), and for ``focus_cmd`` (which may press a key, such as the one that switches the input
method on), also with ``dryrun``. Without it the runner exits with status 2 for a real backend, and neither a
reset nor a focus command is run: the manifest records each as skipped with the reason, and the oracle gets no
reset row. A real
backend also needs the backend option that its ``focus_back`` key uses (the application or window to bring to
the front); without it the runner exits with status 2 before any child is started. The flag check, the plan
check, the clock check, and the check that the run directory does not exist all happen before any child process
is started, and an existing run directory is never touched.

A child that exits while the run is in progress stops the run at the next key (the check is made
synchronously just before each key press): the run is marked ``aborted`` with the reason (a material for the
exclusion rule "the host crashed"). A focus command that cannot be run, times out, or exits with a non-zero
status aborts the run, and so does a failed front-window confirmation, since the keys would go to the wrong
window. A reset command that fails is recorded (with its kind of failure: ``could_not_start``, ``timed_out``,
or ``exit_status``) and the run continues.

Children are stopped with their descendants: on POSIX a child is started in its own session and the process
group receives SIGTERM (``terminated`` if the child ends within ``stop_wait_ms``, ``killed`` otherwise); on
Windows the process tree is ended with ``taskkill /T /F`` (``forced``, or ``killed`` on a timeout). A child
command must be the program itself, not a wrapper script, and it must write each record to the operating system
as it is produced, so that the lines written before a forced end remain.

Exit status: 0 the run completed / 1 the run was aborted or its manifest could not be written / 2 refused or
could not start.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

RUN_FORMAT = "run/1"
RUNNER_VERSION = "2"
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
LOG_NAMES = ("plan", "oracle", "gt", "obs")
LOG_FILES = {"plan": "plan.jsonl", "oracle": "oracle.jsonl", "gt": "gt.jsonl", "obs": "obs.jsonl"}
MANIFEST_FILE = "run.json"
ERROR_FILE = "runner.error.txt"
OUT_MARK = "{out}"
POLL_SECONDS = 0.05
MAX_WAIT_MS = 3600000
DEFAULT_STOP_WAIT_MS = 5000
DEFAULT_SETTLE_MS = 1000
DEFAULT_COMMAND_TIMEOUT_MS = 60000
FAILED_TO_RUN = -1     # exit status recorded for a command that could not start or timed out
SIGTERM_STATUS = 143
GIT_TIMEOUT_S = 10
TASKKILL_TIMEOUT_S = 10
STOP_BY_REQUEST = "terminated" if os.name == "posix" else "forced"


def _load_module(name, *parts):
    path = os.path.join(KIT, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EM = _load_module("kit_emit", "emit", "emit.py")
SEND_FLAG = EM.SEND_FLAG
BACKENDS = EM.BACKENDS
OS_NAMES = EM.OS_NAMES


class CellError(Exception):
    pass


class Aborted(Exception):
    """Raised inside a run when it has to stop and be marked aborted."""


class ChildDied(RuntimeError):
    pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ------------------------------------------------------------------ cell file

REQUIRED_KEYS = ("os", "ime", "ime_settings", "refinput_cmd", "logger_cmd", "reset_cmd", "focus_cmd",
                 "ready_wait_ms", "backend", "clock_sync_disabled", "auto_update_disabled")
OPTIONAL_KEYS = ("label", "backend_opts", "stop_wait_ms", "settle_ms", "command_timeout_ms")
LABEL_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")   # the form of a cell label that the analysis accepts


def _cmd(cell, key, allow_empty, needs_out):
    v = cell[key]
    if (not isinstance(v, list) or not all(isinstance(a, str) and a for a in v)
            or (not allow_empty and not v)):
        raise CellError("%s must be an array of non-empty strings%s" % (key, "" if allow_empty else " (not empty)"))
    if needs_out and not any(OUT_MARK in a for a in v):
        raise CellError("%s must contain %s in an argument, where the path of its log goes" % (key, OUT_MARK))
    return v


def check_cell(cell):
    """Return the cell with its optional keys filled in; raise CellError."""
    if not isinstance(cell, dict):
        raise CellError("the cell file must hold one JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in cell]
    if missing:
        raise CellError("missing key(s): %s" % ", ".join(missing))
    unknown = sorted(set(cell) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS))
    if unknown:
        raise CellError("unknown key(s): %s" % ", ".join(unknown))
    if cell["os"] not in OS_NAMES:
        raise CellError("os must be one of %s" % ", ".join(OS_NAMES))
    if not isinstance(cell["ime"], str) or not cell["ime"]:
        raise CellError("ime must be a non-empty string (name and version)")
    if not isinstance(cell["ime_settings"], dict):
        raise CellError("ime_settings must be an object")
    if "label" in cell and (not isinstance(cell["label"], str) or not LABEL_RE.fullmatch(cell["label"])):
        raise CellError("label must be 1 to 64 characters from letters, digits and _ . + -")
    _cmd(cell, "refinput_cmd", False, True)
    _cmd(cell, "logger_cmd", False, True)
    _cmd(cell, "reset_cmd", True, False)
    _cmd(cell, "focus_cmd", True, False)
    if cell["backend"] not in BACKENDS:
        raise CellError("backend must be one of %s" % ", ".join(BACKENDS))
    for key in ("clock_sync_disabled", "auto_update_disabled"):
        if not isinstance(cell[key], bool):
            raise CellError("%s must be true or false" % key)
    full = dict(cell)
    full.setdefault("backend_opts", {})
    full.setdefault("stop_wait_ms", DEFAULT_STOP_WAIT_MS)
    full.setdefault("settle_ms", DEFAULT_SETTLE_MS)
    full.setdefault("command_timeout_ms", DEFAULT_COMMAND_TIMEOUT_MS)
    opts = full["backend_opts"]
    if not isinstance(opts, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in opts.items()):
        raise CellError("backend_opts must be an object of strings")
    for key, low in (("ready_wait_ms", 0), ("stop_wait_ms", 0), ("settle_ms", 0), ("command_timeout_ms", 1)):
        if not _is_int(full[key]) or not low <= full[key] <= MAX_WAIT_MS:
            raise CellError("%s must be an integer from %d to %d" % (key, low, MAX_WAIT_MS))
    return full


def load_cell(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        cell = json.loads(raw.decode("utf-8"), object_pairs_hook=EM._no_duplicate_keys)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise CellError("the cell file is not JSON (or a key is repeated)")
    return check_cell(cell)


def _is_batch_script(cmd):
    return os.path.splitext(cmd[0])[1].lower() in (".bat", ".cmd")


# ------------------------------------------------------------------ children

def _substitute(cmd, out):
    return [a.replace(OUT_MARK, out) for a in cmd]


def _taskkill():
    return os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "taskkill.exe")


class Child(object):
    """One child process whose standard error goes to ``<name>.stderr.txt`` in the run directory."""

    def __init__(self, name, cmd, run_dir):
        self.name = name
        self.run_dir = run_dir
        self.stderr_file = name + ".stderr.txt"
        self.how_stopped = None
        self.exit_code = None
        self._err = open(os.path.join(run_dir, self.stderr_file), "xb")
        try:
            extra = {"start_new_session": True} if os.name == "posix" else {}
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=self._err, **extra)
        except BaseException:
            self._err.close()
            raise

    def _kill_tree(self):
        """End the child and everything it started, without waiting for a reply."""
        if os.name == "posix":
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            try:
                subprocess.run([_taskkill(), "/PID", str(self.proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TASKKILL_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            self.proc.kill()
        except OSError:
            pass

    def _stop_posix(self, wait_s):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=wait_s)
            self.how_stopped = "terminated"
            self._kill_tree()          # descendants that outlived the child
        except subprocess.TimeoutExpired:
            self._kill_tree()
            self.proc.wait()
            self.how_stopped = "killed"

    def _stop_windows(self, wait_s):
        try:
            subprocess.run([_taskkill(), "/PID", str(self.proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TASKKILL_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()
            self.proc.wait()
            self.how_stopped = "killed"
            return
        try:
            self.proc.wait(timeout=wait_s)
            self.how_stopped = "forced"
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            self.how_stopped = "killed"

    def stop(self, wait_s):
        """End the child with its descendants and remember how: ``exited_before_stop``; ``terminated`` (POSIX,
        SIGTERM was enough) or ``forced`` (Windows, the process tree was ended); ``killed`` (it did not end in
        time)."""
        try:
            if self.proc.poll() is not None:
                self.how_stopped = "exited_before_stop"
            elif os.name == "posix":
                self._stop_posix(wait_s)
            else:
                self._stop_windows(wait_s)
            self.exit_code = self.proc.returncode
        except BaseException:
            self._kill_tree()
            self.how_stopped = "killed"
            self.exit_code = self.proc.poll()
            raise
        finally:
            self._err.close()


def check_children(children):
    for c in children:
        code = c.proc.poll()
        if code is not None:
            raise ChildDied("%s exited with status %s before the run was over" % (c.name, code))


class Watcher(threading.Thread):
    """Notices a child that exits, so that the next key press is refused."""

    def __init__(self, children):
        threading.Thread.__init__(self)
        self.daemon = True
        self.children = children
        self.dead = None
        self._done = threading.Event()

    def run(self):
        while not self._done.wait(POLL_SECONDS):
            try:
                check_children(self.children)
            except ChildDied as e:
                self.dead = str(e)
                return

    def finish(self):
        self._done.set()
        self.join()


class GuardedBackend(object):
    """Wraps a backend: once a child has died no key is pressed (releases still go through, so no key is left
    held). The children are checked synchronously just before every press."""

    def __init__(self, inner, watcher, children):
        self._inner = inner
        self._watcher = watcher
        self._children = children

    def open(self):
        self._inner.open()

    def send(self, key, down):
        if down:
            if self._watcher.dead is not None:
                raise ChildDied(self._watcher.dead)
            check_children(self._children)
        self._inner.send(key, down)

    def close(self):
        self._inner.close()


class Commands(object):
    """Runs the reset and focus commands; each one's standard error is appended to ``<name>.stderr.txt``."""

    def __init__(self, run_dir, timeout_ms):
        self._run_dir = run_dir
        self._timeout = timeout_ms / 1000.0
        self._files = {}

    def run(self, name, cmd):
        """Return (exit status, failure kind or None). The status is FAILED_TO_RUN when the command could not
        start or timed out; the kind is ``could_not_start``, ``timed_out``, or ``exit_status``."""
        if name not in self._files:
            self._files[name] = open(os.path.join(self._run_dir, name + ".stderr.txt"), "xb")
        try:
            code = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=self._files[name], timeout=self._timeout).returncode
        except subprocess.TimeoutExpired:
            return FAILED_TO_RUN, "timed_out"
        except (OSError, subprocess.SubprocessError):
            return FAILED_TO_RUN, "could_not_start"
        return code, (None if code == 0 else "exit_status")

    def close(self):
        for f in self._files.values():
            f.close()


def wait_ready(children, ms):
    deadline = time.perf_counter() + ms / 1000.0
    while True:
        check_children(children)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, POLL_SECONDS))


# ------------------------------------------------------------------ manifest

def sha256_and_lines(path):
    """(sha256, number of lines) of a file, or (None, None) if it is absent or empty."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None, None
    if not data:
        return None, None
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
    return hashlib.sha256(data).hexdigest(), lines


def _git(args):
    try:
        r = subprocess.run(["git"] + args, cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def git_state():
    """(commit, dirty): the commit of the checkout that holds the runner, and whether ``git status
    --porcelain`` lists anything; None for a value that could not be determined."""
    text = _git(["rev-parse", "HEAD"])
    commit = text.strip() if text is not None else None
    if commit is not None and not (len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)):
        commit = None
    status = _git(["status", "--porcelain"])
    return commit, (None if status is None else bool(status.strip()))


def repo_root():
    top = _git(["rev-parse", "--show-toplevel"])
    if top is not None and top.strip():
        return os.path.normpath(top.strip())
    return os.path.dirname(os.path.dirname(os.path.dirname(KIT)))


def make_redactor(home, repo):
    """Return a function that replaces the home directory by ``~`` and the repository root by ``<repo>`` in a
    string, in the spelling with ``/`` and the one with ``\\``, the longer path first."""
    forms = {}
    ignore_case = os.name == "nt"
    for path, repl in ((repo, "<repo>"), (home, "~")):
        if not path or path in ("~", "/", "\\"):
            continue
        path = path.rstrip("/\\")
        if len(path) < 2:
            continue
        for form in (path, path.replace("\\", "/"), path.replace("/", "\\")):
            forms[form.lower() if ignore_case else form] = repl
    if not forms:
        return lambda s: s
    alternatives = sorted(forms, key=len, reverse=True)
    pattern = re.compile("(?:%s)(?![\\w.-])" % "|".join(re.escape(a) for a in alternatives),
                         re.IGNORECASE if ignore_case else 0)

    def redact(text):
        return pattern.sub(lambda m: forms[m.group(0).lower() if ignore_case else m.group(0)], text)

    return redact


def _redact_all(obj, redact):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [_redact_all(v, redact) for v in obj]
    if isinstance(obj, dict):
        return dict((redact(k), _redact_all(v, redact)) for k, v in obj.items())
    return obj


def host_info():
    host = {"platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "python_implementation": platform.python_implementation()}
    if sys.platform == "darwin":
        release, _, machine = platform.mac_ver()
        host["mac_ver"] = {"release": release, "machine": machine}
    if os.name == "nt":
        v = sys.getwindowsversion()
        host["windows"] = {"major": v.major, "minor": v.minor, "build": v.build,
                           "service_pack": getattr(v, "service_pack", "")}
    return host


def _clocks():
    return {"wall_ms": EM._wall_ns() // 1000000, "mono_ns": EM._mono_ns()}


def build_manifest(cell, plan_sha, run_dir, started, ended, children, resets, focus, aborted, reason,
                   commit, dirty):
    outputs = {}
    for name in LOG_NAMES:
        sha, lines = sha256_and_lines(os.path.join(run_dir, LOG_FILES[name]))
        outputs[name] = {"file": LOG_FILES[name], "sha256": sha, "lines": lines}
    manifest = {
        "format": RUN_FORMAT,
        "runner_version": RUNNER_VERSION,
        "emitter_version": EM.EMITTER_VERSION,
        "cell": cell,
        "plan_sha256": plan_sha,
        "git_commit": commit,
        "git_dirty": dirty,
        "host": host_info(),
        "started": started,
        "ended": ended,
        "outputs": outputs,
        "children": dict((c.name, {"exit_code": c.exit_code, "stopped": c.how_stopped,
                                   "stderr_file": c.stderr_file}) for c in children),
        "resets": resets,
        "focus": focus,
        "aborted": aborted,
        "abort_reason": reason,
    }
    return _redact_all(manifest, make_redactor(os.path.expanduser("~"), repo_root()))


def write_manifest(run_dir, manifest):
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(os.path.join(run_dir, MANIFEST_FILE), "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ------------------------------------------------------------------ the run

def _sigterm(signum, frame):
    sys.exit(SIGTERM_STATUS)


def _confirm_front(guarded, block_id):
    """Press and release the backend's focus_back key: the backend brings the window forward and reads it back."""
    try:
        guarded.send("focus_back", True)
        guarded.send("focus_back", False)
    except ChildDied:
        raise
    except Exception as e:
        raise Aborted("the front window could not be confirmed before block %d (%s); nothing was sent to that "
                      "block" % (block_id, type(e).__name__))


def play(cell, header, blocks, plan_sha, backend, module, allow_send, run_dir, children, resets, focus):
    """Start the children, play every block, and return; raise Aborted / ChildDied / emitter errors."""
    logger_out = os.path.join(run_dir, LOG_FILES["obs"])
    refinput_out = os.path.join(run_dir, LOG_FILES["gt"])
    commands = Commands(run_dir, cell["command_timeout_ms"])
    watcher = None
    try:
        children.append(Child("logger", _substitute(cell["logger_cmd"], logger_out), run_dir))
        children.append(Child("refinput", _substitute(cell["refinput_cmd"], refinput_out), run_dir))
        watcher = Watcher(children)
        watcher.start()
        wait_ready(children, cell["ready_wait_ms"])
        guarded = GuardedBackend(backend, watcher, children)
        confirm = cell["backend"] != "dryrun"
        with EM.Emitter(guarded, os.path.join(run_dir, LOG_FILES["oracle"]), plan_sha, header["os"],
                        cell["backend"], allow_send=allow_send, supported_keys=module.SUPPORTED_KEYS) as em:
            for block, trials in blocks:
                block_id = block["block_id"]
                check_children(children)
                if block.get("reset_ime") is True:
                    if not allow_send:
                        resets.append({"block_id": block_id, "exit_code": None,
                                       "skipped": "the send flag was not given; a reset changes the state of "
                                                  "the machine and is not run without it"})
                    elif not cell["reset_cmd"]:
                        resets.append({"block_id": block_id, "exit_code": None,
                                       "skipped": "the cell has no reset command"})
                    else:
                        code, failure = commands.run("reset", cell["reset_cmd"])
                        em.write_reset(block_id, code)
                        resets.append({"block_id": block_id, "exit_code": code, "failure": failure})
                entry = {"block_id": block_id, "exit_code": None, "failure": None, "front_confirmed": False}
                focus.append(entry)
                if cell["focus_cmd"] and not allow_send:
                    entry["skipped"] = ("the send flag was not given; a focus command may press a key and is not "
                                        "run without it")
                elif cell["focus_cmd"]:
                    code, failure = commands.run("focus", cell["focus_cmd"])
                    entry["exit_code"], entry["failure"] = code, failure
                    if failure is not None:
                        raise Aborted("the focus command failed (%s, status %d) before block %d; nothing was "
                                      "sent to that block" % (failure, code, block_id))
                check_children(children)
                if confirm:
                    _confirm_front(guarded, block_id)
                    entry["front_confirmed"] = True
                em.run_block(block, trials)
        time.sleep(cell["settle_ms"] / 1000.0)
        check_children(children)
    finally:
        try:
            if watcher is not None:
                watcher.finish()
        finally:
            commands.close()


def _stop_children(children, wait_s, notes):
    """Stop every child, last started first, also when a stop is itself interrupted. Return the first
    KeyboardInterrupt or SystemExit that arrived meanwhile (or None)."""
    pending = None
    for c in reversed(children):
        try:
            c.stop(wait_s)
        except OSError as e:
            c.how_stopped = "stop failed: %s" % type(e).__name__
        except BaseException as e:
            notes.append("stopping %s was interrupted (%s)" % (c.name, type(e).__name__))
            if pending is None and isinstance(e, (KeyboardInterrupt, SystemExit)):
                pending = e
    return pending


def _record_error(run_dir):
    text = traceback.format_exc()
    sys.stderr.write(text)
    try:
        with open(os.path.join(run_dir, ERROR_FILE), "xb") as f:
            f.write(text.encode("utf-8", "replace"))
    except OSError:
        pass


def execute(cell, header, blocks, plan_sha, plan_bytes, backend, module, allow_send, run_dir):
    """Create the run directory and run the cell in it. Return the exit status."""
    parent = os.path.dirname(run_dir)
    os.makedirs(parent, exist_ok=True)
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        print("run_cell: the run directory already exists; it is never reused", file=sys.stderr)
        return 2
    with open(os.path.join(run_dir, LOG_FILES["plan"]), "xb") as f:
        f.write(plan_bytes)
    children, resets, focus, notes = [], [], [], []
    aborted, reason, pending = False, None, None
    commit, dirty = git_state()
    started = _clocks()
    previous = signal.signal(signal.SIGTERM, _sigterm)
    try:
        try:
            play(cell, header, blocks, plan_sha, backend, module, allow_send, run_dir, children, resets, focus)
        except (Aborted, ChildDied) as e:
            aborted, reason = True, str(e)
        except (EM.SendRefused, EM.ClockTooCoarse, EM.PlanError) as e:
            aborted, reason = True, "the emitter refused to run: %s" % e
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            aborted, reason = True, "%s: %s" % (type(e).__name__, e)
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                aborted, reason, pending = True, "interrupted (%s)" % type(e).__name__, e
            else:
                aborted, reason = True, "unexpected error (%s)" % type(e).__name__
                _record_error(run_dir)
        stop_interrupt = _stop_children(children, cell["stop_wait_ms"] / 1000.0, notes)
        if pending is None:
            pending = stop_interrupt
            if pending is not None:
                aborted, reason = True, reason or "interrupted (%s)" % type(pending).__name__
        ended = _clocks()
        if not aborted:
            for c, name in (("logger", "obs"), ("refinput", "gt")):
                if sha256_and_lines(os.path.join(run_dir, LOG_FILES[name]))[0] is None:
                    aborted, reason = True, "%s wrote no output" % c
                    break
        if notes and reason is not None:
            reason = "%s; %s" % (reason, "; ".join(notes))
        manifest = build_manifest(cell, plan_sha, run_dir, started, ended, children, resets, focus, aborted,
                                  reason, commit, dirty)
        try:
            write_manifest(run_dir, manifest)
        except OSError as e:
            print("run_cell: could not write the manifest: %s" % type(e).__name__, file=sys.stderr)
            if pending is not None:
                raise pending
            return 1
        if pending is not None:
            raise pending
    finally:
        signal.signal(signal.SIGTERM, previous)
    if aborted:
        print("run_cell: the run was aborted: %s" % reason, file=sys.stderr)
        return 1
    print("run_cell: wrote %s" % os.path.join(run_dir, MANIFEST_FILE))
    return 0


def run(args):
    run_dir = os.path.abspath(args.run_dir)
    try:
        cell = load_cell(args.cell)
        plan_sha, header, blocks = EM.read_plan(args.plan)
        with open(args.plan, "rb") as f:
            plan_bytes = f.read()
    except (CellError, EM.PlanError, OSError, UnicodeDecodeError) as e:
        print("run_cell: %s" % e, file=sys.stderr)
        return 2
    if cell["backend"] != "dryrun" and not args.send_flag:
        print("run_cell: backend %s sends real key events; it is not started without %s"
              % (cell["backend"], SEND_FLAG), file=sys.stderr)
        return 2
    if hashlib.sha256(plan_bytes).hexdigest() != plan_sha:
        print("run_cell: the plan file changed while it was being read", file=sys.stderr)
        return 2
    if cell["os"] != header["os"]:
        print("run_cell: the cell is for %s but the plan is for %s" % (cell["os"], header["os"]), file=sys.stderr)
        return 2
    try:
        module = EM.load_backend(cell["backend"])
    except (EM.PlanError, ValueError, OSError, SyntaxError, ImportError) as e:
        print("run_cell: backend %s could not be loaded: %s" % (cell["backend"], e), file=sys.stderr)
        return 2
    if cell["backend"] != "dryrun" and header["os"] != cell["backend"]:
        print("run_cell: the plan is for %s but the backend is %s" % (header["os"], cell["backend"]),
              file=sys.stderr)
        return 2
    keys = EM.plan_keys(blocks)
    unsupported = sorted(keys - set(module.SUPPORTED_KEYS))
    if unsupported:
        print("run_cell: keys not supported by backend %s: %s" % (cell["backend"], ", ".join(unsupported)),
              file=sys.stderr)
        return 2
    missing = EM.check_key_options(module, keys, cell["backend_opts"])
    if missing:
        print("run_cell: keys that need a backend option the cell does not give: %s" % ", ".join(missing),
              file=sys.stderr)
        return 2
    if cell["backend"] != "dryrun":
        option = getattr(module, "KEY_OPTIONS", {}).get("focus_back")
        if option is None or option not in cell["backend_opts"]:
            print("run_cell: backend %s needs the backend option %s so that the front window can be confirmed "
                  "before each block" % (cell["backend"], option or "for focus_back"), file=sys.stderr)
            return 2
    if os.name == "nt" and any(_is_batch_script(cell[k]) for k in ("logger_cmd", "refinput_cmd")):
        print("run_cell: a child command must be the program itself, not a .bat or .cmd wrapper (the process "
              "tree could not be ended reliably)", file=sys.stderr)
        return 2
    wall, mono = EM.clock_resolutions()
    if max(wall, mono) > EM.MAX_CLOCK_RESOLUTION_S:
        print("run_cell: the clocks resolve %.6f s (wall) and %.6f s (monotonic); at most %.3f s is required"
              % (wall, mono, EM.MAX_CLOCK_RESOLUTION_S), file=sys.stderr)
        return 2
    if os.path.exists(run_dir):
        print("run_cell: the run directory already exists; it is never reused", file=sys.stderr)
        return 2
    try:
        backend = module.Backend(cell["backend_opts"])
    except (ValueError, TypeError) as e:
        print("run_cell: %s" % e, file=sys.stderr)
        return 2
    if cell["backend"] != "dryrun" and not (cell["clock_sync_disabled"] and cell["auto_update_disabled"]):
        print("run_cell: note: the cell does not state that time synchronisation and automatic updates are off",
              file=sys.stderr)
    return execute(cell, header, blocks, plan_sha, plan_bytes, backend, module, args.send_flag, run_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure one cell: start the observers, play a plan, stop, and "
                                             "write the run directory.")
    ap.add_argument("--cell", help="cell file (JSON)")
    ap.add_argument("--plan", help="plan/1 file to play")
    ap.add_argument("--run-dir", help="run directory to create (must not exist)")
    ap.add_argument(SEND_FLAG, dest="send_flag", action="store_true",
                    help="consent to every operation that changes the machine: real key events and the reset "
                         "command")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.cell and args.plan and args.run_dir):
        ap.print_usage(sys.stderr)
        return 2
    return run(args)


# ------------------------------------------------------------------ self-test (dryrun, stub children, and a fake backend)

ZERO = "0" * 64
GT_HEADER = '{"kind":"header","format":"gt/1","src":"mac_nstic","app_version":"0.1.0","os":"macos"}'
OBS_HEADER = ('{"type":"meta","format":"obs/1","os":"macos","logger_version":"0.1.0","manifest_sha256":"%s",'
              '"capabilities":{"unconditional":["key"],"threshold_gated":[],"config_gated":["ime_shadow"]}}' % ZERO)
STUB_SOURCE = '''import signal, sys, time
out, header, mode, seconds = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
if mode == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode != "silent":
    with open(out, "w", encoding="utf-8", newline="\\n") as f:
        f.write(header + "\\n")
open(out + ".ready", "w").close()
time.sleep(seconds)
'''
READY_LIMIT_S = 30.0
ALL_STUB_KEYS = frozenset(["a", "f13", "f14", "focus_back", "focus_away"])


class FakeBackend(object):
    """A backend that records what it is given and sends nothing; it stands in for a real one in the self-test."""

    sent = []
    fail_on = None

    def __init__(self, opts):
        self.opts = opts

    def open(self):
        pass

    def send(self, key, down):
        FakeBackend.sent.append((key, down))
        if key == FakeBackend.fail_on:
            raise RuntimeError("injected failure")

    def close(self):
        pass


class FakeModule(object):
    SUPPORTED_KEYS = ALL_STUB_KEYS
    KEY_OPTIONS = {"focus_back": "focus_app"}
    Backend = FakeBackend


def _write_plan(path, first_gap_ms=0):
    rows = [{"kind": "header", "format": "plan/1", "os": "macos", "seed": 1, "generator_version": "0",
             "config_sha256": ZERO, "recipes_sha256": ZERO, "corpus_sha256": ZERO}]
    n = 0
    for b in range(2):
        rows.append({"kind": "block", "block_id": b, "speed": "mid", "reset_ime": b == 1,
                     "marker": ["f13", "f14"], "marker_gap_ms": 5})
        for _ in range(2):
            n += 1
            rows.append({"kind": "trial", "trial_id": "t%05d" % n, "block_id": b, "scenario": "S3", "speed": "mid",
                         "gap_before_ms": first_gap_ms if n == 1 else 20, "think_ms": 0, "phrase_id": "w1-1-1",
                         "reading_len": 1,
                         "steps": [{"key": "a", "at_ms": 0, "down": True}, {"key": "a", "at_ms": 30, "down": False}],
                         "intent": {"commit": "enter", "cand_index": 0, "nav_count": 0, "abandon": False,
                                    "predictive": False, "ime_toggle_mid": False, "focus_switch_mid": False,
                                    "expect_session": True}})
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(EM._dumps(r) + "\n")


def _cell(stub, over=None, logger_mode="normal", logger_seconds="60", refinput_mode="normal"):
    cell = {"os": "macos", "ime": "stub-ime 1.0", "ime_settings": {"layout": "stub"},
            "refinput_cmd": [sys.executable, stub, OUT_MARK, GT_HEADER, refinput_mode, "60"],
            "logger_cmd": [sys.executable, stub, OUT_MARK, OBS_HEADER, logger_mode, logger_seconds],
            "reset_cmd": [], "focus_cmd": [], "ready_wait_ms": 50, "backend": "dryrun",
            "clock_sync_disabled": True, "auto_update_disabled": True, "stop_wait_ms": 3000, "settle_ms": 0}
    cell.update(over or {})
    return cell


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f)


def _quiet_main(argv):
    """Run main() with its messages captured; the self-test reports results itself."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def _tool(*parts):
    return os.path.join(KIT, *parts)


def _tool_ok(args):
    return subprocess.run([sys.executable] + args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def _patient_ready(original):
    """Wait as the runner does, then until every stub child has said it is up (bounded), so that a slow start
    on a loaded machine does not fail a check."""
    names = {"logger": "obs", "refinput": "gt"}

    def wait(children, ms):
        original(children, ms)
        deadline = time.perf_counter() + READY_LIMIT_S
        for c in children:
            marker = os.path.join(c.run_dir, LOG_FILES[names[c.name]] + ".ready")
            while not os.path.exists(marker) and time.perf_counter() < deadline:
                check_children(children)
                time.sleep(0.02)

    return wait


@contextlib.contextmanager
def _patched(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield original
    finally:
        setattr(obj, name, original)


def selftest():
    global wait_ready
    failures = []
    started_at = time.perf_counter()

    def check(name, cond):
        print("%s %s" % ("ok  " if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    def exit_with(n):
        return [sys.executable, "-c", "import sys; sys.exit(%d)" % n]

    real_wait = wait_ready
    wait_ready = _patient_ready(real_wait)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _selftest_body(tmp, check, exit_with)
    finally:
        wait_ready = real_wait
    print("info self-test took %.1f s" % (time.perf_counter() - started_at))
    return 1 if failures else 0


def _selftest_body(tmp, check, exit_with):
    stub = os.path.join(tmp, "stub.py")
    with open(stub, "w", encoding="utf-8", newline="\n") as f:
        f.write(STUB_SOURCE)
    plan = os.path.join(tmp, "plan.jsonl")
    _write_plan(plan)
    cell_path = os.path.join(tmp, "cell.json")

    def go(name, cell, plan_path=None, flag=False):
        """Run one cell in the directory tmp/<name>; return (exit status, run directory)."""
        _write_json(cell_path, cell)
        run_dir = os.path.join(tmp, name)
        argv = ["--cell", cell_path, "--plan", plan_path or plan, "--run-dir", run_dir]
        if flag:
            argv.append(SEND_FLAG)
        return _quiet_main(argv), run_dir

    def manifest_of(run_dir):
        try:
            with open(os.path.join(run_dir, MANIFEST_FILE), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def oracle_rows(run_dir):
        with open(os.path.join(run_dir, LOG_FILES["oracle"]), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def resets_in(run_dir):
        return [r for r in oracle_rows(run_dir) if r["kind"] == "reset"]

    def pressed(run_dir):
        return [r for r in oracle_rows(run_dir) if r["kind"] in ("sent", "marker")]

    # a complete run of one cell
    rc, d = go("ok", _cell(stub))
    check("a dryrun cell with stub children exits 0", rc == 0)
    files = sorted(os.listdir(d)) if os.path.isdir(d) else []
    check("the run directory holds the four logs and the manifest",
          all(name in files for name in list(LOG_FILES.values()) + [MANIFEST_FILE]))
    m = manifest_of(d)
    check("the manifest is complete and not aborted",
          m.get("format") == RUN_FORMAT and m.get("aborted") is False and m.get("abort_reason") is None
          and m["cell"]["ime"] == "stub-ime 1.0" and m["plan_sha256"] == EM.read_plan(plan)[0])
    sums_ok = True
    for name in LOG_NAMES:
        with open(os.path.join(d, LOG_FILES[name]), "rb") as f:
            data = f.read()
        entry = m["outputs"][name]
        sums_ok = sums_ok and entry["sha256"] == hashlib.sha256(data).hexdigest() \
            and entry["lines"] == data.count(b"\n") and entry["file"] == LOG_FILES[name]
    check("the SHA-256 and the line count of every log match the files", sums_ok)
    with open(os.path.join(d, "plan.jsonl"), "rb") as f1, open(plan, "rb") as f2:
        check("plan.jsonl is a byte-for-byte copy of the plan", f1.read() == f2.read())
    check("the manifest holds both clocks at the start and at the end",
          all(isinstance(m[k][c], int) for k in ("started", "ended") for c in ("wall_ms", "mono_ns"))
          and m["ended"]["mono_ns"] >= m["started"]["mono_ns"])
    expected_commit = git_state()[0]
    check("the manifest records the commit (40 hexadecimal digits when git is usable) and the modified state",
          m["git_commit"] == expected_commit
          and (expected_commit is None or re.match(r"^[0-9a-f]{40}$", expected_commit) is not None)
          and (m["git_dirty"] is None) == (expected_commit is None or m["git_dirty"] is None)
          and (m["git_dirty"] is None or isinstance(m["git_dirty"], bool)))
    check("the manifest records the host",
          isinstance(m["host"].get("platform"), str) and isinstance(m["host"].get("python"), str)
          and ("windows" in m["host"] if os.name == "nt" else True))
    check("both children were stopped by a termination request, the last one started first",
          all(m["children"][n]["stopped"] == STOP_BY_REQUEST for n in ("logger", "refinput")))
    check("the gt log passes its validator", _tool_ok([_tool("formats", "validate_logs.py"), "--kind", "gt",
                                                      os.path.join(d, "gt.jsonl")]))
    check("the obs log passes its validator", _tool_ok([_tool("formats", "validate_logs.py"), "--kind", "obs",
                                                       os.path.join(d, "obs.jsonl")]))
    check("the oracle passes the emitter's validation",
          _tool_ok([_tool("emit", "emit.py"), "--validate", os.path.join(d, "oracle.jsonl")]))
    check("the plan passes the generator's validation",
          _tool_ok([_tool("gen", "generate_plan.py"), "--validate", os.path.join(d, "plan.jsonl")]))
    check("with an empty reset command the reset is skipped and no reset row is written",
          resets_in(d) == [] and len(m["resets"]) == 1 and m["resets"][0]["skipped"] and m["resets"][0]["block_id"] == 1
          and m["resets"][0]["exit_code"] is None)
    check("the settle time defaults to one second", check_stub_default_settle(stub))
    check("dryrun does not claim that the front window was confirmed",
          all(f["front_confirmed"] is False for f in m["focus"]))

    # the reset command: gated by the flag, and its exit status goes into the oracle
    marker = os.path.join(tmp, "reset-ran.txt")
    touch = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('x')", marker]
    rc, d = go("reset_gated", _cell(stub, {"reset_cmd": touch}))
    m = manifest_of(d)
    check("without the send flag the reset command is not run and no reset row is written",
          rc == 0 and not os.path.exists(marker) and resets_in(d) == []
          and m["resets"] == [{"block_id": 1, "exit_code": None, "skipped": m["resets"][0]["skipped"]}]
          and "flag" in m["resets"][0]["skipped"])
    rc, d = go("reset_flag", _cell(stub, {"reset_cmd": touch}), flag=True)
    check("with the send flag the reset command is run", rc == 0 and os.path.exists(marker))
    rc, d = go("reset", _cell(stub, {"reset_cmd": exit_with(3)}), flag=True)
    rows = resets_in(d) if rc == 0 else []
    m = manifest_of(d)
    check("the exit status of the reset command is written to the oracle",
          len(rows) == 1 and rows[0]["exit_code"] == 3 and rows[0]["block_id"] == 1
          and m["resets"] == [{"block_id": 1, "exit_code": 3, "failure": "exit_status"}])
    check("the reset command's standard error goes to a file", os.path.isfile(os.path.join(d, "reset.stderr.txt")))
    check("an oracle with a reset row passes the emitter's validation",
          _tool_ok([_tool("emit", "emit.py"), "--validate", os.path.join(d, "oracle.jsonl")]))
    rc, d = go("reset_ok", _cell(stub, {"reset_cmd": exit_with(0)}), flag=True)
    check("a reset that succeeds is recorded with status 0",
          rc == 0 and [r["exit_code"] for r in resets_in(d)] == [0] and manifest_of(d)["resets"][0]["failure"] is None)
    rc, d = go("reset_missing", _cell(stub, {"reset_cmd": [os.path.join(tmp, "no-such-command")]}), flag=True)
    check("a reset command that cannot start is recorded as failed, not fatal",
          rc == 0 and [r["exit_code"] for r in resets_in(d)] == [FAILED_TO_RUN]
          and manifest_of(d)["resets"][0]["failure"] == "could_not_start")
    rc, d = go("reset_slow", _cell(stub, {"reset_cmd": [sys.executable, "-c", "import time; time.sleep(30)"],
                                          "command_timeout_ms": 300}), flag=True)
    check("a reset command that times out is recorded as timed out",
          rc == 0 and manifest_of(d)["resets"][0]["failure"] == "timed_out")

    # the flag reaches the emitter
    seen = []
    real_emitter = EM.Emitter

    class SpyEmitter(real_emitter):
        def __init__(self, *a, **kw):
            seen.append(kw.get("allow_send"))
            real_emitter.__init__(self, *a, **kw)

    with _patched(EM, "Emitter", SpyEmitter):
        go("spy_off", _cell(stub))
        go("spy_on", _cell(stub), flag=True)
    check("allow_send is False without the flag and True with it", seen == [False, True])

    # a failing focus command stops the run before anything is sent
    rc, d = go("focus", _cell(stub, {"focus_cmd": exit_with(4)}), flag=True)
    m = manifest_of(d)
    check("a focus command that fails aborts the run before any key",
          rc == 1 and m.get("aborted") is True and "focus" in (m.get("abort_reason") or "")
          and m["focus"][0]["failure"] == "exit_status" and not pressed(d))

    # the focus command: gated by the flag, like the reset command (it may press a key)
    focus_marker = os.path.join(tmp, "focus-ran.txt")
    focus_touch = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('x')", focus_marker]
    rc, d = go("focus_gated", _cell(stub, {"focus_cmd": focus_touch}))
    m = manifest_of(d)
    check("without the flag the focus command is not run, and the manifest records it as skipped",
          rc == 0 and not os.path.exists(focus_marker) and all(f.get("skipped") for f in m["focus"])
          and all(f["exit_code"] is None for f in m["focus"]))
    rc, d = go("focus_flag", _cell(stub, {"focus_cmd": focus_touch}), flag=True)
    m = manifest_of(d)
    check("with the flag the focus command is run before each block",
          rc == 0 and os.path.exists(focus_marker) and all(f["exit_code"] == 0 and "skipped" not in f
                                                           for f in m["focus"]))

    # a real backend: the front window is confirmed before each block (a fake backend; nothing is sent)
    def with_fake(name, opts, fail_on=None, flag=True):
        FakeBackend.sent, FakeBackend.fail_on = [], fail_on
        with _patched(EM, "load_backend", lambda backend_name: FakeModule):
            return go(name, _cell(stub, {"backend": "macos", "backend_opts": opts}), flag=flag)

    rc, d = with_fake("front_missing", {})
    check("a real backend without the option of its focus_back key exits 2 and starts nothing",
          rc == 2 and not os.path.exists(d) and FakeBackend.sent == [])
    rc, d = with_fake("front_ok", {"focus_app": "stub.app"})
    seq = FakeBackend.sent
    first_marker = seq.index(("f13", True)) if ("f13", True) in seq else -1
    check("the front window is confirmed before each block, ahead of its first key",
          rc == 0 and seq[:2] == [("focus_back", True), ("focus_back", False)] and first_marker == 2
          and [k for k, _ in seq].count("focus_back") == 4
          and all(f["front_confirmed"] is True for f in manifest_of(d)["focus"]))
    rc, d = with_fake("front_fail", {"focus_app": "stub.app"}, fail_on="focus_back")
    m = manifest_of(d)
    check("a failed front-window confirmation aborts the block before any key",
          rc == 1 and m.get("aborted") is True and "front window" in (m.get("abort_reason") or "")
          and not pressed(d) and [k for k, _ in FakeBackend.sent] == ["focus_back"])

    # a child that dies is an aborted run
    slow = os.path.join(tmp, "slow.jsonl")
    _write_plan(slow, first_gap_ms=1500)
    rc, d = go("dead", _cell(stub, logger_seconds="0.3", over={"ready_wait_ms": 100}), plan_path=slow)
    m = manifest_of(d)
    check("a dead child process marks the run aborted",
          rc == 1 and m.get("aborted") is True and "logger" in (m.get("abort_reason") or ""))
    check("the other child is still stopped after an abort",
          m.get("children", {}).get("refinput", {}).get("stopped") == STOP_BY_REQUEST)
    check("no key is pressed after the child died",
          not [r for r in oracle_rows(d) if r["kind"] == "sent" and r["down"]])
    rc, d = go("silent", _cell(stub, logger_mode="silent"))
    m = manifest_of(d)
    check("a child that never writes its log marks the run aborted",
          rc == 1 and m.get("aborted") is True and "no output" in (m.get("abort_reason") or ""))
    if os.name == "posix":
        rc, d = go("stubborn", _cell(stub, logger_mode="ignore-term", over={"ready_wait_ms": 500,
                                                                           "stop_wait_ms": 300}))
        check("a child that ignores the termination request is killed and the manifest says so",
              rc == 0 and manifest_of(d)["children"]["logger"]["stopped"] == "killed"
              and manifest_of(d)["children"]["refinput"]["stopped"] == "terminated")
    else:
        print("info the kill path is checked on POSIX only (a forced end cannot be ignored on Windows)")

    # unexpected errors and interruptions
    class BrokenEmitter(real_emitter):
        def __enter__(self):
            raise ZeroDivisionError("injected")

    with _patched(EM, "Emitter", BrokenEmitter):
        rc, d = go("unexpected", _cell(stub))
    m = manifest_of(d)
    trace = os.path.join(d, ERROR_FILE)
    check("an unexpected error leaves a traceback file and a reason with the type",
          rc == 1 and m.get("abort_reason") == "unexpected error (ZeroDivisionError)" and os.path.isfile(trace)
          and "ZeroDivisionError" in open(trace, encoding="utf-8").read()
          and all(c["stopped"] is not None for c in m["children"].values()))

    class InterruptedEmitter(real_emitter):
        def __enter__(self):
            raise KeyboardInterrupt()

    raised = False
    with _patched(EM, "Emitter", InterruptedEmitter):
        try:
            go("interrupt", _cell(stub))
        except KeyboardInterrupt:
            raised = True
    m = manifest_of(os.path.join(tmp, "interrupt"))
    check("an interruption is recorded in the manifest and then raised again",
          raised and m.get("aborted") is True and m.get("abort_reason") == "interrupted (KeyboardInterrupt)")

    # paths that must not be deposited
    home = os.path.expanduser("~")
    if len(home.rstrip("/\\")) >= 3 and home != "~":
        slashed, backslashed = home.replace("\\", "/"), home.replace("/", "\\")
        note = "%s/sub %s\\sub" % (slashed, backslashed)
        rc, d = go("redact", _cell(stub, {"ime_settings": {"note": note},
                                          "reset_cmd": [sys.executable, "-c", "pass", home + "/x"]}), flag=True)
        with open(os.path.join(d, MANIFEST_FILE), encoding="utf-8") as f:
            text = f.read()
        leaks = [p for p in (home, slashed, backslashed, json.dumps(backslashed)[1:-1]) if p in text]
        check("no home directory path is left in the manifest",
              rc == 0 and not leaks and "~/sub" in text)
    else:
        print("info the redaction check was skipped (the home directory is too short to test)")
    redact = make_redactor("/home/alice", "/home/alice/work/repo")
    check("the repository root is replaced before the home directory, and a longer name is left alone",
          redact("/home/alice/work/repo/x /home/alice/y /home/alice2/z") == "<repo>/x ~/y /home/alice2/z")

    # refusals happen before any child is started
    keep = os.path.join(tmp, "existing")
    os.mkdir(keep)
    with open(os.path.join(keep, "keep.txt"), "w") as f:
        f.write("keep")
    _write_json(cell_path, _cell(stub))
    rc = _quiet_main(["--cell", cell_path, "--plan", plan, "--run-dir", keep])
    with open(os.path.join(keep, "keep.txt")) as f:
        kept = f.read()
    check("an existing run directory is not overwritten", rc == 2 and kept == "keep"
          and os.listdir(keep) == ["keep.txt"])
    loaded = []

    def sentinel_loader(name):
        loaded.append(name)
        raise AssertionError("load_backend must not be reached without the send flag")

    with _patched(EM, "load_backend", sentinel_loader):
        rc, d = go("real", _cell(stub, {"backend": "macos"}))
    check("a real backend without the send flag exits 2 and starts nothing",
          rc == 2 and loaded == [] and not os.path.exists(d))
    with _patched(EM, "clock_resolutions", lambda: (0.02, 1e-9)):
        rc, d = go("coarse", _cell(stub))
    check("a coarse clock exits 2 before any child is started", rc == 2 and not os.path.exists(d))
    real_read = EM.read_plan

    def read_wrong_sha(path):
        sha, header, blocks = real_read(path)
        return "1" * 64, header, blocks

    with _patched(EM, "read_plan", read_wrong_sha):
        rc, d = go("plan_changed", _cell(stub))
    check("a plan whose bytes differ from the ones that were checked exits 2", rc == 2 and not os.path.exists(d))
    check("a .bat or .cmd wrapper is recognised", _is_batch_script(["C:\\x\\run.BAT"]) and _is_batch_script(["a.cmd"])
          and not _is_batch_script(["prog.exe"]))
    if os.name == "nt":
        rc, d = go("wrapper", _cell(stub, {"logger_cmd": ["logger.cmd", OUT_MARK]}))
        check("a batch wrapper as a child command exits 2 and starts nothing", rc == 2 and not os.path.exists(d))
    bad_plan = os.path.join(tmp, "bad_plan.jsonl")
    with open(bad_plan, "w") as f:
        f.write("not a plan\n")
    rc, d = go("badplan", _cell(stub), plan_path=bad_plan)
    check("an invalid plan exits 2 and starts nothing", rc == 2 and not os.path.exists(d))
    cell = _cell(stub)
    del cell["reset_cmd"]
    rc, d = go("missing_key", cell)
    check("a cell with a missing key exits 2 and starts nothing", rc == 2 and not os.path.exists(d))
    rc, d = go("unknown_key", _cell(stub, {"rest_cmd": []}))
    check("a cell with an unknown key exits 2 and starts nothing", rc == 2 and not os.path.exists(d))
    rc, d = go("bad_label", _cell(stub, {"label": "two words"}))
    check("a cell label outside the allowed characters exits 2 and starts nothing",
          rc == 2 and not os.path.exists(d))
    check("a cell label of the allowed form is kept", check_cell(_cell(stub, {"label": "macos-live_on.1"}))["label"]
          == "macos-live_on.1")
    rc, d = go("no_out", _cell(stub, {"logger_cmd": [sys.executable, stub]}))
    check("a child command that never receives its log path exits 2", rc == 2 and not os.path.exists(d))
    rc, d = go("os_mismatch", _cell(stub, {"os": "windows"}))
    check("a cell whose os differs from the plan's exits 2", rc == 2 and not os.path.exists(d))
    check("no argument at all exits 2", _quiet_main([]) == 2)


def check_stub_default_settle(stub):
    cell = _cell(stub)
    del cell["settle_ms"]
    return check_cell(cell)["settle_ms"] == 1000


if __name__ == "__main__":
    sys.exit(main())
