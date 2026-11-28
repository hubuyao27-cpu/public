# Minimal Illustrative Usage Example

> **[ILLUSTRATIVE EXAMPLE — NOT A FORMAL BENCHMARK CASE]**
> This walkthrough demonstrates the streamlined 3-step invocation interface, intermediate diagnostics, repaired `.tex`/`.bib`, and final `result.json` structure using a synthetic sample project.

---

## 1. Illustrative Input (`/input/`)

- `/input/case.json`:
```json
{
  "case_id": "illustrative-ieee-repair-01",
  "output_contract": {
    "case_id": "string",
    "status": "string",
    "primary_tex_path": "string",
    "summary": "string",
    "sources": "array",
    "change_records": "array",
    "uncertainty": "array"
  }
}
```

- `/input/task.md`:
> Repair `main.tex` and `refs.bib` using `IEEEtran` rules so it compiles cleanly with zero undefined citations/references, replaces `\resizebox` table scaling with clean column wrapping, and flags any missing citation metadata.

- `/input/main.tex` (broken excerpt):
```latex
\documentclass[conference]{IEEEtran}
\usepackage{natbib}
\usepackage{biblatex}
\usepackage{graphicx}
\begin{document}
\title{Catalytic Performance Summary}
\author{A. Researcher}
\maketitle
\begin{abstract}
We measure 94.2\% selectivity at 300 K \cite{smith2024,missing2025}.
\end{abstract}
\section{Results}
Table~\ref{tab:perf} summarizes conversion.
\begin{table}[h]
\label{tab:perf}
\resizebox{\textwidth}{!}{%
\begin{tabular}{lcc}
\hline
Catalyst & Temp (K) & Selectivity (\%) \\
\hline
Ni-N-C & 300 & 94.2 \\
\hline
\end{tabular}}
\caption{Performance summary.}
\end{table}
\bibliographystyle{IEEEtran}
\bibliography{refs}
\end{document}
```

---

## 2. Step 1: Prepare, Stage, Safe-Autofix & Partial Checkpoint

```bash
export SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output

python3 -B "$SKILL_DIR/scripts/prepare_and_audit.py" \
  --input-dir /input --work-dir /app/work --out-dir /app/output --apply-safe-fixes
```

### Illustrative Output Excerpt from `/app/work/latex_digest.md`
```markdown
- **Recommended engine**: `pdflatex` | **Bibliography tool**: `biber`
- **Static findings**: 3 errors, 4 warnings
- **[ERROR LINT-PKG-CONFLICT]** `main.tex:3` — Incompatible packages 'natbib' and 'biblatex'.
- **[ERROR LINT-LABEL-BEFORE-CAPTION]** `main.tex:15` — \label{tab:perf} appears BEFORE \caption inside table.
- **[ERROR LINT-MISSING-CITE-KEY]** `main.tex:10` — Citation key 'missing2025' missing from .bib.
- **[WARNING LINT-ANTIPATTERN]** `main.tex:16` — Masking wide-table overflow with \resizebox distorts font size.
- **[WARNING LINT-TABLE-CAPTION-BOTTOM]** `main.tex:23` — Table \caption appears below tabular.
```

---

## 3. Step 2: Repaired `/app/work/src/main.tex` & Multi-Pass Compilation

```latex
\documentclass[conference]{IEEEtran}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{graphicx}
\begin{document}
\title{Catalytic Performance Summary}
\author{A. Researcher}
\maketitle
\begin{abstract}
We measure 94.2\% selectivity at 300 K \cite{smith2024}. % [SRC: main.tex:L10] [AUTHOR_CONFIRM: GAP-01 missing2025 omitted/pending bib entry]
\end{abstract}
\begin{IEEEkeywords}
catalysis, selectivity % [AUTHOR_CONFIRM: GAP-02 confirm IEEEkeywords]
\end{IEEEkeywords}
\section{Results}
Table~\ref{tab:perf} summarizes conversion.
\begin{table}[htbp]
\centering
\caption{Performance summary.}
\label{tab:perf}
\begin{tabularx}{\columnwidth}{Xcc}
\toprule
Catalyst & Temp (K) & Selectivity (\%) \\
\midrule
Ni-N-C & 300 & 94.2 \\
\bottomrule
\end{tabularx}
\end{table}
\bibliographystyle{IEEEtran}
\bibliography{refs}
\end{document}
```

```bash
python3 -B "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/main.tex --project-root /app/work/src \
  --work-dir /app/work/build --preview-dir /app/work/previews \
  --out-report /app/work/compile_report.json --out-summary /app/work/compile_summary.md
```

---

## 4. Step 3: Auto-Hydrated Verification & Final Output (`result.json`)

```bash
python3 -B "$SKILL_DIR/scripts/verify_and_assemble.py" \
  --tex /app/work/src/main.tex --project-root /app/work/src \
  --payload /app/work/deliverable_payload.json \
  --work-dir /app/work --out-dir /app/output
```

Illustrative `/app/output/result.json` excerpt:
```json
{
  "case_id": "illustrative-ieee-repair-01",
  "status": "completed",
  "primary_tex_path": "main.tex",
  "summary": "Removed conflicting biblatex package, moved table caption above tabular with label after caption, replaced resizebox with booktabs+tabularx, verified 3-pass pdflatex+bibtex convergence, and flagged missing2025 for author confirmation.",
  "sources": [
    {"id": "SRC-01", "fact_or_target": "94.2% selectivity at 300 K", "source_locator": "main.tex:L10", "target_locator": "main.tex:L10", "verification_status": "verified"}
  ],
  "change_records": [
    {"file": "main.tex", "locator": "L3", "change_type": "package_conflict_fix", "description": "Removed biblatex conflicting with natbib/IEEEtran.", "reason": "Incompatible bibliography drivers."}
  ],
  "uncertainty": [
    {"id": "GAP-01", "category": "missing_citation", "description": "Key 'missing2025' absent from refs.bib.", "impact": "Tagged for author confirmation rather than fabricating bib entry."}
  ]
}
```
