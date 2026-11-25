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
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            return None, f"encoding_error:{exc}"
    except PermissionError as exc:
        return None, f"permission_denied:{exc}"
    except Exception as exc:
        return None, f"read_error:{exc}"
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
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
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
    if any(w in fn for w in ("claim_citation", "claim_map", "citation_map", "claim_to_citation")):
        return "claim_citation_map"
    if any(w in fn for w in ("comparison_matrix", "related_work_matrix", "matrix", "comparison_table", "rw_table", "literature_table")):
        return "comparison_matrix"
    if any(w in fn for w in ("research_gap", "gap_list", "gaps", "open_problem")) or fn == "gap":
        return "research_gaps"
    if any(w in fn for w in ("contrib", "contribution")):
        return "contributions"
    if any(w in fn for w in ("conflict", "discrepanc", "mismatch", "inconsisten")):
        return "conflicts"
    if any(w in fn for w in ("confirm", "question", "author_confirm", "todo_author", "open_issue")):
        return "author_confirmation_items"
    if any(w in fn for w in ("change_record", "changes", "revision", "diff", "edit_log", "modification")):
        return "change_records"
    if any(w in fn for w in ("uncertain", "unknown", "warning", "limit", "boundary")):
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
            req_raw = contract.get("required") or contract.get("required_fields") or []
            req_set = set(req_raw) if isinstance(req_raw, list) else set()
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                ftype = _normalize_contract_type(sd.get("type", type(spec).__name__))
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
            or contract.get("type") == "object"
            or isinstance(contract.get("required"), list)
            or isinstance(contract.get("required_fields"), list)
        )
        meta_keys = (
            {"type", "description", "format", "required", "required_fields", "$schema", "title", "additionalProperties"}
            if is_schema_wrapper
            else {"required", "required_fields", "$schema", "additionalProperties"}
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


def extract_pdf_text(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract per-page text from PDF with quality-aware multi-backend fallback and error flags.

    Returns: (pages=[{"page": 1, "text": "...", "method": "..."}], warnings=[...])
    """
    warnings: List[str] = []
    if not path.exists():
        return [], [f"file_not_found:{path}"]
    try:
        header = path.read_bytes()[:1024]
    except Exception as exc:
        return [], [f"read_error:{exc}"]

    if not header.lstrip().startswith(b"%PDF"):
        return [], [f"pdf_corrupt:missing_pdf_header:{path.name}"]

    best_pages: List[Dict[str, Any]] = []
    best_chars = 0

    def consider(cand: List[Dict[str, Any]]) -> None:
        nonlocal best_pages, best_chars
        c = _pdf_total_chars(cand)
        if cand and (not best_pages or c > best_chars):
            best_pages = cand
            best_chars = c

    # 1. pypdf
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

    # 2. fitz (PyMuPDF) - also retry when pypdf returned near-empty text
    if best_chars < 100:
        try:
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore

            doc = fitz.open(str(path))
            if getattr(doc, "needs_pass", False):
                doc.close()
                return [], [f"pdf_encrypted:{path.name}"]
            cand = []
            for i, page in enumerate(doc, start=1):
                cand.append({"page": i, "text": page.get_text("text") or "", "method": "pymupdf"})
            doc.close()
            consider(cand)
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"pymupdf_failed:{exc}")

    # 3. pdfplumber
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

    # 4. pdftotext CLI
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

    pages = best_pages

    # 5. Pure-stdlib fallback: scan PDF text literals or mark corrupt/unavailable
    if not pages:
        raw_bytes = path.read_bytes()
        if b"/Encrypt" in raw_bytes[:16384]:
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


def _clean_bib_value(val: str) -> str:
    val = val.strip().rstrip(",").strip()
    while (val.startswith("{") and val.endswith("}")) or (val.startswith('"') and val.endswith('"')):
        val = val[1:-1].strip()
    val = re.sub(r"(?<!\\)[{}]", "", val)
    val = val.replace(r"\{", "{").replace(r"\}", "}")
    return normalize_ws(val)


def parse_bibtex_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    """Parse BibTeX/BibLaTeX entries with exact starting line numbers and brace/quote safety."""
    entries: List[Dict[str, Any]] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = re.match(r"^\s*@([a-zA-Z]+)\s*([{(])\s*([^,\s]+)\s*,?", line)
        if not m:
            i += 1
            continue
        etype = m.group(1).lower()
        open_delim = m.group(2)
        if etype in ("comment", "preamble", "string"):
            i += 1
            continue
        cite_key = m.group(3).strip()
        start_line = i + 1
        buf_lines = [line]
        paren_mode = open_delim == "("
        depth = (line.count("(") - line.count(")")) if paren_mode else (line.count("{") - line.count("}"))
        i += 1
        while i < n and depth > 0:
            cur = lines[i]
            # Unclosed entry forward recovery (JabRef pattern): stop if a new @type{key starts at col 0
            if re.match(r"^@[a-zA-Z]+\s*[{(]\s*[^,\s]+\s*,", cur) and depth == 1:
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
            ch = body[idx]
            if ch == "{":
                d = 0
                start_v = idx
                while idx < blen:
                    if body[idx] == "{" and (idx == 0 or body[idx - 1] != "\\"):
                        d += 1
                    elif body[idx] == "}" and (idx == 0 or body[idx - 1] != "\\"):
                        d -= 1
                        if d == 0:
                            idx += 1
                            break
                    idx += 1
                val = body[start_v:idx]
            elif ch == '"':
                start_v = idx
                idx += 1
                brace_d = 0
                while idx < blen:
                    c_cur = body[idx]
                    prev_esc = idx > 0 and body[idx - 1] == "\\"
                    if c_cur == "{" and not prev_esc:
                        brace_d += 1
                    elif c_cur == "}" and not prev_esc and brace_d > 0:
                        brace_d -= 1
                    elif c_cur == '"' and not prev_esc and brace_d == 0:
                        idx += 1
                        break
                    idx += 1
                val = body[start_v:idx]
            else:
                start_v = idx
                while idx < blen and body[idx] not in ",\n\r":
                    idx += 1
                val = body[start_v:idx]
            fields[fname] = _clean_bib_value(val)
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
                "pages": fields.get("pages", ""),
                "abstract": fields.get("abstract", "") or fields.get("note", ""),
                "source_locator": f"{source_path}:L{start_line}",
            }
        )
    return entries


def parse_ris_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"authors_list": [], "abstract_parts": [], "start_line": 1}

    def flush(end_lineno: int) -> None:
        if not (cur.get("title") or cur.get("authors_list") or cur.get("doi_or_id")):
            return
        authors = "; ".join(cur.get("authors_list", []))
        year = cur.get("year", "")
        first_surname = extract_first_author_surname(authors) or "Ref"
        cite_key = cur.get("cite_key") or f"{first_surname}{year}"
        entries.append(
            {
                "cite_key": cite_key,
                "entry_type": cur.get("entry_type", "article"),
                "authors": authors,
                "year": year,
                "title": cur.get("title", ""),
                "venue": cur.get("venue", ""),
                "doi_or_id": cur.get("doi_or_id", ""),
                "abstract": " ".join(cur.get("abstract_parts", [])).strip(),
                "source_locator": f"{source_path}:L{cur.get('start_line', end_lineno)}",
            }
        )

    last_lineno = 1
    for lineno, line in enumerate(text.splitlines(), start=1):
        last_lineno = lineno
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", line.strip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
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
    """Extract numbered or bulleted bibliographic lines from markdown/txt/paper ref lists."""
    entries: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            line_cand = line[2:].strip()
            if not re.search(r"\b(?:19|20)\d{2}\b", line_cand) and not re.search(r"10\.\d{4,9}/", line_cand):
                continue
            line = line_cand
        m = re.match(r"^(?:\[([A-Za-z0-9_-]{1,24})\]|(\d{1,3})\.)\s+(.+)$", line)
        if not m:
            continue
        key = m.group(1) or f"ref_{m.group(2)}"
        body = m.group(3).strip()
        ym = re.search(r"\b((?:19|20)\d{2})\b", body)
        doi_m = re.search(r"(10\.\d{4,9}/[^\s,;]+|arXiv:\d{4}\.\d{4,5})", body, re.I)
        if not ym and not doi_m:
            continue
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
}


def _norm_unit(u: str) -> str:
    return _UNIT_NORM.get(u, u)


def extract_numeric_tokens(text: str, filter_structural: bool = True) -> List[Dict[str, Any]]:
    """Extract quantitative numeric tokens while skipping headings, decades, and structural indices."""
    if not text:
        return []
    cleaned = text
    if filter_structural:
        # Mask Markdown section heading numbers (e.g., '## 1.', '### 2.1 ')
        cleaned = re.sub(r"(?m)^\s*#{1,6}\s+\d+(?:\.\d+)*\.?\s*", " ", cleaned)
        # Mask decades like 1990s / 2020s so they never parse as '2020 s'
        cleaned = re.sub(r"\b(?:19|20)\d0s\b", " ", cleaned)
        # Mask structural references: [1], [1, 2], [R01], GAP-01, CONF-01, Fig. 1, Table 2, Eq. (3)
        cleaned = re.sub(r"\[(?:R?\d+(?:\s*[-,–]\s*R?\d+)*)\]", " ", cleaned)
        cleaned = re.sub(r"\b(?:GAP|CONF|CONTRIB|CLAIM|RG|CH|CL|G|C|R)-?\d{1,3}\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"(?:Fig(?:ure)?|Table|Tab\.|Eq\.?|Section|Sec\.|图|表|式|第)\s*\.?\s*\(?\d+(?:\.\d+)?\)?", " ", cleaned, flags=re.I)
        # Mask locators like :L2, :p1_L02, :L10-L20 without eating adjacent numbers
        cleaned = re.sub(r":[Lp]\d+(?:[_-][LpA-Za-z]?\d+)*", " ", cleaned)
        # Mask common precision/dimension/chemical tokens like FP16, BF16, INT8, 3D, 2D, CO2, H2O, L2
        cleaned = re.sub(r"\b(?:FP16|BF16|FP32|INT8|INT4|[1-4]D|L[0-2]|CO2|H2O|O2|N2|CH4|NH3|SiO2|TiO2)\b", " ", cleaned)

    tokens: List[Dict[str, Any]] = []
    left_boundary = r"(?<![A-Za-z0-9_.-])" if filter_structural else r"(?<![0-9_.-])"
    # Trailing (?![A-Za-z]) prevents '5 studies' -> '5 s', '2 groups' -> '2 g', '4 methods' -> '4 m'
    pat = re.compile(
        left_boundary
        + r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        + r"(?:\s*(%|％|x|×|ms|s|min|h|Hz|kHz|MHz|GHz|nm|um|μm|mm|cm|m|km|mg|g|kg|K|°C|Pa|kPa|MPa|GPa|eV|keV|MeV|W|kW|V|mV|A|mA|mol|mmol|kmol|dB|GB|MB|KB|B|fps|tokens)(?![A-Za-z]))?"
    )
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
    """Compare two numeric tokens allowing % <-> decimal equivalence, rounding tolerance, and unit safety."""
    ua = _norm_unit(str(tok_a.get("unit") or ""))
    ub = _norm_unit(str(tok_b.get("unit") or ""))
    if ua and ub and ua != ub:
        # Both carry explicit units and they disagree (e.g. '92.4%' vs '92.4 s', or '50 ms' vs '50 s')
        return False

    va, vb = tok_a["value"], tok_b["value"]
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


def render_inline_citations(md_text: str, refs: List[Dict[str, Any]], style: str, language: str = "zh") -> str:
    """Convert internal [R01] / [R01, R02] markers to target-style citations consistently."""
    if not md_text or not refs:
        return md_text or ""
    eff_style = style or "unspecified"
    if eff_style == "unspecified":
        eff_style = "gbt7714" if language == "zh" else "ieee_numeric"

    by_id: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    by_key: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for idx, r in enumerate(refs, start=1):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("ref_id") or "").strip().lower()
        ck = str(r.get("cite_key") or "").strip().lower()
        if rid:
            by_id[rid] = (idx, r)
        if ck:
            by_key[ck] = (idx, r)

    def _resolve_tok(tok: str) -> Optional[Tuple[int, Dict[str, Any]]]:
        tl = tok.strip().lower()
        return by_id.get(tl) or by_key.get(tl)

    def _replace_bracket(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        # Preserve confirmation/judgment tags and file locators
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", "待补充", ":")):
            return m.group(0)
        # Support R-range like [R01-R03]
        rm = re.match(r"^R(\d{1,3})\s*[-–]\s*R(\d{1,3})$", inner)
        if rm:
            a, b = int(rm.group(1)), int(rm.group(2))
            if 0 < a <= b <= a + 20:
                resolved = [_resolve_tok(f"R{k:02d}") for k in range(a, b + 1)]
                if all(x is not None for x in resolved):
                    items = [x for x in resolved if x is not None]
                else:
                    return m.group(0)
            else:
                return m.group(0)
        else:
            parts = [p.strip() for p in re.split(r"[,;]", inner) if p.strip()]
            if not parts:
                return m.group(0)
            # Only convert when every part is an R-id or exact cite_key (never touch plain [1] or [CO2])
            if not all(re.match(r"^R\d{1,3}$", p) or p.lower() in by_key for p in parts):
                return m.group(0)
            resolved = [_resolve_tok(p) for p in parts]
            if not all(x is not None for x in resolved):
                return m.group(0)
            items = [x for x in resolved if x is not None]

        if eff_style == "bibtex_latex":
            keys = [str(r.get("cite_key") or r.get("ref_id") or f"ref{idx}") for idx, r in items]
            return f"\\cite{{{', '.join(keys)}}}"
        if eff_style == "apa_author_year":
            apa_parts = [format_apa_inline_item(r, language=language) for _, r in items]
            return f"({'; '.join(apa_parts)})"
        if eff_style == "ieee_numeric":
            return ", ".join(f"[{idx}]" for idx, _ in items)
        # gbt7714 / nature_numeric
        return f"[{', '.join(str(idx) for idx, _ in items)}]"

    return re.sub(r"\[([^\[\]]+)\]", _replace_bracket, md_text)


def format_citation_entry(ref: Dict[str, Any], style: str, idx: int) -> str:
    authors = str(ref.get("authors") or "[待补充作者]").strip().rstrip(".")
    year = str(ref.get("year") or "[待补充年份]").strip()
    title = str(ref.get("title") or "[待补充标题]").strip().rstrip(".")
    venue = str(ref.get("venue") or "[待补充来源]").strip().rstrip(".")
    doi = str(ref.get("doi_or_id") or "").strip()
    extra = f" DOI: {doi}." if doi else ""

    if style == "apa_author_year":
        return f"{authors} ({year}). {title}. {venue}.{extra}".strip()
    if style == "ieee_numeric":
        return f"[{idx}] {authors}, \"{title},\" {venue}, {year}.{extra}".strip()
    if style == "nature_numeric":
        return f"{idx}. {authors} {title}. {venue} ({year}).{extra}".strip()
    if style == "bibtex_latex":
        key = ref.get("cite_key") or f"ref{idx}"
        return f"@article{{{key}, author={{{authors}}}, title={{{title}}}, journal={{{venue}}}, year={{{year}}}}}"
    # Default: GB/T 7714-2015
    return f"[{idx}] {authors}. {title}[J/C]. {venue}, {year}.{extra}".strip()
