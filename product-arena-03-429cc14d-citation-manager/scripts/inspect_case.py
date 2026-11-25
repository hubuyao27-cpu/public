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
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, posix_rel, read_text_safe, save_json

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md", "case_plan.json", "result.json"}
EXPECTED_OUTPUT_FILES = {
    "result.json",
    "citation_plan.json",
    "citation_plan_skeleton.json",
    "case_plan.json",
    "revised_manuscript.md",
    "revised_manuscript.docx",
    "revised_manuscript.tex",
    "revised_manuscript.pdf",
    "references_formatted.md",
    "references_clean.bib",
    "references_clean.ris",
    "references_clean.csl.json",
    "references_table.csv",
    "citation_audit_report.md",
    "provenance_ledger.json",
    "verification_report.json",
    "pre_audit.json",
    "sync_result.json",
    "library_canonical.json",
    "manuscript_parsed.json",
    "evidence_fulltexts.json",
    "raw_records.json",
}
_SHORTHAND_TYPES = {
    "string": "string",
    "str": "string",
    "text": "string",
    "array": "array",
    "list": "array",
    "object": "object",
    "dict": "object",
    "map": "object",
    "boolean": "boolean",
    "bool": "boolean",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
}

LIB_EXTS = {".bib", ".ris", ".enw", ".csl"}
DOC_EXTS = {".md", ".markdown", ".txt", ".docx", ".tex", ".html", ".htm", ".rtf"}
TABLE_EXTS = {".csv", ".tsv"}
PDF_EXTS = {".pdf"}
JSON_EXTS = {".json", ".jsonl"}
XML_EXTS = {".xml"}
BINARY_IGNORE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".tar", ".gz", ".exe", ".dll", ".so"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    fn = field_name.lower().strip()
    k = f"{fn} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if fn.endswith(("_path", "_paths", "_file", "_files")) or any(
        w in fn for w in ("deliverable", "artifact", "preview", "output_file", "exported_file")
    ):
        return "deliverable_files"
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


