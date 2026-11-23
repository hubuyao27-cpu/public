#!/usr/bin/env python3
"""Step 1: Inspect Harbor /input/case.json, task.md, and workspace materials.

Produces /app/work/case_plan.json with:
- case_id and parsed output_contract schema/keys
- trigger guard (is_non_polish_qa)
- detected genre, target language, edit_intensity, requested formats
- categorized file inventory (manuscripts, glossaries, style guides, missing refs)
"""

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

MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf", ".rst"}
GLOSSARY_EXTS = {".json", ".csv", ".tsv", ".yaml", ".yml"}
BINARY_SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".bin"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract field key to a canonical semantic role."""
    k = f"{field_name} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if any(w in k for w in ("polished_text", "polished_manuscript", "revised_text", "edited_text", "manuscript", "output_text", "final_text", "corrected_text")):
        return "polished_text"
    if any(w in k for w in ("change_record", "change_log", "edit_log", "revision", "sentence_edits", "redline", "diff")):
        return "change_records"
    if any(w in k for w in ("glossary", "terminology", "term_table", "terms")):
        return "glossary"
    if any(w in k for w in ("bilingual", "alignment", "parallel")):
        return "bilingual_pairs"
    if any(w in k for w in ("source", "provenance", "mapping", "traceability")):
        return "sources"
    if any(w in k for w in ("confirm", "question", "human_review", "author_confirm", "ambigu")):
        return "author_confirmation_items"
    if any(w in k for w in ("uncertain", "warning", "conflict", "gap", "risk")):
        return "uncertainty"
    if any(w in k for w in ("limitation", "boundary", "caveat")):
        return "limitations"
    if any(w in k for w in ("audit", "verif", "metric", "scorecard", "check")):
        return "verification_summary"
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
                fields.append(
                    {
                        "name": k,
                        "expected_type": ftype,
                        "required": (k in req_set) if req_set else True,
                        "description": desc,
                        "semantic_role": infer_semantic_role(k, desc),
                    }
                )
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
            fields.append(
                {
                    "name": k,
                    "expected_type": ftype,
                    "required": req,
                    "description": desc,
                    "semantic_role": infer_semantic_role(k, desc),
                }
            )
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


def classify_input_file(path: Path, rel: str) -> str:
    """Classify an input file as manuscript, glossary, style_guide, or skip."""
    name_low = path.name.lower()
    rel_low = rel.lower()
    ext = path.suffix.lower()

    if name_low in CONTROL_FILES:
        return "control"
    if ext in BINARY_SKIP_EXTS:
        return "binary_skip"
    if any(k in rel_low for k in ("glossary", "terminology", "term_list", "terms", "dict", "词表", "术语")):
        return "glossary"
    if any(k in rel_low for k in ("style", "guideline", "author_guide", "rubric", "规范", "指南")):
        return "style_guide"
    if ext in GLOSSARY_EXTS:
        return "glossary_or_data"
    if ext in MANUSCRIPT_EXTS:
        return "manuscript"
    return "other_text"


def detect_signals(task_text: str, case_data: Dict[str, Any], has_manuscript_files: bool) -> Dict[str, Any]:
    """Detect genre, target language, edit intensity, requested formats, and trigger guard."""
    combined = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    low = combined.lower()

    # Guard: pure factual Q&A without editing/polishing request
    polish_verbs = (
        "polish", "proofread", "copyedit", "edit", "revise", "rewrite", "refine", "improve language",
        "correct grammar", "fix grammar", "terminology consistency", "润色", "校对", "修改", "改写",
        "语病", "语法", "术语统一", "去直译腔", "中式英语", "中译英", "英译中", "双语对照",
    )
    qa_verbs = (
        "what is", "how many", "which temperature", "summarize the findings", "answer the following",
        "extract the value", "请问", "是什么", "多少", "总结这篇论文", "回答以下问题",
    )
    has_polish_intent = any(v in low for v in polish_verbs)
    has_qa_intent = any(v in low for v in qa_verbs)
    has_inline_text = bool(case_data.get("text") or case_data.get("manuscript") or case_data.get("draft"))
    is_non_polish_qa = (not has_polish_intent) and has_qa_intent and (not has_inline_text)

    # Target genre
    if any(k in low for k in ("reviewer response", "rebuttal", "response to reviewers", "审稿回复", "修改说明")):
        genre = "reviewer_response"
        genre_conf = "explicit"
    elif any(k in low for k in ("grant", "proposal", "nsfc", "nih", "基金", "申请书", "立项依据")):
        genre = "grant_proposal"
        genre_conf = "explicit"
    elif any(k in low for k in ("thesis", "dissertation", "学位论文", "硕士论文", "博士论文")):
        genre = "thesis_dissertation"
        genre_conf = "explicit"
    elif any(k in low for k in ("abstract", "摘要")) and not any(k in low for k in ("methods", "introduction", "full manuscript", "全文", "章节")):
        genre = "english_abstract"
        genre_conf = "explicit"
    elif any(k in low for k in ("bilingual", "双语", "中英对照", "中译英", "直译腔")):
        genre = "bilingual_zh_en"
        genre_conf = "explicit"
    elif any(k in low for k in ("journal", "manuscript", "paper", "article", "期刊", "论文", "稿件")):
        genre = "journal_article"
        genre_conf = "explicit"
    else:
        genre = "journal_article"
        genre_conf = "inferred_default"

    # Language
    if any(k in low for k in ("bilingual", "双语", "中英对照", "中译英")):
        language = "bilingual"
    elif any(k in low for k in ("english", "英文", "chinglish", "中式英语")) and not any(k in low for k in ("中文润色", "润色中文")):
        language = "en"
    elif any(k in low for k in ("中文稿件", "中文润色", "润色中文", "中文学位论文")):
        language = "zh"
    else:
        language = "en"

    # Edit intensity
    if any(k in low for k in ("minimal edit", "light edit", "only fix grammar", "最小编辑", "仅改硬错", "轻量润色")):
        edit_intensity = "minimal"
    elif any(k in low for k in ("substantive", "deep polish", "rewrite translationese", "深度润色", "重构", "去直译腔")):
        edit_intensity = "substantive"
    else:
        edit_intensity = "standard"

    requested_formats = ["markdown", "json", "csv"]
    if any(k in low for k in (".docx", "docx", "word")):
        requested_formats.append("docx")
    if any(k in low for k in (".tex", "latex")):
        requested_formats.append("latex")

    return {
        "is_non_polish_qa": is_non_polish_qa,
        "genre": genre,
        "genre_confidence": genre_conf,
        "language": language,
        "edit_intensity": edit_intensity,
        "requested_formats": requested_formats,
        "has_manuscript_files": has_manuscript_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect polishing case contract and input materials.")
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
        warnings.append(f"Task instruction file not found at {task_path}")

    manuscript_files: List[Dict[str, Any]] = []
    glossary_files: List[Dict[str, Any]] = []
    style_guide_files: List[Dict[str, Any]] = []
    other_files: List[Dict[str, Any]] = []

    if input_dir.exists():
        for root, dirs, files in os.walk(input_dir):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for fname in sorted(files):
                fpath = Path(root) / fname
                rel = posix_rel(fpath, input_dir)
                cat = classify_input_file(fpath, rel)
                if cat in ("control", "binary_skip"):
                    continue
                entry = {"path": str(fpath), "rel_path": rel, "ext": fpath.suffix.lower(), "size_bytes": fpath.stat().st_size}
                if cat == "manuscript":
                    manuscript_files.append(entry)
                elif cat == "glossary":
                    glossary_files.append(entry)
                elif cat == "style_guide":
                    style_guide_files.append(entry)
                elif cat == "glossary_or_data":
                    # Peek JSON/CSV keys to decide whether glossary or manuscript data
                    data, _ = load_json_safe(fpath, default=None)
                    if isinstance(data, (dict, list)) and ("terms" in str(data)[:300].lower() or "canonical" in str(data)[:300].lower() or fpath.suffix.lower() in (".csv", ".tsv")):
                        glossary_files.append(entry)
                    else:
                        other_files.append(entry)
                else:
                    other_files.append(entry)
    else:
        warnings.append(f"Input directory not found: {input_dir}")

    # Check explicitly referenced files in case.json / task.md
    missing_refs: List[str] = []
    referenced: List[str] = []
    for key in ("input_files", "files", "manuscript_path", "glossary_path", "draft_path", "documents"):
        val = case_data.get(key)
        if isinstance(val, str):
            referenced.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    referenced.append(item)
                elif isinstance(item, dict) and isinstance(item.get("path"), str):
                    referenced.append(item["path"])
    for ref in referenced:
        p = Path(ref)
        if not p.is_absolute():
            p = input_dir / ref
        if not p.exists():
            missing_refs.append(ref)

    inline_text = case_data.get("text") or case_data.get("manuscript") or case_data.get("draft") or ""
    inline_glossary = case_data.get("glossary") or case_data.get("terminology") or None

    output_contract = parse_output_contract(case_data.get("output_contract"))
    signals = detect_signals(task_text, case_data, bool(manuscript_files or inline_text))

    plan = {
        "case_id": case_data.get("case_id") or case_data.get("id") or "unknown_case",
        "input_dir": str(input_dir),
        "task_text": task_text,
        "output_contract": output_contract,
        "signals": signals,
        "manuscript_files": manuscript_files,
        "glossary_files": glossary_files,
        "style_guide_files": style_guide_files,
        "other_files": other_files,
        "has_inline_text": bool(inline_text),
        "inline_text": inline_text if isinstance(inline_text, str) else json.dumps(inline_text, ensure_ascii=False),
        "inline_glossary": inline_glossary,
        "missing_referenced_files": missing_refs,
        "warnings": warnings,
    }
    save_json(Path(args.out), plan)


if __name__ == "__main__":
    main()
