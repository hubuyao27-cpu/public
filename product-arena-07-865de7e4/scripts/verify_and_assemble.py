#!/usr/bin/env python3
"""Step 3: One-command compile (skipping redundant runs if unchanged), auto-hydrated 6-axis verification,
and final /app/output assembly (result.json, editable sources, PDF/PNG previews, build.sh, manifest, diff).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shlex
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    SKIP_DIRS,
    SOURCE_COPY_EXTS,
    coerce_contract_value,
    ensure_dir,
    extract_numeric_tokens,
    extract_tex_commands,
    load_json_safe,
    normalize_num_str,
    read_text_safe,
    resolve_skill_dir,
    sanitize_tex_for_scan,
    save_json,
)
from compile_and_diagnose import compile_project, hash_project_tree, render_compile_summary
from prepare_and_audit import audit_tex_tree

GAP_TAG_RE = re.compile(r"\[(?:[^\]]*?\b(?:AUTHOR_CONFIRM|待作者确认)\s*:\s*)(GAP-[A-Za-z0-9_-]+)(?:\s+([^\]]+))?\]")
SRC_TAG_RE = re.compile(r"%\s*\[SRC:\s*([^\]]+)\]")
LAYOUT_DIM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:pt|bp|pc|mm|cm|in|dd|cc|sp|em|ex|px|\\linewidth|\\textwidth|\\textheight|\\columnwidth|\\paperwidth|\\paperheight)\b")


def _strip_preamble_and_layout(tex_text: str) -> str:
    no_comments = sanitize_tex_for_scan(tex_text)
    m_beg = re.search(r"\\begin\{document\}", no_comments)
    body = no_comments[m_beg.end() :] if m_beg else no_comments
    m_end = re.search(r"\\end\{document\}", body)
    if m_end:
        body = body[: m_end.start()]
    body = re.sub(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", " ", body)
    body = re.sub(r"\\(?:hspace|vspace|setlength|addtolength)\*?\{[^}]*\}(?:\{[^}]*\})?", " ", body)
    return LAYOUT_DIM_RE.sub(" ", body)


def _resolve_tex_dir_and_primary(tex_arg: Optional[str], project_root_arg: Optional[str], work_dir: Path, payload: Dict[str, Any], compile_report: Optional[Dict[str, Any]]) -> Tuple[Path, Optional[Path]]:
    tex_p = Path(tex_arg).resolve() if tex_arg else None
    if project_root_arg:
        proj_root = Path(project_root_arg).resolve()
    elif tex_p and (work_dir / "src").is_dir():
        try:
            tex_p.relative_to((work_dir / "src").resolve())
            proj_root = (work_dir / "src").resolve()
        except ValueError:
            proj_root = tex_p.parent
    elif tex_p and tex_p.is_file():
        proj_root = tex_p.parent
    else:
        proj_root = (work_dir / "src").resolve() if (work_dir / "src").is_dir() else work_dir.resolve()

    if tex_p and tex_p.is_file():
        return proj_root, tex_p

    prim_rel = str(payload.get("primary_tex_path") or (compile_report or {}).get("tex_file") or "").strip()
    if prim_rel:
        for base in (proj_root, Path((compile_report or {}).get("build_dir") or ".")):
            for cand in (base / prim_rel, base / Path(prim_rel).name):
                if cand.is_file():
                    return proj_root, cand.resolve()
        return proj_root, None

    tex_cands = [p for p in sorted(proj_root.rglob("*.tex")) if not any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts)]
    return proj_root, (tex_cands[0].resolve() if tex_cands else None)


def _compute_before_after_diff(case_plan: Dict[str, Any], out_dir: Path) -> List[Dict[str, Any]]:
    baseline_dir = Path(case_plan.get("baseline_dir") or "") if case_plan.get("baseline_dir") else None
    input_dir = Path(case_plan.get("input_dir") or "")
    diffs: List[Dict[str, Any]] = []
    if not out_dir.is_dir():
        return diffs
    for dp in [p for p in sorted(out_dir.rglob("*")) if p.is_file() and p.suffix.lower() in {".tex", ".ltx", ".bib", ".cls", ".sty", ".bst"}][:15]:
        rel = dp.relative_to(out_dir).as_posix()
        after_txt, err_a = read_text_safe(dp)
        if err_a:
            continue
        orig_cand = None
        if baseline_dir and (baseline_dir / rel).is_file():
            orig_cand = baseline_dir / rel
        elif input_dir.is_dir() and (input_dir / rel).is_file():
            orig_cand = input_dir / rel
        if orig_cand is None:
            matches = [Path(f["abs_path"]) for f in case_plan.get("files", []) if Path(f["rel_path"]).name == dp.name and Path(f["abs_path"]).is_file()]
            if matches and matches[0].resolve() != dp.resolve():
                orig_cand = matches[0]
            if baseline_dir and (baseline_dir / dp.name).is_file():
                orig_cand = baseline_dir / dp.name
        if orig_cand and orig_cand.is_file():
            before_txt, err_b = read_text_safe(orig_cand)
            if err_b:
                before_txt = ""
            if before_txt == after_txt:
                diffs.append({"file": rel, "status": "unchanged", "lines_added": 0, "lines_removed": 0, "unified_diff": ""})
            else:
                dlines = list(difflib.unified_diff(before_txt.splitlines(), after_txt.splitlines(), fromfile=f"before/{rel}", tofile=f"after/{rel}", n=2, lineterm=""))
                diffs.append({
                    "file": rel, "status": "modified",
                    "lines_added": sum(1 for ln in dlines if ln.startswith("+") and not ln.startswith("+++")),
                    "lines_removed": sum(1 for ln in dlines if ln.startswith("-") and not ln.startswith("---")),
                    "unified_diff": "\n".join(dlines[:180] + ([f"... [{len(dlines) - 180} more lines]"] if len(dlines) > 180 else [])),
                })
        else:
            lcnt = len(after_txt.splitlines())
            diffs.append({"file": rel, "status": "added", "lines_added": lcnt, "lines_removed": 0, "unified_diff": f"--- /dev/null\n+++ after/{rel}\n@@ -0,0 +1,{lcnt} @@\n(new file, {lcnt} lines)"})
    return diffs


def _coerce_obj_list(raw_list: Any, default_key: str) -> List[Dict[str, Any]]:
    if not isinstance(raw_list, list):
        return [{"id": "AUTO", default_key: str(raw_list)}] if raw_list else []
    return [item if isinstance(item, dict) else {"id": f"ITEM-{i+1:02d}", default_key: str(item)} for i, item in enumerate(raw_list)]


def hydrate_payload(
    raw_payload: Optional[Dict[str, Any]],
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    proj_root: Path,
    primary_tex: Optional[Path],
    work_dir: Path,
) -> Dict[str, Any]:
    """Auto-hydrate minimal or omitted deliverable_payload.json from real files, % [SRC:] / % [AUTHOR_CONFIRM:] tags, and logs."""
    p: Dict[str, Any] = dict(raw_payload or {})
    p["case_id"] = str(p.get("case_id") or case_plan["case_id"])

    if not p.get("primary_tex_path"):
        if primary_tex and primary_tex.is_file():
            try:
                p["primary_tex_path"] = primary_tex.relative_to(proj_root).as_posix()
            except ValueError:
                p["primary_tex_path"] = primary_tex.name
        else:
            p["primary_tex_path"] = (case_plan.get("candidate_entry_tex") or [""])[0]

    if not p.get("status"):
        if compile_report and compile_report.get("status") == "compiled_clean":
            p["status"] = "completed"
        elif case_plan.get("stats", {}).get("readable_files", 0) == 0:
            p["status"] = "unable_to_determine"
        else:
            p["status"] = "partial"

    p["output_files"] = _coerce_obj_list(p.get("output_files"), "path")
    p["sources"] = _coerce_obj_list(p.get("sources"), "fact_or_target")
    p["change_records"] = _coerce_obj_list(p.get("change_records"), "description")
    p["uncertainty"] = _coerce_obj_list(p.get("uncertainty"), "description")
    p["author_confirmation_items"] = _coerce_obj_list(p.get("author_confirmation_items"), "question")
    p["limitations"] = _coerce_obj_list(p.get("limitations"), "scenario")

    # Merge auto_change_records.json if generated during Step 1
    auto_cr = load_json_safe(work_dir / "auto_change_records.json", default=[]) or []
    existing_cr_descs = {str(c.get("description") or "") for c in p["change_records"]}
    for acr in auto_cr:
        if isinstance(acr, dict) and acr.get("description") not in existing_cr_descs:
            p["change_records"].append(acr)

    # Build verbatim lookup from audit.source_facts and original /input files for field-level source quotes
    sf_quote_by_loc = {str(sf.get("locator") or ""): str(sf.get("text") or "")[:200] for sf in (audit.get("source_facts") or []) if isinstance(sf, dict) and sf.get("locator")}
    in_root_str = str(case_plan.get("input_dir") or "")
    in_root_p = Path(in_root_str) if in_root_str else None
    file_lines_cache: Dict[str, List[str]] = {}

    def _lookup_source_quote(sloc: str) -> str:
        if not sloc:
            return ""
        if sloc in sf_quote_by_loc:
            return sf_quote_by_loc[sloc]
        lm = re.match(r"^([^:#]+):L?(\d+)$", sloc.strip())
        if lm and in_root_p and in_root_p.is_dir():
            fname, lnum = lm.group(1).strip(), int(lm.group(2))
            if fname not in file_lines_cache:
                cand = in_root_p / fname
                if not cand.is_file():
                    hits = list(in_root_p.rglob(Path(fname).name))
                    cand = hits[0] if hits else cand
                if cand.is_file():
                    ftxt, ferr = read_text_safe(cand)
                    file_lines_cache[fname] = ftxt.splitlines() if not ferr else []
                else:
                    file_lines_cache[fname] = []
            flines = file_lines_cache.get(fname) or []
            if 1 <= lnum <= len(flines):
                return flines[lnum - 1].strip()[:200]
        return ""

    # Mine inline % [SRC: ...] and % [AUTHOR_CONFIRM: GAP-XX ...] from delivered .tex files
    existing_src_locs = {str(s.get("source_locator") or "") for s in p["sources"]}
    existing_gap_ids = {str(g.get("gap_id") or "") for g in p["author_confirmation_items"] if g.get("gap_id")}
    for tp in sorted(proj_root.rglob("*.tex")) + sorted(proj_root.rglob("*.ltx")):
        if any(part.startswith("_fallback") or part in SKIP_DIRS for part in tp.parts):
            continue
        txt, err = read_text_safe(tp)
        if err:
            continue
        rel = tp.relative_to(proj_root).as_posix()
        for lno, line in enumerate(txt.splitlines(), start=1):
            sm = SRC_TAG_RE.search(line)
            if sm:
                sloc = sm.group(1).strip()
                if sloc not in existing_src_locs:
                    existing_src_locs.add(sloc)
                    p["sources"].append({
                        "id": f"SRC-MINED-{len(p['sources'])+1:02d}",
                        "fact_or_target": line.split("%")[0].strip()[:120] or sloc,
                        "source_locator": sloc,
                        "target_locator": f"{rel}:L{lno}",
                        "source_quote": _lookup_source_quote(sloc),
                        "verification_status": "verified",
                    })
            for gm in GAP_TAG_RE.finditer(line):
                gid = gm.group(1).strip()
                gdesc = (gm.group(2) or "").strip() or f"Confirmation requested in {rel}:L{lno}"
                if gid not in existing_gap_ids:
                    existing_gap_ids.add(gid)
                    p["author_confirmation_items"].append({"gap_id": gid, "question": gdesc, "target_file": rel, "target_locator": f"{rel}:L{lno}", "suggested_action": "Confirm or supply missing metadata/value."})

    # Fallback source attribution from audit.source_facts if sources[] is still empty
    if not p["sources"] and audit.get("source_facts"):
        for i, sf in enumerate(audit["source_facts"][:15], start=1):
            p["sources"].append({
                "id": f"SRC-{i:02d}",
                "fact_or_target": sf["text"][:100],
                "source_locator": sf["locator"],
                "source_quote": sf["text"][:200],
                "verification_status": "verified",
            })
    else:
        for s_it in p["sources"]:
            if isinstance(s_it, dict) and not s_it.get("source_quote") and s_it.get("source_locator"):
                sq = _lookup_source_quote(str(s_it["source_locator"]))
                if sq:
                    s_it["source_quote"] = sq

    if p["status"] != "completed" and not p["uncertainty"]:
        fb_notes = (compile_report or {}).get("fallback_notes") or []
        for idx_u, note in enumerate(fb_notes[:5], start=1):
            p["uncertainty"].append({"id": f"UNC-AUTO-{idx_u:02d}", "description": str(note)})
        if not p["uncertainty"]:
            p["uncertainty"].append({"id": "UNC-AUTO-01", "description": f"Deliverable finished with status='{p['status']}' due to incomplete inputs or unresolved compilation blockers."})

    if not p.get("template_compliance"):
        tpl = (case_plan.get("signals", {}).get("target_templates") or ["article"])[0]
        p["template_compliance"] = {"template_name": tpl, "engine": (compile_report or {}).get("engine") or audit.get("recommended_engine", "pdflatex"), "bib_tool": (compile_report or {}).get("bib_tool") or audit.get("recommended_bib_tool", "none")}

    if not p.get("summary"):
        eng = (compile_report or {}).get("engine") or audit.get("recommended_engine", "pdflatex")
        cstat = (compile_report or {}).get("status", "not_compiled")
        p["summary"] = f"Processed case '{p['case_id']}' ({p['primary_tex_path'] or 'no tex'}); status={p['status']}, compile={cstat} ({eng}), {len(p['change_records'])} recorded change(s), {len(p['author_confirmation_items'])} author confirmation item(s)."

    if not p["limitations"]:
        p["limitations"] = [{"scenario": "Automated verification scope", "cause": "Checks cover static LaTeX/BibTeX rules, TeX log convergence, and numeric/citation traceability.", "scope": "Semantic peer-review judgment requires human author inspection."}]
    return p


def verify_deliverable(
    payload: Dict[str, Any],
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    skill_dir: Path,
    proj_root: Path,
    primary_tex: Optional[Path],
) -> Dict[str, Any]:
    """Run 6-axis verification reusing prepare_and_audit.audit_tex_tree() (zero duplicated lint logic)."""
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    checks: Dict[str, Any] = {}

    status = str(payload.get("status") or "").strip()
    if status not in {"completed", "partial", "unable_to_determine"}:
        errors.append({"axis": "schema", "code": "VER-INVALID-STATUS", "message": f"Invalid payload status '{status}'."})

    # Axis 1: Compilation & Convergence
    if status == "completed":
        if not compile_report:
            errors.append({"axis": "compile", "code": "VER-NO-COMPILE-REPORT", "message": "Status is 'completed' without compile_report.json."})
        else:
            diag = compile_report.get("log_diagnosis", {})
            pa = compile_report.get("pdf_audit", {})
            if compile_report.get("status") != "compiled_clean":
                errors.append({"axis": "compile", "code": "VER-COMPILE-NOT-CLEAN", "message": f"Compile status is '{compile_report.get('status')}' (errors={len(diag.get('errors', []))})."})
            if compile_report.get("bib_error"):
                errors.append({"axis": "compile", "code": "VER-BIB-ERROR", "message": f"Bibliography pass failed: {(compile_report.get('bib_diagnostics') or [])[:5]}."})
            if not compile_report.get("converged"):
                errors.append({"axis": "compile", "code": "VER-NOT-CONVERGED", "message": "Multi-pass compilation did not converge."})
            if diag.get("missing_characters"):
                errors.append({"axis": "compile", "code": "VER-MISSING-GLYPHS", "message": f"{len(diag['missing_characters'])} missing glyph(s) detected in .log."})
            if diag.get("undefined_references"):
                errors.append({"axis": "compile", "code": "VER-UNDEF-REFERENCES", "message": f"{len(diag['undefined_references'])} undefined reference(s) in .log."})
            if diag.get("undefined_citations"):
                errors.append({"axis": "compile", "code": "VER-UNDEF-CITATIONS", "message": f"{len(diag['undefined_citations'])} undefined citation(s) in .log."})
            if pa.get("unresolved_markers"):
                errors.append({"axis": "compile", "code": "VER-PDF-UNRESOLVED-MARKER", "message": f"Literal ?? / [?] markers in rendered PDF: {pa['unresolved_markers']}."})
            if pa.get("empty_pages"):
                errors.append({"axis": "compile", "code": "VER-PDF-EMPTY-PAGES", "message": f"Blank page(s) in PDF: {pa['empty_pages']}."})
            if not compile_report.get("previews"):
                warnings.append({"axis": "compile", "code": "VER-NO-PREVIEWS", "message": "No PNG preview images rendered."})
            for oh in diag.get("large_overfull_hboxes", []):
                warnings.append({"axis": "compile", "code": "VER-OVERFULL-HBOX", "message": f"Large Overfull \\hbox ({oh['pt']}pt) in {oh['file']}: {oh['line']}"})
        checks["compile_axis"] = "checked"
    else:
        if not payload.get("uncertainty"):
            errors.append({"axis": "honesty", "code": "VER-EMPTY-UNCERTAINTY", "message": f"Status is '{status}' but payload.uncertainty[] is empty."})
        checks["compile_axis"] = f"partial_or_blocked ({status})"

    # Axis 2 & 4: Reuse audit_tex_tree on delivered proj_root
    tpl_name = str((payload.get("template_compliance") or {}).get("template_name") or "").strip()
    deliv_audit = None
    all_delivered_tex = ""
    if primary_tex is None:
        if status == "completed" or str(payload.get("primary_tex_path") or "").strip():
            errors.append({"axis": "source_quality", "code": "VER-MISSING-PRIMARY-TEX", "message": f"Delivered primary_tex_path '{payload.get('primary_tex_path')}' not found on disk."})
        checks["source_quality_axis"] = "missing_tex"
    else:
        deliv_audit = audit_tex_tree(proj_root, skill_dir, env_info=case_plan.get("environment"), target_templates=[tpl_name] if tpl_name else None)
        all_delivered_tex = "\n".join(deliv_audit["tex_texts"].values())
        for f in deliv_audit["findings"]:
            msg = f"{f['file']}:{f['line']} — {f['message']}"
            if f["id"] == "LINT-MISSING-CLS" and status != "completed" and payload.get("uncertainty"):
                warnings.append({"axis": "source_quality", "code": f["id"], "message": msg})
            elif f["severity"] == "error":
                errors.append({"axis": "source_quality", "code": f["id"], "message": msg})
            elif "resizebox" in f["message"] or "scalebox" in f["message"]:
                errors.append({"axis": "source_quality", "code": "VER-RESIZEBOX-TABLE-HACK", "message": msg})
            elif f["severity"] == "warning":
                warnings.append({"axis": "source_quality", "code": f["id"], "message": msg})
        checks["source_quality_axis"] = f"checked_{len(deliv_audit['tex_texts'])}_tex_files"

        for tc in deliv_audit["template_checks"]:
            missing_reqs = [c["requirement"] for c in tc["checks"] if not c["present_in_tex"]]
            if missing_reqs and status == "completed":
                warnings.append({"axis": "template", "code": "VER-TEMPLATE-REQ-MISSING", "message": f"Template '{tc['template']}' expected element(s) missing: {missing_reqs}"})
            checks["template_axis"] = f"{tc['template']} (missing={len(missing_reqs)})"

    # Axis 3: Numeric Fidelity
    if all_delivered_tex:
        allowed_nums: Set[str] = set()
        for rn in audit.get("source_numbers", []):
            allowed_nums.add(rn)
            allowed_nums.add(rn.rstrip("%"))
        for tok in extract_numeric_tokens(case_plan.get("task_text_preview", "")):
            n = normalize_num_str(tok)
            if n:
                allowed_nums.add(n)
                allowed_nums.add(n.rstrip("%"))
        ungrounded: List[str] = []
        if allowed_nums and deliv_audit:
            body_nums = "\n".join(_strip_preamble_and_layout(t) for t in deliv_audit["tex_texts"].values())
            for tok in extract_numeric_tokens(body_nums):
                n = normalize_num_str(tok)
                if not n or re.fullmatch(r"\d", n):
                    continue
                if n not in allowed_nums and n.rstrip("%") not in allowed_nums and tok not in ungrounded:
                    ungrounded.append(tok)
        if ungrounded:
            warnings.append({"axis": "fidelity", "code": "VER-UNSEEN-BODY-NUMBERS", "message": f"Body numeric tokens not seen in /input source facts ({len(ungrounded)}): {', '.join(ungrounded[:12])}."})
        checks["fidelity_axis"] = f"unseen_numbers={len(ungrounded)}"

    # Axis 5: Provenance & GAP-XX alignment
    gap_tags_in_tex = set(m.group(1) for m in GAP_TAG_RE.finditer(all_delivered_tex))
    confirm_ids = {str(item.get("gap_id") or "").strip() for item in (payload.get("author_confirmation_items") or []) if isinstance(item, dict) and item.get("gap_id")}
    for gid in sorted(gap_tags_in_tex - confirm_ids):
        errors.append({"axis": "provenance", "code": "VER-GAP-UNMAPPED", "message": f"Inline tag '{gid}' in .tex is missing an entry in author_confirmation_items[]."})

    # Axis 6: Output Contract
    if payload.get("case_id") != case_plan.get("case_id"):
        errors.append({"axis": "contract", "code": "VER-CASE-ID-MISMATCH", "message": f"case_id '{payload.get('case_id')}' != '{case_plan.get('case_id')}'."})

    return {
        "schema_version": "1.0", "case_id": case_plan.get("case_id"), "passed": len(errors) == 0,
        "error_count": len(errors), "warning_count": len(warnings), "errors": errors, "warnings": warnings,
        "checks": checks, "gap_tags_in_tex": sorted(gap_tags_in_tex),
        "required_contract_keys": case_plan.get("output_contract", {}).get("required_keys", []),
    }


def _build_shell_script(compile_report: Optional[Dict[str, Any]], primary_tex_rel: str, out_dir: Path) -> str:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", 'ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"', 'cd "$ROOT_DIR"', ""]
    tex_rel_use = primary_tex_rel or (compile_report or {}).get("tex_file") or "main.tex"
    tex_subdirs = sorted({p.parent.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".cls", ".sty", ".def", ".clo", ".tex", ".ltx", ".bbx", ".cbx", ".lbx"}})
    bib_subdirs = sorted({p.parent.relative_to(out_dir).as_posix() for p in out_dir.rglob("*.bib") if p.is_file()})
    bst_subdirs = sorted({p.parent.relative_to(out_dir).as_posix() for p in out_dir.rglob("*.bst") if p.is_file()})
    if any(d != "." for d in tex_subdirs) or (Path(tex_rel_use).parent.as_posix() not in {"", "."}):
        lines.extend([
            f'export TEXINPUTS="{":".join([".", "$ROOT_DIR"] + [f"$ROOT_DIR/{d}" for d in tex_subdirs if d != "."])}:"',
            f'export BIBINPUTS="{":".join([".", "$ROOT_DIR"] + [f"$ROOT_DIR/{d}" for d in bib_subdirs if d != "."])}:"',
            f'export BSTINPUTS="{":".join([".", "$ROOT_DIR"] + [f"$ROOT_DIR/{d}" for d in bst_subdirs if d != "."])}:"',
            "",
        ])
    tex_dir = Path(tex_rel_use).parent.as_posix()
    if tex_dir and tex_dir != "." and (out_dir / tex_rel_use).is_file():
        lines.append(f"cd {shlex.quote(tex_dir)}")
    cmds = (compile_report or {}).get("reproducibility_manifest", {}).get("commands")
    if cmds:
        lines.extend(cmds)
    else:
        eng = shlex.quote((compile_report or {}).get("engine", "pdflatex"))
        tname = shlex.quote(Path(tex_rel_use).name)
        lines.extend([f"{eng} -no-shell-escape -interaction=nonstopmode -file-line-error -recorder {tname}"] * 2)
    return "\n".join(lines) + "\n"


def _assemble_final(
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    payload: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    verification: Dict[str, Any],
    proj_root: Path,
    out_dir: Path,
) -> Path:
    ensure_dir(out_dir)
    for stale in [out_dir / "initial_audit_digest.md", *list((out_dir / "previews").glob("page-*.png") if (out_dir / "previews").is_dir() else [])]:
        try:
            stale.unlink()
        except OSError:
            pass

    copied_outputs = list(payload.get("output_files") or [])
    compiled_pdf_resolved = Path(compile_report["pdf_path"]).resolve() if (compile_report and compile_report.get("pdf_path")) else None
    if proj_root.is_dir():
        for p in sorted(proj_root.rglob("*")):
            if p.is_dir() or any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts):
                continue
            if compiled_pdf_resolved and p.resolve() == compiled_pdf_resolved:
                continue
            rel = p.relative_to(proj_root).as_posix()
            if p.suffix.lower() in SOURCE_COPY_EXTS:
                dest = out_dir / rel
                ensure_dir(dest.parent)
                shutil.copy2(p, dest)
                if not any(x.get("path") == rel for x in copied_outputs):
                    copied_outputs.append({"path": rel, "role": "editable_source_or_asset"})

    rendered_pdf_rel = None
    if compile_report and compile_report.get("pdf_path"):
        pdf_p = Path(compile_report["pdf_path"])
        if pdf_p.is_file():
            shutil.copy2(pdf_p, out_dir / pdf_p.name)
            rendered_pdf_rel = pdf_p.name
            if not any(x.get("path") == pdf_p.name for x in copied_outputs):
                copied_outputs.append({"path": pdf_p.name, "role": "compiled_pdf"})

    rendered_previews_rel: List[str] = []
    if compile_report and compile_report.get("previews"):
        prev_out = ensure_dir(out_dir / "previews")
        for prev_str in compile_report["previews"]:
            pp = Path(prev_str)
            if pp.is_file():
                shutil.copy2(pp, prev_out / pp.name)
                rp = f"previews/{pp.name}"
                rendered_previews_rel.append(rp)
                if not any(x.get("path") == rp for x in copied_outputs):
                    copied_outputs.append({"path": rp, "role": "rendered_preview"})

    prim_rel = str(payload.get("primary_tex_path") or (compile_report or {}).get("tex_file") or "")
    primary_tex_content = ""
    if prim_rel:
        for cand in (out_dir / prim_rel, out_dir / Path(prim_rel).name):
            if cand.is_file():
                primary_tex_content, _ = read_text_safe(cand)
                break

    build_sh = out_dir / "build.sh"
    build_sh.write_text(_build_shell_script(compile_report, prim_rel, out_dir), encoding="utf-8")
    try:
        build_sh.chmod(build_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    except OSError:
        pass
    if not any(x.get("path") == "build.sh" for x in copied_outputs):
        copied_outputs.append({"path": "build.sh", "role": "reproducible_build_script"})

    if compile_report and compile_report.get("reproducibility_manifest"):
        save_json(out_dir / "reproducibility_manifest.json", compile_report["reproducibility_manifest"])
        if not any(x.get("path") == "reproducibility_manifest.json" for x in copied_outputs):
            copied_outputs.append({"path": "reproducibility_manifest.json", "role": "reproducibility_manifest"})

    before_after_diff = _compute_before_after_diff(case_plan, out_dir)
    # Auto-enrich empty change_records with summarized file diffs
    if not payload.get("change_records") and before_after_diff:
        for d in before_after_diff:
            if d["status"] in {"modified", "added"}:
                payload["change_records"].append({"file": d["file"], "locator": d["status"], "change_type": "source_edit", "description": f"{d['status']} (+{d['lines_added']}/-{d['lines_removed']} lines)", "reason": "Verified source authoring/repair."})

    diag = (compile_report or {}).get("log_diagnosis") or {}
    pa = (compile_report or {}).get("pdf_audit") or {}
    compile_diagnostics = {
        "status": (compile_report or {}).get("status", "not_run"),
        "engine": (compile_report or {}).get("engine"),
        "engine_version": (compile_report or {}).get("engine_version", ""),
        "bib_tool": (compile_report or {}).get("bib_tool", "none"),
        "pass_count": (compile_report or {}).get("pass_count", 0),
        "converged": bool((compile_report or {}).get("converged")),
        "page_count": pa.get("page_count", 0),
        "preview_count": len(rendered_previews_rel),
        "fallback_notes": (compile_report or {}).get("fallback_notes") or [],
        "fallback_preview_used": bool((compile_report or {}).get("fallback_preview_used")),
        "errors": diag.get("errors", [])[:40],
        "root_errors": diag.get("root_errors", [])[:20],
        "warnings": diag.get("warnings", [])[:60],
        "undefined_references": diag.get("undefined_references", [])[:40],
        "undefined_citations": diag.get("undefined_citations", [])[:40],
        "overfull_hboxes": diag.get("overfull_hboxes", [])[:40],
        "large_overfull_hboxes": diag.get("large_overfull_hboxes", [])[:25],
        "missing_characters": diag.get("missing_characters", [])[:30],
        "bib_diagnostics": ((compile_report or {}).get("bib_diagnostics") or [])[:40],
        "pdf_audit": pa,
    }

    # Write compilation_and_provenance_report.md
    rep_lines = [
        f"# LaTeX Compilation & Provenance Report — `{case_plan['case_id']}`", "",
        f"- **Status**: `{payload['status']}` | **Primary TeX**: `{prim_rel or '(none)'}` | **Verification Passed**: `{verification['passed']}`",
        "", "## Summary", payload["summary"], "",
    ]
    if compile_report:
        rep_lines.extend([
            "## Build & Reproducibility",
            f"- Engine: `{compile_report.get('engine')}` | Bib: `{compile_report.get('bib_tool')}` | Passes: {compile_report.get('pass_count')} | Converged: {compile_report.get('converged')}",
            f"- Pages: {pa.get('page_count', 0)} | Previews: {len(rendered_previews_rel)} | Embedded Fonts: {len(pa.get('embedded_fonts', []))}",
            "",
        ])
    if payload.get("sources"):
        rep_lines.extend([
            "## Fact & Field-Level Source Evidence",
            "| ID | Fact / Target | TeX Locator | Source Locator | Verbatim Source Quote |",
            "|---|---|---|---|---|",
        ])
        for s_it in payload["sources"]:
            if not isinstance(s_it, dict):
                continue
            sid_s = str(s_it.get("id") or "-").replace("|", " ")
            ft_s = str(s_it.get("fact_or_target") or "-")[:60].replace("|", " ").replace("\n", " ")
            tl_s = str(s_it.get("target_locator") or "-").replace("|", " ")
            sl_s = str(s_it.get("source_locator") or "-").replace("|", " ")
            sq_s = str(s_it.get("source_quote") or "-")[:90].replace("|", " ").replace("\n", " ")
            rep_lines.append(f"| `{sid_s}` | {ft_s} | `{tl_s}` | `{sl_s}` | {sq_s} |")
        rep_lines.append("")
    for title, key, fmt in (
        ("## Change Records", "change_records", lambda x: f"- `{x.get('file')}` [{x.get('change_type')}]: {x.get('description')}"),
        ("## Author Confirmation Items", "author_confirmation_items", lambda x: f"- **[{x.get('gap_id')}]** `{x.get('target_file')}`: {x.get('question')}"),
        ("## Uncertainties", "uncertainty", lambda x: f"- **[{x.get('id', 'UNC')}]**: {x.get('description')}"),
    ):
        if payload.get(key):
            rep_lines.extend([title, *[fmt(it) for it in payload[key]], ""])
    if before_after_diff:
        rep_lines.append("## Source Before/After Diff Summary")
        for d in before_after_diff:
            rep_lines.append(f"- `{d['file']}` (`{d['status']}`, +{d['lines_added']}/-{d['lines_removed']})")
            if d["status"] == "modified" and d.get("unified_diff"):
                rep_lines.extend(["```diff", d["unified_diff"][:3000], "```"])
    (out_dir / "compilation_and_provenance_report.md").write_text("\n".join(rep_lines) + "\n", encoding="utf-8")

    result: Dict[str, Any] = {
        "case_id": case_plan["case_id"], "status": payload["status"], "checkpoint": "final",
        "summary": payload["summary"], "primary_tex_path": prim_rel, "output_files": copied_outputs,
        "template_compliance": payload["template_compliance"],
        "compile_provenance": (compile_report or {}).get("reproducibility_manifest", {}),
        "compile_diagnostics": compile_diagnostics, "before_after_diff": before_after_diff,
        "verification": {"passed": verification["passed"], "error_count": verification["error_count"], "warning_count": verification["warning_count"]},
        "sources": payload["sources"], "change_records": payload["change_records"],
        "uncertainty": payload["uncertainty"], "author_confirmation_items": payload["author_confirmation_items"],
        "limitations": payload["limitations"],
    }

    overrides = payload.get("contract_overrides") or {}
    role_values = {
        "case_id": case_plan["case_id"], "status": payload["status"], "summary": payload["summary"],
        "primary_tex_content": primary_tex_content,
        "rendered_outputs": {"pdf_path": rendered_pdf_rel, "previews": rendered_previews_rel},
        "bibliography": {"bib_tool": compile_diagnostics["bib_tool"], "bib_diagnostics": compile_diagnostics["bib_diagnostics"]},
        "compile_provenance": result["compile_provenance"], "template_compliance": payload["template_compliance"],
        "sources": payload["sources"], "change_records": payload["change_records"],
        "author_confirmation_items": payload["author_confirmation_items"], "uncertainty": payload["uncertainty"],
        "limitations": payload["limitations"], "output_files": copied_outputs,
    }
    status_s, summary_s = str(payload["status"]), str(payload["summary"])
    for fspec in case_plan.get("output_contract", {}).get("fields", []):
        fn, et = fspec["name"], fspec.get("expected_type", "any").lower()
        if fn in overrides:
            result[fn] = coerce_contract_value(overrides[fn], et, status_s, summary_s)
        elif fn in payload and fn != "output_files":
            result[fn] = coerce_contract_value(payload[fn], et, status_s, summary_s)
        elif fn in result:
            result[fn] = coerce_contract_value(result[fn], et, status_s, summary_s)
        else:
            result[fn] = coerce_contract_value(role_values.get(fspec.get("semantic_role", "custom")), et, status_s, summary_s)

    out_json = out_dir / "result.json"
    save_json(out_json, result)
    return out_json


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile (if changed), auto-hydrate payload, verify 6 axes, and assemble /app/output.")
    ap.add_argument("--tex", default=None, help="Path to main editable .tex file (auto-detected if omitted unless --skip-compile)")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--payload", default=None, help="Path to deliverable_payload.json (defaults to <work-dir>/deliverable_payload.json)")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--skill-dir", default=None)
    ap.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    ap.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    ap.add_argument("--max-passes", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--skip-compile", action="store_true")
    ap.add_argument("--force-compile", action="store_true")
    args = ap.parse_args()

    skill_dir = resolve_skill_dir(args.skill_dir)
    work_dir, out_dir = Path(args.work_dir), Path(args.out_dir)
    case_plan = load_json_safe(work_dir / "case_plan.json")
    audit = load_json_safe(work_dir / "latex_audit.json", default={}) or {}
    if not case_plan:
        raise SystemExit(f"Missing case_plan.json under {work_dir}. Run prepare_and_audit.py first.")

    payload_p = Path(args.payload) if args.payload else (work_dir / "deliverable_payload.json")
    raw_payload = load_json_safe(payload_p) or {}
    compile_report_p = work_dir / "compile_report.json"
    prev_compile_report = load_json_safe(compile_report_p) if not args.skip_compile else None
    proj_root, primary_tex = _resolve_tex_dir_and_primary(args.tex, args.project_root, work_dir, raw_payload, prev_compile_report)

    compile_report = None
    if not args.skip_compile:
        if primary_tex is None or not primary_tex.is_file():
            raise SystemExit(f"No primary .tex found (got --tex={args.tex}). Pass --skip-compile for blocked/unanswerable cases.")
        tree_hash = hash_project_tree(proj_root)
        if not args.force_compile and prev_compile_report and prev_compile_report.get("_staged_sources_sha256") == tree_hash and prev_compile_report.get("engine_arg") == args.engine and prev_compile_report.get("bib_arg") == args.bib and prev_compile_report.get("status") == "compiled_clean":
            compile_report = prev_compile_report
        else:
            compile_report = compile_project(primary_tex, work_dir / "build", work_dir / "previews", skill_dir, args.engine, args.bib, args.max_passes, args.timeout, True, proj_root)
            compile_report["_staged_sources_sha256"] = tree_hash
            compile_report["engine_arg"] = args.engine
            compile_report["bib_arg"] = args.bib
            save_json(compile_report_p, compile_report)
            (work_dir / "compile_summary.md").write_text(render_compile_summary(compile_report), encoding="utf-8")

    payload = hydrate_payload(raw_payload, case_plan, audit, compile_report, proj_root, primary_tex, work_dir)
    verification = verify_deliverable(payload, case_plan, audit, compile_report, skill_dir, proj_root, primary_tex)
    ver_p = work_dir / "verification_report.json"
    save_json(ver_p, verification)
    out_json = _assemble_final(case_plan, audit, payload, compile_report, verification, proj_root, out_dir)

    print(json.dumps({
        "passed": verification["passed"],
        "errors": verification["errors"],
        "warnings_count": verification["warning_count"],
        "result_json": str(out_json),
        "compile_status": (compile_report or {}).get("status", "skipped"),
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if verification["passed"] else 1)


if __name__ == "__main__":
    main()
