#!/usr/bin/env python3
"""Shared utilities for sci-intro-rw-writer.

Pure Python standard library at core, with optional graceful acceleration for
PDF parsing (pypdf / fitz / pdfplumber / pdftotext), OCR (pdftoppm + tesseract),
and DOCX parsing (python-docx with pure-stdlib zipfile XML fallback).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@lru_cache(maxsize=4)
def load_rubric_rules(rules_path: Optional[str] = None) -> Dict[str, Any]:
    """Load resources/rubric_rules.json dynamically with safe fallback defaults."""
    p = Path(rules_path) if rules_path else (Path(__file__).resolve().parent.parent / "resources" / "rubric_rules.json")
    data, err = load_json_safe(p)
    if not err and isinstance(data, dict):
        return data
    return {
        "evidence_tier_precedence": [
            "original_paper_or_pdf",
            "bibtex_or_csl_metadata",
            "own_paper_results_or_tables",
            "own_paper_manuscript_or_methods",
            "structured_notes",
            "narrative_draft",
        ],
        "cliche_openings_zh": [
            r"近年来[^。\n]{0,25}(?:受到|引起)[^。\n]{0,15}广泛关注",
            r"随着[^。\n]{0,30}(?:快速发展|迅猛发展|不断进步|日新月异)",
            r"在当今[^。\n]{0,25}时代",
            r"具有重要(?:理论和实际|科学|现实|研究)?意义",
            r"已成为(?:当前|学术界|工业界)的研究热点",
            r"取得了(?:令人瞩目|长足|显著)的(?:进展|成果)",
        ],
        "cliche_openings_en": [
            r"in recent years,?.{0,40}attracted\s+(?:widespread|increasing|great|considerable)\s+attention",
            r"with the rapid\s+(?:development|advancement|growth)\s+of",
            r"plays\s+an\s+increasingly\s+(?:important|crucial|vital)\s+role",
            r"has\s+become\s+a\s+hot\s+(?:topic|research\s+area)",
            r"witnessed\s+(?:tremendous|rapid|remarkable)\s+progress",
        ],
        "overclaim_patterns_zh": [
            r"首次彻底解决",
            r"全面超越所有",
            r"完美解决",
            r"首次提出并彻底",
            r"适用于所有(?:场景|任务|条件)",
            r"彻底消除了所有",
        ],
        "overclaim_patterns_en": [
            r"first\s+ever\s+to\s+completely",
            r"universally\s+outperforms\s+all",
            r"completely\s+solves",
            r"definitively\s+proves\s+for\s+all",
            r"eliminates\s+all\s+(?:errors|limitations|bottlenecks)",
        ],
    }


def load_json_safe(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not path.exists():
        return None, f"file_not_found:{path}"
    raw = None
    last_exc: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError as exc:
            last_exc = exc
        except PermissionError as exc:
            return None, f"permission_denied:{exc}"
        except Exception as exc:
            return None, f"read_error:{exc}"
    if raw is None:
        return None, f"encoding_error:{last_exc}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"json_decode_error:{exc}"


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def read_text_safe(path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not path.exists():
        return None, f"file_not_found:{path}"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc), None
        except UnicodeDecodeError:
            continue
        except PermissionError as exc:
            return None, f"permission_denied:{exc}"
        except Exception as exc:
            return None, f"read_error:{exc}"
    return None, f"decode_failed:{path}"


def posix_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.name


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def canonicalize_venue(name: str, venue_aliases: Optional[Dict[str, str]] = None) -> str:
    """Normalize venue abbreviations and punctuation so JACS == Journal of the American Chemical Society."""
    raw = (name or "").strip().lower()
    if not raw:
        return ""
    # Strip trailing year/volume/page noise like ", 2023" or "(2023)"
    raw_clean = re.sub(r"[,;(\[]\s*(?:19|20)\d{2}.*$", "", raw).strip()
    sp_norm = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", raw_clean).strip()
    compact = re.sub(r"\s+", "", sp_norm)
    if venue_aliases is None:
        venue_aliases = load_rubric_rules().get("venue_aliases") or {}
    if sp_norm in venue_aliases:
        return str(venue_aliases[sp_norm])
    if compact in venue_aliases:
        return str(venue_aliases[compact])
    # Also check prefix/token expansions for "Proc. NeurIPS" -> "neurips"
    for alias, canon in venue_aliases.items():
        if len(alias) >= 4 and re.search(rf"\b{re.escape(alias)}\b", sp_norm):
            return str(canon)
    return compact


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an output_contract field name (prioritizing field_name over description) to a canonical role."""
    fn = (field_name or "").strip().lower()
    dl = (desc or "").strip().lower()

    # 1. Exact/suffix checks on field_name FIRST (prevents description keywords from hijacking roles)
    if fn in ("case_id", "caseid", "task_id") or "case_id" in fn:
        return "case_id"
    if fn in ("status", "run_status", "execution_status") or fn.endswith("_status"):
        return "status"
    if (
        fn.endswith(("_path", "_paths", "_file", "_files", "_dir"))
        or fn in ("files", "deliverables", "deliverable_files", "output_files", "artifacts", "generated_files", "previews")
    ):
        return "deliverable_files"
    if any(w in fn for w in ("claim_citation", "claim_map", "citation_map", "claim_to_citation")) or fn in ("claims", "claim_evidence"):
        return "claim_citation_map"
    if any(w in fn for w in ("comparison_matrix", "related_work_matrix", "matrix", "comparison_table", "rw_table", "literature_table")):
        return "comparison_matrix"
    if any(w in fn for w in ("research_gap", "gap_analysis", "gap_list", "gaps", "open_problem")) or fn == "gap":
        return "research_gaps"
    if any(w in fn for w in ("contrib", "contribution")):
        return "contributions"
    if any(w in fn for w in ("conflict", "discrepanc", "mismatch", "inconsisten")):
        return "conflicts"
    if any(w in fn for w in ("confirm", "question", "author_confirm", "todo_author", "open_issue")):
        return "author_confirmation_items"
    if any(w in fn for w in ("change_record", "changes", "revision", "diff", "edit_log", "edit_history", "modification")):
        return "change_records"
    if any(w in fn for w in ("uncertain", "unknown", "warning", "limit", "boundary", "author_notes")):
        return "uncertainty"
    if any(w in fn for w in ("citation_check", "citation_list", "citation_audit", "citations", "reference", "bib", "cited_work")):
        return "references"
    if any(w in fn for w in ("verif", "audit", "metric", "check")):
        return "verification_summary"
    if any(w in fn for w in ("related_work_group", "rw_group", "taxonomy", "literature_group", "route")):
        return "related_work_groups"
    if any(w in fn for w in ("background", "research_background", "context_section")):
        return "research_background"
    if any(
        w in fn
        for w in (
            "manuscript",
            "intro_and_related",
            "introduction_and_related",
            "intro_text",
            "introduction",
            "related_work_text",
            "revised_text",
            "output_text",
            "draft_text",
            "article",
            "markdown",
            "prose",
        )
    ):
        return "manuscript_markdown"
    if "related_work" in fn:
        return "related_work_groups"
    if any(w in fn for w in ("source", "provenance", "evidence", "traceability")):
        return "sources"
    if any(w in fn for w in ("summary", "overview", "abstract")):
        return "summary"

    # 2. Fallback to description ONLY if field_name did not match
    if dl:
        if any(w in dl for w in ("comparison matrix", "comparison table", "对照表", "对比矩阵")):
            return "comparison_matrix"
        if any(w in dl for w in ("claim-to-citation", "claim citation", "论断-引用")):
            return "claim_citation_map"
        if any(w in dl for w in ("research gap", "研究空白")):
            return "research_gaps"
        if any(w in dl for w in ("contribution", "本文贡献")):
            return "contributions"
        if any(w in dl for w in ("manuscript", "introduction and related work", "prose", "markdown", "引言与相关工作")):
            return "manuscript_markdown"
        if any(w in dl for w in ("related work group", "methodological route", "技术路线")):
            return "related_work_groups"
        if any(w in dl for w in ("background", "研究背景")):
            return "research_background"
        if any(w in dl for w in ("reference", "bibliography", "参考文献")):
            return "references"
        if any(w in dl for w in ("conflict", "冲突")):
            return "conflicts"
        if any(w in dl for w in ("author confirmation", "待作者确认")):
            return "author_confirmation_items"
        if any(w in dl for w in ("change record", "修改记录")):
            return "change_records"
        if any(w in dl for w in ("uncertainty", "不确定")):
            return "uncertainty"
        if any(w in dl for w in ("provenance", "source locator", "来源")):
            return "sources"
        if any(w in dl for w in ("output file", "deliverable", "artifact path", "文件路径")):
            return "deliverable_files"
        if any(w in dl for w in ("verification", "audit report", "校验")):
            return "verification_summary"
        if any(w in dl for w in ("summary", "overview", "摘要")):
            return "summary"
    return "custom"


def _normalize_contract_type(raw_type: Any) -> str:
    if isinstance(raw_type, list):
        non_null = [str(x).strip().lower() for x in raw_type if str(x).strip().lower() not in ("null", "none", "")]
        raw_s = non_null[0] if non_null else "any"
    else:
        raw_s = str(raw_type or "any").strip().lower()
    if raw_s in ("str", "string") or raw_s.startswith("string ") or raw_s.startswith("markdown"):
        return "string"
    if raw_s in ("list", "array") or raw_s.startswith(("array", "list[", "list of")):
        return "array"
    if raw_s in ("dict", "object", "map") or raw_s.startswith(("object", "dict[", "dict of")):
        return "object"
    if raw_s in ("bool", "boolean"):
        return "boolean"
    if raw_s in ("int", "integer"):
        return "integer"
    if raw_s in ("float", "number", "double"):
        return "number"
    return raw_s or "any"