def _infer_type_from_val(val: Any) -> Tuple[str, str]:
    if isinstance(val, str):
        s_low = val.strip().lower()
        if s_low in _SHORTHAND_TYPES:
            return _SHORTHAND_TYPES[s_low], ""
        return "string", val
    if isinstance(val, bool):
        return "boolean", ""
    if isinstance(val, int):
        return "integer", ""
    if isinstance(val, float):
        return "number", ""
    if isinstance(val, list):
        return "array", ""
    if isinstance(val, dict):
        return "object", ""
    return "any", ""


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req_set = set(contract.get("required") or [])
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                raw_t = sd.get("type")
                if raw_t:
                    ftype = _SHORTHAND_TYPES.get(str(raw_t).strip().lower(), str(raw_t))
                else:
                    ftype, _ = _infer_type_from_val(spec)
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

        is_schema_wrapper = (
            "$schema" in contract
            or (contract.get("type") == "object" and isinstance(contract.get("required"), list))
        )
        schema_meta_keys = {"$schema", "additionalProperties"}
        if is_schema_wrapper:
            schema_meta_keys |= {"type", "description", "title", "format", "required"}
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        for k, val in contract.items():
            if k in schema_meta_keys and len(contract) > 1:
                continue
            if k == "required" and isinstance(val, list) and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                raw_t = str(val.get("type", "object")).strip().lower()
                ftype = _SHORTHAND_TYPES.get(raw_t, raw_t)
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype, desc = _infer_type_from_val(val)
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
        head_low = txt[:3000].lower()
        looks_like_json_lib = (
            any(w in rel_low for w in ("csl", "ref", "bib", "library", "citation", "文献"))
            or '"container-title"' in head_low
            or '"doi"' in head_low
            or ('"title"' in head_low and ('"author"' in head_low or '"authors"' in head_low))
        )
        if looks_like_json_lib:
            info["role"] = "reference_library"
            info["format_hint"] = "csl_json" if '"container-title"' in head_low else "json_library"
        else:
            info["role"] = "auxiliary_json"
        return info

    if ext in PDF_EXTS:
        try:
            with path.open("rb") as fh:
                hdr = fh.read(1024)
            if b"%PDF-" not in hdr:
                info["role"] = "corrupt_or_unreadable"
                info["warning"] = "corrupt_or_non_pdf_header"
                return info
        except Exception as exc:
            info["role"] = "corrupt_or_unreadable"
            info["warning"] = f"unreadable_pdf:{exc}"
            return info
        if any(w in rel_low for w in ("draft", "manuscript", "稿件", "初稿", "thesis", "paper_to_edit")):
            info["role"] = "manuscript"
        elif any(w in rel_low for w in ("guide", "style", "规范", "要求")):
            info["role"] = "style_guide"
        else:
            info["role"] = "evidence_paper"
        return info

    if ext in DOC_EXTS:
        if ext == ".docx":
            try:
                if not zipfile.is_zipfile(path):
                    info["role"] = "corrupt_or_unreadable"
                    info["warning"] = "corrupt_docx_not_zip"
                    return info
                with zipfile.ZipFile(path, "r") as zf:
                    if "word/document.xml" not in zf.namelist():
                        info["role"] = "corrupt_or_unreadable"
                        info["warning"] = "corrupt_docx_missing_document_xml"
                        return info
            except Exception as exc:
                info["role"] = "corrupt_or_unreadable"
                info["warning"] = f"corrupt_docx:{exc}"
                return info
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

        parent_parts = [pt.lower() for pt in Path(rel).parts[:-1]]
        in_evidence_dir = any(pt in ("papers", "evidence", "sources", "fulltexts", "source_papers", "pdfs") for pt in parent_parts)
        if ext == ".txt" and _looks_like_cnki_or_ris_txt(txt):
            info["role"] = "reference_library"
            info["format_hint"] = "tagged_txt"
        elif any(w in rel_low for w in ("guide", "style_spec", "author_guide", "投稿须知", "格式规范")):
            info["role"] = "style_guide"
        elif in_evidence_dir or any(w in rel_low for w in ("evidence", "source_paper", "fulltext", "abstract", "pdf_text", "原文", "证据")):
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


def _match_style_in_text(text: str) -> Optional[Tuple[str, str]]:
    low = (text or "").lower()
    if not low.strip():
        return None
    if "gbt7714_2015_author_year" in low:
        return "gbt7714_2015_author_year", "author_year"
    if "gbt7714_2015_numeric" in low:
        return "gbt7714_2015_numeric", "superscript_bracket" if any(k in low for k in ("上标", "superscript")) else "bracket"
    if any(k in low for k in ("著者—出版年", "著者-出版年", "著者出版年", "author-year", "author year", "author_year")) and (
        "7714" in low or "gb/t" in low or "国标" in low
    ):
        return "gbt7714_2015_author_year", "author_year"
    if any(k in low for k in ("7714", "gb/t", "顺序编码")):
        return "gbt7714_2015_numeric", "superscript_bracket" if any(k in low for k in ("上标", "superscript")) else "bracket"
    if any(k in low for k in ("apa 7", "apa7", "apa 7th", "apa第7", "apa第七")):
        return "apa7", "author_year"
    if any(k in low for k in ("apa 6", "apa6", "apa 6th", "apa第6", "apa第六")):
        return "apa6", "author_year"
    if re.search(r"\bapa\b", low):
        return "apa7", "author_year"
    if re.search(r"\bieee\b", low):
        return "ieee", "bracket_separate"
    if any(k in low for k in ("nature style", "nature format", "nature 期刊", "《nature》")) or low.strip() == "nature":
        return "nature", "superscript_html"
    if "vancouver" in low or "icmje" in low:
        return "vancouver", "bracket"
    if "chicago" in low:
        return "chicago_author_date", "author_year"
    if any(k in low for k in ("著者—出版年", "著者-出版年", "author-year", "author-date")):
        return "gbt7714_2015_author_year", "author_year"
    return None


