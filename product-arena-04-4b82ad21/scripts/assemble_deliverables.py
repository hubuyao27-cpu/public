#!/usr/bin/env python3
"""Step 3 (Early Partial Checkpoint) & Step 6 (Final Verified Deliverables Assembly).

Outputs under --out-dir (default /app/output):
- result.json                        (Harbor-compliant with case_id + ALL output_contract keys + provenance)
- introduction_and_related_work.md   (Clean publication-ready manuscript + formatted bibliography)
- related_work_matrix.md & .csv      (Multi-dimensional grouped comparison matrix)
- evidence_and_citation_audit.md     (Independent 7-section verification & author confirmation report)
- provenance_map.json                (Machine-verifiable claim/gap/contribution source links)
- Conditional exports + rendered PDF/PNG previews when requested (tex, bib, docx, diagram)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from common import format_citation_entry, load_json_safe, save_json


def _render_matrix_md(matrix: List[Dict[str, Any]]) -> str:
    lines = [
        "# Related Work Multi-Dimensional Comparison Matrix (相关工作多维对照表)",
        "",
        "| Ref ID | Route / Group | Problem Setting | Method Route | Dataset & Eval | Key Result | Applicability Conditions | Limitations | Source Locator |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in matrix:
        if not isinstance(r, dict):
            continue
        cells = [
            str(r.get("ref_id") or "-"),
            str(r.get("route") or "-"),
            str(r.get("problem_setting") or "-"),
            str(r.get("method_route") or "-"),
            str(r.get("dataset_and_eval") or "-"),
            str(r.get("key_result") or "-"),
            str(r.get("applicability_conditions") or "-"),
            str(r.get("limitations") or "-"),
            f"`{r.get('source_locator') or '-'}`",
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if len(lines) == 4:
        lines.append("| - | *No structured comparisons populated yet* | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def _write_matrix_csv(matrix: List[Dict[str, Any]], out_path: Path) -> None:
    cols = [
        "ref_id",
        "route",
        "problem_setting",
        "method_route",
        "dataset_and_eval",
        "key_result",
        "applicability_conditions",
        "limitations",
        "source_locator",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in matrix:
        if isinstance(r, dict):
            writer.writerow(r)
    out_path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="\n")


def _render_manuscript_md(payload: Dict[str, Any]) -> str:
    style = str(payload.get("citation_style") or "gbt7714")
    if style == "unspecified":
        style = "gbt7714" if payload.get("language") == "zh" else "ieee_numeric"

    lines: List[str] = []
    if payload.get("title"):
        lines.extend([f"# {payload['title']}", ""])
    lines.append(str(payload.get("manuscript_markdown") or "").rstrip())
    lines.extend(["", "## References (参考文献)", ""])
    refs = payload.get("references") or []
    for idx, r in enumerate(refs, start=1):
        if not isinstance(r, dict):
            continue
        fmt = str(r.get("formatted_citation") or "").strip() or format_citation_entry(r, style, idx)
        tag = f" *(Status: `{r.get('verification_status', 'verified')}` | Source: `{r.get('source_locator', '-')}`)*"
        lines.append(f"- {fmt}{tag}")
    if not refs:
        lines.append("- *No verified references supplied in input materials.*")
    return "\n".join(lines) + "\n"


def _render_audit_md(payload: Dict[str, Any], verification: Dict[str, Any]) -> str:
    lines = [
        f"# Evidence, Citation & Provenance Audit Report (`{payload.get('case_id', '')}`)",
        "",
        "## 1. Citation Coverage & Metadata Verification Ledger",
        "| Ref ID | Cite Key | Authors | Year | Venue | DOI/ID | Recency | Status | Source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in payload.get("references") or []:
        if not isinstance(r, dict):
            continue
        cells = [
            str(r.get("ref_id") or "-"),
            f"`{r.get('cite_key') or '-'}`",
            str(r.get("authors") or "-"),
            str(r.get("year") or "-"),
            str(r.get("venue") or "-"),
            str(r.get("doi_or_id") or "-"),
            str(r.get("recency_role") or "-"),
            f"`{r.get('verification_status') or '-'}`",
            f"`{r.get('source_locator') or '-'}`",
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if not (payload.get("references") or []):
        lines.append("| - | - | *Empty literature catalog* | - | - | - | - | `unverified_source` | - |")

    lines.extend(["", "## 2. Claim-to-Citation Mapping (`claim_citation_map`)"])
    for cm in payload.get("claim_citation_map") or []:
        if isinstance(cm, dict):
            lines.append(
                f"- **{cm.get('claim_id', 'C')}** [`{cm.get('section', '')}` | `{cm.get('attribution_type', '')}`]: "
                f"{cm.get('claim_excerpt', '')} -> Citations: `{cm.get('citation_ids', [])}` | Sources: `{cm.get('source_locators', [])}`"
            )

    lines.extend(["", "## 3. Research Gap Derivation Chain"])
    for g in payload.get("research_gaps") or []:
        if isinstance(g, dict):
            lines.append(
                f"- **{g.get('gap_id', 'G')}** (`{g.get('derivation_type', '')}` | `{g.get('attribution', '')}`): "
                f"{g.get('gap_statement', '')} | Cited Refs: `{g.get('supporting_ref_ids', [])}` | Addressed by: {g.get('how_this_work_addresses', '')}"
            )

    lines.extend(["", "## 4. Contribution-to-Evidence Alignment Ledger"])
    for c in payload.get("contributions") or []:
        if isinstance(c, dict):
            lines.append(
                f"- **{c.get('contrib_id', 'CT')}** [`{c.get('verification_status', '')}`]: {c.get('claim_text', '')}\n"
                f"  - Scope: {c.get('scope_qualification', '')}\n"
                f"  - Methods: `{c.get('linked_methods', [])}` | Figs/Tables: `{c.get('linked_figures_tables', [])}` | Metrics: `{c.get('linked_metrics', [])}` | Locators: `{c.get('own_evidence_locators', [])}`"
            )

    lines.extend(["", "## 5. Cross-Source Conflicts & Revision Change Records"])
    for cf in payload.get("conflicts") or []:
        if isinstance(cf, dict):
            lines.append(f"- **{cf.get('conflict_id')}**: {cf.get('item')} -> {cf.get('rationale')} (Adopted: `{cf.get('adopted_value_or_status')}`)")
    for ch in payload.get("change_records") or []:
        if isinstance(ch, dict):
            lines.append(f"- **{ch.get('change_id')}** [`{ch.get('target')}` / `{ch.get('action')}`]: `{ch.get('before')}` -> `{ch.get('after')}` (Basis: `{ch.get('basis_locator')}`)")

    lines.extend(["", "## 6. Author Confirmation Questionnaire"])
    for q in payload.get("author_confirmation_items") or []:
        if isinstance(q, dict):
            lines.append(
                f"- **[{q.get('gap_id', 'GAP')}]** (`{str(q.get('severity', 'important')).upper()}` | `{q.get('location_in_draft', '')}`): "
                f"{q.get('question', '')} *(Checked: {q.get('what_was_checked', '')})*"
            )

    if verification:
        lines.extend(
            [
                "",
                "## 7. Mechanical Verification Summary",
                f"- **Passed**: `{verification.get('passed')}` | **Errors**: `{verification.get('error_count')}` | **Warnings**: `{verification.get('warning_count')}`",
                f"- **Metrics**: `{json.dumps(verification.get('metrics') or {}, ensure_ascii=False)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _latex_escape(txt: str) -> str:
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}
    out = txt
    for k, v in rep.items():
        if k != "\\":
            out = out.replace(k, v)
    return out


def _maybe_export_requested(
    payload: Dict[str, Any],
    requested_formats: List[str],
    out_dir: Path,
) -> Tuple[List[str], List[str]]:
    extra_files: List[str] = []
    notes: List[str] = []

    if "bib" in requested_formats or "tex" in requested_formats:
        bib_path = out_dir / "references.bib"
        bib_entries: List[str] = []
        for idx, r in enumerate(payload.get("references") or [], start=1):
            if not isinstance(r, dict):
                continue
            ck = str(r.get("cite_key") or f"ref{idx}")
            bib_entries.append(
                f"@article{{{ck},\n"
                f"  author = {{{r.get('authors') or 'Unknown'}}},\n"
                f"  title = {{{r.get('title') or 'Untitled'}}},\n"
                f"  journal = {{{r.get('venue') or 'Unknown'}}},\n"
                f"  year = {{{r.get('year') or '0000'}}},\n"
                f"  note = {{{r.get('doi_or_id') or ''}}}\n"
                f"}}"
            )
        bib_path.write_text("\n\n".join(bib_entries) + "\n", encoding="utf-8", newline="\n")
        extra_files.append("references.bib")

    if "tex" in requested_formats:
        tex_path = out_dir / "introduction_and_related_work.tex"
        body = _latex_escape(str(payload.get("manuscript_markdown") or ""))
        title_tex = _latex_escape(str(payload.get("title") or "Introduction and Related Work"))
        tex_src = (
            "\\documentclass[11pt]{article}\n"
            "\\usepackage[utf8]{inputenc}\n"
            "\\usepackage[margin=1in]{geometry}\n"
            f"\\title{{{title_tex}}}\n"
            "\\begin{document}\n"
            "\\maketitle\n"
            "\\begin{verbatim}\n"
            + str(payload.get("manuscript_markdown") or "")[:6000]
            + "\n\\end{verbatim}\n"
            "\\end{document}\n"
        )
        tex_path.write_text(tex_src, encoding="utf-8", newline="\n")
        extra_files.append("introduction_and_related_work.tex")
        compiler = shutil.which("xelatex") or shutil.which("pdflatex")
        if compiler:
            proc = subprocess.run(
                [compiler, "-interaction=nonstopmode", "-output-directory", str(out_dir), str(tex_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            pdf_out = out_dir / "introduction_and_related_work.pdf"
            if proc.returncode == 0 and pdf_out.exists():
                extra_files.append("introduction_and_related_work.pdf")
                if shutil.which("pdftoppm"):
                    subprocess.run(
                        ["pdftoppm", "-png", "-r", "150", str(pdf_out), str(out_dir / "tex_preview")],
                        capture_output=True,
                    )
                    for png in sorted(out_dir.glob("tex_preview*.png")):
                        extra_files.append(png.name)
            else:
                notes.append(f"latex_compile_failed:{proc.returncode}")
        else:
            notes.append("latex_compiler_not_installed:exported_tex_source_only")

    if "docx" in requested_formats:
        docx_path = out_dir / "introduction_and_related_work.docx"
        try:
            import docx  # type: ignore

            doc = docx.Document()
            doc.add_heading(payload.get("title") or "Introduction and Related Work", level=1)
            for ln in str(payload.get("manuscript_markdown") or "").splitlines():
                s = ln.strip()
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
            doc.save(str(docx_path))
            extra_files.append("introduction_and_related_work.docx")
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)], capture_output=True, timeout=30)
                pdf_p = out_dir / "introduction_and_related_work.pdf"
                if pdf_p.exists():
                    extra_files.append("introduction_and_related_work.pdf")
                    if shutil.which("pdftoppm"):
                        subprocess.run(["pdftoppm", "-png", "-r", "150", str(pdf_p), str(out_dir / "docx_preview")], capture_output=True)
                        for png in sorted(out_dir.glob("docx_preview*.png")):
                            extra_files.append(png.name)
            else:
                notes.append("soffice_not_installed:docx_written_without_pdf_preview")
        except ImportError:
            notes.append("python_docx_not_installed:markdown_deliverables_written")

    if "diagram" in requested_formats:
        try:
            import matplotlib  # type: ignore

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt  # type: ignore
            from matplotlib.patches import FancyBboxPatch  # type: ignore

            groups = payload.get("related_work_groups") or []
            gaps = payload.get("research_gaps") or []
            contribs = payload.get("contributions") or []
            fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 6)
            ax.axis("off")
            ax.text(5, 5.65, f"Related Work -> Research Gaps -> Contributions ({payload.get('case_id', '')})", ha="center", va="center", fontsize=11, fontweight="bold")

            def draw_col(x: float, title: str, items: List[str], color: str) -> None:
                ax.text(x + 1.4, 5.1, title, ha="center", va="center", fontsize=10, fontweight="bold")
                for i, it in enumerate(items[:4]):
                    y = 4.2 - i * 1.05
                    box = FancyBboxPatch((x, y), 2.8, 0.78, boxstyle="round,pad=0.08", facecolor=color, edgecolor="#334155", linewidth=1.0)
                    ax.add_patch(box)
                    ax.text(x + 1.4, y + 0.39, it[:42], ha="center", va="center", fontsize=8)

            g_labels = [f"{g.get('group_id', '')}: {g.get('route_name', '')}" for g in groups] or ["No routes"]
            gap_labels = [f"{g.get('gap_id', '')}: {g.get('gap_statement', '')}" for g in gaps] or ["Pending gaps"]
            c_labels = [f"{c.get('contrib_id', '')}: {c.get('claim_text', '')}" for c in contribs] or ["Pending contribs"]
            draw_col(0.3, "Methodological Routes", g_labels, "#E0F2FE")
            draw_col(3.6, "Derived Research Gaps", gap_labels, "#FEF3C7")
            draw_col(6.9, "Aligned Contributions", c_labels, "#DCFCE7")
            ax.annotate("", xy=(3.55, 3.6), xytext=(3.15, 3.6), arrowprops=dict(arrowstyle="->", lw=1.5))
            ax.annotate("", xy=(6.85, 3.6), xytext=(6.45, 3.6), arrowprops=dict(arrowstyle="->", lw=1.5))

            png_p = out_dir / "related_work_taxonomy.png"
            svg_p = out_dir / "related_work_taxonomy.svg"
            fig.savefig(png_p, bbox_inches="tight")
            fig.savefig(svg_p, bbox_inches="tight")
            plt.close(fig)
            extra_files.extend(["related_work_taxonomy.png", "related_work_taxonomy.svg"])
        except Exception as exc:
            notes.append(f"diagram_export_skipped:{exc}")

    return extra_files, notes


def _coerce_type(val: Any, expected_type: str) -> Any:
    et = (expected_type or "").lower()
    if et in ("string", "str"):
        if isinstance(val, str):
            return val
        return json.dumps(val, ensure_ascii=False)
    if et in ("array", "list"):
        if isinstance(val, list):
            return val
        return [val] if val is not None else []
    if et in ("object", "dict"):
        if isinstance(val, dict):
            return val
        return {"value": val}
    if et in ("boolean", "bool"):
        return bool(val)
    return val


def build_partial_payload(plan: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    cid = plan.get("case_id", "")
    sigs = plan.get("signals") or {}
    refs_out = []
    for r in ledger.get("references_catalog") or []:
        refs_out.append(
            {
                "ref_id": r.get("ref_id"),
                "cite_key": r.get("cite_key"),
                "authors": r.get("authors") or "[待补充作者]",
                "year": r.get("year") or "[待补充年份]",
                "title": r.get("title") or "[待补充标题]",
                "venue": r.get("venue") or "[待补充来源]",
                "doi_or_id": r.get("doi_or_id") or "",
                "recency_role": r.get("recency_role", "representative"),
                "source_locator": (r.get("source_locators") or ["-"])[0],
                "verification_status": "partial_metadata" if r.get("missing_metadata_fields") else "verified",
                "formatted_citation": "",
            }
        )
    gaps_confirm = [
        {
            "gap_id": g.get("gap_id"),
            "severity": "important",
            "location_in_draft": "global",
            "issue_type": g.get("issue"),
            "question": f"Please clarify or supply material for: {g.get('detail')}",
            "what_was_checked": "Automated ingestion of /input/",
        }
        for g in (ledger.get("pre_gaps") or [])
    ]
    return {
        "case_id": cid,
        "status": "insufficient_material" if not refs_out and not (ledger.get("own_paper_evidence") or []) else "partial",
        "language": sigs.get("language", "zh"),
        "citation_style": sigs.get("citation_style", "unspecified"),
        "title": f"Partial Checkpoint ({cid})",
        "manuscript_markdown": f"[Partial checkpoint saved before drafting. Discovered {len(refs_out)} candidate references and {len(ledger.get('own_paper_evidence') or [])} own-paper snippets.]",
        "research_background": {"domain_lineage": "", "concrete_problem": "", "technical_constraints": [], "why_now": "", "evidence_locators": [], "citation_ids": []},
        "related_work_groups": [],
        "comparison_matrix": [],
        "research_gaps": [],
        "contributions": [],
        "claim_citation_map": [],
        "references": refs_out,
        "excluded_references": [],
        "conflicts": ledger.get("conflicts") or [],
        "author_confirmation_items": gaps_confirm,
        "change_records": [],
        "uncertainty": [w for w in (ledger.get("access_warnings") or [])] + [g.get("detail", "") for g in (ledger.get("pre_gaps") or [])],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble final or partial Harbor deliverables under /app/output.")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--verification", default=None)
    ap.add_argument("--checkpoint", choices=["partial", "final"], default="final")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    plan, p_err = load_json_safe(Path(args.case_plan))
    ledger, l_err = load_json_safe(Path(args.ledger))
    if p_err or not isinstance(plan, dict):
        raise SystemExit(f"Cannot load case_plan: {p_err}")
    if l_err or not isinstance(ledger, dict):
        raise SystemExit(f"Cannot load ledger: {l_err}")

    if args.checkpoint == "partial" or not args.payload:
        payload = build_partial_payload(plan, ledger)
    else:
        payload, pay_err = load_json_safe(Path(args.payload))
        if pay_err or not isinstance(payload, dict):
            raise SystemExit(f"Cannot load payload: {pay_err}")

    verification: Dict[str, Any] = {}
    if args.verification and Path(args.verification).exists():
        vdata, _ = load_json_safe(Path(args.verification))
        if isinstance(vdata, dict):
            verification = vdata

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write core human-readable deliverables
    (out_dir / "introduction_and_related_work.md").write_text(_render_manuscript_md(payload), encoding="utf-8", newline="\n")
    matrix = payload.get("comparison_matrix") or []
    (out_dir / "related_work_matrix.md").write_text(_render_matrix_md(matrix), encoding="utf-8", newline="\n")
    _write_matrix_csv(matrix, out_dir / "related_work_matrix.csv")
    (out_dir / "evidence_and_citation_audit.md").write_text(_render_audit_md(payload, verification), encoding="utf-8", newline="\n")

    provenance_map = {
        "case_id": plan.get("case_id"),
        "claim_citation_map": payload.get("claim_citation_map") or [],
        "research_gaps": payload.get("research_gaps") or [],
        "contributions": payload.get("contributions") or [],
        "references": payload.get("references") or [],
        "conflicts": payload.get("conflicts") or [],
        "change_records": payload.get("change_records") or [],
    }
    save_json(out_dir / "provenance_map.json", provenance_map)

    extra_files, export_notes = _maybe_export_requested(
        payload,
        (plan.get("signals") or {}).get("requested_formats") or ["md"],
        out_dir,
    )

    deliverables = [
        "result.json",
        "introduction_and_related_work.md",
        "related_work_matrix.md",
        "related_work_matrix.csv",
        "evidence_and_citation_audit.md",
        "provenance_map.json",
    ] + extra_files

    uncertainty = list(payload.get("uncertainty") or []) + export_notes
    sources_list = []
    for r in payload.get("references") or []:
        if isinstance(r, dict):
            sources_list.append({"id": r.get("ref_id"), "cite_key": r.get("cite_key"), "locator": r.get("source_locator"), "status": r.get("verification_status")})
    for c in payload.get("contributions") or []:
        if isinstance(c, dict):
            sources_list.append({"id": c.get("contrib_id"), "locators": c.get("own_evidence_locators"), "status": c.get("verification_status")})

    # Populate result.json respecting EVERY output_contract field
    result: Dict[str, Any] = {
        "case_id": plan.get("case_id"),
        "status": payload.get("status", "partial"),
        "checkpoint": args.checkpoint,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manuscript_markdown": payload.get("manuscript_markdown", ""),
        "research_background": payload.get("research_background", {}),
        "related_work_groups": payload.get("related_work_groups", []),
        "comparison_matrix": matrix,
        "research_gaps": payload.get("research_gaps", []),
        "contributions": payload.get("contributions", []),
        "references": payload.get("references", []),
        "claim_citation_map": payload.get("claim_citation_map", []),
        "sources": sources_list,
        "conflicts": payload.get("conflicts", []),
        "author_confirmation_items": payload.get("author_confirmation_items", []),
        "change_records": payload.get("change_records", []),
        "uncertainty": uncertainty,
        "deliverable_files": deliverables,
        "verification_summary": verification or {"passed": False, "note": "partial_checkpoint_or_not_verified"},
    }

    role_map = {
        "case_id": result["case_id"],
        "status": result["status"],
        "manuscript_markdown": result["manuscript_markdown"],
        "research_background": result["research_background"],
        "related_work_groups": result["related_work_groups"],
        "comparison_matrix": result["comparison_matrix"],
        "research_gaps": result["research_gaps"],
        "contributions": result["contributions"],
        "references": result["references"],
        "claim_citation_map": result["claim_citation_map"],
        "conflicts": result["conflicts"],
        "author_confirmation_items": result["author_confirmation_items"],
        "change_records": result["change_records"],
        "uncertainty": result["uncertainty"],
        "sources": result["sources"],
        "deliverable_files": result["deliverable_files"],
        "verification_summary": result["verification_summary"],
        "summary": f"Case {result['case_id']} ({result['status']}): {len(result['references'])} refs, {len(result['related_work_groups'])} routes, {len(result['research_gaps'])} gaps, {len(result['contributions'])} contributions.",
    }
    overrides = payload.get("contract_overrides") or {}
    for fspec in (plan.get("output_contract") or {}).get("fields") or []:
        fname = fspec["name"]
        etype = fspec.get("expected_type", "any")
        role = fspec.get("semantic_role", "custom")
        if fname in overrides:
            result[fname] = _coerce_type(overrides[fname], etype)
        elif fname in payload and fname not in ("contract_overrides",):
            result[fname] = _coerce_type(payload[fname], etype)
        elif role in role_map:
            result[fname] = _coerce_type(role_map[role], etype)
        elif fname not in result:
            result[fname] = _coerce_type("", etype)

    save_json(out_dir / "result.json", result)
    print(json.dumps({"status": "ok", "checkpoint": args.checkpoint, "out_dir": str(out_dir), "deliverables": deliverables}, ensure_ascii=False))


if __name__ == "__main__":
    main()
