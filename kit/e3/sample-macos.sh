#!/usr/bin/env bash
# macOS: sample the observed process every INTERVAL seconds.
# Nothing but the process name is recorded (no other process is enumerated, no window title is read).
#
# The process to sample is named by the environment variable E3_PROC (default: ime-observation-logger).
#
# How CPU is measured:
#   The CPU figure is the increase of the cumulative CPU time (`ps -o cputime`) divided by the increase of
#   the wall-clock time. It is a cumulative counter, so a burst shorter than the sampling interval is not
#   missed: the CPU time of the burst is added to the next sample as it is.
#   The earlier method, the first column of the last frame of `top -l 2`, under-reports: a process that
#   saturates one core is reported as 47.6 % (ground truth 99.7 %). The cause is that the denominator of the
#   second frame includes the start-up time of `top` itself, about 1.1 s; lengthening `-s` to 2, 5 and 10 s
#   gave 62.5, 79.1 and 88.3 %, approaching 100 %. The factor is about 0.48, and it is systematic.
#   The instrument is calibrated by `calibrate-cpu.sh` (positive control: one saturated core; negative
#   control: an absent process).
#   `top` is kept only for memory, thread count and power, and it is run with `-l 3`: the third frame has
#   the right denominator and agreed with the ground truth in the calibration.
#
# Basis: one core = 100 % (not the sum over all cores; a machine with 10 cores has a maximum of 1000 %).
set -euo pipefail
DURATION_S="$1"; INTERVAL_S="$2"
E3_PROC="${E3_PROC:-ime-observation-logger}"

# When the interval is short, the power of the reference process (Finder) is not sampled:
# one `top -l 3` call takes about 3.1 s, and two calls per sample cannot fit a short interval.
REF_POWER=1
[ "$INTERVAL_S" -ge 10 ] || REF_POWER=0

# Print the CPU time in seconds. If it cannot be read, print nothing (an empty value is never mixed with 0).
cputime_s() {
  local raw
  raw=$(ps -o cputime= -p "$1" 2>/dev/null | tr -d ' ') || return 0
  [ -n "$raw" ] || return 0
  printf '%s' "$raw" | awk -F: '{ if (NF==3) print $1*3600+$2*60+$3; else if (NF==2) print $1*60+$2; else print $1 }'
}

prev_pid=""; prev_cpu=""; prev_t=""
end=$(( $(date +%s) + DURATION_S ))
while [ "$(date +%s)" -lt "$end" ]; do
  now=$(date +%s)
  # The pid of the observed process. If it is absent (the control condition), the row is filled with 0.
  pid=$(pgrep -x "${E3_PROC}" 2>/dev/null | head -1 || true)
  if [ -n "$pid" ]; then
    # `power` is the "energy impact" of Activity Monitor; it needs no sudo.
    #   It is not watts: it is a composite index that Apple computes from CPU, wake-ups, GPU and disk, so
    #   it cannot be converted into battery hours. It is read against a reference process.
    line=$(top -l 3 -pid "$pid" -stats cpu,mem,th,power -n 1 2>/dev/null | tail -1 || true)
    mem=$(printf '%s' "$line" | awk '{print $2}')
    # `top` on macOS may print the thread count as `31/1` (total/running); 21 of 880 rows had this form in
    # a measurement. Written as is, the aggregation cannot convert it to a number and drops the whole row
    # (CPU and RSS with it). Only the leading number is taken.
    th=$(printf '%s'  "$line" | awk '{print $3}' | cut -d/ -f1)
    # The mem column of `top` looks like "123M" or "546M+".
    # It may carry a `+` or `-` suffix (a mark of increase or decrease since the previous sample). If the
    # suffix is not removed, the value does not match `/M$/`, falls to the last branch, is divided by 1024
    # and becomes 0 (all the memory of a long run silently becomes zero). It is removed first.
    mem=$(printf '%s' "$mem" | tr -d '+-')
    rss_kb=$(printf '%s' "$mem" | awk '/G$/{printf "%d", $0*1024*1024} /M$/{printf "%d", $0*1024} /K$/{printf "%d", $0} !/[GMK]$/{printf "%d", $0/1024}')
    pw=$(printf '%s' "$line" | awk '{print $4}' | tr -d '+-')
    # Cross-check: `ps rss` (a different quantity from the MEM column of `top`).
    rss_alt=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
    # The power of the reference process is taken at the same time: a single value has no meaning (the
    # energy impact is a dimensionless composite index), so it is compared with a lightweight resident
    # application.
    ref_pw=""
    if [ "$REF_POWER" = 1 ]; then
      ref_pid=$(pgrep -x Finder 2>/dev/null | head -1)
      if [ -n "$ref_pid" ]; then
        ref_pw=$(top -l 3 -pid "$ref_pid" -stats power -n 1 2>/dev/null | tail -1 | awk '{print $1}' | tr -d '+-')
      fi
    fi
    # CPU: increase of the cumulative CPU time / increase of the wall-clock time (the cpu column of `top`
    # is not used).
    cur_cpu=$(cputime_s "$pid"); cur_t=$(date +%s)
    cpu=""
    if [ -n "$cur_cpu" ] && [ "$pid" = "$prev_pid" ] && [ -n "$prev_cpu" ]; then
      cpu=$(awk -v a="$prev_cpu" -v b="$cur_cpu" -v t0="$prev_t" -v t1="$cur_t" \
            'BEGIN{ d=t1-t0; if (d>0 && b>=a) printf "%.2f", 100*(b-a)/d }')
    fi
    prev_pid="$pid"; prev_cpu="$cur_cpu"; prev_t="$cur_t"
    # The first row has no previous value, so its CPU is empty (it is not written as 0).
    #   The aggregation treats an empty value as "not measured".
  else
    # The process is absent: the control condition. All columns are 0 (a negative control of the instrument).
    cpu=0; rss_kb=0; th=0; rss_alt=0; pw=0; ref_pw=""
    prev_pid=""; prev_cpu=""; prev_t=""
  fi
  batt=$(pmset -g batt 2>/dev/null | grep -oE '[0-9]+%' | head -1 | tr -d '%' || true)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$now" "${cpu:-}" "${rss_kb:-0}" "${th:-0}" "${batt:-}" "${rss_alt:-0}" "${pw:-0}" "${ref_pw:-}"
  sleep "$INTERVAL_S"
done