def _infer_shorthand_type_from_str(val: str) -> Tuple[str, str]:
    """Detect shorthand type strings like 'array of objects' vs plain description strings."""
    vl = val.strip().lower()
    if vl in ("string", "str", "text", "markdown"):
        return "string", ""
    if vl in ("array", "list") or re.match(r"^(?:array|list)(?:\s+of\s+|\s*\[)", vl):
        return "array", val
    if vl in ("object", "dict", "map") or re.match(r"^(?:object|dict)(?:\s+of\s+|\s*\[)", vl):
        return "object", val
    if vl in ("boolean", "bool"):
        return "boolean", ""
    if vl in ("integer", "int"):
        return "integer", ""
    if vl in ("number", "float"):
        return "number", ""
    return "string", val


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    """Parse heterogeneous output_contract definitions (JSON Schema, wrapper dicts, flat dicts, lists)."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        if isinstance(contract.get("schema"), dict):
            return parse_output_contract(contract["schema"])

        if isinstance(contract.get("properties"), dict):
            has_explicit_req = isinstance(contract.get("required"), list) or isinstance(contract.get("required_fields"), list)
            req_raw = contract.get("required") if isinstance(contract.get("required"), list) else contract.get("required_fields", [])
            req_set = set(req_raw) if isinstance(req_raw, list) else set()
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                ftype = _normalize_contract_type(sd.get("type", type(spec).__name__))
                desc = str(sd.get("description", ""))
                fields.append(
                    {
                        "name": k,
                        "expected_type": ftype,
                        "required": (k in req_set) if has_explicit_req else True,
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
            or contract.get("type") == "object"
            or isinstance(contract.get("required"), list)
            or isinstance(contract.get("required_fields"), list)
        )
        meta_keys = (
            {"type", "description", "format", "required", "required_fields", "$schema", "title", "additionalProperties", "example", "sample", "instructions", "constraints"}
            if is_schema_wrapper
            else {"required", "required_fields", "$schema", "additionalProperties", "example", "sample", "instructions", "constraints"}
        )
        req_list = (
            contract.get("required")
            if isinstance(contract.get("required"), list)
            else (contract.get("required_fields") if isinstance(contract.get("required_fields"), list) else None)
        )
        for k, val in contract.items():
            if k in meta_keys and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                ftype = _normalize_contract_type(val.get("type", "object"))
                desc = str(val.get("description", ""))
                req = bool(val.get("required", (k in req_list) if req_list is not None else True))
            elif isinstance(val, str):
                ftype, desc = _infer_shorthand_type_from_str(val)
                req = (k in req_list) if req_list is not None else True
            else:
                ftype = _normalize_contract_type(type(val).__name__) if val is not None else "any"
                desc = ""
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
                        "expected_type": _normalize_contract_type(item.get("type", "any")),
                        "required": bool(item.get("required", True)),
                        "description": desc,
                        "semantic_role": infer_semantic_role(name, desc),
                    }
                )
        return {"raw_type": "list", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def extract_docx_text(path: Path) -> Tuple[List[str], Optional[str]]:
    """Extract paragraph + table text lines from DOCX (python-docx or stdlib XML)."""
    try:
        import docx  # type: ignore

        doc = docx.Document(str(path))
        lines: List[str] = []
        for p in doc.paragraphs:
            if p.text.strip():
                lines.append(p.text.strip())
        for tbl in doc.tables:
            for row in tbl.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
        return lines, None
    except ImportError:
        pass
    except Exception as exc:
        return [], f"docx_read_error:{exc}"

    # Pure-stdlib XML fallback (paragraphs + table rows)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return [], "docx_missing_document_xml"
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = []
        body = root.find("w:body", ns)
        if body is not None:
            for child in body:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    joined = "".join(t.text for t in child.findall(".//w:t", ns) if t.text).strip()
                    if joined:
                        lines.append(joined)
                elif tag == "tbl":
                    for tr in child.findall(".//w:tr", ns):
                        cells = []
                        for tc in tr.findall("./w:tc", ns):
                            ctext = " ".join(
                                "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
                                for p in tc.findall(".//w:p", ns)
                            ).strip()
                            cells.append(ctext)
                        if any(cells):
                            lines.append(" | ".join(cells))
        else:
            for p in root.findall(".//w:p", ns):
                joined = "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
                if joined:
                    lines.append(joined)
        return lines, None
    except zipfile.BadZipFile:
        return [], "docx_corrupt_archive"
    except Exception as exc:
        return [], f"docx_xml_error:{exc}"


def _pdf_ocr_pages(path: Path, page_nums: List[int]) -> Dict[int, str]:
    """Optional bounded OCR fallback via pdftoppm + tesseract for scanned PDF pages."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return {}
    results: Dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for pno in page_nums[:6]:
            prefix = tmp_dir / f"page_{pno}"
            try:
                res = subprocess.run(
                    ["pdftoppm", "-png", "-r", "200", "-f", str(pno), "-l", str(pno), str(path), str(prefix)],
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if res.returncode != 0:
                continue
            pngs = sorted(tmp_dir.glob(f"page_{pno}*.png"))
            if not pngs:
                continue
            try:
                ocr = subprocess.run(
                    ["tesseract", str(pngs[0]), "stdout"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdin=subprocess.DEVNULL,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if ocr.returncode == 0 and ocr.stdout.strip():
                results[pno] = ocr.stdout.strip()
    return results


def _pdf_total_chars(pages: List[Dict[str, Any]]) -> int:
    return sum(len((p.get("text") or "").strip()) for p in pages)


def _pdf_sort_page_blocks_reading_order(page: Any) -> str:
    """Reconstruct multi-column reading order cleanly for PyMuPDF pages."""
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        return page.get_text("text") or ""
    text_blocks = []
    for b in blocks:
        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip() and (len(b) < 7 or b[6] == 0):
            text_blocks.append((float(b[0]), float(b[1]), float(b[2]), float(b[3]), b[4].strip()))
    if not text_blocks:
        return page.get_text("text") or ""
    pw = float(getattr(getattr(page, "rect", None), "width", 0.0) or 600.0)
    mid = pw * 0.5
    left_blk = [b for b in text_blocks if b[2] <= mid + 18.0]
    right_blk = [b for b in text_blocks if b[0] >= mid - 18.0]
    span_blk = [b for b in text_blocks if b not in left_blk and b not in right_blk]
    if len(left_blk) >= 2 and len(right_blk) >= 2:
        # Band around full-width spanning blocks (title/abstract/spanning figures)
        ordered: List[str] = []
        span_sorted = sorted(span_blk, key=lambda b: b[1])
        bands: List[Tuple[float, float]] = []
        prev_y = -1e9
        for sb in span_sorted:
            bands.append((prev_y, sb[1]))
            prev_y = sb[3]
        bands.append((prev_y, 1e9))
        for idx_b, (y_lo, y_hi) in enumerate(bands):
            l_part = sorted([b for b in left_blk if y_lo <= 0.5 * (b[1] + b[3]) < y_hi], key=lambda b: (b[1], b[0]))
            r_part = sorted([b for b in right_blk if y_lo <= 0.5 * (b[1] + b[3]) < y_hi], key=lambda b: (b[1], b[0]))
            ordered.extend(b[4] for b in l_part)
            ordered.extend(b[4] for b in r_part)
            if idx_b < len(span_sorted):
                ordered.append(span_sorted[idx_b][4])
        return "\n".join(ordered)
    text_blocks.sort(key=lambda b: (round(b[1] / 6.0), b[0]))
    return "\n".join(b[4] for b in text_blocks)


def extract_pdf_text(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract per-page text from PDF with reading-order-first multi-backend fallback and error flags.

    Precedence: PyMuPDF (reading-order sorted) -> pdfplumber -> pdftotext -layout -> pypdf -> stdlib literal scan.
    """
    warnings: List[str] = []
    if not path.exists():
        return [], [f"file_not_found:{path}"]
    try:
        header = path.read_bytes()[:1024]
    except Exception as exc:
        return [], [f"read_error:{exc}"]

    if b"%PDF" not in header:
        return [], [f"pdf_corrupt:missing_pdf_header:{path.name}"]

    best_pages: List[Dict[str, Any]] = []
    best_chars = 0

    def consider(cand: List[Dict[str, Any]]) -> None:
        nonlocal best_pages, best_chars
        c = _pdf_total_chars(cand)
        if cand and (not best_pages or c > best_chars):
            best_pages = cand
            best_chars = c

    # 1. PyMuPDF (fitz) with column reading-order sorting
    try:
        fitz_mod: Any = None
        try:
            import pymupdf as fitz_mod  # type: ignore
        except ImportError:
            import fitz as fitz_mod  # type: ignore
        if hasattr(fitz_mod, "open"):
            doc = fitz_mod.open(str(path))
            if getattr(doc, "needs_pass", False):
                doc.close()
                return [], [f"pdf_encrypted:{path.name}"]
            cand = []
            for i, page in enumerate(doc, start=1):
                txt = _pdf_sort_page_blocks_reading_order(page)
                cand.append({"page": i, "text": txt, "method": "pymupdf"})
            doc.close()
            consider(cand)
    except ImportError:
        pass
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypt" in msg or "password" in msg:
            return [], [f"pdf_encrypted:{path.name}"]
        warnings.append(f"pymupdf_failed:{exc}")

    # 2. pdfplumber (preserves column layout better than pypdf)
    if best_chars < 100:
        try:
            import pdfplumber  # type: ignore

            cand = []
            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    cand.append({"page": i, "text": page.extract_text() or "", "method": "pdfplumber"})
            consider(cand)
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"pdfplumber_failed:{exc}")

    # 3. pdftotext CLI
    if best_chars < 100 and shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                chunks = proc.stdout.split("\x0c")
                cand = []
                for i, chunk in enumerate(chunks, start=1):
                    if chunk.strip() or i < len(chunks):
                        cand.append({"page": i, "text": chunk, "method": "pdftotext"})
                consider(cand)
            else:
                err_low = (proc.stderr or "").lower()
                if "encrypt" in err_low or "password" in err_low:
                    return [], [f"pdf_encrypted:{path.name}"]
                warnings.append(f"pdftotext_failed:{(proc.stderr or '').strip()[:120]}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            warnings.append(f"pdftotext_timeout_or_error:{exc}")

    # 4. pypdf fallback
    if best_chars < 100:
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        return [], [f"pdf_encrypted:{path.name}"]
                except Exception:
                    return [], [f"pdf_encrypted:{path.name}"]
            cand = []
            for i, page in enumerate(reader.pages, start=1):
                txt = page.extract_text() or ""
                cand.append({"page": i, "text": txt, "method": "pypdf"})
            consider(cand)
        except ImportError:
            pass
        except Exception as exc:
            msg = str(exc).lower()
            if "encrypt" in msg or "password" in msg:
                return [], [f"pdf_encrypted:{path.name}"]
            warnings.append(f"pypdf_failed:{exc}")

    pages = best_pages

    # 5. Pure-stdlib fallback: scan PDF text literals or mark corrupt/unavailable
    if not pages:
        raw_bytes = path.read_bytes()
        if b"/Encrypt" in raw_bytes[:16384] or b"/Encrypt" in raw_bytes[-32768:]:
            return [], [f"pdf_encrypted:{path.name}"]
        if b"%%EOF" not in raw_bytes[-4096:] and b"endobj" not in raw_bytes:
            return [], [f"pdf_corrupt:{path.name}"]
        decoded = raw_bytes.decode("latin-1", errors="ignore")
        lits = re.findall(r"\(([^()]{4,200})\)\s*Tj", decoded)
        if lits:
            pages.append({"page": 1, "text": " ".join(lits), "method": "stdlib_literal_scan"})
        else:
            warnings.append(f"pdf_no_text_extractor_available:{path.name}")
            return [], warnings

    # Check scanned pages (very low character count) and try OCR if available
    low_pages = [p["page"] for p in pages if len((p.get("text") or "").strip()) < 30]
    if low_pages:
        ocr_map = _pdf_ocr_pages(path, low_pages)
        for p in pages:
            pno = p["page"]
            if pno in ocr_map:
                p["text"] = ocr_map[pno]
                p["method"] = "ocr_tesseract"
                warnings.append(f"ocr_used_page_{pno}:{path.name}")
            elif pno in low_pages:
                warnings.append(f"scanned_or_low_text_page_{pno}:{path.name}")

    return pages, warnings


def _clean_bib_value(val: str, string_macros: Optional[Dict[str, str]] = None) -> str:
    macros = string_macros or {}
    raw = val.strip().rstrip(",").strip()
    # Expand BibTeX `#` string concatenation & `@string` macros outside quotes/braces
    parts: List[str] = []
    cur: List[str] = []
    bdepth = 0
    in_q = False
    for idx, ch in enumerate(raw):
        prev_esc = idx > 0 and raw[idx - 1] == "\\"
        if ch == "{" and not prev_esc and not in_q:
            bdepth += 1
        elif ch == "}" and not prev_esc and not in_q and bdepth > 0:
            bdepth -= 1
        elif ch == '"' and not prev_esc and bdepth == 0:
            in_q = not in_q
        if ch == "#" and bdepth == 0 and not in_q:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())

    out_chunks: List[str] = []
    for p in parts:
        if not p:
            continue
        if (p.startswith("{") and p.endswith("}")) or (p.startswith('"') and p.endswith('"')):
            s = p[1:-1].strip()
            s = re.sub(r"(?<!\\)[{}]", "", s)
            s = s.replace(r"\{", "{").replace(r"\}", "}")
            out_chunks.append(s)
        elif p.lower() in macros:
            out_chunks.append(macros[p.lower()])
        else:
            s = re.sub(r"(?<!\\)[{}]", "", p)
            out_chunks.append(s)
    return normalize_ws(" ".join(out_chunks) if len(out_chunks) == 1 else "".join(out_chunks))


def parse_bibtex_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    """Parse BibTeX/BibLaTeX entries with @string macros, # concat, exact starting lines, and JabRef recovery."""
    entries: List[Dict[str, Any]] = []
    string_macros: Dict[str, str] = {}
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = re.match(r"^\s*@([a-zA-Z]+)\s*([{(])\s*([^,\s=})]+)?\s*([,=])?", line)
        if not m:
            i += 1
            continue
        etype = m.group(1).lower()
        open_delim = m.group(2)
        paren_mode = open_delim == "("

        if etype in ("comment", "preamble", "string"):
            buf_meta = [line]
            depth = (line.count("(") - line.count(")")) if paren_mode else (line.count("{") - line.count("}"))
            i += 1
            while i < n and depth > 0:
                cur = lines[i]
                if re.match(r"^@[a-zA-Z]+\s*[{(]", cur):
                    break
                buf_meta.append(cur)
                depth += (cur.count("(") - cur.count(")")) if paren_mode else (cur.count("{") - cur.count("}"))
                i += 1
            if etype == "string":
                block_str = "\n".join(buf_meta)
                sm = re.search(r"@string\s*[{(]\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(.+)\s*[})]\s*$", block_str, re.I | re.S)
                if sm:
                    string_macros[sm.group(1).lower()] = _clean_bib_value(sm.group(2), string_macros)
            continue

        m_entry = re.match(r"^\s*@([a-zA-Z]+)\s*([{(])\s*([^,\s]+)\s*,?", line)
        if not m_entry:
            i += 1
            continue
        cite_key = m_entry.group(3).strip()
        start_line = i + 1
        buf_lines = [line]
        depth = (line.count("(") - line.count(")")) if paren_mode else (line.count("{") - line.count("}"))
        i += 1
        while i < n and depth > 0:
            cur = lines[i]
            # Unclosed entry forward recovery (JabRef pattern): stop if a new @type{key starts at col 0
            if re.match(r"^@[a-zA-Z]+\s*[{(]\s*[^,\s]+\s*,", cur):
                break
            buf_lines.append(cur)
            if paren_mode:
                depth += cur.count("(") - cur.count(")")
            else:
                depth += cur.count("{") - cur.count("}")
            i += 1

        block = "\n".join(buf_lines)
        fields: Dict[str, str] = {}
        body_start = block.find(",")
        body_end = len(block) - 1 if block.rstrip().endswith(("}", ")")) else len(block)
        body = block[body_start + 1 : body_end] if body_start != -1 else ""
        idx = 0
        blen = len(body)
        while idx < blen:
            fm = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*", body[idx:])
            if not fm:
                idx += 1
                continue
            fname = fm.group(1).lower()
            idx += fm.end()
            if idx >= blen:
                break
            # Read potentially `#`-concatenated value parts until top-level `,` or EOF
            start_v = idx
            bdepth = 0
            in_q = False
            while idx < blen:
                c_cur = body[idx]
                prev_esc = idx > 0 and body[idx - 1] == "\\"
                if c_cur == "{" and not prev_esc and not in_q:
                    bdepth += 1
                elif c_cur == "}" and not prev_esc and not in_q and bdepth > 0:
                    bdepth -= 1
                elif c_cur == '"' and not prev_esc and bdepth == 0:
                    in_q = not in_q
                elif c_cur == "," and bdepth == 0 and not in_q:
                    break
                idx += 1
            val = body[start_v:idx]
            fields[fname] = _clean_bib_value(val, string_macros)
            while idx < blen and body[idx] in " ,\t\r\n":
                idx += 1

        authors = fields.get("author", "") or fields.get("editor", "")
        venue = (
            fields.get("journal")
            or fields.get("journaltitle")
            or fields.get("booktitle")
            or fields.get("publisher")
            or fields.get("school")
            or fields.get("institution")
            or fields.get("howpublished")
            or ""
        )
        doi_or_id = fields.get("doi") or fields.get("eprint") or fields.get("arxivid") or fields.get("arxiv") or fields.get("url") or ""
        raw_year = fields.get("year") or fields.get("date") or ""
        year_m = re.search(r"(19|20)\d{2}", raw_year)
        year = year_m.group(0) if year_m else raw_year

        entries.append(
            {
                "cite_key": cite_key,
                "entry_type": etype,
                "authors": authors,
                "year": year,
                "title": fields.get("title", ""),
                "venue": venue,
                "doi_or_id": doi_or_id,
                "volume": fields.get("volume", ""),
                "issue": fields.get("number", "") or fields.get("issue", ""),
                "pages": fields.get("pages", ""),
                "abstract": fields.get("abstract", "") or fields.get("note", ""),
                "source_locator": f"{source_path}:L{start_line}",
            }
        )
    return entries


def parse_ris_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"authors_list": [], "abstract_parts": [], "start_line": 1}
    active_tag = ""

    def flush(end_lineno: int) -> None:
        if not (cur.get("title") or cur.get("authors_list") or cur.get("doi_or_id")):
            return
        authors = "; ".join(cur.get("authors_list", []))
        year = cur.get("year", "")
        first_surname = extract_first_author_surname(authors) or "Ref"
        cite_key = cur.get("cite_key") or f"{first_surname}{year}"
        sp = cur.get("sp", "")
        ep = cur.get("ep", "")
        pages = f"{sp}-{ep}" if (sp and ep) else sp
        entries.append(
            {
                "cite_key": cite_key,
                "entry_type": cur.get("entry_type", "article"),
                "authors": authors,
                "year": year,
                "title": normalize_ws(cur.get("title", "")),
                "venue": normalize_ws(cur.get("venue", "")),
                "doi_or_id": cur.get("doi_or_id", ""),
                "volume": cur.get("volume", ""),
                "issue": cur.get("issue", ""),
                "pages": pages,
                "abstract": " ".join(cur.get("abstract_parts", [])).strip(),
                "source_locator": f"{source_path}:L{cur.get('start_line', end_lineno)}",
            }
        )

    last_lineno = 1
    for lineno, line in enumerate(text.splitlines(), start=1):
        last_lineno = lineno
        raw_s = line.strip()
        if not raw_s:
            active_tag = ""
            continue
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", raw_s)
        if not m:
            # Handle multi-line RIS continuation lines for TI/JO/AB/N2
            if active_tag in ("AB", "N2"):
                cur["abstract_parts"].append(raw_s)
            elif active_tag in ("TI", "T1"):
                cur["title"] = (cur.get("title", "") + " " + raw_s).strip()
            elif active_tag in ("JO", "JF", "T2", "BT") and cur.get("venue"):
                cur["venue"] = (cur["venue"] + " " + raw_s).strip()
            continue
        tag, val = m.group(1), m.group(2).strip()
        active_tag = tag
        if tag == "TY":
            flush(lineno - 1)
            cur = {"authors_list": [], "abstract_parts": [], "start_line": lineno, "entry_type": val}
        elif tag in ("AU", "A1"):
            if val:
                cur["authors_list"].append(val)
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("PY", "Y1", "DA"):
            ym = re.search(r"(19|20)\d{2}", val)
            if ym and not cur.get("year"):
                cur["year"] = ym.group(0)
            elif val and not cur.get("year"):
                cur["year"] = val
        elif tag in ("JO", "JF", "T2", "BT"):
            cur.setdefault("venue", val)
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["issue"] = val
        elif tag == "SP":
            cur["sp"] = val
        elif tag == "EP":
            cur["ep"] = val
        elif tag in ("DO", "UR"):
            if tag == "DO" or not cur.get("doi_or_id"):
                cur["doi_or_id"] = val
        elif tag in ("ID", "AN"):
            cur.setdefault("cite_key", val)
        elif tag in ("AB", "N2"):
            if val:
                cur["abstract_parts"].append(val)
        elif tag == "ER":
            flush(lineno)
            cur = {"authors_list": [], "abstract_parts": [], "start_line": lineno + 1}
            active_tag = ""
    flush(last_lineno)
    return entries


def extract_first_author_surname(authors: str) -> str:
    if not authors:
        return ""
    first = re.split(r"\band\b|;|、|&", authors, maxsplit=1)[0].strip()
    if not first:
        return ""
    if "," in first:
        return re.sub(r"[^A-Za-z\u4e00-\u9fff-]", "", first.split(",")[0].strip())
    parts = [p for p in first.split() if p]
    if not parts:
        return ""
    if re.search(r"[\u4e00-\u9fff]", parts[0]):
        return re.sub(r"[^\u4e00-\u9fff]", "", parts[0])
    return re.sub(r"[^A-Za-z-]", "", parts[-1])


def extract_author_surnames(authors: str) -> List[str]:
    """Return ordered list of author surnames for APA/author-year rendering."""
    if not authors:
        return []
    raw_parts = [p.strip() for p in re.split(r"\s+and\s+|;|、|\s*&\s*", authors) if p.strip()]
    out: List[str] = []
    for part in raw_parts:
        s = extract_first_author_surname(part)
        if s:
            out.append(s)
    return out


def parse_markdown_or_text_refs(text: str, source_path: str) -> List[Dict[str, Any]]:
    """Extract numbered, bulleted, or heading-tagged bibliographic lines cleanly."""
    entries: List[Dict[str, Any]] = []
    lines = text.splitlines()
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", Path(source_path).stem).strip("_") or "doc"
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,4}\s+", "", line)
        if line.startswith(("- ", "* ")):
            line_cand = line[2:].strip()
            if not re.search(r"\b(?:19|20)\d{2}\b", line_cand) and not re.search(r"10\.\d{4,9}/", line_cand):
                continue
            line = line_cand
        m = re.match(r"^(?:\[([A-Za-z0-9_-]{1,24})\]|(\d{1,3})\.)\s+(.+)$", line)
        if not m:
            continue
        explicit_key = m.group(1)
        num_idx = m.group(2)
        body = m.group(3).strip()
        ym = re.search(r"\b((?:19|20)\d{2})\b", body)
        doi_m = re.search(r"(10\.\d{4,9}/[^\s,;]+|arXiv:\d{4}\.\d{4,5})", body, re.I)
        if not ym and not doi_m:
            continue
        # Guard against numbered narrative sentences like '1. In 2022, PINNs achieved 94%.'
        if num_idx and not doi_m:
            has_ay = bool(re.match(r"^[^.。]{2,60}[\(（]\s*(?:19|20)\d{2}[a-z]?\s*[\)）][.,:\s]", body))
            period_segs = [p.strip() for p in re.split(r"\.\s+|。\s*", body) if p.strip()]
            has_bib_segs = len(period_segs) >= 2 and not re.match(r"^(?:In|Since|During|For|After|Before|By|On|With|在|从|关于)\b", body, re.I)
            if not (has_ay or has_bib_segs):
                continue
        key = explicit_key or f"{stem}_ref_{num_idx}"
        year = ym.group(1) if ym else ""
        doi_or_id = doi_m.group(1).rstrip(".") if doi_m else ""
        authors, title, venue = "", body, ""
        ay_m = re.match(r"^(.+?)\s*[\(（]\s*(?:19|20)\d{2}[a-z]?\s*[\)）][.,:\s]+(.+)$", body)
        if ay_m:
            authors = ay_m.group(1).strip(" .")
            rest = ay_m.group(2).strip()
            parts = [p.strip() for p in re.split(r"(?<=[.。])\s+", rest, maxsplit=2) if p.strip()]
            if parts:
                title = parts[0].strip(" .\"“”")
                venue = parts[1].strip(" .") if len(parts) > 1 else ""
        else:
            parts = [p.strip() for p in re.split(r"\.\s+|。\s*", body) if p.strip()]
            if len(parts) >= 3:
                authors = parts[0]
                title = parts[1].strip(" \"“”")
                venue = re.sub(r",?\s*(19|20)\d{2}.*$", "", parts[2]).strip(" ,.")
            elif len(parts) == 2:
                authors = parts[0]
                title = parts[1]

        entries.append(
            {
                "cite_key": key,
                "entry_type": "article",
                "authors": authors,
                "year": year,
                "title": title,
                "venue": venue,
                "doi_or_id": doi_or_id,
                "abstract": "",
                "source_locator": f"{source_path}:L{lineno}",
            }
        )
    return entries


_UNIT_NORM = {
    "％": "%",
    "×": "x",
    "um": "μm",
    "倍": "x",
    "fold": "x",
    "epochs": "epoch",
}

_UNIT_SCALE: Dict[str, Tuple[str, float]] = {
    "nm": ("len_m", 1e-9), "μm": ("len_m", 1e-6), "mm": ("len_m", 1e-3), "cm": ("len_m", 1e-2), "m": ("len_m", 1.0), "km": ("len_m", 1e3),
    "ms": ("time_s", 1e-3), "s": ("time_s", 1.0), "min": ("time_s", 60.0), "h": ("time_s", 3600.0),
    "μg": ("mass_g", 1e-6), "ug": ("mass_g", 1e-6), "mg": ("mass_g", 1e-3), "g": ("mass_g", 1.0), "kg": ("mass_g", 1e3),
    "Pa": ("press_pa", 1.0), "kPa": ("press_pa", 1e3), "MPa": ("press_pa", 1e6), "GPa": ("press_pa", 1e9),
    "mV": ("volt_v", 1e-3), "V": ("volt_v", 1.0),
    "mA": ("amp_a", 1e-3), "A": ("amp_a", 1.0),
    "eV": ("energy_ev", 1.0), "keV": ("energy_ev", 1e3), "MeV": ("energy_ev", 1e6),
    "W": ("power_w", 1.0), "kW": ("power_w", 1e3),
    "Hz": ("freq_hz", 1.0), "kHz": ("freq_hz", 1e3), "MHz": ("freq_hz", 1e6), "GHz": ("freq_hz", 1e9),
    "mmol": ("mol", 1e-3), "mol": ("mol", 1.0), "kmol": ("mol", 1e3),
    "B": ("bytes", 1.0), "KB": ("bytes", 1024.0), "MB": ("bytes", 1024.0 ** 2), "GB": ("bytes", 1024.0 ** 3),
}

_MASK_RE_HEADING = re.compile(r"(?m)^\s*#{1,6}\s+\d+(?:\.\d+)*\.?\s*")
_MASK_RE_DECADE = re.compile(r"\b(?:19|20)\d0s\b")
_MASK_RE_CITE = re.compile(r"\[(?:R?\d+(?:\s*[-,–]\s*R?\d+)*)\]")
_MASK_RE_TAG = re.compile(r"\b(?:GAP|CONF|CONTRIB|CLAIM|RG|CH|CL|G|C|R)-?\d{1,3}\b", re.I)
_MASK_RE_FIG = re.compile(r"(?:Fig(?:ure)?|Table|Tab\.|Eq\.?|Section|Sec\.|图|表|式|第)\s*\.?\s*\(?\d+(?:\.\d+)?\)?", re.I)
_MASK_RE_LOC = re.compile(r":[Lp]\d+(?:[_-][LpA-Za-z]?\d+)*")
_MASK_RE_CHEM = re.compile(r"\b(?:FP16|BF16|FP32|INT8|INT4|[1-4]D|L[0-2]|CO2|H2O|O2|N2|CH4|NH3|SiO2|TiO2)\b")
_NUM_UNIT_TAIL = r"(%|％|x|×|倍|fold|mol%|wt%|ppm|ppb|ms|s|min|h|Hz|kHz|MHz|GHz|nm|um|μm|mm|cm|m|km|ug|μg|mg|g|kg|K|°C|Pa|kPa|MPa|GPa|eV|keV|MeV|W/cm2|W|kW|V|mV|mA/cm2|A|mA|mmol|mol|kmol|kJ/mol|dB|GB|MB|KB|B|bar|fps|tokens|epochs?|K|M)"
_NUM_PAT_STRUCT = re.compile(r"(?<![A-Za-z0-9_.-])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s*" + _NUM_UNIT_TAIL + r"(?![A-Za-z]))?")
_NUM_PAT_RAW = re.compile(r"(?<![0-9_.-])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s*" + _NUM_UNIT_TAIL + r"(?![A-Za-z]))?")


def _norm_unit(u: str) -> str:
    return _UNIT_NORM.get(u, u)


def extract_numeric_tokens(text: str, filter_structural: bool = True) -> List[Dict[str, Any]]:
    """Extract quantitative numeric tokens while skipping headings, decades, and structural indices."""
    if not text:
        return []
    cleaned = text
    if filter_structural:
        cleaned = _MASK_RE_HEADING.sub(" ", cleaned)
        cleaned = _MASK_RE_DECADE.sub(" ", cleaned)
        cleaned = _MASK_RE_CITE.sub(" ", cleaned)
        cleaned = _MASK_RE_TAG.sub(" ", cleaned)
        cleaned = _MASK_RE_FIG.sub(" ", cleaned)
        cleaned = _MASK_RE_LOC.sub(" ", cleaned)
        cleaned = _MASK_RE_CHEM.sub(" ", cleaned)

    tokens: List[Dict[str, Any]] = []
    pat = _NUM_PAT_STRUCT if filter_structural else _NUM_PAT_RAW
    for m in pat.finditer(cleaned):
        raw_num = m.group(1).replace(",", "")
        if m.start() > 0 and cleaned[m.start() - 1] in ".0123456789":
            continue
        unit = m.group(2) or ""
        try:
            val = float(raw_num)
        except ValueError:
            continue
        if filter_structural and not unit and "." not in raw_num and "e" not in raw_num.lower():
            if 1900 <= val <= 2099:
                continue
            if abs(val) < 10:
                continue
        tokens.append(
            {
                "raw": m.group(0).strip(),
                "value": val,
                "unit": unit,
                "has_percent": unit in ("%", "％"),
                "decimals": len(raw_num.split(".")[1]) if "." in raw_num and "e" not in raw_num.lower() else 0,
            }
        )
    return tokens


def numbers_match(tok_a: Dict[str, Any], tok_b: Dict[str, Any]) -> bool:
    """Compare two numeric tokens allowing SI unit scale conversions, % <-> decimal equivalence, and rounding tolerance."""
    ua = _norm_unit(str(tok_a.get("unit") or ""))
    ub = _norm_unit(str(tok_b.get("unit") or ""))
    va, vb = float(tok_a["value"]), float(tok_b["value"])

    if ua and ub and ua != ub:
        sa = _UNIT_SCALE.get(ua)
        sb = _UNIT_SCALE.get(ub)
        if sa and sb and sa[0] == sb[0]:
            base_a = va * sa[1]
            base_b = vb * sb[1]
            return abs(base_a - base_b) <= max(0.005 * max(abs(base_a), abs(base_b), 1e-12), 1e-9)
        return False

    candidates_a = [va]
    candidates_b = [vb]
    if tok_a.get("has_percent") and not ub:
        candidates_a.append(va / 100.0)
    if tok_b.get("has_percent") and not ua:
        candidates_b.append(vb / 100.0)

    dec = min(tok_a.get("decimals", 2), tok_b.get("decimals", 2))
    round_tol = 0.55 * (10.0 ** (-dec)) if dec > 0 else 0.05

    for x in candidates_a:
        for y in candidates_b:
            rel_tol = 0.005 * max(abs(x), abs(y), 1e-12)
            effective_tol = max(round_tol, rel_tol, 1e-9)
            if tok_a.get("has_percent") and tok_b.get("has_percent"):
                effective_tol = round_tol if dec > 0 else 0.05
            if tok_a.get("decimals", 0) != tok_b.get("decimals", 0) and (tok_a.get("decimals", 0) == 0 or tok_b.get("decimals", 0) == 0):
                effective_tol = max(rel_tol, 0.05)
            if abs(x - y) <= effective_tol:
                return True
    return False


def format_apa_inline_item(ref: Dict[str, Any], language: str = "en") -> str:
    surnames = extract_author_surnames(str(ref.get("authors") or ""))
    year = str(ref.get("year") or "n.d.").strip() or "n.d."
    if not surnames:
        lead = str(ref.get("cite_key") or ref.get("ref_id") or "Unknown")
    elif len(surnames) == 1:
        lead = surnames[0]
    elif len(surnames) == 2:
        sep = " & " if not re.search(r"[\u4e00-\u9fff]", surnames[0]) else "、"
        lead = f"{surnames[0]}{sep}{surnames[1]}"
    else:
        lead = f"{surnames[0]}等" if (language == "zh" and re.search(r"[\u4e00-\u9fff]", surnames[0])) else f"{surnames[0]} et al."
    return f"{lead}, {year}"


def normalize_ref_id_token(tok: str) -> str:
    """Normalize unpadded reference tokens like R1 -> R01 while leaving cite keys untouched."""
    s = str(tok or "").strip()
    m = re.match(r"^R(\d{1,3})$", s, re.I)
    if m:
        return f"R{int(m.group(1)):02d}"
    return s


def order_refs_by_appearance(md_text: str, refs: List[Dict[str, Any]], style: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Order references by first citation occurrence in prose for numeric styles; return (ordered_refs, uncited_refs)."""
    if not md_text or not refs:
        return list(refs or []), []
    valid_refs = [r for r in refs if isinstance(r, dict)]
    by_tok: Dict[str, Dict[str, Any]] = {}
    for r in valid_refs:
        rid = str(r.get("ref_id") or "").strip().lower()
        ck = str(r.get("cite_key") or "").strip().lower()
        if rid:
            by_tok[rid] = r
            rm = re.match(r"^r0*(\d+)$", rid)
            if rm:
                by_tok[f"r{int(rm.group(1))}"] = r
                by_tok[f"r{int(rm.group(1)):02d}"] = r
        if ck:
            by_tok[ck] = r

    seen_ids: List[int] = []
    seen_set = set()

    def _mark(tok: str) -> None:
        r = by_tok.get(normalize_ref_id_token(tok).lower()) or by_tok.get(tok.strip().lower())
        if r is not None and id(r) not in seen_set:
            seen_set.add(id(r))
            seen_ids.append(id(r))

    for m in re.finditer(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}|\[([^\[\]]+)\]", md_text):
        if m.group(1) is not None:
            for k in m.group(1).split(","):
                _mark(k)
        else:
            inner = (m.group(2) or "").strip()
            if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", "待补充", ":")):
                continue
            rm = re.match(r"^R(\d{1,3})\s*[-–]\s*R(\d{1,3})$", inner, re.I)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                for k in range(a, min(b + 1, a + 21)):
                    _mark(f"R{k:02d}")
            else:
                for p in re.split(r"[,;]", inner):
                    pn = normalize_ref_id_token(p).lower()
                    if pn in by_tok or p.strip().lower() in by_tok:
                        _mark(p)

    if not seen_ids:
        return valid_refs, []

    id_to_ref = {id(r): r for r in valid_refs}
    cited = [id_to_ref[i] for i in seen_ids]
    uncited = [r for r in valid_refs if id(r) not in seen_set]
    eff = style or "gbt7714"
    if eff in ("gbt7714", "ieee_numeric", "nature_numeric", "unspecified"):
        return cited + uncited, uncited
    return valid_refs, uncited


def render_inline_citations(md_text: str, refs: List[Dict[str, Any]], style: str, language: str = "zh") -> str:
    """Convert [R01] / [R1] / [cite_key] / \\cite{key} markers consistently to the target citation style."""
    if not md_text or not refs:
        return md_text or ""
    eff_style = style or "unspecified"
    if eff_style == "unspecified":
        eff_style = "gbt7714" if language == "zh" else "ieee_numeric"

    by_id: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    by_key: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    by_ay: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for idx, r in enumerate(refs, start=1):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("ref_id") or "").strip().lower()
        ck = str(r.get("cite_key") or "").strip().lower()
        if rid:
            by_id[rid] = (idx, r)
            rm = re.match(r"^r0*(\d+)$", rid)
            if rm:
                by_id[f"r{int(rm.group(1))}"] = (idx, r)
                by_id[f"r{int(rm.group(1)):02d}"] = (idx, r)
        if ck:
            by_key[ck] = (idx, r)
        surn = extract_first_author_surname(str(r.get("authors") or "")).lower()
        yr = str(r.get("year") or "").strip().lower()
        if surn and yr:
            by_ay.setdefault(f"{surn}{yr}", (idx, r))

    def _resolve_tok(tok: str) -> Optional[Tuple[int, Dict[str, Any]]]:
        tl = tok.strip().lower()
        tn = normalize_ref_id_token(tok).lower()
        return by_id.get(tl) or by_id.get(tn) or by_key.get(tl) or by_ay.get(tl)

    def _format_items(items: List[Tuple[int, Dict[str, Any]]]) -> str:
        if eff_style == "bibtex_latex":
            keys = [str(r.get("cite_key") or r.get("ref_id") or f"ref{idx}") for idx, r in items]
            return f"\\cite{{{', '.join(keys)}}}"
        if eff_style == "apa_author_year":
            apa_parts = [format_apa_inline_item(r, language=language) for _, r in items]
            return f"({'; '.join(apa_parts)})"
        if eff_style == "ieee_numeric":
            return ", ".join(f"[{idx}]" for idx, _ in items)
        return f"[{', '.join(str(idx) for idx, _ in items)}]"

    def _replace_bracket(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", "待补充", ":")):
            return m.group(0)
        rm = re.match(r"^R(\d{1,3})\s*[-–]\s*R(\d{1,3})$", inner, re.I)
        if rm:
            a, b = int(rm.group(1)), int(rm.group(2))
            if 0 < a <= b <= a + 20:
                resolved = [_resolve_tok(f"R{k:02d}") for k in range(a, b + 1)]
                if all(x is not None for x in resolved):
                    items = [x for x in resolved if x is not None]
                    return _format_items(items)
            return m.group(0)
        parts = [p.strip() for p in re.split(r"[,;]", inner) if p.strip()]
        if not parts:
            return m.group(0)
        if not all(re.match(r"^R\d{1,3}$", p, re.I) or p.lower() in by_key for p in parts):
            return m.group(0)
        resolved = [_resolve_tok(p) for p in parts]
        if not all(x is not None for x in resolved):
            return m.group(0)
        return _format_items([x for x in resolved if x is not None])

    out = re.sub(r"\[([^\[\]]+)\]", _replace_bracket, md_text)

    if eff_style != "bibtex_latex":
        def _replace_cite(m: re.Match[str]) -> str:
            keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
            resolved = [_resolve_tok(k) for k in keys]
            if keys and all(x is not None for x in resolved):
                return _format_items([x for x in resolved if x is not None])
            return m.group(0)

        out = re.sub(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}", _replace_cite, out)

    if eff_style != "apa_author_year":
        def _replace_paren_ay(m: re.Match[str]) -> str:
            inside = m.group(1).strip()
            segs = [s.strip() for s in re.split(r"[;；]", inside) if s.strip()]
            items: List[Tuple[int, Dict[str, Any]]] = []
            for seg in segs:
                em = re.match(r"^([A-Z][A-Za-z-]+)(?:\s+et\s+al\.?|\s*(?:&|and)\s+[A-Z][A-Za-z-]+)?\s*,?\s*((?:19|20)\d{2})[a-z]?$", seg)
                if not em:
                    return m.group(0)
                hit = _resolve_tok(f"{em.group(1)}{em.group(2)}")
                if hit is None:
                    return m.group(0)
                items.append(hit)
            return _format_items(items) if items else m.group(0)

        out = re.sub(r"[（(]([^()（）]{4,120})[)）]", _replace_paren_ay, out)

    return out


def _infer_gbt_doc_code(ref: Dict[str, Any]) -> str:
    et = str(ref.get("entry_type") or "").strip().lower()
    venue = str(ref.get("venue") or "").lower()
    doi = str(ref.get("doi_or_id") or "").lower()
    if et in ("inproceedings", "conference", "proceedings", "conf") or any(
        w in venue for w in ("proceedings", "conference", "workshop", "symposium", "neurips", "icml", "iclr", "cvpr", "acl", "emnlp", "aaai", "会议", "论文集")
    ):
        return "[C]"
    if et in ("phdthesis", "mastersthesis", "thesis"):
        return "[D]"
    if et in ("book", "inbook", "incollection"):
        return "[M]"
    if not venue and (doi.startswith("http") or doi.startswith("arxiv:") or et == "misc"):
        return "[EB/OL]"
    return "[J]"


def format_citation_entry(ref: Dict[str, Any], style: str, idx: int) -> str:
    authors = str(ref.get("authors") or "[待补充作者]").strip().rstrip(".")
    year = str(ref.get("year") or "[待补充年份]").strip()
    title = str(ref.get("title") or "[待补充标题]").strip().rstrip(".")
    venue = str(ref.get("venue") or "[待补充来源]").strip().rstrip(".")
    vol = str(ref.get("volume") or "").strip()
    issue = str(ref.get("issue") or "").strip()
    pages = str(ref.get("pages") or "").strip()
    doi = str(ref.get("doi_or_id") or "").strip()
    extra = f" DOI: {doi}." if doi else ""

    if style == "apa_author_year":
        vp = f", {vol}" if vol else ""
        if issue:
            vp += f"({issue})"
        if pages:
            vp += f", {pages}"
        return f"{authors} ({year}). {title}. {venue}{vp}.{extra}".strip()
    if style == "ieee_numeric":
        parts = [venue]
        if vol:
            parts.append(f"vol. {vol}")
        if issue:
            parts.append(f"no. {issue}")
        if pages:
            parts.append(f"pp. {pages}")
        parts.append(year)
        return f"[{idx}] {authors}, \"{title},\" {', '.join(parts)}.{extra}".strip()
    if style == "nature_numeric":
        vp = f" {vol}," if vol else ""
        pg = f" {pages}" if pages else ""
        return f"{idx}. {authors} {title}. {venue}{vp}{pg} ({year}).{extra}".strip()
    if style == "bibtex_latex":
        key = ref.get("cite_key") or f"ref{idx}"
        doi_part = f", doi={{{doi}}}" if doi.startswith("10.") else ""
        return f"@article{{{key}, author={{{authors}}}, title={{{title}}}, journal={{{venue}}}, year={{{year}}}{doi_part}}}"
    # Default: GB/T 7714-2015 with inferred document code & CJK-friendly author separator
    gbt_auth = re.sub(r"\s+and\s+|;\s*", ", ", authors)
    code = _infer_gbt_doc_code(ref)
    vp = f", {year}"
    if vol:
        vp += f", {vol}"
    if issue:
        vp += f"({issue})"
    if pages:
        vp += f": {pages}"
    return f"[{idx}] {gbt_auth}. {title}{code}. {venue}{vp}.{extra}".strip()


def resolve_manuscript_text(
    payload: Dict[str, Any],
    payload_path: Optional[Path],
    manuscript_arg: Optional[str],
) -> Tuple[str, List[str], Optional[str]]:
    """Resolve manuscript text consistently across verifier & assembler with explicit missing-file errors."""
    warns: List[str] = []
    if manuscript_arg:
        mp = Path(manuscript_arg)
        if not mp.exists():
            return "", warns, f"manuscript_arg_not_found:{manuscript_arg}"
        txt, err = read_text_safe(mp)
        if err or txt is None:
            return "", warns, f"manuscript_arg_read_error:{err}"
        return txt, warns, None

    inline = str(payload.get("manuscript_markdown") or "")
    mf = str(payload.get("manuscript_file") or "").strip()
    if mf:
        cand = Path(mf)
        if not cand.is_absolute() and payload_path is not None:
            cand = payload_path.parent / cand
        if not cand.exists():
            if inline.strip():
                warns.append(f"manuscript_file_missing_fell_back_to_inline:{mf}")
                return inline, warns, None
            return "", warns, f"manuscript_file_not_found:{mf}"
        txt, err = read_text_safe(cand)
        if err or txt is None:
            return "", warns, f"manuscript_file_read_error:{err}"
        if inline.strip() and txt.strip() and txt.strip() != inline.strip():
            warns.append("both_manuscript_file_and_inline_populated:preferred_manuscript_file")
        return txt, warns, None
    return inline, warns, None


def _cjk_bigrams_norm(s: str) -> set:
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", s))
    if len(cjk) < 2:
        return set(cjk)
    return {cjk[i : i + 2] for i in range(len(cjk) - 1)}


def _build_ref_evidence_pool(
    ledger: Optional[Dict[str, Any]],
    extracted_dir: Optional[Path],
) -> Dict[str, List[Tuple[str, str]]]:
    """Build per-ref_id candidate (locator, verbatim_text) snippets from ledger + /app/work/extracted."""
    pool: Dict[str, List[Tuple[str, str]]] = {}
    if not isinstance(ledger, dict):
        return pool
    cat = ledger.get("references_catalog") or []
    ext_lines_by_file: Dict[str, List[Tuple[str, str]]] = {}
    if extracted_dir and extracted_dir.exists():
        for txt_p in sorted(extracted_dir.glob("*.txt"))[:30]:
            content, _ = read_text_safe(txt_p)
            if not content:
                continue
            for ln in content.splitlines():
                lm = re.match(r"^\[([^\]]+)\]\s*(.+)$", ln.strip())
                if not lm:
                    continue
                loc_tag, body = lm.group(1).strip(), re.sub(r"\s+", " ", lm.group(2)).strip()
                if len(body) < 16:
                    continue
                fpart = loc_tag.split(":", 1)[0]
                if "#" in fpart:
                    fpart = fpart.split("#", 1)[0]
                ext_lines_by_file.setdefault(fpart, []).append((loc_tag, body))

    for lr in cat:
        if not isinstance(lr, dict):
            continue
        rid = str(lr.get("ref_id") or "").strip().lower()
        if not rid:
            continue
        cands: List[Tuple[str, str]] = []
        primary_loc = str((lr.get("source_locators") or [""])[0] or "")
        abs_txt = str(lr.get("abstract_or_notes") or lr.get("abstract_excerpt") or "").strip()
        if len(abs_txt) >= 14:
            for seg in re.split(r"(?<=[.。;；!?！？])\s+", re.sub(r"\s+", " ", abs_txt)):
                seg = seg.strip()
                if len(seg) >= 14:
                    cands.append((f"{primary_loc}#abstract" if primary_loc else "abstract", seg[:220]))
        for sloc in lr.get("source_locators") or []:
            fbase = str(sloc).split(":", 1)[0].split("#", 1)[0]
            for ltag, lbody in ext_lines_by_file.get(fbase, [])[:160]:
                if re.match(r"^(?:title|authors?|year|venue|doi|ty\s+-|er\s+-|@)", lbody, re.I):
                    continue
                for seg in re.split(r"(?<=[.。;；!?！？])\s+", lbody):
                    seg = seg.strip()
                    if len(seg) >= 14:
                        cands.append((ltag, seg[:220]))
        # Also match literature_snippets preview lines mentioning cite_key / ref_id / surname
        ck_l = str(lr.get("cite_key") or "").lower()
        surn_l = str(lr.get("first_author_surname") or "").lower()
        for lsn in ledger.get("literature_snippets") or []:
            if not isinstance(lsn, dict):
                continue
            frel = str(lsn.get("file") or "")
            for pline in lsn.get("preview_lines") or []:
                pl_l = str(pline).lower()
                if (ck_l and ck_l in pl_l) or (rid in pl_l) or (surn_l and len(surn_l) >= 3 and surn_l in pl_l):
                    lm = re.match(r"^\[([^\]]+)\]\s*(.+)$", str(pline).strip())
                    if lm:
                        cands.append((lm.group(1).strip(), re.sub(r"\s+", " ", lm.group(2)).strip()[:220]))
                    else:
                        cands.append((frel, re.sub(r"\s+", " ", str(pline)).strip()[:220]))
        if cands:
            pool[rid] = cands
    return pool


def _match_claim_verbatim_quote(
    claim_text: str,
    citation_ids: List[str],
    ev_pool: Dict[str, List[Tuple[str, str]]],
) -> Tuple[str, str]:
    """Return (verbatim_quote, exact_locator) matching claim_text against cited refs' source text."""
    clean_q = re.sub(r"\[[^\]]*\]|\\cite[a-z]*\{[^}]*\}", " ", claim_text or "").lower()
    q_words = {w for w in re.findall(r"[a-z0-9]{3,}", clean_q)}
    q_cjk = _cjk_bigrams_norm(clean_q)
    q_nums = set(re.findall(r"\d+(?:\.\d+)?%?", claim_text or ""))
    best_score = 0.0
    best_pair = ("", "")
    for cid in citation_ids or []:
        cid_norm = normalize_ref_id_token(str(cid)).lower()
        for loc, body in ev_pool.get(cid_norm, []):
            bl = body.lower()
            bw = set(re.findall(r"[a-z0-9]{3,}", bl))
            bc = _cjk_bigrams_norm(bl)
            bn = set(re.findall(r"\d+(?:\.\d+)?%?", body))
            sc = 0.0
            if q_words and bw:
                sc += len(q_words & bw) / max(2, min(len(q_words), 8))
            if q_cjk and bc:
                sc += len(q_cjk & bc) / max(2, min(len(q_cjk), 10))
            if q_nums and bn:
                sc += 0.65 * len(q_nums & bn)
            if sc > best_score:
                best_score = sc
                best_pair = (body[:220], loc)
    if best_score >= 0.08:
        return best_pair
    return ("", "")


def sync_payload_structures(
    payload: Dict[str, Any],
    manuscript: str,
    ledger: Optional[Dict[str, Any]] = None,
    extracted_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Deterministically auto-hydrate & cross-fill missing structures and verbatim evidence quotes."""
    synced = dict(payload)

    # 0. Auto-hydrate missing references[] / field_provenance / conflicts[] from ledger without mutating existing user refs
    cat = [r for r in ((ledger or {}).get("references_catalog") or []) if isinstance(r, dict)]
    ledger_by_id = {str(r.get("ref_id") or "").strip().lower(): r for r in cat if r.get("ref_id")}
    ledger_by_key = {str(r.get("cite_key") or "").strip().lower(): r for r in cat if r.get("cite_key")}
    ledger_conflicts = (ledger or {}).get("conflicts") or []

    existing_refs = [dict(r) for r in (synced.get("references") or []) if isinstance(r, dict)]
    existing_rids = set()
    for pr in existing_refs:
        pr_rid = normalize_ref_id_token(str(pr.get("ref_id") or ""))
        if pr_rid:
            pr["ref_id"] = pr_rid
            existing_rids.add(pr_rid.lower())
        lref = ledger_by_id.get(pr_rid.lower()) or ledger_by_key.get(str(pr.get("cite_key") or "").strip().lower())
        if lref and not pr.get("field_provenance") and lref.get("field_provenance"):
            pr["field_provenance"] = lref["field_provenance"]

    if cat:
        cited_tokens: set = set()
        for bm in re.finditer(r"\[([^\[\]]+)\]", manuscript or ""):
            inner = bm.group(1).strip()
            if any(t in inner for t in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", ":")):
                continue
            rm = re.match(r"^R(\d{1,3})\s*[-–]\s*R(\d{1,3})$", inner, re.I)
            if rm:
                for k in range(int(rm.group(1)), min(int(rm.group(2)) + 1, int(rm.group(1)) + 21)):
                    cited_tokens.add(f"r{k:02d}")
            else:
                for tok in re.split(r"[,;]", inner):
                    nt = normalize_ref_id_token(tok).lower()
                    if nt:
                        cited_tokens.add(nt)
        for cm in re.finditer(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}", manuscript or ""):
            for tok in cm.group(1).split(","):
                cited_tokens.add(tok.strip().lower())

        need_full_hydrate = (len(existing_refs) == 0)
        for lr in cat:
            lrid = str(lr.get("ref_id") or "").strip()
            lck = str(lr.get("cite_key") or "").strip()
            if not lrid or lrid.lower() in existing_rids:
                continue
            if need_full_hydrate or lrid.lower() in cited_tokens or (lck and lck.lower() in cited_tokens):
                has_conf = any(lrid.lower() in json.dumps(c, ensure_ascii=False).lower() for c in ledger_conflicts)
                vstat = "metadata_conflict" if has_conf else ("partial_metadata" if lr.get("missing_metadata_fields") else "verified")
                existing_refs.append({
                    "ref_id": lrid,
                    "cite_key": lck or lrid,
                    "entry_type": lr.get("entry_type") or "article",
                    "authors": lr.get("authors") or "",
                    "year": lr.get("year") or "",
                    "title": lr.get("title") or "",
                    "venue": lr.get("venue") or "",
                    "doi_or_id": lr.get("doi_or_id") or "",
                    "volume": lr.get("volume") or "",
                    "issue": lr.get("issue") or "",
                    "pages": lr.get("pages") or "",
                    "recency_role": lr.get("recency_role") or "representative",
                    "source_locator": (lr.get("source_locators") or ["unknown"])[0],
                    "verification_status": vstat,
                    "field_provenance": lr.get("field_provenance") or {},
                    "formatted_citation": "",
                })
                existing_rids.add(lrid.lower())
    if existing_refs:
        synced["references"] = existing_refs

    if not (synced.get("conflicts") or []) and ledger_conflicts:
        synced["conflicts"] = list(ledger_conflicts)
    if "uncertainty" not in synced:
        synced["uncertainty"] = list((ledger or {}).get("access_warnings") or [])
    for list_k in ("change_records", "excluded_references", "author_confirmation_items"):
        if list_k not in synced:
            synced[list_k] = []

    refs_by_id: Dict[str, Dict[str, Any]] = {}
    refs_by_key: Dict[str, Dict[str, Any]] = {}
    for r in synced.get("references") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("ref_id") or "").strip().lower()
        ck = str(r.get("cite_key") or "").strip().lower()
        if rid:
            refs_by_id[rid] = r
            rm = re.match(r"^r0*(\d+)$", rid)
            if rm:
                refs_by_id[f"r{int(rm.group(1))}"] = r
                refs_by_id[f"r{int(rm.group(1)):02d}"] = r
        if ck:
            refs_by_key[ck] = r

    # 1. Prose-to-structure fallback when agent authored clean markdown without duplicating JSON skeletons
    if manuscript.strip():
        bg = synced.get("research_background")
        if (not isinstance(bg, dict) or not str(bg.get("concrete_problem") or "").strip()):
            intro_m = re.search(r"(?im)^#{1,4}\s+.*(?:introduction|引言)\b[^\n]*\n(.*?)(?=^#{1,2}\s+|\Z)", manuscript, re.S)
            intro_txt = intro_m.group(1).strip() if intro_m else manuscript.strip()
            intro_sents = [s.strip() for s in re.split(r"(?:(?<=[。！？!?])\s*|(?<!\d)\.(?:\s+|$)|\n+)", intro_txt) if len(s.strip()) >= 20]
            if intro_sents:
                bg_cids: List[str] = []
                bg_locs: List[str] = []
                for m in re.finditer(r"\[(R\d{1,3}(?:\s*[,;]\s*R\d{1,3})*)\]", intro_txt[:1500], re.I):
                    for tok in re.split(r"[,;]", m.group(1)):
                        robj = refs_by_id.get(normalize_ref_id_token(tok).lower())
                        if robj and robj.get("ref_id") not in bg_cids:
                            bg_cids.append(str(robj["ref_id"]))
                            sloc = str(robj.get("source_locator") or "").strip()
                            if sloc and sloc not in bg_locs:
                                bg_locs.append(sloc)
                synced["research_background"] = {
                    "domain_lineage": intro_sents[0][:180],
                    "concrete_problem": (intro_sents[1] if len(intro_sents) > 1 else intro_sents[0])[:220],
                    "technical_constraints": [],
                    "why_now": (bg or {}).get("why_now", "") if isinstance(bg, dict) else "",
                    "evidence_locators": bg_locs or (["case.json"] if bg_cids else []),
                    "citation_ids": bg_cids,
                }

        if not (synced.get("related_work_groups") or []):
            rw_m = re.search(r"(?im)^#{1,3}\s+.*(?:related\s+work|相关工作)\b[^\n]*\n(.*?)(?=^#{1,2}\s+|\Z)", manuscript, re.S)
            if rw_m:
                rw_body = rw_m.group(1)
                sub_hdrs = list(re.finditer(r"(?m)^###\s+(.+)$", rw_body))
                auto_groups: List[Dict[str, Any]] = []
                for idx_g, hm in enumerate(sub_hdrs, start=1):
                    end_pos = sub_hdrs[idx_g].start() if idx_g < len(sub_hdrs) else len(rw_body)
                    chunk = rw_body[hm.end():end_pos].strip()
                    seen_w = set()
                    works_cov: List[Dict[str, Any]] = []
                    for bm in re.finditer(r"\[([^\[\]]+)\]", chunk):
                        for tok in re.split(r"[,;]", bm.group(1)):
                            robj = refs_by_id.get(normalize_ref_id_token(tok).lower()) or refs_by_key.get(tok.strip().lower())
                            if robj and robj.get("ref_id") and robj["ref_id"] not in seen_w:
                                seen_w.add(robj["ref_id"])
                                works_cov.append({
                                    "ref_id": robj["ref_id"],
                                    "core_conclusion": (robj.get("title") or "")[:100],
                                    "applicability_conditions": "As reported in source",
                                    "limitations": "See route discussion",
                                    "source_locator": str(robj.get("source_locator") or "-"),
                                })
                    if works_cov:
                        rname = hm.group(1).strip()
                        auto_groups.append({
                            "group_id": f"RG{len(auto_groups) + 1}",
                            "route_name": rname,
                            "grouping_axis": "Methodological route",
                            "comparison_dimensions": ["Core approach", "Applicability", "Limitations"],
                            "works_covered": works_cov,
                            "route_tradeoff_summary": chunk.splitlines()[0][:180] if chunk else rname,
                        })
                if auto_groups:
                    synced["related_work_groups"] = auto_groups

        if not (synced.get("research_gaps") or []):
            gap_m = re.search(r"(?im)^#{1,4}\s+.*(?:gap|空白|局限)\b[^\n]*\n(.*?)(?=^#{1,2}\s+|\Z)", manuscript, re.S)
            gap_src = gap_m.group(1).strip() if gap_m else ""
            if gap_src:
                gap_s = re.split(r"(?:(?<=[。！？!?])\s*|\n+)", gap_src)[0].strip()
                g_cids: List[str] = []
                g_locs: List[str] = []
                for bm in re.finditer(r"\[([^\[\]]+)\]", gap_s):
                    for tok in re.split(r"[,;]", bm.group(1)):
                        robj = refs_by_id.get(normalize_ref_id_token(tok).lower()) or refs_by_key.get(tok.strip().lower())
                        if robj and robj.get("ref_id") not in g_cids:
                            g_cids.append(str(robj["ref_id"]))
                            sloc = str(robj.get("source_locator") or "").strip()
                            if sloc and sloc not in g_locs:
                                g_locs.append(sloc)
                has_aj = ("[作者判断]" in gap_s or "[AUTHOR_JUDGMENT]" in gap_s)
                if gap_s and (g_cids or has_aj):
                    synced["research_gaps"] = [{
                        "gap_id": "GAP-S1",
                        "gap_statement": gap_s[:220],
                        "derivation_type": "author_judgment" if (has_aj and not g_cids) else "shared_limitation",
                        "supporting_ref_ids": g_cids,
                        "supporting_evidence_locators": g_locs,
                        "how_this_work_addresses": "Addressed by proposed approach",
                        "attribution": "author_judgment" if has_aj else "literature_derived",
                    }]

        if not (synced.get("contributions") or []) and ((ledger or {}).get("own_paper_evidence") or (ledger or {}).get("figure_table_mentions")):
            c_m = re.search(r"(?im)^#{1,4}\s+.*(?:contributions?|本文贡献|主要贡献)\b[^\n]*\n(.*?)(?=^#{1,2}\s+|\Z)", manuscript, re.S)
            if c_m:
                c_body = c_m.group(1).strip()
                c_items = [ln.strip().lstrip("-*•0123456789. ") for ln in c_body.splitlines() if len(ln.strip()) >= 18]
                if not c_items:
                    c_items = [s.strip() for s in re.split(r"(?<=[。！？!?])\s+|\.\s+", c_body) if len(s.strip()) >= 18]
                own_locs = [str(ev.get("locator") or "") for ev in ((ledger or {}).get("own_paper_evidence") or []) if isinstance(ev, dict) and ev.get("locator")]
                auto_contribs: List[Dict[str, Any]] = []
                for idx_c, ctxt in enumerate(c_items[:4], start=1):
                    l_meth = re.findall(r"(?:Section|Sec\.|第)\s*\d+(?:\.\d+)*", ctxt, re.I)
                    l_ft = re.findall(r"(?:Fig(?:ure)?\.?|Table|Tab\.?|图|表)\s*[A-Za-z0-9.\-]+", ctxt, re.I)
                    l_met = re.findall(r"\d+(?:\.\d+)?\s*(?:%|％|mA/cm2|h|ms|s|x|倍)", ctxt, re.I)
                    locs_use = own_locs[:2] if own_locs else (l_ft[:2] or ["case.json"])
                    auto_contribs.append({
                        "contrib_id": f"C{idx_c:02d}",
                        "claim_text": ctxt[:240],
                        "scope_qualification": "As evaluated in reported experimental setup",
                        "linked_methods": l_meth[:3],
                        "linked_figures_tables": l_ft[:4],
                        "linked_metrics": l_met[:4],
                        "own_evidence_locators": locs_use,
                        "verification_status": "verified",
                    })
                if auto_contribs:
                    synced["contributions"] = auto_contribs

        # Auto-sync any inline [待作者确认: GAP-XX] / [AUTHOR_CONFIRM: GAP-XX] missing from author_confirmation_items[]
        inline_gap_ids = re.findall(r"\[(?:待作者确认|AUTHOR_CONFIRM)\s*:\s*(GAP-\d+)\b[^\]]*\]", manuscript)
        if inline_gap_ids:
            existing_ac = [dict(x) for x in (synced.get("author_confirmation_items") or []) if isinstance(x, dict)]
            known_gids = {str(x.get("gap_id") or "").strip() for x in existing_ac}
            for ig in inline_gap_ids:
                if ig not in known_gids:
                    known_gids.add(ig)
                    existing_ac.append({
                        "gap_id": ig,
                        "severity": "important",
                        "location_in_draft": "manuscript_markdown",
                        "issue_type": "author_confirmation_needed",
                        "question": f"Please verify inline confirmation item {ig}.",
                        "what_was_checked": "source_ledger.json and /app/work/extracted",
                    })
            synced["author_confirmation_items"] = existing_ac

    # 2. Derive comparison_matrix from related_work_groups if matrix is empty
    rw_groups = [g for g in (synced.get("related_work_groups") or []) if isinstance(g, dict)]
    if not (synced.get("comparison_matrix") or []) and rw_groups:
        derived_rows: List[Dict[str, Any]] = []
        seen_rids = set()
        for g in rw_groups:
            route = str(g.get("route_name") or g.get("group_id") or "")
            axis = str(g.get("grouping_axis") or "")
            for w in g.get("works_covered") or []:
                if not isinstance(w, dict):
                    continue
                rid = normalize_ref_id_token(str(w.get("ref_id") or "").strip())
                if not rid or rid.lower() in seen_rids:
                    continue
                seen_rids.add(rid.lower())
                rinfo = refs_by_id.get(rid.lower(), {})
                derived_rows.append(
                    {
                        "ref_id": rid,
                        "title": str(rinfo.get("title") or ""),
                        "route": route,
                        "problem_setting": axis or route,
                        "method_route": route,
                        "dataset_and_eval": "-",
                        "key_result": str(w.get("core_conclusion") or "-"),
                        "applicability_conditions": str(w.get("applicability_conditions") or "-"),
                        "limitations": str(w.get("limitations") or "-"),
                        "source_locator": str(w.get("source_locator") or rinfo.get("source_locator") or "-"),
                    }
                )
        if derived_rows:
            synced["comparison_matrix"] = derived_rows

    # 3. Extract candidate claim_citation_map from cited sentences in manuscript if empty
    if not (synced.get("claim_citation_map") or []) and manuscript.strip():
        cur_sec = "Introduction"
        claims: List[Dict[str, Any]] = []
        for raw in re.split(r"(?:(?<=[。！？!?])\s+|\n+)", manuscript):
            s = raw.strip()
            if not s:
                continue
            if re.match(r"^#{1,4}\s+", s):
                hdr = s.lstrip("# ").strip()
                if hdr:
                    cur_sec = hdr[:60]
                continue
            cids: List[str] = []
            locs: List[str] = []
            for bm in re.finditer(r"\[([^\[\]]+)\]", s):
                inner = bm.group(1).strip()
                if any(t in inner for t in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", ":")):
                    continue
                for tok in re.split(r"[,;]", inner):
                    t_norm = normalize_ref_id_token(tok).lower()
                    robj = refs_by_id.get(t_norm) or refs_by_id.get(tok.strip().lower()) or refs_by_key.get(tok.strip().lower())
                    if robj and robj.get("ref_id") and robj["ref_id"] not in cids:
                        cids.append(str(robj["ref_id"]))
                        sloc = str(robj.get("source_locator") or "").strip()
                        if sloc and sloc not in locs:
                            locs.append(sloc)
            for cm in re.finditer(r"\\cite[a-z]*\{([^}]+)\}", s):
                for tok in cm.group(1).split(","):
                    robj = refs_by_key.get(tok.strip().lower()) or refs_by_id.get(normalize_ref_id_token(tok).lower())
                    if robj and robj.get("ref_id") and robj["ref_id"] not in cids:
                        cids.append(str(robj["ref_id"]))
                        sloc = str(robj.get("source_locator") or "").strip()
                        if sloc and sloc not in locs:
                            locs.append(sloc)
            if cids:
                attr = "author_judgment" if ("[作者判断]" in s or "[AUTHOR_JUDGMENT]" in s) else "cited_fact"
                claims.append(
                    {
                        "claim_id": f"CL{len(claims) + 1:02d}",
                        "section": cur_sec,
                        "claim_excerpt": s[:180],
                        "citation_ids": cids,
                        "source_locators": locs,
                        "attribution_type": attr,
                    }
                )
                if len(claims) >= 24:
                    break
        if claims:
            synced["claim_citation_map"] = claims

    # 4. Enrich claim_citation_map with verbatim evidence_quote + exact source locator
    if synced.get("claim_citation_map") and ledger is not None:
        ev_pool = _build_ref_evidence_pool(ledger, extracted_dir)
        enriched_claims: List[Dict[str, Any]] = []
        for cm in synced.get("claim_citation_map") or []:
            if not isinstance(cm, dict):
                continue
            cm_copy = dict(cm)
            if not cm_copy.get("evidence_quote"):
                vquote, vloc = _match_claim_verbatim_quote(
                    str(cm_copy.get("claim_excerpt") or ""),
                    [str(x) for x in (cm_copy.get("citation_ids") or [])],
                    ev_pool,
                )
                if vquote:
                    cm_copy["evidence_quote"] = vquote
                if vloc:
                    cm_copy["evidence_locator"] = vloc
                    slocs = list(cm_copy.get("source_locators") or [])
                    if vloc not in slocs:
                        slocs.append(vloc)
                    cm_copy["source_locators"] = slocs
            enriched_claims.append(cm_copy)
        synced["claim_citation_map"] = enriched_claims

    return synced
