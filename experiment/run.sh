#!/usr/bin/env bash
# experiment/run.sh -- from the run directories to the first experiment's results, in one command.
#
#   bash run.sh --synthetic   Build two synthetic run directories in a temporary directory and run every
#                             stage on them. Nothing real is read. Run it twice: the result files are
#                             byte-identical.
#   bash run.sh               The real analysis. The run directories are the "- run_dir:" lines of data.md.
#                             It stops with exit status 3 unless preregistration.md carries the freeze
#                             marks (exactly one "frozen_at: YYYY-MM-DD" line and one "sha256:" line, outside
#                             comments and code fences) and the sha256 equals the value that
#                             "pipeline.py --freeze-hash" computes from config.json, SEED and the ground-truth
#                             rule file, so that no real data is analysed before the analysis is frozen and
#                             the settings cannot change after the freeze.
#
# Environment: IME_RESULTS_DIR is where the result files are written (default: results/ next to this
#   file for the real analysis, a temporary directory for --synthetic).
#
# Step 0 of the reproduction template (a capability gate of the parent repository) is replaced by the
#   checks that pipeline.py runs before any statistic: the content-free validator for the ground-truth
#   and observation logs, the plan and oracle validators, and the capability check on the observation
#   header. The reason is recorded in notes.md.
#
# Exit: 0 = done / 1 = a check found a violation / 2 = usage error or an unreadable input /
#       3 = the analysis is not frozen (real analysis only)
#
# Must run under bash 3.2 (the macOS default): variables are always braced, and no arrays are used.
set -u

HERE="$(CDPATH= cd -- "$(dirname "$0")" >/dev/null && pwd)"
if [ -z "${HERE}" ]; then
  echo "run.sh: cannot locate its own directory" >&2
  exit 2
fi
KIT="${HERE}/../kit"
PIPELINE="${KIT}/analysis/pipeline.py"

PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"
if ! command -v "${PY}" >/dev/null 2>&1; then
  echo "run.sh: neither python3 nor python is available" >&2
  exit 2
fi
export PYTHONUTF8=1

MODE="real"
case "${1:-}" in
  "") ;;
  --synthetic) MODE="synthetic" ;;
  *) echo "run.sh: unknown argument: ${1} (use --synthetic or no argument)" >&2; exit 2 ;;
esac
if [ $# -gt 1 ]; then
  echo "run.sh: too many arguments" >&2
  exit 2
fi

SEED_FILE="${HERE}/SEED"
if [ ! -f "${SEED_FILE}" ]; then
  echo "run.sh: SEED is missing" >&2
  exit 2
fi
SEED="$(tr -d '\r' < "${SEED_FILE}")"
case "${SEED}" in
  ""|*[!0-9]*) echo "run.sh: SEED must hold one non-negative integer and nothing else (no spaces)" >&2; exit 2 ;;
esac

CONFIG="${HERE}/config.json"

if [ "${MODE}" = "synthetic" ]; then
  WORK="$(mktemp -d)"
  if [ -z "${WORK}" ] || [ ! -d "${WORK}" ]; then
    echo "run.sh: could not create a temporary directory" >&2
    exit 2
  fi
  trap 'rm -rf "${WORK}"' EXIT
  RESULTS="${IME_RESULTS_DIR:-${WORK}/results}"
  echo "run.sh: synthetic run (seed ${SEED})"
  "${PY}" "${PIPELINE}" --synthetic "${WORK}/runs" --seed "${SEED}" || exit $?
  "${PY}" "${PIPELINE}" --runs "${WORK}/runs/run-a" "${WORK}/runs/run-b" --config "${CONFIG}" --seed "${SEED}" --out "${RESULTS}"
  exit $?
fi

# Real analysis: refuse to run before the analysis is frozen.
PREREG="${HERE}/preregistration.md"
if [ ! -f "${PREREG}" ]; then
  echo "run.sh: preregistration.md does not exist; the analysis is not frozen, so no real data is analysed" >&2
  exit 3
fi
"${PY}" "${PIPELINE}" --check-prereg "${PREREG}" --config "${CONFIG}" --seed-file "${SEED_FILE}"
RC=$?
if [ "${RC}" -ne 0 ]; then
  echo "run.sh: the freeze check failed (exit ${RC}); no real data is analysed" >&2
  exit "${RC}"
fi

RESULTS="${IME_RESULTS_DIR:-${HERE}/results}"
echo "run.sh: real analysis (seed ${SEED})"
"${PY}" "${PIPELINE}" --runs-from "${HERE}/data.md" --config "${CONFIG}" --seed "${SEED}" --seed-file "${SEED_FILE}" --prereg "${PREREG}" --out "${RESULTS}"
exit $?
