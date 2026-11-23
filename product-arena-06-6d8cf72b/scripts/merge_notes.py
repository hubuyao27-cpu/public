#!/usr/bin/env python3
"""Step 3b: Merge batch note payloads and canonicalize/deduplicate knowledge items."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List

from common import load_json, normalize_for_match, save_json, unique_preserve_order


def is_meaningful(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str):
        low = val.strip().lower()
        return low not in ("", "not_reported", "unknown", "n/a", "none")
    if isinstance(val, (list, dict)):
        return len(val) > 0
    return True


def merge_evidence_lists(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    merged: List[Dict[str, Any]] = []
    for item in (a or []) + (b or []):
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("anchor_id", "")),
            str(item.get("page", "")),
            normalize_for_match(str(item.get("quote", "")))[:60],
        )
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def merge_paper_card(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in incoming.items():
        if k not in out or not is_meaningful(out[k]):
            out[k] = v
            continue
        if isinstance(out[k], list) and isinstance(v, list):
            # Merge list fields cleanly
            if all(isinstance(x, dict) for x in out[k] + v):
                out[k] = merge_evidence_lists(out[k], v) if k == "evidence" else out[k] + [
                    x for x in v if x not in out[k]
                ]
            else:
                out[k] = unique_preserve_order([str(x) for x in out[k] + v])
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_paper_card(out[k], v)
    return out


def canonical_key(term: str) -> str:
    """Normalize term string (stripping trailing parenthetical acronym) for deduplication."""
    s = re.sub(r"\s*[（(][^)）]{1,25}[)）]\s*$", "", str(term or "")).strip()
    return normalize_for_match(s)


def deduplicate_knowledge_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate knowledge items across batches while preserving distinct senses."""
    by_key: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        term = str(item.get("canonical_term") or item.get("term") or item.get("name") or f"item_{idx}")
        kind = str(item.get("kind") or "concept").lower()
        key = f"{kind}::{canonical_key(term)}"

        if key not in by_key:
            copy_item = dict(item)
            copy_item.setdefault("item_id", f"K{len(ordered_keys) + 1:03d}")
            copy_item["canonical_term"] = term
            copy_item["aliases"] = unique_preserve_order([str(a) for a in item.get("aliases", []) if str(a).strip()])
            copy_item["evidence"] = merge_evidence_lists([], item.get("evidence", []))
            copy_item["preconditions"] = unique_preserve_order(
                [str(p) for p in item.get("preconditions", []) if str(p).strip()]
            )
            by_key[key] = copy_item
            ordered_keys.append(key)
        else:
            existing = by_key[key]
            existing["aliases"] = unique_preserve_order(
                existing.get("aliases", []) + [str(a) for a in item.get("aliases", []) if str(a).strip()]
            )
            existing["evidence"] = merge_evidence_lists(existing.get("evidence", []), item.get("evidence", []))
            existing["preconditions"] = unique_preserve_order(
                existing.get("preconditions", []) + [str(p) for p in item.get("preconditions", []) if str(p).strip()]
            )
            new_stmt = str(item.get("statement") or item.get("definition") or "").strip()
            old_stmt = str(existing.get("statement") or existing.get("definition") or "").strip()
            if new_stmt and normalize_for_match(new_stmt) not in normalize_for_match(old_stmt):
                existing["statement"] = f"{old_stmt}; {new_stmt}".strip("; ")

    result = []
    for i, k in enumerate(ordered_keys, start=1):
        entry = by_key[k]
        entry["item_id"] = f"K{i:03d}"
        result.append(entry)
    return result


def merge_payloads(paths: List[Path], outline_path: Path | None = None) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "executive_summary": "",
        "task_responses": [],
        "paper_card": {},
        "section_summaries": [],
        "knowledge_items": [],
        "not_reported_and_conflicts": {"not_reported": [], "unable_to_determine": [], "conflicts": []},
        "contract_overrides": {},
        "limitations_note": "",
        "change_records": [],
    }

    summaries_parts: List[str] = []
    sec_map: Dict[str, Dict[str, Any]] = {}
    resp_map: Dict[str, Dict[str, Any]] = {}
    all_kitems: List[Dict[str, Any]] = []

    for p in paths:
        data = load_json(p, {})
        if not isinstance(data, dict):
            continue

        es = str(data.get("executive_summary") or "").strip()
        if es and es not in summaries_parts:
            summaries_parts.append(es)

        for r in data.get("task_responses", []):
            if isinstance(r, dict):
                qid = str(r.get("question_id") or r.get("id") or f"Q{len(resp_map) + 1}")
                if qid not in resp_map or is_meaningful(r.get("answer")):
                    resp_map[qid] = r

        if isinstance(data.get("paper_card"), dict):
            merged["paper_card"] = merge_paper_card(merged["paper_card"], data["paper_card"])

        for sec in data.get("section_summaries", []):
            if isinstance(sec, dict):
                sid = str(sec.get("section_id") or sec.get("title") or f"S_{len(sec_map)}")
                if sid not in sec_map:
                    sec_map[sid] = sec
                else:
                    # Merge key points & evidence if section spanned batches
                    prev = sec_map[sid]
                    prev["key_points"] = prev.get("key_points", []) + sec.get("key_points", [])
                    prev["evidence"] = merge_evidence_lists(prev.get("evidence", []), sec.get("evidence", []))

        all_kitems.extend(data.get("knowledge_items", []))

        nrc = data.get("not_reported_and_conflicts", {})
        if isinstance(nrc, dict):
            for key in ("not_reported", "unable_to_determine", "conflicts"):
                vals = nrc.get(key, [])
                if isinstance(vals, list):
                    for item in vals:
                        if item not in merged["not_reported_and_conflicts"][key]:
                            merged["not_reported_and_conflicts"][key].append(item)

        if isinstance(data.get("contract_overrides"), dict):
            merged["contract_overrides"].update(data["contract_overrides"])

        if isinstance(data.get("change_records"), list):
            merged["change_records"].extend(data["change_records"])

    merged["executive_summary"] = "\n\n".join(summaries_parts)
    merged["task_responses"] = list(resp_map.values())
    merged["knowledge_items"] = deduplicate_knowledge_items(all_kitems)

    # Order section_summaries by original paper outline order when available
    if outline_path and outline_path.exists():
        outline = load_json(outline_path, {})
        order = [s["section_id"] for s in outline.get("sections", [])]
        rank = {sid: idx for idx, sid in enumerate(order)}
        merged["section_summaries"] = sorted(
            sec_map.values(),
            key=lambda s: rank.get(str(s.get("section_id", "")), 10**6),
        )
    else:
        merged["section_summaries"] = list(sec_map.values())

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge batch notes payloads into canonical notes_payload.json.")
    parser.add_argument("--inputs", nargs="+", required=True, help="Batch JSON files to merge.")
    parser.add_argument("--outline", default=None, help="Optional section_outline.json for original order sorting.")
    parser.add_argument("--out", default="/app/work/notes_payload.json", help="Merged output path.")
    args = parser.parse_args()

    in_paths = [Path(p) for p in args.inputs]
    outline_path = Path(args.outline) if args.outline else None
    merged = merge_payloads(in_paths, outline_path)
    save_json(args.out, merged)
    print(
        f"Merged {len(in_paths)} payload(s) -> {len(merged['section_summaries'])} sections, "
        f"{len(merged['knowledge_items'])} deduplicated knowledge items -> {args.out}"
    )


if __name__ == "__main__":
    main()
