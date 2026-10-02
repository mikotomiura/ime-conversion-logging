#!/usr/bin/env bash
# Run one condition of the execution-cost measurement (E3): sample the standalone observer for a fixed time.
#
#   bash run_e3.sh <idle|working> <duration_s> <interval_s> <out_dir>
#
# One condition is one night: 28800 seconds (eight hours) at a 30-second interval.
#
#   idle      Nothing operates the machine while the observer runs. The observer must already be running,
#             exactly one process of that name.
#   working   Synthetic typing is played meanwhile. This script does not type: it prints the command of the
#             cell runner, and the person who measures starts it in another terminal. The observer must NOT
#             be running yet: the cell runner starts it, as one process that covers the whole window (one
#             long plan, not a repetition that starts the observer again).
#
# This script samples the process named by E3_PROC (default: ime-observation-logger; on Windows without the
# .exe suffix). The name may contain letters, digits, dot, underscore and hyphen. A running process of that
# name is counted before the run; the count must be exactly one for idle and zero for working. A duplicate
# that appears during the run is not detected.
# Rows taken while the process is absent carry zero in every column and are left out by the summary.
#
# E3_EXE (optional) is the path of the observer executable; its SHA-256 is recorded in conditions.txt (the
# path itself is not). Without it the hash is taken from the running process, or is recorded as unknown.
#
# Output (out_dir must not exist; its parent is created): samples.tsv (a header and one row per sample) and
# conditions.txt. Then:  python3 summarize_e3.py idle=<idle dir>/samples.tsv working=<working dir>/samples.tsv
#
# Battery is not measured: the machine runs on mains power and a discharge cannot be read. The battery
# column of the sampler is kept only so that the layout stays the same on both systems.
#
# Must run under bash 3.2 (the macOS default): always brace variables as ${VAR}; no arrays or mapfile.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "${HERE}/.." && pwd)"

usage () {
  echo "usage: bash run_e3.sh <idle|working> <duration_s> <interval_s> <out_dir>" >&2
  echo "       (E3_PROC names the process: letters, digits, dot, underscore, hyphen)" >&2
  exit 2
}
[ $# -eq 4 ] || usage
COND="$1"; DURATION_S="$2"; INTERVAL_S="$3"; OUT="$4"
case "${COND}" in idle|working) ;; *) usage ;; esac
case "${DURATION_S}" in ''|*[!0-9]*) usage ;; esac
case "${INTERVAL_S}" in ''|*[!0-9]*) usage ;; esac
# Both numbers are read as decimal (08 is 8) and kept short enough for shell arithmetic.
[ "${#DURATION_S}" -le 9 ] && [ "${#INTERVAL_S}" -le 9 ] || usage
DURATION_S=$(( 10#${DURATION_S} ))
INTERVAL_S=$(( 10#${INTERVAL_S} ))
[ "${DURATION_S}" -gt 0 ] && [ "${INTERVAL_S}" -gt 0 ] || usage

case "$(uname -s)" in
  Darwin) OS=macos ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) echo "run_e3.sh: unsupported system: $(uname -s) (macOS and Windows only)" >&2; exit 2 ;;
esac

# The process name. On macOS `pgrep -x` compares the full executable name (22 characters here), not a
# 16-character prefix; only the COMMAND column of `top` is cut to 16 characters.
E3_PROC="${E3_PROC:-ime-observation-logger}"
case "${E3_PROC}" in ''|-*|*[!A-Za-z0-9._-]*) usage ;; esac
export E3_PROC

# Optional executable path, used only to hash the file.
E3_EXE="${E3_EXE:-}"
if [ -n "${E3_EXE}" ] && [ ! -f "${E3_EXE}" ]; then
  echo "run_e3.sh: E3_EXE is not a file" >&2
  exit 2
fi
export E3_EXE

# The output directory is created in one step: the parent may be new, the directory itself must not exist.
mkdir -p "$(dirname "${OUT}")"
if ! mkdir "${OUT}" 2>/dev/null; then
  echo "run_e3.sh: the output directory already exists or cannot be created; an earlier measurement is never overwritten" >&2
  exit 3
fi
abort_pre () {
  echo "run_e3.sh: $1" >&2
  rmdir "${OUT}" 2>/dev/null || true
  exit 3
}

ps_run () {
  powershell.exe -NoProfile -NonInteractive -Command "$1" 2>/dev/null | tr -d '\r'
}

# The number of running processes of the sampled name.
count_procs () {
  if [ "${OS}" = "macos" ]; then
    pgrep -x -u "$(id -u)" "${E3_PROC}" 2>/dev/null | wc -l | tr -d ' '
  else
    ps_run '@(Get-Process -Name $env:E3_PROC -ErrorAction SilentlyContinue).Count' | tail -1
  fi
}

# The SHA-256 of the observer executable, or "unknown". The path is never printed.
exe_sha256 () {
  local h=""
  if [ "${OS}" = "macos" ]; then
    local p="${E3_EXE}"
    if [ -z "${p}" ]; then
      local pid
      pid="$(pgrep -x -u "$(id -u)" "${E3_PROC}" 2>/dev/null | head -1 || true)"
      [ -z "${pid}" ] || p="$(ps -o comm= -p "${pid}" 2>/dev/null || true)"
    fi
    [ -z "${p}" ] || [ ! -f "${p}" ] || h="$(shasum -a 256 "${p}" 2>/dev/null | cut -d' ' -f1 || true)"
  else
    h="$(ps_run '$p = $env:E3_EXE; if (-not $p) { $q = Get-Process -Name $env:E3_PROC -ErrorAction SilentlyContinue | Select-Object -First 1; if ($q) { $p = $q.Path } }; if ($p -and (Test-Path -LiteralPath $p)) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }' | tail -1 || true)"
  fi
  case "${h}" in
    *[!0-9a-f]*|'') echo unknown ;;
    *) [ "${#h}" -eq 64 ] && echo "${h}" || echo unknown ;;
  esac
}

