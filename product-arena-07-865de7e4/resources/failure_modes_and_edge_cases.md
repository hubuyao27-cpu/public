# Failure Modes & Edge Cases Playbook

When executing LaTeX writing, migration, or compilation tasks under strict Harbor budgets, follow these exact decision rules instead of guessing or fabricating missing files.

---

## 1. Missing Template `.cls` or `.sty` (`File 'xxx.cls' not found`)
1. Search all subdirectories of `/input/` and `/app/work/` for the missing `.cls`/`.sty`/`.bst` file.
2. Check `kpsewhich <filename>` to verify whether it exists anywhere in `TEXMF`.
3. **If found in a subfolder of `/input/`**: Copy it alongside the main `.tex` build directory (or set `TEXINPUTS`) and record the path in `reproducibility_manifest.json`.
4. **If genuinely absent from both `/input/` and `TEXMF`**:
   - For **non-essential aesthetic packages** (e.g., decorative font/border styles): substitute standard TeX Live equivalents, record in `change_records[]`, and note under `uncertainty[]`.
   - For **mandatory publisher `.cls`/`.sty`** (e.g., proprietary `custom-journal.cls`): do **NOT** fake compilation under `article` and claim full template compliance. Clearly explain the missing class, provide clean template-ready `.tex` using standard hooks + explicit `[AUTHOR_CONFIRM: GAP-XX]`, set `"status": "partial"` (or `"unable_to_determine"` if nothing can proceed), and record the missing file in `uncertainty[]` and `author_confirmation_items[]`.

## 2. Missing CJK / Custom Fonts (`fontspec Error` / `Missing character`)
1. Always read `environment.cjk_fonts` from `case_plan.json` (gathered from `fc-list :lang=zh family`).
2. Never hardcode `SimSun`, `SimHei`, `FangSong`, `Microsoft YaHei`, or `Times New Roman` unless `fc-list` confirms they are installed.
3. Fallback priority on Linux TeX Live:
   - Serif CJK: `Noto Serif CJK SC` -> `FandolSong` -> `AR PL UMing CN`
   - Sans CJK: `Noto Sans CJK SC` -> `FandolHei` -> `WenQuanYi Micro Hei`
   - Or `\documentclass[UTF8,fontset=fandol]{ctexart}` / `\documentclass[UTF8,fontset=ubuntu]{ctexart}` depending on installed fonts.
4. If `.log` emits `Missing character: There is no ... in font ...`, escalate immediately — fix the font or wrap math glyphs (`≤`, `≥`, `±`, Greek letters) in math mode (`$\le$`, `$\ge$`, `$\pm$`, `$\alpha$`).

## 3. Cascading Syntax Errors
- One missing `}` or unclosed `\begin{...}` on line 42 often triggers 20+ false errors (`Undefined control sequence`, `Extra }`, `Emergency stop`) at line 250.
- **Rule**: Never patch downstream cascade lines first! Run `analyze_latex.py` (bracket/environment stack pinpoints the unclosed opener) and `compile_and_diagnose.py` (clusters downstream errors to root cause). Fix the earliest root cause, then recompile.

## 4. Circular `\input` / `\include` or Wrong Entry `.tex`
- If `/input/` contains `main.tex`, `chap1.tex`, `appendix.tex`:
  - `analyze_latex.py` builds the include graph and identifies files containing `\documentclass` and `\begin{document}` as entry points.
  - Never try to compile `chap1.tex` directly unless it uses `subfiles`.
  - If a circular `\input` exists (`a.tex -> b.tex -> a.tex`), `analyze_latex.py` detects the cycle immediately so you break it before exceeding TeX memory.

## 5. Missing Figures (`File 'fig1.png' not found`)
- First search `/input/` case-insensitively (`fig1.pdf`, `figures/fig1.png`, etc.) and update `\graphicspath` or path spelling.
- If the figure binary genuinely does not exist in `/input/`:
  - **NEVER** draw a fake blank PNG or invent scientific plots to hide a missing figure.
  - Guard the `\includegraphics` with a clearly commented placeholder or `\fbox{\parbox{0.8\linewidth}{[MISSING FIGURE: fig1.png — AUTHOR_CONFIRM: GAP-XX]}}` so compilation succeeds honestly, and log `GAP-XX` in `uncertainty[]` and `author_confirmation_items[]`.

## 6. Missing / Conflicting Numbers, Citations, or Draft Inputs
- **Conflicting sources** (e.g., `draft.md` says `92.4%` while `results.csv` says `91.8%`): do NOT silently pick one unless hierarchy is explicitly given in `task.md`. Disclose both in `uncertainty[]` and `author_confirmation_items[]` and mark `[AUTHOR_CONFIRM: GAP-XX]` in `.tex`.
- **Missing citations**: never invent BibTeX entries, authors, DOIs, or publication years. Mark `\cite{MISSING_REF}` cleanly as a flagged comment/todo (`GAP-XX`) or keep existing placeholder key flagged in `author_confirmation_items[]` and explain why `.bib` lacks it.
- **Encrypted / scanned / unreadable PDFs or permission errors**: report `"status": "unable_to_determine"` (or `"partial"` if other readable files exist) with exact OS/PDF error messages in `uncertainty[]`.

## 7. Budget & Early Checkpoint Rule (64 requests / 900s)
- Immediately after `analyze_latex.py`, run `assemble_deliverables.py --checkpoint partial` so `/app/output/result.json` always exists even under timeout pressure.
- Keep `compile_and_diagnose.py` bounded (`--max-passes 4`, per-command timeout `60s`) so a broken infinite loop never hangs the container.
