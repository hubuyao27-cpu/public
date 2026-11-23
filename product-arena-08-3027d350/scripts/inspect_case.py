#!/usr/bin/env python3
"""Inspect execution case metadata (/input/case.json and task.md) to align output contracts."""

import argparse
import json
from pathlib import Path


def discover_pdfs(search_roots):
    """Find PDF files across input directories."""
    pdfs = []
    seen = set()
    for root in search_roots:
        p = Path(root)
        if not p.exists():
            continue
        if p.is_file() and p.suffix.lower() == ".pdf":
            rp = str(p.resolve())
            if rp not in seen:
                seen.add(rp)
                pdfs.append(str(p))
        elif p.is_dir():
            for fp in sorted(p.rglob("*.pdf")):
                rp = str(fp.resolve())
                if rp not in seen:
                    seen.add(rp)
                    pdfs.append(str(fp))
    return pdfs


def inspect_case(case_json_path: str, task_md_path: str = None, extra_dir: str = "/input"):
    """Build a structured checklist of case_id, inputs, and required output_contract fields."""
    cpath = Path(case_json_path)
    case_data = {}
    if cpath.exists():
        case_data = json.loads(cpath.read_text(encoding="utf-8"))

    task_text = ""
    if task_md_path and Path(task_md_path).exists():
        task_text = Path(task_md_path).read_text(encoding="utf-8", errors="replace")

    roots = [extra_dir]
    if cpath.parent.exists():
        roots.append(str(cpath.parent))
    for k in ("input_file", "pdf_path", "document_path", "material_path", "files", "materials", "inputs"):
        val = case_data.get(k)
        if isinstance(val, str):
            roots.append(val)
            if not Path(val).is_absolute() and cpath.parent.exists():
                roots.append(str(cpath.parent / val))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    roots.append(item)
                    if not Path(item).is_absolute() and cpath.parent.exists():
                        roots.append(str(cpath.parent / item))
                elif isinstance(item, dict) and "path" in item:
                    roots.append(str(item["path"]))

    pdfs = discover_pdfs(roots)
    contract = case_data.get("output_contract", {})
    required_fields = []
    if isinstance(contract, dict):
        if "required" in contract and isinstance(contract["required"], list):
            required_fields = contract["required"]
        elif "fields" in contract and isinstance(contract["fields"], dict):
            required_fields = list(contract["fields"].keys())
        elif "properties" in contract and isinstance(contract["properties"], dict):
            required_fields = list(contract["properties"].keys())
        else:
            required_fields = [k for k in contract.keys() if k not in ("type", "description", "format")]

    return {
        "case_id": case_data.get("case_id", cpath.stem if cpath.exists() else None),
        "case_json_found": cpath.exists(),
        "task_md_found": bool(task_text),
        "discovered_pdfs": pdfs,
        "output_contract_raw": contract,
        "extracted_contract_fields": required_fields,
        "mandatory_envelope_fields": ["case_id", "status", "sources", "uncertainty", "change_records"],
        "prompt_or_question": case_data.get("prompt") or case_data.get("question") or case_data.get("task") or None,
    }


def main():
    parser = argparse.ArgumentParser(description="Inspect /input/case.json and summarize case requirements.")
    parser.add_argument("--case", default="/input/case.json", help="Path to case.json")
    parser.add_argument("--task", default="/input/task.md", help="Path to task.md")
    parser.add_argument("--input-dir", default="/input", help="Root input directory to scan for PDFs")
    parser.add_argument("--out", help="Optional path to write case_brief.json")
    args = parser.parse_args()

    brief = inspect_case(args.case, args.task, args.input_dir)
    payload = json.dumps(brief, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
