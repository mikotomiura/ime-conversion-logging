#!/usr/bin/env bash
# kit/check.sh -- verification entry point for the paper kit (the loop's verify test / typecheck).
#
#   bash kit/check.sh              Default: finds every .py under kit/ that carries a
#                                  "# kit: selftest" line (a comment occupying the whole line),
#                                  and runs python3 <file> --selftest on each in turn. Exits 1 if
#                                  even one fails. Exits 1 if none are found (a no-op is never
#                                  reported as green).
#   bash kit/check.sh --compile    Runs python3 -m py_compile on every .py under kit/ (exits 1 if
#                                  there are none).
#   bash kit/check.sh --self-test  This entry point's own negative controls. Builds selftests that
#                                  should turn green or red in a temporary directory and confirms
#                                  the entry point reports red as red (a no-op is never reported as
#                                  green).
#
# Rule (for anyone writing a later selftest): this entry point runs on every verify and on every
#   acceptance check. Write selftests to be fast by default (each under 30 seconds). Split heavy
#   checks into something like `--selftest --full` and do not run them by default. A selftest that
#   takes over 30 seconds only triggers a warning (it does not stop the run).
#
# Environment variable: KIT_SLOW_SECONDS = warning threshold in seconds (default 30; --self-test
#   uses this to confirm the warning fires)
# Exit: 0 = green / 1 = red (including a no-op with zero matches) / 2 = usage error or python missing
#
# Must run under bash 3.2 (the macOS default): always brace variables as ${VAR}. Placing an
#   unbraced variable directly before a multibyte character makes bash 3.2 read "variable name +
#   byte of the character" as a single name and crash. Do not use arrays or mapfile (some `set -u`
#   builds crash on an empty array).
set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="selftests"
SLOW="${KIT_SLOW_SECONDS:-30}"
MARK="# kit: selftest"

while [ $# -gt 0 ]; do
  case "$1" in
    --compile) MODE="compile" ;;
    --self-test) MODE="entry-selftest" ;;
    --root)
      if [ $# -lt 2 ]; then echo "check.sh: --root requires a directory argument" >&2; exit 2; fi
      ROOT="$2"; shift ;;
    -h|--help) sed -n '2,28p' "${SELF}"; exit 0 ;;
    *) echo "check.sh: unknown argument: ${1} (--compile / --self-test / --root DIR)" >&2; exit 2 ;;
  esac
  shift
done
if [ ! -d "${ROOT}" ]; then
  echo "check.sh: --root is not a directory: ${ROOT}" >&2
  exit 2
fi

export PYTHONUTF8=1
PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"
if ! command -v "${PY}" >/dev/null 2>&1; then
  echo "check.sh: neither python3 nor python is available (cannot check)" >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# List the .py files under kit/ in a stable order (generated-output directories are not scanned).
list_py () { # list_py <root>
  find "$1" -type f -name '*.py' \
    -not -path '*/__pycache__/*' -not -path '*/target/*' -not -path '*/.build/*' \
    -not -path '*/.venv/*' -not -path '*/venv/*' \
    2>/dev/null | LC_ALL=C sort
}

# Whether the file has the marker. The marker must be an entire comment line (a marker inside a
#   string literal is not picked up). Trailing whitespace and CR are allowed (this keeps a CRLF
#   file from silently losing the marker and never running its selftest).
has_mark () { # has_mark <file>
  grep -qE -- '^# kit: selftest[[:space:]]*$' "$1"
}

