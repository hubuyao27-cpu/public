#!/usr/bin/env python3
"""Closed-loop validator for result.json against case.json contract and parsed_document.json provenance."""

import argparse
import json
import sys
from pathlib import Path

PLACEHOLDERS = {"todo", "tbd", "placeholder", "n/a_todo", "fill_me", "..."}


def extract_required_contract_keys(case_data: dict):
    """Collect expected top-level output_contract fields from /input/case.json."""
    contract = case_data.get("output_contract")
    if not isinstance(contract, dict):
        return []
    if isinstance(contract.get("required"), list):
        return list(contract["required"])
    if isinstance(contract.get("fields"), dict):
        return list(contract["fields"].keys())
    if isinstance(contract.get("properties"), dict):
        return list(contract["properties"].keys())
    return [k for k in contract.keys() if k not in ("type", "description", "format")]


def audit_result(result_path: str, case_path: str = None, parsed_path: str = None):
    """Verify contract compliance, non-placeholder values, provenance resolution, and honesty."""
    errors = []
    warnings = []

    rp = Path(result_path)
    if not rp.exists():
        return {"passed": False, "errors": [f"Missing result file: {result_path}"], "warnings": []}

    try:
        res = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"passed": False, "errors": [f"Invalid JSON in {result_path}: {exc}"], "warnings": []}

    if not isinstance(res, dict):
        return {"passed": False, "errors": ["result.json top-level value must be a JSON object."], "warnings": []}

    # 1. Envelope checks
    for env_key in ("case_id", "status", "sources", "uncertainty", "change_records"):
        if env_key not in res:
            errors.append(f"Missing required provenance/envelope key: '{env_key}'")

    allowed_status = {"completed", "partial", "unanswerable", "failed"}
    if res.get("status") not in allowed_status:
        warnings.append(f"Non-standard status '{res.get('status')}'; expected one of {sorted(allowed_status)}")

    # 2. Case contract check
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
        if case_data.get("case_id") and res.get("case_id") != case_data.get("case_id"):
            errors.append(f"case_id mismatch: result='{res.get('case_id')}' vs case.json='{case_data.get('case_id')}'")
        for rk in extract_required_contract_keys(case_data):
            if rk not in res:
                errors.append(f"Missing output_contract field: '{rk}'")
            elif res[rk] is None or (isinstance(res[rk], str) and res[rk].strip().lower() in PLACEHOLDERS):
                warnings.append(f"Field '{rk}' is None or placeholder; ensure honest explanation in 'uncertainty' if unreportable.")

    # 3. Provenance grounding check against parsed_document.json
    valid_pages = set()
    valid_ids = set()
    parsed = None
    if parsed_path and Path(parsed_path).exists():
        parsed = json.loads(Path(parsed_path).read_text(encoding="utf-8"))
        for pg in parsed.get("pages", []):
            valid_pages.add(pg.get("page"))
            for blk in pg.get("blocks", []):
                valid_ids.add(blk.get("block_id"))
        for tb in parsed.get("tables", []):
            valid_ids.add(tb.get("table_id"))
        for mt in parsed.get("merged_tables", []):
            valid_ids.add(mt.get("merged_table_id"))
        for eq in parsed.get("equations", []):
            valid_ids.add(eq.get("eq_id"))
        for fg in parsed.get("figures", []):
            valid_ids.add(fg.get("figure_id"))

    sources = res.get("sources", [])
    if not isinstance(sources, list) or (res.get("status") in ("completed", "partial") and len(sources) == 0):
        errors.append("Field 'sources' must be a non-empty list anchoring claims to pages/blocks.")
    elif parsed is not None:
        for idx, src in enumerate(sources):
            if not isinstance(src, dict):
                warnings.append(f"sources[{idx}] is not a dict")
                continue
            pg = src.get("page")
            if pg is not None and valid_pages and pg not in valid_pages:
                errors.append(f"sources[{idx}] cites nonexistent page={pg} (valid: {min(valid_pages)}..{max(valid_pages)})")
            bid = src.get("block_id")
            if bid:
                bids = bid if isinstance(bid, list) else [bid]
                for one_id in bids:
                    if valid_ids and one_id not in valid_ids:
                        errors.append(f"sources[{idx}] cites unknown block/table/eq id '{one_id}'")

    # 4. Uncertainty check when parser flagged scanned/garbled pages
    if parsed is not None:
        ocr_pages = parsed.get("quality_metrics", {}).get("ocr_recommended_pages", [])
        if ocr_pages and not res.get("uncertainty"):
            warnings.append(f"Parser flagged OCR/garble pages {ocr_pages}, yet 'uncertainty' is empty.")

    return {
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit final result.json against case contract and parsed provenance.")
    parser.add_argument("--result", default="/app/output/result.json", help="Path to result.json")
    parser.add_argument("--case", default="/input/case.json", help="Path to case.json")
    parser.add_argument("--parsed", default="/app/work/parsed/parsed_document.json", help="Path to parsed_document.json")
    parser.add_argument("--out", help="Optional path to write audit_report.json")
    args = parser.parse_args()

    report = audit_result(args.result, args.case, args.parsed)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        op = Path(args.out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(payload, encoding="utf-8")
    print(payload)
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
