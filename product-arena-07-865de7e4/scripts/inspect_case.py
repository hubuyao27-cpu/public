#!/usr/bin/env python3
"""Step 1: Inspect Harbor /input/case.json, task.md, input files, and live TeX/font environment.

Produces /app/work/case_plan.json containing:
- case_id and parsed output_contract schema/keys
- inferred task_mode, language, detected templates, and candidate main .tex entry points
- full categorized inventory under /input with access/permission/empty warnings
- live TeX toolchain probe (pdflatex, xelatex, lualatex, latexmk, bibtex, biber, kpsewhich, pdftoppm, fitz)
- installed CJK system font discovery via fc-list
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    has_cjk,
    load_json_safe,
    parse_output_contract,
    posix_rel,
    read_text_safe,
    run_cmd,
    sanitize_tex_for_scan,
    save_json,
    which_tool,
)

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md"}
EXPECTED_OUTPUT_FILES = {
    "case.json",
    "task.md",
    "result.json",
    "SKILL.md",
    "build.sh",
    "reproducibility_manifest.json",
    "compilation_and_provenance_report.md",
    "initial_audit_digest.md",
    "deliverable_payload.json",
    "compile_report.json",
    "compile_summary.md",
    "verification_report.json",
    "case_plan.json",
    "latex_audit.json",
    "latex_digest.md",
}

CATEGORY_BY_EXT = {
    ".tex": "tex_source",
    ".ltx": "tex_source",
    ".bib": "bib_database",
    ".cls": "tex_class_or_style",
    ".sty": "tex_class_or_style",
    ".bst": "bib_style",
    ".def": "tex_class_or_style",
    ".clo": "tex_class_or_style",
    ".md": "draft_or_doc",
    ".markdown": "draft_or_doc",
    ".txt": "draft_or_doc",
    ".rst": "draft_or_doc",
    ".docx": "word_doc",
    ".pdf": "pdf_doc",
    ".csv": "tabular_data",
    ".tsv": "tabular_data",
    ".json": "structured_data",
    ".yaml": "structured_data",
    ".yml": "structured_data",
    ".log": "tex_log",
    ".blg": "bib_log",
    ".aux": "tex_aux",
    ".png": "figure_asset",
    ".jpg": "figure_asset",
    ".jpeg": "figure_asset",
    ".eps": "figure_asset",
    ".svg": "figure_asset",
}


def probe_tex_environment() -> Dict[str, Any]:
    """Probe installed TeX compilers, bibliography drivers, PDF tools, and CJK fonts."""
    tools = {}
    for t in (
        "pdflatex",
        "xelatex",
        "lualatex",
        "latexmk",
        "bibtex",
        "biber",
        "kpsewhich",
        "chktex",
        "fc-list",
        "pdftoppm",
        "pdffonts",
        "pdftotext",
    ):
        tools[t] = which_tool(t)

    python_libs = {}
    for mod in ("fitz",):
        try:
            try:
                import pymupdf  # noqa: F401
            except ImportError:
                __import__(mod)
            python_libs[mod] = True
        except Exception:
            python_libs[mod] = False

    engine_versions: Dict[str, str] = {}
    for eng in ("pdflatex", "xelatex", "lualatex", "bibtex", "biber"):
        if tools.get(eng):
            res = run_cmd([eng, "--version"], timeout=10)
            first_line = (res.get("stdout") or res.get("stderr") or "").splitlines()[:1]
            if first_line:
                engine_versions[eng] = first_line[0].strip()

    cjk_fonts: List[str] = []
    if tools.get("fc-list"):
        res = run_cmd(["fc-list", ":lang=zh", "family"], timeout=15)
        if res["returncode"] == 0:
            seen = set()
            for raw_line in res["stdout"].splitlines():
                for fam in raw_line.split(","):
                    fam_clean = fam.strip()
                    if fam_clean and fam_clean not in seen:
                        seen.add(fam_clean)
                        cjk_fonts.append(fam_clean)

    # Check common CJK TeX classes/packages and Fandol OTF font availability
    kpse_checks: Dict[str, Optional[str]] = {}
    if tools.get("kpsewhich"):
        for req in ("ctexart.cls", "xeCJK.sty", "FandolSong-Regular.otf", "IEEEtran.cls", "acmart.cls", "elsarticle.cls", "booktabs.sty", "biblatex.sty"):
            res = run_cmd(["kpsewhich", req], timeout=10)
            out = (res.get("stdout") or "").strip()
            kpse_checks[req] = out if (res["returncode"] == 0 and out) else None
    fandol_available = bool(kpse_checks.get("FandolSong-Regular.otf")) or any("fandol" in f.lower() for f in cjk_fonts)

    return {
        "platform": sys.platform,
        "tools": tools,
        "python_libs": python_libs,
        "engine_versions": engine_versions,
        "cjk_fonts": sorted(cjk_fonts),
        "fandol_available": fandol_available,
        "kpse_checks": kpse_checks,
    }


def infer_signals(
    task_text: str,
    case_json: Dict[str, Any],
    files: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Infer task_mode, language, and target templates strictly from task prompt and real files."""
    combined = f"{task_text}\n{case_json.get('prompt', '')}\n{case_json.get('instruction', '')}\n{case_json.get('task', '')}".lower()

    has_tex = any(f["category"] == "tex_source" for f in files)
    has_bib = any(f["category"] == "bib_database" for f in files)
    has_draft = any(f["category"] in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data"} for f in files)

    modes: List[str] = []
    if any(k in combined for k in ("template", "migrate", "ieee", "acm", "elsevier", "elsarticle", "llncs", "springer", "neurips", "icml", "模板", "迁移", "期刊", "会议")):
        modes.append("template_migration")
    if any(k in combined for k in ("compile", "error", "fix", "debug", "repair", "broken", "编译", "报错", "修复", "排错")):
        modes.append("compile_and_repair")
    if any(k in combined for k in ("bib", "citation", "reference", "cite", "引用", "参考文献")):
        modes.append("citation_bib_alignment")
    if any(k in combined for k in ("formula", "equation", "table", "tabular", "booktabs", "float", "公式", "表格", "三线表")):
        modes.append("formula_table_refinement")
    if any(k in combined for k in ("bilingual", "chinese", "cjk", "翻译", "双语", "中文")):
        modes.append("bilingual_latex_writing")
    if any(k in combined for k in ("draft", "write", "author", "convert", "from markdown", "撰写", "生成", "改写")):
        modes.append("draft_to_latex")
    if not modes:
        if has_tex and not has_draft:
            modes.append("compile_and_repair")
        elif has_draft and not has_tex:
            modes.append("draft_to_latex")
        else:
            modes.append("compile_and_repair" if has_tex else "draft_to_latex")

    cjk_in_files = any(f.get("contains_cjk") for f in files)
    explicit_zh_target = any(k in combined for k in ("中文论文", "中文版", "用中文撰写", "中文排版", "chinese paper", "ctexart", "ctexbook", "xecjk"))
    explicit_en_target = any(k in combined for k in ("english", "en-us", "英文论文", "英文版", "ieeetran", "acmart", "elsarticle", "llncs", "neurips", "icml"))
    explicit_bilingual = any(k in combined for k in ("bilingual", "双语", "中英对照", "中英文"))
    if explicit_bilingual:
        language = "bilingual"
    elif cjk_in_files or explicit_zh_target:
        language = "en" if (explicit_en_target and not cjk_in_files and not explicit_zh_target) else "zh"
    elif has_cjk(combined) and not has_tex and not explicit_en_target:
        language = "zh"
    else:
        language = "en"

    detected_templates: List[str] = []
    for tpl in ("IEEEtran", "acmart", "elsarticle", "llncs", "sn-jnl", "neurips", "icml", "ctexart", "ctexbook", "revtex4-2", "beamer"):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(tpl)}(?![A-Za-z0-9])", combined, re.I):
            detected_templates.append(tpl)
    if re.search(r"\\documentclass(?:\[[^\]]*\])?\{article\}|\barticle\s+(?:class|template)\b", combined, re.I):
        detected_templates.append("article")

    for f in files:
        if f["category"] == "tex_class_or_style":
            stem = Path(f["rel_path"]).stem
            if stem not in detected_templates:
                detected_templates.append(stem)

    return {
        "primary_mode": modes[0],
        "detected_modes": modes,
        "language": language,
        "target_templates": detected_templates,
        "has_tex_inputs": has_tex,
        "has_bib_inputs": has_bib,
        "has_non_tex_sources": has_draft,
    }


