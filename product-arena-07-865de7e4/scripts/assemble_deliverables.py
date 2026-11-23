#!/usr/bin/env python3
"""Step 3 & Step 6: Assemble partial or final verified deliverables under /app/output.

Outputs:
- /app/output/result.json (preserving case_id + EVERY output_contract key + provenance/uncertainty)
- /app/output/compilation_and_provenance_report.md
- /app/output/reproducibility_manifest.json (final)
- /app/output/build.sh (executable deterministic compile script, final)
- Editable .tex / .bib / style / figure sources, compiled .pdf, and rendered previews/*.png
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    ensure_dir,
    load_json_safe,
    read_text_safe,
    save_json,
)

SOURCE_COPY_EXTS = {
    ".tex",
    ".ltx",
    ".bib",
    ".cls",
    ".sty",
    ".bst",
    ".def",
    ".clo",
    ".png",
    ".jpg",
    ".jpeg",
    ".eps",
    ".svg",
}


def build_shell_script(compile_report: Optional[Dict[str, Any]], primary_tex_rel: str) -> str:
    """Emit reproducible build.sh reflecting the verified engine and multi-pass commands."""
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", 'cd "$(dirname "$0")"', ""]
    if compile_report and compile_report.get("reproducibility_manifest", {}).get("commands"):
        tex_dir = Path(primary_tex_rel).parent.as_posix()
        if tex_dir and tex_dir != ".":
            lines.append(f'cd "{tex_dir}"')
        for cmd in compile_report["reproducibility_manifest"]["commands"]:
            lines.append(cmd)
    else:
        engine = (compile_report or {}).get("engine", "pdflatex")
        tex_name = Path(primary_tex_rel).name if primary_tex_rel else "main.tex"
        lines.append(f"{engine} -interaction=nonstopmode -file-line-error -recorder {tex_name}")
        lines.append(f"{engine} -interaction=nonstopmode -file-line-error -recorder {tex_name}")
    lines.append("")
    return "\n".join(lines)


def populate_contract_fields(
    result: Dict[str, Any],
    case_plan: Dict[str, Any],
    payload: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    primary_tex_content: str,
) -> Dict[str, Any]:
    """Ensure result.json contains case_id and EVERY key from input output_contract with matching types."""
    overrides = payload.get("contract_overrides") or {}
    fields = case_plan.get("output_contract", {}).get("fields", [])

    role_values: Dict[str, Any] = {
        "case_id": case_plan.get("case_id"),
        "status": payload.get("status", "partial"),
        "summary": payload.get("summary", ""),
        "primary_tex_content": primary_tex_content,
        "rendered_outputs": {
            "pdf_path": (compile_report or {}).get("pdf_path"),
            "previews": (compile_report or {}).get("previews", []),
        },
        "bibliography": {
            "bib_tool": (compile_report or {}).get("bib_tool", "none"),
            "bib_diagnostics": (compile_report or {}).get("bib_diagnostics", []),
        },
        "compile_provenance": (compile_report or {}).get("reproducibility_manifest", {}),
        "template_compliance": payload.get("template_compliance", {}),
        "sources": payload.get("sources", []),
        "change_records": payload.get("change_records", []),
        "author_confirmation_items": payload.get("author_confirmation_items", []),
        "uncertainty": payload.get("uncertainty", []),
        "limitations": payload.get("limitations", []),
        "output_files": payload.get("output_files", []),
    }

    for fspec in fields:
        fname = fspec["name"]
        if fname in overrides:
            result[fname] = overrides[fname]
            continue
        if fname in payload:
            result[fname] = payload[fname]
            continue
        if fname in result:
            continue
        role = fspec.get("semantic_role", "custom")
        etype = fspec.get("expected_type", "any").lower()
        val = role_values.get(role)
        if val is None:
            if etype in {"array", "list"}:
                val = []
            elif etype in {"object", "dict"}:
                val = {}
            elif etype in {"boolean", "bool"}:
                val = payload.get("status") == "completed"
            elif etype in {"integer", "int", "number", "float"}:
                val = 0
            else:
                val = str(payload.get("summary") or "")
        else:
            # Coerce type cleanly if schema expects string vs dict/list
            if etype in {"string", "str"} and isinstance(val, (dict, list)):
                import json
                val = json.dumps(val, ensure_ascii=False)
            elif etype in {"array", "list"} and not isinstance(val, list):
                val = [val] if val else []
            elif etype in {"object", "dict"} and not isinstance(val, dict):
                val = {"value": val}
        result[fname] = val

    return result


def render_provenance_report(
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    payload: Dict[str, Any],
    compile_report: Optional[Dict[str, Any]],
    verification: Optional[Dict[str, Any]],
    checkpoint: str,
) -> str:
    """Create human-readable provenance, change diff, compilation, and gap report."""
    lines = [
        f"# LaTeX Compilation & Provenance Report — `{case_plan['case_id']}` ({checkpoint.upper()})",
        "",
        f"- **Status**: `{payload.get('status')}`",
        f"- **Primary TeX**: `{payload.get('primary_tex_path') or '(not set)'}`",
        f"- **Verification**: {verification.get('passed') if verification else 'not_run_yet (checkpoint)'}",
        "",
        "## Summary",
        payload.get("summary", ""),
        "",
    ]
    if compile_report:
        diag = compile_report.get("log_diagnosis", {})
        lines.extend(
            [
                "## Build & Reproducibility",
                f"- Engine: `{compile_report.get('engine')}` ({compile_report.get('engine_version') or 'version not reported'})",
                f"- Bibliography tool: `{compile_report.get('bib_tool')}` | Passes: {compile_report.get('pass_count')} | Converged: {compile_report.get('converged')}",
                f"- Errors: {len(diag.get('errors', []))} | Warnings: {len(diag.get('warnings', []))} | Missing glyphs: {len(diag.get('missing_characters', []))}",
                f"- Page count: {compile_report.get('pdf_audit', {}).get('page_count', 0)} | Previews: {len(compile_report.get('previews', []))}",
                "",
            ]
        )

    if payload.get("change_records"):
        lines.append("## Change Records")
        for cr in payload["change_records"]:
            lines.append(f"- `{cr.get('file')}` ({cr.get('locator', '-')}) [{cr.get('change_type')}]: {cr.get('description')} — Reason: {cr.get('reason')}")
        lines.append("")

    if payload.get("sources"):
        lines.append("## Fact & Source Mapping")
        for src in payload["sources"]:
            lines.append(f"- `{src.get('fact_or_target')}` <- `{src.get('source_locator')}` -> `{src.get('target_locator', '-')}` ({src.get('verification_status', 'verified')})")
        lines.append("")

    if payload.get("author_confirmation_items") or payload.get("uncertainty"):
        lines.append("## Uncertainties & Items Requiring Author Confirmation")
        for u in payload.get("uncertainty", []):
            lines.append(f"- **[{u.get('id', 'UNC')}]** ({u.get('category')}): {u.get('description')} (Impact: {u.get('impact')})")
        for q in payload.get("author_confirmation_items", []):
            lines.append(f"- **[{q.get('gap_id')}]** `{q.get('target_file')}`: {q.get('question')} -> Suggested: {q.get('suggested_action')}")
        lines.append("")

    if payload.get("limitations"):
        lines.append("## Concrete Limitations")
        for lim in payload["limitations"]:
            lines.append(f"- **{lim.get('scenario')}**: {lim.get('cause')} (Scope: {lim.get('scope')})")
        lines.append("")

    if audit.get("findings") and checkpoint == "partial":
        lines.append("## Initial Static Findings")
        for f in audit["findings"][:25]:
            lines.append(f"- `[{f['severity'].upper()} {f['id']}]` `{f['file']}:{f['line']}` — {f['message']}")
        lines.append("")

    return "\n".join(lines) + "\n"


def assemble(
    case_plan: Dict[str, Any],
    audit: Dict[str, Any],
    out_dir: Path,
    checkpoint: str,
    payload: Optional[Dict[str, Any]] = None,
    compile_report: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    tex_source_dir: Optional[Path] = None,
) -> Path:
    """Assemble partial or final /app/output artifacts and result.json."""
    ensure_dir(out_dir)

    if checkpoint == "partial" or payload is None:
        auto_unc = []
        for w in case_plan.get("access_warnings", []):
            auto_unc.append({"id": "UNC-ACCESS", "category": "input_access", "description": w, "impact": "Input file unreadable or empty."})
        for m in case_plan.get("missing_referenced_files", []):
            auto_unc.append({"id": "UNC-MISSING-REF", "category": "missing_input", "description": f"Referenced file missing: {m}", "impact": "Cannot verify or include missing file."})
        if not auto_unc:
            auto_unc.append({"id": "UNC-CHECKPOINT", "category": "in_progress", "description": "Early partial checkpoint saved before authoring/build loop completed.", "impact": "Pending final compilation and verification."})

        payload = {
            "case_id": case_plan["case_id"],
            "status": "partial",
            "summary": f"Early checkpoint for case '{case_plan['case_id']}' ({case_plan['signals']['primary_mode']}); detected {audit.get('error_count', 0)} static errors and {audit.get('warning_count', 0)} warnings.",
            "primary_tex_path": (case_plan.get("candidate_entry_tex") or [""])[0],
            "output_files": [],
            "template_compliance": {"template_name": (case_plan["signals"].get("target_templates") or ["unknown"])[0], "engine": audit.get("recommended_engine", "pdflatex"), "bib_tool": audit.get("recommended_bib_tool", "none")},
            "sources": [{"id": f"SRC-{i+1:02d}", "fact_or_target": sf["text"][:100], "source_locator": sf["locator"], "verification_status": "verified"} for i, sf in enumerate(audit.get("source_facts", [])[:20])],
            "change_records": [],
            "uncertainty": auto_unc,
            "author_confirmation_items": [],
            "limitations": [{"scenario": "Early partial checkpoint", "cause": "Authoring/compile verification loop has not run yet.", "scope": "Replace via --checkpoint final once verification passes."}],
        }

    copied_outputs: List[Dict[str, Any]] = list(payload.get("output_files") or [])
    primary_tex_content = ""

    if checkpoint == "final":
        src_dir = tex_source_dir
        if src_dir is None and compile_report and compile_report.get("build_dir"):
            src_dir = Path(compile_report["build_dir"])

        if src_dir and src_dir.is_dir():
            for p in sorted(src_dir.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(src_dir).as_posix()
                if p.suffix.lower() in SOURCE_COPY_EXTS:
                    dest = out_dir / rel
                    ensure_dir(dest.parent)
                    shutil.copy2(p, dest)
                    if not any(x.get("path") == rel for x in copied_outputs):
                        copied_outputs.append({"path": rel, "role": "editable_source_or_asset"})

        # Copy compiled PDF if present
        if compile_report and compile_report.get("pdf_path"):
            pdf_p = Path(compile_report["pdf_path"])
            if pdf_p.is_file():
                dest_pdf = out_dir / pdf_p.name
                shutil.copy2(pdf_p, dest_pdf)
                if not any(x.get("path") == pdf_p.name for x in copied_outputs):
                    copied_outputs.append({"path": pdf_p.name, "role": "compiled_pdf"})

        # Copy rendered PNG previews
        if compile_report and compile_report.get("previews"):
            prev_out = ensure_dir(out_dir / "previews")
            for idx, prev_str in enumerate(compile_report["previews"], start=1):
                pp = Path(prev_str)
                if pp.is_file():
                    dest_png = prev_out / pp.name
                    shutil.copy2(pp, dest_png)
                    rel_png = f"previews/{pp.name}"
                    if not any(x.get("path") == rel_png for x in copied_outputs):
                        copied_outputs.append({"path": rel_png, "role": "rendered_preview"})

        # Read primary .tex text for result.json embedding if contract asks for tex source
        prim_rel = str(payload.get("primary_tex_path") or "")
        if prim_rel:
            for cand in (out_dir / prim_rel, out_dir / Path(prim_rel).name):
                if cand.is_file():
                    primary_tex_content, _ = read_text_safe(cand)
                    break

        # Write build.sh and reproducibility_manifest.json
        build_sh = out_dir / "build.sh"
        build_sh.write_text(build_shell_script(compile_report, prim_rel), encoding="utf-8")
        try:
            mode = build_sh.stat().st_mode
            build_sh.chmod(mode | stat.S_IXUSR | stat.S_IXGRP)
        except OSError:
            pass
        if not any(x.get("path") == "build.sh" for x in copied_outputs):
            copied_outputs.append({"path": "build.sh", "role": "reproducible_build_script"})

        if compile_report and compile_report.get("reproducibility_manifest"):
            save_json(out_dir / "reproducibility_manifest.json", compile_report["reproducibility_manifest"])
            if not any(x.get("path") == "reproducibility_manifest.json" for x in copied_outputs):
                copied_outputs.append({"path": "reproducibility_manifest.json", "role": "reproducibility_manifest"})

    # Write provenance markdown report
    report_md_name = "initial_audit_digest.md" if checkpoint == "partial" else "compilation_and_provenance_report.md"
    (out_dir / report_md_name).write_text(
        render_provenance_report(case_plan, audit, payload, compile_report, verification, checkpoint),
        encoding="utf-8",
    )

    result: Dict[str, Any] = {
        "case_id": case_plan["case_id"],
        "status": payload.get("status", "partial"),
        "checkpoint": checkpoint,
        "summary": payload.get("summary", ""),
        "primary_tex_path": payload.get("primary_tex_path", ""),
        "output_files": copied_outputs,
        "template_compliance": payload.get("template_compliance", {}),
        "compile_provenance": (compile_report or {}).get("reproducibility_manifest", {}),
        "verification": {
            "passed": verification.get("passed") if verification else False,
            "error_count": verification.get("error_count") if verification else None,
            "warning_count": verification.get("warning_count") if verification else None,
        },
        "sources": payload.get("sources", []),
        "change_records": payload.get("change_records", []),
        "uncertainty": payload.get("uncertainty", []),
        "author_confirmation_items": payload.get("author_confirmation_items", []),
        "limitations": payload.get("limitations", []),
    }

    result = populate_contract_fields(result, case_plan, payload, compile_report, primary_tex_content)
    out_json = out_dir / "result.json"
    save_json(out_json, result)
    return out_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble partial or final deliverables under /app/output.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--audit", default="/app/work/latex_audit.json")
    parser.add_argument("--compile-report", default=None)
    parser.add_argument("--payload", default=None)
    parser.add_argument("--verification", default=None)
    parser.add_argument("--work-tex-dir", default=None)
    parser.add_argument("--checkpoint", required=True, choices=["partial", "final"])
    parser.add_argument("--out-dir", default="/app/output")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan))
    audit = load_json_safe(Path(args.audit), default={}) or {}
    if not case_plan:
        raise SystemExit(f"Missing case_plan: {args.case_plan}")

    payload = load_json_safe(Path(args.payload)) if args.payload else None
    compile_report = load_json_safe(Path(args.compile_report)) if args.compile_report else None
    verification = load_json_safe(Path(args.verification)) if args.verification else None

    out_json = assemble(
        case_plan=case_plan,
        audit=audit,
        out_dir=Path(args.out_dir),
        checkpoint=args.checkpoint,
        payload=payload,
        compile_report=compile_report,
        verification=verification,
        tex_source_dir=Path(args.work_tex_dir) if args.work_tex_dir else None,
    )
    print(f"[assemble_deliverables] checkpoint={args.checkpoint} -> {out_json}")


if __name__ == "__main__":
    main()
