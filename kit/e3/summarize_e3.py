#!/usr/bin/env python3
"""Summarise the samples of the execution-cost measurement (E3).

    python3 summarize_e3.py [--min-samples N] [LABEL=]samples.tsv [[LABEL=]samples.tsv ...]
    python3 summarize_e3.py --selftest

Each file is the output of `sample-macos.sh` or `sample-windows.sh` behind a header line. One block is
printed per file: CPU (mean, median, 95th percentile, maximum), the slope of the resident memory in MB per
hour, the measured sampling interval (median and maximum) and the floors that were applied. Pass one file
per condition (for example `idle=idle/samples.tsv working=working/samples.tsv`): the cost is reported per
condition, never as a single figure.

A run that was not measured is never reported as a low cost:
  - a file with no sample row, or whose rows are all zero (the process was never found), is `not measured`;
  - a file is `insufficient` when it has fewer than --min-samples usable rows (default 480, at least 2),
    fewer than --min-samples rows with a CPU value, or when the usable rows span less than 4 hours: the
    figures are printed for inspection but must not be quoted as rates;
  - an empty CPU value (the first row of a run has no previous cumulative counter) is left out of the CPU
    statistics; it is never read as 0;
  - a row is a usable row when the resident memory, the thread count or the alternative resident memory
    (sixth column) is positive; a row that is zero in all of them means the process was absent.

Replacement of the observed process: a run in which the process is present, then absent, then present
again (a restart) has a resident memory that starts over. The transitions are counted and printed. When
there is more than one continuous stretch, the slope of the resident memory is reported per stretch (with
the number of stretches) and the single overall slope is n/a, with the reason. A single absent row inside
a stretch (a failed reading) is counted as a transition as well, so a count above zero means the file
should be inspected.

An all-zero CPU value on every usable row is flagged with `cpu_all_zero: true` and a warning. The status is
not changed: a machine that nobody touches can produce a true zero, but so can a counter that cannot be read.

Rows with a negative or non-finite number are skipped and counted. Only the label and the base name of a
file are printed, and an unreadable row is reported by its line number.

Exit status: 0 every file is measured, 1 a file is insufficient or not measured, 2 a usage error or an
unreadable file.
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import sys
import tempfile

# kit: selftest

# Rows needed to cover eight hours at a 30-second interval is 960; half of it is the floor for a rate.
MIN_SAMPLES = 480
# The usable rows must also span at least this many hours.
MIN_HOURS = 4.0


def parse_threads(v):
    """Thread count. `top` on macOS may print `31/1` (total/running); only the leading number is taken."""
    return int(str(v).split("/")[0] or 0)


def _num(text):
    """A finite, non-negative number; an empty text is 0. Anything else raises ValueError."""
    if not text.strip():
        return 0.0
    v = float(text)
    if not math.isfinite(v) or v < 0:
        raise ValueError("out of range")
    return v


def load(path):
    """Read a samples file. Returns (rows, skipped); skipped holds the line numbers (1-based) of every row
    that could not be read. OSError and UnicodeDecodeError propagate."""
    rows, skipped = [], []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        parts = line.split("\t")
        if i == 0 and not parts[0].strip().isdigit():
            continue  # header line
        if len(parts) < 5:
            skipped.append(i + 1)
            continue
        try:
            t = int(parts[0])
            if t < 0:
                raise ValueError("negative time")
            th = parse_threads(parts[3])
            if th < 0:
                raise ValueError("negative threads")
            rows.append({
                "t": t,
                # An empty CPU value means "not measured"; it is kept as None and left out downstream.
                "cpu": _num(parts[1]) if parts[1].strip() else None,
                "rss_kb": _num(parts[2]),
                "threads": th,
                "rss_alt_kb": _num(parts[5]) if len(parts) > 5 else 0.0,
            })
        except ValueError:
            skipped.append(i + 1)
    return rows, skipped


def percentile(sorted_values, q):
    """Percentile with linear interpolation between closest ranks (q in 0..100)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def slope_mb_per_h(rows):
    """Least-squares slope of the resident memory in MB per hour. None for fewer than two points."""
    pts = [(r["t"], r["rss_kb"] / 1024.0) for r in rows if r["rss_kb"] > 0]
    n = len(pts)
    if n < 2:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 3600.0 for t, _ in pts]
    ys = [y for _, y in pts]
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def is_present(r):
    """The process was present in this row: a positive resident memory, thread count or alternative memory."""
    return r["rss_kb"] > 0 or r["threads"] > 0 or r["rss_alt_kb"] > 0


