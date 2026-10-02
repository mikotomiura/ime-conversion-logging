#!/usr/bin/env python3
# kit: selftest
"""Windows backend: posts synthetic keyboard events through ``user32!SendInput``.

Character keys are sent as hardware scan codes (``KEYEVENTF_SCANCODE``), so the input method sees the same
event stream as a physical keyboard. Arrow keys are extended keys (``KEYEVENTF_EXTENDEDKEY``). The two
input-method keys and the function keys F13-F19 have no reliable scan code and are sent as virtual keys;
``ime_on`` and ``ime_off`` are ``VK_IME_ON`` (0x16) and ``VK_IME_OFF`` (0x1A), and whether a given input method
honours them has to be confirmed in a pilot before the key map is relied upon. Events go to whichever window
has the keyboard focus; a process cannot inject into a window of a higher integrity level (UIPI).

Virtual-key events carry ``wScan`` = 0. The observer of this kit classifies keys by their virtual key code
(``is_observed_key(vk_code)`` in ``kit/logger/src/driver_windows/keyboard.rs``); how the virtual key of a key that
was sent as a scan code appears in a low-level hook has to be confirmed in a pilot.

  backend_windows.py --probe      load user32 and winmm, build one INPUT record; nothing is sent
  backend_windows.py --selftest   check the key map, the structure layout and the call sequence against a
                                  stub; nothing is sent

The libraries are loaded lazily, so importing this module on another operating system is harmless and the
self-test reports "not applicable" there.

Timer resolution: the default system timer tick is 15.6 ms, which is coarser than the inter-key intervals of
a plan. ``open`` therefore requests a 1 ms resolution with ``winmm!timeBeginPeriod(1)`` and ``close``
restores it with ``timeEndPeriod(1)``; the driver calls ``close`` in a ``finally`` block. On Windows 11 the
resolution is not guaranteed while the process that owns a window is minimised or hidden, so the driver should
be run from a window that stays visible.

Backend options (``--backend-opt k=v``):
  focus_window=<title>   required; top-level window that must be in front when a key is pressed, and the window
                         brought to the front by the ``focus_back`` pseudo-key
  away_window=<title>    optional; top-level window brought to the front by ``focus_away`` (needed only by a
                         plan that uses ``focus_away``)

A window is identified by its title: the visible top-level windows are enumerated and exactly one must have a
title equal to the given one (case-sensitive, whole string). Zero or several matches stop the run. The title
must therefore be unique and must not change while the plan runs. A minimised window is restored before it is
brought to the front.

Foreground checks: ``open`` brings the ``focus_window`` window to the front and reads the foreground window
back before the timer is started; it stops the run when the window is not in front. Immediately before every
key press (a key-down other than a pseudo-key) the foreground window is read again and the key is not sent
when it is not the ``focus_window`` window. Key releases and the releases issued by ``close`` are not checked,
so that no key is left held. After ``focus_away`` no key is pressed until ``focus_back`` has brought the window
back (the plan is expected to be ``focus_away``, a wait, ``focus_back``, then the next key). After every
activation the backend reads the foreground window back and stops the run when it is not the requested one.
``SetForegroundWindow`` can be refused by the foreground lock (it then returns 0); the read-back stops the run
and the return value is reported in the message.

The backend refuses to open when the process runs with administrator rights, because a child process would be
elevated as well and a leaked key would reach an elevated console.

``SendInput`` returns the number of events inserted. An event that is dropped because of UIPI cannot be told
apart by the return value or by ``GetLastError``; it appears as a disagreement between the oracle and the
observer output.
"""
import ctypes
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
TIMER_PERIOD_MS = 1
PSEUDO_KEYS = frozenset(["focus_away", "focus_back"])
KEY_OPTIONS = {"focus_away": "away_window", "focus_back": "focus_window"}
FRONT_CHECKS = 5          # how many times the foreground window is read back after an activation
FRONT_WAIT_S = 0.2        # pause between two readings
MAX_TITLE = 256
SW_RESTORE = 9


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUT_UNION)]


