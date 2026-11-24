#!/usr/bin/env python3
"""Step 5: Independent Mechanical Auditor & Hallucination Gate for sci-methods-writer.

Audits /app/work/chapter_payload.json against /app/work/fact_ledger.json and /app/work/case_plan.json:
  1. Zero-hallucination numeric & parameter grounding (exact + SI unit conversion + declared derivations)
  2. Bidirectional Prose <-> Parameter Table numeric alignment
  3. Source locator existence & line-bounds verification
  4. Cross-source conflict disclosure gate (no silent suppression)
  5. Unsourced draft-claim honesty gate
  6. 6-pillar coverage, stage separation, baseline budget-parity, and boilerplate limitation detection
  7. Inline [待作者确认: GAP-XX] / [AUTHOR_CONFIRM: GAP-XX] 1-to-1 consistency
  8. Harbor case_id, output_contract, and envelope completeness
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from common import (
    build_alias_lookup,
    canonicalize_param_key,
    load_json_safe,
    load_resource_json,
    parse_numeric_and_unit,
    save_json,
    values_equivalent,
)

AUTHORITATIVE_TIERS = {
    "runtime_log",
    "cli_script",
    "exp_config",
    "data_table",
    "paper_source",
    "lab_note",
    "code_default",
}

PLACEHOLDERS = {"todo", "tbd", "placeholder", "fill_me", "xxx", "..."}

# High-signal scientific/experimental quantitative claim patterns in prose
PROSE_QUANT_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "unit_quantity",
        re.compile(
            r"(?<![A-Za-z0-9_/\-])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?)?)\s*(°C/min|℃/min|K/min|mL/min|ml/min|L/min|m/s|cm/s|mol/L|wt%|at%|vol%|°C|℃|MPa|kPa|Pa|bar|atm|Torr|mmHg|mL|ml|μL|uL|kg|mg|μg|ug|km|cm|mm|μm|um|nm|Å|hours?|hrs?|mins?|minutes?|min|secs?|seconds?|ms|μs|us|ns|ps|fs|rpm|kV|mV|mA|μA|uA|kW|mW|GHz|MHz|kHz|Hz|TB|GB|MB|KB|%|epochs?|steps?|folds?|replicates?|小时|分钟)(?![A-Za-z0-9_])"
        ),
    ),
    (
        "sci_notation",
        re.compile(r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?))(?![A-Za-z0-9_])"),
    ),
    (
        "named_hyperparam",
        re.compile(
            r"(?:learning\s+rate|batch\s+size|weight\s+decay|dropout(?:\s+rate)?|warmup\s+steps?|random\s+seed|seed|lr|epochs?|学习率|批大小|批次大小|权重衰减|随机种子|训练轮数)\s*(?:of|is|was|=|:|为|设为|设置为)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?)?)",
            re.I,
        ),
    ),
    (
        "split_ratio",
        re.compile(r"(?<![0-9\-/])(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?))(?![0-9\-/])"),
    ),
]

PROSE_PARAM_MENTIONS: List[Tuple[str, re.Pattern[str]]] = [
    ("learning_rate", re.compile(r"(?:learning\s+rate|学习率|\blr\b)\s*(?:of|is|was|=|:|为|设为|设置为)?\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?))", re.I)),
    ("batch_size", re.compile(r"(?:batch\s+size|批大小|批次大小|\bbatch_size\b)\s*(?:of|is|was|=|:|为|设为|设置为)?\s*(\d+)", re.I)),
    ("epochs", re.compile(r"(?:(\d+)\s*(?:epochs?|训练轮次|轮|个\s*epoch)|(?:epochs?|训练轮数)\s*(?:of|is|was|=|:|为|设为)?\s*(\d+))", re.I)),
    ("weight_decay", re.compile(r"(?:weight\s+decay|权重衰减)\s*(?:of|is|was|=|:|为|设为)?\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", re.I)),
    ("dropout", re.compile(r"(?:dropout(?:\s+rate)?)\s*(?:of|is|was|=|:|为|设为)?\s*(0?\.\d+|\d+(?:\.\d+)?%)", re.I)),
    ("temperature", re.compile(r"(?:temperature|temp\.?|calcin\w*\s+at|anneal\w*\s+at|温度|煅烧温度|反应温度)\s*(?:of|at|is|was|=|:|为)?\s*([+-]?\d+(?:\.\d+)?)\s*(°C|℃|K)", re.I)),
    ("pressure", re.compile(r"(?:pressure|压力|反应压力)\s*(?:of|at|is|was|=|:|为)?\s*([+-]?\d+(?:\.\d+)?)\s*(MPa|kPa|Pa|bar|atm)", re.I)),
]


def strip_non_claim_regions(md: str) -> str:
    """Remove headings, code blocks, author-confirm brackets, locator tags, and conflict-history clauses before numeric grounding scan."""
    cleaned_lines = []
    in_fence = False
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Skip markdown headings (e.g. '### 3.1 ...')
        if re.match(r"^#{1,6}\s+", s):
            continue
        # Remove inline [待作者确认: ...] / [AUTHOR_CONFIRM: ...] tags
        line = re.sub(r"\[(?:待作者确认|AUTHOR_CONFIRM)[^\]]*\]", " ", line, flags=re.I)
        # Remove locator citations like (`config.yaml:L12`, `train.log:L8`)
        line = re.sub(r"`[^`]+:(?:L\d+(?:-L\d+)?|p\d+_b\d+|p\d+|t\d+)`", " ", line)
        # Remove structural references (Section 3.1, Step 2, Table 1, Fig. 2, Eq. (3), GAP-01, CONF-01, P01)
        line = re.sub(r"\b(?:Section|Sec\.|Step|Table|Tab\.|Fig\.|Figure|Eq\.|Equation|GAP|CONF|CHG|LIM|S|P)\s*[-#.]?\s*\(?\d+(?:\.\d+)*\)?", " ", line, flags=re.I)
        line = re.sub(r"(?:第\s*\d+\s*[步章节表图条]|表\s*\d+|图\s*\d+|式\s*\(?\d+\)?)", " ", line)
        # Remove citation brackets [1], [1, 2], [1-3]
        line = re.sub(r"\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]", " ", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def build_grounded_pool(ledger: Dict[str, Any], payload: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> List[Tuple[float, str]]:
    """Build deduplicated pool of (numeric_value, unit) from authoritative ledger entries + derived sums + declared values."""
    raw_pool: List[Tuple[float, str]] = []
    unitless_ints: List[int] = []
    for gn in ledger.get("grounded_numbers", []):
        if gn.get("source_tier") in AUTHORITATIVE_TIERS:
            v = float(gn["value"])
            u = str(gn.get("unit") or "")
            raw_pool.append((v, u))
            if not u and v.is_integer() and 0 < v <= 10_000_000 and len(unitless_ints) < 40:
                unitless_ints.append(int(v))
    for p in ledger.get("parameters", []):
        if p.get("source_tier") in AUTHORITATIVE_TIERS:
            nv, nu = parse_numeric_and_unit(p.get("raw_value"))
            if nv is not None:
                raw_pool.append((nv, str(p.get("unit") or nu)))
    # Include pairwise and 3-way sums of small split/sample counts (e.g. 1600 + 200 + 200 = 2000)
    uniq_ints = list(dict.fromkeys(unitless_ints))[:25]
    for i in range(len(uniq_ints)):
        for j in range(i + 1, len(uniq_ints)):
            s2 = uniq_ints[i] + uniq_ints[j]
            raw_pool.append((float(s2), ""))
            for k in range(j + 1, min(len(uniq_ints), i + 8)):
                raw_pool.append((float(s2 + uniq_ints[k]), ""))
    # Include explicit numbers in task_md_excerpt if any
    if plan and plan.get("task_md_excerpt"):
        for m in re.finditer(r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", str(plan["task_md_excerpt"])):
            try:
                raw_pool.append((float(m.group(1)), ""))
            except ValueError:
                pass
    # Allow explicitly documented conflict-compared or draft-cited values (so prose can cite old draft values when documenting a change!)
    for c in payload.get("conflicts", []):
        for sc in c.get("sources_compared", []):
            nv, nu = parse_numeric_and_unit(sc.get("value"))
            if nv is not None:
                raw_pool.append((nv, str(sc.get("unit") or nu)))
    for ch in payload.get("change_records", []):
        for field in ("before", "after"):
            for m in re.finditer(r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", str(ch.get(field) or "")):
                try:
                    raw_pool.append((float(m.group(1)), ""))
                except ValueError:
                    pass
    # Allow explicitly declared derived parameters in parameter_table
    for row in payload.get("parameter_table", []):
        if isinstance(row, dict) and row.get("verification_status") in ("derived", "draft_only_unverified", "conflict_unresolved"):
            nv, nu = parse_numeric_and_unit(row.get("value"))
            if nv is not None:
                raw_pool.append((nv, str(row.get("unit") or nu)))

    # Deduplicate to keep is_value_grounded O(1)-like fast even on large logs
    seen: Set[Tuple[float, str]] = set()
    dedup_pool: List[Tuple[float, str]] = []
    for pv, pu in raw_pool:
        key = (round(pv, 8), pu.strip().lower())
        if key not in seen:
            seen.add(key)
            dedup_pool.append((pv, pu.strip()))
    return dedup_pool


def is_value_grounded(
    val: float,
    unit: str,
    pool: List[Tuple[float, str]],
    unit_conv: Dict[str, Dict[str, float]],
) -> bool:
    for pv, pu in pool:
        if values_equivalent(val, unit, pv, pu, unit_conv):
            return True
    return False


def validate_locator(
    loc: str,
    valid_files: Set[str],
    valid_basenames: Set[str],
    file_line_counts: Dict[str, int],
) -> Tuple[bool, str]:
    """Validate that a source locator resolves to an actual input file (and valid line range if specified)."""
    s = (loc or "").strip()
    if not s:
        return False, "empty_locator"
    low = s.lower()
    if any(tok in low for tok in ("author_confirm", "待作者确认", "derived", "case.json", "task.md", "not_reported")):
        return True, "ok"

    # Extract file part before ':L', ':p', '::', or '#L'
    file_part = re.split(r"::|(?::(?=L\d+|p\d+|t\d+))|#L", s, maxsplit=1)[0].strip().lstrip("./")
    if file_part.startswith("/input/"):
        file_part = file_part[len("/input/"):]

    matched_file: Optional[str] = None
    if file_part in valid_files:
        matched_file = file_part
    elif Path(file_part).name in valid_basenames:
        matched_file = next((vf for vf in valid_files if Path(vf).name == Path(file_part).name), None)

    if not matched_file:
        return False, f"locator_file_not_in_input: '{s}'"

    # Check line number bounds if :Lxx present
    lm = re.search(r":L(\d+)(?:-L(\d+))?", s)
    if lm and matched_file in file_line_counts and file_line_counts[matched_file] > 0:
        max_ln = file_line_counts[matched_file]
        l1 = int(lm.group(1))
        l2 = int(lm.group(2)) if lm.group(2) else l1
        if l1 < 1 or l2 > max_ln + 2:
            return False, f"locator_line_out_of_range: '{s}' (file has {max_ln} lines)"
    return True, "ok"


def audit_chapter(
    payload_path: Path,
    ledger_path: Path,
    case_plan_path: Path,
) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def add_err(cat: str, path: str, msg: str) -> None:
        errors.append({"severity": "ERROR", "category": cat, "path": path, "message": msg})

    def add_warn(cat: str, path: str, msg: str) -> None:
        warnings.append({"severity": "WARNING", "category": cat, "path": path, "message": msg})

    payload, p_err = load_json_safe(payload_path)
    if p_err or not isinstance(payload, dict):
        return {
            "passed": False,
            "error_count": 1,
            "warning_count": 0,
            "errors": [{"severity": "ERROR", "category": "invalid_payload", "path": str(payload_path), "message": str(p_err)}],
            "warnings": [],
            "metrics": {},
        }

    ledger, l_err = load_json_safe(ledger_path)
    if l_err or not isinstance(ledger, dict):
        ledger = {}
        add_warn("missing_ledger", str(ledger_path), f"Could not load fact_ledger.json: {l_err}")

    plan, c_err = load_json_safe(case_plan_path)
    if c_err or not isinstance(plan, dict):
        plan = {}
        add_warn("missing_case_plan", str(case_plan_path), f"Could not load case_plan.json: {c_err}")

    alias_cfg = load_resource_json("parameter_aliases.json")
    rubric = load_resource_json("six_pillar_rubric.json")
    alias_lookup = build_alias_lookup(alias_cfg)
    unit_conv = alias_cfg.get("unit_conversions") or {}

    valid_files = set(ledger.get("valid_files") or [])
    valid_basenames = {Path(vf).name for vf in valid_files}
    # Compute line counts for real text files if accessible (skip >5MB files to avoid slow I/O or truncated-line mismatch)
    file_line_counts: Dict[str, int] = {}
    for item in plan.get("file_inventory", []):
        if item.get("category") != "binary_or_other":
            try:
                fp = Path(item["abs_path"])
                if fp.exists() and fp.suffix.lower() not in (".pdf", ".docx") and fp.stat().st_size <= 2_000_000:
                    file_line_counts[item["rel_path"]] = len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass

    # 1. Envelope & case_id check
    expected_cid = plan.get("case_id")
    if expected_cid and payload.get("case_id") != expected_cid:
        add_err("case_id_mismatch", "case_id", f"Expected '{expected_cid}', got '{payload.get('case_id')}'")

    for req_key in ("manuscript_markdown", "parameter_table", "sources", "uncertainty", "change_records"):
        if req_key not in payload:
            add_err("missing_required_field", req_key, f"Payload missing required key '{req_key}'")

    manuscript = str(payload.get("manuscript_markdown") or "")
    min_prose_len = 15 if str(plan.get("signals", {}).get("task_mode") or "") == "structured_extraction" else 40
    if len(manuscript.strip()) < min_prose_len and ledger.get("stats", {}).get("authoritative_files", 0) > 0:
        add_err("empty_manuscript", "manuscript_markdown", "manuscript_markdown is empty or too short despite available materials.")

    # 2. Numeric & Parameter Grounding Audit (Zero-Hallucination Gate)
    pool = build_grounded_pool(ledger, payload, plan)
    param_table = payload.get("parameter_table") if isinstance(payload.get("parameter_table"), list) else []

    param_checked = 0
    param_grounded = 0
    param_by_canonical: Dict[str, List[Dict[str, Any]]] = {}

    for idx, row in enumerate(param_table):
        if not isinstance(row, dict):
            add_err("invalid_param_row", f"parameter_table[{idx}]", "Row must be an object.")
            continue
        status = str(row.get("verification_status") or "")
        stype = str(row.get("source_type") or "")
        loc = str(row.get("source_locator") or "")
        val = row.get("value")
        unit = str(row.get("unit") or "")

        # Placeholder check
        if isinstance(val, str) and val.strip().lower() in PLACEHOLDERS:
            add_err("placeholder_value", f"parameter_table[{idx}].value", f"Placeholder value '{val}' not allowed; use explicit author_confirm gap.")

        # Locator check
        if status != "missing_author_confirm" and stype != "author_confirm":
            ok_loc, loc_msg = validate_locator(loc, valid_files, valid_basenames, file_line_counts)
            if not ok_loc:
                add_err("invalid_source_locator", f"parameter_table[{idx}].source_locator", loc_msg)

        # Register by canonical key for prose-table cross check
        ck = canonicalize_param_key(str(row.get("canonical_key") or row.get("symbol_or_key") or row.get("name") or ""), alias_lookup)
        if ck:
            param_by_canonical.setdefault(ck, []).append(row)

        # Grounding check on numeric values claimed as verified
        nv, nu = parse_numeric_and_unit(val)
        if nv is not None and status in ("verified", "conflict_resolved_with_note"):
            param_checked += 1
            if pool and not is_value_grounded(nv, unit or nu, pool, unit_conv):
                add_err(
                    "ungrounded_parameter_value",
                    f"parameter_table[{idx}]",
                    f"Parameter '{row.get('name')}'={val} {unit} marked '{status}' is not grounded in authoritative input artifacts.",
                )
            else:
                param_grounded += 1

    # Scan manuscript prose for ungrounded quantitative scientific claims
    cleaned_prose = strip_non_claim_regions(manuscript)
    prose_quant_total = 0
    prose_quant_grounded = 0
    seen_ungrounded: Set[str] = set()

    if pool:
        for pat_name, pat in PROSE_QUANT_PATTERNS:
            for m in pat.finditer(cleaned_prose):
                if pat_name == "split_ratio":
                    parts = [p.strip() for p in m.group(1).split("/")]
                    if re.match(r"^(?:19|20)\d{2}$", parts[0]):
                        continue
                    for p in parts:
                        nv, _ = parse_numeric_and_unit(p)
                        if nv is None:
                            continue
                        prose_quant_total += 1
                        if is_value_grounded(nv, "", pool, unit_conv) or is_value_grounded(nv, "%", pool, unit_conv):
                            prose_quant_grounded += 1
                        else:
                            sig = f"split:{m.group(1)}"
                            if sig not in seen_ungrounded:
                                seen_ungrounded.add(sig)
                                add_err(
                                    "ungrounded_numeric_claim",
                                    "manuscript_markdown",
                                    f"Split ratio '{m.group(1)}' in prose has no grounding in authoritative input files. Remove or tag [待作者确认].",
                                )
                    continue

                raw_num = m.group(1)
                raw_unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                nv, nu = parse_numeric_and_unit(f"{raw_num} {raw_unit}".strip())
                if nv is None:
                    continue
                unit_eff = raw_unit or nu
                # Ignore bare calendar years like 2020..2030 when unitless
                if not unit_eff and 1950 <= nv <= 2035 and nv.is_integer():
                    continue
                prose_quant_total += 1
                if is_value_grounded(nv, unit_eff, pool, unit_conv):
                    prose_quant_grounded += 1
                else:
                    tok = f"{raw_num}{(' ' + unit_eff) if unit_eff else ''}"
                    if tok not in seen_ungrounded:
                        seen_ungrounded.add(tok)
                        add_err(
                            "ungrounded_numeric_claim",
                            "manuscript_markdown",
                            f"Quantitative claim '{tok}' ({pat_name}) in manuscript_markdown is not grounded in any authoritative input artifact. Cite a verified source, declare derivation, or tag [待作者确认].",
                        )

    # 3. Bidirectional Prose <-> Parameter Table Alignment
    prose_table_checks = 0
    prose_table_aligned = 0
    sibling_keys = {
        "batch_size": ("batch_size", "eval_batch_size"),
        "eval_batch_size": ("eval_batch_size", "batch_size"),
    }
    for ck, pat in PROSE_PARAM_MENTIONS:
        candidate_keys = sibling_keys.get(ck, (ck,))
        if not any(k in param_by_canonical for k in candidate_keys):
            continue
        for m in pat.finditer(cleaned_prose):
            val_s = next((g for g in m.groups()[:2] if g and re.search(r"\d", g)), None)
            unit_s = m.group(2) if (ck in ("temperature", "pressure") and m.lastindex and m.lastindex >= 2) else ""
            if not val_s:
                continue
            # Stage-aware key selection for batch_size vs eval_batch_size
            prefix_ctx = cleaned_prose[max(0, m.start() - 14) : m.start()].lower()
            if ck == "batch_size":
                if any(w in prefix_ctx for w in ("eval", "val", "test", "验证", "测试", "推理")):
                    target_keys = ("eval_batch_size", "batch_size") if "eval_batch_size" in param_by_canonical else ("batch_size",)
                elif any(w in prefix_ctx for w in ("train", "训练")):
                    target_keys = ("batch_size",)
                else:
                    target_keys = ("batch_size", "eval_batch_size")
            else:
                target_keys = (ck,)

            table_rows = []
            for tk in target_keys:
                table_rows.extend(
                    r for r in param_by_canonical.get(tk, [])
                    if str(r.get("verification_status") or "") in ("verified", "conflict_resolved_with_note", "derived")
                )
            if not table_rows:
                continue
            prose_table_checks += 1
            matched = any(
                values_equivalent(val_s, unit_s, tr.get("value"), str(tr.get("unit") or ""), unit_conv)
                for tr in table_rows
            )
            if matched:
                prose_table_aligned += 1
            else:
                expected_vals = [f"{tr.get('value')} {tr.get('unit', '')}".strip() for tr in table_rows]
                add_err(
                    "prose_table_mismatch",
                    f"manuscript_markdown <-> parameter_table[{target_keys[0]}]",
                    f"Prose mentions '{m.group(0).strip()}' for '{target_keys[0]}', but parameter_table records {expected_vals}. Align prose and table.",
                )

    # 4. Source Locator Integrity across sources[], method_steps[], change_records[]
    loc_total = 0
    loc_valid = 0
    for idx, src in enumerate(payload.get("sources") or []):
        if isinstance(src, dict) and src.get("source_locator"):
            loc_total += 1
            ok, msg = validate_locator(str(src["source_locator"]), valid_files, valid_basenames, file_line_counts)
            if ok:
                loc_valid += 1
            else:
                add_err("invalid_source_locator", f"sources[{idx}].source_locator", msg)

    for idx, st in enumerate(payload.get("method_steps") or []):
        if isinstance(st, dict):
            for l_idx, sl in enumerate(st.get("source_locators") or []):
                loc_total += 1
                ok, msg = validate_locator(str(sl), valid_files, valid_basenames, file_line_counts)
                if ok:
                    loc_valid += 1
                else:
                    add_err("invalid_source_locator", f"method_steps[{idx}].source_locators[{l_idx}]", msg)

    # 5. Cross-Source Conflict Disclosure Gate
    detected_conflicts = ledger.get("conflicts") or []
    reported_conflicts = payload.get("conflicts") or []
    reported_conf_keys = set()
    for rc in reported_conflicts:
        if isinstance(rc, dict):
            if rc.get("conflict_id"):
                reported_conf_keys.add(str(rc["conflict_id"]).upper())
            if rc.get("item"):
                reported_conf_keys.add(canonicalize_param_key(str(rc["item"]), alias_lookup))
    disclosed_count = 0
    for dc in detected_conflicts:
        cid = str(dc.get("conflict_id") or "").upper()
        ck = str(dc.get("canonical_key") or "")
        if cid in reported_conf_keys or ck in reported_conf_keys:
            disclosed_count += 1
        else:
            add_err(
                "undisclosed_conflict",
                "conflicts",
                f"Detected cross-artifact conflict {cid} ('{ck}') was not disclosed in payload['conflicts']. Disclose both sources and rationale.",
            )

    # 6. Unsourced Draft Claims Honesty Gate
    unsourced_draft = ledger.get("unsourced_draft_claims") or []
    for ud in unsourced_draft:
        ck = str(ud.get("canonical_key") or "")
        for idx, row in enumerate(param_table):
            if not isinstance(row, dict):
                continue
            rck = canonicalize_param_key(str(row.get("canonical_key") or row.get("symbol_or_key") or row.get("name") or ""), alias_lookup)
            if rck == ck and row.get("verification_status") == "verified":
                if values_equivalent(row.get("value"), str(row.get("unit") or ""), ud.get("value"), str(ud.get("unit") or ""), unit_conv):
                    add_err(
                        "unsourced_draft_claimed_verified",
                        f"parameter_table[{idx}]",
                        f"Draft-only uncorroborated claim '{ck}={ud.get('value')}' from '{ud.get('source_locator')}' cannot be marked 'verified'; use 'draft_only_unverified' and add an author confirmation item.",
                    )

    # 7. 6-Pillar Completeness, Stage Separation, Baseline Budget Parity & Boilerplate Detector
    pillars_present = 0
    if payload.get("method_steps"):
        pillars_present += 1
    else:
        add_warn("missing_pillar", "method_steps", "method_steps is empty; populate ordered input->op->output->stop steps or log gap.")

    setup = payload.get("experimental_setup")
    if isinstance(setup, dict) and any(setup.get(k) for k in ("training_or_preparation_protocol", "testing_or_evaluation_protocol", "hardware_or_instruments")):
        pillars_present += 1
    else:
        add_warn("missing_pillar", "experimental_setup", "experimental_setup lacks stage-separated protocols or hardware/environment details.")

    if param_table:
        pillars_present += 1
    if payload.get("datasets_and_splits"):
        pillars_present += 1
    if payload.get("baseline_comparisons"):
        pillars_present += 1
        for bi, bc in enumerate(payload["baseline_comparisons"]):
            if isinstance(bc, dict) and not bc.get("budget_parity_status"):
                add_err("missing_budget_parity", f"baseline_comparisons[{bi}]", "Baseline entry must declare budget_parity_status (matched/partially_matched/unmatched/unknown_author_confirm).")

    repro = payload.get("reproducibility_checklist")
    lims = payload.get("limitations") or []
    if (isinstance(repro, dict) and repro) or lims:
        pillars_present += 1

    # Boilerplate limitation check
    boilerplate_pats = [bp.lower() for bp in (rubric.get("boilerplate_limitation_patterns") or [])]
    for li, lim in enumerate(lims):
        if not isinstance(lim, dict):
            continue
        fs = str(lim.get("failure_scenario") or "")
        tc = str(lim.get("trigger_condition") or "")
        sc = str(lim.get("affected_scope") or "")
        combined_lim = f"{fs} {tc} {sc}".lower()
        has_boilerplate = any(bp in combined_lim for bp in boilerplate_pats)
        if has_boilerplate and (len(tc.strip()) < 15 or len(sc.strip()) < 10):
            add_err(
                "boilerplate_limitation",
                f"limitations[{li}]",
                f"Limitation '{fs[:80]}' uses generic boilerplate without concrete trigger_condition and affected_scope.",
            )
        if not tc.strip() or not sc.strip():
            add_warn("incomplete_limitation", f"limitations[{li}]", "Limitation should specify concrete trigger_condition and affected_scope.")

    # 8. Inline GAP Tag <-> author_confirmation_items 1-to-1 consistency
    inline_gaps = set(re.findall(r"\[(?:待作者确认|AUTHOR_CONFIRM)\s*:\s*(GAP-\d+)\]", manuscript, flags=re.I))
    inline_gaps = {g.upper() for g in inline_gaps}
    author_items = payload.get("author_confirmation_items") or []
    declared_gaps = {str(it.get("gap_id") or "").upper() for it in author_items if isinstance(it, dict) and it.get("gap_id")}

    for ig in sorted(inline_gaps - declared_gaps):
        add_err("dangling_gap_tag", "author_confirmation_items", f"Manuscript cites inline tag '{ig}' not present in author_confirmation_items.")
    for dg in sorted(declared_gaps - inline_gaps):
        add_warn("unanchored_gap_item", "manuscript_markdown", f"author_confirmation_items includes '{dg}' not anchored inline in manuscript_markdown.")

    # Check missing referenced files & ingestion warnings reflected in uncertainty
    if (plan.get("missing_referenced_files") or ledger.get("ingestion_warnings")) and not payload.get("uncertainty"):
        add_err("missing_uncertainty_disclosure", "uncertainty", "Input had missing files or ingestion warnings, yet 'uncertainty' is empty.")

    # 9. output_contract coverage preview
    contract_overrides = payload.get("contract_overrides") if isinstance(payload.get("contract_overrides"), dict) else {}
    for fspec in (plan.get("output_contract") or {}).get("fields", []):
        if fspec.get("required") and fspec.get("semantic_role") == "custom":
            fname = fspec["name"]
            if fname not in contract_overrides and fname not in payload:
                add_warn("custom_contract_field", f"contract_overrides.{fname}", f"Custom output_contract field '{fname}' ({fspec.get('description')}) is not explicitly supplied in contract_overrides.")

    total_quant = param_checked + prose_quant_total
    grounded_quant = param_grounded + prose_quant_grounded
    metrics = {
        "numeric_grounding_rate": round(grounded_quant / total_quant, 4) if total_quant > 0 else 1.0,
        "prose_table_alignment_rate": round(prose_table_aligned / prose_table_checks, 4) if prose_table_checks > 0 else 1.0,
        "locator_validity_rate": round(loc_valid / loc_total, 4) if loc_total > 0 else 1.0,
        "conflict_disclosure_rate": round(disclosed_count / len(detected_conflicts), 4) if detected_conflicts else 1.0,
        "pillar_coverage_rate": round(pillars_present / 6.0, 4),
        "inline_gap_tags_count": len(inline_gaps),
        "author_confirmation_count": len(author_items),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

    return {
        "passed": len(errors) == 0,
        "case_id": payload.get("case_id"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "metrics": metrics,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mechanically verify chapter_payload.json against fact_ledger.json and case_plan.json.")
    parser.add_argument("--payload", default="/app/work/chapter_payload.json", help="Path to chapter_payload.json")
    parser.add_argument("--ledger", default="/app/work/fact_ledger.json", help="Path to fact_ledger.json")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json", help="Path to case_plan.json")
    parser.add_argument("--out", default="/app/work/verification_report.json", help="Output path for verification_report.json")
    args = parser.parse_args()

    report = audit_chapter(Path(args.payload), Path(args.ledger), Path(args.case_plan))
    save_json(Path(args.out), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