def stretches(rows):
    """Maximal runs of consecutive rows in which the process is present."""
    out, cur = [], []
    for r in rows:
        if is_present(r):
            cur.append(r)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _span_h(rows):
    return (rows[-1]["t"] - rows[0]["t"]) / 3600.0 if rows else 0.0


def summarise(rows, min_samples=MIN_SAMPLES, min_hours=MIN_HOURS):
    """Return a dict with `status` (measured / insufficient / not measured) and the statistics."""
    # Rows in which the process was absent carry zero in every column (the negative control of the sampler).
    present = [r for r in rows if is_present(r)]
    out = {"n_rows": len(rows), "n_absent": len(rows) - len(present), "n_present": len(present),
           "min_samples": min_samples, "min_hours": min_hours}
    gaps = sorted(b["t"] - a["t"] for a, b in zip(rows, rows[1:]) if b["t"] > a["t"])
    out["interval_median_s"] = percentile(gaps, 50)
    out["interval_max_s"] = gaps[-1] if gaps else None
    if not rows:
        out["status"] = "not measured"
        out["reason"] = "the file has no sample row"
        return out
    if not present:
        out["status"] = "not measured"
        out["reason"] = "every row is zero: the process was never found (check the process name)"
        return out
    cpus = sorted(r["cpu"] for r in present if r["cpu"] is not None)
    out["n_cpu_empty"] = len(present) - len(cpus)
    if not cpus:
        out["status"] = "not measured"
        out["reason"] = "no CPU value could be measured"
        return out
    out["n_cpu"] = len(cpus)
    out["cpu_mean"] = sum(cpus) / len(cpus)
    out["cpu_median"] = percentile(cpus, 50)
    out["cpu_p95"] = percentile(cpus, 95)
    out["cpu_max"] = cpus[-1]
    out["cpu_all_zero"] = all(c == 0 for c in cpus)
    runs = stretches(rows)
    out["n_stretches"] = len(runs)
    out["restarts"] = len(runs) - 1
    out["rss_slopes_by_stretch"] = [
        {"n_rows": len(g), "hours": _span_h(g), "slope_mb_per_h": slope_mb_per_h(g)} for g in runs]
    if len(runs) == 1:
        out["rss_slope_mb_per_h"] = out["rss_slopes_by_stretch"][0]["slope_mb_per_h"]
        out["rss_slope_note"] = None
    else:
        out["rss_slope_mb_per_h"] = None
        out["rss_slope_note"] = ("the process was replaced %d time(s): the resident memory starts over, so no "
                                 "single slope is reported" % (len(runs) - 1))
    out["rss_final_mb"] = present[-1]["rss_kb"] / 1024.0
    out["hours"] = _span_h(present)
    why = []
    if len(present) < min_samples:
        why.append("%d usable rows, fewer than %d" % (len(present), min_samples))
    if len(cpus) < min_samples:
        why.append("%d rows with a CPU value, fewer than %d" % (len(cpus), min_samples))
    if out["hours"] < min_hours:
        why.append("the usable rows span %.2f h, less than %.1f h" % (out["hours"], min_hours))
    if why:
        out["status"] = "insufficient"
        out["reason"] = "; ".join(why) + ": do not quote these as rates"
    else:
        out["status"] = "measured"
    return out


def fmt(v, spec):
    return "n/a" if v is None else format(v, spec)