run_selftests () { # run_selftests <root>  green exits 0, red exits 1
  local root="$1" f t0 dt total=0 failed=0 out
  list_py "${root}" > "${WORK}/py.list"
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    has_mark "${f}" || continue
    total=$((total + 1))
    out="${WORK}/out.${total}"
    t0="${SECONDS}"
    if "${PY}" "${f}" --selftest > "${out}" 2>&1; then
      dt=$((SECONDS - t0))
      echo "  ✅ ${f#${root}/} (${dt}s)"
    else
      dt=$((SECONDS - t0))
      failed=$((failed + 1))
      echo "  ❌ ${f#${root}/} (${dt}s)"
      tail -n 20 "${out}" | sed 's/^/       /'
    fi
    if [ "${dt}" -gt "${SLOW}" ]; then
      echo "  WARN ${f#${root}/} took ${dt}s, exceeding the ${SLOW}s threshold. Split heavy checks into something like --selftest --full and keep the default fast."
    fi
  done < "${WORK}/py.list"
  if [ "${total}" -eq 0 ]; then
    echo "  ❌ no .py file carries '${MARK}' (a no-op is never reported as green): ${root}"
    return 1
  fi
  if [ "${failed}" -ne 0 ]; then
    echo "  ---- ${failed} of ${total} selftests failed ----"
    return 1
  fi
  echo "  ---- all ${total} selftests passed ----"
  return 0
}

run_compile () { # run_compile <root>
  local root="$1" f total=0 failed=0
  list_py "${root}" > "${WORK}/py.list"
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    total=$((total + 1))
    # Redirect output to a temporary directory (do not leave __pycache__ in the repository).
    if ! PYTHONPYCACHEPREFIX="${WORK}/pyc" "${PY}" -m py_compile "${f}" > "${WORK}/compile.out" 2>&1; then
      failed=$((failed + 1))
      echo "  ❌ ${f#${root}/}"
      tail -n 10 "${WORK}/compile.out" | sed 's/^/       /'
    fi
  done < "${WORK}/py.list"
  if [ "${total}" -eq 0 ]; then
    echo "  ❌ no .py files found (a no-op is never reported as green): ${root}"
    return 1
  fi
  if [ "${failed}" -ne 0 ]; then
    echo "  ---- ${failed} of ${total} files failed to compile ----"
    return 1
  fi
  echo "  ---- all ${total} files compiled ----"
  return 0
}

# --- --self-test: confirms the entry point reports red as red (negative controls) ---------------------
ST_FAILS=0
st_ng () { echo "  ❌ ${1}"; ST_FAILS=$((ST_FAILS + 1)); }
st_ok () { echo "  ✅ ${1}"; }
# st_expect <expected exit code> <description> <args...>  Runs the entry point against another root.
st_expect () {
  local want="$1" desc="$2" rc; shift 2
  bash "${SELF}" "$@" > "${WORK}/st.out" 2>&1; rc=$?
  if [ "${rc}" -eq "${want}" ]; then st_ok "${desc} (exit ${rc})"
  else st_ng "${desc} (expected exit ${want}, got ${rc}): $(tail -n 3 "${WORK}/st.out" | tr '\n' ' ')"; fi
}
st_expect_out () { # st_expect_out <fixed string> <description>  Must appear in the preceding st_expect's output.
  if grep -qF -- "$1" "${WORK}/st.out"; then st_ok "$2"; else st_ng "$2 (not found in output: $1)"; fi
}

