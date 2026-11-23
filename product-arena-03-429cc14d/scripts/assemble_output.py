#!/usr/bin/env python3
"""Step 3 / Step 6: Assemble synchronized citation outputs, BibTeX/RIS exports, previews, and result.json."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, save_json, save_text


def maybe_export_docx(md_text: str, out_docx: Path) -> Optional[str]:
    """Export clean .docx if python-docx is installed."""
    try:
        import docx  # type: ignore
    except ImportError:
        return "python-docx not installed; delivered Markdown/BibTeX/RIS/JSON without binary .docx."
    doc = docx.Document()
    for block in md_text.strip().split("\n\n"):
        b = block.strip()
        if not b:
            continue
        if b.startswith("# "):
            doc.add_heading(b[2:].strip(), level=1)
        elif b.startswith("## "):
            doc.add_heading(b[3:].strip(), level=2)
        elif b.startswith("### "):
            doc.add_heading(b[4:].strip(), level=3)
        else:
            doc.add_paragraph(b)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_docx))
    return None


def try_render_previews(artifact_path: Path, out_dir: Path) -> Tuple[List[str], List[str]]:
    """Render PDF and PNG previews for Office/LaTeX deliverables when tools exist."""
    previews: List[str] = []
    notes: List[str] = []
    ext = artifact_path.suffix.lower()
    pdf_path = out_dir / f"{artifact_path.stem}.pdf"

    if ext == ".docx":
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(artifact_path)], capture_output=True, text=True, timeout=90, check=True)
            except Exception as exc:
                notes.append(f"LibreOffice DOCX->PDF failed: {exc}")
        else:
            notes.append("soffice/libreoffice unavailable; skipped DOCX PDF preview.")
    elif ext == ".tex":
        xelatex = shutil.which("xelatex") or shutil.which("pdflatex")
        if xelatex:
            try:
                subprocess.run([xelatex, "-interaction=nonstopmode", "-output-directory", str(out_dir), str(artifact_path)], capture_output=True, text=True, timeout=90, check=True)
            except Exception as exc:
                notes.append(f"LaTeX PDF compile failed: {exc}")
        else:
            notes.append("xelatex/pdflatex unavailable; skipped LaTeX PDF preview.")

    if pdf_path.exists():
        previews.append(pdf_path.name)
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            prefix = out_dir / f"{artifact_path.stem}_page"
            try:
                subprocess.run([pdftoppm, "-png", "-r", "150", str(pdf_path), str(prefix)], capture_output=True, text=True, timeout=60, check=True)
                for png in sorted(out_dir.glob(f"{artifact_path.stem}_page*.png")):
                    previews.append(png.name)
            except Exception as exc:
                notes.append(f"pdftoppm PNG render failed: {exc}")
    return previews, notes


def coerce_type(val: Any, expected_type: str) -> Any:
    et = expected_type.lower()
    if "string" in et or et == "str":
        return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    if "array" in et or et == "list":
        if isinstance(val, list):
            return val
        return [] if val is None else [val]
    if "object" in et or et == "dict":
        return val if isinstance(val, dict) else {"value": val}
    return val


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble citation management deliverables and result.json.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--dedup", required=True)
    parser.add_argument("--synced", default="")
    parser.add_argument("--decisions", default="")
    parser.add_argument("--verification", default="")
    parser.add_argument("--checkpoint", choices=["partial", "final"], default="final")
    parser.add_argument("--out-dir", default="/app/output")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_plan, _ = load_json_safe(Path(args.case_plan), default={})
    snapshot, _ = load_json_safe(Path(args.snapshot), default={})
    dedup, _ = load_json_safe(Path(args.dedup), default={})
    synced, _ = load_json_safe(Path(args.synced), default={}) if args.synced else ({}, None)
    decisions, _ = load_json_safe(Path(args.decisions), default={}) if args.decisions else ({}, None)
    verification, _ = load_json_safe(Path(args.verification), default={}) if args.verification else ({}, None)

    signals = case_plan.get("signals", {})
    raw_records = snapshot.get("raw_records", [])
    has_manuscript = bool(case_plan.get("manuscript_files") or case_plan.get("inline_manuscript"))
    missing_refs = case_plan.get("missing_referenced_files", [])

    if (not has_manuscript and not raw_records) or (missing_refs and not synced and args.checkpoint == "final"):
        status = "unable_to_determine"
    elif args.checkpoint == "partial" or not synced:
        status = "partial"
    elif verification and not verification.get("passed", True):
        status = "partial"
    else:
        status = "completed"

    deliverable_files: List[str] = []
    limitations: List[str] = list(decisions.get("limitations", [])) if isinstance(decisions, dict) else []
    uncertainty: List[str] = list(decisions.get("uncertainty", [])) if isinstance(decisions, dict) else []

    for w in case_plan.get("warnings", []) + snapshot.get("warnings", []):
        if w not in uncertainty:
            uncertainty.append(w)
    if missing_refs:
        uncertainty.append(f"Missing referenced files: {', '.join(missing_refs)}")
    if not any("offline" in u.lower() for u in limitations + uncertainty):
        limitations.append("Offline verification mode: DOI syntax and snapshot provenance verified locally without live Crossref HTTP resolution.")

    updated_manuscript = synced.get("updated_manuscript", "") if isinstance(synced, dict) else ""
    formatted_bib = synced.get("formatted_bibliography", []) if isinstance(synced, dict) else []
    renum_map = synced.get("renumbering_map", []) if isinstance(synced, dict) else []

    if updated_manuscript:
        save_text(out_dir / "manuscript_updated.md", updated_manuscript)
        deliverable_files.append("manuscript_updated.md")

        req_formats = set(signals.get("requested_formats", []))
        m_exts = {m.get("ext") for m in case_plan.get("manuscript_files", [])}
        if ".tex" in m_exts or "latex" in req_formats:
            tex_p = out_dir / "manuscript_updated.tex"
            save_text(tex_p, updated_manuscript)
            deliverable_files.append(tex_p.name)
            pvs, pnotes = try_render_previews(tex_p, out_dir)
            deliverable_files.extend(pvs)
            limitations.extend(pnotes)
        if ".docx" in m_exts or "docx" in req_formats:
            docx_p = out_dir / "manuscript_updated.docx"
            dnote = maybe_export_docx(updated_manuscript, docx_p)
            if dnote:
                limitations.append(dnote)
            elif docx_p.exists():
                deliverable_files.append(docx_p.name)
                pvs, pnotes = try_render_previews(docx_p, out_dir)
                deliverable_files.extend(pvs)
                limitations.extend(pnotes)

    if formatted_bib:
        bib_md = [f"# Formatted References ({synced.get('target_style')})", ""] + [fb["formatted_entry"] for fb in formatted_bib]
        save_text(out_dir / "references_formatted.md", "\n".join(bib_md) + "\n")
        deliverable_files.append("references_formatted.md")
    if synced.get("bibtex_export"):
        save_text(out_dir / "references_clean.bib", synced["bibtex_export"])
        deliverable_files.append("references_clean.bib")
    if synced.get("ris_export"):
        save_text(out_dir / "references_clean.ris", synced["ris_export"])
        deliverable_files.append("references_clean.ris")

    # Dedup & renumbering report
    rep_lines = [
        f"# Deduplication & Renumbering Report ({case_plan.get('case_id')})",
        f"- Raw Library Records: {dedup.get('raw_count', 0)}",
        f"- Canonical Records: {dedup.get('canonical_count', 0)}",
        f"- Duplicates Merged: {dedup.get('duplicates_merged', 0)}",
        "",
        "## Merge Groups",
    ]
    for mg in dedup.get("merge_groups", []):
        rep_lines.append(f"- **{mg['canonical_ref_id']}** <- {mg['merged_raw_ids']} ({mg['retained_type']}): {mg['retained_title']}")
        for ev in mg.get("evidence", []):
            rep_lines.append(f"  - `{ev['tier']}`: {ev['reason']}")
    if dedup.get("preserved_distinct_pairs"):
        rep_lines.append("\n## Anti-False-Merge Preserved Pairs")
        for pd in dedup["preserved_distinct_pairs"]:
            rep_lines.append(f"- {pd['pair']}: {pd['reason']}")
    if dedup.get("ambiguous_candidates"):
        rep_lines.append("\n## Ambiguous Deduplication Candidates")
        for ac in dedup["ambiguous_candidates"]:
            rep_lines.append(f"- {ac['pair']} (sim={ac['title_similarity']}): {ac['titles']}")
    rep_lines.append("\n## In-Text Renumbering Map")
    rep_lines.append("| Sentence | Zone | Point | Old | Canonical Ref | New |")
    rep_lines.append("|-|-|-|-|-|-|")
    for rm in renum_map:
        rep_lines.append(f"| {rm['sentence_id']} | {rm['zone']} | {rm['citation_point_id']} | {rm['old_marker']} | {', '.join(rm['canonical_ref_ids'])} | {rm['new_marker']} |")
    save_text(out_dir / "dedup_and_renumbering_report.md", "\n".join(rep_lines) + "\n")
    deliverable_files.append("dedup_and_renumbering_report.md")

    # Citation verification matrix
    verifications: List[Dict[str, Any]] = []
    for ins in decisions.get("claim_insertions", []):
        verifications.append(
            {
                "kind": "inserted",
                "sentence_id": ins.get("sentence_id"),
                "anchor_phrase": ins.get("anchor_phrase"),
                "ref_ids": ins.get("ref_ids", []),
                "match_verdict": ins.get("match_verdict"),
                "match_evidence": ins.get("match_evidence"),
                "rationale": ins.get("rationale"),
            }
        )
    for aud in decisions.get("existing_citation_audits", []):
        verifications.append(
            {
                "kind": "existing_audit",
                "citation_point_id": aud.get("citation_point_id"),
                "sentence_id": aud.get("sentence_id"),
                "old_marker": aud.get("old_marker"),
                "action": aud.get("action"),
                "ref_ids": aud.get("resolved_ref_ids", []),
                "match_verdict": aud.get("match_verdict"),
                "match_evidence": aud.get("match_evidence"),
                "rationale": aud.get("rationale"),
            }
        )

    csv_p = out_dir / "citation_verification_matrix.csv"
    with open(csv_p, "w", encoding="utf-8", newline="\n") as cf:
        w = csv.DictWriter(cf, fieldnames=["kind", "sentence_id", "ref_ids", "match_verdict", "match_evidence", "rationale"], lineterminator="\n")
        w.writeheader()
        for v in verifications:
            w.writerow(
                {
                    "kind": v.get("kind", ""),
                    "sentence_id": v.get("sentence_id", ""),
                    "ref_ids": "|".join(v.get("ref_ids", [])),
                    "match_verdict": v.get("match_verdict", ""),
                    "match_evidence": v.get("match_evidence", ""),
                    "rationale": v.get("rationale", ""),
                }
            )
    deliverable_files.append("citation_verification_matrix.csv")

    mat_md = ["# Citation Verification Matrix", "", "| Kind | Sentence | Ref IDs | Verdict | Evidence | Rationale |", "|-|-|-|-|-|-|"]
    for v in verifications:
        mat_md.append(
            f"| {v.get('kind','')} | {v.get('sentence_id','')} | {', '.join(v.get('ref_ids',[]))} | {v.get('match_verdict','')} | {str(v.get('match_evidence','')).replace('|','/')} | {str(v.get('rationale','')).replace('|','/')} |"
        )
    save_text(out_dir / "citation_verification_matrix.md", "\n".join(mat_md) + "\n")
    deliverable_files.append("citation_verification_matrix.md")

    sources_prov = [
        {"raw_id": r["raw_id"], "source_format": r["source_format"], "source_location": r["source_location"], "title": r["title"], "doi": r["doi"]}
        for r in raw_records
    ]
    change_records = renum_map + [
        {"type": "metadata_correction", **mc} for mc in decisions.get("metadata_corrections", [])
    ]
    conf_items = list(decisions.get("author_confirmation_items", [])) if isinstance(decisions, dict) else []

    result: Dict[str, Any] = {
        "case_id": case_plan.get("case_id", "unknown_case"),
        "status": status,
        "checkpoint": args.checkpoint,
        "target_style": synced.get("target_style") or signals.get("target_style"),
        "summary": decisions.get("summary") if isinstance(decisions, dict) and decisions.get("summary") else f"Processed {len(raw_records)} raw library records into {dedup.get('canonical_count', 0)} canonical entries; status={status}.",
        "updated_manuscript": updated_manuscript,
        "formatted_bibliography": formatted_bib,
        "renumbering_map": renum_map,
        "dedup_summary": dedup,
        "citation_verifications": verifications,
        "sources": sources_prov,
        "change_records": change_records,
        "audit_metrics": verification.get("metrics", {}) if isinstance(verification, dict) else {},
        "verification_findings": verification.get("findings", []) if isinstance(verification, dict) else [],
        "uncertainty": uncertainty,
        "author_confirmation_items": conf_items,
        "limitations": limitations,
        "deliverable_files": deliverable_files,
    }

    role_values = {
        "case_id": result["case_id"],
        "status": result["status"],
        "updated_manuscript": result["updated_manuscript"],
        "formatted_bibliography": result["formatted_bibliography"],
        "renumbering_map": result["renumbering_map"],
        "dedup_summary": result["dedup_summary"],
        "citation_verifications": result["citation_verifications"],
        "sources": result["sources"],
        "change_records": result["change_records"],
        "author_confirmation_items": result["author_confirmation_items"],
        "uncertainty": result["uncertainty"],
        "limitations": result["limitations"],
        "audit_metrics": result["audit_metrics"],
        "deliverable_files": result["deliverable_files"],
        "summary": result["summary"],
    }
    overrides = decisions.get("contract_overrides", {}) if isinstance(decisions, dict) else {}
    for spec in case_plan.get("output_contract", {}).get("fields", []):
        fn = spec["name"]
        if isinstance(overrides, dict) and fn in overrides:
            result[fn] = coerce_type(overrides[fn], spec.get("expected_type", "any"))
        elif fn not in result:
            role = spec.get("semantic_role", "custom")
            result[fn] = coerce_type(role_values.get(role), spec.get("expected_type", "any"))

    save_json(out_dir / "result.json", result)

    if args.strict:
        missing = [k for k in case_plan.get("output_contract", {}).get("required_keys", []) if k not in result]
        if missing:
            raise SystemExit(f"Strict output_contract failed; missing required keys: {missing}")


if __name__ == "__main__":
    main()
