# LaTeX Engineering Quality Checklist

Use this checklist before declaring any `.tex` deliverable complete.

## 1. Compilation & Reproducibility (`编译得通` & `复现得了`)
- [ ] Root entry `.tex` file compiles with **0 fatal errors** via the recorded engine (`pdflatex` / `xelatex` / `lualatex`) and pass sequence (`engine -> bib -> engine -> engine`).
- [ ] Multi-pass convergence achieved: `.aux`/`.toc`/`.out` stabilized and no `Rerun to get cross-references right` warnings remain.
- [ ] Zero unresolved citations or references (`??`, `[?]`, `Citation ... undefined`, `Reference ... undefined`).
- [ ] Zero missing glyphs (`Missing character: There is no ...`) in `.log`.
- [ ] Rendered PDF previews (`previews/page_*.png`) visually inspected for clipping, blank pages, or overlapping floats.
- [ ] `build.sh` and `reproducibility_manifest.json` record exact engine version, flags, pass order, and package/font dependencies.

## 2. Math & Scientific Notation (`改得对`)
- [ ] Multi-line equations use `amsmath` (`align`, `align*`, `multline`, `gather`) — never deprecated `eqnarray` or plain `$$...$$`.
- [ ] Multi-letter operators use upright text: `\sin`, `\cos`, `\log`, `\exp`, `\max`, `\arg\max`, `\operatorname{RMSE}`, differential `\mathrm{d}x` (or consistent convention matching draft).
- [ ] Physical units formatted upright (`\mathrm{kg\,m^{-3}}` or `siunitx` `\qty{...}{...}` / `\SI{...}{...}`), never italicized math variables.
- [ ] Variable notation, subscripts/superscripts, and equation numbers match the source manuscript 100% — never silently renumber or alter exponents.

## 3. Tables & Floats
- [ ] Publication three-line style via `booktabs` (`\toprule`, `\midrule`, `\bottomrule`); avoid heavy vertical rules (`|`) and raw `\hline` stacks.
- [ ] Wide tables fixed by structure — `tabularx` (`X` wrap columns), `p{width}`, `longtable` (multi-page), `\small`, `\setlength{\tabcolsep}{4pt}`, or `siunitx` `S` decimal alignment — **never** hide overflow by wrapping `tabular` in `\resizebox{\textwidth}{!}{...}`.
- [ ] Table captions placed **above** `tabular`; figure captions placed **below** `\includegraphics`.
- [ ] `\label{...}` always placed **after** `\caption{...}` (never before `\caption` or inside `\caption` if avoidable).
- [ ] Float placement uses `[htbp]` (or `[tp]`), avoiding rigid `[h]` that stalls float queues.

## 4. Citations & `.bib` Integrity
- [ ] Every `\cite{k}` / `\citep{k}` / `\citet{k}` matches an exact entry key in `.bib` (or `\bibitem`).
- [ ] BibTeX titles preserve case for acronyms/proper nouns via braces (`{LaTeX}`, `{IEEE}`, `{CO$_2$}`).
- [ ] No fabricated citations, DOIs, page numbers, or author years. Unverified citations tagged `% [AUTHOR_CONFIRM: GAP-XX]`.

## 5. CJK / Bilingual Font Chain (Linux TeX Live Safe)
- [ ] Any document containing CJK uses `xelatex` or `lualatex` with `ctex` or `xeCJK` — never `pdflatex` with bare UTF-8 Chinese.
- [ ] Probe `fc-list :lang=zh family` (via `inspect_case.py`) before setting font names.
- [ ] Recommended Linux fallback chain: `Noto Serif CJK SC` / `Noto Sans CJK SC` -> `FandolSong` / `FandolHei` (`fontset=fandol`) -> `AR PL UMing CN` -> `WenQuanYi Micro Hei`. Never hardcode Windows-only `SimSun`/`Microsoft YaHei` without checking availability.

## 6. Maintainability & Source Provenance (`交得可用`)
- [ ] Source delivered as clean, modular, editable `.tex` + `.bib` (not hardcoded PDF/plain text dumps).
- [ ] Preserve non-trivial numeric/methodological provenance via concise TeX comments: `% [SRC: source_file:L12]` and mark gaps explicitly with `% [AUTHOR_CONFIRM: GAP-XX]`.
