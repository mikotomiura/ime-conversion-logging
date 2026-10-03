#!/usr/bin/env python3
# kit: selftest
"""Starts ``run_cell.py`` for a Windows cell and brings the reference input field to the front while the runner
waits for its children (Python standard library only).

  start_windows.py --cell CELL.json --plan PLAN.jsonl --run-dir runs/<name> [--i-understand-this-sends-keys]
  start_windows.py --selftest     check the sequence against stubs; nothing is started and nothing is sent

Why: when the emitter opens, it brings the ``focus_window`` window to the front with ``SetForegroundWindow`` and
stops the run when the window is not in front (``../emit/backend_windows.py``). Windows refuses that call to a
process that is not in the foreground (the foreground lock); in the calibration pilot it was refused when the runner
was started from a shell in the background, and no key was sent. This program therefore watches for the window of
the reference input field (the cell's ``backend_opts.focus_window``) during the runner's ``ready_wait_ms`` and,
as soon as exactly one such window is visible, brings it forward in the way ``focus_windows.py`` does (with
``AttachThreadInput`` when the lock refuses), reading the foreground window back after each of up to five
attempts. The runner itself, the emitter and their checks are unchanged: when the window is not in front the
emitter's read-back still stops the run before any key is sent.

When the cell names an ``away_window`` (scenario S15) and no visible window carries that title, the program starts
``away_windows.py`` with that title before the runner and closes it after the runner has ended.

Without ``--i-understand-this-sends-keys`` the arguments are passed to the runner unchanged and no window is
started or brought forward (the runner then refuses a real backend). The exit status is the runner's; 2 when the
cell cannot be read or the window to bring forward is not named, or when the away window does not appear.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import focus_windows  # noqa: E402

CONSENT = "--i-understand-this-sends-keys"
WATCH_EXTRA_S = 30.0   # watched beyond ready_wait_ms (the children may take long to start)
POLL_S = 0.1
SETTLE_S = 0.5         # wait after the window appears, before it is brought forward
TRIES = 5
TRY_GAP_S = 0.3
AWAY_WAIT_S = 10.0


def away_windows_default():
    import away_windows
    return away_windows.DEFAULT_TITLE


def read_cell(path):
    with open(path, encoding="utf-8") as f:
        cell = json.load(f)
    opts = cell.get("backend_opts") or {}
    title = opts.get("focus_window")
    if not isinstance(title, str) or not title:
        raise ValueError("the cell names no backend_opts.focus_window")
    away = opts.get("away_window")
    ready = cell.get("ready_wait_ms", 0)
    return title, away if isinstance(away, str) and away else None, ready / 1000.0 if isinstance(ready, int) else 0.0


def bring_when_shown(api, title, child, limit_s, sleep=time.sleep, clock=time.monotonic):
    """Wait for exactly one window titled ``title`` while ``child`` runs; return True when it was brought forward."""
    t0 = clock()
    while child.poll() is None and clock() - t0 < limit_s:
        found = api.find_windows(title)
        if len(found) == 1:
            sleep(SETTLE_S)
            for _ in range(TRIES):
                focus_windows.bring(api, found[0])
                sleep(TRY_GAP_S)
                if api.foreground() == found[0]:
                    return True
            return False
        sleep(POLL_S)
    return False


def start_away(api, title, spawn, sleep=time.sleep, clock=time.monotonic):
    """Start the away window when none is shown; return the child (or None when one was already shown)."""
    shown = api.find_windows(title)
    if len(shown) > 1:
        raise RuntimeError("%d visible windows carry the away title %r (exactly one is required)" % (len(shown), title))
    if shown:
        return None
    child = spawn([sys.executable, os.path.join(HERE, "away_windows.py"), "--title", title])
    try:
        t0 = clock()
        while clock() - t0 < AWAY_WAIT_S:
            if len(api.find_windows(title)) == 1:
                return child
            if child.poll() is not None:
                break
            sleep(POLL_S)
    except BaseException:
        stop(child)
        raise
    stop(child)
    raise RuntimeError("the away window %r did not appear" % title)


def stop(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def launch(argv, api_factory, spawn, sleep=time.sleep, clock=time.monotonic, err=sys.stderr):
    ap = argparse.ArgumentParser(prog="start_windows.py")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument(CONSENT, dest="consent", action="store_true")
    a = ap.parse_args(argv)
    runner = [sys.executable, os.path.join(HERE, "run_cell.py"), "--cell", a.cell, "--plan", a.plan,
              "--run-dir", a.run_dir] + ([CONSENT] if a.consent else [])
    if not a.consent:
        return spawn(runner).wait()
    try:
        title, away, ready_s = read_cell(a.cell)
    except (OSError, ValueError) as e:
        err.write("start_windows.py: %s\n" % e)
        return 2
    api = api_factory()
    try:
        away_child = start_away(api, away, spawn, sleep, clock) if away else None
    except RuntimeError as e:
        err.write("start_windows.py: %s\n" % e)
        return 2
    child = None
    try:
        child = spawn(runner)
        shown = bring_when_shown(api, title, child, ready_s + WATCH_EXTRA_S, sleep, clock)
        err.write("start_windows.py: reference input field brought to the front: %s\n" % ("yes" if shown else "no"))
        return child.wait()
    except BaseException:
        # the runner is stopped before the away window, so that no block runs without its S15 target; the runner
        # then records the run as aborted
        stop(child)
        raise
    finally:
        stop(away_child)


class _Child(object):
    def __init__(self, cmd, polls_alive=10**6, code=0):
        self.cmd, self.alive, self.code, self.terminated = cmd, polls_alive, code, False

    def poll(self):
        if self.terminated:
            return -15
        if self.alive > 0:
            self.alive -= 1
            return None
        return self.code

    def wait(self, timeout=None):
        return -15 if self.terminated else self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class _Api(focus_windows._Stub):
    """A stub whose windows appear after ``appear_after`` lookups of each title."""

    def __init__(self, appear_after=None, **kw):
        focus_windows._Stub.__init__(self, **kw)
        self.appear_after, self.seen = dict(appear_after or {}), {}

    def find_windows(self, title):
        self.calls.append(("find", title))
        self.seen[title] = self.seen.get(title, 0) + 1
        return [len(title)] if self.seen[title] > self.appear_after.get(title, 0) else []


def selftest():
    import tempfile
    fails = []

    def check(name, ok):
        print("  %s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp()
    cell = os.path.join(tmp, "cell.json")
    with open(cell, "w", encoding="utf-8") as f:
        json.dump({"ready_wait_ms": 3000, "backend_opts": {"focus_window": "field", "away_window": "away"}}, f)
    tick = [0.0]
    sleep = lambda s: tick.__setitem__(0, tick[0] + s)
    clock = lambda: tick[0]
    base = ["--cell", cell, "--plan", "p.jsonl", "--run-dir", "runs/x"]

    def run(argv, api, children):
        def spawn(cmd):
            c = _Child(cmd, code=1 if "run_cell.py" in cmd[1] else 0)
            children.append(c)
            return c
        tick[0] = 0.0
        return launch(argv, lambda: api, spawn, sleep, clock, err=io.StringIO())

    api, ch = _Api(), []
    st = run(base, api, ch)
    check("without the consent flag only the runner is started, unchanged, and no window is touched",
          st == 1 and len(ch) == 1 and CONSENT not in ch[0].cmd and ch[0].cmd[1].endswith("run_cell.py")
          and not api.calls)
    api, ch = _Api(appear_after={"field": 3}), []
    st = run(base + [CONSENT], api, ch)
    check("with the flag the field is brought forward once its window appears, and the runner's status is returned",
          st == 1 and ("set_foreground", len("field")) in api.calls and ch[-1].cmd[-1] == CONSENT)
    check("an away window that is already shown is not started again", len(ch) == 1)
    api, ch = _Api(appear_after={"away": 1}), []
    run(base + [CONSENT], api, ch)
    check("a missing away window is started before the runner and closed after it",
          len(ch) == 2 and ch[0].cmd[1].endswith("away_windows.py") and ch[0].cmd[-1] == "away"
          and ch[0].terminated and not ch[1].terminated)
    api, ch = _Api(appear_after={"away": 10**6}), []
    check("an away window that does not appear stops before the runner",
          run(base + [CONSENT], api, ch) == 2 and len(ch) == 1 and ch[0].terminated)

    class _Failing(_Api):
        def find_windows(self, title):
            if title == "field":
                raise RuntimeError("EnumWindows failed")
            return _Api.find_windows(self, title)

    api, ch = _Failing(appear_after={"away": 1}), []
    try:
        run(base + [CONSENT], api, ch)
        raised = False
    except RuntimeError:
        raised = True
    check("an error while watching for the field stops the runner and then the away window",
          raised and len(ch) == 2 and ch[0].terminated and ch[1].terminated)

    class _Two(_Api):
        def find_windows(self, title):
            return [1, 2] if title == "away" else _Api.find_windows(self, title)

    api, ch = _Two(), []
    check("two windows with the away title are refused before anything starts",
          run(base + [CONSENT], api, ch) == 2 and not ch)
    api, ch = _Api(appear_after={"field": 10**6}), []
    tick[0] = 0.0
    child = _Child(["run_cell.py"])
    shown = bring_when_shown(api, "field", child, 33.0, sleep, clock)
    check("a field that never appears is not brought forward, and the watch ends", not shown and 33.0 <= tick[0] < 34.0)
    api = _Api(stuck=True)
    tick[0] = 0.0
    check("a field that stays behind is reported after five attempts",
          not bring_when_shown(api, "field", _Child(["x"]), 33.0, sleep, clock)
          and len([c for c in api.calls if c[0] == "set_foreground"]) >= TRIES)
    with open(cell, "w", encoding="utf-8") as f:
        json.dump({"backend_opts": {}}, f)
    api, ch = _Api(), []
    check("a cell that names no focus_window is refused before anything starts",
          run(base + [CONSENT], api, ch) == 2 and not ch)
    # the Windows cell of this directory: accepted by the runner, and one title in its three places
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_cell", os.path.join(HERE, "run_cell.py"))
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    try:
        wc = rc.load_cell(os.path.join(HERE, "cell.windows.json"))
        accepted = True
    except rc.CellError:
        wc, accepted = {}, False
    check("cell.windows.json is accepted by the runner", accepted)
    title = wc.get("backend_opts", {}).get("focus_window")
    fc = wc.get("focus_cmd", [])
    check("cell.windows.json: focus_cmd runs focus_windows.py on the focus_window title and switches the input "
          "method on, and the reference input field is the TSF one",
          wc.get("os") == "windows" and wc.get("backend") == "windows" and title == "refinput_tsf (gt/1 win_tsf)"
          and len(fc) == 5 and fc[1].endswith("focus_windows.py") and fc[2:] == ["--title", title, "--ime-on"]
          and wc["refinput_cmd"][0].endswith("refinput_tsf.exe")
          and wc["backend_opts"].get("away_window") == away_windows_default())
    print("selftest %s (%d failed)" % ("OK" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    if sys.platform != "win32":
        sys.stderr.write("start_windows.py: Windows only\n")
        return 2
    return launch(argv, focus_windows.Api, lambda cmd: subprocess.Popen(cmd))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
