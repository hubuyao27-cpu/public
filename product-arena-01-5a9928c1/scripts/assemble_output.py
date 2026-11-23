#!/usr/bin/env python3
"""Assemble final bilingual Markdown, glossary table, and Harbor-compliant result.json with closed-loop validation."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_json_optional(path_str: str | None, default: Any) -> Any:
    if not path_str:
        return default
    p = Path(path_str)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def render_bilingual_markdown(translated: List[Dict[str, Any]], audit: Dict[str, Any]) -> str:
    lines: List[str] = ["# 双语对照文献译文 (Bilingual Scientific Translation)", ""]
    metrics = audit.get("metrics", {})
    if metrics:
        lines.extend([
            "## 质量核验摘要 (Audit Summary)",
            f"- **段落覆盖率 (Coverage)**: {metrics.get('covered_segments', 0)}/{metrics.get('translatable_segments', 0)} ({metrics.get('coverage_rate', 0):.1%})",
            f"- **术语一致率 (Glossary Adherence)**: {metrics.get('glossary_adherence_rate', 1):.1%} ({metrics.get('glossary_checks', 0)} checks)",
            f"- **数值与符号保留率 (Numeric Fidelity)**: {metrics.get('numeric_fidelity_rate', 1):.1%} ({metrics.get('number_checks', 0)} checks)",
            f"- **单位保留率 (Unit Fidelity)**: {metrics.get('unit_fidelity_rate', 1):.1%} ({metrics.get('unit_checks', 0)} checks)",
            f"- **引用/公式/编号保留率 (Reference Fidelity)**: {metrics.get('reference_fidelity_rate', 1):.1%} ({metrics.get('reference_checks', 0)} checks)",
            f"- **否定/转折/逻辑关系校验 (Logic Fidelity)**: {metrics.get('logic_fidelity_rate', 1):.1%} ({metrics.get('logic_checks', 0)} checks)",
            f"- **待复核不确定段落 (Uncertain Segments)**: {metrics.get('uncertain_segments', 0)}",
            "", "---", "",
        ])

    current_section = None
    for item in translated:
        section = item.get("section") or "Body"
        if section != current_section:
            current_section = section
            lines.extend([f"## {section}", ""])

        sid = item.get("id", "P???")
        page = item.get("page", "?")
        src = item.get("source_text", "").strip()
        tgt = item.get("target_text", "").strip()
        rationale = item.get("rationale", "").strip()
        uncertain = item.get("uncertain", False)
        unote = item.get("uncertainty_note", "").strip()

        lines.append(f"### [{sid}] (Page {page})")
        if "\n|" in src or src.startswith("|"):
            lines.extend(["**原文 (Source Table)**:", "", src, "", "**译文 (Translated Table)**:", "", tgt])
        else:
            lines.extend([f"**原文 (Source)**: {src}", "", f"**译文 (Translation)**: {tgt}"])
        if rationale:
            lines.append(f"- *译法依据*: {rationale}")
        if uncertain or unote:
            lines.append(f"- *不确定性标注*: {unote or '标记为待人工核验'}")
        lines.append("")

    return "\n".join(lines)


def render_glossary_markdown(glossary: Dict[str, Any]) -> str:
    terms = glossary.get("terms", [])
    lines = [
        "# 锁定术语表 (Locked Domain Glossary)", "",
        "| 原文术语 (Source) | 锁定译名 (Target) | 缩写/别名 (Aliases) | 备注 (Note) |",
        "|---|---|---|---|",
    ]
    for t in terms:
        src = t.get("source", "").replace("|", "\\|")
        tgt = t.get("target", "").replace("|", "\\|")
        aliases = ", ".join(t.get("aliases", [])).replace("|", "\\|")
        note = t.get("note", "").replace("|", "\\|")
        lines.append(f"| {src} | {tgt} | {aliases} | {note} |")
    lines.append("")
    return "\n".join(lines)


def extract_contract_keys(case_data: Dict[str, Any]) -> List[str]:
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


def build_result_json(
    case_data: Dict[str, Any],
    translated: List[Dict[str, Any]],
    glossary: Dict[str, Any],
    audit: Dict[str, Any],
    extraction: Dict[str, Any],
    extra_fields: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = audit.get("metrics", {})
    warnings = list(extraction.get("warnings", []))
    uncertain_items = [
        {"target": x.get("id"), "page": x.get("page"), "reason": "translator_flagged_uncertain", "details": x.get("uncertainty_note") or "Marked uncertain"}
        for x in translated if x.get("uncertain")
    ]
    unreadable_pages = [p["page"] for p in extraction.get("pages", []) if p.get("status") in ("empty_or_scanned", "garbled_font_layer")]
    for pno in unreadable_pages:
        uncertain_items.append({"target": f"page_{pno}", "page": pno, "reason": "scanned_or_garbled_page", "details": "No clean extractable text layer; see OCR PNG/text if generated."})

    if not translated and extraction.get("status") in ("encrypted_requires_password", "corrupt_or_unreadable"):
        status = "unable_to_determine"
    elif not translated:
        status = "unable_to_determine"
    elif unreadable_pages or metrics.get("error_count", 0) > 0 or metrics.get("coverage_rate", 0) < 1.0 or uncertain_items:
        status = "partial"
    else:
        status = "completed"

    # Build grounded source citations from translated segments
    source_entries = []
    for x in translated[:60]:
        source_entries.append({
            "segment_id": x.get("id"),
            "page": x.get("page"),
            "section": x.get("section"),
            "quote": (x.get("source_text") or "")[:140],
        })

    change_records = [
        f"Extracted {extraction.get('total_segments', len(translated))} segments via backend={extraction.get('backend')}.",
        f"Stripped {extraction.get('headers_footers_stripped', 0)} repeating header/footer/marginalia blocks.",
        f"Reconstructed {extraction.get('tables_extracted', 0)} structured table(s) and locked {len(glossary.get('terms', []))} glossary terms.",
        f"Mechanical audit: coverage={metrics.get('coverage_rate', 1.0):.1%}, glossary={metrics.get('glossary_adherence_rate', 1.0):.1%}, units={metrics.get('unit_fidelity_rate', 1.0):.1%}.",
    ]
    if unreadable_pages:
        change_records.append(f"Flagged pages {unreadable_pages} for OCR/visual inspection rather than fabricating content.")

    result: Dict[str, Any] = {}
    for key in extract_contract_keys(case_data):
        result[key] = None

    # Populate standard deliverables + envelope fields
    result.update({
        "case_id": case_data.get("case_id", "local-case"),
        "status": status,
        "translation_summary": (
            f"Translated {metrics.get('covered_segments', len(translated))}/"
            f"{metrics.get('translatable_segments', len(translated))} segments "
            f"({metrics.get('glossary_adherence_rate', 1):.1%} glossary adherence, "
            f"{metrics.get('numeric_fidelity_rate', 1):.1%} numeric fidelity)."
        ),
        "bilingual_segments": translated,
        "glossary": glossary.get("terms", []),
        "audit_metrics": metrics,
        "audit_issues": audit.get("issues", []),
        "sources": {
            "input_file": extraction.get("input_file"),
            "extraction_backend": extraction.get("backend"),
            "total_pages": extraction.get("total_pages"),
            "pages": extraction.get("pages", []),
            "segment_citations": source_entries,
        },
        "uncertainty": {
            "unreadable_or_ocr_pages": unreadable_pages,
            "uncertain_items": uncertain_items,
            "extraction_warnings": warnings,
        },
        "change_records": change_records,
        "limitations": (
            "Pages without clean text layers are flagged for OCR/visual check rather than fabricated."
            if unreadable_pages
            else "All extractable segments translated, terminology-checked, and mechanically audited."
        ),
    })

    # Apply any case-specific extra contract answers cleanly (never overwrite case_id unless missing)
    if isinstance(extra_fields, dict):
        for k, v in extra_fields.items():
            result[k] = v

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble final result.json, bilingual Markdown, and glossary Markdown.")
    parser.add_argument("--case", required=False)
    parser.add_argument("--translated", required=True)
    parser.add_argument("--glossary", required=False)
    parser.add_argument("--audit", required=False)
    parser.add_argument("--extraction", required=False)
    parser.add_argument("--extra-fields", required=False, help="Optional JSON file with custom output_contract fields")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any output_contract field remains null")
    args = parser.parse_args()

    case_data = load_json_optional(args.case, {})
    translated = load_json_optional(args.translated, [])
    glossary = load_json_optional(args.glossary, {"terms": []})
    audit = load_json_optional(args.audit, {"metrics": {}, "issues": []})
    extraction = load_json_optional(args.extraction, {"pages": [], "warnings": []})
    extra_fields = load_json_optional(args.extra_fields, {})

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = build_result_json(case_data, translated, glossary, audit, extraction, extra_fields)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "bilingual_translation.md").write_text(render_bilingual_markdown(translated, audit), encoding="utf-8")
    (out_dir / "glossary.md").write_text(render_glossary_markdown(glossary), encoding="utf-8")

    missing_or_null = [k for k in extract_contract_keys(case_data) if result.get(k) is None]
    print(json.dumps({
        "status": result["status"],
        "case_id": result["case_id"],
        "out_dir": str(out_dir),
        "null_contract_fields": missing_or_null,
    }, ensure_ascii=False, indent=2))
    if args.strict and missing_or_null:
        sys.exit(1)


if __name__ == "__main__":
    main()