def report(label, path, min_samples):
    """Print one block. Returns True when the file is measured. OSError / UnicodeDecodeError propagate."""
    rows, skipped = load(path)
    s = summarise(rows, min_samples)
    print("== %s (%s)" % (label, os.path.basename(path)))
    print("rows: %d usable, %d with the process absent, %d unreadable"
          % (s["n_present"], s["n_absent"], len(skipped)))
    if skipped:
        print("warning: %d line(s) could not be read; line numbers: %s%s"
              % (len(skipped), ", ".join(str(n) for n in skipped[:10]), " ..." if len(skipped) > 10 else ""))
    print("floors used: at least %d usable rows and %d rows with a CPU value; window of at least %.1f h"
          % (s["min_samples"], s["min_samples"], s["min_hours"]))
    print("sampling interval (measured): median %s s  max %s s"
          % (fmt(s["interval_median_s"], ".1f"), fmt(s["interval_max_s"], ".0f")))
    if s.get("n_cpu_empty"):
        print("note: %d row(s) had an empty CPU value and were left out of the CPU statistics"
              % s["n_cpu_empty"])
    if s["status"] == "not measured":
        print("status: not measured -- %s" % s["reason"])
        return False
    print("cpu %% (one core = 100): mean %s  median %s  p95 %s  max %s"
          % (fmt(s["cpu_mean"], ".2f"), fmt(s["cpu_median"], ".2f"), fmt(s["cpu_p95"], ".2f"),
             fmt(s["cpu_max"], ".2f")))
    print("cpu_all_zero: %s" % ("true" if s["cpu_all_zero"] else "false"))
    if s["cpu_all_zero"]:
        print("warning: every CPU value is exactly 0. Either the machine really was untouched or the counter "
              "could not be read; check before quoting.")
    print("presence transitions (present, absent, present): %d" % s["restarts"])
    print("resident memory: final %s MB  slope %s MB/h  over %s h"
          % (fmt(s["rss_final_mb"], ".1f"), fmt(s["rss_slope_mb_per_h"], ".2f"), fmt(s["hours"], ".2f")))
    if s["rss_slope_note"]:
        print("note: %s" % s["rss_slope_note"])
        print("resident memory slope per continuous stretch (%d stretches):" % s["n_stretches"])
        for i, g in enumerate(s["rss_slopes_by_stretch"], 1):
            print("  stretch %d: %d rows  %s h  slope %s MB/h"
                  % (i, g["n_rows"], fmt(g["hours"], ".2f"), fmt(g["slope_mb_per_h"], ".2f")))
    if s["status"] == "insufficient":
        print("status: insufficient -- %s" % s["reason"])
        return False
    print("status: measured")
    return True


# ---------------------------------------------------------------- selftest

def _tsv(rows):
    head = "epoch_s\tcpu_pct\trss_kb\tthreads\tbattery_pct\trss_alt_kb\tenergy_impact\tref_energy_impact\n"
    return head + "".join("\t".join(str(x) for x in r) + "\n" for r in rows)


def _row(t, cpu, rss_kb=100000, th=5):
    return [t, "" if cpu is None else cpu, rss_kb, th, "", rss_kb, 0, ""]


def _absent(t):
    return _row(t, 0, rss_kb=0, th=0)


def _cpu_from_cputime(times, cputimes):
    """The arithmetic of the macOS sampler: 100 * (increase of CPU seconds) / (increase of wall seconds)."""
    out = [None]
    for i in range(1, len(times)):
        d = times[i] - times[i - 1]
        out.append(100.0 * (cputimes[i] - cputimes[i - 1]) / d if d > 0 else None)
    return out


