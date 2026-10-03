#!/usr/bin/env python3
# kit: selftest
"""Focus command of the Windows cells: brings the reference input field to the front, confirms that it is in
front, and only then switches the input method on (Python standard library only).

  focus_windows.py --title TITLE [--ime-on]   the cell's focus_cmd (run by run_cell.py before every block)
  focus_windows.py --selftest                 check the call sequence against a stub; nothing is sent

Exactly one visible top-level window must carry TITLE (case-sensitive, whole string), as for the emitter's
``focus_window`` option (``../emit/backend_windows.py``). A minimised window is restored. The window is brought to
the front with ``SetForegroundWindow``; when the foreground lock refuses it (the call returns 0), the command
attaches to the input of the thread that owns the foreground window (``AttachThreadInput``), calls
``BringWindowToTop`` and ``SetForegroundWindow`` again, and detaches. The foreground window is then read back up to
five times, 0.2 seconds apart, as the emitter does; when the window is not in front the command exits with status
1 and presses nothing, so that the runner aborts the run before the block. With ``--ime-on`` it then presses and
releases ``VK_IME_ON`` (0x16) through ``SendInput``, the key that switched the Microsoft input method on in the
Windows calibration pilot (``experiment/pilot-windows.md``).

Started by hand with ``--ime-on``, it presses that one key without the runner's consent flag; the runner runs it only
with the flag. No output on success. Exit status: 0 done; 1 the window was not found exactly once, was not in front, or the key
was refused; 2 usage error. The libraries are loaded lazily, so the self-test runs on every operating system.
"""
import argparse
import ctypes
import sys
import time

VK_IME_ON = 0x16
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
SW_RESTORE = 9
MAX_TITLE = 512
SETTLE_S = 0.5      # wait after bringing the window forward before the first read-back
READS = 5           # read-backs of the foreground window, as in the emitter
READ_GAP_S = 0.2
KEY_HOLD_S = 0.06
AFTER_KEY_S = 0.3   # wait after the input-method key, before the runner confirms the window and sends the block


def _input_types():
    from ctypes import wintypes
    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    return KEYBDINPUT, INPUT


