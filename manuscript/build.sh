#!/usr/bin/env bash
# Build the manuscript and the supplement with pdfLaTeX and BibTeX.
#
#   bash build.sh <template-dir> <out-dir> [--submission]
#
# <template-dir> is the directory sn-article-template of the Springer Nature LaTeX template (it holds sn-jnl.cls and
# the directory bst); see BUILD.md for where to obtain it. The template is not part of this directory. <out-dir>
# receives main.pdf, supplement.pdf and the logs, so that no build output is written next to the sources; it must lie
# outside this directory. Both may be given as relative paths. With --submission the manuscript is also built in the
# layout that the journal asks for a submission (double spaced, 1-inch margins, page numbers in the upper right
# corner) as main-submission.pdf.
set -euo pipefail

submission=0
if [ "$#" -eq 3 ] && [ "$3" = "--submission" ]; then
  submission=1
elif [ "$#" -ne 2 ]; then
  echo "usage: bash build.sh <template-dir> <out-dir> [--submission]" >&2
  exit 2
fi
template=$1
out=$2

# TeX reads the search paths with the separator of the system: a semicolon on Windows, a colon elsewhere. On Windows
# every directory is passed in the form of the system (C:/...), which a Windows TeX can open and bash can use.
windows=0
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) windows=1 ;;
esac
# The physical path (symbolic links resolved), so that an output directory cannot reach the sources through a link.
absolute() {
  local p
  p=$(cd "$1" && pwd -P)
  if [ "$windows" = 1 ]; then cygpath -m "$p"; else echo "$p"; fi
}
here=$(absolute "$(dirname "$0")")
# The directory of the article's public files (manuscript, experiment, kit); no output may be written inside it.
root=$(absolute "$here/..")

# The title page in this directory is a template whose address and telephone fields stay empty: the filled copy is kept
# outside the repository (BUILD.md). Stop if the template has been filled in.
for field in '[postal address]' '[telephone number]'; do
  if ! grep -qF "$field" "$here/submission/title-page.tex"; then
    echo "build.sh: submission/title-page.tex no longer holds the empty field $field; fill in a copy outside the repository instead" >&2
    exit 2
  fi
done

if [ ! -f "$template/sn-jnl.cls" ] || [ ! -f "$template/bst/sn-apacite.bst" ]; then
  echo "build.sh: $template does not hold sn-jnl.cls and bst/sn-apacite.bst" >&2
  exit 2
fi
created=0
if [ ! -d "$out" ]; then
  mkdir -p "$out"
  created=1
fi
# Both directories are made absolute before the script changes directory, so that a relative path names the same place
# throughout.
template=$(absolute "$template")
out=$(absolute "$out")
# Windows compares paths without regard to case. The logs of the build name the template's location, so they are kept
# out of every directory of the article's public files, not only out of the manuscript's.
out_cmp=$out
root_cmp=$root
if [ "$windows" = 1 ]; then
  out_cmp=$(printf '%s' "$out" | tr '[:upper:]' '[:lower:]')
  root_cmp=$(printf '%s' "$root" | tr '[:upper:]' '[:lower:]')
fi
case "$out_cmp/" in
  "$root_cmp"/*)
    if [ "$created" = 1 ]; then rmdir "$out"; fi
    echo "build.sh: $out lies inside $root; give an output directory outside it" >&2
    exit 2 ;;
esac

sep=":"
if [ "$windows" = 1 ]; then sep=";"; fi
export TEXINPUTS="${template}${sep}"
export BSTINPUTS="${template}/bst${sep}"
export BIBINPUTS="${here}${sep}"

# The tables and values of the results must match the result files (set PYTHON when python3 is not the interpreter).
"${PYTHON:-python3}" "$here/tools/results_tables.py" --results "$here/../experiment/results" --out "$here/generated" --check

# The template's class is run without shell escape.
latex() {
  local log
  log="$out/$(basename "$1" .tex).run.log"
  if ! pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error -output-directory="$out" "$1" > "$log"; then
    echo "build.sh: pdflatex failed on $1; see $log" >&2
    exit 1
  fi
}

# The manuscript: one pass to write the citations, BibTeX, and two passes for the references and cross-references.
# BibTeX exits with 1 when it only warns; the warnings are counted below.
cd "$here"
latex main.tex
(cd "$out" && bibtex main > main.bibtex.log) || [ $? -eq 1 ] || {
  echo "build.sh: bibtex failed; see $out/main.blg" >&2
  exit 1
}
latex main.tex
latex main.tex

# The layout for submission: the same sources under another job name, with the switch that main.tex reads.
names="main supplement"
if [ "$submission" = 1 ]; then
  sub() {
    if ! pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error -output-directory="$out" \
        -jobname=main-submission '\def\imsubmission{1}\input{main.tex}' > "$out/main-submission.run.log"; then
      echo "build.sh: pdflatex failed on the submission layout; see $out/main-submission.run.log" >&2
      exit 1
    fi
  }
  sub
  (cd "$out" && bibtex main-submission > main-submission.bibtex.log) || [ $? -eq 1 ] || {
    echo "build.sh: bibtex failed; see $out/main-submission.blg" >&2
    exit 1
  }
  sub
  sub
  names="main main-submission supplement"
fi

# The supplement has no citations.
cd "$here/supplement"
latex supplement.tex
latex supplement.tex

for name in $names; do
  log="$out/$name.log"
  echo "$name: $(grep -c '^!' "$log" || true) errors, $(grep -c 'LaTeX Warning: Reference' "$log" || true) undefined references, $(grep -c 'Warning: Citation' "$log" || true) undefined citations, $(grep -c 'Package results Warning: Result value' "$log" || true) result values not generated, $(grep -c '^Overfull' "$log" || true) overfull boxes, $(grep -c 'Float too large for page' "$log" || true) floats too large for the page"
done
# LaTeX does not always warn when a table runs off the page, so the PDFs are read for text and rules outside the page
# (tools/check_page_bounds.py, which needs PyMuPDF and says so when it is missing).
for name in $names; do
  "${PYTHON:-python3}" "$here/tools/check_page_bounds.py" "$out/$name.pdf" || true
done
for blg in main $( [ "$submission" = 1 ] && echo main-submission ); do
  echo "bibtex ($blg): $(grep -c '^Warning--' "$out/$blg.blg" || true) warnings"
done
