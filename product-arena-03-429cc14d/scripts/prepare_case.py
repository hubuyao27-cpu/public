#!/usr/bin/env python3
"""Step 1: Inspect case, parse manuscripts & libraries, index local evidence, and save early partial checkpoint."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    CJK_RE,
    detect_script_lang,
    extract_docx_structured,
    extract_numbers,
    extract_pdf_text_safe,
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    parse_author_name,
    posix_rel,
    read_text_safe,
    save_json,
    save_text,
    title_similarity,
)
from library_engine import (
    deduplicate_records,
    parse_embedded_references,
    parse_library_file,
)

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md"}
LIB_EXTS = {".bib", ".ris", ".enw", ".csl"}
DOC_EXTS = {".md", ".markdown", ".txt", ".docx", ".tex", ".html", ".htm"}
TABLE_EXTS = {".csv", ".tsv"}
BINARY_IGNORE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".tar", ".gz", ".exe", ".dll", ".so"}

REF_HEADER_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:参考文献|主要参考文献|references|bibliography|works\s+cited|literature\s+cited)\s*[:：]?\s*$",
    re.I,
)
APPENDIX_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?(?:appendix|appendices|附录|补充材料)\b", re.I)
FIG_CAPTION_RE = re.compile(r"^(?:\*\*)?(?:fig(?:ure)?\.?|图)\s*[A-Za-z0-9.\-]+", re.I)
TAB_CAPTION_RE = re.compile(r"^(?:\*\*)?(?:table|tab\.?|表)\s*[A-Za-z0-9.\-]+", re.I)

RE_PLACEHOLDER = re.compile(r"(\[(?:REF|CITE|CITATION(?:\s+NEEDED)?|\?|待引用|待补引|引用|未核验引用[^\]]*|TODO[^\]]*)\])", re.I)
RE_LATEX_CITE = re.compile(r"\\(?:cite|citep|citet|parencite|textcite|autocite|supercite)(?:\[[^\]]*\])?(?:\[[^\]]*\])?\{([^}]+)\}")
RE_SUP_HTML = re.compile(r"<sup>\s*(\[?)([0-9,\s\-–—]+)(\]?)\s*</sup>", re.I)
RE_SUP_MD = re.compile(r"\^\[([0-9,\s\-–—;]+)\]")
RE_SUP_UNI = re.compile(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[⁻,][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*)")
RE_IEEE_RANGE = re.compile(r"(?<![\]\^\[])\[(\d+)\]\s*[-–—]\s*\[(\d+)\](?!\()")
RE_BRACKET_NUM = re.compile(r"(?<![\]\^\[])\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()")

UNICODE_SUP_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")

_AY_PREFIX_OPT = r"(?:(?:e\.g\.|i\.e\.|cf\.|see\s+also|see|见|参见|如)\s*[,，]?\s*)?"
_AY_PARTICLE = r"(?:(?:van\s+der|van\s+den|de\s+la|van|von|de|del|da|di|la|le|du)\s+)?"
_AY_SINGLE = (
    rf"{_AY_PREFIX_OPT}"
    rf"(?:{_AY_PARTICLE}[A-Z][A-Za-z.\-']*(?:\s+(?:{_AY_PARTICLE}[A-Z][A-Za-z.\-']*|&|and|et\s+al\.))*"
    rf"|[\u4e00-\u9fff]{{2,6}}(?:等|(?:和|与|、)[\u4e00-\u9fff]{{2,6}})*)"
    rf"\s*[,，]\s*(?:(?:19|20)\d{{2}}[a-z]?(?:\s*[,，]\s*(?:19|20)\d{{2}}[a-z]?)*)"
    rf"(?:\s*[,，:]\s*(?:pp?\.\s*\d+(?:[-–—]\d+)?|第\s*\d+(?:[-–—]\d+)?\s*页))?"
)
RE_AUTHOR_YEAR_PAREN = re.compile(rf"[（(]\s*({_AY_SINGLE}(?:\s*[;；]\s*{_AY_SINGLE})*)\s*[)）]")
RE_AUTHOR_YEAR_NARR = re.compile(
    rf"\b({_AY_PARTICLE}[A-Z][A-Za-z'\-]+(?:\s+(?:and|&)\s+{_AY_PARTICLE}[A-Z][A-Za-z'\-]+|\s+et\s+al\.)?)\s*\(((?:19|20)\d{{2}}[a-z]?)\)"
    r"|([\u4e00-\u9fff]{2,4}(?:等|(?:和|与)[\u4e00-\u9fff]{2,4})?)\s*[（(]((?:19|20)\d{2}[a-z]?)[)）]"
)

NON_AUTHOR_STOP = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "table", "figure", "fig", "section", "chapter",
    "equation", "model", "version", "dataset", "benchmark", "imagenet", "bert", "gpt",
    "llama", "resnet", "transformer", "standard", "policy", "guideline", "report", "release",
    "update", "since", "until", "from", "in", "by", "as", "the", "this", "that", "these",
    "those", "phase", "stage", "period", "year", "edition", "series", "project", "program",
    "截至目前", "目前", "至今", "模型版本", "版本", "模型", "数据集", "数据", "方法", "标准",
    "指南", "政策", "实验", "结果", "如图", "如表", "见图", "见表", "附录", "国家标准",
}
_SCI_UNIT_PREFIX_RE = re.compile(
    r"(?:10|[0-9]|[×xX·\-−+±]|(?:^|[^A-Za-z])(?:m|cm|mm|km|nm|um|μm|kg|mg|g|s|ms|ns|h|K|L|mL|mol|Pa|Hz|eV|W|V|A|R|r|χ|N|n|x|y|z|e))$",
)
ATTRIBUTION_CUE_RE = re.compile(
    r"(?:previous\s+(?:work|stud(?:y|ies))|prior\s+(?:research|work)|it\s+has\s+been\s+shown|studies\s+(?:show|demonstrate|report)|"
    r"proposed\s+by|introduced\s+by|according\s+to|reported\s+that|outperform(?:s|ed)?|benchmark|"
    r"研究表明|已有研究|前人研究|文献报道|文献指出|提出了一种|相比(?:现有|传统|基线)|显著优于|准确率(?:达到|为)|p\s*[<≤=]\s*0\.\d)",
    re.I,
)

_SHORTHAND_TYPES = {
    "string": "string", "str": "string", "text": "string",
    "array": "array", "list": "array",
    "object": "object", "dict": "object", "map": "object",
    "boolean": "boolean", "bool": "boolean",
    "integer": "integer", "int": "integer",
    "number": "number", "float": "number",
}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    fn = field_name.lower().strip()
    k = f"{fn} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if fn.endswith(("_path", "_paths", "_file", "_files")) or any(w in fn for w in ("deliverable", "artifact", "preview", "output_file")):
        return "deliverable_files"
    if any(w in k for w in ("revised_manuscript", "manuscript", "revised_text", "revised_draft", "draft_text", "edited_text", "output_text")):
        return "revised_manuscript"
    if any(w in k for w in ("reference_list", "bibliography", "references_formatted", "formatted_references", "citations_list", "reference_entries", "references")):
        return "reference_list"
    if any(w in k for w in ("renumber", "mapping", "cross_ref", "citation_map", "in_text_map", "order_map")):
        return "renumbering_map"
    if any(w in k for w in ("dedup", "merge", "duplicate", "cluster")):
        return "dedup_report"
    if any(w in k for w in ("claim_verif", "verification", "support", "alignment", "consistency", "audit", "source_check")):
        return "claim_verification"
    if any(w in k for w in ("provenance", "field_source", "evidence_map", "source_map", "sources")):
        return "field_provenance"
    if any(w in k for w in ("change", "diff", "revision", "edit_log", "modification")):
        return "change_records"
    if any(w in k for w in ("confirm", "question", "gap", "missing", "manual_confirm", "human_confirm")):
        return "human_confirmation_items"
    if any(w in k for w in ("uncertain", "unknown", "warning", "risk", "limitation", "error")):
        return "uncertainty"
    if any(w in k for w in ("summary", "overview", "report")):
        return "summary"
    return "custom"


def _infer_type_from_val(val: Any) -> Tuple[str, str]:
    if isinstance(val, str):
        low = val.strip().lower()
        return (_SHORTHAND_TYPES[low], "") if low in _SHORTHAND_TYPES else ("string", val)
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
                ftype = _SHORTHAND_TYPES.get(str(raw_t).strip().lower(), str(raw_t)) if raw_t else _infer_type_from_val(spec)[0]
                desc = str(sd.get("description", ""))
                fields.append({
                    "name": k,
                    "expected_type": ftype,
                    "required": (k in req_set) if req_set else True,
                    "description": desc,
                    "semantic_role": infer_semantic_role(k, desc),
                })
            return {"raw_type": "json_schema", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}
        if "fields" in contract and isinstance(contract["fields"], (dict, list)):
            return parse_output_contract(contract["fields"])
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        meta_keys = {"$schema", "additionalProperties", "type", "description", "title", "format", "required"} if ("$schema" in contract or req_list is not None) else set()
        for k, val in contract.items():
            if k in meta_keys and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                ftype = _SHORTHAND_TYPES.get(str(val.get("type", "object")).strip().lower(), "object")
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype, desc = _infer_type_from_val(val)
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
            elif isinstance(item, dict) and item.get("name"):
                nm = str(item["name"])
                et = _SHORTHAND_TYPES.get(str(item.get("type", "any")).lower(), str(item.get("type", "any")))
                desc = str(item.get("description", ""))
                fields.append({"name": nm, "expected_type": et, "required": bool(item.get("required", True)), "description": desc, "semantic_role": infer_semantic_role(nm, desc)})
        return {"raw_type": "list_fields", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}
    return {"raw_type": "unknown", "fields": [], "required_keys": []}


def expand_numeric_spec(spec: str) -> List[int]:
    s = spec.replace("–", "-").replace("—", "-").replace("~", "-").replace(";", ",")
    nums: List[int] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a_s, b_s = [x.strip() for x in p.split("-", 1)]
            if a_s.isdigit() and b_s.isdigit():
                a, b = int(a_s), int(b_s)
                if a <= b and (b - a) <= 100:
                    nums.extend(range(a, b + 1))
                else:
                    nums.extend([a, b])
        elif p.isdigit():
            nums.append(int(p))
    return nums


def filter_valid_cite_nums(nums: List[int]) -> List[int]:
    if not nums or any(n <= 0 or n >= 1900 or n > 500 for n in nums):
        return []
    return nums


def mask_code_and_math_spans(text: str) -> List[Tuple[int, int]]:
    """Return protected [start, end) spans for inline code `...` and math $...$ / $$...$$."""
    spans: List[Tuple[int, int]] = []
    for pat in (r"```[\s\S]*?```", r"`[^`\n]+`", r"\$\$[\s\S]*?\$\$", r"(?<!\\)\$[^$\n]+\$"):
        for m in re.finditer(pat, text):
            spans.append((m.start(), m.end()))
    return spans


def _in_spans(a: int, b: int, spans: List[Tuple[int, int]]) -> bool:
    return any(not (b <= sa or a >= sb) for sa, sb in spans)


def _expand_ay_items(inner: str) -> List[str]:
    items: List[str] = []
    for raw_seg in re.split(r"[;；]", inner):
        seg = re.sub(r"^(?:e\.g\.|i\.e\.|cf\.|see\s+also|see|见|参见|如)\s*[,，]?\s*", "", raw_seg.strip(), flags=re.I).strip()
        seg = re.sub(r"\s*[,，:]\s*(?:pp?\.\s*\d+(?:[-–—]\d+)?|第\s*\d+(?:[-–—]\d+)?\s*页)\s*$", "", seg, flags=re.I).strip()
        m_yr = re.search(r"[,，]\s*((?:19|20)\d{2}[a-z]?(?:\s*[,，]\s*(?:19|20)\d{2}[a-z]?)*)$", seg)
        if m_yr:
            author_part = seg[: m_yr.start()].strip()
            for yr in re.findall(r"(?:19|20)\d{2}[a-z]?", m_yr.group(1)):
                items.append(f"{author_part}, {yr}")
        elif seg:
            items.append(seg)
    return items


def extract_citations_from_sentence(sent: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    occupied = mask_code_and_math_spans(sent)

    def _add(hit: Dict[str, Any]) -> None:
        a, b = hit["span"]
        hits.append(hit)
        occupied.append((a, b))

    for m in RE_PLACEHOLDER.finditer(sent):
        if not _in_spans(m.start(), m.end(), occupied):
            _add({"kind": "placeholder", "raw": m.group(1), "span": [m.start(), m.end()], "nums": [], "keys": [], "author_years": []})

    for m in RE_LATEX_CITE.finditer(sent):
        if not _in_spans(m.start(), m.end(), occupied):
            keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
            _add({"kind": "latex_cite", "raw": m.group(0), "span": [m.start(), m.end()], "nums": [], "keys": keys, "author_years": []})

    for m in RE_SUP_HTML.finditer(sent):
        if _in_spans(m.start(), m.end(), occupied):
            continue
        has_brackets = bool(m.group(1) and m.group(3))
        if not has_brackets and _SCI_UNIT_PREFIX_RE.search(sent[max(0, m.start() - 12) : m.start()]):
            continue
        nums = filter_valid_cite_nums(expand_numeric_spec(m.group(2)))
        if nums:
            _add({"kind": "numeric_superscript_html", "raw": m.group(0), "span": [m.start(), m.end()], "nums": nums, "keys": [], "author_years": []})

    for m in RE_SUP_MD.finditer(sent):
        if not _in_spans(m.start(), m.end(), occupied):
            nums = filter_valid_cite_nums(expand_numeric_spec(m.group(1)))
            if nums:
                _add({"kind": "numeric_superscript_md", "raw": m.group(0), "span": [m.start(), m.end()], "nums": nums, "keys": [], "author_years": []})

    for m in RE_IEEE_RANGE.finditer(sent):
        if not _in_spans(m.start(), m.end(), occupied):
            a_n, b_n = int(m.group(1)), int(m.group(2))
            nums = filter_valid_cite_nums(list(range(a_n, b_n + 1)) if a_n <= b_n and (b_n - a_n) <= 100 else [a_n, b_n])
            if nums:
                _add({"kind": "numeric_bracket", "raw": m.group(0), "span": [m.start(), m.end()], "nums": nums, "keys": [], "author_years": []})

    for m in RE_BRACKET_NUM.finditer(sent):
        if _in_spans(m.start(), m.end(), occupied):
            continue
        raw_spec = m.group(1)
        if re.search(r"\b(?:19|20)\d{2}\s*[-–—]\s*\d{2}\s*[-–—]\s*\d{2}\b", raw_spec):
            continue
        nums = filter_valid_cite_nums(expand_numeric_spec(raw_spec))
        if nums:
            _add({"kind": "numeric_bracket", "raw": m.group(0), "span": [m.start(), m.end()], "nums": nums, "keys": [], "author_years": []})

    for m in RE_SUP_UNI.finditer(sent):
        if _in_spans(m.start(), m.end(), occupied) or m.group(1).startswith("⁻"):
            continue
        if _SCI_UNIT_PREFIX_RE.search(sent[max(0, m.start() - 12) : m.start()]):
            continue
        nums = filter_valid_cite_nums(expand_numeric_spec(m.group(1).translate(UNICODE_SUP_MAP)))
        if nums:
            _add({"kind": "numeric_superscript_unicode", "raw": m.group(0), "span": [m.start(), m.end()], "nums": nums, "keys": [], "author_years": []})

    for m in RE_AUTHOR_YEAR_PAREN.finditer(sent):
        if not _in_spans(m.start(), m.end(), occupied):
            ays = _expand_ay_items(m.group(1))
            if ays:
                _add({"kind": "author_year_paren", "raw": m.group(0), "span": [m.start(), m.end()], "nums": [], "keys": [], "author_years": ays})

    for m in RE_AUTHOR_YEAR_NARR.finditer(sent):
        if _in_spans(m.start(), m.end(), occupied):
            continue
        author = (m.group(1) or m.group(3) or "").strip()
        year = (m.group(2) or m.group(4) or "").strip()
        if not author or not year or author.lower() in NON_AUTHOR_STOP:
            continue
        is_bare = ("et al" not in author.lower() and "等" not in author and "and" not in author.lower() and "&" not in author and "和" not in author and "与" not in author)
        _add({"kind": "author_year_narrative", "raw": m.group(0), "span": [m.start(), m.end()], "nums": [], "keys": [], "author_years": [f"{author}, {year}"], "is_bare_narrative": is_bare})

    hits.sort(key=lambda h: h["span"][0])
    return hits


def split_sentences(text: str) -> List[str]:
    if not text.strip():
        return []
    # Protect abbreviations and decimals briefly using safe sentinel replacement
    protected = text
    sents: List[str] = []
    buf = ""
    i = 0
    n = len(protected)
    in_math = False
    while i < n:
        ch = protected[i]
        buf += ch
        if ch == "$" and (i == 0 or protected[i - 1] != "\\"):
            in_math = not in_math
        if not in_math:
            if ch in "。！？":
                # Include trailing citation marker attached after period if any (e.g., 。[1])
                j = i + 1
                while j < n and protected[j] in " \t" and j + 1 < n and protected[j + 1] in "[<^":
                    j += 1
                if j < n and protected[j : j + 5].startswith("<sup>"):
                    end_sup = protected.find("</sup>", j)
                    if end_sup != -1:
                        buf += protected[i + 1 : end_sup + 6]
                        i = end_sup + 5
                elif j < n and protected[j] == "[":
                    end_br = protected.find("]", j)
                    if end_br != -1 and end_br - j <= 20:
                        buf += protected[i + 1 : end_br + 1]
                        i = end_br
                sents.append(buf.strip())
                buf = ""
            elif ch in ".!?" and i + 1 < n and protected[i + 1].isspace():
                tail = buf.strip()
                # Guard common academic abbreviations and decimal numbers
                if not re.search(r"(?:\b(?:et al|e\.g|i\.e|vs|Dr|Prof|Mr|Ms|Fig|Eq|Tab|Vol|No|pp|cf|approx|al|St)\.|^\d+\.|\d+\.\d+)$", tail, re.I):
                    # Check next non-space char looks like sentence start or quote or digit
                    rest = protected[i + 1 :].lstrip()
                    if not rest or rest[0].isupper() or rest[0] in "\"'“‘([" or CJK_RE.match(rest[0]):
                        sents.append(tail)
                        buf = ""
        i += 1
    if buf.strip():
        sents.append(buf.strip())
    return [s for s in sents if s]


def _parse_markdown_or_text_blocks(raw_text: str, source_file: str, is_tex: bool = False) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse Markdown/TXT/LaTeX into lossless blocks while splitting off trailing Reference section."""
    lines = raw_text.splitlines()
    ref_start_idx: Optional[int] = None
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if REF_HEADER_RE.match(s) or (is_tex and re.match(r"\\(?:section\*?\{(?:References|参考文献|Bibliography)\}|begin\{thebibliography\})", s, re.I)):
            ref_start_idx = idx
            break

    body_lines = lines[:ref_start_idx] if ref_start_idx is not None else lines
    ref_lines = lines[ref_start_idx + 1 :] if ref_start_idx is not None else []
    if is_tex and ref_lines:
        ref_lines = [ln for ln in ref_lines if not re.match(r"\\end\{(?:thebibliography|document)\}", ln.strip())]

    blocks: List[Dict[str, Any]] = []
    cur_section = "Preamble"
    in_appendix = False
    i = 0
    n = len(body_lines)
    while i < n:
        ln = body_lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        # Fenced code block
        if s.startswith("```"):
            buf = [ln]
            i += 1
            while i < n:
                buf.append(body_lines[i])
                if body_lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            blocks.append({"block_type": "code_block", "section": cur_section, "text": "\n".join(buf)})
            continue
        # Display math block $$...$$ or LaTeX equation environment
        if s.startswith("$$") or (is_tex and re.match(r"\\begin\{(?:equation|align|gather|multline)\*?\}", s)):
            end_tok = "$$" if s.startswith("$$") else "\\end{"
            buf = [ln]
            if not (s.startswith("$$") and len(s) > 4 and s.endswith("$$")):
                i += 1
                while i < n:
                    buf.append(body_lines[i])
                    if (end_tok == "$$" and body_lines[i].strip().endswith("$$")) or (end_tok != "$$" and end_tok in body_lines[i]):
                        i += 1
                        break
                    i += 1
            else:
                i += 1
            blocks.append({"block_type": "math_block", "section": cur_section, "text": "\n".join(buf)})
            continue
        # Markdown heading or LaTeX section
        m_md_h = re.match(r"^(#{1,4})\s+(.+)$", s)
        m_tex_h = re.match(r"^\\((?:sub)*section)\*?\{(.+)\}\s*$", s) if is_tex else None
        if m_md_h or m_tex_h:
            if m_md_h:
                lvl = len(m_md_h.group(1))
                title = m_md_h.group(2).strip()
            else:
                cmd = m_tex_h.group(1)
                lvl = 1 + cmd.count("sub")
                title = m_tex_h.group(2).strip()
            cur_section = title
            if APPENDIX_HEADER_RE.match(s) or "appendix" in title.lower() or "附录" in title:
                in_appendix = True
            blocks.append({"block_type": "heading", "heading_level": lvl, "section": cur_section, "text": ln})
            i += 1
            continue
        # LaTeX preamble / document wrapper lines
        if is_tex and (s.startswith("\\documentclass") or s.startswith("\\usepackage") or s in ("\\begin{document}", "\\end{document}") or s.startswith("\\title{") or s.startswith("\\author{") or s.startswith("\\maketitle") or s.startswith("\\bibliographystyle")):
            blocks.append({"block_type": "tex_meta", "section": cur_section, "text": ln})
            i += 1
            continue
        # Markdown table
        if s.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|\-]+\|$", body_lines[i + 1].strip()):
            tbuf = [ln, body_lines[i + 1]]
            i += 2
            while i < n and body_lines[i].strip().startswith("|"):
                tbuf.append(body_lines[i])
                i += 1
            blocks.append({"block_type": "table", "section": cur_section, "text": "\n".join(tbuf)})
            continue
        # Footnote definition
        if re.match(r"^\[\^[^\]]+\]:\s*", s):
            blocks.append({"block_type": "footnote", "section": cur_section, "text": ln})
            i += 1
            continue
        # List item (preserve bullet prefix cleanly)
        m_li = re.match(r"^(\s*(?:[-*+]|\d+\.)\s+)(.+)$", ln)
        if m_li:
            blocks.append({"block_type": "list_item", "list_prefix": m_li.group(1), "section": cur_section, "text": m_li.group(2).strip()})
            i += 1
            continue
        # Blockquote
        if s.startswith(">"):
            qbuf = [ln]
            i += 1
            while i < n and body_lines[i].strip().startswith(">"):
                qbuf.append(body_lines[i])
                i += 1
            blocks.append({"block_type": "blockquote", "section": cur_section, "text": "\n".join(qbuf)})
            continue

        # Standard paragraph (collect consecutive non-special lines)
        pbuf = [ln]
        i += 1
        while i < n:
            nxt = body_lines[i].strip()
            if not nxt or nxt.startswith(("#", "```", "$$", ">", "|")) or re.match(r"^\[\^[^\]]+\]:", nxt) or re.match(r"^(?:[-*+]|\d+\.)\s+", nxt):
                break
            if is_tex and (nxt.startswith("\\section") or nxt.startswith("\\subsection") or nxt.startswith("\\begin{") or nxt in ("\\end{document}",)):
                break
            pbuf.append(body_lines[i])
            i += 1
        para_txt = " ".join(x.strip() for x in pbuf).strip()
        btype = "appendix" if in_appendix else "body"
        if FIG_CAPTION_RE.match(para_txt) or (is_tex and "\\caption{" in para_txt and "figure" in para_txt.lower()):
            btype = "figure_caption"
        elif TAB_CAPTION_RE.match(para_txt) or (is_tex and "\\caption{" in para_txt and "table" in para_txt.lower()):
            btype = "table_caption"
        blocks.append({"block_type": btype, "section": cur_section, "text": para_txt})

    return blocks, ref_lines