class Api(object):
    """Thin ctypes wrapper over the user32 / kernel32 calls that are needed (loaded on construction)."""

    def __init__(self):
        from ctypes import wintypes
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        u = self._u
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetForegroundWindow.argtypes = []
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.AttachThreadInput.restype = wintypes.BOOL
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.BringWindowToTop.restype = wintypes.BOOL
        u.BringWindowToTop.argtypes = [wintypes.HWND]
        u.IsIconic.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]
        u.ShowWindow.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.restype = wintypes.BOOL
        u.EnumWindows.argtypes = [self._enum_proc, wintypes.LPARAM]
        self._k.GetCurrentThreadId.restype = wintypes.DWORD
        self._k.GetCurrentThreadId.argtypes = []
        self._keybdinput, self._input = _input_types()
        u.SendInput.restype = ctypes.c_uint
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(self._input), ctypes.c_int]

    def find_windows(self, title):
        found = []
        buf = ctypes.create_unicode_buffer(MAX_TITLE + 2)

        def visit(hwnd, lparam):
            if self._u.IsWindowVisible(hwnd) and self._u.GetWindowTextW(hwnd, buf, len(buf)) and buf.value == title:
                found.append(hwnd)
            return True

        if not self._u.EnumWindows(self._enum_proc(visit), 0):
            raise RuntimeError("EnumWindows failed (GetLastError %d)" % ctypes.get_last_error())
        return found

    def is_iconic(self, hwnd):
        return bool(self._u.IsIconic(hwnd))

    def restore(self, hwnd):
        self._u.ShowWindow(hwnd, SW_RESTORE)

    def set_foreground(self, hwnd):
        return bool(self._u.SetForegroundWindow(hwnd))

    def bring_to_top(self, hwnd):
        self._u.BringWindowToTop(hwnd)

    def foreground(self):
        return self._u.GetForegroundWindow()

    def window_thread(self, hwnd):
        return self._u.GetWindowThreadProcessId(hwnd, None)

    def current_thread(self):
        return self._k.GetCurrentThreadId()

    def attach(self, a, b, on):
        return bool(self._u.AttachThreadInput(a, b, on))

    def key(self, vk, up):
        rec = self._input(type=INPUT_KEYBOARD)
        rec.u.ki = self._keybdinput(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
        return self._u.SendInput(1, ctypes.byref(rec), ctypes.sizeof(self._input)) == 1


def bring(api, hwnd):
    """Bring ``hwnd`` forward; when the foreground lock refuses, retry while attached to the foreground thread."""
    if api.is_iconic(hwnd):
        api.restore(hwnd)
    if api.set_foreground(hwnd):
        return
    fg = api.foreground()
    fg_tid = api.window_thread(fg) if fg else 0
    me = api.current_thread()
    attached = bool(fg_tid and fg_tid != me and api.attach(me, fg_tid, True))
    try:
        api.bring_to_top(hwnd)
        api.set_foreground(hwnd)
    finally:
        if attached:
            api.attach(me, fg_tid, False)


def in_front(api, hwnd, sleep=time.sleep):
    for i in range(READS):
        if i:
            sleep(READ_GAP_S)
        if api.foreground() == hwnd:
            return True
    return False


def focus(api, title, ime_on, sleep=time.sleep):
    """Return (status, message): 0 done, 1 refused before any key was pressed or the key was refused."""
    found = api.find_windows(title)
    if len(found) != 1:
        return 1, "%d visible top-level windows carry the title %r (exactly one is required)" % (len(found), title)
    hwnd = found[0]
    bring(api, hwnd)
    sleep(SETTLE_S)
    if not in_front(api, hwnd, sleep):
        return 1, "the window %r is not in front; no key was pressed" % title
    if ime_on:
        down = api.key(VK_IME_ON, False)
        sleep(KEY_HOLD_S)
        up = api.key(VK_IME_ON, True)
        if not (down and up):
            return 1, "SendInput refused the input-method key"
        sleep(AFTER_KEY_S)
    return 0, ""


class _Stub(object):
    """Records the calls; ``refuse`` makes the first SetForegroundWindow fail, ``stuck`` keeps another window in front."""

    def __init__(self, windows=(7,), refuse=False, stuck=False, key_ok=True):
        self.windows, self.refuse, self.stuck, self.key_ok = list(windows), refuse, stuck, key_ok
        self.front, self.calls, self.iconic = 99, [], False

    def find_windows(self, title):
        self.calls.append(("find", title))
        return list(self.windows)

    def is_iconic(self, hwnd):
        return self.iconic

    def restore(self, hwnd):
        self.calls.append(("restore", hwnd))

    def set_foreground(self, hwnd):
        self.calls.append(("set_foreground", hwnd))
        if self.refuse and not any(c[0] == "attach" for c in self.calls):
            return False
        if not self.stuck:
            self.front = hwnd
        return True

    def bring_to_top(self, hwnd):
        self.calls.append(("bring_to_top", hwnd))

    def foreground(self):
        return self.front

    def window_thread(self, hwnd):
        return 500

    def current_thread(self):
        return 400

    def attach(self, a, b, on):
        self.calls.append(("attach", a, b, on))
        return True

    def key(self, vk, up):
        self.calls.append(("key", vk, up))
        return self.key_ok


def selftest():
    fails = []

    def check(name, ok):
        print("  %s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            fails.append(name)

    nosleep = lambda s: None
    s = _Stub()
    st, _ = focus(s, "field", True, nosleep)
    keys = [c for c in s.calls if c[0] == "key"]
    check("in front: VK_IME_ON is pressed and released after the read-back",
          st == 0 and keys == [("key", VK_IME_ON, False), ("key", VK_IME_ON, True)]
          and s.calls.index(("set_foreground", 7)) < s.calls.index(keys[0]))
    s = _Stub()
    check("without --ime-on no key is pressed", focus(s, "field", False, nosleep)[0] == 0
          and not [c for c in s.calls if c[0] == "key"])
    s = _Stub(refuse=True)
    st, _ = focus(s, "field", True, nosleep)
    att = [c for c in s.calls if c[0] == "attach"]
    check("a refused SetForegroundWindow is retried while attached, and detached afterwards",
          st == 0 and att == [("attach", 400, 500, True), ("attach", 400, 500, False)]
          and ("bring_to_top", 7) in s.calls)
    s = _Stub(stuck=True)
    st, msg = focus(s, "field", True, nosleep)
    check("a window that stays behind stops the command before any key",
          st == 1 and "not in front" in msg and not [c for c in s.calls if c[0] == "key"])
    for n in (0, 2):
        s = _Stub(windows=range(n))
        check("%d matching windows are refused before anything is brought forward" % n,
              focus(s, "field", True, nosleep)[0] == 1 and len(s.calls) == 1)
    s = _Stub()
    s.iconic = True
    focus(s, "field", False, nosleep)
    check("a minimised window is restored first", s.calls[1] == ("restore", 7))
    s = _Stub(key_ok=False)
    check("a refused key press is an error", focus(s, "field", True, nosleep)[0] == 1)
    reads = []
    s = _Stub(stuck=True)
    in_front(s, 7, reads.append)
    check("five read-backs, 0.2 s apart", reads == [READ_GAP_S] * (READS - 1))
    print("selftest %s (%d failed)" % ("OK" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title")
    ap.add_argument("--ime-on", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.title:
        ap.error("--title is required")
    if sys.platform != "win32":
        sys.stderr.write("focus_windows.py: Windows only\n")
        return 2
    status, msg = focus(Api(), a.title, a.ime_on)
    if msg:
        sys.stderr.write("focus_windows.py: %s\n" % msg)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