def inventory_inputs(input_dir: Path) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Walk input directory and classify every file honestly."""
    files: List[Dict[str, Any]] = []
    warnings: List[str] = []
    tex_candidates: List[str] = []

    if not input_dir.exists():
        warnings.append(f"input_dir_missing: {input_dir}")
        return files, warnings, tex_candidates

    for root, dirs, filenames in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            fpath = Path(root) / fname
            rel = posix_rel(fpath, input_dir)
            if rel in CONTROL_FILES:
                continue
            ext = fpath.suffix.lower()
            category = CATEGORY_BY_EXT.get(ext, "other")
            try:
                size = fpath.stat().st_size
            except OSError as exc:
                warnings.append(f"stat_failed: {rel} ({exc})")
                size = -1

            entry: Dict[str, Any] = {
                "rel_path": rel,
                "abs_path": str(fpath.resolve()),
                "ext": ext,
                "category": category,
                "size_bytes": size,
                "readable": True,
                "read_error": None,
                "contains_cjk": False,
                "is_entry_tex": False,
            }

            if size == 0:
                warnings.append(f"empty_file: {rel}")

            if category in {"tex_source", "bib_database", "draft_or_doc", "tabular_data", "structured_data", "tex_log", "bib_log", "tex_class_or_style"}:
                text, err = read_text_safe(fpath)
                if err:
                    entry["readable"] = False
                    entry["read_error"] = err
                    warnings.append(f"read_error: {rel} ({err})")
                else:
                    entry["contains_cjk"] = has_cjk(text)
                    entry["line_count"] = len(text.splitlines())
                    if category == "tex_source":
                        clean_tex = sanitize_tex_for_scan(text)
                        has_docclass = bool(re.search(r"^\s*\\documentclass\b", clean_tex, re.M))
                        has_begindoc = bool(re.search(r"\\begin\{document\}", clean_tex))
                        entry["has_documentclass"] = has_docclass
                        entry["has_begin_document"] = has_begindoc
                        if has_docclass and has_begindoc:
                            entry["is_entry_tex"] = True
                            tex_candidates.append(rel)
            elif ext in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".eps"}:
                # Verify openability at OS permission level
                try:
                    with fpath.open("rb") as fh:
                        head = fh.read(16)
                    if ext == ".pdf" and not head.startswith(b"%PDF"):
                        entry["readable"] = False
                        entry["read_error"] = "invalid_pdf_header"
                        warnings.append(f"invalid_pdf_header: {rel}")
                except PermissionError as exc:
                    entry["readable"] = False
                    entry["read_error"] = f"permission_denied: {exc}"
                    warnings.append(f"permission_denied: {rel}")
                except OSError as exc:
                    entry["readable"] = False
                    entry["read_error"] = f"os_error: {exc}"
                    warnings.append(f"os_error: {rel}")

            files.append(entry)

    # Check if task/case mentions files that do not exist
    return files, warnings, tex_candidates


def detect_missing_referenced_files(task_text: str, case_json: Dict[str, Any], input_dir: Path, files: List[Dict[str, Any]]) -> List[str]:
    """Identify files explicitly named in case.json/task.md that are absent from /input."""
    existing = {f["rel_path"] for f in files} | {Path(f["rel_path"]).name for f in files}
    missing: List[str] = []

    # Check explicit file lists inside case.json if present (never stringify dicts lacking path/file)
    for key in ("files", "inputs", "artifacts", "materials", "attachments"):
        val = case_json.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    raw_cand = item.get("path") or item.get("file") or item.get("rel_path")
                    if not isinstance(raw_cand, str) or not raw_cand.strip():
                        continue
                    candidate_clean = raw_cand.strip().lstrip("./")
                elif isinstance(item, str):
                    candidate_clean = item.strip().lstrip("./")
                else:
                    continue
                if Path(candidate_clean).name in EXPECTED_OUTPUT_FILES:
                    continue
                if candidate_clean and not (input_dir / candidate_clean).exists() and candidate_clean not in existing:
                    if candidate_clean not in missing:
                        missing.append(candidate_clean)

    # Check filename tokens in task_text (skip output deliverables & filenames preceded by output cues)
    out_cue_re = re.compile(r"(?:输出|生成|交付|创建|保存为|写入|output|generate|deliver|create|produce|write\s+to)\s*[:：]?\s*$", re.I)
    for m in re.finditer(r"`([A-Za-z0-9_./-]+\.(?:tex|bib|cls|sty|bst|md|csv|tsv|pdf|docx|png|jpg|eps|json))`", task_text):
        ref = m.group(1).lstrip("./")
        base = Path(ref).name
        if base in EXPECTED_OUTPUT_FILES or ref.startswith(("app/output/", "/app/output/", "app/work/", "/app/work/", "output/")):
            continue
        before_ctx = task_text[max(0, m.start() - 32) : m.start()]
        if out_cue_re.search(before_ctx):
            continue
        if not (input_dir / ref).exists() and base not in existing:
            if ref not in missing:
                missing.append(ref)
    return missing


def inspect_case(input_dir: Path, task_path_override: Optional[Path] = None) -> Dict[str, Any]:
    """Run complete case inspection and return structured case_plan dictionary."""
    case_path = input_dir / "case.json"
    case_json = load_json_safe(case_path, default={}) or {}
    if not isinstance(case_json, dict):
        case_json = {}

    task_candidates = [p for p in [task_path_override, input_dir / "task.md", Path("task.md")] if p is not None]
    task_text = ""
    task_source = None
    for tp in task_candidates:
        if tp.exists():
            txt, err = read_text_safe(tp)
            if not err:
                task_text = txt
                task_source = str(tp)
                break

    case_id = str(case_json.get("case_id") or case_json.get("id") or input_dir.name or "unknown_case")
    output_contract = parse_output_contract(case_json.get("output_contract"))

    files, access_warnings, tex_candidates = inventory_inputs(input_dir)
    missing_refs = detect_missing_referenced_files(task_text, case_json, input_dir, files)
    env_info = probe_tex_environment()

    inline_keys = [
        k for k in ("source_tex", "tex", "tex_source", "latex", "latex_source", "manuscript", "draft", "draft_text", "bib_text", "bibtex", "references")
        if isinstance(case_json.get(k), str) and case_json.get(k).strip()
    ]
    signals = infer_signals(task_text, case_json, files)

    return {
        "schema_version": "1.0",
        "input_dir": str(input_dir.resolve()),
        "case_id": case_id,
        "case_json_present": case_path.exists(),
        "case_json_raw": case_json,
        "task_md_path": task_source,
        "task_text_preview": task_text[:4000],
        "output_contract": output_contract,
        "signals": signals,
        "inline_material_keys": inline_keys,
        "candidate_entry_tex": tex_candidates,
        "files": files,
        "stats": {
            "total_files": len(files),
            "readable_files": sum(1 for f in files if f["readable"]),
            "tex_files": sum(1 for f in files if f["category"] == "tex_source"),
            "bib_files": sum(1 for f in files if f["category"] == "bib_database"),
            "class_or_style_files": sum(1 for f in files if f["category"] == "tex_class_or_style"),
            "non_tex_sources": sum(1 for f in files if f["category"] in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data", "structured_data"}),
            "inline_material_count": len(inline_keys),
        },
        "access_warnings": access_warnings,
        "missing_referenced_files": missing_refs,
        "environment": env_info,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Harbor /input case contract, materials, and TeX/font environment.")
    parser.add_argument("--input-dir", default="/input", help="Input directory containing case.json, task.md, and materials.")
    parser.add_argument("--task-md", default=None, help="Optional override path for task.md.")
    parser.add_argument("--out", default="/app/work/case_plan.json", help="Output path for case_plan.json.")
    args = parser.parse_args()

    plan = inspect_case(
        Path(args.input_dir),
        Path(args.task_md) if args.task_md else None,
    )
    save_json(Path(args.out), plan)
    print(
        f"[inspect_case] case_id={plan['case_id']} mode={plan['signals']['primary_mode']} "
        f"lang={plan['signals']['language']} files={plan['stats']['total_files']} "
        f"cjk_fonts={len(plan['environment']['cjk_fonts'])} -> {args.out}"
    )


if __name__ == "__main__":
    main()
