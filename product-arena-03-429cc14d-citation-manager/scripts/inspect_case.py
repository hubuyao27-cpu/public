#!/usr/bin/env python3
"""Step 1: Inspect Harbor /input/case.json, task.md, and all input files.

Produces /app/work/case_plan.json with:
- case_id and parsed output_contract schema + semantic role mapping
- detected target citation style, in-text marker format, requested actions, output formats
- categorized file inventory (reference_library, manuscript, evidence_paper, style_guide, corrupt_or_unreadable)
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
CONTROL_FILES = {"case.json", "task.md", "case_plan.json", "result.json"}

LIB_EXTS = {".bib", ".ris", ".enw", ".csl"}
DOC_EXTS = {".md", ".markdown", ".txt", ".docx", ".tex", ".html", ".htm", ".rtf"}
TABLE_EXTS = {".csv", ".tsv"}
PDF_EXTS = {".pdf"}
JSON_EXTS = {".json", ".jsonl"}
XML_EXTS = {".xml"}
BINARY_IGNORE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".tar", ".gz", ".exe", ".dll", ".so"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    k = f"{field_name} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if any(w in k for w in ("revised_manuscript", "manuscript", "revised_text", "revised_draft", "draft_text", "article", "edited_text", "output_text", "chapter", "body")):
        return "revised_manuscript"
    if any(w in k for w in ("reference_list", "bibliography", "references_formatted", "formatted_references", "citations_list", "reference_entries", "references")):
        return "reference_list"
    if any(w in k for w in ("renumber", "mapping", "cross_ref", "citation_map", "in_text_map", "order_map")):
        return "renumbering_map"
    if any(w in k for w in ("dedup", "merge", "duplicate", "cluster")):
        return "dedup_report"
    if any(w in k for w in ("claim_verif", "verification", "support", "alignment", "consistency", "audit", "source_check", "fact_check")):
        return "claim_verification"
    if any(w in k for w in ("provenance", "field_source", "evidence_map", "source_map", "sources")):
        return "field_provenance"
    if any(w in k for w in ("change", "diff", "revision", "edit_log", "modification")):
        return "change_records"
    if any(w in k for w in ("confirm", "question", "gap", "missing", "manual_confirm", "human_confirm", "todo")):
        return "human_confirmation_items"
    if any(w in k for w in ("uncertain", "unknown", "warning", "risk", "limitation", "error")):
        return "uncertainty"
    if any(w in k for w in ("file", "artifact", "deliverable", "preview", "path", "bib", "ris")):
        return "deliverable_files"
    if any(w in k for w in ("summary", "overview", "report")):
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
            return {
                "raw_type": "json_schema",
                "fields": fields,
                "required_keys": [f["name"] for f in fields if f["required"]],
            }

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
        return {
            "raw_type": "dict_template",
            "fields": fields,
            "required_keys": [f["name"] for f in fields if f["required"]],
        }

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
        return {
            "raw_type": "list",
            "fields": fields,
            "required_keys": [f["name"] for f in fields if f["required"]],
        }

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def _looks_like_cnki_or_ris_txt(text: str) -> bool:
    if not text:
        return False
    markers = (
        "TY  -", "ER  -", "RT ", "T1 ", "A1 ", "SrcDatabase-", "Title-题名",
        "%0 ", "%T ", "%A ", "@article{", "@inproceedings{", "@book{",
    )
    return sum(1 for m in markers if m in text[:4000]) >= 2


def _looks_like_ref_list_only(text: str, rel_low: str) -> bool:
    if any(w in rel_low for w in ("ref", "bib", "library", "cnki", "endnote", "wos", "scopus", "literature", "文献", "参考")):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    numbered = sum(1 for ln in lines[:40] if re.match(r"^(?:\[\d+\]|\d+[.)])\s+", ln))
    return numbered >= max(2, int(len(lines[:40]) * 0.5))


def classify_input_file(path: Path, rel: str) -> Dict[str, Any]:
    rel_low = rel.lower()
    ext = path.suffix.lower()

    info: Dict[str, Any] = {
        "rel_path": rel,
        "abs_path": str(path.resolve()),
        "ext": ext,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "role": "unknown",
        "format_hint": ext.lstrip(".") or "unknown",
        "warning": None,
    }

    if info["size_bytes"] == 0:
        info["role"] = "corrupt_or_unreadable"
        info["warning"] = "empty_file"
        return info

    if ext in BINARY_IGNORE_EXTS:
        info["role"] = "auxiliary_binary"
        return info

    if ext in LIB_EXTS:
        txt, enc, warn = read_text_safe(path)
        if enc in ("binary", "unreadable", "empty"):
            info["role"] = "corrupt_or_unreadable"
            info["warning"] = warn
        else:
            info["role"] = "reference_library"
            info["warning"] = warn
        return info

    if ext in XML_EXTS:
        txt, enc, warn = read_text_safe(path)
        info["warning"] = warn
        if "<records" in txt.lower() or "<record>" in txt.lower():
            info["role"] = "reference_library"
            info["format_hint"] = "endnote_xml"
        elif "<article" in txt.lower() or "<sec" in txt.lower():
            info["role"] = "evidence_paper"
            info["format_hint"] = "jats_xml"
        else:
            info["role"] = "auxiliary_text"
        return info

    if ext in TABLE_EXTS:
        txt, enc, warn = read_text_safe(path)
        if enc in ("binary", "unreadable", "empty"):
            info["role"] = "corrupt_or_unreadable"
            info["warning"] = warn
        else:
            info["role"] = "reference_library"
            info["warning"] = warn
        return info

    if ext in JSON_EXTS:
        txt, enc, warn = read_text_safe(path)
        info["warning"] = warn
        if enc in ("binary", "unreadable", "empty"):
            info["role"] = "corrupt_or_unreadable"
            return info
        if any(w in rel_low for w in ("csl", "ref", "bib", "library", "citation", "文献")) or '"container-title"' in txt[:2000] or '"DOI"' in txt[:2000]:
            info["role"] = "reference_library"
            info["format_hint"] = "csl_json" if '"container-title"' in txt[:2000] else "json_library"
        else:
            info["role"] = "auxiliary_json"
        return info

    if ext in PDF_EXTS:
        if any(w in rel_low for w in ("draft", "manuscript", "稿件", "初稿", "thesis", "paper_to_edit")):
            info["role"] = "manuscript"
        elif any(w in rel_low for w in ("guide", "style", "规范", "要求")):
            info["role"] = "style_guide"
        else:
            info["role"] = "evidence_paper"
        return info

    if ext in DOC_EXTS:
        if ext == ".docx":
            if any(w in rel_low for w in ("guide", "style", "规范")):
                info["role"] = "style_guide"
            else:
                info["role"] = "manuscript"
            return info

        txt, enc, warn = read_text_safe(path)
        if enc in ("binary", "unreadable", "empty"):
            info["role"] = "corrupt_or_unreadable"
            info["warning"] = warn
            return info
        info["warning"] = warn

        if ext == ".txt" and _looks_like_cnki_or_ris_txt(txt):
            info["role"] = "reference_library"
            info["format_hint"] = "tagged_txt"
        elif any(w in rel_low for w in ("guide", "style_spec", "author_guide", "投稿须知", "格式规范")):
            info["role"] = "style_guide"
        elif any(w in rel_low for w in ("evidence", "source_paper", "fulltext", "abstract", "pdf_text", "原文", "证据")):
            info["role"] = "evidence_paper"
        elif ext in (".md", ".txt") and _looks_like_ref_list_only(txt, rel_low) and not any(
            w in rel_low for w in ("draft", "manuscript", "paper", "section", "chapter", "稿", "正文")
        ):
            info["role"] = "reference_library"
            info["format_hint"] = "numbered_ref_txt"
        else:
            info["role"] = "manuscript"
        return info

    info["role"] = "auxiliary_text"
    return info


def detect_task_signals(task_text: str, case_data: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
    combined = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    low = combined.lower()

    target_style = "gbt7714_2015_numeric"
    in_text_format = "superscript_bracket"

    if any(k in low for k in ("apa 7", "apa7", "apa 7th", "apa第7", "apa第七")):
        target_style = "apa7"
        in_text_format = "author_year"
    elif any(k in low for k in ("apa 6", "apa6", "apa 6th", "apa第6", "apa第六")):
        target_style = "apa6"
        in_text_format = "author_year"
    elif re.search(r"\bapa\b", low):
        target_style = "apa7"
        in_text_format = "author_year"
    elif "ieee" in low:
        target_style = "ieee"
        in_text_format = "bracket_separate"
    elif any(k in low for k in ("nature style", "nature format", "nature 期刊", "《nature》")):
        target_style = "nature"
        in_text_format = "superscript_html"
    elif "vancouver" in low or "icmje" in low:
        target_style = "vancouver"
        in_text_format = "bracket"
    elif "chicago" in low:
        target_style = "chicago_author_date"
        in_text_format = "author_year"
    elif any(k in low for k in ("著者—出版年", "著者-出版年", "著者出版年", "author-year", "author year", "author_year")) and (
        "7714" in low or "gb/t" in low or "国标" in low
    ):
        target_style = "gbt7714_2015_author_year"
        in_text_format = "author_year"
    elif any(k in low for k in ("7714", "gb/t", "gb/t 7714", "顺序编码")):
        target_style = "gbt7714_2015_numeric"
        in_text_format = "superscript_bracket" if any(k in low for k in ("上标", "superscript")) else "bracket"
    elif any(k in low for k in ("著者—出版年", "著者-出版年", "author-year", "author-date")):
        target_style = "gbt7714_2015_author_year"
        in_text_format = "author_year"

    if any(k in low for k in ("\\cite{", "latex", ".tex")):
        has_tex_input = any(f["ext"] == ".tex" for f in files)
        explicit_non_tex_style = any(k in low for k in ("7714", "gb/t", "apa", "上标", "superscript", "顺序编码", "著者—出版年", "著者-出版年"))
        if has_tex_input or "\\cite{" in low or ("latex" in low and not explicit_non_tex_style):
            in_text_format = "latex_cite"
        elif any(k in low for k in ("上标", "superscript")) and target_style in ("gbt7714_2015_numeric", "nature", "vancouver"):
            in_text_format = "superscript_bracket"
    elif any(k in low for k in ("脚注", "footnote")):
        in_text_format = "footnote"
    elif any(k in low for k in ("上标", "superscript")) and target_style in ("gbt7714_2015_numeric", "nature", "vancouver"):
        in_text_format = "superscript_bracket"
    elif any(k in low for k in ("行内方括号", "非上标", "[1]", "inline bracket")) and target_style in ("gbt7714_2015_numeric", "ieee", "vancouver"):
        in_text_format = "bracket"

    requested_formats = ["markdown", "json", "bib", "ris", "csl_json", "csv"]
    if any(f["ext"] == ".docx" for f in files) or any(k in low for k in (".docx", "docx", "word文档", "word format")):
        requested_formats.append("docx")
    if any(f["ext"] == ".tex" for f in files) or any(k in low for k in (".tex", "latex")):
        requested_formats.append("tex")

    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", task_text or ""))
    en_words = len(re.findall(r"[A-Za-z]{3,}", task_text or ""))
    language = "zh" if zh_chars >= 20 or zh_chars >= en_words else "en"

    actions = []
    action_cues = [
        ("citation_insertion_and_placement", ("插入", "挂接", "引用位置", "段落末尾", "insert", "placement", "relocate", "locate")),
        ("renumbering_and_cross_ref_sync", ("编号", "重排", "调序", "删除", "交叉引用", "上标", "renumber", "reorder", "cross-ref", "sync")),
        ("deduplication_and_merging", ("去重", "合并", "预印本", "中英文", "dedup", "duplicate", "merge", "arxiv")),
        ("format_conversion_and_completion", ("格式", "转换", "补齐", "7714", "apa", "ieee", "bibtex", "ris", "endnote", "cnki", "convert", "format")),
        ("source_and_claim_verification", ("核验", "一致", "匹配", "真伪", "编造", "doi", "verify", "claim", "hallucin", "mismatch", "evidence")),
    ]
    for act, cues in action_cues:
        if any(c in low for c in cues):
            actions.append(act)
    if not actions:
        actions = [a for a, _ in action_cues]

    return {
        "target_style": target_style,
        "in_text_format": in_text_format,
        "language": language,
        "requested_actions": actions,
        "requested_output_formats": requested_formats,
    }


def detect_missing_referenced_files(combined_text: str, discovered_rels: List[str], input_dir: Path) -> List[str]:
    candidates = re.findall(
        r"([A-Za-z0-9_.\-/\\]+\.(?:bib|ris|enw|xml|json|jsonl|csv|tsv|md|txt|docx|tex|pdf))",
        combined_text,
        flags=re.I,
    )
    disc_names = {Path(r).name.lower() for r in discovered_rels}
    disc_rels = {r.lower() for r in discovered_rels}
    missing: List[str] = []
    for c in candidates:
        c_norm = c.replace("\\", "/").lstrip("./")
        base = Path(c_norm).name.lower()
        if base in CONTROL_FILES or base in ("result.json", "citation_plan.json"):
            continue
        if c_norm.lower() not in disc_rels and base not in disc_names and not (input_dir / c_norm).exists():
            if c_norm not in missing:
                missing.append(c_norm)
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Harbor input case and classify materials.")
    parser.add_argument("--input-dir", default="/input", help="Input directory containing case.json, task.md, and materials.")
    parser.add_argument("--out", default="/app/work/case_plan.json", help="Output path for case_plan.json.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    out_path = Path(args.out)

    case_path = input_dir / "case.json"
    case_data = load_json_safe(case_path, {}) if case_path.exists() else {}
    if not isinstance(case_data, dict):
        case_data = {}

    task_text = ""
    for tp in (input_dir / "task.md", Path("task.md"), Path("/input/task.md")):
        if tp.exists():
            t, _, _ = read_text_safe(tp)
            if t.strip():
                task_text = t
                break

    case_id = str(case_data.get("case_id") or case_data.get("id") or input_dir.name or "unknown_case")
    contract_raw = case_data.get("output_contract") or case_data.get("expected_output") or case_data.get("schema")
    output_contract_parsed = parse_output_contract(contract_raw)

    files: List[Dict[str, Any]] = []
    access_warnings: List[str] = []
    if input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = posix_rel(p, input_dir)
            if p.name.lower() in CONTROL_FILES:
                continue
            finfo = classify_input_file(p, rel)
            files.append(finfo)
            if finfo.get("warning"):
                access_warnings.append(f"{rel}: {finfo['warning']}")
    else:
        access_warnings.append(f"input_dir_not_found: {input_dir}")

    signals = detect_task_signals(task_text, case_data, files)
    if isinstance(case_data.get("citation_style"), str) and case_data["citation_style"].strip():
        cs_low = case_data["citation_style"].strip().lower()
        for cand in ("gbt7714_2015_numeric", "gbt7714_2015_author_year", "apa7", "apa6", "ieee", "nature", "vancouver", "chicago_author_date"):
            if cand in cs_low:
                signals["target_style"] = cand
                break

    combined_text = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    missing_refs = detect_missing_referenced_files(combined_text, [f["rel_path"] for f in files], input_dir)

    by_role: Dict[str, List[str]] = {}
    for f in files:
        by_role.setdefault(f["role"], []).append(f["rel_path"])

    inline_ms_keys = [
        k for k in ("manuscript", "manuscript_text", "draft", "draft_text", "text", "body", "paper", "article", "sections")
        if case_data.get(k)
    ]
    inline_lib_keys = [
        k for k in ("references", "bibliography", "library", "papers", "records", "bib_text", "bibtex", "ris_text", "cnki_text", "reference_list_text")
        if case_data.get(k)
    ]
    if not inline_lib_keys and task_text and (re.search(r"@\w+\s*\{", task_text) or ("TY  -" in task_text and "ER  -" in task_text)):
        inline_lib_keys.append("task.md#inline_library")

    if inline_ms_keys and "manuscript" not in by_role:
        # Check inline text for LaTeX signals
        inline_blob = json.dumps({k: case_data[k] for k in inline_ms_keys if k in case_data}, ensure_ascii=False)
        if "\\cite" in inline_blob or "\\section" in inline_blob:
            if "tex" not in signals["requested_output_formats"]:
                signals["requested_output_formats"].append("tex")
            signals["in_text_format"] = "latex_cite"

    plan = {
        "case_id": case_id,
        "input_dir": str(input_dir.resolve()) if input_dir.exists() else str(input_dir),
        "case_json_present": case_path.exists(),
        "case_metadata": {
            k: v for k, v in case_data.items() if k not in ("output_contract",)
        },
        "task_md_excerpt": task_text[:4000],
        "output_contract_raw": contract_raw,
        "output_contract_parsed": output_contract_parsed,
        "signals": signals,
        "inline_material_keys": {
            "manuscript": inline_ms_keys,
            "library": inline_lib_keys,
        },
        "files": files,
        "files_by_role": by_role,
        "missing_referenced_files": missing_refs,
        "access_warnings": access_warnings,
        "stats": {
            "total_material_files": len(files),
            "reference_library_count": len(by_role.get("reference_library", [])) + (1 if inline_lib_keys else 0),
            "manuscript_count": len(by_role.get("manuscript", [])) + (1 if inline_ms_keys else 0),
            "evidence_paper_count": len(by_role.get("evidence_paper", [])),
            "corrupt_or_unreadable_count": len(by_role.get("corrupt_or_unreadable", [])),
            "has_inline_manuscript": bool(inline_ms_keys),
            "has_inline_library": bool(inline_lib_keys),
        },
    }
    save_json(out_path, plan)
    print(json.dumps({
        "case_id": case_id,
        "target_style": signals["target_style"],
        "in_text_format": signals["in_text_format"],
        "stats": plan["stats"],
        "missing_referenced_files": missing_refs,
        "access_warnings": access_warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
