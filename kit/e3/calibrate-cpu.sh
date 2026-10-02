#!/usr/bin/env bash
# Calibrate the CPU instrument of the macOS sampler (sample-macos.sh) by hand.
#
# Why: a reading of CPU load must be checked against a known load before it is trusted. This script starts a
# process that saturates one core (the positive control), takes the ground truth from the increase of the
# cumulative CPU time over the wall-clock time, and prints next to it what `ps -o %cpu` and `top -l 2`,
# `top -l 3` and `top -l 4` report for the same process. After the process is stopped it prints what `top`
# reports for the absent process (the negative control).
#
# What to expect:
#   - `top -l 3` is the reading used for memory and threads and is expected to agree with the ground truth;
#     `top -l 4` is printed for reference. `top -l 2` reads low, about 0.48 times the
#     ground truth, because the denominator of its second frame includes the start-up time of `top` itself.
#   - `ps -o %cpu` is an average since the start of the process: it agrees for a steady load, but a burst
#     disappears in it.
#   - For the absent process the reading is empty or 0.
#
# This is a manual check of the instrument. Its output is not used by summarize_e3.py.
#
# Usage:  bash calibrate-cpu.sh
#   It uses one core at 100 % for about 30 seconds. The busy loop ends by itself after 60 seconds at the
#   latest, and it is stopped at the end of the script (also when interrupted).
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "calibrate-cpu.sh: this check applies to the macOS sampler only (it uses top -l and ps -o cputime)" >&2
  exit 2
fi

echo "logical processors: $(sysctl -n hw.logicalcpu 2>/dev/null || echo '?')"

# Positive control: a single-threaded busy loop saturates exactly one core (the interpreter lock keeps it to one).
SPIN=""
trap 'if [ -n "${SPIN}" ]; then kill "${SPIN}" 2>/dev/null || true; fi' EXIT
# The loop has a deadline of 60 seconds, so that it ends by itself if this script is killed.
python3 -c 'import time
end = time.time() + 60
while time.time() < end: pass' &
SPIN=$!
sleep 5

# Ground truth: increase of the CPU time / wall-clock time (depends on neither top nor ps %cpu).
c0=$(ps -o cputime= -p "$SPIN" | tr -d ' '); t0=$(date +%s)
sleep 8
c1=$(ps -o cputime= -p "$SPIN" | tr -d ' '); t1=$(date +%s)
truth=$(python3 - "$c0" "$c1" "$((t1 - t0))" <<'PY'
import sys
def sec(s):
    p = s.split(':')
    return int(p[0]) * 60 + float(p[1]) if len(p) == 2 else float(p[0])
print(f"{100 * (sec(sys.argv[2]) - sec(sys.argv[1])) / float(sys.argv[3]):.1f}")
PY
)
echo "ground truth (increase of cputime / wall-clock time): ${truth} %   (one core = 100 %)"

echo "readings of each instrument for the same process:"
printf '  %-28s %s\n' "ps -o %cpu (mean since start)" "$(ps -o %cpu= -p "$SPIN" | tr -d ' ')"
for L in 2 3 4; do
  printf '  %-28s %s\n' "top -l $L -n 1 (last frame)" \
    "$(top -l "$L" -pid "$SPIN" -stats cpu -n 1 2>/dev/null | tail -1)"
done

cat <<'NOTE'

How to read this:
  top -l 3 should agree with the ground truth (-l 4 is printed for reference).
  top -l 2 reads about 0.48 times the ground truth.
  ps -o %cpu agrees for a steady load, but it hides bursts.

Negative control (the process is absent): the reading below should be empty or 0.
NOTE
kill "$SPIN" 2>/dev/null || true
sleep 1
echo "  top for the absent process: [$(top -l 3 -pid "$SPIN" -stats cpu -n 1 2>/dev/null | tail -1)]   (empty or 0 is correct)"
wait "${SPIN}" 2>/dev/null || true
trap - EXIT
