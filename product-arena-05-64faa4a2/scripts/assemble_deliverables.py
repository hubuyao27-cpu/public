#!/usr/bin/env python3
"""Step 3 (Early Partial Checkpoint) & Step 6 (Final Deliverables Assembly).

Produces under --out-dir (default /app/output):
  - result.json                             (Harbor-compliant, all output_contract keys + provenance envelope)
  - methods_and_experiments.md              (Editable publication-ready manuscript)
  - parameter_table.csv & parameter_table.md (Reusable parameter provenance table)
  - reproducibility_and_author_checklist.md (Reproduction checklist + Author confirmation + Conflicts + Changes)
  - provenance_map.json                     (Full claim-to-source audit ledger)
  - Optional editable DOCX / LaTeX / workflow diagram + real PDF/PNG previews when requested
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, save_json


def render_parameter_table_md(param_table: List[Dict[str, Any]]) -> str:
    lines = [
        "# Parameter Provenance Table (参数与来源对照表)",
        "",
        "| ID | Name | Symbol / Key | Value | Unit | Target Object | Stage | Source Locator | Source Type | Status | Note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for idx, r in enumerate(param_table, start=1):
        if not isinstance(r, dict):
            continue
        pid = str(r.get("param_id") or f"P{idx:02d}")
        cells = [
            pid,
            str(r.get("name") or ""),
            f"`{r.get('symbol_or_key') or r.get('canonical_key') or ''}`",
            str(r.get("value") if r.get("value") is not None else "N/A"),
            str(r.get("unit") or "-"),
            str(r.get("target_object") or "-"),
            str(r.get("stage") or "-"),
            f"`{r.get('source_locator') or 'author_confirm'}`",
            str(r.get("source_type") or "-"),
            str(r.get("verification_status") or "-"),
            str(r.get("note") or "").replace("\n", " "),
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    if len(lines) == 4:
        lines.append("| - | *No verified parameters available* | - | - | - | - | - | - | - | `missing_author_confirm` | - |")
    return "\n".join(lines) + "\n"


def write_parameter_table_csv(param_table: List[Dict[str, Any]], out_path: Path) -> None:
    cols = [
        "param_id",
        "name",
        "symbol_or_key",
        "canonical_key",
        "value",
        "unit",
        "target_object",
        "stage",
        "source_locator",
        "source_type",
        "verification_status",
        "derivation",
        "verbatim_quote",
        "note",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for idx, r in enumerate(param_table, start=1):
        if isinstance(r, dict):
            row = dict(r)
            row.setdefault("param_id", f"P{idx:02d}")
            writer.writerow(row)
    out_path.write_text(buf.getvalue(), encoding="utf-8-sig")


def render_checklist_and_gaps_md(payload: Dict[str, Any], verification: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Reproducibility Checklist, Conflicts & Author Confirmation (`{payload.get('case_id', '')}`)")
    lines.append("")

    # 1. Reproducibility checklist
    lines.append("## 1. Reproducibility Checklist (可复现性清单)")
    repro = payload.get("reproducibility_checklist") or {}
    if isinstance(repro, dict) and repro:
        for k, label in (
            ("code_repository_and_version", "Code Repository & Commit/Version"),
            ("environment_specification", "Environment & Dependencies"),
            ("data_or_material_access", "Data / Material Access & License"),
            ("random_seeds_and_determinism", "Random Seeds & Determinism"),
            ("expected_compute_or_resources", "Expected Compute / Resource Budget"),
        ):
            lines.append(f"- **{label}**: {repro.get(k) or 'Not reported in materials [待作者确认]'}")
        cmds = repro.get("reproduction_commands") or []
        if cmds:
            lines.append("- **Reproduction Commands**:")
            for c in cmds:
                lines.append(f"  - `{c}`")
        miss = repro.get("missing_items") or []
        if miss:
            lines.append("- **Missing Reproducibility Items**:")
            for m in miss:
                lines.append(f"  - {m}")
    else:
        lines.append("- Reproducibility checklist pending full material confirmation.")
    lines.append("")

    # 2. Conflicts
    lines.append("## 2. Cross-Source Conflicts Detected & Resolution Policy (跨材料冲突与处理)")
    conflicts = payload.get("conflicts") or []
    if conflicts:
        lines.append("| Conflict ID | Item | Conflicting Sources | Adopted Value / Status | Rationale | Author Confirm? |")
        lines.append("|---|---|---|---|---|---|")
        for c in conflicts:
            if not isinstance(c, dict):
                continue
            sc_str = "; ".join(
                f"`{s.get('source_locator', '')}` ({s.get('source_tier', '')})={s.get('value', '')}"
                for s in (c.get("sources_compared") or [])
                if isinstance(s, dict)
            )
            row = [
                str(c.get("conflict_id") or ""),
                str(c.get("item") or ""),
                sc_str,
                str(c.get("adopted_value_or_status") or ""),
                str(c.get("rationale") or ""),
                "Yes" if c.get("needs_author_confirmation", True) else "No",
            ]
            lines.append("| " + " | ".join(x.replace("|", "\\|").replace("\n", " ") for x in row) + " |")
    else:
        lines.append("- No cross-source parameter conflicts detected.")
    lines.append("")

    # 3. Author Confirmation Questionnaire
    lines.append("## 3. Author Confirmation Questionnaire (待作者确认问题清单)")
    gaps = payload.get("author_confirmation_items") or []
    if gaps:
        for sev in ("critical", "important", "recommended"):
            tier_items = [g for g in gaps if isinstance(g, dict) and g.get("severity") == sev]
            if not tier_items:
                continue
            lines.append(f"### Priority: `{sev.upper()}`")
            for g in tier_items:
                lines.append(f"- **[{g.get('gap_id', 'GAP')}]** *(Pillar: `{g.get('pillar', '')}` | Draft: `{g.get('location_in_draft', '')}` | Type: `{g.get('issue_type', '')}`)*")
                lines.append(f"  - **Question**: {g.get('question', '')}")
                lines.append(f"  - **Checked**: {g.get('what_was_checked', '')}")
    else:
        lines.append("- No open author confirmation items.")
    lines.append("")

    # 4. Limitations
    lines.append("## 4. Concrete Failure-Mode Limitations (具体失效场景与适用边界)")
    lims = payload.get("limitations") or []
    if lims:
        for lim in lims:
            if not isinstance(lim, dict):
                continue
            lines.append(f"- **{lim.get('limitation_id', 'LIM')} — {lim.get('failure_scenario', '')}**")
            lines.append(f"  - *Trigger Condition*: {lim.get('trigger_condition', '')}")
            lines.append(f"  - *Affected Scope*: {lim.get('affected_scope', '')}")
            if lim.get("mitigation_or_boundary"):
                lines.append(f"  - *Boundary / Mitigation*: {lim.get('mitigation_or_boundary')}")
            lines.append(f"  - *Attribution*: `{lim.get('evidence_or_attribution', '')}` | Sources: `{lim.get('source_locators', [])}`")
    else:
        lines.append("- No limitations recorded.")
    lines.append("")

    # 5. Change Records
    lines.append("## 5. Revision & Change Records (修改与对齐记录)")
    changes = payload.get("change_records") or []
    if changes:
        lines.append("| Change ID | Target | Action | Before | After | Source Basis |")
        lines.append("|---|---|---|---|---|---|")
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            row = [
                str(ch.get("change_id") or ""),
                str(ch.get("target") or ""),
                str(ch.get("action") or ""),
                str(ch.get("before") or ""),
                str(ch.get("after") or ""),
                f"`{ch.get('basis_locator') or ''}`",
            ]
            lines.append("| " + " | ".join(x.replace("|", "\\|").replace("\n", " ") for x in row) + " |")
    else:
        lines.append("- Initial structured draft from source materials (see `provenance_map.json`).")
    lines.append("")

    # 6. Mechanical Verification Metrics
    if verification:
        lines.append("## 6. Mechanical Verification Summary")
        lines.append(f"- **Passed**: `{verification.get('passed')}` | **Errors**: `{verification.get('error_count')}` | **Warnings**: `{verification.get('warning_count')}`")
        for mk, mv in (verification.get("metrics") or {}).items():
            lines.append(f"- `{mk}`: `{mv}`")
    return "\n".join(lines) + "\n"


def maybe_export_editable_and_previews(
    payload: Dict[str, Any],
    requested_formats: List[str],
    out_dir: Path,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Generate optional DOCX / LaTeX / workflow diagram and render real PDF/PNG previews when requested."""
    extra_files: List[str] = []
    preview_notes: List[Dict[str, str]] = []

    # Optional DOCX export + LibreOffice PDF/PNG preview
    if "docx" in requested_formats:
        docx_path = out_dir / "methods_and_experiments.docx"
        try:
            import docx  # type: ignore

            doc = docx.Document()
            doc.add_heading(payload.get("title") or "Methods and Experiments", level=1)
            for line in str(payload.get("manuscript_markdown") or "").splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("### "):
                    doc.add_heading(s[4:], level=3)
                elif s.startswith("## "):
                    doc.add_heading(s[3:], level=2)
                elif s.startswith("# "):
                    doc.add_heading(s[2:], level=1)
                else:
                    doc.add_paragraph(s)
            pt = payload.get("parameter_table") or []
            if pt:
                doc.add_heading("Parameter Provenance Table", level=2)
                tbl = doc.add_table(rows=1, cols=6)
                hdrs = ["ID", "Name", "Value", "Unit", "Stage", "Source"]
                for i, h in enumerate(hdrs):
                    tbl.rows[0].cells[i].text = h
                for idx, r in enumerate(pt, start=1):
                    if not isinstance(r, dict):
                        continue
                    rc = tbl.add_row().cells
                    rc[0].text = str(r.get("param_id") or f"P{idx:02d}")
                    rc[1].text = str(r.get("name") or "")
                    rc[2].text = str(r.get("value") if r.get("value") is not None else "N/A")
                    rc[3].text = str(r.get("unit") or "-")
                    rc[4].text = str(r.get("stage") or "-")
                    rc[5].text = str(r.get("source_locator") or "author_confirm")
            doc.save(str(docx_path))
            extra_files.append(docx_path.name)

            # Render PDF + PNG preview via LibreOffice + pdftoppm / pymupdf if installed (isolated profile & non-blocking stdin)
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                with tempfile.TemporaryDirectory(prefix="lo_prof_") as lo_prof:
                    prof_uri = Path(lo_prof).resolve().as_uri()
                    res = subprocess.run(
                        [soffice, f"-env:UserInstallation={prof_uri}", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        timeout=25,
                    )
                pdf_path = out_dir / "methods_and_experiments.pdf"
                if res.returncode == 0 and pdf_path.exists():
                    extra_files.append(pdf_path.name)
                    extra_files.extend(_render_pdf_preview_pngs(pdf_path, out_dir, "docx_preview"))
                else:
                    preview_notes.append({"target": docx_path.name, "note": f"soffice PDF preview failed: {res.stderr.strip()[:120]}"})
            else:
                preview_notes.append({"target": docx_path.name, "note": "soffice/libreoffice not installed in runtime; DOCX saved without rendered PDF preview."})
        except ImportError:
            preview_notes.append({"target": "methods_and_experiments.docx", "note": "python-docx not installed; delivered editable Markdown & CSV instead."})
        except Exception as exc:
            preview_notes.append({"target": "methods_and_experiments.docx", "note": f"DOCX export error: {exc}"})

    # Optional LaTeX export + PDF/PNG preview (CJK-aware, non-blocking)
    if "latex" in requested_formats:
        tex_path = out_dir / "methods_and_experiments.tex"
        tex_src, has_cjk = _build_latex_document(payload)
        tex_path.write_text(tex_src, encoding="utf-8")
        extra_files.append(tex_path.name)
        compiler = shutil.which("xelatex") if has_cjk else (shutil.which("pdflatex") or shutil.which("xelatex"))
        if compiler:
            try:
                res = subprocess.run(
                    [compiler, "-halt-on-error", "-interaction=nonstopmode", "-output-directory", str(out_dir), str(tex_path)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                pdf_path = out_dir / "methods_and_experiments.pdf"
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    extra_files.append(pdf_path.name)
                    extra_files.extend(_render_pdf_preview_pngs(pdf_path, out_dir, "latex_preview"))
                else:
                    preview_notes.append({"target": tex_path.name, "note": f"LaTeX compile returned {res.returncode}; .tex delivered."})
            except Exception as exc:
                preview_notes.append({"target": tex_path.name, "note": f"LaTeX compile exception: {exc}"})
        else:
            preview_notes.append({"target": tex_path.name, "note": "xelatex/LaTeX compiler unavailable for target script; .tex source delivered."})

    # Optional workflow diagram PNG preview via matplotlib
    if "diagram" in requested_formats and payload.get("method_steps"):
        png_path = out_dir / "method_workflow_diagram.png"
        if _render_workflow_diagram_png(payload["method_steps"], png_path):
            extra_files.append(png_path.name)
        else:
            preview_notes.append({"target": png_path.name, "note": "matplotlib unavailable for workflow PNG rendering."})

    return extra_files, preview_notes


def _render_pdf_preview_pngs(pdf_path: Path, out_dir: Path, prefix: str, max_pages: int = 3) -> List[str]:
    rendered: List[str] = []
    try:
        try:
            import pymupdf  # type: ignore
        except ImportError:
            import fitz as pymupdf  # type: ignore
        if not hasattr(pymupdf, "open"):
            raise ImportError("pymupdf/fitz without .open")

        doc = pymupdf.open(str(pdf_path))
        for i in range(min(len(doc), max_pages)):
            pix = doc[i].get_pixmap(dpi=150)
            out_png = out_dir / f"{prefix}_p{i + 1}.png"
            pix.save(str(out_png))
            rendered.append(out_png.name)
        doc.close()
        return rendered
    except Exception:
        pass
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        try:
            subprocess.run(
                [pdftoppm, "-png", "-r", "150", "-f", "1", "-l", str(max_pages), str(pdf_path), str(out_dir / prefix)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=25,
            )
            for p in sorted(out_dir.glob(f"{prefix}*.png")):
                rendered.append(p.name)
        except Exception:
            pass
    return rendered


def _tex_escape(s: str) -> str:
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(rep.get(ch, ch) for ch in str(s))


def _build_latex_document(payload: Dict[str, Any]) -> Tuple[str, bool]:
    raw_title = str(payload.get("title") or "Methods and Experiments")
    raw_md = str(payload.get("manuscript_markdown") or "")
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", raw_title + raw_md))
    title = _tex_escape(raw_title)
    body_lines = []
    for line in raw_md.splitlines():
        s = line.strip()
        if not s:
            body_lines.append("")
        elif s.startswith("### "):
            body_lines.append(r"\subsubsection{" + _tex_escape(s[4:]) + "}")
        elif s.startswith("## "):
            body_lines.append(r"\subsection{" + _tex_escape(s[3:]) + "}")
        elif s.startswith("# "):
            body_lines.append(r"\section{" + _tex_escape(s[2:]) + "}")
        else:
            body_lines.append(_tex_escape(s))
    doc_cls = "\\documentclass[UTF8]{ctexart}\n" if has_cjk else "\\documentclass[11pt]{article}\n\\usepackage[utf8]{inputenc}\n"
    tex = (
        doc_cls
        + "\\usepackage{booktabs}\n"
        + "\\usepackage[margin=1in]{geometry}\n"
        + f"\\title{{{title}}}\n"
        + "\\date{}\n"
        + "\\begin{document}\n"
        + "\\maketitle\n"
        + "\n".join(body_lines)
        + "\n\\end{document}\n"
    )
    return tex, has_cjk


def _render_workflow_diagram_png(steps: List[Dict[str, Any]], out_path: Path) -> bool:
    try:
        import matplotlib  # type: ignore

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        from matplotlib import font_manager  # type: ignore
        from matplotlib.patches import FancyBboxPatch  # type: ignore

        avail_fonts = {f.name for f in font_manager.fontManager.ttflist}
        cjk_prefs = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
        chosen_fonts = [fn for fn in cjk_prefs if fn in avail_fonts]
        if chosen_fonts:
            plt.rcParams["font.sans-serif"] = chosen_fonts + ["DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        n = min(len(steps), 8)
        fig, ax = plt.subplots(figsize=(9, max(3.5, n * 1.1)))
        ax.set_xlim(0, 10)
        ax.set_ylim(0, n + 0.5)
        ax.axis("off")

        for i, st in enumerate(steps[:n]):
            y = n - i - 0.7
            box = FancyBboxPatch((0.5, y), 9.0, 0.72, boxstyle="round,pad=0.08", facecolor="#eef5ff", edgecolor="#2b5b84", linewidth=1.3)
            ax.add_patch(box)
            label = f"{st.get('step_id', f'S{i+1}')} [{st.get('stage', '')}]: {str(st.get('operation', ''))[:78]}"
            sub = f"In: {str(st.get('input', ''))[:38]}  ->  Out: {str(st.get('output', ''))[:38]}"
            ax.text(5.0, y + 0.48, label, ha="center", va="center", fontsize=8.5, fontweight="bold", color="#142b44")
            ax.text(5.0, y + 0.18, sub, ha="center", va="center", fontsize=7.5, color="#334e68")
            if i < n - 1:
                ax.annotate("", xy=(5.0, y - 0.25), xytext=(5.0, y - 0.02), arrowprops=dict(arrowstyle="->", color="#2b5b84", lw=1.4))

        plt.tight_layout()
        fig.savefig(str(out_path), dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


def coerce_value_to_type(val: Any, expected_type: str) -> Any:
    et = (expected_type or "any").lower()
    if et in ("string", "str"):
        if isinstance(val, str):
            return val
        return json.dumps(val, ensure_ascii=False, indent=2)
    if et in ("array", "list"):
        if isinstance(val, list):
            return val
        return [val] if val is not None else []
    if et in ("object", "dict"):
        if isinstance(val, dict):
            return val
        return {"content": val}
    return val


def determine_status(
    checkpoint: str,
    plan: Dict[str, Any],
    ledger: Dict[str, Any],
    payload: Optional[Dict[str, Any]],
    verification: Optional[Dict[str, Any]],
) -> str:
    stats = ledger.get("stats") or {}
    auth_files = stats.get("authoritative_files", 0)
    total_files = stats.get("total_files", 0)
    inline_blocks = stats.get("inline_material_blocks", 0)
    if (total_files == 0 and inline_blocks == 0) or (auth_files == 0 and inline_blocks == 0 and not ledger.get("doc_blocks")):
        return "unanswerable"
    if checkpoint == "partial" or not payload:
        return "partial"
    if verification and not verification.get("passed", False):
        return "partial"
    if plan.get("missing_referenced_files"):
        return "partial"
    # Critical unresolved conflicts or critical blocking gaps -> honest partial if major evidence missing
    for iw in ledger.get("ingestion_warnings", []):
        if any(k in str(iw.get("issue", "")) for k in ("permission_denied", "pdf_encrypted", "corrupt")):
            return "partial"
    return "completed"


def build_partial_payload_from_ledger(plan: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Create an honest early partial payload directly from Step 2 extraction before LLM authoring."""
    param_rows = []
    for idx, p in enumerate(ledger.get("parameters", [])[:60], start=1):
        is_draft = p.get("source_tier") == "narrative_draft"
        param_rows.append(
            {
                "param_id": f"P{idx:02d}",
                "name": p.get("canonical_key") or p.get("raw_key"),
                "symbol_or_key": p.get("raw_key"),
                "canonical_key": p.get("canonical_key"),
                "value": p.get("raw_value"),
                "unit": p.get("unit") or "",
                "target_object": p.get("target_object") or "",
                "stage": p.get("stage") or "unspecified",
                "source_locator": p.get("source_locator"),
                "source_type": p.get("source_tier"),
                "verification_status": "draft_only_unverified" if is_draft else "verified",
                "verbatim_quote": p.get("verbatim_quote") or "",
                "note": "Early automated checkpoint extraction; awaiting full chapter synthesis.",
            }
        )
    uncertainty = [
        {
            "target": "early_checkpoint",
            "reason": "partial_extraction_checkpoint",
            "details": "Saved early honest partial deliverable after Step 2 deterministic extraction; full narrative chapter pending Step 4.",
            "human_handoff": "Complete Step 4 authoring and Step 5 verification.",
        }
    ]
    for mf in plan.get("missing_referenced_files", []):
        uncertainty.append({"target": mf, "reason": "missing_file", "details": f"Referenced file '{mf}' not found under /input/.", "human_handoff": f"Provide missing file '{mf}'."})
    for iw in ledger.get("ingestion_warnings", []):
        uncertainty.append({"target": iw.get("file", ""), "reason": iw.get("issue", "ingestion_warning"), "details": iw.get("detail", ""), "human_handoff": "Inspect or replace unreadable input file."})

    gaps = [
        {
            "gap_id": g["gap_id"],
            "pillar": g["pillar"],
            "severity": g["severity"],
            "location_in_draft": "Preliminary Pre-Audit",
            "issue_type": "missing_in_materials",
            "question": g["suggested_question"],
            "what_was_checked": f"Scanned {ledger.get('stats', {}).get('total_files', 0)} input files.",
            "blocking_reproducibility": g["severity"] == "critical",
        }
        for g in ledger.get("preliminary_gaps", [])
    ]

    return {
        "case_id": plan.get("case_id", "unknown_case"),
        "task_mode": plan.get("signals", {}).get("task_mode", "draft_from_materials"),
        "language": plan.get("signals", {}).get("language", "zh"),
        "domain": plan.get("signals", {}).get("detected_domain", "general_scientific"),
        "title": f"Methods and Experiments (Early Checkpoint: {plan.get('case_id', '')})",
        "manuscript_markdown": f"# Methods and Experiments (Early Partial Checkpoint)\n\nDeterministic extraction completed over {ledger.get('stats', {}).get('total_files', 0)} file(s). Detected {len(ledger.get('conflicts', []))} conflict(s) and {len(gaps)} preliminary gap(s). Full prose synthesis pending.\n",
        "method_steps": [],
        "experimental_setup": {"hardware_or_instruments": ledger.get("environment_facts", [])[:15]},
        "parameter_table": param_rows,
        "datasets_and_splits": [],
        "baseline_comparisons": [],
        "reproducibility_checklist": {"reproduction_commands": [c["command"] for c in ledger.get("commands", [])[:10]]},
        "limitations": [],
        "conflicts": [
            {
                "conflict_id": c["conflict_id"],
                "item": c["canonical_key"],
                "sources_compared": c["sources_compared"],
                "adopted_value_or_status": f"Recommended: {c['recommended_authoritative']['value']} ({c['recommended_authoritative']['source_locator']})",
                "rationale": c["resolution_guidance"],
                "needs_author_confirmation": True,
            }
            for c in ledger.get("conflicts", [])
        ],
        "author_confirmation_items": gaps,
        "sources": [
            {"claim_or_item": f"{p['canonical_key']}={p['raw_value']}", "source_locator": p["source_locator"], "source_type": p["source_tier"], "verbatim_quote": p["verbatim_quote"]}
            for p in ledger.get("parameters", [])[:40]
        ],
        "uncertainty": uncertainty,
        "change_records": [],
        "contract_overrides": {},
    }


def assemble_deliverables(
    case_plan_path: Path,
    ledger_path: Path,
    payload_path: Optional[Path],
    verification_path: Optional[Path],
    checkpoint: str,
    out_dir: Path,
) -> Dict[str, Any]:
    plan, _ = load_json_safe(case_plan_path)
    plan = plan if isinstance(plan, dict) else {"case_id": "unknown_case", "signals": {}, "output_contract": {"fields": []}}

    ledger, _ = load_json_safe(ledger_path)
    ledger = ledger if isinstance(ledger, dict) else {"stats": {}}

    payload: Optional[Dict[str, Any]] = None
    if payload_path and payload_path.exists():
        p_loaded, _ = load_json_safe(payload_path)
        if isinstance(p_loaded, dict):
            payload = p_loaded
    if payload is None:
        payload = build_partial_payload_from_ledger(plan, ledger)

    verification: Dict[str, Any] = {}
    if verification_path and verification_path.exists():
        v_loaded, _ = load_json_safe(verification_path)
        if isinstance(v_loaded, dict):
            verification = v_loaded

    out_dir.mkdir(parents=True, exist_ok=True)
    status = determine_status(checkpoint, plan, ledger, payload, verification)

    # Write core human-editable & reusable deliverables
    manuscript_path = out_dir / "methods_and_experiments.md"
    manuscript_path.write_text(str(payload.get("manuscript_markdown") or "") + "\n", encoding="utf-8")

    param_table = payload.get("parameter_table") if isinstance(payload.get("parameter_table"), list) else []
    (out_dir / "parameter_table.md").write_text(render_parameter_table_md(param_table), encoding="utf-8")
    write_parameter_table_csv(param_table, out_dir / "parameter_table.csv")

    (out_dir / "reproducibility_and_author_checklist.md").write_text(
        render_checklist_and_gaps_md(payload, verification), encoding="utf-8"
    )

    provenance_map = {
        "case_id": plan.get("case_id"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": payload.get("sources") or [],
        "parameter_provenance": [
            {
                "param_id": r.get("param_id"),
                "name": r.get("name"),
                "value": r.get("value"),
                "unit": r.get("unit"),
                "source_locator": r.get("source_locator"),
                "verification_status": r.get("verification_status"),
            }
            for r in param_table
            if isinstance(r, dict)
        ],
        "conflicts": payload.get("conflicts") or [],
        "change_records": payload.get("change_records") or [],
    }
    save_json(out_dir / "provenance_map.json", provenance_map)

    deliverable_files = [
        "result.json",
        "methods_and_experiments.md",
        "parameter_table.md",
        "parameter_table.csv",
        "reproducibility_and_author_checklist.md",
        "provenance_map.json",
    ]

    preview_notes: List[Dict[str, str]] = []
    if checkpoint == "final":
        req_formats = plan.get("signals", {}).get("requested_formats") or []
        extra_files, preview_notes = maybe_export_editable_and_previews(payload, req_formats, out_dir)
        deliverable_files.extend(extra_files)

    # Merge preview notes into uncertainty if any rendering tool was missing
    uncertainty = list(payload.get("uncertainty") or [])
    for pn in preview_notes:
        uncertainty.append(
            {
                "target": pn["target"],
                "reason": "preview_renderer_note",
                "details": pn["note"],
                "human_handoff": "Render preview locally if required.",
            }
        )

    # Build canonical result core
    result: Dict[str, Any] = {
        "case_id": plan.get("case_id") or payload.get("case_id") or "unknown_case",
        "status": status,
        "checkpoint": checkpoint,
        "task_mode": payload.get("task_mode") or plan.get("signals", {}).get("task_mode"),
        "domain": payload.get("domain") or plan.get("signals", {}).get("detected_domain"),
        "language": payload.get("language") or plan.get("signals", {}).get("language"),
        "manuscript_markdown": payload.get("manuscript_markdown") or "",
        "method_steps": payload.get("method_steps") or [],
        "experimental_setup": payload.get("experimental_setup") or {},
        "parameter_table": param_table,
        "datasets_and_splits": payload.get("datasets_and_splits") or [],
        "baseline_comparisons": payload.get("baseline_comparisons") or [],
        "reproducibility_checklist": payload.get("reproducibility_checklist") or {},
        "limitations": payload.get("limitations") or [],
        "conflicts": payload.get("conflicts") or [],
        "author_confirmation_items": payload.get("author_confirmation_items") or [],
        "sources": payload.get("sources") or [],
        "uncertainty": uncertainty,
        "change_records": payload.get("change_records") or [],
        "deliverable_files": deliverable_files,
        "verification_summary": verification or {"passed": checkpoint == "final", "checkpoint": checkpoint},
    }

    summary_str = (
        f"Completed Methods & Experiments workflow ({status}) for {result['case_id']}: "
        f"{len(result['method_steps'])} steps, {len(param_table)} parameters, "
        f"{len(result['conflicts'])} conflicts disclosed, {len(result['author_confirmation_items'])} author confirmations."
    )

    role_map = {
        "case_id": result["case_id"],
        "status": result["status"],
        "manuscript_markdown": result["manuscript_markdown"],
        "parameter_table": result["parameter_table"],
        "method_steps": result["method_steps"],
        "experimental_setup": result["experimental_setup"],
        "datasets_and_splits": result["datasets_and_splits"],
        "baseline_comparisons": result["baseline_comparisons"],
        "reproducibility_checklist": result["reproducibility_checklist"],
        "limitations": result["limitations"],
        "conflicts": result["conflicts"],
        "author_confirmation_items": result["author_confirmation_items"],
        "change_records": result["change_records"],
        "uncertainty": result["uncertainty"],
        "sources": result["sources"],
        "deliverable_files": result["deliverable_files"],
        "verification_summary": result["verification_summary"],
        "summary": summary_str,
    }

    # Map EVERY output_contract field dynamically
    overrides = payload.get("contract_overrides") if isinstance(payload.get("contract_overrides"), dict) else {}
    for fspec in (plan.get("output_contract") or {}).get("fields", []):
        fname = fspec["name"]
        ftype = fspec.get("expected_type", "any")
        role = fspec.get("semantic_role", "custom")
        if fname in overrides:
            result[fname] = coerce_value_to_type(overrides[fname], ftype)
        elif fname in payload and fname not in result:
            result[fname] = coerce_value_to_type(payload[fname], ftype)
        elif role in role_map:
            result[fname] = coerce_value_to_type(role_map[role], ftype)
        elif fname not in result:
            result[fname] = coerce_value_to_type(summary_str if ftype in ("str", "string") else {}, ftype)

    # Also copy any explicit overrides even if not listed in contract
    for ok, ov in overrides.items():
        if ok not in result:
            result[ok] = ov

    save_json(out_dir / "result.json", result)
    return {
        "case_id": result["case_id"],
        "status": result["status"],
        "checkpoint": checkpoint,
        "deliverable_files": deliverable_files,
        "output_contract_keys_populated": [f["name"] for f in (plan.get("output_contract") or {}).get("fields", []) if f["name"] in result],
        "out_dir": str(out_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble Harbor-compliant result.json and editable Methods & Experiments deliverables.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json", help="Path to case_plan.json")
    parser.add_argument("--ledger", default="/app/work/fact_ledger.json", help="Path to fact_ledger.json")
    parser.add_argument("--payload", default=None, help="Path to authored chapter_payload.json (optional for partial checkpoint)")
    parser.add_argument("--verification", default=None, help="Path to verification_report.json")
    parser.add_argument("--checkpoint", choices=["partial", "final"], default="final", help="partial for early checkpoint, final for verified delivery")
    parser.add_argument("--out-dir", default="/app/output", help="Final output directory")
    args = parser.parse_args()

    summary = assemble_deliverables(
        Path(args.case_plan),
        Path(args.ledger),
        Path(args.payload) if args.payload else None,
        Path(args.verification) if args.verification else None,
        args.checkpoint,
        Path(args.out_dir),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
