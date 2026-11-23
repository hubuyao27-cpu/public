#!/usr/bin/env python3
"""Step 1: Inspect case inputs, boundaries, questions, and output_contract.

Scans /input (or custom paths) for case.json, task.md, and source documents,
producing /app/work/case_plan.json as the single source of truth for downstream
parsing, verification, and Harbor output_contract assembly.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import load_json, read_text_safe, save_json, unique_preserve_order


SUPPORTED_DOC_EXTS = {".pdf", ".txt", ".md", ".markdown", ".tex", ".docx", ".xml", ".nxml", ".csv", ".tsv", ".html", ".htm"}
CONTROL_FILENAMES = {"case.json", "task.md", "case_plan.json", "result.json"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract key name to a canonical semantic role."""
    k = f"{field_name} {desc}".lower()
    if any(x in k for x in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(x in k for x in ("status", "state", "run_status")):
        return "status"
    if any(x in k for x in ("answer", "qa", "question", "response", "reply")):
        return "task_responses"
    if any(x in k for x in ("paper_card", "card", "metadata", "bibliographic", "study_profile")):
        return "paper_card"
    if any(x in k for x in ("section_summar", "sections", "outline", "chapter")):
        return "section_summaries"
    if any(x in k for x in ("knowledge", "concept", "glossary", "terminology", "entity", "entries")):
        return "knowledge_items"
    if any(x in k for x in ("evidence", "citation", "anchor", "provenance", "reference_links")):
        return "evidence_index"
    if any(x in k for x in ("source", "input_file", "materials")):
        return "sources"
    if any(x in k for x in ("uncertain", "unknown", "unreported", "conflict", "gap", "discrepanc")):
        return "uncertainty_and_conflicts"
    if any(x in k for x in ("limitation", "caveat", "boundary", "assumption", "precondition")):
        return "limitations_and_preconditions"
    if any(x in k for x in ("manual", "takeover", "human_review")):
        return "manual_takeover_points"
    if any(x in k for x in ("change", "changelog", "history", "audit_log")):
        return "change_records"
    if any(x in k for x in ("audit", "metric", "verification", "check")):
        return "audit_metrics"
    if any(x in k for x in ("finding", "result", "conclusion", "takeaway")):
        return "key_findings"
    if any(x in k for x in ("method", "protocol", "experiment", "setup")):
        return "methodology"
    if any(x in k for x in ("data", "dataset", "sample", "corpus")):
        return "data_and_samples"
    if any(x in k for x in ("summary", "abstract", "overview", "note", "notes", "digest")):
        return "executive_summary"
    return "custom"


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    """Normalize output_contract across dict-template, list, or JSON-Schema shapes."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    required_keys: List[str] = []

    if isinstance(contract, dict):
        # Case A: JSON Schema object with "properties"
        if "properties" in contract and isinstance(contract["properties"], dict):
            req_set = set(contract.get("required", []) if isinstance(contract.get("required"), list) else [])
            for k, spec in contract["properties"].items():
                spec_dict = spec if isinstance(spec, dict) else {"example": spec}
                ftype = str(spec_dict.get("type", type(spec).__name__))
                desc = str(spec_dict.get("description", ""))
                fields.append(
                    {
                        "name": k,
                        "expected_type": ftype,
                        "required": k in req_set,
                        "description": desc,
                        "semantic_role": infer_semantic_role(k, desc),
                        "schema": spec_dict,
                    }
                )
            required_keys = list(contract["properties"].keys())
            return {"raw_type": "json_schema", "fields": fields, "required_keys": required_keys}

        # Case B: Key -> example / type / description dictionary
        for k, val in contract.items():
            if isinstance(val, dict) and ("type" in val or "description" in val):
                ftype = str(val.get("type", "object"))
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype = type(val).__name__ if val is not None else "any"
                desc = val if isinstance(val, str) else ""
                req = True
            fields.append(
                {
                    "name": k,
                    "expected_type": ftype,
                    "required": req,
                    "description": desc,
                    "semantic_role": infer_semantic_role(k, desc),
                    "example": val,
                }
            )
        required_keys = [f["name"] for f in fields if f["required"]]
        return {"raw_type": "dict_template", "fields": fields, "required_keys": required_keys}

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
            elif isinstance(item, dict) and "name" in item:
                name = str(item["name"])
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
        required_keys = [f["name"] for f in fields if f["required"]]
        return {"raw_type": "list", "fields": fields, "required_keys": required_keys}

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def extract_questions(case_data: Dict[str, Any], task_text: str) -> List[Dict[str, str]]:
    """Collect explicit questions or reading sub-tasks from case.json and task.md."""
    items: List[Dict[str, str]] = []
    seen_texts: set[str] = set()

    def add_q(qid: str, text: str, source: str) -> None:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned or cleaned in seen_texts:
            return
        seen_texts.add(cleaned)
        items.append({"id": qid, "question": cleaned, "source": source})

    # 1. From case.json explicit fields
    for key in ("questions", "subquestions", "tasks", "prompts"):
        val = case_data.get(key)
        if isinstance(val, list):
            for idx, entry in enumerate(val, start=1):
                if isinstance(entry, dict):
                    qid = str(entry.get("id") or entry.get("qid") or f"Q{idx}")
                    qtxt = entry.get("question") or entry.get("prompt") or entry.get("text") or json.dumps(entry, ensure_ascii=False)
                    add_q(qid, qtxt, f"case.json:{key}")
                else:
                    add_q(f"Q{idx}", str(entry), f"case.json:{key}")

    for key in ("question", "prompt", "query", "task", "instruction", "instructions"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            add_q(f"CASE_{key.upper()}", val, f"case.json:{key}")

    # 2. From task.md numbered items or question lines
    if task_text:
        q_idx = len(items) + 1
        for line in task_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            is_numbered = bool(re.match(r"^(?:\d+[.)、]|[-*]\s*(?:Q\d+|问题\d+|Question\s*\d+))", stripped, re.IGNORECASE))
            is_question = stripped.endswith("?") or stripped.endswith("？")
            if is_numbered or is_question:
                add_q(f"T{q_idx}", stripped, "task.md")
                q_idx += 1

    return items


def check_pdf_quick(path: Path) -> Dict[str, Any]:
    """Perform a lightweight metadata/encryption check on PDFs if pymupdf is present."""
    info: Dict[str, Any] = {"encrypted": False, "page_count": None, "probe_note": ""}
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(str(path))
        info["encrypted"] = bool(doc.is_encrypted and doc.needs_pass)
        info["page_count"] = len(doc)
        doc.close()
    except ImportError:
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(path))
            info["encrypted"] = bool(doc.is_encrypted and doc.needs_pass)
            info["page_count"] = len(doc)
            doc.close()
        except Exception as exc:
            info["probe_note"] = f"pdf probe skipped: {exc}"
    except Exception as exc:
        info["probe_note"] = f"pdf open error: {exc}"
    return info


def discover_input_files(input_dir: Path, case_data: Dict[str, Any], explicit_files: List[str]) -> List[Dict[str, Any]]:
    """Discover real material files without trusting labels blindly."""
    candidates: List[Path] = []
    for f in explicit_files:
        p = Path(f)
        if p.exists() and p.is_file():
            candidates.append(p)

    # Check paths mentioned in case.json
    for key in ("files", "materials", "papers", "documents", "inputs", "attachments"):
        val = case_data.get(key)
        if isinstance(val, list):
            for entry in val:
                p_str = entry.get("path") if isinstance(entry, dict) else str(entry)
                p = Path(p_str)
                if not p.is_absolute():
                    p = input_dir / p_str
                if p.exists() and p.is_file():
                    candidates.append(p)
    for key in ("paper", "pdf", "document", "input_file", "file", "source_file"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            p = Path(val.strip())
            if not p.is_absolute():
                p = input_dir / val.strip()
            if p.exists() and p.is_file():
                candidates.append(p)

    # Scan input_dir recursively
    if input_dir.exists() and input_dir.is_dir():
        for p in sorted(input_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.name.lower() in CONTROL_FILENAMES:
                continue
            if p.suffix.lower() in SUPPORTED_DOC_EXTS or p.suffix.lower() == ".json":
                candidates.append(p)

    deduped: List[Path] = []
    seen_resolved: set[str] = set()
    for p in candidates:
        rp = str(p.resolve())
        if rp not in seen_resolved:
            seen_resolved.add(rp)
            deduped.append(p)

    records: List[Dict[str, Any]] = []
    for idx, p in enumerate(deduped, start=1):
        ext = p.suffix.lower()
        rec: Dict[str, Any] = {
            "doc_id": f"D{idx}",
            "filename": p.name,
            "path": str(p.resolve()),
            "ext": ext,
            "size_bytes": p.stat().st_size,
        }
        if ext == ".pdf":
            rec.update(check_pdf_quick(p))
        records.append(rec)
    return records


def resolve_optional_path(cli_path: Optional[str], fallbacks: List[str]) -> Optional[Path]:
    if cli_path:
        p = Path(cli_path)
        return p if p.exists() else None
    for fb in fallbacks:
        p = Path(fb)
        if p.exists():
            return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect case boundaries, questions, materials, and output_contract.")
    parser.add_argument("--case", default=None, help="Path to case.json (auto-detects /input/case.json).")
    parser.add_argument("--task", default=None, help="Path to task.md (auto-detects /input/task.md or ./task.md).")
    parser.add_argument("--input-dir", default="/input", help="Input directory containing case materials.")
    parser.add_argument("--files", nargs="*", default=[], help="Optional explicit material file paths.")
    parser.add_argument("--out", default="/app/work/case_plan.json", help="Output path for case_plan.json.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    case_path = resolve_optional_path(args.case, [str(input_dir / "case.json"), "case.json"])
    task_path = resolve_optional_path(args.task, [str(input_dir / "task.md"), "task.md"])

    case_data: Dict[str, Any] = load_json(case_path, {}) if case_path else {}
    if not isinstance(case_data, dict):
        case_data = {"raw_case": case_data}

    task_text = read_text_safe(task_path) if task_path else ""
    materials = discover_input_files(input_dir, case_data, args.files)
    contract_info = parse_output_contract(case_data.get("output_contract"))
    questions = extract_questions(case_data, task_text)

    warnings: List[str] = []
    if not case_path:
        warnings.append("case.json not found; using default Harbor schema and local identifiers.")
    if not materials:
        warnings.append("No readable source documents discovered in input directory.")
    if any(m.get("encrypted") for m in materials):
        warnings.append("At least one input PDF is password-encrypted or permission-restricted.")

    case_plan = {
        "case_id": case_data.get("case_id") or case_data.get("id") or "local-case",
        "case_file": str(case_path.resolve()) if case_path else None,
        "task_file": str(task_path.resolve()) if task_path else None,
        "input_dir": str(input_dir.resolve()) if input_dir.exists() else str(input_dir),
        "task_title": case_data.get("title") or case_data.get("name") or "",
        "raw_task_excerpt": task_text[:4000],
        "constraints": case_data.get("constraints") or case_data.get("input_constraints") or [],
        "questions_detected": questions,
        "output_contract_raw": case_data.get("output_contract"),
        "output_contract_parsed": contract_info,
        "materials": materials,
        "warnings": warnings,
    }
    save_json(args.out, case_plan)
    print(
        f"Inspected case_id={case_plan['case_id']}: "
        f"{len(materials)} material(s), {len(questions)} question(s), "
        f"{len(contract_info['fields'])} contract field(s) -> {args.out}"
    )


if __name__ == "__main__":
    main()
