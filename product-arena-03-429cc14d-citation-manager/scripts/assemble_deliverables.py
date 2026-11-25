#!/usr/bin/env python3
"""Assemble final (or early partial checkpoint) deliverables under /app/output.

Outputs:
- result.json (strictly honors case_id + every output_contract key + full provenance envelope)
- revised_manuscript.md
- references_formatted.md, references_clean.bib, references_clean.ris,
  references_clean.csl.json, references_table.csv
- citation_audit_report.md
- provenance_ledger.json
- Optional revised_manuscript.docx (+ real PDF/PNG preview via LibreOffice/PyMuPDF when available)
- Optional revised_manuscript.tex (+ real PDF/PNG preview via pdflatex/xelatex when available)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, save_json


def _add_rich_runs(paragraph: Any, text: str, tok_split_re: re.Pattern) -> None:
    parts = tok_split_re.split(text)
    for part in parts:
        if not part:
            continue
        if part.startswith("<sup>") and part.endswith("</sup>"):
            r = paragraph.add_run(part[5:-6])
            r.font.superscript = True
        elif part.startswith("^[") and part.endswith("]"):
            r = paragraph.add_run(part[1:])
            r.font.superscript = True
        elif part.startswith("**") and part.endswith("**"):
            r = paragraph.add_run(part[2:-2])
            r.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) >= 3:
            r = paragraph.add_run(part[1:-1])
            r.italic = True
        else:
            paragraph.add_run(part)


def _export_docx(
    revised_md: str,
    out_docx: Path,
    title: str,
) -> Tuple[bool, Optional[str]]:
    try:
        import docx  # type: ignore
    except ImportError:
        return False, "python_docx_not_installed"

    try:
        doc = docx.Document()
        md_lines = revised_md.splitlines()
        first_nonempty = next((ln.strip() for ln in md_lines if ln.strip()), "")
        if not first_nonempty.startswith("# "):
            doc.add_heading(title or "Revised Manuscript", level=1)

        tok_split_re = re.compile(r"(<sup>.*?</sup>|\^\[[^\]]+\]|\*\*[^*]+\*\*|\*[^*\n]+\*)")
        idx_ln = 0
        while idx_ln < len(md_lines):
            s = md_lines[idx_ln].strip()
            if not s:
                idx_ln += 1
                continue
            if s.startswith("|") and s.endswith("|") and idx_ln + 1 < len(md_lines) and re.match(r"^\|[\s:|\-]+\|$", md_lines[idx_ln + 1].strip()):
                rows_raw: List[List[str]] = []
                while idx_ln < len(md_lines) and md_lines[idx_ln].strip().startswith("|"):
                    row_s = md_lines[idx_ln].strip()
                    if not re.match(r"^\|[\s:|\-]+\|$", row_s):
                        rows_raw.append([c.strip() for c in row_s.strip("|").split("|")])
                    idx_ln += 1
                if rows_raw:
                    ncol = max(len(r) for r in rows_raw)
                    tbl = doc.add_table(rows=len(rows_raw), cols=ncol)
                    for ri, row in enumerate(rows_raw):
                        for ci, cell_val in enumerate(row[:ncol]):
                            cell = tbl.rows[ri].cells[ci]
                            cell.text = ""
                            _add_rich_runs(cell.paragraphs[0], cell_val, tok_split_re)
                continue
            if s.startswith("### "):
                doc.add_heading(s[4:], level=3)
            elif s.startswith("## "):
                doc.add_heading(s[3:], level=2)
            elif s.startswith("# "):
                doc.add_heading(s[2:], level=1)
            else:
                p = doc.add_paragraph()
                _add_rich_runs(p, s, tok_split_re)
            idx_ln += 1
        doc.save(str(out_docx))
        return True, None
    except Exception as exc:
        return False, f"docx_export_error:{exc}"


def _render_office_or_tex_previews(
    artifact_path: Path,
    out_dir: Path,
    prefix: str,
) -> Tuple[List[str], List[str]]:
    """Render real PDF & PNG previews if LibreOffice/LaTeX/PyMuPDF are installed. Never fabricate previews."""
    previews: List[str] = []
    notes: List[str] = []
    pdf_path: Optional[Path] = None

    if artifact_path.suffix.lower() == ".docx":
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                with tempfile.TemporaryDirectory(prefix="lo_prof_") as lo_dir:
                    lo_uri = Path(lo_dir).resolve().as_uri()
                    proc = subprocess.run(
                        [
                            soffice,
                            f"-env:UserInstallation={lo_uri}",
                            "--headless",
                            "--norestore",
                            "--convert-to",
                            "pdf",
                            "--outdir",
                            str(out_dir),
                            str(artifact_path),
                        ],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
                cand = out_dir / f"{artifact_path.stem}.pdf"
                if proc.returncode == 0 and cand.exists() and cand.stat().st_size > 0:
                    pdf_path = cand
                    previews.append(cand.name)
                else:
                    notes.append(f"libreoffice_pdf_failed:rc={proc.returncode}")
            except Exception as exc:
                notes.append(f"libreoffice_pdf_error:{exc}")
        else:
            notes.append("preview_unavailable_no_libreoffice_soffice")

    elif artifact_path.suffix.lower() == ".tex":
        tex_bin = shutil.which("xelatex") or shutil.which("pdflatex")
        if tex_bin:
            try:
                cmd = [
                    tex_bin,
                    "-no-shell-escape",
                    "-interaction=nonstopmode",
                    "-output-directory",
                    str(out_dir),
                    str(artifact_path),
                ]
                proc = None
                for _ in range(2):
                    proc = subprocess.run(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
                cand = out_dir / f"{artifact_path.stem}.pdf"
                if cand.exists() and cand.stat().st_size > 0:
                    pdf_path = cand
                    previews.append(cand.name)
                else:
                    rc = proc.returncode if proc is not None else -1
                    notes.append(f"latex_compile_failed:rc={rc}")
            except Exception as exc:
                notes.append(f"latex_compile_error:{exc}")
        else:
            notes.append("preview_unavailable_no_latex_compiler")

    if pdf_path and pdf_path.exists():
        # Render first 3 + last 2 pages (up to 6 unique pages) so both body and References are previewed
        rendered_pngs = False
        try:
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore
            doc = fitz.open(str(pdf_path))
            total_p = len(doc)
            page_indices = list(dict.fromkeys(list(range(min(3, total_p))) + list(range(max(0, total_p - 2), total_p))))[:6]
            for idx in page_indices:
                pix = doc[idx].get_pixmap(dpi=150)
                png_name = f"{prefix}_preview_p{idx + 1}.png"
                pix.save(str(out_dir / png_name))
                previews.append(png_name)
            doc.close()
            rendered_pngs = True
        except Exception:
            pass
        if not rendered_pngs:
            pdftoppm = shutil.which("pdftoppm")
            if pdftoppm:
                try:
                    subprocess.run(
                        [pdftoppm, "-png", "-r", "150", "-f", "1", "-l", "3", str(pdf_path), str(out_dir / f"{prefix}_preview")],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=25,
                    )
                    for png in sorted(out_dir.glob(f"{prefix}_preview*.png")):
                        previews.append(png.name)
                except Exception as exc:
                    notes.append(f"pdftoppm_error:{exc}")
    return previews, notes


def _build_audit_report(
    case_plan: Dict[str, Any],
    lib: Dict[str, Any],
    ms: Dict[str, Any],
    pre_audit: Dict[str, Any],
    plan: Dict[str, Any],
    synced: Dict[str, Any],
    verification: Dict[str, Any],
    uncertainty: List[Dict[str, Any]],
    human_items: List[Dict[str, Any]],
    checkpoint: str,
) -> str:
    lines: List[str] = [
        f"# Citation & Reference Management Audit Report (`{case_plan.get('case_id', '')}`)",
        "",
        f"- **Checkpoint**: `{checkpoint}` | **Target Style**: `{synced.get('target_style') or case_plan.get('signals', {}).get('target_style')}` | **In-Text Format**: `{synced.get('in_text_format') or case_plan.get('signals', {}).get('in_text_format')}`",
        f"- **Canonical References**: {len(synced.get('updated_canonical_library') or lib.get('canonical_records', []))} (from {lib.get('stats', {}).get('raw_record_count', 0)} raw records; {lib.get('stats', {}).get('duplicates_removed', 0)} duplicates removed)",
        f"- **Cited in Final Manuscript**: {synced.get('stats', {}).get('cited_ref_count', 0)} | **Uncited Library Entries**: {synced.get('stats', {}).get('uncited_library_count', 0)}",
        "",
    ]

    lines.append("## 1. Deduplication & Anti-False-Merge Decisions (收得净)")
    mgs = lib.get("merge_groups", [])
    if mgs:
        for mg in mgs:
            members = ", ".join(f"`{m['raw_id']}` ({m['source_locator']})" for m in mg.get("member_records", []))
            rules = "; ".join(f"`{r['tier']}`: {r['rule']}" for r in mg.get("pair_rationales", []))
            lines.append(f"- **`{mg['canonical_id']}`**: Merged {members} -> Primary `{mg['primary_raw_id']}` | Rationale: {rules}")
    else:
        lines.append("- No duplicate records auto-merged.")
    for em in synced.get("extra_tier3_merges", []):
        lines.append(f"- **Agent-Confirmed Tier-3 Merge (`{em['canonical_id']}`)**: absorbed `{em['merged_secondary_ref_id']}` ({em['rationale']})")
    pnds = lib.get("protected_non_duplicates", [])
    if pnds:
        lines.append("### Protected Non-Duplicates (Kept Separate)")
        for pn in pnds:
            lines.append(f"- `{pn['raw_a']}` (*{pn['title_a']}*) vs `{pn['raw_b']}` (*{pn['title_b']}*) — `{pn['rule']}`")
    rdcs = lib.get("recycled_doi_conflicts", [])
    if rdcs:
        lines.append("### Recycled / Conflicting DOI Alerts (NOT Merged)")
        for rc in rdcs:
            lines.append(f"- `{rc['raw_a']}` vs `{rc['raw_b']}` — `{rc['rule']}` ({rc['metrics']})")
    lines.append("")

    lines.append("## 2. Renumbering & Cross-Reference Synchronization (编得顺)")
    rmap = synced.get("renumbering_map", [])
    if rmap:
        lines.append("| New # | Ref ID | Old Inline # / Keys | New In-Text Marker | First Appeared | Title |")
        lines.append("|---|---|---|---|---|---|")
        for rm in rmap:
            old_str = ", ".join([f"[{n}]" for n in rm.get("old_inline_numbers", [])] + rm.get("old_source_keys", [])) or "-"
            loc_str = f"`{rm.get('first_appeared_sentence')}` ({rm.get('first_appeared_block_type')})"
            title_s = (rm.get("title") or "")[:70].replace("|", "\\|")
            lines.append(f"| {rm['new_number']} | `{rm['ref_id']}` | {old_str} | `{rm['new_in_text_marker']}` | {loc_str} | {title_s} |")
    else:
        lines.append("- Pending final synchronization.")
    uncited = synced.get("uncited_library_entries") or pre_audit.get("uncited_library_entries") or []
    if uncited:
        lines.append("### Uncited Library Entries (Excluded from Final Bibliography)")
        for u in uncited:
            lines.append(f"- `{u['ref_id']}` ({u.get('year')}): {u.get('title')}")
    lines.append("")

    lines.append("## 3. Claim-to-Reference Verification & Clause Placement (挂得对 & 对得上)")
    cvers = plan.get("claim_verifications") or []
    if cvers:
        lines.append("| Claim | Sentence | Cited Refs | Verdict | Evidence Locator | Details |")
        lines.append("|---|---|---|---|---|---|")
        for cv in cvers:
            det = (cv.get("mismatch_detail") or cv.get("evidence_quote") or "")[:90].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{cv.get('claim_id', '')}` | `{cv.get('sentence_id', '')}` | `{','.join(cv.get('cited_ref_ids', []))}` | `{cv.get('verdict', '')}` | `{cv.get('evidence_locator', '')}` | {det} |"
            )
    else:
        lines.append("- Pre-audit recorded; see `pre_audit.json`.")
    lines.append("")

    lines.append("## 4. Field-Level Provenance & Missing Metadata")
    recs = synced.get("updated_canonical_library") or lib.get("canonical_records", [])
    for r in recs:
        miss = r.get("missing_required_fields") or []
        flags = r.get("integrity_flags") or []
        lines.append(f"- **`{r['ref_id']}`**: DOI status=`{r.get('doi_verification_status')}` | Missing required=`{miss}` | Flags=`{flags}`")
    lines.append("")

    lines.append("## 5. Uncertainty, Offline Boundaries & Human Confirmation Items")
    if uncertainty:
        for u in uncertainty:
            lines.append(f"- **[{u.get('item', '')}]**: {u.get('reason', '')} *(Scope: {u.get('scope', '')})*")
    if human_items:
        lines.append("### Action Required by Author / Reviewer")
        for hi in human_items:
            lines.append(f"- **[{hi.get('issue_id', '')} | {hi.get('severity', '')}]** `{hi.get('location', '')}`: {hi.get('question', '')}")
    if not uncertainty and not human_items:
        lines.append("- None.")
    lines.append("")

    if verification:
        lines.append("## 6. Mechanical Verification Result")
        lines.append(f"- **Passed**: `{verification.get('passed')}` | **Errors**: `{verification.get('error_count')}` | **Warnings**: `{verification.get('warning_count')}`")
    return "\n".join(lines) + "\n"


def _coerce_to_contract_type(value: Any, expected_type: str) -> Any:
    et = (expected_type or "any").lower()
    if et in ("string", "str"):
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)
    if et in ("array", "list"):
        if isinstance(value, list):
            return value
        return [value] if value not in (None, "") else []
    if et in ("object", "dict"):
        if isinstance(value, dict):
            return value
        return {"value": value} if value not in (None, "") else {}
    if et in ("boolean", "bool"):
        return bool(value)
    if et in ("integer", "int"):
        try:
            return int(value)
        except Exception:
            return 0
    if et in ("number", "float"):
        try:
            return float(value)
        except Exception:
            return 0.0
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble Harbor /app/output deliverables and result.json.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--library", default="/app/work/library/library_canonical.json")
    parser.add_argument("--manuscript", default="/app/work/manuscript/manuscript_parsed.json")
    parser.add_argument("--pre-audit", default="/app/work/verify/pre_audit.json")
    parser.add_argument("--plan", default="/app/work/citation_plan.json")
    parser.add_argument("--synced", default="/app/work/synced/sync_result.json")
    parser.add_argument("--verification", default="/app/work/verification_report.json")
    parser.add_argument("--checkpoint", choices=["partial", "final"], default="final")
    parser.add_argument("--skip-previews", action="store_true", help="Reuse already-rendered DOCX/LaTeX previews without re-running soffice/xelatex.")
    parser.add_argument("--out-dir", default="/app/output")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    lib = load_json_safe(Path(args.library), {})
    ms = load_json_safe(Path(args.manuscript), {})
    pre_audit = load_json_safe(Path(args.pre_audit), {})
    plan = load_json_safe(Path(args.plan), {}) if Path(args.plan).exists() else {}
    synced = load_json_safe(Path(args.synced), {}) if Path(args.synced).exists() else {}
    verification = load_json_safe(Path(args.verification), {}) if Path(args.verification).exists() else {}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    synced_dir = Path(args.synced).parent

    # Determine honest run status (account for inline case.json/task.md materials too)
    stats = case_plan.get("stats", {})
    total_files = stats.get("total_material_files", 0)
    corrupt_cnt = stats.get("corrupt_or_unreadable_count", 0)
    has_any_material = (total_files - corrupt_cnt > 0) or len(lib.get("canonical_records", [])) > 0 or len(ms.get("sentences", [])) > 0
    if args.checkpoint == "partial":
        status = "partial"
    elif not has_any_material:
        status = "unable_to_determine"
    elif verification and verification.get("error_count", 0) > 0:
        status = "partial"
    else:
        status = "completed"

    # Build uncertainty & human confirmation items deterministically + merge Agent plan items
    uncertainty: List[Dict[str, Any]] = list(plan.get("uncertainty") or [])
    human_items: List[Dict[str, Any]] = list(plan.get("human_confirmation_items") or [])

    existing_u_keys = {u.get("item") for u in uncertainty if isinstance(u, dict)}
    recs = synced.get("updated_canonical_library") or lib.get("canonical_records", [])
    offline_dois = [r["ref_id"] for r in recs if r.get("doi_verification_status") == "not_checked_offline"]
    if offline_dois and "offline_doi_resolution" not in existing_u_keys:
        uncertainty.append({
            "item": "offline_doi_resolution",
            "reason": "Offline sandbox environment without external Crossref/OpenAlex verification; DOI syntax validated only.",
            "scope": ", ".join(offline_dois),
            "recommended_action": "Verify live DOI resolution when connected.",
        })
    for warn in case_plan.get("access_warnings", []) + lib.get("parse_warnings", []) + ms.get("parse_warnings", []):
        uncertainty.append({"item": "input_parse_or_access_warning", "reason": str(warn), "scope": "input_files"})
    if case_plan.get("missing_referenced_files"):
        uncertainty.append({
            "item": "missing_referenced_files",
            "reason": f"Files referenced in task/case missing from input directory: {case_plan['missing_referenced_files']}",
            "scope": "input_files",
        })

    existing_h_ids = {h.get("issue_id") for h in human_items if isinstance(h, dict)}
    for rc in lib.get("recycled_doi_conflicts", []):
        hid = f"HUM_RDOI_{rc['raw_a']}_{rc['raw_b']}"
        if hid not in existing_h_ids:
            human_items.append({
                "issue_id": hid,
                "severity": "critical",
                "location": f"{rc['raw_a']} vs {rc['raw_b']}",
                "question": f"Records share DOI/ID ({rc['metrics'].get('shared_id', '')}) but differ completely in title/authors ('{rc['title_a']}' vs '{rc['title_b']}'). Please confirm true DOI.",
                "checked_sources": "library_canonical.json",
            })
    edited_sids = {
        ed.get("sentence_id")
        for ed in (plan.get("manuscript_edits") or [])
        if isinstance(ed, dict) and ed.get("sentence_id") and str(ed.get("action", "")).lower() != "keep"
    }
    for dc in pre_audit.get("dangling_citations", []):
        # Skip auto-injecting dangling citations that were already explicitly resolved by a manuscript edit
        if args.checkpoint == "final" and dc.get("sentence_id") in edited_sids:
            continue
        hid = f"HUM_DANGLE_{dc['sentence_id']}_{dc['missing_token']}"
        if hid not in existing_h_ids:
            human_items.append({
                "issue_id": hid,
                "severity": "critical",
                "location": dc["sentence_id"],
                "question": f"In-text citation {dc['raw_marker']} ({dc['reason']}) has no matching entry in reference library. Supply correct reference or remove citation.",
                "checked_sources": "library_canonical.json, manuscript_parsed.json",
            })

    # Copy or initialize editable deliverables
    deliverable_files: List[str] = []
    if args.checkpoint == "final" and synced:
        for fname in (
            "revised_manuscript.md",
            "revised_manuscript.tex",
            "references_formatted.md",
            "references_clean.bib",
            "references_clean.ris",
            "references_clean.csl.json",
            "references_table.csv",
        ):
            src_f = synced_dir / fname
            if src_f.exists():
                shutil.copy2(src_f, out_dir / fname)
                deliverable_files.append(fname)
    else:
        # Partial checkpoint placeholders derived strictly from actual input
        (out_dir / "revised_manuscript.md").write_text(
            f"# Partial Checkpoint (`{case_plan.get('case_id', '')}`)\n\n"
            f"Input inspection & pre-audit completed (`status={status}`). Awaiting final citation_plan synchronization.\n",
            encoding="utf-8",
        )
        deliverable_files.append("revised_manuscript.md")

    revised_md_text = (out_dir / "revised_manuscript.md").read_text(encoding="utf-8")
    refs_formatted_list = [fr["formatted_entry"] for fr in synced.get("formatted_references", [])]

    # Optional DOCX & previews
    req_fmts = list(
        dict.fromkeys(
            (case_plan.get("signals", {}).get("requested_output_formats") or [])
            + (plan.get("requested_output_formats") or [])
        )
    )
    if args.checkpoint == "final" and "docx" in req_fmts:
        docx_path = out_dir / "revised_manuscript.docx"
        if args.skip_previews and docx_path.exists() and docx_path.stat().st_size > 0:
            deliverable_files.append("revised_manuscript.docx")
            if (out_dir / "revised_manuscript.pdf").exists():
                deliverable_files.append("revised_manuscript.pdf")
            for png in sorted(out_dir.glob("docx_preview*.png")):
                deliverable_files.append(png.name)
        else:
            ok, docx_err = _export_docx(revised_md_text, docx_path, f"Manuscript ({case_plan.get('case_id', '')})")
            if ok:
                deliverable_files.append("revised_manuscript.docx")
                previews, pnotes = _render_office_or_tex_previews(docx_path, out_dir, "docx")
                deliverable_files.extend(previews)
                for pn in pnotes:
                    uncertainty.append({"item": "office_preview", "reason": pn, "scope": "revised_manuscript.docx"})
            elif docx_err:
                uncertainty.append({"item": "docx_export", "reason": docx_err, "scope": "revised_manuscript.docx"})

    if args.checkpoint == "final" and (out_dir / "revised_manuscript.tex").exists():
        if args.skip_previews:
            if (out_dir / "revised_manuscript.pdf").exists():
                deliverable_files.append("revised_manuscript.pdf")
            for png in sorted(out_dir.glob("tex_preview*.png")):
                deliverable_files.append(png.name)
        else:
            previews, pnotes = _render_office_or_tex_previews(out_dir / "revised_manuscript.tex", out_dir, "tex")
            deliverable_files.extend(previews)
            for pn in pnotes:
                uncertainty.append({"item": "latex_preview", "reason": pn, "scope": "revised_manuscript.tex"})

    # Audit report & provenance ledger
    audit_md = _build_audit_report(case_plan, lib, ms, pre_audit, plan, synced, verification, uncertainty, human_items, args.checkpoint)
    (out_dir / "citation_audit_report.md").write_text(audit_md, encoding="utf-8")
    deliverable_files.append("citation_audit_report.md")

    provenance_ledger = {
        "case_id": case_plan.get("case_id", ""),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_files": [f["rel_path"] for f in case_plan.get("files", [])],
        "raw_to_canonical_map": lib.get("raw_to_canonical_map", {}),
        "merge_groups": lib.get("merge_groups", []),
        "protected_non_duplicates": lib.get("protected_non_duplicates", []),
        "recycled_doi_conflicts": lib.get("recycled_doi_conflicts", []),
        "field_provenance_by_ref": {
            r["ref_id"]: r.get("field_provenance", {}) for r in recs
        },
        "renumbering_map": synced.get("renumbering_map", []),
        "claim_verifications": plan.get("claim_verifications", []),
        "change_records": synced.get("change_records", []),
    }
    save_json(out_dir / "provenance_ledger.json", provenance_ledger)
    deliverable_files.append("provenance_ledger.json")
    deliverable_files = list(dict.fromkeys(["result.json"] + deliverable_files))

    # Build result.json satisfying output_contract + full provenance
    role_values: Dict[str, Any] = {
        "case_id": case_plan.get("case_id", ""),
        "status": status,
        "revised_manuscript": revised_md_text,
        "reference_list": refs_formatted_list,
        "renumbering_map": synced.get("renumbering_map", []),
        "dedup_report": {
            "raw_record_count": lib.get("stats", {}).get("raw_record_count", 0),
            "canonical_record_count": len(recs),
            "duplicates_removed": lib.get("stats", {}).get("duplicates_removed", 0),
            "merge_groups": lib.get("merge_groups", []),
            "extra_tier3_merges": synced.get("extra_tier3_merges", []),
            "ambiguous_candidates": lib.get("ambiguous_duplicate_candidates", []),
            "protected_non_duplicates": lib.get("protected_non_duplicates", []),
            "recycled_doi_conflicts": lib.get("recycled_doi_conflicts", []),
        },
        "claim_verification": plan.get("claim_verifications") or pre_audit.get("sentence_links", []),
        "field_provenance": {
            "input_snapshot": [f["rel_path"] for f in case_plan.get("files", [])],
            "by_ref_id": {r["ref_id"]: r.get("field_provenance", {}) for r in recs},
        },
        "change_records": synced.get("change_records", []),
        "uncertainty": uncertainty,
        "human_confirmation_items": human_items,
        "deliverable_files": deliverable_files,
        "summary": (
            f"Processed {len(recs)} canonical references ({lib.get('stats', {}).get('duplicates_removed', 0)} duplicates merged) "
            f"in style `{synced.get('target_style') or case_plan.get('signals', {}).get('target_style')}`, "
            f"{synced.get('stats', {}).get('cited_ref_count', 0)} cited in manuscript; status={status}."
        ),
    }

    result_obj: Dict[str, Any] = {
        "case_id": role_values["case_id"],
        "status": status,
        "checkpoint": args.checkpoint,
        "target_style": synced.get("target_style") or case_plan.get("signals", {}).get("target_style"),
        "in_text_format": synced.get("in_text_format") or case_plan.get("signals", {}).get("in_text_format"),
        "revised_manuscript": role_values["revised_manuscript"],
        "reference_list": role_values["reference_list"],
        "renumbering_map": role_values["renumbering_map"],
        "dedup_report": role_values["dedup_report"],
        "claim_verifications": role_values["claim_verification"],
        "sources": role_values["field_provenance"],
        "change_records": role_values["change_records"],
        "uncertainty": role_values["uncertainty"],
        "human_confirmation_items": role_values["human_confirmation_items"],
        "uncited_library_entries": synced.get("uncited_library_entries", []),
        "deliverable_files": deliverable_files,
        "verification_summary": verification if verification else {"passed": None, "checkpoint": args.checkpoint},
    }

    # Guarantee EVERY declared key from output_contract is present and coerced to expected type
    overrides = plan.get("contract_overrides") or {}
    for field in case_plan.get("output_contract_parsed", {}).get("fields", []):
        fname = field["name"]
        etype = field.get("expected_type", "any")
        if fname in overrides:
            result_obj[fname] = _coerce_to_contract_type(overrides[fname], etype)
        elif fname in result_obj:
            result_obj[fname] = _coerce_to_contract_type(result_obj[fname], etype)
        else:
            role = field.get("semantic_role", "custom")
            val = role_values.get(role, role_values["summary"])
            result_obj[fname] = _coerce_to_contract_type(val, etype)

    save_json(out_dir / "result.json", result_obj)
    print(json.dumps({
        "checkpoint": args.checkpoint,
        "status": status,
        "out_dir": str(out_dir),
        "deliverable_files": deliverable_files,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
