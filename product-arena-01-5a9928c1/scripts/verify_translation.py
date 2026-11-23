#!/usr/bin/env python3
"""Mechanically audit translated segments for coverage, glossary, numbers, units, refs, formulas, tables, and logic cues."""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?")
CITE_NUM_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
FIG_NUM_RE = re.compile(r"\b(?:Fig(?:ure)?s?|Table|Tables|Eq(?:uation)?s?|Scheme|Chart)\s*\.?\s*\(?(\d+[a-zA-Z]?)\)?", re.I)
SUB_SUP_RE = re.compile(r"([A-Za-z0-9\)]+[_^]\{[^}]+\})")
OP_CUES = ["±", "≤", "≥", "≈"]


def load_translation_cues() -> Dict[str, Any]:
    p = RESOURCES_DIR / "translation_cues.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "unit_equivalents": {"kJ/mol": ["kJ/mol", "千焦/摩尔"], "nm": ["nm", "纳米"], "%": ["%", "％"]},
        "negation_en_patterns": [r"\bnot\b", r"\bnever\b", r"\bno\s+significant\b", r"\bneither\b", r"\bnor\b", r"\bwithout\b", r"\bunless\b", r"\bcannot\b"],
        "negation_zh_pattern": r"(?:不|未|无|非|没|除非|不能|未能|并不|绝不|缺乏)",
        "concession_en_pattern": r"\b(?:however|although|whereas|despite)\b",
        "concession_zh_pattern": r"(?:然而|但是|但|尽管|虽然|不过|却|而)",
        "respectively_en_pattern": r"\brespectively\b",
        "respectively_zh_pattern": r"(?:分别|各自|依次)",
    }


def normalize_number(token: str) -> str:
    return token.replace(",", "")


def count_md_table_shape(md: str):
    lines = [ln.strip() for ln in md.splitlines() if ln.strip().startswith("|")]
    data_lines = [ln for ln in lines if not re.match(r"^\|\s*[-:|\s]+\|$", ln)]
    if not data_lines:
        return 0, 0
    cols = max(1, data_lines[0].count("|") - 1)
    return len(data_lines), cols


