#!/usr/bin/env python3
"""Step 3 / Step 6: Assemble polished deliverables, redline diffs, previews, and contract result.json."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import inline_word_diff, load_json_safe, save_json, save_text


def reconstruct_manuscript(segments: List[Dict[str, Any]], pol_by_id: Dict[str, Dict[str, Any]]) -> str:
    """Rebuild full editable manuscript preserving paragraph breaks and section headings."""
    by_para: "OrderedDict[str, List[str]]" = OrderedDict()
    for seg in segments:
        pid = seg["paragraph_id"]
        sid = seg["sentence_id"]
        txt = pol_by_id[sid]["polished"] if sid in pol_by_id else seg["original"]
        by_para.setdefault(pid, []).append(txt)

    para_strings: List[str] = []
    for sents in by_para.values():
        joined = " ".join(s.strip() for s in sents if s.strip())
        # Clean extra space before Chinese punctuation if CJK paragraph
        para_strings.append(joined)
    return "\n\n".join(para_strings).strip() + "\n"


def maybe_export_docx(md_text: str, out_docx: Path) -> Optional[str]:
    """Export clean .docx if python-docx is installed."""
    try:
        import docx  # type: ignore
    except ImportError:
        return "python-docx not installed; delivered Markdown/JSON without binary .docx."

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


def try_render_previews(artifact_path: Path, out_dir: Path) -> Tuple_Preview:
    """Attempt real PDF + PNG rendering via soffice or xelatex + pdftoppm."""
    previews: List[str] = []
    notes: List[str] = []
    ext = artifact_path.suffix.lower()
    pdf_path = out_dir / f"{artifact_path.stem}.pdf"

    if ext == ".docx":
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(artifact_path)],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=True,
                )
            except Exception as exc:
                notes.append(f"LibreOffice DOCX->PDF failed: {exc}")
        else:
            notes.append("soffice/libreoffice not found; skipped DOCX preview rendering.")
    elif ext == ".tex":
        xelatex = shutil.which("xelatex") or shutil.which("pdflatex")
        if xelatex:
            try:
                subprocess.run(
                    [xelatex, "-interaction=nonstopmode", "-output-directory", str(out_dir), str(artifact_path)],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=True,
                )
            except Exception as exc:
                notes.append(f"LaTeX PDF compile failed: {exc}")
        else:
            notes.append("xelatex/pdflatex not found; skipped LaTeX PDF preview rendering.")

    if pdf_path.exists():
        previews.append(pdf_path.name)
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            prefix = out_dir / f"{artifact_path.stem}_page"
            try:
                subprocess.run(
                    [pdftoppm, "-png", "-r", "150", str(pdf_path), str(prefix)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                for png in sorted(out_dir.glob(f"{artifact_path.stem}_page*.png")):
                    previews.append(png.name)
            except Exception as exc:
                notes.append(f"pdftoppm PNG render failed: {exc}")
    return previews, notes


Tuple_Preview = tuple


def coerce_to_expected_type(val: Any, expected_type: str) -> Any:
    """Coerce mapped output_contract values lightly so type checks pass cleanly."""
    et = expected_type.lower()
    if "string" in et or et == "str":
        if isinstance(val, str):
            return val
        import json
        return json.dumps(val, ensure_ascii=False)
    if "array" in et or et == "list":
        if isinstance(val, list):
            return val
        return [] if val is None else [val]
    if "object" in et or et == "dict":
        if isinstance(val, dict):
            return val
        return {"value": val}
    return val


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble academic polishing outputs and result.json.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--segments", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--payload", default="")
    parser.add_argument("--verification", default="")
    parser.add_argument("--checkpoint", choices=["partial", "final"], default="final")
    parser.add_argument("--out-dir", default="/app/output")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_plan, _ = load_json_safe(Path(args.case_plan), default={})
    seg_doc, _ = load_json_safe(Path(args.segments), default={})
    glossary_doc, _ = load_json_safe(Path(args.glossary), default={})
    payload: Dict[str, Any] = {}
    if args.payload:
        payload, _ = load_json_safe(Path(args.payload), default={})
    verification: Dict[str, Any] = {}
    if args.verification:
        verification, _ = load_json_safe(Path(args.verification), default={})

    segments: List[Dict[str, Any]] = seg_doc.get("segments", [])
    signals = case_plan.get("signals", {})
    pol_sentences: List[Dict[str, Any]] = payload.get("sentences", []) if isinstance(payload, dict) else []
    pol_by_id = {s["sentence_id"]: s for s in pol_sentences if isinstance(s, dict) and s.get("sentence_id")}

    # Determine honest execution status
    missing_refs = case_plan.get("missing_referenced_files", [])
    if signals.get("is_non_polish_qa"):
        status = "not_applicable_qa"
    elif not segments:
        status = "unable_to_determine"
    elif args.checkpoint == "partial" or not pol_by_id:
        status = "partial"
    elif payload.get("status") in ("partial", "unable_to_determine", "not_applicable_qa"):
        status = payload["status"]
    elif verification and not verification.get("passed", True):
        status = "partial"
    else:
        status = "completed"

    manuscript_md = reconstruct_manuscript(segments, pol_by_id) if segments else ""
    deliverable_files: List[str] = []
    limitations: List[str] = list(payload.get("limitations", [])) if isinstance(payload, dict) else []
    uncertainty: List[str] = list(payload.get("uncertainty", [])) if isinstance(payload, dict) else []

    if case_plan.get("warnings"):
        for w in case_plan["warnings"]:
            if w not in uncertainty:
                uncertainty.append(w)
    if missing_refs:
        uncertainty.append(f"Missing referenced input files: {', '.join(missing_refs)}")
    if signals.get("genre_confidence") == "inferred_default":
        uncertainty.append(f"Target genre not stated explicitly; defaulted to '{signals.get('genre')}'.")
    for gc in glossary_doc.get("glossary_conflicts", []):
        msg = f"Internal glossary conflict {gc['conflict_id']} for '{gc['key']}' requires author confirmation."
        if msg not in uncertainty:
            uncertainty.append(msg)

    # Write primary Markdown manuscript
    if manuscript_md:
        save_text(out_dir / "polished_manuscript.md", manuscript_md)
        deliverable_files.append("polished_manuscript.md")

    # Optional .tex / .docx deliverables and previews
    doc_formats = {d.get("format") for d in seg_doc.get("docs", [])}
    req_formats = set(signals.get("requested_formats", []))
    if ("tex" in str(doc_formats) or "latex" in req_formats or any(m.get("ext") == ".tex" for m in case_plan.get("manuscript_files", []))) and manuscript_md:
        tex_path = out_dir / "polished_manuscript.tex"
        save_text(tex_path, manuscript_md)
        deliverable_files.append(tex_path.name)
        previews, pnotes = try_render_previews(tex_path, out_dir)
        deliverable_files.extend(previews)
        limitations.extend(pnotes)

    if ("docx" in doc_formats or "docx" in req_formats) and manuscript_md:
        docx_path = out_dir / "polished_manuscript.docx"
        d_note = maybe_export_docx(manuscript_md, docx_path)
        if d_note:
            limitations.append(d_note)
        elif docx_path.exists():
            deliverable_files.append(docx_path.name)
            previews, pnotes = try_render_previews(docx_path, out_dir)
            deliverable_files.extend(previews)
            limitations.extend(pnotes)

    # Build change records & redline diff
    change_records: List[Dict[str, Any]] = []
    redline_lines = [f"# Sentence-Level Redline Diff & Audit ({case_plan.get('case_id')})\n"]
    for seg in segments:
        sid = seg["sentence_id"]
        pol = pol_by_id.get(sid)
        orig = seg["original"]
        new_txt = pol["polished"] if pol else orig
        changed = bool(pol and pol.get("changed") and new_txt != orig)
        edit_types = (pol.get("edit_types") if pol else ["unchanged"]) or (["unchanged"] if not changed else ["grammar_syntax"])
        rationale = (pol.get("rationale") if pol else "Not yet edited (checkpoint).") or ""
        needs_conf = bool(pol.get("needs_human_confirmation", False)) if pol else False
        conf_q = str(pol.get("confirmation_question", "")) if pol else ""
        diff_md = inline_word_diff(orig, new_txt)
        change_records.append(
            {
                "sentence_id": sid,
                "paragraph_id": seg["paragraph_id"],
                "section": seg["section"],
                "source_location": f"{seg['doc_rel_path']}:L{seg['line_range'][0]}-L{seg['line_range'][1]}",
                "original": orig,
                "polished": new_txt,
                "changed": changed,
                "edit_types": edit_types,
                "rationale": rationale,
                "inline_diff": diff_md,
                "needs_human_confirmation": needs_conf,
                "confirmation_question": conf_q,
            }
        )
        badge = "CHANGED" if changed else "UNCHANGED"
        redline_lines.append(f"### {sid} [{seg['section']}] — `{badge}` ({', '.join(edit_types)})")
        redline_lines.append(f"- **Original**: {orig}")
        redline_lines.append(f"- **Polished**: {new_txt}")
        if changed:
            redline_lines.append(f"- **Diff**: {diff_md}")
        redline_lines.append(f"- **Rationale**: {rationale}")
        if needs_conf and conf_q:
            redline_lines.append(f"- **Needs Confirmation**: {conf_q}")
        redline_lines.append("")

    save_text(out_dir / "redline_diff.md", "\n".join(redline_lines))
    deliverable_files.append("redline_diff.md")

    # Write CSV & Markdown change_log
    csv_path = out_dir / "change_log.csv"
    with open(csv_path, "w", encoding="utf-8", newline="\n") as cf:
        writer = csv.DictWriter(
            cf,
            fieldnames=["sentence_id", "section", "source_location", "changed", "edit_types", "original", "polished", "rationale", "needs_human_confirmation"],
            lineterminator="\n",
        )
        writer.writeheader()
        for cr in change_records:
            writer.writerow(
                {
                    "sentence_id": cr["sentence_id"],
                    "section": cr["section"],
                    "source_location": cr["source_location"],
                    "changed": cr["changed"],
                    "edit_types": "|".join(cr["edit_types"]),
                    "original": cr["original"],
                    "polished": cr["polished"],
                    "rationale": cr["rationale"],
                    "needs_human_confirmation": cr["needs_human_confirmation"],
                }
            )
    deliverable_files.append("change_log.csv")

    md_log_lines = [
        f"# Change Log ({case_plan.get('case_id')})",
        "",
        "| Sentence | Section | Changed | Edit Types | Original | Polished | Rationale |",
        "|-|-|-|-|-|-|-|",
    ]
    for cr in change_records:
        if cr["changed"] or cr["needs_human_confirmation"]:
            o_esc = cr["original"].replace("|", "\\|")
            p_esc = cr["polished"].replace("|", "\\|")
            r_esc = cr["rationale"].replace("|", "\\|")
            md_log_lines.append(f"| {cr['sentence_id']} | {cr['section']} | {cr['changed']} | {', '.join(cr['edit_types'])} | {o_esc} | {p_esc} | {r_esc} |")
    save_text(out_dir / "change_log.md", "\n".join(md_log_lines) + "\n")
    deliverable_files.append("change_log.md")

    # Glossary table
    glossary_final = payload.get("glossary_final") if isinstance(payload, dict) and payload.get("glossary_final") else glossary_doc.get("user_terms", [])
    g_lines = [
        "# Locked Academic Glossary",
        "",
        "| Canonical EN | Canonical ZH | Acronym | Allowed Variants | Note |",
        "|-|-|-|-|-|",
    ]
    for g in glossary_final:
        g_lines.append(
            f"| {g.get('canonical_en','')} | {g.get('canonical_zh','')} | {g.get('acronym','')} | {', '.join(g.get('allowed_variants', []))} | {g.get('note','')} |"
        )
    save_text(out_dir / "glossary_table.md", "\n".join(g_lines) + "\n")
    deliverable_files.append("glossary_table.md")

    # Author confirmation & audit report
    conf_items = list(payload.get("author_confirmation_items", [])) if isinstance(payload, dict) else []
    audit_lines = [
        f"# Verification & Author Confirmation Report ({case_plan.get('case_id')})",
        f"- **Status**: `{status}`",
        f"- **Checkpoint**: `{args.checkpoint}`",
        "",
        "## Audit Metrics",
        "```json",
        __import__("json").dumps(verification.get("metrics", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Uncertainty & Material Gaps",
    ]
    for u in uncertainty or ["None"]:
        audit_lines.append(f"- {u}")
    audit_lines.append("\n## Author Confirmation Items")
    if conf_items:
        for item in conf_items:
            audit_lines.append(f"- **{item.get('id')}** ({item.get('sentence_id')}, `{item.get('issue_type')}`): {item.get('question')} | Options: {item.get('options')} | Current: {item.get('current_handling')}")
    else:
        audit_lines.append("- None")
    save_text(out_dir / "audit_and_confirmation_report.md", "\n".join(audit_lines) + "\n")
    deliverable_files.append("audit_and_confirmation_report.md")

    sources_map = [
        {
            "sentence_id": cr["sentence_id"],
            "source_location": cr["source_location"],
            "protected_spans": next((s["protected_spans"] for s in segments if s["sentence_id"] == cr["sentence_id"]), {}),
        }
        for cr in change_records
    ]

    summary_text = (
        payload.get("summary")
        if isinstance(payload, dict) and payload.get("summary")
        else f"Processed {len(segments)} sentences across {len(seg_doc.get('docs', []))} document(s); status={status}."
    )

    result: Dict[str, Any] = {
        "case_id": case_plan.get("case_id", "unknown_case"),
        "status": status,
        "checkpoint": args.checkpoint,
        "genre": payload.get("genre") if isinstance(payload, dict) and payload.get("genre") else signals.get("genre"),
        "language": payload.get("language") if isinstance(payload, dict) and payload.get("language") else signals.get("language"),
        "edit_intensity": payload.get("edit_intensity") if isinstance(payload, dict) and payload.get("edit_intensity") else signals.get("edit_intensity"),
        "summary": summary_text,
        "polished_text": manuscript_md,
        "change_records": change_records,
        "glossary": glossary_final,
        "glossary_conflicts": glossary_doc.get("glossary_conflicts", []),
        "bilingual_alignment": payload.get("bilingual_alignment", []) if isinstance(payload, dict) else [],
        "sources": sources_map,
        "audit_metrics": verification.get("metrics", {}),
        "verification_findings": verification.get("findings", []),
        "uncertainty": uncertainty,
        "author_confirmation_items": conf_items,
        "limitations": limitations,
        "deliverable_files": deliverable_files,
    }

    # Populate all output_contract fields dynamically
    role_values = {
        "case_id": result["case_id"],
        "status": result["status"],
        "polished_text": result["polished_text"],
        "change_records": result["change_records"],
        "glossary": result["glossary"],
        "bilingual_pairs": result["bilingual_alignment"],
        "sources": result["sources"],
        "author_confirmation_items": result["author_confirmation_items"],
        "uncertainty": result["uncertainty"],
        "limitations": result["limitations"],
        "verification_summary": result["audit_metrics"],
        "deliverable_files": result["deliverable_files"],
        "summary": result["summary"],
    }
    overrides = payload.get("contract_overrides", {}) if isinstance(payload, dict) else {}
    contract_fields = case_plan.get("output_contract", {}).get("fields", [])
    for spec in contract_fields:
        fname = spec["name"]
        if isinstance(overrides, dict) and fname in overrides:
            result[fname] = coerce_to_expected_type(overrides[fname], spec.get("expected_type", "any"))
        elif fname not in result:
            role = spec.get("semantic_role", "custom")
            if role in role_values:
                result[fname] = coerce_to_expected_type(role_values[role], spec.get("expected_type", "any"))
            else:
                result[fname] = coerce_to_expected_type(None, spec.get("expected_type", "any"))

    save_json(out_dir / "result.json", result)

    if args.strict:
        missing_contract = [k for k in case_plan.get("output_contract", {}).get("required_keys", []) if k not in result]
        if missing_contract:
            raise SystemExit(f"Strict contract check failed; missing required keys: {missing_contract}")


if __name__ == "__main__":
    main()
