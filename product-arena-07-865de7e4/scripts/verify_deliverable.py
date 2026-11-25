#!/usr/bin/env python3
"""Step 5: Multi-axis verification gate for delivered LaTeX sources, compilation,
numeric/citation fidelity, template compliance, GAP-XX alignment, and output_contract.

Exits 0 when verification passes (error_count == 0), non-zero otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_latex import (
    CITE_CMDS,
    REF_CMDS,
    check_brace_and_env_balance,
    check_tables_math_and_antipatterns,
    check_template_requirement,
    parse_bib_file,
)
from common import (
    extract_numeric_tokens,
    extract_tex_commands,
    load_json_safe,
    normalize_num_str,
    read_text_safe,
    resolve_skill_dir,
    sanitize_tex_for_scan,
    save_json,
    strip_tex_comments_preserve_lines,
)

GAP_TAG_RE = re.compile(r"\[(?:[^\]]*?\b(?:AUTHOR_CONFIRM|待作者确认)\s*:\s*)(GAP-[A-Za-z0-9_-]+)[^\]]*\]")
LAYOUT_DIM_RE = re.compile(
    r"[-+]?\d+(?:\.\d+)?\s*(?:pt|bp|pc|mm|cm|in|dd|cc|sp|em|ex|px|\\linewidth|\\textwidth|\\textheight|\\columnwidth|\\paperwidth|\\paperheight)\b"
)


def strip_preamble_and_layout_tokens(tex_text: str) -> str:
    """Return document body stripped of LaTeX layout dimensions/options to reduce false numeric alerts."""
    no_comments = sanitize_tex_for_scan(tex_text)
    m_beg = re.search(r"\\begin\{document\}", no_comments)
    body = no_comments[m_beg.end() :] if m_beg else no_comments
    m_end = re.search(r"\\end\{document\}", body)
    if m_end:
        body = body[: m_end.start()]
    # Remove \includegraphics[...]{...}, \begin{tabular*}{...}, \hspace{...}, \vspace{...}, p{...}
    body = re.sub(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", " ", body)
    body = re.sub(r"\\(?:hspace|vspace|setlength|addtolength)\*?\{[^}]*\}(?:\{[^}]*\})?", " ", body)
    body = LAYOUT_DIM_RE.sub(" ", body)
    return body


def resolve_deliverable_tex(
    payload: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    tex_dir_override: Optional[Path],
) -> Optional[Path]:
    """Locate the primary delivered .tex file on disk. Never mask an explicitly missing primary_tex_path."""
    primary_rel = str(payload.get("primary_tex_path") or "").strip()
    candidates: List[Path] = []
    if tex_dir_override:
        if primary_rel:
            candidates.append(tex_dir_override / primary_rel)
            candidates.append(tex_dir_override / Path(primary_rel).name)
        else:
            candidates.extend(sorted(tex_dir_override.rglob("*.tex")))
    if compile_report:
        for base_k in ("project_root", "build_dir"):
            if compile_report.get(base_k):
                bdir = Path(compile_report[base_k])
                if primary_rel:
                    candidates.append(bdir / primary_rel)
                    candidates.append(bdir / Path(primary_rel).name)
                elif compile_report.get("tex_file"):
                    candidates.append(bdir / compile_report["tex_file"])
    if primary_rel and Path(primary_rel).is_file():
        candidates.append(Path(primary_rel))

    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def verify_deliverable(
    payload: Dict[str, Any],
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    skill_dir: Path,
    tex_dir_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run 6-axis verification and return structured verification_report."""
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}

    conflict_rules = load_json_safe(skill_dir / "resources" / "package_conflicts.json", default={}) or {}
    template_profiles = load_json_safe(skill_dir / "resources" / "template_profiles.json", default={}) or {}

    status = str(payload.get("status") or "").strip()
    if status not in {"completed", "partial", "unable_to_determine"}:
        errors.append({"axis": "schema", "code": "VER-INVALID-STATUS", "message": f"Invalid payload status '{status}'; must be completed|partial|unable_to_determine."})

    # Validate payload_schema list-of-object fields so string lists don't crash assemble_deliverables.py
    for list_key in ("sources", "change_records", "uncertainty", "author_confirmation_items", "limitations", "output_files"):
        lv = payload.get(list_key)
        if lv is not None:
            if not isinstance(lv, list) or any(not isinstance(item, dict) for item in lv):
                errors.append(
                    {
                        "axis": "schema",
                        "code": "VER-INVALID-PAYLOAD-TYPE",
                        "message": f"payload.{list_key} must be an array of JSON objects per payload_schema.json.",
                    }
                )

    # ── Axis 1: Compilation & Convergence ────────────────────────────────────
    if status == "completed":
        if not compile_report:
            errors.append({"axis": "compile", "code": "VER-NO-COMPILE-REPORT", "message": "Status is 'completed' but compile_report.json was not supplied."})
        else:
            c_status = compile_report.get("status")
            diag = compile_report.get("log_diagnosis", {})
            pa = compile_report.get("pdf_audit", {})
            if c_status != "compiled_clean":
                errors.append({"axis": "compile", "code": "VER-COMPILE-NOT-CLEAN", "message": f"Compile status is '{c_status}' (errors={len(diag.get('errors', []))}). Fix all errors or report honest partial status."})
            if compile_report.get("bib_error"):
                errors.append({"axis": "compile", "code": "VER-BIB-ERROR", "message": f"Bibliography pass failed or reported errors: {(compile_report.get('bib_diagnostics') or [])[:5]}."})
            if not compile_report.get("converged"):
                errors.append({"axis": "compile", "code": "VER-NOT-CONVERGED", "message": "Multi-pass compilation did not converge (rerun warnings or unstable aux state)."})
            if diag.get("missing_characters"):
                errors.append({"axis": "compile", "code": "VER-MISSING-GLYPHS", "message": f"{len(diag['missing_characters'])} missing glyph(s) detected in .log (e.g., {diag['missing_characters'][0]})."})
            if diag.get("undefined_references"):
                errors.append({"axis": "compile", "code": "VER-UNDEF-REFERENCES", "message": f"{len(diag['undefined_references'])} undefined reference(s) in .log: {diag['undefined_references'][:5]}."})
            if diag.get("undefined_citations"):
                errors.append({"axis": "compile", "code": "VER-UNDEF-CITATIONS", "message": f"{len(diag['undefined_citations'])} undefined citation(s) in .log: {diag['undefined_citations'][:5]}."})
            if pa.get("unresolved_markers"):
                errors.append({"axis": "compile", "code": "VER-PDF-UNRESOLVED-MARKER", "message": f"Literal unresolved markers (?? / [?]) detected in rendered PDF: {pa['unresolved_markers']}."})
            if pa.get("empty_pages"):
                errors.append({"axis": "compile", "code": "VER-PDF-EMPTY-PAGES", "message": f"Blank page(s) with no text or graphics detected in PDF: {pa['empty_pages']}."})
            if pa.get("image_only_pages"):
                warnings.append({"axis": "compile", "code": "VER-PDF-IMAGE-ONLY-PAGES", "message": f"Full-page figure/graphic page(s) with zero text detected: {pa['image_only_pages']}."})
            if not compile_report.get("previews"):
                warnings.append({"axis": "compile", "code": "VER-NO-PREVIEWS", "message": "No PNG preview images rendered from compiled PDF."})
            for oh in diag.get("large_overfull_hboxes", []):
                warnings.append({"axis": "compile", "code": "VER-OVERFULL-HBOX", "message": f"Large Overfull \\hbox ({oh['pt']}pt) in {oh['file']}: {oh['line']}"})
        checks["compile_axis"] = "checked"
    else:
        if not payload.get("uncertainty"):
            errors.append({"axis": "honesty", "code": "VER-EMPTY-UNCERTAINTY", "message": f"Status is '{status}' but payload.uncertainty[] is empty; must explain blocker/limits."})
        checks["compile_axis"] = f"partial_or_blocked ({status})"

    # ── Axis 2: Source Quality & Maintainability ─────────────────────────────
    primary_tex = resolve_deliverable_tex(payload, compile_report, tex_dir_override)
    tex_text = ""
    tex_files_checked: List[Path] = []
    tex_contents_by_rel: Dict[str, str] = {}
    bib_entries_delivered: Dict[str, Any] = {}
    delivered_packages: List[Dict[str, Any]] = []
    delivered_labels: Dict[str, List[Dict[str, Any]]] = {}
    delivered_refs: List[Dict[str, Any]] = []
    delivered_cites: List[Dict[str, Any]] = []

    if primary_tex is None:
        if status == "completed" or str(payload.get("primary_tex_path") or "").strip():
            errors.append({"axis": "source_quality", "code": "VER-MISSING-PRIMARY-TEX", "message": f"Delivered primary_tex_path '{payload.get('primary_tex_path')}' not found on disk."})
        checks["source_quality_axis"] = "missing_tex"
    else:
        tex_root = tex_dir_override.resolve() if (tex_dir_override and tex_dir_override.is_dir()) else primary_tex.parent
        tex_files_checked = [
            p for p in sorted(tex_root.rglob("*.tex"))
            if not any(part.startswith("_fallback") or part in {".git", "__pycache__"} for part in p.parts)
        ]
        tex_text, err = read_text_safe(primary_tex)
        if err or not tex_text.strip():
            errors.append({"axis": "source_quality", "code": "VER-UNREADABLE-TEX", "message": f"Primary .tex is unreadable or empty ({err})."})

        eng_used = (compile_report or {}).get("engine") or audit.get("recommended_engine", "pdflatex")
        for tf in tex_files_checked:
            ftxt, ferr = read_text_safe(tf)
            if ferr:
                continue
            rel = tf.relative_to(tex_root).as_posix()
            tex_contents_by_rel[rel] = ftxt
            for f in check_brace_and_env_balance(rel, ftxt):
                errors.append({"axis": "source_quality", "code": f["id"], "message": f"{f['file']}:{f['line']} — {f['message']}"})
            for f in check_tables_math_and_antipatterns(rel, ftxt, conflict_rules, eng_used):
                if f["severity"] == "error":
                    errors.append({"axis": "source_quality", "code": f["id"], "message": f"{f['file']}:{f['line']} — {f['message']}"})
                elif "resizebox" in f["message"] or "scalebox" in f["message"]:
                    errors.append({"axis": "source_quality", "code": "VER-RESIZEBOX-TABLE-HACK", "message": f"{f['file']}:{f['line']} — {f['message']}"})
                else:
                    warnings.append({"axis": "source_quality", "code": f["id"], "message": f"{f['file']}:{f['line']} — {f['message']}"})

            for pk in extract_tex_commands(ftxt, ("usepackage", "RequirePackage")):
                for p_item in pk["arg"].split(","):
                    pname = p_item.strip()
                    if pname:
                        delivered_packages.append({"package": pname, "options": pk["opt"], "file": rel, "line": pk["line"]})
            for lb in extract_tex_commands(ftxt, ("label",)):
                if lb["arg"]:
                    delivered_labels.setdefault(lb["arg"], []).append({"file": rel, "line": lb["line"]})
            for rf in extract_tex_commands(ftxt, REF_CMDS):
                for rkey in rf["arg"].split(","):
                    rk = rkey.strip()
                    if rk:
                        delivered_refs.append({"key": rk, "cmd": rf["command"], "file": rel, "line": rf["line"]})
            for ct in extract_tex_commands(ftxt, CITE_CMDS):
                for ckey in ct["arg"].split(","):
                    ck = ckey.strip()
                    if ck:
                        delivered_cites.append({"key": ck, "cmd": ct["command"], "file": rel, "line": ct["line"]})

        # Package conflict & load order re-verification on delivered sources
        pkg_names_lower = {p["package"].lower(): p for p in delivered_packages}
        combined_clean_deliv = "\n".join(sanitize_tex_for_scan(t) for t in tex_contents_by_rel.values())
        has_eng_guard = bool(re.search(r"\\if(?:PDFTeX|pdfTeX|xetex|XeTeX|luatex|LuaTeX|tutex)\b", combined_clean_deliv))
        for pair in conflict_rules.get("incompatible_pairs", []):
            pa, pb = pair["pkg_a"].lower(), pair["pkg_b"].lower()
            if pa in pkg_names_lower and pb in pkg_names_lower:
                if {pa, pb} == {"inputenc", "fontspec"} and has_eng_guard:
                    continue
                errors.append({"axis": "source_quality", "code": "LINT-PKG-CONFLICT", "message": f"{pkg_names_lower[pb]['file']}:{pkg_names_lower[pb]['line']} — Incompatible packages '{pair['pkg_a']}' and '{pair['pkg_b']}': {pair['reason']}"})
        pkgs_by_file: Dict[str, List[Dict[str, Any]]] = {}
        for p in delivered_packages:
            pkgs_by_file.setdefault(p["file"], []).append(p)
        for frel, plist in pkgs_by_file.items():
            idx_map = {p["package"].lower(): i for i, p in enumerate(plist)}
            for rule in conflict_rules.get("load_order_rules", []):
                fk, sk = rule["first"].lower(), rule["second"].lower()
                if fk in idx_map and sk in idx_map and idx_map[sk] < idx_map[fk]:
                    warnings.append({"axis": "source_quality", "code": "LINT-PKG-ORDER", "message": f"{frel}:{plist[idx_map[sk]]['line']} — Package '{rule['second']}' loaded before '{rule['first']}': {rule['reason']}"})

        # Duplicate \label and unresolved \ref on delivered sources
        for lkey, locs in delivered_labels.items():
            if len(locs) > 1:
                warnings.append({"axis": "source_quality", "code": "LINT-DUPLICATE-LABEL", "message": f"Duplicate \\label{{{lkey}}} in {locs[1]['file']}:{locs[1]['line']} (first at {locs[0]['file']}:{locs[0]['line']})."})
        for rf in delivered_refs:
            if rf["key"] not in delivered_labels:
                errors.append({"axis": "source_quality", "code": "LINT-UNRESOLVED-REF", "message": f"{rf['file']}:{rf['line']} — \\{rf['cmd']}{{{rf['key']}}} has no matching \\label{{{rf['key']}}} in delivered project."})

        for bf in sorted(tex_root.rglob("*.bib")):
            if any(part.startswith("_fallback") or part in {".git", "__pycache__"} for part in bf.parts):
                continue
            btxt, berr = read_text_safe(bf)
            if berr:
                continue
            rel_b = bf.relative_to(tex_root).as_posix()
            entries, bf_issues = parse_bib_file(rel_b, btxt)
            for bk, bent in entries.items():
                if bk in bib_entries_delivered:
                    errors.append({"axis": "source_quality", "code": "BIB-DUPLICATE-KEY", "message": f"{bent['file']}:{bent['line']} — Duplicate BibTeX key '{bk}' across delivered .bib files (also in {bib_entries_delivered[bk]['file']}:{bib_entries_delivered[bk]['line']})."})
                else:
                    bib_entries_delivered[bk] = bent
            for bi in bf_issues:
                if bi["severity"] == "error":
                    errors.append({"axis": "source_quality", "code": bi["id"], "message": f"{bi['file']}:{bi['line']} — {bi['message']}"})
                else:
                    warnings.append({"axis": "source_quality", "code": bi["id"], "message": f"{bi['file']}:{bi['line']} — {bi['message']}"})

        checks["source_quality_axis"] = f"checked_{len(tex_files_checked)}_tex_files"

    # ── Axis 3: Numeric & Citation Fidelity (per-file body stripping so subfiles are NEVER dropped) ──
    all_delivered_tex = "\n".join(tex_contents_by_rel.values()) if tex_contents_by_rel else tex_text
    all_delivered_clean = "\n".join(sanitize_tex_for_scan(t) for t in tex_contents_by_rel.values()) if tex_contents_by_rel else sanitize_tex_for_scan(tex_text)
    if all_delivered_tex:
        allowed_nums: Set[str] = set()
        for raw_n in audit.get("source_numbers", []):
            allowed_nums.add(raw_n)
            if raw_n.endswith("%"):
                allowed_nums.add(raw_n[:-1])
        for tok in extract_numeric_tokens(case_plan.get("task_text_preview", "")):
            n = normalize_num_str(tok)
            if n:
                allowed_nums.add(n)
                if n.endswith("%"):
                    allowed_nums.add(n[:-1])

        per_file_bodies = [strip_preamble_and_layout_tokens(t) for t in (tex_contents_by_rel.values() or [tex_text])]
        body_for_nums = "\n".join(per_file_bodies)
        ungrounded_nums: List[str] = []
        seen_body_nums: Set[str] = set()
        if allowed_nums:
            for tok in extract_numeric_tokens(body_for_nums):
                n = normalize_num_str(tok)
                if not n:
                    continue
                seen_body_nums.add(n)
                seen_body_nums.add(n.rstrip("%"))
                # Ignore simple 1-digit integers (often list/section numbers or table cols)
                if re.fullmatch(r"\d", n):
                    continue
                if n not in allowed_nums and n.rstrip("%") not in allowed_nums and tok not in ungrounded_nums:
                    ungrounded_nums.append(tok)

        if ungrounded_nums:
            warnings.append(
                {
                    "axis": "fidelity",
                    "code": "VER-UNSEEN-BODY-NUMBERS",
                    "message": f"Delivered .tex body contains numeric tokens not seen in /input source facts ({len(ungrounded_nums)}): {', '.join(ungrounded_nums[:12])}. Confirm they are layout constants or mark provenance.",
                }
            )

        # Check citations across ALL 13 CITE_CMDS even when .bib is empty (ignore \nocite{*})
        inline_bibs = {b["arg"] for t in (tex_contents_by_rel.values() or [tex_text]) for b in extract_tex_commands(t, ("bibitem",)) if b["arg"]}
        known_delivered_keys = set(bib_entries_delivered.keys()) | inline_bibs
        for ct in delivered_cites:
            ck = ct["key"]
            if ct["cmd"] == "nocite" and ck == "*":
                continue
            if ck and ck not in known_delivered_keys:
                errors.append({"axis": "fidelity", "code": "VER-UNRESOLVED-CITE-IN-DELIVERABLE", "message": f"{ct['file']}:{ct['line']} — Citation key '{ck}' in \\{ct['cmd']} has no entry in delivered .bib / \\bibitem."})
        checks["fidelity_axis"] = f"unseen_numbers={len(ungrounded_nums)}"

    # ── Axis 4: Template Compliance ──────────────────────────────────────────
    tpl_comp = payload.get("template_compliance") or {}
    tpl_name = str(tpl_comp.get("template_name") or "").strip()
    if not tpl_name and all_delivered_tex:
        dcs = extract_tex_commands(all_delivered_tex, ("documentclass",))
        if dcs:
            tpl_name = dcs[0]["arg"]

    prof = template_profiles.get("profiles", {}).get(tpl_name)
    if prof and all_delivered_clean:
        missing_reqs: List[str] = []
        for req in prof.get("required_elements", []):
            if not check_template_requirement(req, all_delivered_clean):
                missing_reqs.append(req)
        if missing_reqs:
            if status == "completed":
                warnings.append({"axis": "template", "code": "VER-TEMPLATE-REQ-MISSING", "message": f"Template '{tpl_name}' expected element(s) not detected in delivered .tex: {missing_reqs}"})
        checks["template_axis"] = f"{tpl_name} (missing={len(missing_reqs)})"
    else:
        checks["template_axis"] = tpl_name or "generic"

    # ── Axis 5: Provenance & GAP-XX Alignment ────────────────────────────────
    gap_tags_in_tex = set(GAP_TAG_RE.findall(all_delivered_tex))
    confirm_items = payload.get("author_confirmation_items") or []
    confirm_ids = {str(item.get("gap_id") or "").strip() for item in confirm_items if isinstance(item, dict) and item.get("gap_id")}

    for gid in sorted(gap_tags_in_tex - confirm_ids):
        errors.append({"axis": "provenance", "code": "VER-GAP-UNMAPPED", "message": f"Inline tag '{gid}' in .tex is missing a corresponding entry in payload.author_confirmation_items[]."})

    if case_plan.get("stats", {}).get("readable_files", 0) > 0 and not payload.get("sources"):
        warnings.append({"axis": "provenance", "code": "VER-NO-SOURCES", "message": "payload.sources[] is empty despite readable input materials."})

    # ── Axis 6: Output Contract Completeness ─────────────────────────────────
    expected_cid = case_plan.get("case_id")
    if payload.get("case_id") != expected_cid:
        errors.append({"axis": "contract", "code": "VER-CASE-ID-MISMATCH", "message": f"payload.case_id '{payload.get('case_id')}' does not match case_plan '{expected_cid}'."})

    req_keys = case_plan.get("output_contract", {}).get("required_keys", [])
    overrides = payload.get("contract_overrides") or {}
    # Check custom keys not automatically derivable
    unmapped_custom = []
    for fspec in case_plan.get("output_contract", {}).get("fields", []):
        fname = fspec["name"]
        if fname in payload or fname in overrides:
            continue
        if fspec["semantic_role"] == "custom" and fspec["required"]:
            unmapped_custom.append(fname)
    if unmapped_custom:
        warnings.append({"axis": "contract", "code": "VER-CUSTOM-CONTRACT-DEFAULTED", "message": f"Custom output_contract keys {unmapped_custom} not explicitly in contract_overrides; assemble_deliverables.py will populate safe defaults."})

    passed = len(errors) == 0
    return {
        "schema_version": "1.0",
        "case_id": expected_cid,
        "passed": passed,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "gap_tags_in_tex": sorted(gap_tags_in_tex),
        "required_contract_keys": req_keys,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify LaTeX deliverable quality, fidelity, gaps, and output contract.")
    parser.add_argument("--payload", default="/app/work/deliverable_payload.json")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--audit", default="/app/work/latex_audit.json")
    parser.add_argument("--compile-report", default="/app/work/compile_report.json")
    parser.add_argument("--work-tex-dir", default=None, help="Directory holding final editable .tex/.bib files.")
    parser.add_argument("--skill-dir", default=None)
    parser.add_argument("--out", default="/app/work/verification_report.json")
    args = parser.parse_args()

    skill_dir = resolve_skill_dir(args.skill_dir)
    payload = load_json_safe(Path(args.payload))
    case_plan = load_json_safe(Path(args.case_plan))
    audit = load_json_safe(Path(args.audit), default={}) or {}
    compile_report = load_json_safe(Path(args.compile_report)) if args.compile_report else None

    if not payload or not case_plan:
        raise SystemExit("Missing payload or case_plan JSON.")

    report = verify_deliverable(
        payload=payload,
        case_plan=case_plan,
        audit=audit,
        compile_report=compile_report,
        skill_dir=skill_dir,
        tex_dir_override=Path(args.work_tex_dir) if args.work_tex_dir else None,
    )
    save_json(Path(args.out), report)
    print(f"[verify_deliverable] passed={report['passed']} errors={report['error_count']} warnings={report['warning_count']} -> {args.out}")
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
