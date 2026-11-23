#!/usr/bin/env python3
"""Step 1: Inspect Harbor /input/case.json, task.md, manuscript & library files."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from common import load_json_safe, posix_rel, read_text_safe, save_json

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md", "case_plan.json", "result.json"}

LIB_EXTS = {".bib", ".ris", ".enw", ".nbib", ".ciw"}
MANUSCRIPT_EXTS = {".md", ".markdown", ".tex", ".docx", ".pdf", ".rst"}
BINARY_SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".bin"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract field key to a semantic role."""
    k = f"{field_name} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if any(w in k for w in ("updated_manuscript", "manuscript", "revised_text", "paper_text", "output_text", "draft")):
        return "updated_manuscript"
    if any(w in k for w in ("bibliograph", "reference_list", "references", "formatted_refs", "clean_library")):
        return "formatted_bibliography"
    if any(w in k for w in ("renumber", "mapping", "sync_map", "cross_ref")):
        return "renumbering_map"
    if any(w in k for w in ("dedup", "merge", "duplicate")):
        return "dedup_summary"
    if any(w in k for w in ("verif", "citation_audit", "claim_match", "authenticity")):
        return "citation_verifications"
    if any(w in k for w in ("source", "provenance", "snapshot")):
        return "sources"
    if any(w in k for w in ("change", "diff", "edit_log", "revision")):
        return "change_records"
    if any(w in k for w in ("confirm", "question", "human_review", "author_confirm")):
        return "author_confirmation_items"
    if any(w in k for w in ("uncertain", "warning", "gap", "unresolved")):
        return "uncertainty"
    if any(w in k for w in ("limitation", "boundary", "caveat")):
        return "limitations"
    if any(w in k for w in ("metric", "scorecard", "audit_summary")):
        return "audit_metrics"
    if any(w in k for w in ("file", "artifact", "deliverable", "preview", "path")):
        return "deliverable_files"
    if any(w in k for w in ("summary", "overview")):
        return "summary"
    return "custom"


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    """Normalize any output_contract shape into structured field specs."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req_set = set(contract.get("required") or [])
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                ftype = str(sd.get("type", type(spec).__name__))
                desc = str(sd.get("description", ""))
                fields.append({"name": k, "expected_type": ftype, "required": (k in req_set) if req_set else True, "description": desc, "semantic_role": infer_semantic_role(k, desc)})
            return {"raw_type": "json_schema", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

        if "fields" in contract and isinstance(contract["fields"], (dict, list)):
            return parse_output_contract(contract["fields"])

        meta_keys = {"type", "description", "format", "required", "$schema", "title"}
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        for k, val in contract.items():
            if k in meta_keys and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                ftype = str(val.get("type", "object"))
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype = type(val).__name__ if val is not None else "any"
                desc = val if isinstance(val, str) else ""
                req = (k in req_list) if req_list is not None else True
            fields.append({"name": k, "expected_type": ftype, "required": req, "description": desc, "semantic_role": infer_semantic_role(k, desc)})
        if req_list:
            existing = {f["name"] for f in fields}
            for rk in req_list:
                if rk not in existing:
                    fields.append({"name": rk, "expected_type": "any", "required": True, "description": "", "semantic_role": infer_semantic_role(rk)})
        return {"raw_type": "dict_template", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append({"name": item, "expected_type": "any", "required": True, "description": "", "semantic_role": infer_semantic_role(item)})
            elif isinstance(item, dict) and ("name" in item or "key" in item):
                name = str(item.get("name") or item.get("key"))
                desc = str(item.get("description", ""))
                fields.append({"name": name, "expected_type": str(item.get("type", "any")), "required": bool(item.get("required", True)), "description": desc, "semantic_role": infer_semantic_role(name, desc)})
        return {"raw_type": "list", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def classify_file(path: Path, rel: str) -> str:
    """Classify an input file as library, manuscript, style_spec, or other."""
    name_low = path.name.lower()
    rel_low = rel.lower()
    ext = path.suffix.lower()

    if name_low in CONTROL_FILES:
        return "control"
    if ext in BINARY_SKIP_EXTS:
        return "binary_skip"
    if ext in LIB_EXTS:
        return "library"
    if any(k in rel_low for k in ("bib", "ref", "library", "cnki", "endnote", "ris", "csl", "literature", "文献", "参考")):
        if ext in (".json", ".csv", ".tsv", ".xml", ".txt", ".md"):
            return "library"
    if ext in (".json", ".csv", ".tsv", ".xml"):
        txt, _, _ = read_text_safe(path)
        sample = (txt or "")[:1200].lower()
        if any(tok in sample for tok in ("<records>", "<record>", "\"title\"", "author", "doi", "journal", "booktitle")):
            return "library"
        return "other"
    if ext == ".txt":
        txt, _, _ = read_text_safe(path)
        sample = (txt or "")[:1200]
        if any(sig in sample for sig in ("TY  -", "%0 ", "%T ", "RT ", "T1 ", "SrcDatabase-", "{Reference Type}:", "@article", "@inproceedings")):
            return "library"
        return "manuscript"
    if ext in MANUSCRIPT_EXTS:
        return "manuscript"
    return "other"


def detect_signals(task_text: str, case_data: Dict[str, Any]) -> Dict[str, Any]:
    """Detect target citation style, system, requested actions, and language."""
    combined = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    low = combined.lower()

    if ("7714" in low or "国标" in low) and any(k in low for k in ("著者", "出版年", "author-year", "author year")):
        target_style = "gbt7714_author_year"
    elif "7714" in low or "国标" in low or "顺序编码" in low:
        target_style = "gbt7714_numeric"
    elif "apa" in low:
        target_style = "apa7"
    elif "ieee" in low:
        target_style = "ieee"
    elif "nature" in low:
        target_style = "nature"
    elif "vancouver" in low or "icmje" in low:
        target_style = "vancouver"
    elif "chicago" in low:
        target_style = "chicago_author_year"
    else:
        # Infer default based on predominant script
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", task_text or ""))
        target_style = "gbt7714_numeric" if zh_chars > 30 else "ieee"

    system = "author_year" if target_style in ("gbt7714_author_year", "apa7", "chicago_author_year") else "numeric"

    actions: List[str] = []
    if any(k in low for k in ("insert", "place", "add citation", "插入", "补引", "引用定位")):
        actions.append("insert")
    if any(k in low for k in ("renumber", "reorder", "delete", "remove", "重排", "编号", "调序", "更新", "同步")):
        actions.append("renumber")
    if any(k in low for k in ("dedup", "merge", "duplicate", "去重", "合并", "归并")):
        actions.append("deduplicate")
    if any(k in low for k in ("convert", "format", "style", "7714", "apa", "ieee", "bib", "ris", "格式")):
        actions.append("format_convert")
    if any(k in low for k in ("verify", "check", "audit", "fabricat", "mismatch", "核验", "核查", "一致")):
        actions.append("verify_sources")
    if not actions:
        actions = ["insert", "renumber", "deduplicate", "format_convert", "verify_sources"]

    requested_formats = ["markdown", "json", "bib", "ris", "csv"]
    if any(k in low for k in (".docx", "docx", "word")):
        requested_formats.append("docx")
    if any(k in low for k in (".tex", "latex")):
        requested_formats.append("latex")

    zh_count = len(re.findall(r"[\u4e00-\u9fff]", task_text or ""))
    lang = "zh" if zh_count > 25 else "en"
    retain_uncited = bool(case_data.get("retain_uncited_library", False)) or ("保留全部文献" in low or "retain uncited" in low)

    return {
        "target_style": target_style,
        "citation_system": system,
        "requested_actions": actions,
        "requested_formats": requested_formats,
        "language": lang,
        "retain_uncited_library": retain_uncited,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect citation task contract and input files.")
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--case", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--out", default="/app/work/case_plan.json")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    case_path = Path(args.case) if args.case else (input_dir / "case.json")
    task_path = Path(args.task) if args.task else (input_dir / "task.md")
    if not task_path.exists() and Path("task.md").exists():
        task_path = Path("task.md")

    warnings: List[str] = []
    case_data, case_err = load_json_safe(case_path, default={})
    if case_err:
        warnings.append(case_err)
    if not isinstance(case_data, dict):
        case_data = {}

    task_text = ""
    if task_path.exists():
        t_raw, _, t_warn = read_text_safe(task_path)
        task_text = t_raw or ""
        if t_warn:
            warnings.append(t_warn)
    else:
        warnings.append(f"Task file not found: {task_path}")

    manuscript_files: List[Dict[str, Any]] = []
    library_files: List[Dict[str, Any]] = []
    other_files: List[Dict[str, Any]] = []

    if input_dir.exists():
        for root, dirs, files in os.walk(input_dir):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for fname in sorted(files):
                fpath = Path(root) / fname
                rel = posix_rel(fpath, input_dir)
                cat = classify_file(fpath, rel)
                if cat in ("control", "binary_skip"):
                    continue
                entry = {"path": str(fpath), "rel_path": rel, "ext": fpath.suffix.lower(), "size_bytes": fpath.stat().st_size}
                if cat == "library":
                    library_files.append(entry)
                elif cat == "manuscript":
                    manuscript_files.append(entry)
                else:
                    other_files.append(entry)
    else:
        warnings.append(f"Input directory missing: {input_dir}")

    missing_refs: List[str] = []
    for key in ("input_files", "files", "manuscript_path", "library_path", "bib_path"):
        val = case_data.get(key)
        items = [val] if isinstance(val, str) else (val if isinstance(val, list) else [])
        for it in items:
            ref = it if isinstance(it, str) else (it.get("path") if isinstance(it, dict) else "")
            if ref:
                p = Path(ref) if Path(ref).is_absolute() else (input_dir / ref)
                if not p.exists():
                    missing_refs.append(ref)

    plan = {
        "case_id": case_data.get("case_id") or case_data.get("id") or "unknown_case",
        "input_dir": str(input_dir),
        "task_text": task_text,
        "output_contract": parse_output_contract(case_data.get("output_contract")),
        "signals": detect_signals(task_text, case_data),
        "manuscript_files": manuscript_files,
        "library_files": library_files,
        "other_files": other_files,
        "inline_manuscript": case_data.get("manuscript") or case_data.get("text") or "",
        "inline_library": case_data.get("library") or case_data.get("references") or None,
        "missing_referenced_files": missing_refs,
        "warnings": warnings,
    }
    save_json(Path(args.out), plan)


if __name__ == "__main__":
    main()