def _analyze_blocks(raw_blocks: List[Dict[str, Any]], source_file: str, primary_format: str) -> Dict[str, Any]:
    sentences: List[Dict[str, Any]] = []
    blocks_out: List[Dict[str, Any]] = []
    dump_flags: List[Dict[str, Any]] = []
    uncited_claim_candidates: List[Dict[str, Any]] = []
    s_counter = 0

    for b_idx, blk in enumerate(raw_blocks, start=1):
        btype = blk.get("block_type", "body")
        sec = blk.get("section", "Preamble")
        txt = blk.get("text", "")
        if btype in ("heading", "code_block", "math_block", "tex_meta"):
            blocks_out.append({**blk, "block_id": f"B{b_idx:03d}", "sentence_ids": []})
            continue
        if btype == "table":
            # Keep table structure, split non-separator cell prose into sentences by row
            lines = txt.splitlines()
            row_sids: List[str] = []
            row_map: List[Dict[str, Any]] = []
            for r_i, r_ln in enumerate(lines):
                if r_i <= 1 or re.match(r"^\|[\s:|\-]+\|$", r_ln.strip()):
                    row_map.append({"raw": r_ln, "sentence_ids": []})
                    continue
                s_counter += 1
                sid = f"S{s_counter:03d}"
                cites = extract_citations_from_sentence(r_ln)
                sentences.append({
                    "sentence_id": sid,
                    "block_id": f"B{b_idx:03d}",
                    "block_type": "table_cell",
                    "section": sec,
                    "text": r_ln,
                    "citations": cites,
                    "has_citation": bool(cites),
                    "has_placeholder": any(c["kind"] == "placeholder" for c in cites),
                })
                row_sids.append(sid)
                row_map.append({"raw": r_ln, "sentence_ids": [sid]})
            blocks_out.append({**blk, "block_id": f"B{b_idx:03d}", "sentence_ids": row_sids, "table_rows": row_map})
            continue

        # Footnote prefix handling
        fn_prefix = ""
        body_for_split = txt
        if btype == "footnote":
            m_fn = re.match(r"^(\[\^[^\]]+\]:\s*)(.*)$", txt)
            if m_fn:
                fn_prefix, body_for_split = m_fn.group(1), m_fn.group(2)

        sents_txt = split_sentences(body_for_split) if body_for_split.strip() else []
        b_sids: List[str] = []
        for s_txt in sents_txt:
            s_counter += 1
            sid = f"S{s_counter:03d}"
            cites = extract_citations_from_sentence(s_txt)
            has_ph = any(c["kind"] == "placeholder" for c in cites)
            has_real_cite = any(c["kind"] != "placeholder" for c in cites)
            sentences.append({
                "sentence_id": sid,
                "block_id": f"B{b_idx:03d}",
                "block_type": btype,
                "section": sec,
                "text": s_txt,
                "citations": cites,
                "has_citation": bool(cites),
                "has_placeholder": has_ph,
            })
            b_sids.append(sid)
            if not cites and btype in ("body", "list_item", "appendix") and (ATTRIBUTION_CUE_RE.search(s_txt) or re.search(r"\d+(?:\.\d+)?%", s_txt)):
                uncited_claim_candidates.append({"sentence_id": sid, "section": sec, "text": s_txt, "reason": "factual_or_quantitative_claim_without_citation"})

        # Detect paragraph-end citation dumps
        if len(b_sids) >= 2:
            b_sents = [s for s in sentences[-len(b_sids):]]
            if all(not s["has_citation"] for s in b_sents[:-1]):
                last_cites = [c for c in b_sents[-1]["citations"] if c["kind"] != "placeholder"]
                total_refs = sum(len(c.get("nums") or c.get("keys") or c.get("author_years") or [1]) for c in last_cites)
                if total_refs >= 2:
                    dump_flags.append({
                        "block_id": f"B{b_idx:03d}",
                        "section": sec,
                        "final_sentence_id": b_sids[-1],
                        "prior_sentence_ids": b_sids[:-1],
                        "reason": f"Paragraph stacks {total_refs} citations at the final sentence while earlier {len(b_sids)-1} sentences have zero citations.",
                    })

        blocks_out.append({**blk, "block_id": f"B{b_idx:03d}", "fn_prefix": fn_prefix, "sentence_ids": b_sids})

    return {
        "source_file": source_file,
        "primary_format": primary_format,
        "blocks": blocks_out,
        "sentences": sentences,
        "paragraph_end_dump_flags": dump_flags,
        "uncited_claim_candidates": uncited_claim_candidates,
        "stats": {
            "total_blocks": len(blocks_out),
            "total_sentences": len(sentences),
            "cited_sentences": sum(1 for s in sentences if s["has_citation"]),
            "placeholder_sentences": sum(1 for s in sentences if s["has_placeholder"]),
            "paragraph_end_dumps": len(dump_flags),
            "uncited_claim_candidates": len(uncited_claim_candidates),
        },
    }