def audit_translations(
    segments: List[Dict[str, Any]],
    translated: List[Dict[str, Any]],
    glossary_terms: List[Dict[str, Any]],
) -> Dict[str, Any]:
    cues = load_translation_cues()
    neg_en_re = re.compile("|".join(cues.get("negation_en_patterns", [])), re.I)
    neg_zh_re = re.compile(cues.get("negation_zh_pattern", r"(?:不|未|无|非|没)"))
    conc_en_re = re.compile(cues.get("concession_en_pattern", r"\bhowever\b"), re.I)
    conc_zh_re = re.compile(cues.get("concession_zh_pattern", r"(?:然而|但是|尽管|虽然)"))
    resp_en_re = re.compile(cues.get("respectively_en_pattern", r"\brespectively\b"), re.I)
    resp_zh_re = re.compile(cues.get("respectively_zh_pattern", r"(?:分别|各自|依次)"))
    unit_map = cues.get("unit_equivalents", {})

    src_by_id = {seg["id"]: seg for seg in segments}
    tgt_by_id: Dict[str, Dict[str, Any]] = {}
    duplicate_ids: List[str] = []

    for item in translated:
        sid = item.get("id", "")
        if sid in tgt_by_id:
            duplicate_ids.append(sid)
        tgt_by_id[sid] = item

    missing_ids = [seg["id"] for seg in segments if seg.get("kind") != "unreadable_page" and seg["id"] not in tgt_by_id]
    extra_ids = [item["id"] for item in translated if item.get("id") not in src_by_id]

    issues: List[Dict[str, Any]] = []
    glossary_checks = glossary_hits = 0
    number_checks = number_hits = 0
    unit_checks = unit_hits = 0
    ref_checks = ref_hits = 0
    logic_checks = logic_hits = 0
    table_checks = table_hits = 0
    uncertain_ids: List[str] = []

    for seg in segments:
        sid = seg["id"]
        if seg.get("kind") == "unreadable_page":
            continue
        item = tgt_by_id.get(sid)
        if not item:
            issues.append({"severity": "ERROR", "type": "missing_segment", "id": sid, "detail": "Segment missing in translation."})
            continue

        src_text = seg.get("text", "")
        tgt_text = item.get("target_text", "")
        if item.get("uncertain") or "[UNCERTAIN" in tgt_text:
            uncertain_ids.append(sid)

        if src_text.strip() and not tgt_text.strip():
            issues.append({"severity": "ERROR", "type": "empty_translation", "id": sid, "detail": "Target translation is empty."})
            continue

        # 1. Glossary adherence
        for term in glossary_terms:
            src_term = term.get("source", "").strip()
            tgt_term = term.get("target", "").strip()
            if not src_term or not tgt_term:
                continue
            if re.search(r"\b" + re.escape(src_term) + r"\b", src_text, re.I):
                glossary_checks += 1
                allowed = [tgt_term] + [a.strip() for a in term.get("allowed_variants", []) if a.strip()]
                if any(cand in tgt_text for cand in allowed):
                    glossary_hits += 1
                else:
                    issues.append({"severity": "ERROR", "type": "glossary_mismatch", "id": sid, "detail": f"Source has '{src_term}' -> expected '{tgt_term}'."})

        # 2. Numeric & operator fidelity
        src_nums = [normalize_number(n) for n in NUMBER_RE.findall(src_text)]
        tgt_nums = set(normalize_number(n) for n in NUMBER_RE.findall(tgt_text))
        for num in src_nums:
            number_checks += 1
            if num in tgt_nums or num.lstrip("+") in tgt_nums:
                number_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "number_missing", "id": sid, "detail": f"Number '{num}' not found verbatim in target."})

        for op in OP_CUES:
            if op in src_text:
                number_checks += 1
                if op in tgt_text:
                    number_hits += 1
                else:
                    issues.append({"severity": "WARNING", "type": "operator_missing", "id": sid, "detail": f"Operator '{op}' in source missing from target."})

        # 3. Physical unit fidelity
        for ukey, aliases in unit_map.items():
            # Avoid false positives on single-letter units like K, h, s unless preceded by digit
            if len(ukey) <= 2 and ukey != "%":
                pat = re.compile(r"\d\s*" + re.escape(ukey) + r"(?![A-Za-z])")
            else:
                pat = re.compile(r"(?<![A-Za-z])" + re.escape(ukey) + r"(?![A-Za-z])")
            if pat.search(src_text):
                unit_checks += 1
                if any(al in tgt_text for al in aliases):
                    unit_hits += 1
                else:
                    issues.append({"severity": "WARNING", "type": "unit_missing", "id": sid, "detail": f"Unit '{ukey}' in source missing in target (accepted: {aliases})."})

        # 4. Citations & Figure/Table/Equation markers
        for cite in CITE_NUM_RE.findall(src_text):
            ref_checks += 1
            if cite in tgt_text:
                ref_hits += 1
            else:
                issues.append({"severity": "ERROR", "type": "citation_missing", "id": sid, "detail": f"Citation '[{cite}]' missing in target."})

        for fnum in FIG_NUM_RE.findall(src_text):
            ref_checks += 1
            if fnum in tgt_text:
                ref_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "figure_ref_missing", "id": sid, "detail": f"Figure/Table/Eq number '{fnum}' missing in target."})

        # 5. Sub/superscript chemical & math tokens (soft check accepting Unicode or LaTeX equivalents)
        for tok in SUB_SUP_RE.findall(src_text):
            ref_checks += 1
            plain = tok.replace("_{", "").replace("^{", "").replace("}", "")
            if tok in tgt_text or plain in tgt_text or tok.split("_")[0].split("^")[0] in tgt_text:
                ref_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "formula_token_missing", "id": sid, "detail": f"Formula/script token '{tok}' missing in target."})

        # 6. Bidirectional negation, concession, and 'respectively' cues
        has_en_neg = bool(neg_en_re.search(src_text))
        has_zh_neg = bool(neg_zh_re.search(tgt_text))
        if has_en_neg:
            logic_checks += 1
            if has_zh_neg:
                logic_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "negation_missing", "id": sid, "detail": "Source contains negation/restriction cue; verify Chinese negation fidelity."})
        elif re.search(r"(?:未能|绝不|缺乏|没有|并非|不可)", tgt_text):
            issues.append({"severity": "WARNING", "type": "spurious_negation_risk", "id": sid, "detail": "Target contains strong negative cue without obvious English negation; verify polarity."})

        if conc_en_re.search(src_text):
            logic_checks += 1
            if conc_zh_re.search(tgt_text):
                logic_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "concession_missing", "id": sid, "detail": "Source has contrast/concession marker (however/although/despite); verify translation relation."})

        if resp_en_re.search(src_text):
            logic_checks += 1
            if resp_zh_re.search(tgt_text):
                logic_hits += 1
            else:
                issues.append({"severity": "WARNING", "type": "respectively_missing", "id": sid, "detail": "Source uses 'respectively'; verify parallel pairing ('分别/依次')."})

        # 7. Markdown table structure preservation
        if seg.get("kind") == "table":
            table_checks += 1
            sr, sc = count_md_table_shape(src_text)
            tr, tc = count_md_table_shape(tgt_text)
            if sr > 0 and tr >= sr and tc == sc:
                table_hits += 1
            else:
                issues.append({"severity": "ERROR", "type": "table_shape_mismatch", "id": sid, "detail": f"Table shape mismatch: source={sr}x{sc} vs target={tr}x{tc}."})

    translatable = sum(1 for s in segments if s.get("kind") != "unreadable_page")
    covered = sum(1 for s in segments if s.get("kind") != "unreadable_page" and s["id"] in tgt_by_id)
    err_cnt = sum(1 for i in issues if i["severity"] == "ERROR")
    warn_cnt = sum(1 for i in issues if i["severity"] == "WARNING")

    metrics = {
        "total_segments": len(segments),
        "translatable_segments": translatable,
        "covered_segments": covered,
        "coverage_rate": round(covered / translatable, 4) if translatable else 1.0,
        "glossary_checks": glossary_checks,
        "glossary_adherence_rate": round(glossary_hits / glossary_checks, 4) if glossary_checks else 1.0,
        "number_checks": number_checks,
        "numeric_fidelity_rate": round(number_hits / number_checks, 4) if number_checks else 1.0,
        "unit_checks": unit_checks,
        "unit_fidelity_rate": round(unit_hits / unit_checks, 4) if unit_checks else 1.0,
        "reference_checks": ref_checks,
        "reference_fidelity_rate": round(ref_hits / ref_checks, 4) if ref_checks else 1.0,
        "logic_checks": logic_checks,
        "logic_fidelity_rate": round(logic_hits / logic_checks, 4) if logic_checks else 1.0,
        "table_checks": table_checks,
        "table_fidelity_rate": round(table_hits / table_checks, 4) if table_checks else 1.0,
        "uncertain_segments": len(uncertain_ids),
        "error_count": err_cnt,
        "warning_count": warn_cnt,
    }
    return {
        "metrics": metrics,
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "duplicate_ids": duplicate_ids,
        "uncertain_ids": uncertain_ids,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify translated segments against source segments, glossary, units, and logic cues.")
    parser.add_argument("--segments", required=True)
    parser.add_argument("--translated", required=True)
    parser.add_argument("--glossary", required=False)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    segments = json.loads(Path(args.segments).read_text(encoding="utf-8"))
    translated = json.loads(Path(args.translated).read_text(encoding="utf-8"))
    glossary_terms = []
    if args.glossary and Path(args.glossary).exists():
        glossary_terms = json.loads(Path(args.glossary).read_text(encoding="utf-8")).get("terms", [])

    report = audit_translations(segments, translated, glossary_terms)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    m = report["metrics"]
    print(
        f"Audit complete: coverage={m['coverage_rate']:.1%}, glossary={m['glossary_adherence_rate']:.1%}, "
        f"numbers={m['numeric_fidelity_rate']:.1%}, units={m['unit_fidelity_rate']:.1%}, "
        f"logic={m['logic_fidelity_rate']:.1%}, errors={m['error_count']}, warnings={m['warning_count']}"
    )


if __name__ == "__main__":
    main()