def _load_keymap():
    with open(os.path.join(HERE, "keymap_windows.json"), encoding="utf-8") as f:
        return json.load(f)


KEYMAP = _load_keymap()
SUPPORTED_KEYS = frozenset(KEYMAP) | PSEUDO_KEYS


def make_input(key, down):
    """Build the INPUT record for one press or release of ``key`` (a name in the key map)."""
    entry = KEYMAP[key]
    flags = 0 if down else KEYEVENTF_KEYUP
    vk = scan = 0
    if "scan" in entry:
        scan = entry["scan"]
        flags |= KEYEVENTF_SCANCODE
        if entry.get("ext"):
            flags |= KEYEVENTF_EXTENDEDKEY
    else:
        vk = entry["vk"]
    rec = INPUT()
    rec.type = INPUT_KEYBOARD
    rec.ki.wVk = vk
    rec.ki.wScan = scan
    rec.ki.dwFlags = flags
    return rec




class _Api(object):
    """Thin ctypes wrapper over the few user32 / winmm / shell32 calls that are needed."""

    def __init__(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._winmm = ctypes.WinDLL("winmm", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        u = self._user32
        u.SendInput.restype = ctypes.c_uint
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SetForegroundWindow.restype = ctypes.c_int
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.GetForegroundWindow.argtypes = []
        u.IsWindowVisible.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u.IsIconic.restype = ctypes.c_int
        u.IsIconic.argtypes = [ctypes.c_void_p]
        u.ShowWindow.restype = ctypes.c_int
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        self._enum_proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        u.EnumWindows.restype = ctypes.c_int
        u.EnumWindows.argtypes = [self._enum_proc, ctypes.c_void_p]
        self._winmm.timeBeginPeriod.restype = ctypes.c_uint
        self._winmm.timeBeginPeriod.argtypes = [ctypes.c_uint]
        self._winmm.timeEndPeriod.restype = ctypes.c_uint
        self._winmm.timeEndPeriod.argtypes = [ctypes.c_uint]
        self._shell32.IsUserAnAdmin.restype = ctypes.c_int
        self._shell32.IsUserAnAdmin.argtypes = []

    def send_input(self, rec):
        return self._user32.SendInput(1, ctypes.byref(rec), ctypes.sizeof(INPUT))

    def last_error(self):
        return ctypes.get_last_error()

    def time_begin(self, ms):
        return self._winmm.timeBeginPeriod(ms)

    def time_end(self, ms):
        return self._winmm.timeEndPeriod(ms)

    def find_windows(self, title):
        """Handles of the visible top-level windows whose title equals ``title`` (case-sensitive)."""
        found = []
        buf = ctypes.create_unicode_buffer(MAX_TITLE + 2)

        def visit(hwnd, lparam):
            if self._user32.IsWindowVisible(hwnd):
                n = self._user32.GetWindowTextW(hwnd, buf, len(buf))
                if n and buf.value == title:
                    found.append(hwnd)
            return 1

        if not self._user32.EnumWindows(self._enum_proc(visit), None):
            raise RuntimeError("EnumWindows failed (GetLastError %d)" % ctypes.get_last_error())
        return found

    def is_iconic(self, hwnd):
        return bool(self._user32.IsIconic(hwnd))

    def restore(self, hwnd):
        self._user32.ShowWindow(hwnd, SW_RESTORE)

    def set_foreground(self, hwnd):
        return self._user32.SetForegroundWindow(hwnd)

    def foreground(self):
        return self._user32.GetForegroundWindow()

    def is_admin(self):
        return bool(self._shell32.IsUserAnAdmin())


class Backend(object):
    def __init__(self, opts, api=None):
        unknown = sorted(set(opts) - {"focus_window", "away_window"})
        if unknown:
            raise ValueError("unknown backend option(s) for windows: %s" % ", ".join(unknown))
        self._focus_window = opts.get("focus_window")
        self._away_window = opts.get("away_window")
        if self._focus_window is None:
            raise ValueError("the backend option focus_window=<window title> is required")
        for name, value in (("focus_window", self._focus_window), ("away_window", self._away_window)):
            if value is not None and (not value or len(value) > MAX_TITLE
                                      or any(ord(ch) < 32 for ch in value)):
                raise ValueError("%s is not a usable window title" % name)
        self._injected = api        # kept for every open; close never discards it
        self._api = None            # set while open
        self._front = None          # handle of the focus_window window
        self._timer = False
        self._held = set()
        self._sleep = time.sleep

    def open(self):
        if self._api is not None:
            raise RuntimeError("the backend is already open")
        api = self._injected or _Api()
        if api.is_admin():
            raise RuntimeError("refusing to run with administrator rights: a child process would be "
                               "elevated too and a leaked key could reach an elevated console")
        self._api = api
        try:
            self._front = self._activate("focus_window", self._focus_window)
            if api.time_begin(TIMER_PERIOD_MS) != 0:
                raise RuntimeError("timeBeginPeriod(%d) was refused" % TIMER_PERIOD_MS)
            self._timer = True
        except BaseException:
            self._api = None
            self._front = None
            raise

    def _post(self, key, down):
        sent = self._api.send_input(make_input(key, down))
        if sent != 1:
            raise RuntimeError("SendInput accepted %d of 1 events (GetLastError %d)"
                               % (sent, self._api.last_error()))

    def send(self, key, down):
        if key in PSEUDO_KEYS:
            if down:
                self._focus(key)
            return
        if down:
            if self._api.foreground() != self._front:
                raise RuntimeError("the foreground window changed; %s is not sent" % key)
            self._held.add(key)      # recorded before sending, so that close releases it even if the send fails
        self._post(key, down)
        if not down:
            self._held.discard(key)

    def _focus(self, key):
        if key == "focus_away":
            self._activate(key, self._away_window)
        else:
            self._front = self._activate(key, self._focus_window)

    def _activate(self, key, title):
        """Bring the one window titled ``title`` to the front and read it back; return its handle."""
        if title is None:
            raise RuntimeError("%s needs the backend option %s=<window title>" % (key, KEY_OPTIONS[key]))
        found = self._api.find_windows(title)
        if len(found) != 1:
            raise RuntimeError("%d visible top-level windows have the title given for %s; exactly one is "
                               "required" % (len(found), key))
        hwnd = found[0]
        if self._api.is_iconic(hwnd):
            self._api.restore(hwnd)
        result = self._api.set_foreground(hwnd)
        for i in range(FRONT_CHECKS):
            if i:
                self._sleep(FRONT_WAIT_S)
            if self._api.foreground() == hwnd:
                return hwnd
        raise RuntimeError("the foreground window is not the one requested by %s (SetForegroundWindow "
                           "returned %s); stopping before any further key is sent" % (key, result))

    def close(self):
        api, self._api = self._api, None
        if api is None:
            return
        first = None
        try:
            for key in sorted(self._held):
                try:
                    if api.send_input(make_input(key, False)) != 1:
                        raise RuntimeError("SendInput did not accept the release of %s" % key)
                except Exception as e:      # every held key gets its release attempt
                    if first is None:
                        first = e
            self._held.clear()
        finally:
            if self._timer:
                self._timer = False
                api.time_end(TIMER_PERIOD_MS)
        if first is not None:
            raise first


def probe():
    if sys.platform != "win32":
        print("probe: not applicable on this operating system")
        return 0
    _Api()
    rec = make_input("a", True)
    print("probe: user32 and winmm loaded, one INPUT record built (%d bytes), nothing sent" % ctypes.sizeof(rec))
    return 0


class _StubApi(object):
    def __init__(self, accept=1, timer_result=0):
        self.sent = []
        self.timer = []
        self.events = []
        self.set_calls = []
        self._accept = accept
        self._timer_result = timer_result
        self.windows = {}       # title -> list of handles
        self.iconic = set()
        self.admin = False
        self.refuse = False     # SetForegroundWindow leaves the foreground window unchanged
        self.front = None

    def send_input(self, rec):
        self.sent.append((rec.ki.wVk, rec.ki.wScan, rec.ki.dwFlags))
        return self._accept

    def last_error(self):
        return 5

    def time_begin(self, ms):
        self.timer.append(("begin", ms))
        return self._timer_result

    def time_end(self, ms):
        self.timer.append(("end", ms))
        return 0

    def find_windows(self, title):
        return list(self.windows.get(title, []))

    def is_iconic(self, hwnd):
        return hwnd in self.iconic

    def restore(self, hwnd):
        self.iconic.discard(hwnd)
        self.events.append(("restore", hwnd))

    def set_foreground(self, hwnd):
        self.events.append(("front", hwnd))
        self.set_calls.append(hwnd)
        if self.refuse:
            return 0
        self.front = hwnd
        return 1

    def foreground(self):
        return self.front

    def is_admin(self):
        return self.admin


def _forbidden_api():
    raise AssertionError("the real API was constructed during the self-test")


def keymap_matches(keymap, names):
    """True when ``keymap`` has an entry for exactly the plain key names of the plan format."""
    return set(keymap) == set(names) - {"mouse_click"} - PSEUDO_KEYS


def _load_core():
    spec = importlib.util.spec_from_file_location("emit_core", os.path.join(HERE, "emit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


def _opened(stub, opts=None, titles=None):
    """A backend over ``stub`` with the focus window (handle 11) in front after open."""
    stub.windows.setdefault("Editor", [11])
    b = Backend(opts or {"focus_window": "Editor"}, api=stub)
    b._sleep = lambda s: None
    b.open()
    return b


def _checks(check):
    core = _load_core()
    check("key map covers exactly the plain key names of the plan format", keymap_matches(KEYMAP, core.KEYNAMES))
    check("the coverage check is false for a map that lacks one key",
          not keymap_matches({k: v for k, v in KEYMAP.items() if k != "period"}, core.KEYNAMES))
    check("the supported keys are the plain key names plus the pseudo-keys",
          SUPPORTED_KEYS == (core.KEYNAMES - {"mouse_click"}))
    check("every entry has a scan code or a virtual key, not both",
          all((("scan" in v) != ("vk" in v)) and set(v) <= {"scan", "vk", "ext"} for v in KEYMAP.values()))
    check("scan and vk are integers and not booleans; ext is a boolean set only together with scan",
          all(type(v.get("scan", v.get("vk"))) is int and ("ext" not in v or ("scan" in v and v["ext"] is True))
              for v in KEYMAP.values()))
    scans = [v["scan"] for v in KEYMAP.values() if "scan" in v]
    check("scan codes are distinct bytes", all(0 < s < 128 for s in scans) and
          len(set((v["scan"], bool(v.get("ext"))) for v in KEYMAP.values() if "scan" in v)) == len(scans))
    check("the input-method keys are the virtual keys 0x16 and 0x1A",
          KEYMAP.get("ime_on") == {"vk": 0x16} and KEYMAP.get("ime_off") == {"vk": 0x1A})
    check("arrow keys are extended keys", all(KEYMAP[k].get("ext") for k in ("left", "right", "up", "down")))
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    check("INPUT has the size the operating system expects (%d)" % expected, ctypes.sizeof(INPUT) == expected)

    press, release = make_input("a", True), make_input("a", False)
    check("a character key is a scan-code press then a keyup",
          (press.ki.wVk, press.ki.wScan, press.ki.dwFlags) == (0, 30, KEYEVENTF_SCANCODE)
          and release.ki.dwFlags == KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP and press.type == INPUT_KEYBOARD)
    up = make_input("up", True)
    check("an arrow key carries the extended flag", up.ki.dwFlags == KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY)
    check("an arrow key release carries the scan-code, extended and keyup flags",
          make_input("up", False).ki.dwFlags == KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
    ime = make_input("ime_on", True)
    check("a virtual key is sent without the scan-code flag and with wScan 0",
          (ime.ki.wVk, ime.ki.wScan, ime.ki.dwFlags) == (0x16, 0, 0))
    ime_up = make_input("ime_on", False)
    check("a virtual key release carries only the keyup flag",
          (ime_up.ki.wVk, ime_up.ki.wScan, ime_up.ki.dwFlags) == (0x16, 0, KEYEVENTF_KEYUP))

    stub = _StubApi()
    b = _opened(stub)
    b.send("a", True)
    b.send("a", False)
    b.close()
    check("open and close pair the timer request", stub.timer == [("begin", 1), ("end", 1)])
    check("a key is sent as a press then a release",
          stub.sent == [(0, 30, KEYEVENTF_SCANCODE), (0, 30, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)])

    stub = _StubApi()
    b = _opened(stub)
    b.send("shift", True)
    b.close()
    check("close releases a key that is still held",
          stub.sent[-1] == (0, KEYMAP["shift"]["scan"], KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))

    stub = _StubApi(accept=0)
    b = _opened(stub)
    check("a send that SendInput does not accept raises", _raises(RuntimeError, lambda: b.send("a", True)))
    try:
        b.send("b", True)
    except RuntimeError as e:
        check("the SendInput failure message reports the last error code", "GetLastError 5" in str(e))
    try:
        b.close()
    except RuntimeError:
        pass
    check("the timer request is undone even when the release fails", stub.timer == [("begin", 1), ("end", 1)])

    stub = _StubApi(timer_result=97)
    b = Backend({"focus_window": "Editor"}, api=stub)
    stub.windows["Editor"] = [11]
    check("a refused timer request stops the run", _raises(RuntimeError, b.open))
    b.close()
    check("close after a refused timer request does not end a period that was never begun",
          stub.timer == [("begin", 1)])

    # opening and reuse
    stub = _StubApi()
    b = _opened(stub)
    check("a second open before close is refused", _raises(RuntimeError, b.open))
    b.send("a", True)
    b.send("a", False)
    b.close()
    b.open()
    b.send("a", True)
    b.send("a", False)
    b.close()
    check("close then open keeps using the injected api",
          stub.timer == [("begin", 1), ("end", 1), ("begin", 1), ("end", 1)] and len(stub.sent) == 4)

    # focus_window is required
    check("a missing focus_window is refused", _raises(ValueError, lambda: Backend({}, api=_StubApi())))
    check("away_window alone is refused", _raises(ValueError, lambda: Backend({"away_window": "Other"})))

    # activation at open
    stub = _StubApi()
    stub.windows = {"Editor": [11]}
    stub.iconic = {11}
    stub.front = 5
    b = Backend({"focus_window": "Editor"}, api=stub)
    b.open()
    check("open restores a minimised window before it brings it to the front",
          stub.events == [("restore", 11), ("front", 11)] and stub.front == 11)
    b.close()

    stub = _StubApi()
    stub.windows = {"Editor": [11]}
    stub.refuse = True
    stub.front = 5
    sleeps = []
    b = Backend({"focus_window": "Editor"}, api=stub)
    b._sleep = sleeps.append
    try:
        b.open()
        check("open stops when the window is not in front after activation", False)
    except RuntimeError as e:
        check("open stops when the window is not in front after activation",
              len(sleeps) == FRONT_CHECKS - 1 and "returned 0" in str(e))
    check("open that fails the front check does not start the timer", stub.timer == [])
    b.close()
    check("close after a failed open ends no timer period", stub.timer == [])

    stub = _StubApi()
    stub.windows = {"Editor": [11, 12]}
    b = Backend({"focus_window": "Editor"}, api=stub)
    check("open refuses a title shared by two windows", _raises(RuntimeError, b.open))
    check("a refused title starts no timer and activates nothing", stub.timer == [] and stub.events == [])
    b = Backend({"focus_window": "Missing"}, api=stub)
    check("open refuses a title that matches no window", _raises(RuntimeError, b.open))

    stub = _StubApi()
    stub.windows = {"Editor": [11]}
    stub.admin = True
    b = Backend({"focus_window": "Editor"}, api=stub)
    check("open refuses to run with administrator rights", _raises(RuntimeError, b.open))
    check("the administrator refusal happens before anything is activated or timed",
          stub.timer == [] and stub.events == [])

    # foreground check before each press
    stub = _StubApi()
    b = _opened(stub)
    stub.front = 99
    check("a press is refused when the foreground window has changed", _raises(RuntimeError, lambda: b.send("a", True)))
    check("a refused press sends nothing", stub.sent == [])
    stub.front = 11
    b.send("a", True)
    b.send("a", False)
    check("a press is sent while the window is in front", len(stub.sent) == 2)
    b.close()

    stub = _StubApi()
    b = _opened(stub)
    b.send("shift", True)
    stub.front = 99
    b.send("shift", False)
    check("a release is sent even when the foreground window has changed",
          stub.sent[-1] == (0, KEYMAP["shift"]["scan"], KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP) and len(stub.sent) == 2)
    b.close()

    stub = _StubApi()
    b = _opened(stub)
    b.send("shift", True)
    stub.front = 99
    b.close()
    check("close releases a held key without checking the foreground window",
          len(stub.sent) == 2 and stub.sent[-1][2] & KEYEVENTF_KEYUP == KEYEVENTF_KEYUP)

    # pseudo-keys
    stub = _StubApi()
    stub.windows = {"Editor": [11], "Other": [22]}
    b = _opened(stub, {"focus_window": "Editor", "away_window": "Other"})
    before = len(stub.set_calls)
    b.send("focus_away", False)
    check("a focus_away release does not change the foreground window", len(stub.set_calls) == before)
    b.send("focus_away", True)
    check("focus_away activates the named window on press only",
          stub.front == 22 and len(stub.set_calls) == before + 1)
    check("a press after focus_away is refused until focus_back", _raises(RuntimeError, lambda: b.send("enter", True)))
    b.send("focus_back", True)
    check("focus_back activates the focus window", stub.front == 11)
    b.send("enter", True)
    b.send("enter", False)
    check("a key is sent after focus_back", len(stub.sent) == 2)
    b.close()

    stub = _StubApi()
    b = _opened(stub)
    check("focus_away without away_window is refused", _raises(RuntimeError, lambda: b.send("focus_away", True)))
    b.close()
    stub = _StubApi()
    stub.windows = {"Editor": [11], "Other": []}
    b = _opened(stub, {"focus_window": "Editor", "away_window": "Other"})
    check("a title that matches nothing is refused", _raises(RuntimeError, lambda: b.send("focus_away", True)))
    b.close()
    check("the pseudo-keys declare the option each one needs",
          KEY_OPTIONS == {"focus_away": "away_window", "focus_back": "focus_window"})
    for bad in ({"nope": "1"}, {"focus_window": ""}, {"focus_window": "a\nb"},
                {"focus_window": "x" * (MAX_TITLE + 1)}, {"focus_window": "E", "away_window": "a\nb"}):
        check("bad option is refused: %s" % sorted(bad), _raises(ValueError, lambda: Backend(bad)))


def selftest():
    if sys.platform != "win32":
        print("selftest: not applicable on this operating system")
        return 0
    failures = []

    def check(name, cond):
        print("%s %s" % ("ok  " if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    global _Api
    real = _Api
    _Api = _forbidden_api          # a stub-driven test must never touch the real API
    try:
        _checks(check)
    finally:
        _Api = real
    return 1 if failures else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--probe"]:
        return probe()
    if argv == ["--selftest"]:
        return selftest()
    print("usage: backend_windows.py --probe | --selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
