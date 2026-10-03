#!/usr/bin/env python3
# kit: selftest
"""Window that scenario S15 brings to the front on Windows (``focus_away``), so that the reference input field
loses the focus for one second (Python standard library only).

  away_windows.py [--title TITLE]   show a small window titled TITLE (default "ime-away") until it is closed
  away_windows.py --selftest        check the arguments; no window is shown

The emitter finds the window by its title (``away_window`` in the cell's ``backend_opts``, which must equal TITLE)
and brings it forward; no key is pressed while it is in front. A window of its own is used rather than an
application window, which the calibration pilot found could be closed during a run (the run then stops). The
window holds a label only and accepts no text. ``start_windows.py`` starts it for the run and closes it afterwards
when the cell names an ``away_window``; it can also be started by hand before the run.
"""
import argparse
import contextlib
import io
import sys

DEFAULT_TITLE = "ime-away"
GEOMETRY = "320x120+40+40"


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if not a.title or a.title != a.title.strip():
        ap.error("--title must be a non-empty title without leading or trailing spaces")
    return a


def show(title):
    import tkinter as tk
    root = tk.Tk()
    root.title(title)
    root.geometry(GEOMETRY)
    tk.Label(root, text="%s (focus target of scenario S15)" % title).pack(expand=True)
    root.mainloop()


def selftest():
    fails = []

    def check(name, ok):
        print("  %s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            fails.append(name)

    check("the default title is ime-away", parse([]).title == DEFAULT_TITLE)
    check("a given title is kept", parse(["--title", "other"]).title == "other")
    for bad in ("", " x"):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                parse(["--title", bad])
            ok = False
        except SystemExit as e:
            ok = e.code == 2
        check("the title %r is refused" % bad, ok)
    print("selftest %s (%d failed)" % ("OK" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


def main(argv):
    a = parse(argv)
    if a.selftest:
        return selftest()
    show(a.title)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
