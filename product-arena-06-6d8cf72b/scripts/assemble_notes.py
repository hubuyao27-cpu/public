#!/usr/bin/env python3
"""Step 5: Assemble final reusable deliverables and Harbor-compliant result.json.

Produces:
  - result.json (mapping output_contract fields + full structured provenance)
  - structured_notes.md (YAML frontmatter + section-aligned notes + evidence anchors)
  - knowledge_items.jsonl (retrievable atomic knowledge entries)
  - paper_card_row.csv (fixed-column row for cross-paper comparison)
  - verification_report.json (copy of mechanical audit)
Supports --checkpoint partial for early incremental deliverable saving.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from common import format_anchor, load_json, save_json


def collect_flat_evidence(notes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect deduplicated evidence anchors across all sections, card fields, answers, and items."""
    seen = set()
    flat: List[Dict[str, Any]] = []

    def add_evs(source_path: str, evs: Any) -> None:
        if not isinstance(evs, list):
            return
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            aid = str(ev.get("anchor_id") or "")
            page = ev.get("page")
            sec = ev.get("section")
            quote = str(ev.get("quote") or "")
            key = (aid, page, quote[:80])
            if key not in seen:
                seen.add(key)
                flat.append(
                    {
                        "anchor": format_anchor(aid, page, sec),
                        "anchor_id": aid,
                        "page": page,
                        "section": sec,
                        "quote": quote,
                        "used_in": source_path,
                    }
                )

    for i, tr in enumerate(notes.get("task_responses", [])):
        if isinstance(tr, dict):
            add_evs(f"task_responses[{i}]", tr.get("evidence"))
    card = notes.get("paper_card", {})
    if isinstance(card, dict):
        for k, v in card.items():
            if isinstance(v, dict):
                add_evs(f"paper_card.{k}", v.get("evidence"))
            elif isinstance(v, list):
                for j, item in enumerate(v):
                    if isinstance(item, dict):
                        add_evs(f"paper_card.{k}[{j}]", item.get("evidence"))
    for i, sec in enumerate(notes.get("section_summaries", [])):
        if isinstance(sec, dict):
            add_evs(f"section_summaries[{i}]", sec.get("evidence"))
            for j, kp in enumerate(sec.get("key_points", [])):
                if isinstance(kp, dict):
                    add_evs(f"section_summaries[{i}].key_points[{j}]", kp.get("evidence"))
    for i, ki in enumerate(notes.get("knowledge_items", [])):
        if isinstance(ki, dict):
            add_evs(f"knowledge_items[{i}]", ki.get("evidence"))
    return flat