entry_selftest () {
  local T="${WORK}/st"
  mkdir -p "${T}/green/sub" "${T}/red" "${T}/nomark" "${T}/empty" "${T}/broken" "${T}/slow" "${T}/ignored/target" \
           "${T}/ignored/.venv/lib" "${T}/strmark" "${T}/crlf"
  printf '# kit: selftest\nimport sys\nsys.exit(0)\n' > "${T}/green/a.py"
  printf '# kit: selftest\nimport sys\nsys.exit(0)\n' > "${T}/green/sub/b.py"
  cp "${T}/green/a.py" "${T}/red/a.py"
  printf '# kit: selftest\nimport sys\nprint("boom")\nsys.exit(1)\n' > "${T}/red/bad_selftest.py"
  printf 'import sys\nsys.exit(0)\n' > "${T}/nomark/plain.py"
  printf '# kit: selftest\nthis is not python (\n' > "${T}/broken/syntax.py"
  printf '# kit: selftest\nimport time\ntime.sleep(2)\n' > "${T}/slow/slow.py"
  # target/ is not scanned (a failing selftest there is ignored)
  printf '# kit: selftest\nimport sys\nsys.exit(1)\n' > "${T}/ignored/target/x.py"
  printf '# kit: selftest\nimport sys\nsys.exit(0)\n' > "${T}/ignored/ok.py"
  # .venv/ is not scanned either
  printf '# kit: selftest\nimport sys\nsys.exit(1)\n' > "${T}/ignored/.venv/lib/y.py"
  # A marker inside a string literal is not picked up (picking it up would run a helper module
  # that does not know --selftest, causing a red side effect).
  printf 'MARK = "# kit: selftest"\nimport sys\nsys.exit(1)\n' > "${T}/strmark/helper.py"
  cp "${T}/green/a.py" "${T}/strmark/a.py"
  # A marker in a CRLF file is also picked up (otherwise a selftest would silently never run).
  printf '# kit: selftest\r\nimport sys\r\nsys.exit(1)\r\n' > "${T}/crlf/c.py"

  echo "=== entry point negative controls ==="
  st_expect 0 "an all-green selftest set stays green" --root "${T}/green"
  st_expect 1 "one failing selftest turns the run red" --root "${T}/red"
  st_expect_out "bad_selftest.py" "the failing selftest's name appears in the output"
  st_expect_out "boom" "the failing selftest's output appears"
  st_expect 1 "only unmarked .py files yields a red no-op" --root "${T}/nomark"
  st_expect 1 "no .py files at all yields a red no-op" --root "${T}/empty"
  st_expect 0 "failing selftests under target/ and .venv/ are ignored" --root "${T}/ignored"
  st_expect 0 "a marker inside a string literal is not picked up" --root "${T}/strmark"
  st_expect 1 "a marker in a CRLF file is also picked up (the failing selftest runs and turns red)" --root "${T}/crlf"
  st_expect 0 "a slow selftest only warns, it does not stop the run" --root "${T}/slow"
  KIT_SLOW_SECONDS=1 bash "${SELF}" --root "${T}/slow" > "${WORK}/st.out" 2>&1
  st_expect_out "exceeding the" "a warning appears once the threshold is exceeded (KIT_SLOW_SECONDS=1)"
  st_expect 0 "--compile: valid syntax stays green" --compile --root "${T}/green"
  st_expect 1 "--compile: a syntax error turns it red" --compile --root "${T}/broken"
  st_expect 1 "--compile: zero .py files yields a red no-op" --compile --root "${T}/empty"
  st_expect 2 "an unknown argument exits 2" --bogus
  st_expect 2 "a missing --root value exits 2" --root
  st_expect 2 "a non-directory --root exits 2" --root "${T}/no-such-dir"

  # For the real kit/ tree: at least one marked .py file exists (this entry point is not a no-op).
  if [ "${ROOT}" = "$(cd "$(dirname "${SELF}")" && pwd)" ]; then
    list_py "${ROOT}" > "${WORK}/py.list"
    n=0
    while IFS= read -r f; do
      [ -n "${f}" ] && has_mark "${f}" && n=$((n + 1))
    done < "${WORK}/py.list"
    if [ "${n}" -ge 1 ]; then st_ok "the real kit/ tree has ${n} selftest(s)"
    else st_ng "the real kit/ tree has no selftests"; fi
  fi

  echo "  ---- entry point negative controls: ${ST_FAILS} failed ----"
  [ "${ST_FAILS}" -eq 0 ]
}

case "${MODE}" in
  selftests) echo "=== kit selftest (${ROOT}) ==="; run_selftests "${ROOT}" ;;
  compile) echo "=== kit compile (${ROOT}) ==="; run_compile "${ROOT}" ;;
  entry-selftest) entry_selftest ;;
esac
exit $?