def selftest():
    fails = []
    tmp = tempfile.mkdtemp(prefix="e3_selftest_")
    n = [0]

    def write(rows):
        n[0] += 1
        p = os.path.join(tmp, "s%d.tsv" % n[0])
        with open(p, "w", encoding="utf-8") as f:
            f.write(_tsv(rows))
        return p

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    def near(a, b, eps=1e-6):
        return a is not None and abs(a - b) < eps

    try:
        # Case: CPU time rises by 0.02 s every second -> 2.0 %.
        times = list(range(0, 600))
        cpu = _cpu_from_cputime(times, [0.02 * t for t in times])
        rows, _ = load(write([_row(t, None if c is None else round(c, 2)) for t, c in zip(times, cpu)]))
        s = summarise(rows, min_samples=100, min_hours=0)
        check("known series (0.02 s/s is 2.0 %)", s["status"] == "measured" and near(s["cpu_mean"], 2.0)
              and near(s["cpu_median"], 2.0) and near(s["cpu_p95"], 2.0), repr(s))
        check("first row has an empty CPU value and is left out, not read as 0", s["n_cpu_empty"] == 1, repr(s))

        # Case: 1..100 gives mean 50.5, median 50.5, 95th percentile 95.05 (linear interpolation), max 100.
        rows, _ = load(write([_row(30 * i, float(i + 1)) for i in range(100)]))
        s = summarise(rows, min_samples=100, min_hours=0)
        check("statistics of 1..100", near(s["cpu_mean"], 50.5) and near(s["cpu_median"], 50.5)
              and near(s["cpu_p95"], 95.05) and near(s["cpu_max"], 100.0), repr(s))

        # Case: a skewed series (1..99 and 1000) separates the mean (59.5) from the median (50.5).
        vals = [float(i) for i in range(1, 100)] + [1000.0]
        rows, _ = load(write([_row(30 * i, v) for i, v in enumerate(vals)]))
        s = summarise(rows, min_samples=100, min_hours=0)
        check("a skewed series keeps the mean and the median apart",
              near(s["cpu_mean"], 59.5) and near(s["cpu_median"], 50.5) and near(s["cpu_max"], 1000.0), repr(s))

        # Case: resident memory growing by 10 MB per hour.
        rows, _ = load(write([_row(30 * i, 1.0, rss_kb=int(100 * 1024 + 10 * 1024 * (30 * i) / 3600.0))
                              for i in range(200)]))
        s = summarise(rows, min_samples=100, min_hours=0)
        check("slope of 10 MB per hour", near(s["rss_slope_mb_per_h"], 10.0, 0.01), repr(s))
        check("a continuous run has no presence transition", s["restarts"] == 0 and s["n_stretches"] == 1, repr(s))

        # Case: the interval is measured from the timestamps.
        rows, _ = load(write([_row(t, 1.0) for t in (0, 30, 60, 100, 130)]))
        s = summarise(rows, min_samples=2, min_hours=0)
        check("measured interval median and maximum",
              near(s["interval_median_s"], 30.0) and near(s["interval_max_s"], 40.0), repr(s))

        # Case: a restart (present, absent, present). The overall slope is n/a and each stretch has its own.
        first = [_row(30 * i, 1.0, rss_kb=100000 + 1000 * i) for i in range(50)]
        gap = [_absent(30 * (50 + i)) for i in range(3)]
        second = [_row(30 * (53 + i), 1.0, rss_kb=50000) for i in range(50)]
        rows, _ = load(write(first + gap + second))
        s = summarise(rows, min_samples=10, min_hours=0)
        check("a restart is counted", s["restarts"] == 1 and s["n_stretches"] == 2, repr(s))
        check("after a restart the overall slope is n/a", s["rss_slope_mb_per_h"] is None
              and s["rss_slope_note"], repr(s))
        check("after a restart each stretch has its slope",
              len(s["rss_slopes_by_stretch"]) == 2 and s["rss_slopes_by_stretch"][0]["slope_mb_per_h"] > 0
              and near(s["rss_slopes_by_stretch"][1]["slope_mb_per_h"], 0.0, 1e-6), repr(s))

        # Negative controls.
        rows, _ = load(write([_absent(30 * i) for i in range(600)]))
        s = summarise(rows)
        check("all zeros means not measured", s["status"] == "not measured", repr(s))
        rows, _ = load(write([]))
        s = summarise(rows)
        check("a file with no sample row is not measured", s["status"] == "not measured", repr(s))
        rows, _ = load(write([_row(30 * i, None) for i in range(600)]))
        s = summarise(rows)
        check("a file with no CPU value is not measured", s["status"] == "not measured", repr(s))
        rows, _ = load(write([_row(30 * i, 1.0) for i in range(50)]))
        s = summarise(rows)
        check("a short run is insufficient, not measured-as-low", s["status"] == "insufficient", repr(s))
        rows, _ = load(write([_row(20 * i, 1.0) for i in range(500)]))
        s = summarise(rows)
        check("enough rows over less than four hours is insufficient",
              s["status"] == "insufficient" and "span" in s["reason"], repr(s))
        rows, _ = load(write([_row(30 * i, 1.0) for i in range(600)]))
        s = summarise(rows)
        check("enough rows over at least four hours is measured", s["status"] == "measured", repr(s))
        rows, _ = load(write([_row(30 * i, None if i % 2 == 0 else 1.0) for i in range(700)]))
        s = summarise(rows)
        check("too few CPU values is insufficient even with enough rows",
              s["status"] == "insufficient" and s["n_present"] >= 480 and s["n_cpu"] < 480, repr(s))
        rows, _ = load(write([_absent(30 * i) for i in range(300)] + [_row(30 * (300 + i), 4.0) for i in range(600)]))
        s = summarise(rows)
        check("rows with the process absent are left out of the mean",
              s["status"] == "measured" and near(s["cpu_mean"], 4.0) and s["n_absent"] == 300, repr(s))

        # A CPU that is exactly zero on every usable row is flagged; a non-zero one is not.
        rows, _ = load(write([_row(30 * i, 0.0) for i in range(600)]))
        s = summarise(rows)
        check("all-zero CPU is flagged and the status is kept", s["cpu_all_zero"] is True
              and s["status"] == "measured", repr(s))
        rows, _ = load(write([_row(30 * i, 0.0 if i else 0.5) for i in range(600)]))
        check("a single non-zero CPU value clears the flag", summarise(rows)["cpu_all_zero"] is False)

        # The sixth column alone marks the process as present.
        rows, _ = load(write([[30 * i, 1.0, 0, 0, "", 5000, 0, ""] for i in range(10)]))
        s = summarise(rows, min_samples=2, min_hours=0)
        check("a positive sixth column means the process is present", s["n_present"] == 10, repr(s))

        # Row reading: the `31/1` thread form is kept, an unreadable line is counted by its number.
        p = write([_row(0, 1.0)])
        with open(p, "a", encoding="utf-8") as f:
            f.write("30\t2.0\t100000\t31/1\t\t100000\t0\t\n")
            f.write("60\tnot-a-number\t100000\t5\n")
            f.write("90\t2.0\n")
            f.write("120\t-3.0\t100000\t5\t\t100000\t0\t\n")
            f.write("150\tnan\t100000\t5\t\t100000\t0\t\n")
            f.write("180\tinf\t100000\t5\t\t100000\t0\t\n")
        rows, skipped = load(p)
        check("the 31/1 thread form is read", len(rows) == 2 and rows[1]["threads"] == 31, repr(rows))
        check("unreadable, negative and non-finite rows are skipped and numbered",
              skipped == [4, 5, 6, 7, 8], repr(skipped))

        # Command line: an all-zero file exits 1, a measured file exits 0.
        bad = write([_absent(30 * i) for i in range(600)])
        good = write([_row(30 * i, 2.0) for i in range(600)])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc_bad = main([bad])
            rc_good = main(["working=" + good, "idle=" + good])
            rc_none = main([])
            rc_missing = main([os.path.join(tmp, "absent.tsv")])
            rc_floor = main(["--min-samples", "1", good])
            rc_floor_ok = main(["--min-samples", "2", good])
        check("main exits 1 for an all-zero file", rc_bad == 1)
        check("main exits 0 for a measured file", rc_good == 0)
        check("main exits 2 without a file", rc_none == 2)
        check("main exits 2 for a missing file", rc_missing == 2)
        check("main exits 2 for a floor below two rows", rc_floor == 2)
        check("main accepts a floor of two rows", rc_floor_ok == 0)
        binary = os.path.join(tmp, "binary.tsv")
        with open(binary, "wb") as f:
            f.write(b"\xff\xfe\x00\x80\n")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc_binary = main([binary])
        check("main exits 2 for a file that cannot be decoded", rc_binary == 2)
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)
    if fails:
        for m in fails:
            print("FAIL " + m)
        print("selftest failed: %d" % len(fails))
        return 1
    print("selftest OK")
    return 0


