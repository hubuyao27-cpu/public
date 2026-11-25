#!/usr/bin/env python3
"""One-command preparation wrapper for latex-sci-composer.

Runs inspect_case -> stage editable /input sources (including inline case.json fields) into /app/work/src
-> analyze_latex -> assemble_deliverables (--checkpoint partial) in a single command to save Harbor requests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import has_cjk, sanitize_tex_for_scan, save_json

CONTROL_FILES = {"case.json", "task.md"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}


def _run(script: Path, args: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, "-B", str(script)] + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if proc.returncode != 0:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def _copy_tree_resilient(input_dir: Path, dst_dir: Path) -> tuple[list[str], list[str]]:
    """Copy files iteratively catching (OSError, shutil.Error) per file so one unreadable file never crashes staging."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    skipped: list[str] = []
    if not input_dir.is_dir():
        return staged, skipped
    for root, dirs, filenames in os.walk(input_dir, onerror=lambda e: skipped.append(f"walk_error: {e}")):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel_root = Path(root).relative_to(input_dir)
        for fname in sorted(filenames):
            rel = (rel_root / fname).as_posix()
            if rel in CONTROL_FILES:
                continue
            src_f = Path(root) / fname
            dst_f = dst_dir / rel_root / fname
            try:
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_f, dst_f)
                staged.append(rel)
            except (OSError, shutil.Error) as exc:
                skipped.append(f"stage_skipped: {rel} ({exc})")
    return staged, skipped


def _materialize_inline_fields(src_dir: Path, baseline_dir: Path, case_plan: dict) -> list[dict]:
    """Materialize inline case.json tex/bib/draft/references fields into BOTH src_dir and baseline_dir."""
    raw = case_plan.get("case_json_raw") or {}
    created: list[dict] = []

    def _emit(rel_name: str, text: str, category: str, origin_key: str) -> None:
        for target_root in (src_dir, baseline_dir):
            p = target_root / rel_name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        p_src = src_dir / rel_name
        entry = {
            "rel_path": rel_name,
            "abs_path": str(p_src.resolve()),
            "ext": p_src.suffix.lower(),
            "category": category,
            "size_bytes": len(text.encode("utf-8")),
            "readable": True,
            "read_error": None,
            "contains_cjk": has_cjk(text),
            "line_count": len(text.splitlines()),
            "is_entry_tex": False,
            "origin": f"case.json#{origin_key}",
        }
        if category == "tex_source":
            clean = sanitize_tex_for_scan(text)
            has_dc = bool(re.search(r"^\s*\\documentclass\b", clean, re.M))
            has_bd = bool(re.search(r"\\begin\{document\}", clean))
            entry["has_documentclass"] = has_dc
            entry["has_begin_document"] = has_bd
            entry["is_entry_tex"] = has_dc and has_bd
        created.append(entry)

    if not any(src_dir.rglob("*.tex")):
        for k in ("source_tex", "tex", "tex_source", "tex_content", "latex", "latex_source", "latex_content", "main_tex"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                _emit("main.tex", v, "tex_source", k)
                break
    if not any(src_dir.rglob("*.bib")):
        for k in ("bib_text", "bibtex", "bib_content", "bibtex_content"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                _emit("refs.bib", v, "bib_database", k)
                break
    if not any(f.get("category") in {"draft_or_doc", "word_doc", "pdf_doc"} for f in case_plan.get("files", [])):
        for k in ("manuscript", "draft", "draft_text"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                if "\\documentclass" in v or "\\begin{document}" in v:
                    if not any(src_dir.rglob("*.tex")):
                        _emit("main.tex", v, "tex_source", k)
                else:
                    _emit("draft_inline.md", v, "draft_or_doc", k)
                break
    ref_v = raw.get("references")
    if isinstance(ref_v, str) and ref_v.strip() and not any(src_dir.rglob("*.bib")):
        if re.search(r"@\w+\s*[({]", ref_v):
            _emit("refs.bib", ref_v, "bib_database", "references")
        else:
            _emit("references_inline.txt", ref_v, "draft_or_doc", "references")
    return created


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect case, stage editable sources, run static audit, and save partial checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    src_dir = work / "src"
    baseline_dir = work / "baseline_src"
    (work / "build").mkdir(parents=True, exist_ok=True)
    (work / "previews").mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    case_plan_p = work / "case_plan.json"
    audit_p = work / "latex_audit.json"
    digest_p = work / "latex_digest.md"

    o1 = _run(sdir / "inspect_case.py", ["--input-dir", args.input_dir, "--out", str(case_plan_p)])
    case_plan = json.loads(case_plan_p.read_text(encoding="utf-8"))

    staged, skipped = _copy_tree_resilient(Path(args.input_dir), src_dir)
    _copy_tree_resilient(Path(args.input_dir), baseline_dir)
    inline_entries = _materialize_inline_fields(src_dir, baseline_dir, case_plan)

    if skipped:
        case_plan.setdefault("access_warnings", []).extend(skipped)
    case_plan["baseline_dir"] = str(baseline_dir.resolve())
    case_plan["staged_src_dir"] = str(src_dir.resolve())

    if inline_entries:
        existing_rels = {f["rel_path"] for f in case_plan.get("files", [])}
        for ent in inline_entries:
            staged.append(f"{ent['rel_path']} (from {ent['origin']})")
            if ent["rel_path"] not in existing_rels:
                case_plan.setdefault("files", []).append(ent)
            if ent.get("is_entry_tex") and ent["rel_path"] not in case_plan.get("candidate_entry_tex", []):
                case_plan.setdefault("candidate_entry_tex", []).append(ent["rel_path"])
        files_all = case_plan.get("files", [])
        case_plan["stats"] = {
            "total_files": len(files_all),
            "readable_files": sum(1 for f in files_all if f.get("readable")),
            "tex_files": sum(1 for f in files_all if f.get("category") == "tex_source"),
            "bib_files": sum(1 for f in files_all if f.get("category") == "bib_database"),
            "class_or_style_files": sum(1 for f in files_all if f.get("category") == "tex_class_or_style"),
            "non_tex_sources": sum(1 for f in files_all if f.get("category") in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data", "structured_data"}),
            "inline_material_count": len(case_plan.get("inline_material_keys", [])),
        }
    save_json(case_plan_p, case_plan)

    o2 = _run(sdir / "analyze_latex.py", ["--case-plan", str(case_plan_p), "--out-audit", str(audit_p), "--out-digest", str(digest_p)])
    o3 = _run(
        sdir / "assemble_deliverables.py",
        ["--case-plan", str(case_plan_p), "--audit", str(audit_p), "--checkpoint", "partial", "--out-dir", args.out_dir],
    )
    print(json.dumps({
        "status": "ok",
        "inspect": o1,
        "staged_to_work_src": staged,
        "skipped_warnings": skipped,
        "analyze": o2,
        "partial_checkpoint": o3,
        "digest": str(digest_p),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
