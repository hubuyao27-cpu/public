#!/usr/bin/env python3
"""Step 1: Inspect /input/case.json, task.md, and all materials under /input.

Produces /app/work/case_plan.json with:
- case_id and parsed output_contract fields (mapped to canonical semantic roles)
- task_mode, language, citation_style, requested_formats, audience/venue
- file inventory categorized into literature/bibliography/own_paper/draft/control
- inline materials discovered inside case.json
- missing referenced files and access warnings
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from common import load_json_safe, posix_rel, read_text_safe, save_json

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_NAMES = {"case.json", "task.md", "case_plan.json", "result.json"}

BIB_EXTS = {".bib", ".ris", ".enw"}
TEXT_DOC_EXTS = {".md", ".markdown", ".txt", ".tex", ".rst", ".docx", ".pdf"}
TABLE_EXTS = {".csv", ".tsv"}
CODE_CONFIG_EXTS = {".py", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log"}
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pt", ".pth", ".ckpt", ".bin", ".npy", ".npz", ".zip", ".tar", ".gz", ".exe", ".dll"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    k = f"{field_name} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if k.strip() in ("status", "run_status", "execution_status") or "status" in field_name.lower():
        return "status"
    if any(w in k for w in ("manuscript", "intro_and_related", "intro_text", "introduction_and_related", "related_work_text", "revised_text", "output_text", "draft_text", "article", "markdown", "prose")):
        return "manuscript_markdown"
    if any(w in k for w in ("background", "research_background", "context_section")):
        return "research_background"
    if any(w in k for w in ("matrix", "comparison_table", "rw_table", "comparison_matrix", "literature_table")):
        return "comparison_matrix"
    if any(w in k for w in ("related_work", "rw_group", "taxonomy", "literature_group", "route")):
        return "related_work_groups"
    if any(w in k for w in ("gap", "research_gap", "open_problem")):
        return "research_gaps"
    if any(w in k for w in ("contrib", "contribution", "contrib_audit")):
        return "contributions"
    if any(w in k for w in ("claim_citation", "claim_map", "citation_map", "claim_to_citation")):
        return "claim_citation_map"
    if any(w in k for w in ("reference", "bib", "citation_check", "citation_list", "citation_audit", "cited_work")):
        return "references"
    if any(w in k for w in ("conflict", "discrepanc", "mismatch", "inconsisten")):
        return "conflicts"
    if any(w in k for w in ("confirm", "question", "author_confirm", "todo_author", "open_issue")):
        return "author_confirmation_items"
    if any(w in k for w in ("change", "revision", "diff", "edit_log", "modification")):
        return "change_records"
    if any(w in k for w in ("uncertain", "unknown", "warning", "limit", "boundary")):
        return "uncertainty"
    if any(w in k for w in ("source", "provenance", "evidence", "traceability")):
        return "sources"
    if any(w in k for w in ("file", "artifact", "deliverable", "preview", "path")):
        return "deliverable_files"
    if any(w in k for w in ("verif", "audit", "metric", "check")):
        return "verification_summary"
    if any(w in k for w in ("summary", "overview", "abstract")):
        return "summary"
    return "custom"


def parse_output_contract(contract: Any) -> Dict[str, Any]:
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
                    fields.append(
                        {
                            "name": rk,
                            "expected_type": "any",
                            "required": True,
                            "description": "",
                            "semantic_role": infer_semantic_role(rk),
                        }
                    )
        return {"raw_type": "dict_template", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(
                    {
                        "name": item,
                        "expected_type": "any",
                        "required": True,
                        "description": "",
                        "semantic_role": infer_semantic_role(item),
                    }
                )
            elif isinstance(item, dict) and ("name" in item or "key" in item):
                name = str(item.get("name") or item.get("key"))
                desc = str(item.get("description", ""))
                fields.append(
                    {
                        "name": name,
                        "expected_type": str(item.get("type", "any")),
                        "required": bool(item.get("required", True)),
                        "description": desc,
                        "semantic_role": infer_semantic_role(name, desc),
                    }
                )
        return {"raw_type": "list", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def classify_file(path: Path, rel: str) -> str:
    name_low = path.name.lower()
    rel_low = rel.lower()
    ext = path.suffix.lower()

    if name_low in CONTROL_NAMES:
        return "control"
    if ext in BINARY_EXTS:
        return "binary_or_figure"
    if ext in BIB_EXTS or any(w in name_low for w in ("bib", "citation", "references", "ref_list", "library")):
        if ext in (".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".md", ".txt", ".tex"):
            return "bibliography"
    if any(w in rel_low for w in ("draft", "existing_intro", "old_intro", "manuscript_draft", "raw_draft")):
        return "own_paper_draft"
    if any(w in rel_low for w in ("result", "experiment", "eval", "metric", "ablation", "figure", "table", "own_paper", "our_paper", "methods")):
        return "own_paper_evidence"
    if any(w in rel_low for w in ("paper", "lit", "prior", "related", "source", "ref", "survey", "corpus")):
        return "literature_source"
    if ext in TABLE_EXTS or ext in CODE_CONFIG_EXTS:
        return "own_paper_evidence"
    if ext in TEXT_DOC_EXTS or ext == ".json":
        return "literature_source"
    return "other_text"


def detect_signals(case_data: Dict[str, Any], task_text: str) -> Dict[str, Any]:
    blob = f"{task_text}\n{json.dumps({k: v for k, v in case_data.items() if k in ('title', 'task', 'instruction', 'prompt', 'language', 'citation_style', 'style', 'venue', 'audience', 'format', 'output_format', 'formats')}, ensure_ascii=False)}"
    low = blob.lower()

    # Language
    explicit_lang = str(case_data.get("language") or case_data.get("target_language") or "").lower()
    if "bilingual" in low or "双语" in blob or ("中文" in blob and "english" in low):
        lang = "bilingual"
    elif explicit_lang.startswith("en") or ("english" in low and "中文" not in blob):
        lang = "en"
    elif explicit_lang.startswith("zh") or re.search(r"[\u4e00-\u9fff]", task_text):
        lang = "zh"
    else:
        lang = "en"

    # Citation style
    explicit_style = str(case_data.get("citation_style") or case_data.get("reference_format") or "").lower()
    if "apa" in explicit_style or re.search(r"\bapa\b|author[- ]year|著者-出版年", low):
        cstyle = "apa_author_year"
    elif "gb/t" in low or "7714" in low or "gbt7714" in explicit_style:
        cstyle = "gbt7714"
    elif "ieee" in low:
        cstyle = "ieee_numeric"
    elif "nature" in low:
        cstyle = "nature_numeric"
    elif "bibtex" in low or "\\cite" in low or "latex" in low:
        cstyle = "bibtex_latex"
    else:
        cstyle = "unspecified"

    # Task mode
    if any(w in low for w in ("bilingual", "双语")):
        tmode = "bilingual_writing"
    elif any(w in low for w in ("revise", "rewrite", "polish", "修改", "改写", "润色", "重构")):
        tmode = "revise_and_strengthen"
    elif any(w in low for w in ("audit", "verify", "check", "核对", "补引", "补全引用", "校对")):
        tmode = "fill_and_audit_citations"
    elif any(w in low for w in ("group", "matrix", "compare", "分组", "梳理相关工作", "对比")):
        tmode = "organize_related_work"
    elif any(w in low for w in ("contribution", "贡献对齐", "贡献表述")):
        tmode = "align_contributions"
    else:
        tmode = "draft_intro_rw"

    # Requested output formats
    req_fmts = ["md"]
    if any(w in low for w in ("latex", ".tex", "tex", "\\cite")):
        req_fmts.append("tex")
    if any(w in low for w in ("bibtex", ".bib")):
        req_fmts.append("bib")
    if any(w in low for w in ("docx", "word")):
        req_fmts.append("docx")
    if any(w in low for w in ("csv", "excel", "table", "矩阵")):
        req_fmts.append("csv")
    if any(w in low for w in ("diagram", "taxonomy", "figure", "图谱", "路线图")):
        req_fmts.append("diagram")

    venue = str(case_data.get("venue") or case_data.get("audience") or case_data.get("journal") or "").strip()
    return {
        "task_mode": tmode,
        "language": lang,
        "citation_style": cstyle,
        "requested_formats": sorted(set(req_fmts)),
        "audience_or_venue": venue,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect Harbor input case, output_contract, and files.")
    ap.add_argument("--input-dir", default="/input", help="Input directory containing case.json and artifacts")
    ap.add_argument("--case", default=None, help="Optional override path for case.json")
    ap.add_argument("--task", default=None, help="Optional override path for task.md")
    ap.add_argument("--out", default="/app/work/case_plan.json", help="Output JSON path")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    case_path = Path(args.case) if args.case else input_dir / "case.json"
    task_path = Path(args.task) if args.task else input_dir / "task.md"
    if not task_path.exists() and Path("task.md").exists():
        task_path = Path("task.md")

    access_warnings: List[str] = []
    case_data, case_err = load_json_safe(case_path)
    if case_err:
        access_warnings.append(f"case_json:{case_err}")
        case_data = {}
    if not isinstance(case_data, dict):
        case_data = {}

    task_text, task_err = read_text_safe(task_path)
    if task_err:
        access_warnings.append(f"task_md:{task_err}")
        task_text = ""

    case_id = str(case_data.get("case_id") or case_data.get("id") or input_dir.name or "case_unknown")
    contract_raw = case_data.get("output_contract")
    parsed_contract = parse_output_contract(contract_raw)

    inventory: List[Dict[str, Any]] = []
    if input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = posix_rel(p, input_dir)
            bucket = classify_file(p, rel)
            readable = True
            warn = None
            try:
                sz = p.stat().st_size
                if sz == 0:
                    warn = "empty_file"
            except OSError as exc:
                readable = False
                sz = 0
                warn = f"stat_error:{exc}"
            if warn:
                access_warnings.append(f"{rel}:{warn}")
            inventory.append(
                {
                    "path": str(p),
                    "rel_path": rel,
                    "ext": p.suffix.lower(),
                    "size_bytes": sz,
                    "bucket": bucket,
                    "readable": readable,
                }
            )

    # Check explicitly referenced files in case.json
    missing_refs: List[str] = []
    for key in ("files", "materials", "artifacts", "documents", "papers", "input_files"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                cand = None
                if isinstance(item, str):
                    cand = item
                elif isinstance(item, dict):
                    cand = item.get("path") or item.get("file") or item.get("filename")
                if cand and isinstance(cand, str) and ("/" in cand or "." in cand):
                    cp = (input_dir / cand) if not Path(cand).is_absolute() else Path(cand)
                    if not cp.exists():
                        missing_refs.append(cand)

    # Identify inline material keys in case.json
    inline_keys: List[str] = []
    for k in ("references", "bibliography", "papers", "literature", "draft", "manuscript", "experiments", "results", "figures", "tables", "methods", "contributions", "materials", "documents", "notes", "context", "background"):
        if k in case_data and case_data[k]:
            inline_keys.append(k)

    signals = detect_signals(case_data, task_text or "")
    non_control_files = [f for f in inventory if f["bucket"] != "control"]

    plan = {
        "case_id": case_id,
        "input_dir": str(input_dir),
        "case_json_path": str(case_path) if case_path.exists() else None,
        "task_md_path": str(task_path) if task_path.exists() else None,
        "task_instruction_excerpt": (task_text or str(case_data.get("instruction") or case_data.get("prompt") or ""))[:1500],
        "output_contract": parsed_contract,
        "signals": signals,
        "inline_material_keys": inline_keys,
        "inventory": inventory,
        "stats": {
            "total_files": len(inventory),
            "non_control_files": len(non_control_files),
            "bibliography_files": sum(1 for f in inventory if f["bucket"] == "bibliography"),
            "literature_files": sum(1 for f in inventory if f["bucket"] == "literature_source"),
            "own_paper_files": sum(1 for f in inventory if f["bucket"] in ("own_paper_draft", "own_paper_evidence")),
        },
        "missing_referenced_files": sorted(set(missing_refs)),
        "access_warnings": access_warnings,
    }
    save_json(Path(args.out), plan)
    print(json.dumps({"status": "ok", "case_id": case_id, "stats": plan["stats"], "signals": signals}, ensure_ascii=False))


if __name__ == "__main__":
    main()
