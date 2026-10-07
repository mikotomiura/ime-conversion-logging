# Building the manuscript

The manuscript (`main.tex`) and the supplement (`supplement/supplement.tex`) use the Springer Nature LaTeX template
with the APA reference style (class `sn-jnl`, option `sn-apa`, BibTeX style `sn-apacite`). The template is not part
of this directory: its files carry their own terms (the class file states the LaTeX Project Public License), and
they are obtained from the publisher.

## Obtaining the template

1. Download the journal article template package from the LaTeX author support page of Springer Nature
   (<https://www.springernature.com/gp/authors/campaigns/latex-author-support>, "Download the journal article
   template package"). The build was checked with the package of December 2024, a ZIP file of 901,814 bytes with
   the SHA-256 `812e76dcaa9c28dc1bff1fb6065d51729b67d4ea140552a05088317414a3ecae`, downloaded on 5 October 2026.
2. Unpack it outside this directory. It holds the directory `sn-article-template`, with `sn-jnl.cls` and the
   directory `bst`.

## Building

A TeX distribution with pdfLaTeX and BibTeX is needed; the packages that the class and the preamble load (among
them `apacite`, `rotating`, `threeparttable`, `longtable`, `xurl`, `lmodern` and `pgfplots`) are part of the usual
distributions. The build was checked with MiKTeX 25.12 (pdfTeX 4.23, pgfplots 1.18.1) on Windows.

    bash build.sh <path to sn-article-template> <output directory>

The script writes `main.pdf`, `supplement.pdf` and the logs to the output directory and leaves the sources
untouched; both directories may be given as relative paths, and the output directory must lie outside this directory.
LaTeX runs without shell escape. The script ends by printing, for each document, the number of errors, of undefined
references and citations, of result values that the text reads but that were not generated (`\rv{<key>}` with no
value, printed in bold as a value still to be filled in), and of overfull boxes, and the number of BibTeX warnings.
References to sections and figures that are not written yet are reported as undefined.

## Results tables and values

The tables of the results and every value of the results quoted in the text are generated from the result files of
the analysis by `tools/results_tables.py` (standard library only) into `generated/`; the text reads a value with
`\rv{<key>}`, and `generated/values.json` gives the source of each value inside the result files. After the result
files change:

    python3 tools/results_tables.py --results ../experiment/results --out generated

`build.sh` first runs the same command with `--check` and stops when a generated file no longer matches the result
files (set `PYTHON` when the interpreter is not `python3`). `python3 tools/results_tables.py --selftest` checks the
script on synthetic result files.

Figures 2 to 5 are generated in the same way, as `generated/fig-*.tex`: pgfplots code that holds the coordinates and
names the styles defined in the preamble of `main.tex`, so the figures are drawn by TeX and no plotting library is
needed. Figure 1 (`figures/components.tex`) holds no result and is written by hand. In the class of the template
`\scriptsize` is larger than `\footnotesize`, so the figures use `\footnotesize` for their smallest text. A submission
system that asks for the figures as separate files can be given each figure compiled on its own page from the same
sources.

## Submission

For submission the sections are combined into one file, and the template files are not uploaded unless the
submission system asks for them.

    bash build.sh <template-dir> <out-dir> --submission

also builds `main-submission.pdf` in the layout that the journal asks for (double spaced, 1-inch margins, the page
number in the upper right corner); the public `main.pdf` keeps the layout of the template. The title page with the
corresponding author's postal address and telephone number is `submission/title-page.tex`: copy it outside the
repository, fill in the two marked fields, build the copy with `pdflatex`, and upload it to the submission system
only, so that the address and the telephone number are never published with the sources.