def _detect_signals(task_text: str, case_data: Dict[str, Any], style_rules: Dict[str, Any], primary_ms_fmt: str) -> Dict[str, Any]:
    blob = f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"
    low = blob.lower()

    # Target style detection
    target_style = "gbt7714_2015_numeric"
    style_src = "inferred_default"
    for st_name, kws in style_rules.get("style_detection_keywords", {}).items():
        if any(re.search(kw, low, re.I) for kw in kws):
            target_style = st_name
            style_src = "detected_from_prompt"
            break
    if style_src == "inferred_default" and detect_script_lang(blob) == "en":
        target_style = "apa7"

    # In-text format
    st_cfg = style_rules.get("supported_styles", {}).get(target_style, {})
    in_text_format = st_cfg.get("default_in_text_format", "bracket")
    if primary_ms_fmt == "latex" or re.search(r"\\cite|\blatex\b|\.tex\b", low):
        if "latex" in low or primary_ms_fmt == "latex":
            in_text_format = "latex_cite"
    elif "上标" in blob or "superscript" in low:
        in_text_format = "superscript_html" if target_style == "nature" else "superscript_bracket"
    elif "方括号" in blob or "行内编号" in blob or "[1]" in blob:
        if target_style == "gbt7714_2015_numeric":
            in_text_format = "bracket"
    elif "脚注" in blob or "footnote" in low:
        in_text_format = "footnote"

    req_formats: List[str] = ["md"]
    if primary_ms_fmt == "docx" or re.search(r"\bdocx\b|\bword\b", low):
        req_formats.append("docx")
    if primary_ms_fmt == "latex" or re.search(r"\blatex\b|\.tex\b", low):
        req_formats.append("tex")
    for fmt_ext in ("bib", "ris", "csv", "json"):
        if re.search(rf"\b{fmt_ext}\b", low):
            req_formats.append(fmt_ext)

    lang = detect_script_lang(task_text or blob)
    return {
        "target_style": target_style,
        "style_confidence": style_src,
        "in_text_format": in_text_format,
        "language": lang,
        "requested_output_formats": list(dict.fromkeys(req_formats)),
    }