def main(argv):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    args = list(argv)
    if "--selftest" in args:
        return selftest()
    min_samples = MIN_SAMPLES
    if "--min-samples" in args:
        i = args.index("--min-samples")
        try:
            min_samples = int(args[i + 1])
        except (IndexError, ValueError):
            print("summarize_e3: --min-samples needs an integer", file=sys.stderr)
            return 2
        if min_samples < 2:
            print("summarize_e3: --min-samples must be at least 2", file=sys.stderr)
            return 2
        del args[i:i + 2]
    if not args or any(a.startswith("-") for a in args):
        print(__doc__, file=sys.stderr)
        return 2
    targets = []
    for a in args:
        label, sep, path = a.partition("=")
        if not sep:
            label, path = os.path.basename(os.path.dirname(os.path.abspath(a))) or os.path.basename(a), a
        if not os.path.isfile(path):
            print("summarize_e3: no such file: %s" % os.path.basename(path), file=sys.stderr)
            return 2
        targets.append((label, path))
    all_ok = True
    for label, path in targets:
        try:
            all_ok = report(label, path, min_samples) and all_ok
        except (OSError, UnicodeDecodeError):
            print("summarize_e3: cannot read the file of %s" % label, file=sys.stderr)
            return 2
        print()
    if len(targets) == 1:
        print("note: one condition only. The cost is reported for the idle and the working condition, "
              "never as a single figure.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