def build_manual_takeover_points(
    extraction: Dict[str, Any],
    notes: Dict[str, Any],
    verification: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Identify clear human-takeover checkpoints for unreadable/garbled/ambiguous regions."""
    points: List[Dict[str, Any]] = list(notes.get("manual_takeover_points", []) or [])

    for doc in extraction.get("documents", []):
        fname = doc.get("filename", "unknown")
        if doc.get("encrypted"):
            points.append(
                {
                    "location": fname,
                    "reason": "encrypted_pdf",
                    "action_needed": "Provide decrypted PDF or password before extraction can proceed.",
                    "verification_mode": "human_review_required",
                }
            )
        if doc.get("unreadable_pages"):
            points.append(
                {
                    "location": f"{fname} pages {doc['unreadable_pages']}",
                    "reason": "empty_or_scanned_without_ocr_text",
                    "action_needed": "Inspect original scanned page images or run external OCR.",
                    "verification_mode": "human_review_required",
                }
            )
        garbled = [p["page"] for p in doc.get("pages", []) if p.get("status") == "garbled_text_risk"]
        if garbled:
            points.append(
                {
                    "location": f"{fname} pages {garbled}",
                    "reason": "garbled_math_or_font_encoding",
                    "action_needed": "Verify equations/symbols against rendered page images.",
                    "verification_mode": "human_review_required",
                }
            )

    for issue in verification.get("issues", []):
        if issue.get("severity") == "ERROR":
            points.append(
                {
                    "location": issue.get("path", ""),
                    "reason": issue.get("category", "verification_error"),
                    "action_needed": issue.get("message", ""),
                    "verification_mode": "human_review_required",
                }
            )
    return points


def determine_status(
    checkpoint: str,
    extraction: Dict[str, Any],
    notes: Dict[str, Any],
    verification: Dict[str, Any],
) -> str:
    if extraction.get("total_segments", 0) == 0:
        return "unable_to_determine"
    if checkpoint == "partial":
        return "partial"

    metrics = verification.get("metrics", {})
    if extraction.get("any_encrypted") or extraction.get("any_unreadable_pages"):
        return "partial"
    for doc in extraction.get("documents", []):
        if any(p.get("status") == "garbled_text_risk" for p in doc.get("pages", [])):
            return "partial"
    if metrics.get("error_count", 0) > 0:
        return "partial"
    if metrics.get("section_coverage_rate", 1.0) < 1.0:
        return "partial"
    if not (notes.get("paper_card") or notes.get("section_summaries") or notes.get("task_responses")):
        return "partial"
    return "success"


def resolve_contract_field_value(
    field_spec: Dict[str, Any],
    result_core: Dict[str, Any],
    notes: Dict[str, Any],
    flat_evidence: List[Dict[str, Any]],
) -> Any:
    """Resolve an output_contract field cleanly without leaving blind nulls."""
    name = field_spec["name"]
    role = field_spec.get("semantic_role", "custom")
    expected_type = str(field_spec.get("expected_type", "any")).lower()
    overrides = notes.get("contract_overrides", {}) or {}

    if name in overrides and overrides[name] is not None:
        return overrides[name]

    card = result_core.get("paper_card", {})
    role_candidates = {
        "case_id": result_core.get("case_id"),
        "status": result_core.get("status"),
        "task_responses": result_core.get("task_responses"),
        "paper_card": card,
        "section_summaries": result_core.get("section_summaries"),
        "knowledge_items": result_core.get("knowledge_items"),
        "evidence_index": flat_evidence,
        "sources": result_core.get("sources"),
        "uncertainty_and_conflicts": result_core.get("uncertainty"),
        "limitations_and_preconditions": {
            "preconditions": card.get("preconditions", []),
            "limitations": card.get("limitations", []),
        },
        "manual_takeover_points": result_core.get("manual_takeover_points"),
        "change_records": result_core.get("change_records"),
        "audit_metrics": result_core.get("audit_metrics"),
        "key_findings": card.get("key_findings", []),
        "methodology": card.get("methodology_and_conditions", "not_reported"),
        "data_and_samples": card.get("data_or_samples", "not_reported"),
        "executive_summary": result_core.get("executive_summary"),
    }

    val = role_candidates.get(role)
    if val is None or val == "" or val == [] or val == {}:
        if role == "executive_summary" and result_core.get("section_summaries"):
            val = "; ".join(
                f"{s.get('section_id')} {s.get('title')}: {s.get('conclusion') or s.get('results') or s.get('purpose') or ''}"
                for s in result_core["section_summaries"]
            )
        elif role == "task_responses" and expected_type == "string":
            val = result_core.get("executive_summary") or "not_reported"

    # Coerce smoothly if contract expects string while candidate is dict/list
    if expected_type == "string" and isinstance(val, (dict, list)):
        if isinstance(val, dict) and ("summary" in val or "value" in val):
            return str(val.get("summary") or val.get("value"))
        return json.dumps(val, ensure_ascii=False)
    if expected_type == "array" and val is not None and not isinstance(val, list):
        return [val]
    return val if val is not None else "not_reported"


def format_evidence_inline(evs: Any) -> str:
    if not isinstance(evs, list) or not evs:
        return "_(no anchor attached)_"
    parts = []
    for ev in evs:
        if isinstance(ev, dict):
            anchor = format_anchor(str(ev.get("anchor_id", "")), ev.get("page"), ev.get("section"))
            q = str(ev.get("quote") or "").strip()
            parts.append(f"`{anchor}`" + (f' "{q}"' if q else ""))
    return "; ".join(parts) if parts else "_(no anchor attached)_"


def render_structured_markdown(result: Dict[str, Any]) -> str:
    """Render human- and Obsidian/Notion-ready Markdown notes with YAML frontmatter."""
    card = result.get("paper_card", {}) if isinstance(result.get("paper_card"), dict) else {}
    meta = card.get("bibliographic", {}) if isinstance(card.get("bibliographic"), dict) else {}
    title = meta.get("title") or result.get("case_id") or "Structured Literature Notes"
    year = meta.get("year") or "not_reported"
    lang = (result.get("sources") or {}).get("language", "unknown")

    lines = [
        "---",
        f'case_id: "{result.get("case_id", "")}"',
        f'title: "{str(title).replace(chr(34), chr(39))}"',
        f'year: "{year}"',
        f'language: "{lang}"',
        f'status: "{result.get("status", "partial")}"',
        f'generated_at: "{result.get("generated_at", "")}"',
        "---",
        "",
        f"# 结构化文献笔记：{title}",
        "",
        f"- **Case ID**: `{result.get('case_id')}` | **运行状态 (Status)**: `{result.get('status')}`",
    ]

    metrics = result.get("audit_metrics", {})
    if metrics:
        lines.append(
            f"- **机械核验指标**: 锚点有效率 `{metrics.get('anchor_validity_rate', 0):.1%}` | "
            f"引文匹配率 `{metrics.get('quote_match_rate', 0):.1%}` | "
            f"数值溯源率 `{metrics.get('numeric_grounding_rate', 0):.1%}` | "
            f"章节覆盖率 `{metrics.get('section_coverage_rate', 0):.1%}` | "
            f"Errors=`{metrics.get('error_count', 0)}`, Warnings=`{metrics.get('warning_count', 0)}`"
        )
    lines.append("")

    if result.get("executive_summary"):
        lines.extend(["## 执行摘要 (Executive Summary)", "", result["executive_summary"], ""])

    if result.get("task_responses"):
        lines.extend(["## 任务问题回答与原文依据 (Task Q&A with Provenance)", ""])
        for tr in result["task_responses"]:
            if not isinstance(tr, dict):
                continue
            qid = tr.get("question_id", "Q")
            q = tr.get("question", "")
            ans = tr.get("answer", "")
            st = tr.get("status", "answered")
            vm = tr.get("verification_mode", "machine_verifiable")
            lines.append(f"### [{qid}] {q}")
            lines.append(f"- **回答 (`{st}` | `{vm}`)**: {ans}")
            lines.append(f"- **原文依据**: {format_evidence_inline(tr.get('evidence'))}")
            if tr.get("boundaries_or_assumptions"):
                lines.append(f"- **成立边界/前提**: {tr['boundaries_or_assumptions']}")
            lines.append("")

    lines.extend(["## 1. 论文对比卡片 (Structured Paper Card)", ""])
    for key, label in (
        ("research_question", "研究问题 (Research Question)"),
        ("data_or_samples", "数据与样本 (Data & Samples)"),
        ("methodology_and_conditions", "方法与实验/计算条件 (Methodology & Conditions)"),
        ("conclusions", "核心结论 (Conclusions)"),
    ):
        val = card.get(key)
        if isinstance(val, dict):
            txt = val.get("summary") or val.get("value") or val.get("text") or "not_reported"
            lines.append(f"- **{label}**: {txt}")
            lines.append(f"  - *依据*: {format_evidence_inline(val.get('evidence'))}")
        else:
            lines.append(f"- **{label}**: {val or 'not_reported'}")

    for list_key, list_label in (
        ("key_findings", "主要定量与定性发现 (Key Findings)"),
        ("preconditions", "结论成立的关键前提条件 (Critical Preconditions)"),
        ("limitations", "局限性与边界条件 (Limitations & Boundary Conditions)"),
    ):
        arr = card.get(list_key, [])
        lines.append(f"\n### {list_label}")
        if not arr:
            lines.append("- `not_reported`")
        elif isinstance(arr, list):
            for entry in arr:
                if isinstance(entry, dict):
                    txt = entry.get("statement") or entry.get("condition") or entry.get("limitation") or entry.get("text") or ""
                    vm = entry.get("verification_mode", "machine_verifiable")
                    lines.append(f"- [{vm}] {txt} — *依据*: {format_evidence_inline(entry.get('evidence'))}")
                else:
                    lines.append(f"- {entry}")
    lines.append("")

    lines.extend(["## 2. 原文章节承接式摘要 (Section-Aligned Summaries)", ""])
    for sec in result.get("section_summaries", []):
        if not isinstance(sec, dict):
            continue
        sid = sec.get("section_id", "")
        stitle = sec.get("title", "")
        prange = str(sec.get("page_range") or "").strip()
        show_prange = prange and "none" not in prange.lower()
        lines.append(f"### {sid} {stitle}" + (f" (pp. {prange})" if show_prange else ""))
        if sec.get("transition_from_prev"):
            lines.append(f"- **承接上节 (Transition)**: {sec['transition_from_prev']}")
        for subk, subl in (("purpose", "目的"), ("methods", "方法"), ("results", "结果"), ("conclusion", "结论")):
            if sec.get(subk):
                lines.append(f"- **{subl}**: {sec[subk]}")
        for kp in sec.get("key_points", []):
            if isinstance(kp, dict):
                lines.append(
                    f"  - *{kp.get('claim', '')}* — {format_evidence_inline(kp.get('evidence'))}"
                )
        if sec.get("evidence"):
            lines.append(f"- **章节锚点**: {format_evidence_inline(sec.get('evidence'))}")
        lines.append("")

    lines.extend(["## 3. 可检索知识点清单 (Deduplicated Knowledge Items)", ""])
    lines.append("| ID | 类型 | 标准术语 (Canonical) | 别名/缩写 | 定义或论断 | 前提条件 | 锚点 | 核验方式 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for ki in result.get("knowledge_items", []):
        if not isinstance(ki, dict):
            continue
        kid = ki.get("item_id", "")
        kind = ki.get("kind", "")
        term = str(ki.get("canonical_term", "")).replace("|", "\\|")
        aliases = ", ".join(ki.get("aliases", [])).replace("|", "\\|")
        stmt = str(ki.get("statement") or ki.get("definition") or "").replace("|", "\\|")
        pre = "; ".join(ki.get("preconditions", [])).replace("|", "\\|")
        anc = ", ".join(
            format_anchor(str(e.get("anchor_id", "")), e.get("page"), None)
            for e in (ki.get("evidence") or [])
            if isinstance(e, dict)
        )
        vm = ki.get("verification_mode", "machine_verifiable")
        lines.append(f"| {kid} | {kind} | {term} | {aliases} | {stmt} | {pre} | {anc} | {vm} |")
    lines.append("")

    unc = result.get("uncertainty", {})
    lines.extend(["## 4. 未报告项、材料冲突与人工接管点 (Boundaries & Manual Takeover)", ""])
    lines.append(f"- **未报告项 (Not Reported)**: {json.dumps(unc.get('not_reported', []), ensure_ascii=False)}")
    lines.append(f"- **无法判断项 (Unable to Determine)**: {json.dumps(unc.get('unable_to_determine', []), ensure_ascii=False)}")
    lines.append(f"- **材料/数据冲突 (Conflicts)**: {json.dumps(unc.get('conflicts', []), ensure_ascii=False)}")
    lines.append(f"- **人工接管位置 (Manual Takeover)**: {json.dumps(result.get('manual_takeover_points', []), ensure_ascii=False)}")
    lines.append("")
    return "\n".join(lines)


def render_knowledge_jsonl(items: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items if isinstance(item, dict)) + ("\n" if items else "")


def render_paper_card_csv(result: Dict[str, Any]) -> str:
    card = result.get("paper_card", {}) if isinstance(result.get("paper_card"), dict) else {}
    meta = card.get("bibliographic", {}) if isinstance(card.get("bibliographic"), dict) else {}

    def flatten_val(v: Any) -> str:
        if isinstance(v, dict):
            return str(v.get("summary") or v.get("value") or v.get("text") or "not_reported")
        if isinstance(v, list):
            parts = []
            for x in v:
                if isinstance(x, dict):
                    parts.append(str(x.get("statement") or x.get("condition") or x.get("limitation") or x.get("text") or ""))
                else:
                    parts.append(str(x))
            return " | ".join(p for p in parts if p) or "not_reported"
        return str(v) if v else "not_reported"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "case_id",
            "title",
            "authors",
            "year",
            "status",
            "research_question",
            "data_or_samples",
            "methodology_and_conditions",
            "key_findings",
            "preconditions",
            "conclusions",
            "limitations",
            "section_count",
            "knowledge_item_count",
        ]
    )
    writer.writerow(
        [
            result.get("case_id", ""),
            meta.get("title", "not_reported"),
            meta.get("authors", "not_reported"),
            meta.get("year", "not_reported"),
            result.get("status", ""),
            flatten_val(card.get("research_question")),
            flatten_val(card.get("data_or_samples")),
            flatten_val(card.get("methodology_and_conditions")),
            flatten_val(card.get("key_findings")),
            flatten_val(card.get("preconditions")),
            flatten_val(card.get("conclusions")),
            flatten_val(card.get("limitations")),
            len(result.get("section_summaries", [])),
            len(result.get("knowledge_items", [])),
        ]
    )
    return buf.getvalue()


def assemble(
    case_plan: Dict[str, Any],
    extraction: Dict[str, Any],
    outline: Dict[str, Any],
    notes: Dict[str, Any],
    verification: Dict[str, Any],
    checkpoint: str,
) -> Dict[str, Any]:
    case_id = case_plan.get("case_id") or "local-case"
    status = determine_status(checkpoint, extraction, notes, verification)
    flat_evidence = collect_flat_evidence(notes)
    takeover_points = build_manual_takeover_points(extraction, notes, verification)

    nrc = notes.get("not_reported_and_conflicts", {}) or {}
    unreadable_all = [
        {"doc": d.get("filename"), "pages": d.get("unreadable_pages")}
        for d in extraction.get("documents", [])
        if d.get("unreadable_pages")
    ]
    visual_risk_all = [
        {"doc": d.get("filename"), "pages": d.get("visual_or_formula_risk_pages")}
        for d in extraction.get("documents", [])
        if d.get("visual_or_formula_risk_pages")
    ]

    change_records = list(notes.get("change_records", []) or [])
    change_records.append(
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "stage": f"assemble_{checkpoint}",
            "status": status,
            "verification_errors": verification.get("metrics", {}).get("error_count", 0),
        }
    )

    result_core: Dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executive_summary": notes.get("executive_summary", ""),
        "task_responses": notes.get("task_responses", []),
        "paper_card": notes.get("paper_card", {}),
        "section_summaries": notes.get("section_summaries", []),
        "knowledge_items": notes.get("knowledge_items", []),
        "evidence_index": flat_evidence,
        "sources": {
            "documents": extraction.get("documents", []),
            "language": extraction.get("language", "unknown"),
            "total_segments": extraction.get("total_segments", 0),
            "body_sections": outline.get("body_section_ids", []),
            "extraction_warnings": extraction.get("warnings", []),
        },
        "uncertainty": {
            "not_reported": nrc.get("not_reported", []),
            "unable_to_determine": nrc.get("unable_to_determine", []),
            "conflicts": nrc.get("conflicts", []),
            "unreadable_pages": unreadable_all,
            "visual_figure_or_formula_pages": visual_risk_all,
        },
        "limitations": notes.get("limitations_note")
        or (
            "Partial delivery: see manual_takeover_points and uncertainty for gaps/errors."
            if status != "success"
            else "All extractable text sections covered and mechanically verified; figures/tables not transcribed verbatim in text should be visually cross-checked when exact plot readings are critical."
        ),
        "manual_takeover_points": takeover_points,
        "audit_metrics": verification.get("metrics", {}),
        "change_records": change_records,
    }

    # Map every declared output_contract field on top of result_core
    contract_fields = (case_plan.get("output_contract_parsed") or {}).get("fields", [])
    final_result: Dict[str, Any] = {}
    for cf in contract_fields:
        final_result[cf["name"]] = resolve_contract_field_value(cf, result_core, notes, flat_evidence)
    final_result.update(result_core)
    # Re-apply explicit contract_overrides if any share names with result_core except mandatory metadata
    for cf in contract_fields:
        cname = cf["name"]
        if cname in (notes.get("contract_overrides") or {}):
            final_result[cname] = notes["contract_overrides"][cname]
    return final_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble structured notes deliverables and Harbor result.json.")
    parser.add_argument("--case-plan", default=None, help="Path to case_plan.json")
    parser.add_argument("--extraction", default=None, help="Path to extraction_report.json")
    parser.add_argument("--outline", default=None, help="Path to section_outline.json")
    parser.add_argument("--notes", default=None, help="Path to notes_payload.json")
    parser.add_argument("--verification", default=None, help="Path to verification_report.json")
    parser.add_argument("--out-dir", default="/app/output", help="Output directory (e.g., /app/output)")
    parser.add_argument("--checkpoint", choices=("partial", "final"), default="final", help="Checkpoint mode.")
    args = parser.parse_args()

    case_plan = load_json(args.case_plan, {}) if args.case_plan else {}
    extraction = load_json(args.extraction, {"documents": [], "warnings": []}) if args.extraction else {"documents": [], "warnings": []}
    outline = load_json(args.outline, {"body_section_ids": [], "sections": []}) if args.outline else {"body_section_ids": [], "sections": []}
    notes = load_json(args.notes, {}) if args.notes else {}
    verification = load_json(args.verification, {"metrics": {}, "issues": []}) if args.verification else {"metrics": {}, "issues": []}

    result = assemble(case_plan, extraction, outline, notes, verification, args.checkpoint)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    save_json(out_dir / "result.json", result)
    (out_dir / "structured_notes.md").write_text(render_structured_markdown(result), encoding="utf-8")
    (out_dir / "knowledge_items.jsonl").write_text(
        render_knowledge_jsonl(result.get("knowledge_items", [])), encoding="utf-8"
    )
    (out_dir / "paper_card_row.csv").write_text(render_paper_card_csv(result), encoding="utf-8-sig")
    if verification:
        save_json(out_dir / "verification_report.json", verification)

    print(f"Assembled deliverables in {out_dir} (checkpoint={args.checkpoint}, status={result['status']}).")


if __name__ == "__main__":
    main()