# Expected number of rows: each pass takes the interval plus the time of the reading itself (about 9 seconds
# on macOS: three frames of `top` and the process listing; on Windows: two PowerShell starts and the counter
# wait of about one second). Warn before the run, not after it.
if [ "${OS}" = "macos" ]; then OVERHEAD_S=9; else OVERHEAD_S=4; fi
ROWS=$(( DURATION_S / (INTERVAL_S + OVERHEAD_S) ))
if [ "${ROWS}" -lt 480 ]; then
  echo "warning: about ${ROWS} rows are expected (a pass takes about $(( INTERVAL_S + OVERHEAD_S )) s); the summary needs at least 480 usable rows" >&2
fi
if [ "${DURATION_S}" -lt 14400 ]; then
  echo "warning: a window of ${DURATION_S} s is shorter than 4 hours; the summary reports it as insufficient" >&2
fi

# Processes of the sampled name before the run.
N="$(count_procs)"
case "${N}" in ''|*[!0-9]*) abort_pre "could not count the processes named ${E3_PROC}" ;; esac
if [ "${COND}" = "idle" ] && [ "${N}" -ne 1 ]; then
  abort_pre "idle needs exactly one running process named ${E3_PROC}; found ${N}"
fi
if [ "${COND}" = "working" ] && [ "${N}" -ne 0 ]; then
  abort_pre "working needs no running process named ${E3_PROC} (the cell runner starts it); found ${N}. Stop the idle observer first."
fi
echo "processes named ${E3_PROC} before the run: ${N}"

# Windows: read the CPU counter once. The sampler cannot tell an unreadable counter from a zero load.
if [ "${OS}" = "windows" ]; then
  if ! ps_run 'try { $q = Get-Process -Name $env:E3_PROC -ErrorAction SilentlyContinue | Select-Object -First 1; if ($q) { $n = $q.ProcessName } else { $n = "powershell" }; $null = Get-Counter -Counter ("\Process(" + $n + ")\% Processor Time") -ErrorAction Stop; "ok" } catch { "fail" }' | tail -1 | grep -q '^ok$'; then
    abort_pre "the CPU performance counter cannot be read on this system; the sampler would write zero for every row"
  fi
  echo "CPU performance counter: readable"
fi

if [ "${COND}" = "working" ]; then
  echo "working condition: start the typing in another terminal, after this sampler has started:"
  echo "  python3 \"${KIT}/run/run_cell.py\" --cell <CELL.json> --plan <PLAN.jsonl> --run-dir <RUN_DIR>"
  echo "  (add the send-consent flag described in run/README.md; this script never types)"
  echo "  The plan must cover the whole window as one long plan, so that the observer stays one process."
else
  echo "idle condition: leave the keyboard and the pointer alone for the whole run"
fi

printf 'epoch_s\tcpu_pct\trss_kb\tthreads\tbattery_pct\trss_alt_kb\tenergy_impact\tref_energy_impact\n' > "${OUT}/samples.tsv"
if [ "${OS}" = "macos" ]; then
  LOGICAL_CPUS="$(sysctl -n hw.logicalcpu 2>/dev/null || echo unknown)"
  OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
else
  LOGICAL_CPUS="${NUMBER_OF_PROCESSORS:-unknown}"
  OS_VERSION="$(ps_run '(Get-CimInstance -ClassName Win32_OperatingSystem).Version' | tail -1 || true)"
  [ -n "${OS_VERSION}" ] || OS_VERSION=unknown
fi
EXE_SHA="$(exe_sha256)"
{
  echo "condition=${COND}"
  echo "os=${OS}"
  echo "os_version=${OS_VERSION}"
  echo "logical_processors=${LOGICAL_CPUS}"
  echo "process=${E3_PROC}"
  echo "executable_sha256=${EXE_SHA}"
  echo "processes_before_run=${N}"
  echo "duration_s=${DURATION_S}"
  echo "interval_s=${INTERVAL_S}"
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "${OUT}/conditions.txt"

bash "${HERE}/sample-${OS}.sh" "${DURATION_S}" "${INTERVAL_S}" >> "${OUT}/samples.tsv"

# For the working condition the observer did not exist at the start; take its hash at the end if it is still there.
if [ "${EXE_SHA}" = "unknown" ]; then
  echo "executable_sha256_at_end=$(exe_sha256)" >> "${OUT}/conditions.txt"
fi
echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${OUT}/conditions.txt"
echo "done. next: python3 \"${HERE}/summarize_e3.py\" ${COND}=\"${OUT}/samples.tsv\""