def _match_author_year(ay: str, records: List[Dict[str, Any]]) -> Tuple[List[str], str]:
    m = re.match(r"^(.+?)\s*[,，]\s*((?:19|20)\d{2})([a-z]?)$", ay.strip())
    if not m:
        return [], "unparseable_author_year"
    a_raw, yr_s = m.group(1).strip(), m.group(2)
    yr = int(yr_s)
    # Extract first author family name
    first_chunk = re.split(r"(?:\s+(?:et\s+al\.?|and|&)(?:\s+|$)|等|和|与|、)", a_raw, maxsplit=1, flags=re.I)[0].strip()
    fam = parse_author_name(first_chunk)["family"].lower()
    matches: List[str] = []
    for r in records:
        if r.get("year") != yr or not r.get("authors"):
            continue
        r_fam = parse_author_name(r["authors"][0])["family"].lower()
        if r_fam == fam or (len(fam) >= 2 and fam in r_fam):
            matches.append(r["ref_id"])
    return matches, ("matched" if len(matches) == 1 else ("ambiguous" if len(matches) > 1 else "not_found"))


def _extract_inline_materials(case_data: Dict[str, Any], task_text: str) -> Tuple[str, List[str]]:
    """Extract inline manuscript text or inline reference lists embedded inside case.json / task.md."""
    inline_ms = ""
    inline_refs: List[str] = []
    for k in ("manuscript", "manuscript_text", "draft", "draft_text", "text", "body", "article"):
        v = case_data.get(k)
        if isinstance(v, str) and len(v.strip()) > 20:
            inline_ms = v
            break
    for k in ("references", "library", "bibliography", "ref_list", "bib_text"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            inline_refs.extend(v.splitlines())
        elif isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    inline_refs.append(it)
    return inline_ms, inline_refs


def _cjk_bigrams_local(s: str) -> set:
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", s))
    if len(cjk) < 2:
        return set(cjk)
    return {cjk[i : i + 2] for i in range(len(cjk) - 1)}


def _match_evidence_for_claim(sent_text: str, rec: Dict[str, Any], evidence_papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Match a manuscript sentence against local evidence fulltext lines or record abstract verbatim."""
    primary_loc = str(rec.get("source_locator") or "")
    r_doi = str(rec.get("doi") or "")
    r_ax = str(rec.get("arxiv_id") or "")
    r_title = str(rec.get("title") or "")
    r_tnorm = normalize_title(r_title)

    snorm = normalize_title(re.sub(r"\[[^\]]*\]|\\cite[a-z]*\{[^}]*\}", " ", sent_text))
    s_words = {w for w in snorm.split() if len(w) >= 3}
    s_cjk = _cjk_bigrams_local(snorm)
    s_nums = set(re.findall(r"\d+(?:\.\d+)?%?", sent_text))

    def _score_snip(snip: str) -> float:
        nn = normalize_title(snip)
        if len(nn) < 10:
            return 0.0
        w_cand = {w for w in nn.split() if len(w) >= 3}
        c_cand = _cjk_bigrams_local(nn)
        n_cand = set(re.findall(r"\d+(?:\.\d+)?%?", snip))
        score = 0.0
        if s_words and w_cand:
            score += len(s_words & w_cand) / max(3, min(len(s_words), 10))
        if s_cjk and c_cand:
            score += len(s_cjk & c_cand) / max(3, min(len(s_cjk), 12))
        if s_nums and n_cand:
            score += 0.65 * len(s_nums & n_cand)
        return score

    matched_papers: List[Dict[str, Any]] = []
    for evp in evidence_papers:
        if evp.get("error"):
            continue
        full_norm = normalize_title(" ".join(pg.get("text", "") for pg in evp.get("pages", [])))
        full_low = " ".join(pg.get("text", "") for pg in evp.get("pages", [])).lower()
        if (
            (r_doi and (r_doi == evp.get("extracted_doi") or r_doi in full_low))
            or (r_ax and (r_ax == evp.get("extracted_arxiv") or r_ax in full_low))
            or (r_title and evp.get("extracted_title") and title_similarity(r_title, evp["extracted_title"]) >= 0.72)
            or (len(r_tnorm) >= 14 and r_tnorm[:28] in full_norm)
        ):
            matched_papers.append(evp)

    best_full = {"score": -1.0, "locator": "", "quote": ""}
    for evp in matched_papers:
        fname = evp["file"]
        for pg in evp.get("pages", []):
            pnum = pg.get("page", 1)
            raw_pg = str(pg.get("text") or "")
            for lno, raw_ln in enumerate(raw_pg.splitlines(), start=1):
                ln = re.sub(r"\s+", " ", raw_ln).strip()
                if len(ln) < 18:
                    continue
                for seg in re.split(r"(?<=[.。;；!?！？])\s+", ln):
                    seg = seg.strip()
                    if len(seg) < 12:
                        continue
                    sc = _score_snip(seg)
                    if sc > best_full["score"]:
                        best_full = {"score": sc, "locator": f"{fname}:p{pnum}_L{lno}", "quote": seg[:220]}

    if matched_papers and best_full["quote"]:
        sc = best_full["score"]
        verdict = "supported_by_local_fulltext" if sc >= 0.15 else "title_metadata_only_unverified_claim"
        return {
            "evidence_locator": best_full["locator"],
            "evidence_quote": best_full["quote"] if sc >= 0.15 else "",
            "suggested_verdict": verdict,
            "match_score": round(max(0.0, sc), 3),
            "source_scope": "local_fulltext",
        }

    abs_txt = re.sub(r"\s+", " ", str(rec.get("abstract") or "")).strip()
    if len(abs_txt) >= 12:
        best_abs = {"score": -1.0, "quote": ""}
        for seg in re.split(r"(?<=[.。;；!?！？])\s+", abs_txt):
            seg = seg.strip()
            if len(seg) < 12:
                continue
            sc = _score_snip(seg)
            if sc > best_abs["score"]:
                best_abs = {"score": sc, "quote": seg[:220]}
        if best_abs["score"] >= 0.12 and best_abs["quote"]:
            return {
                "evidence_locator": f"{primary_loc}#abstract" if primary_loc else "abstract",
                "evidence_quote": best_abs["quote"],
                "suggested_verdict": "supported_by_abstract",
                "match_score": round(best_abs["score"], 3),
                "source_scope": "abstract",
            }

    return {
        "evidence_locator": primary_loc,
        "evidence_quote": "",
        "suggested_verdict": "title_metadata_only_unverified_claim",
        "match_score": 0.0,
        "source_scope": "metadata_only",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare case, parse manuscript & libraries, index evidence, and write partial checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    in_dir = Path(args.input_dir)
    work = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    rdir = SCRIPT_DIR.parent / "resources"
    style_rules = load_json_safe(rdir / "style_rules.json", {})
    dedup_rules = load_json_safe(rdir / "dedup_rules.json", {})

    case_path = in_dir / "case.json"
    case_data = load_json_safe(case_path, {}) if case_path.exists() else {}
    task_path = in_dir / "task.md"
    if not task_path.exists() and Path("task.md").exists():
        task_path = Path("task.md")
    task_text, _, task_warn = read_text_safe(task_path) if task_path.exists() else ("", "utf-8", None)

    case_id = str(case_data.get("case_id") or case_data.get("id") or in_dir.name or "case_unknown")
    contract_parsed = parse_output_contract(case_data.get("output_contract"))

    # Discover files
    files_inv: List[Dict[str, Any]] = []
    manuscript_files: List[Dict[str, Any]] = []
    library_files: List[Dict[str, Any]] = []
    evidence_files: List[Dict[str, Any]] = []
    access_warnings: List[str] = [task_warn] if task_warn else []
    corrupt_count = 0

    if in_dir.exists():
        for p in sorted(in_dir.rglob("*")):
            if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
                continue
            rel = posix_rel(p, in_dir)
            if p.name in CONTROL_FILES or p.suffix.lower() in BINARY_IGNORE_EXTS:
                continue
            ext = p.suffix.lower()
            size = p.stat().st_size
            info = {"rel_path": rel, "abs_path": str(p), "ext": ext, "size": size}
            if size == 0:
                info["role"] = "corrupt_or_unreadable"
                access_warnings.append(f"Empty input file: {rel}")
                corrupt_count += 1
                files_inv.append(info)
                continue

            if ext in LIB_EXTS or ext in TABLE_EXTS:
                info["role"] = "reference_library"
                library_files.append(info)
            elif ext == ".xml":
                txt, _, _ = read_text_safe(p)
                info["role"] = "reference_library" if "<record" in txt.lower() else "evidence_paper"
                (library_files if info["role"] == "reference_library" else evidence_files).append(info)
            elif ext in (".json", ".jsonl"):
                txt, _, _ = read_text_safe(p)
                if any(k in txt for k in ('"title"', '"author"', '"DOI"', '"doi"', '"container-title"')):
                    info["role"] = "reference_library"
                    library_files.append(info)
                else:
                    info["role"] = "evidence_paper"
                    evidence_files.append(info)
            elif ext == ".pdf":
                pdf_res = extract_pdf_text_safe(p)
                if pdf_res.get("error"):
                    access_warnings.append(f"{rel}: {pdf_res['error']}")
                    if "encrypted" in pdf_res["error"] or "invalid_pdf_header" in pdf_res["error"]:
                        info["role"] = "corrupt_or_unreadable"
                        corrupt_count += 1
                        files_inv.append(info)
                        continue
                full_txt = "\n".join(pg["text"] for pg in pdf_res.get("pages", []))
                if re.search(r"manuscript|draft|稿件", p.name, re.I) and not any(m for m in manuscript_files):
                    info["role"] = "manuscript"
                    manuscript_files.append(info)
                else:
                    info["role"] = "evidence_paper"
                    info["pdf_parsed"] = pdf_res
                    evidence_files.append(info)
            elif ext in DOC_EXTS:
                txt, _, _ = (("", "utf-8", None) if ext == ".docx" else read_text_safe(p))
                name_low = p.name.lower()
                if any(kw in name_low for kw in ("ref", "bib", "cnki", "library", "文献", "导出")) or (
                    ext == ".txt" and ("Title-题名" in txt or "TY  -" in txt or re.match(r"^\s*\[1\]", txt))
                ):
                    info["role"] = "reference_library"
                    library_files.append(info)
                elif any(kw in name_low for kw in ("evidence", "source", "fulltext", "原文")) or ("paper" in name_low and not any(k in name_low for k in ("manuscript", "draft", "稿"))):
                    info["role"] = "evidence_paper"
                    evidence_files.append(info)
                else:
                    info["role"] = "manuscript"
                    manuscript_files.append(info)
            files_inv.append(info)

    if not manuscript_files and len(evidence_files) == 1 and evidence_files[0]["ext"] in DOC_EXTS:
        promoted = evidence_files.pop(0)
        promoted["role"] = "manuscript"
        manuscript_files.append(promoted)

    # Check missing referenced files in task.md / case.json
    missing_refs: List[str] = []
    existing_rels = {f["rel_path"].lower() for f in files_inv} | {Path(f["rel_path"]).name.lower() for f in files_inv}
    for m in re.finditer(r"`([A-Za-z0-9_.\-/]+\.(?:bib|ris|enw|xml|csv|tsv|json|md|txt|docx|tex|pdf))`", f"{task_text}\n{json.dumps(case_data, ensure_ascii=False)}"):
        cand = m.group(1)
        if cand.lower() not in existing_rels and Path(cand).name.lower() not in existing_rels and not cand.startswith("result"):
            if cand not in missing_refs:
                missing_refs.append(cand)

    # Parse primary manuscript (or inline material)
    inline_ms, inline_refs = _extract_inline_materials(case_data, task_text)
    primary_ms_fmt = "markdown"
    raw_blocks: List[Dict[str, Any]] = []
    embedded_ref_lines: List[str] = []
    ms_source = "none"
    ms_warnings: List[str] = []

    if manuscript_files:
        ms_info = manuscript_files[0]
        ms_path = Path(ms_info["abs_path"])
        ms_source = ms_info["rel_path"]
        ext = ms_info["ext"]
        if ext == ".docx":
            primary_ms_fmt = "docx"
            dres = extract_docx_structured(ms_path)
            if dres.get("error"):
                ms_warnings.append(f"{ms_source}: {dres['error']}")
                corrupt_count += 1
            else:
                # Split reference section if present in docx paragraphs
                in_refs = False
                for blk in dres["blocks"]:
                    if blk.get("block_type") == "heading" and REF_HEADER_RE.match(blk.get("text", "").strip()):
                        in_refs = True
                        continue
                    if in_refs:
                        embedded_ref_lines.append(blk.get("text", ""))
                    else:
                        raw_blocks.append(blk)
        elif ext == ".pdf":
            primary_ms_fmt = "pdf"
            pres = extract_pdf_text_safe(ms_path)
            if pres.get("error"):
                ms_warnings.append(f"{ms_source}: {pres['error']}")
            txt = "\n\n".join(pg["text"] for pg in pres.get("pages", []))
            raw_blocks, embedded_ref_lines = _parse_markdown_or_text_blocks(txt, ms_source, is_tex=False)
        else:
            primary_ms_fmt = "latex" if ext == ".tex" else ("html" if ext in (".html", ".htm") else "markdown")
            txt, _, dw = read_text_safe(ms_path)
            if dw:
                ms_warnings.append(dw)
            raw_blocks, embedded_ref_lines = _parse_markdown_or_text_blocks(txt, ms_source, is_tex=(ext == ".tex"))
    elif inline_ms:
        ms_source = "case.json:inline_manuscript"
        raw_blocks, embedded_ref_lines = _parse_markdown_or_text_blocks(inline_ms, ms_source, is_tex=("\\begin{document}" in inline_ms))

    ms_parsed = _analyze_blocks(raw_blocks, ms_source, primary_ms_fmt)
    ms_parsed["parse_warnings"] = ms_warnings
    save_json(work / "manuscript_parsed.json", ms_parsed)

    signals = _detect_signals(task_text, case_data, style_rules, primary_ms_fmt)
    case_plan = {
        "case_id": case_id,
        "input_dir": str(in_dir),
        "output_contract_parsed": contract_parsed,
        "signals": signals,
        "files": [{k: v for k, v in f.items() if k != "pdf_parsed"} for f in files_inv],
        "manuscript_files": [f["rel_path"] for f in manuscript_files],
        "library_files": [f["rel_path"] for f in library_files],
        "evidence_files": [f["rel_path"] for f in evidence_files],
        "missing_referenced_files": missing_refs,
        "access_warnings": access_warnings,
        "stats": {
            "total_material_files": len(files_inv),
            "corrupt_or_unreadable_count": corrupt_count,
            "has_inline_materials": bool(inline_ms or inline_refs),
        },
    }
    save_json(work / "case_plan.json", case_plan)

    # Parse all libraries + embedded + inline references
    raw_lib_records: List[Dict[str, Any]] = []
    lib_warnings: List[str] = []
    for lf in library_files:
        recs, w = parse_library_file(Path(lf["abs_path"]), lf["rel_path"])
        raw_lib_records.extend(recs)
        lib_warnings.extend(w)
    if embedded_ref_lines:
        recs, w = parse_embedded_references(embedded_ref_lines, f"{ms_source}#references")
        raw_lib_records.extend(recs)
        lib_warnings.extend(w)
    if inline_refs:
        recs, w = parse_embedded_references(inline_refs, "case.json#references")
        raw_lib_records.extend(recs)
        lib_warnings.extend(w)

    lib_canon = deduplicate_records(raw_lib_records, dedup_rules, style_rules)
    lib_canon["parse_warnings"] = lib_warnings
    save_json(work / "library_snapshot.json", {"case_id": case_id, "raw_records": lib_canon["raw_records"]})
    save_json(work / "library_canonical.json", lib_canon)

    # Index ALL local evidence files (PDFs + local .md/.txt/.json evidence papers)
    evidence_papers: List[Dict[str, Any]] = []
    for ef in evidence_files:
        ep = Path(ef["abs_path"])
        if ef["ext"] == ".pdf":
            pres = ef.get("pdf_parsed") or extract_pdf_text_safe(ep)
            full_t = "\n".join(pg["text"] for pg in pres.get("pages", []))
            doi_m = re.search(r"(10\.\d{4,9}/[^\s\"'<>]+)", full_t)
            ax_m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", full_t, re.I)
            first_line = next((ln.strip() for ln in full_t.splitlines() if len(ln.strip()) > 10), "")
            evidence_papers.append({
                "file": ef["rel_path"],
                "format": "pdf",
                "error": pres.get("error"),
                "extracted_doi": normalize_doi(doi_m.group(1)) if doi_m else "",
                "extracted_arxiv": normalize_arxiv(ax_m.group(1)) if ax_m else "",
                "extracted_title": first_line[:200],
                "pages": pres.get("pages", []),
            })
        else:
            txt, _, dw = read_text_safe(ep)
            doi_m = re.search(r"(10\.\d{4,9}/[^\s\"'<>]+)", txt)
            ax_m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", txt, re.I)
            first_line = next((ln.strip().lstrip("# ") for ln in txt.splitlines() if len(ln.strip()) > 8), "")
            evidence_papers.append({
                "file": ef["rel_path"],
                "format": ef["ext"].lstrip("."),
                "error": dw,
                "extracted_doi": normalize_doi(doi_m.group(1)) if doi_m else "",
                "extracted_arxiv": normalize_arxiv(ax_m.group(1)) if ax_m else "",
                "extracted_title": first_line[:200],
                "pages": [{"page": 1, "text": txt}],
            })
    save_json(work / "evidence_corpus.json", {"case_id": case_id, "evidence_papers": evidence_papers})

    # Pre-audit sentence links & dangling citations (with pre-matched verbatim evidence anchors)
    canon_recs = lib_canon["canonical_records"]
    canon_by_id = {r["ref_id"]: r for r in canon_recs}
    sent_by_id = {s["sentence_id"]: s for s in ms_parsed["sentences"]}
    num_map = lib_canon["inline_num_to_canonical_map"]
    key_map = lib_canon["key_to_canonical_map"]
    sentence_links: List[Dict[str, Any]] = []
    dangling: List[Dict[str, Any]] = []
    ambiguous_ay: List[Dict[str, Any]] = []
    ev_match_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _add_enriched_link(sid: str, s_text: str, raw_marker: str, cid: str) -> None:
        ck = (sid, cid)
        if ck not in ev_match_cache:
            rec_obj = canon_by_id.get(cid)
            if rec_obj:
                ev_match_cache[ck] = _match_evidence_for_claim(s_text, rec_obj, evidence_papers)
            else:
                ev_match_cache[ck] = {"evidence_locator": "", "evidence_quote": "", "suggested_verdict": "title_metadata_only_unverified_claim", "match_score": 0.0, "source_scope": "metadata_only"}
        em = ev_match_cache[ck]
        sentence_links.append({
            "sentence_id": sid,
            "raw_marker": raw_marker,
            "ref_id": cid,
            "evidence_locator": em["evidence_locator"],
            "evidence_quote": em["evidence_quote"],
            "suggested_verdict": em["suggested_verdict"],
            "match_score": em["match_score"],
            "source_scope": em["source_scope"],
        })

    for s in ms_parsed["sentences"]:
        for c in s["citations"]:
            if c["kind"] == "placeholder":
                continue
            if c["kind"] in ("numeric_bracket", "numeric_superscript_html", "numeric_superscript_md", "numeric_superscript_unicode"):
                for n in c["nums"]:
                    cid = num_map.get(str(n)) or key_map.get(str(n))
                    if cid:
                        _add_enriched_link(s["sentence_id"], s["text"], c["raw"], cid)
                    else:
                        dangling.append({"sentence_id": s["sentence_id"], "raw_marker": c["raw"], "missing_token": str(n), "reason": f"Numeric marker [{n}] not found in library"})
            elif c["kind"] == "latex_cite":
                for k in c["keys"]:
                    cid = key_map.get(k) or next((rv for km, rv in key_map.items() if km.lower() == k.lower()), None)
                    if cid:
                        _add_enriched_link(s["sentence_id"], s["text"], c["raw"], cid)
                    else:
                        dangling.append({"sentence_id": s["sentence_id"], "raw_marker": c["raw"], "missing_token": k, "reason": f"LaTeX key '{k}' not found in library"})
            elif c["kind"] in ("author_year_paren", "author_year_narrative"):
                for ay in c["author_years"]:
                    m_ids, status_ay = _match_author_year(ay, canon_recs)
                    if status_ay == "matched":
                        _add_enriched_link(s["sentence_id"], s["text"], c["raw"], m_ids[0])
                    elif status_ay == "ambiguous":
                        ambiguous_ay.append({"sentence_id": s["sentence_id"], "raw_marker": c["raw"], "ay_token": ay, "candidate_ref_ids": m_ids})
                    elif not (c["kind"] == "author_year_narrative" and c.get("is_bare_narrative")):
                        dangling.append({"sentence_id": s["sentence_id"], "raw_marker": c["raw"], "missing_token": ay, "reason": f"Author-Year '{ay}' not matched in library"})

    pre_audit = {
        "case_id": case_id,
        "sentence_links": sentence_links,
        "dangling_citations": dangling,
        "ambiguous_author_year": ambiguous_ay,
    }
    save_json(work / "pre_audit.json", pre_audit)

    # Compact digests for budget-aware Agent reading (including field-level provenance + evidence quotes)
    lib_lines = [f"# Library Digest (`{case_id}`)", f"- Canonical: {len(canon_recs)} | Duplicates removed: {lib_canon['stats']['duplicates_removed']} | Recycled DOI conflicts: {len(lib_canon['recycled_doi_conflicts'])} | Tier-3 ambiguous: {len(lib_canon['ambiguous_duplicate_candidates'])}", ""]
    for r in canon_recs:
        auth_s = ", ".join(r["authors"][:3]) + (" et al." if len(r["authors"]) > 3 else "")
        lib_lines.append(f"- **{r['ref_id']}** ({r.get('year') or 'n.d.'}, `{r['doc_type']}`): *{r['title']}* — {auth_s} | Venue: {r.get('venue') or '-'} | DOI: {r.get('doi') or '-'} | Keys: {r.get('source_keys')} | Orig#: {r.get('original_list_numbers')}")
        fp = r.get("field_provenance") or {}
        if fp:
            fp_parts: List[str] = []
            alt_parts: List[str] = []
            for fld in ("title", "authors", "year", "venue", "doi", "volume", "pages", "abstract"):
                meta = fp.get(fld)
                if isinstance(meta, dict) and meta.get("source_locator"):
                    fp_parts.append(f"{fld}<-{meta['source_locator']}")
                    alts = meta.get("alternate_values") or []
                    if alts:
                        alt_desc = ", ".join(f"{str(a.get('value', ''))[:30]}@{a.get('source_locator', '')}" for a in alts[:2])
                        alt_parts.append(f"{fld}:[{alt_desc}]")
            if fp_parts:
                prov_line = "  - Field provenance: " + ", ".join(fp_parts)
                if alt_parts:
                    prov_line += " | Field alternates: " + "; ".join(alt_parts)
                lib_lines.append(prov_line)
        if r.get("abstract"):
            lib_lines.append(f"  - Abstract: {r['abstract'][:350]}")
        if r.get("integrity_flags") or r.get("missing_required_fields"):
            lib_lines.append(f"  - Flags: {r.get('integrity_flags')} | Missing required: {r.get('missing_required_fields')}")
    if lib_canon["ambiguous_duplicate_candidates"]:
        lib_lines.append("\n## Tier-3 Ambiguous Duplicate Candidates (Review in `dedup_decisions`)")
        for ac in lib_canon["ambiguous_duplicate_candidates"]:
            lib_lines.append(f"- **{ac['candidate_id']}**: {ac.get('canonical_ids', ac['raw_ids'])} | sim={ac['title_similarity']} | {ac['titles']}")
    save_text(work / "library_digest.md", "\n".join(lib_lines) + "\n")

    links_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for lnk in sentence_links:
        links_by_sid.setdefault(lnk["sentence_id"], []).append(lnk)

    ms_lines = [f"# Manuscript Digest (`{case_id}`)", f"- Source: `{ms_source}` ({primary_ms_fmt}) | Sentences: {len(ms_parsed['sentences'])} | Paragraph-end dumps: {len(ms_parsed['paragraph_end_dump_flags'])} | Uncited claim candidates: {len(ms_parsed['uncited_claim_candidates'])}", ""]
    for s in ms_parsed["sentences"]:
        c_str = ", ".join(c["raw"] for c in s["citations"]) or "-"
        ms_lines.append(f"- **{s['sentence_id']}** [`{s['block_type']}` | {s['section']}]: {s['text']} *(cites: {c_str})*")
        for sl in links_by_sid.get(s["sentence_id"], []):
            if sl.get("evidence_quote"):
                ms_lines.append(f"  -> [{sl['ref_id']}] ({sl['suggested_verdict']} @ {sl['evidence_locator']}): \"{sl['evidence_quote'][:140]}\"")
    if ms_parsed["paragraph_end_dump_flags"]:
        ms_lines.append("\n## Paragraph-End Citation Dumps")
        for df in ms_parsed["paragraph_end_dump_flags"]:
            ms_lines.append(f"- **{df['block_id']}** ({df['section']}): {df['reason']} (prior: {df['prior_sentence_ids']}, final: {df['final_sentence_id']})")
    save_text(work / "manuscript_digest.md", "\n".join(ms_lines) + "\n")

    placeholder_sids = [s["sentence_id"] for s in ms_parsed["sentences"] if any(c["kind"] == "placeholder" for c in s["citations"])]
    brief_lines = [
        f"# Case Brief (`{case_id}`)",
        f"- **Target Style**: `{signals['target_style']}` (`{signals['style_confidence']}`) | **In-Text Format**: `{signals['in_text_format']}` | **Output Formats**: `{signals['requested_output_formats']}`",
        f"- **Required `output_contract` keys**: `{contract_parsed['required_keys']}`",
        f"- **Dangling citations**: {len(dangling)} | **Ambiguous Author-Year**: {len(ambiguous_ay)} | **Recycled DOI conflicts**: {len(lib_canon['recycled_doi_conflicts'])}",
        f"- **Evidence files indexed**: {[e['file'] for e in evidence_papers]}",
        "",
        "## Actionable Fix Checklist",
        f"- **Placeholder sentences needing `manuscript_edits`**: `{placeholder_sids}`",
        f"- **Paragraph-end dump blocks to split**: `{[df['block_id'] for df in ms_parsed['paragraph_end_dump_flags']]}`",
        f"- **Dangling citation sentences**: `{[(d['sentence_id'], d['raw_marker']) for d in dangling]}`",
        f"- **Tier-3 ambiguous duplicates needing `dedup_decisions`**: `{[ac['candidate_id'] for ac in lib_canon['ambiguous_duplicate_candidates']]}`",
    ]
    save_text(work / "case_brief.md", "\n".join(brief_lines) + "\n")

    pre_claims: List[Dict[str, Any]] = []
    seen_cl_pairs = set()
    for lnk in sentence_links:
        pair = (lnk["sentence_id"], lnk["ref_id"])
        if pair in seen_cl_pairs:
            continue
        seen_cl_pairs.add(pair)
        pre_claims.append({
            "claim_id": f"CL{len(pre_claims) + 1:03d}",
            "sentence_id": lnk["sentence_id"],
            "cited_ref_ids": [lnk["ref_id"]],
            "verdict": lnk.get("suggested_verdict", "title_metadata_only_unverified_claim"),
            "evidence_locator": lnk.get("evidence_locator", ""),
            "evidence_quote": lnk.get("evidence_quote", ""),
            "mismatch_detail": "",
        })
        if len(pre_claims) >= 40:
            break

    skeleton = {
        "case_id": case_id,
        "target_style": signals["target_style"],
        "in_text_format": signals["in_text_format"],
        "requested_output_formats": signals["requested_output_formats"],
        "dedup_decisions": [],
        "metadata_resolutions": [],
        "manuscript_edits": [],
        "claim_verifications": pre_claims,
        "uncertainty": [],
        "human_confirmation_items": [],
        "_review_hints": {
            "ambiguous_duplicates": [ac["candidate_id"] for ac in lib_canon["ambiguous_duplicate_candidates"]],
            "paragraph_end_dumps": [df["block_id"] for df in ms_parsed["paragraph_end_dump_flags"]],
            "dangling_citations": dangling,
            "ambiguous_author_year": ambiguous_ay,
            "uncited_claim_sentences": [u["sentence_id"] for u in ms_parsed["uncited_claim_candidates"]],
        },
    }
    save_json(work / "citation_plan_skeleton.json", skeleton)

    # Always write an early honest partial checkpoint immediately
    from verify_and_assemble import assemble_outputs
    partial_summary = assemble_outputs(work, out_dir, checkpoint="partial", run_verify=False)
    print(json.dumps({
        "status": "prepared",
        "case_id": case_id,
        "target_style": signals["target_style"],
        "in_text_format": signals["in_text_format"],
        "canonical_refs": len(canon_recs),
        "sentences": len(ms_parsed["sentences"]),
        "dangling_citations": len(dangling),
        "partial_checkpoint": partial_summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