def detect_task_signals(task_text: str, case_data: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
    combined = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    low = combined.lower()

    target_style = "gbt7714_2015_numeric"
    in_text_format = "superscript_bracket"
    matched_style: Optional[Tuple[str, str]] = None

    # 1. Explicit structured case.json fields take highest precedence
    for key in ("target_style", "citation_style", "reference_style", "output_style", "style"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            matched_style = _match_style_in_text(val)
            if matched_style:
                break

    # 2. Directional target phrases ("转换为 GB/T 7714", "convert to APA 7th") before generic mentions
    if not matched_style:
        dir_re = re.compile(
            r"(?:转换为|转为|改为|改成|统一为|输出为|格式化为|采用|遵循|按照|按|符合|目标格式|target\s+style|convert(?:ed)?\s+to|"
            r"reformat(?:ted)?\s+(?:in|as|to)|format(?:ted)?\s+(?:in|as|to)|follow(?:ing)?|output\s+style)"
            r"\s*[:：]?\s*([^\n。；;]{1,80})",
            re.I,
        )
        for m in dir_re.finditer(combined):
            hit = _match_style_in_text(m.group(1))
            if hit:
                matched_style = hit
                break

    # 3. Fallback full-text detection
    if not matched_style:
        matched_style = _match_style_in_text(combined)

    if matched_style:
        target_style, in_text_format = matched_style

    # Explicit structured in_text_format override if present in case.json
    explicit_itf = case_data.get("in_text_format")
    if isinstance(explicit_itf, str) and explicit_itf.strip():
        in_text_format = explicit_itf.strip().lower()
    else:
        has_tex_input = any(f["ext"] == ".tex" for f in files)
        explicit_non_tex_style = any(k in low for k in ("7714", "gb/t", "apa", "上标", "superscript", "顺序编码", "著者—出版年", "著者-出版年"))
        footnote_style_cues = (
            "脚注引文", "脚注引用格式", "脚注形式引用", "脚注编号引用", "使用脚注引用",
            "footnote citation", "footnote style", "footnote-style", "notes and bibliography",
        )
        if any(k in low for k in ("\\cite{", "latex", ".tex")) and (
            has_tex_input or "\\cite{" in low or ("latex" in low and not explicit_non_tex_style)
        ):
            in_text_format = "latex_cite"
        elif any(k in low for k in footnote_style_cues):
            in_text_format = "footnote"
        elif any(k in low for k in ("上标", "superscript")) and target_style in ("gbt7714_2015_numeric", "nature", "vancouver"):
            in_text_format = "superscript_bracket"
        elif any(k in low for k in ("行内方括号", "非上标", "inline bracket")) and target_style in ("gbt7714_2015_numeric", "ieee", "vancouver"):
            in_text_format = "bracket"

    requested_formats = ["markdown", "json", "bib", "ris", "csl_json", "csv"]
    if any(f["ext"] == ".docx" for f in files) or any(
        k in low for k in (".docx", "docx", "word文档", "word版", "word 版本", "word format", "word document", "ms word", "microsoft word")
    ):
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
        ("body_bibliography_bidirectional_sync", ("双向", "漏引", "悬空", "正文与参考文献", "正文和参考文献", "dangling", "uncited", "bidirectional", "1-to-1")),
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
        low_norm = c_norm.lower()
        base = Path(c_norm).name.lower()
        if (
            base in CONTROL_FILES
            or base in EXPECTED_OUTPUT_FILES
            or low_norm.startswith(("app/output/", "/app/output/", "app/work/", "/app/work/", "output/"))
        ):
            continue
        if low_norm not in disc_rels and base not in disc_names and not (input_dir / c_norm).exists():
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
    task_candidates = [input_dir / "task.md"]
    if str(input_dir) == "/input":
        task_candidates.append(Path("task.md"))
    for tp in task_candidates:
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
