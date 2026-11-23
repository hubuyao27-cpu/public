#!/usr/bin/env python3
"""Step 2: Segment manuscript losslessly, mine terminology/conflicts, and pre-audit diagnostics.

Produces in --out-dir:
- source_segments.json
- glossary_resolved.json
- pre_audit.json
- polish_brief.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, load_resource_json, read_document_any, read_text_safe, save_json, save_text

ABBREVIATIONS = (
    "Fig.", "Figs.", "Eq.", "Eqs.", "Ref.", "Refs.", "Tab.", "Sec.", "Vol.", "No.",
    "Dr.", "Prof.", "Mr.", "Ms.", "et al.", "e.g.", "i.e.", "vs.", "approx.", "ca.",
    "cf.", "inc.", "Ltd.", "Co.", "St.", "U.S.", "p.", "pp.",
)


def mask_protected_for_split(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace abbreviations, decimals, LaTeX math, and citations with safe tokens for sentence splitting."""
    masks: Dict[str, str] = {}
    counter = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal counter
        key = f"__MSK_{counter}__"
        counter += 1
        masks[key] = match.group(0)
        return key

    masked = text
    # Mask math blocks and LaTeX commands with braces
    masked = re.sub(r"\$\$[^$]+\$\$|\$[^$\n]+\$|\\\([^\)]+\\\)|\\\[[^\]]+\\\]", _sub, masked)
    masked = re.sub(r"\\(?:cite|ref|eqref|autoref|label|url|href)\{[^}]+\}", _sub, masked)
    # Mask numeric citations [1, 2] or [1-3] and decimal numbers
    masked = re.sub(r"\d+\.\d+", _sub, masked)
    masked = re.sub(r"\b(?:[A-Z]\.){1,4}", _sub, masked)
    for abbr in ABBREVIATIONS:
        escaped = re.escape(abbr)
        masked = re.sub(rf"(?<!\w){escaped}", _sub, masked)
    return masked, masks


def unmask_text(text: str, masks: Dict[str, str]) -> str:
    """Restore masked spans inside a sentence."""
    out = text
    for k, v in masks.items():
        out = out.replace(k, v)
    return out


def split_sentences(paragraph: str) -> List[str]:
    """Split a paragraph into clean sentences without breaking abbreviations, decimals, or LaTeX."""
    if not paragraph.strip():
        return []
    # Keep Markdown headings, list items, or LaTeX block environments intact as single units when short/structural
    stripped = paragraph.strip()
    if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("\\begin{equation") or stripped.startswith("$$"):
        return [stripped]

    masked, masks = mask_protected_for_split(stripped)
    # Split after EN terminal punctuation (.?!) followed by space + uppercase/CJK/digit/quote/bracket,
    # or after ZH terminal punctuation (。！？)
    parts = re.split(
        r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff\"'(\[\$\\_])|(?<=[。！？])\s*",
        masked,
    )
    sentences: List[str] = []
    for p in parts:
        restored = unmask_text(p, masks).strip()
        if restored:
            sentences.append(restored)
    return sentences if sentences else [stripped]


def parse_paragraphs(raw_text: str) -> List[Dict[str, Any]]:
    """Split manuscript text into structural paragraphs tracking section headings."""
    lines = raw_text.split("\n")
    blocks: List[Dict[str, Any]] = []
    buf: List[str] = []
    current_section = "Document"
    in_code_fence = False
    in_math_env = False
    start_line = 1

    def _flush(end_line: int) -> None:
        nonlocal buf, start_line
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            blocks.append(
                {
                    "section": current_section,
                    "text": text,
                    "start_line": start_line,
                    "end_line": end_line,
                    "is_heading": False,
                }
            )
        buf = []

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("```"):
            in_code_fence = not in_code_fence
        if re.match(r"\\begin\{(?:equation|align|gather|table|figure)\*?\}", s):
            in_math_env = True
        if re.match(r"\\end\{(?:equation|align|gather|table|figure)\*?\}", s):
            buf.append(line)
            in_math_env = False
            _flush(idx)
            start_line = idx + 1
            continue

        if not in_code_fence and not in_math_env:
            md_h = re.match(r"^(#{1,6})\s+(.+)$", s)
            tex_h = re.match(r"^\\(?:part|chapter|section|subsection|subsubsection)\*?\{(.+?)\}", s)
            if md_h or tex_h:
                _flush(idx - 1)
                current_section = (md_h.group(2) if md_h else tex_h.group(1)).strip()  # type: ignore[union-attr]
                blocks.append(
                    {
                        "section": current_section,
                        "text": s,
                        "start_line": idx,
                        "end_line": idx,
                        "is_heading": True,
                    }
                )
                start_line = idx + 1
                continue
            if s == "":
                _flush(idx - 1)
                start_line = idx + 1
                continue

        if not buf:
            start_line = idx
        buf.append(line)

    _flush(len(lines))
    return blocks


def extract_protected_spans(text: str) -> Dict[str, List[str]]:
    """Extract numbers/units, LaTeX math, citations, cross-refs, and chemical formulas."""
    math_latex = re.findall(r"\$\$[^$]+\$\$|\$[^$\n]+\$|\\\([^\)]+\\\)|\\\[[^\]]+\\\]", text)
    citations = re.findall(
        r"\\cite[tp]?\{[^}]+\}|\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]|\([A-Z][A-Za-z\-]+(?:\s+(?:et al\.|and\s+[A-Z][A-Za-z\-]+))?,\s*\d{4}[a-z]?\)",
        text,
    )
    cross_refs = re.findall(
        r"\\(?:eq)?ref\{[^}]+\}|(?:Figs?\.|Figures?|Tables?|Tabs?\.|Eqs?\.|Equations?|Sections?|Secs?\.)\s*\(?\d+(?:\.\d+)*[A-Za-z]?\)?|图\s*\d+(?:\.\d+)*|表\s*\d+(?:\.\d+)*|式\s*[（(]?\d+(?:\.\d+)*[）)]?",
        text,
    )
    # Remove citations & math temporarily before extracting numbers to avoid counting [1] as a data value
    cleaned = text
    for tok in math_latex + citations + cross_refs:
        cleaned = cleaned.replace(tok, " ")
    numbers_units = re.findall(
        r"(?<![A-Za-z0-9_])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?(?:\s*[×xX*]\s*10\^?\{?[+-]?\d+\}?)?(?:\s*(?:%|wt%|vol%|at%|mol%|°C|K|mL/min|mg/mL|mg/L|mmol/L|kJ/mol|eV|nm|μm|um|mm|cm|m|km|ms|s|min|h|Hz|kHz|MHz|GHz|Pa|kPa|MPa|GPa|V|mV|A|mA|W|kW|g|mg|kg|mol|M|rpm|bar|atm))?",
        cleaned,
    )
    numbers_units = [n.strip() for n in numbers_units if n.strip()]
    chemical_symbols = re.findall(r"\b(?:[A-Z][a-z]?\d+)+(?:[-–][A-Z][a-z]?\d*)*\b", cleaned)
    return {
        "numbers_units": numbers_units,
        "math_latex": math_latex,
        "citations": citations,
        "cross_refs": cross_refs,
        "chemical_symbols": chemical_symbols,
    }


def analyze_epistemic_and_polarity(text: str, lexicon: Dict[str, Any]) -> Dict[str, Any]:
    """Compute epistemic hedge tier (1..5), causality/correlation, negation, and conditional/quantifier cues."""
    matched_tiers: List[int] = []
    matched_hedges: List[str] = []
    tiers = lexicon.get("epistemic_tiers", {})
    for tier_data in tiers.values():
        lvl = int(tier_data.get("level", 3))
        for pat in tier_data.get("en_patterns", []):
            for m in re.findall(pat, text, flags=re.IGNORECASE):
                matched_tiers.append(lvl)
                matched_hedges.append(m)
        for pat in tier_data.get("zh_patterns", []):
            if pat in text:
                matched_tiers.append(lvl)
                matched_hedges.append(pat)

    causality_cues: List[str] = []
    correlation_cues: List[str] = []
    for pat in lexicon.get("causality_markers", {}).get("causal", {}).get("en", []):
        causality_cues.extend(re.findall(pat, text, flags=re.IGNORECASE))
    for pat in lexicon.get("causality_markers", {}).get("causal", {}).get("zh", []):
        if pat in text:
            causality_cues.append(pat)
    for pat in lexicon.get("causality_markers", {}).get("correlational", {}).get("en", []):
        correlation_cues.extend(re.findall(pat, text, flags=re.IGNORECASE))
    for pat in lexicon.get("causality_markers", {}).get("correlational", {}).get("zh", []):
        if pat in text:
            correlation_cues.append(pat)

    negations: List[str] = []
    for pat in lexicon.get("negation_markers", {}).get("en", []):
        negations.extend(re.findall(pat, text, flags=re.IGNORECASE))
    for pat in lexicon.get("negation_markers", {}).get("zh", []):
        if pat in text:
            negations.append(pat)

    conditionals: List[str] = []
    quantifiers: List[str] = []
    cq = lexicon.get("conditional_and_quantifier_markers", {})
    for pat in cq.get("conditional_en", []):
        conditionals.extend(re.findall(pat, text, flags=re.IGNORECASE))
    for pat in cq.get("conditional_zh", []):
        if pat in text:
            conditionals.append(pat)
    for pat in cq.get("quantifier_en", []):
        quantifiers.extend(re.findall(pat, text, flags=re.IGNORECASE))
    for pat in cq.get("quantifier_zh", []):
        if pat in text:
            quantifiers.append(pat)

    return {
        "hedge_tier": min(matched_tiers) if matched_tiers else None,
        "max_hedge_tier": max(matched_tiers) if matched_tiers else None,
        "hedges_found": sorted(set(matched_hedges)),
        "causal_cues": sorted(set(causality_cues)),
        "correlational_cues": sorted(set(correlation_cues)),
        "negation_cues": sorted(set(negations)),
        "conditional_cues": sorted(set(conditionals)),
        "quantifier_cues": sorted(set(quantifiers)),
    }


def diagnose_sentence_issues(text: str, rules: Dict[str, Any]) -> List[Dict[str, str]]:
    """Run deterministic diagnostics for Chinglish, colloquial/hype tone, and mechanics."""
    issues: List[Dict[str, str]] = []
    for item in rules.get("chinglish_patterns", []):
        if re.search(item["pattern"], text, flags=re.IGNORECASE):
            issues.append({"rule_id": item["id"], "category": "chinglish_translationese", "detail": item["issue"]})

    flags = rules.get("colloquial_and_promotional_flags", {})
    for pat in flags.get("en", []):
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            issues.append({"rule_id": "REGISTER-EN", "category": "academic_register", "detail": f"Colloquial/promotional/contraction: '{m.group(0)}'"})
    for pat in flags.get("zh", []):
        if pat in text:
            issues.append({"rule_id": "REGISTER-ZH", "category": "academic_register", "detail": f"Colloquial/promotional ZH phrase: '{pat}'"})

    mech = rules.get("punctuation_and_mechanics", {})
    for m_id, pat in mech.items():
        if re.search(pat, text):
            issues.append({"rule_id": f"MECH-{m_id}", "category": "punctuation_mechanics", "detail": f"Mechanics alert: {m_id}"})

    if re.search(r"[\u3040-\u30ff]", text):
        issues.append({"rule_id": "MIXED-SCRIPT-JP", "category": "mixed_script", "detail": "Japanese Kana detected inside manuscript text; confirm intentionality."})
    if "\ufffd" in text:
        issues.append({"rule_id": "ENCODING-REPLACEMENT", "category": "encoding", "detail": "Unicode replacement char U+FFFD detected; inspect encoding."})
    return issues


def parse_glossary_files(glossary_entries: List[Dict[str, Any]], inline_glossary: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse external/inline glossaries and detect internal conflicts."""
    raw_items: List[Dict[str, Any]] = []

    def _add_record(rec: Dict[str, Any], source_loc: str) -> None:
        if not isinstance(rec, dict):
            return
        en = str(rec.get("canonical_en") or rec.get("en") or rec.get("english") or rec.get("source") or rec.get("term") or "").strip()
        zh = str(rec.get("canonical_zh") or rec.get("zh") or rec.get("chinese") or rec.get("target") or "").strip()
        acr = str(rec.get("acronym") or rec.get("abbr") or rec.get("abbreviation") or "").strip()
        avoid = rec.get("avoid") or rec.get("forbidden") or rec.get("disallowed") or []
        if isinstance(avoid, str):
            avoid = [x.strip() for x in re.split(r"[;,|/]", avoid) if x.strip()]
        if en or zh or acr:
            raw_items.append({"canonical_en": en, "canonical_zh": zh, "acronym": acr, "avoid": list(avoid), "note": str(rec.get("note", "")), "source": source_loc})

    if isinstance(inline_glossary, dict):
        if isinstance(inline_glossary.get("terms"), list):
            for idx, r in enumerate(inline_glossary["terms"]):
                _add_record(r, f"case.json:glossary.terms[{idx}]")
        else:
            for k, v in inline_glossary.items():
                if isinstance(v, str):
                    _add_record({"source": k, "target": v}, f"case.json:glossary.{k}")
                elif isinstance(v, dict):
                    _add_record({"source": k, **v}, f"case.json:glossary.{k}")
    elif isinstance(inline_glossary, list):
        for idx, r in enumerate(inline_glossary):
            _add_record(r, f"case.json:glossary[{idx}]")

    for gf in glossary_entries:
        p = Path(gf["path"])
        rel = gf["rel_path"]
        ext = p.suffix.lower()
        if ext == ".json":
            data, _ = load_json_safe(p, default=None)
            if isinstance(data, dict) and isinstance(data.get("terms"), list):
                for idx, r in enumerate(data["terms"]):
                    _add_record(r, f"{rel}:terms[{idx}]")
            elif isinstance(data, list):
                for idx, r in enumerate(data):
                    _add_record(r, f"{rel}[{idx}]")
            elif isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str):
                        _add_record({"source": k, "target": v}, f"{rel}:{k}")
        elif ext in (".csv", ".tsv"):
            txt, _, _ = read_text_safe(p)
            if txt:
                delim = "\t" if ext == ".tsv" else ","
                reader = csv.DictReader(txt.splitlines(), delimiter=delim)
                for row_i, row in enumerate(reader, start=2):
                    norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                    _add_record(
                        {
                            "canonical_en": norm.get("canonical_en") or norm.get("en") or norm.get("english") or norm.get("source") or norm.get("term") or "",
                            "canonical_zh": norm.get("canonical_zh") or norm.get("zh") or norm.get("chinese") or norm.get("target") or "",
                            "acronym": norm.get("acronym") or norm.get("abbr") or "",
                            "avoid": norm.get("avoid") or norm.get("forbidden") or "",
                            "note": norm.get("note") or "",
                        },
                        f"{rel}:L{row_i}",
                    )
        else:
            txt, _, _ = read_text_safe(p)
            if txt:
                for line_i, line in enumerate(txt.splitlines(), start=1):
                    m = re.match(r"^\|\s*([^|]+)\|\s*([^|]+)\|", line.strip())
                    if m and not set(m.group(1).strip()) <= {"-", ":"}:
                        c1, c2 = m.group(1).strip(), m.group(2).strip()
                        if c1.lower() not in ("term", "source", "english", "术语", "英文"):
                            _add_record({"canonical_en": c1, "canonical_zh": c2}, f"{rel}:L{line_i}")

    # Detect conflicts (same normalized EN or ZH mapped to different targets)
    by_en: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_zh: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in raw_items:
        if item["canonical_en"]:
            by_en[item["canonical_en"].lower()].append(item)
        if item["canonical_zh"]:
            by_zh[item["canonical_zh"]].append(item)

    conflicts: List[Dict[str, Any]] = []
    seen_conflict_keys = set()
    for key, group in by_en.items():
        zhs = sorted({g["canonical_zh"] for g in group if g["canonical_zh"]})
        acrs = sorted({g["acronym"] for g in group if g["acronym"]})
        if len(zhs) > 1 or len(acrs) > 1:
            seen_conflict_keys.add(("en", key))
            conflicts.append(
                {
                    "conflict_id": f"GCONF-{len(conflicts)+1:02d}",
                    "key": key,
                    "dimension": "en_to_zh_or_acronym",
                    "candidates": group,
                }
            )
    for key, group in by_zh.items():
        ens = sorted({g["canonical_en"] for g in group if g["canonical_en"]})
        if len(ens) > 1 and ("zh", key) not in seen_conflict_keys:
            conflicts.append(
                {
                    "conflict_id": f"GCONF-{len(conflicts)+1:02d}",
                    "key": key,
                    "dimension": "zh_to_en",
                    "candidates": group,
                }
            )

    # Deduplicate identical canonical entries
    deduped: List[Dict[str, Any]] = []
    seen_pairs = set()
    for item in raw_items:
        sig = (item["canonical_en"].lower(), item["canonical_zh"], item["acronym"])
        if sig not in seen_pairs:
            seen_pairs.add(sig)
            deduped.append(item)

    return deduped, conflicts


def mine_terminology_and_acronyms(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mine Full Term (ACR) definitions, acronym order issues, and hyphen/spelling drifts."""
    full_term_defs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    acronym_mentions: Dict[str, List[str]] = defaultdict(list)
    hyphen_variants: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))

    for seg in segments:
        sid = seg["sentence_id"]
        txt = seg["original"]
        # Full Term (ACR) pattern
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9\- ]{3,55}?)\s*\(([A-Z][A-Z0-9\-]{1,10})\)", txt):
            full_term_defs[m.group(2)].append({"full_term": m.group(1).strip(), "sentence_id": sid, "section": seg["section"]})
        # Standalone uppercase acronyms
        for acr in re.findall(r"\b[A-Z]{2,8}\b", txt):
            if acr not in {"II", "III", "IV", "VI", "DNA", "RNA", "PCR", "SEM", "TEM", "XRD", "XPS", "FTIR", "NMR", "USA", "IEEE", "NSFC"}:
                acronym_mentions[acr].append(sid)
        # Hyphenated vs unhyphenated compound drift (e.g., high-performance vs high performance)
        for m in re.finditer(r"\b([a-z]{3,})-([a-z]{3,})\b", txt.lower()):
            joined = f"{m.group(1)}-{m.group(2)}"
            spaced = f"{m.group(1)} {m.group(2)}"
            hyphen_variants[f"{m.group(1)}_{m.group(2)}"][joined].append(sid)
            # Check elsewhere handled post-pass

    # Cross-check spaced forms for discovered hyphen pairs
    for base_key, variants in list(hyphen_variants.items()):
        w1, w2 = base_key.split("_", 1)
        spaced = f"{w1} {w2}"
        solid = f"{w1}{w2}"
        for seg in segments:
            low = seg["original"].lower()
            if re.search(rf"\b{re.escape(spaced)}\b", low):
                variants[spaced].append(seg["sentence_id"])
            if re.search(rf"\b{re.escape(solid)}\b", low):
                variants[solid].append(seg["sentence_id"])

    drift_candidates = [
        {"concept_key": k, "variants": {vk: sorted(set(vids)) for vk, vids in vdict.items()}}
        for k, vdict in hyphen_variants.items()
        if len(vdict) > 1
    ]

    acronym_issues: List[Dict[str, Any]] = []
    for acr, defs in full_term_defs.items():
        distinct_full = sorted({d["full_term"].lower() for d in defs})
        if len(distinct_full) > 1:
            acronym_issues.append({"type": "conflicting_expansion", "acronym": acr, "definitions": defs})
        first_def_sid = defs[0]["sentence_id"]
        earlier = [sid for sid in acronym_mentions.get(acr, []) if sid < first_def_sid]
        if earlier:
            acronym_issues.append({"type": "used_before_definition", "acronym": acr, "first_used": earlier[0], "defined_at": first_def_sid})

    return {
        "mined_acronyms": dict(full_term_defs),
        "acronym_issues": acronym_issues,
        "hyphen_drift_candidates": drift_candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze manuscript and build compact polishing brief.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--out-dir", default="/app/work")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_plan, err = load_json_safe(Path(args.case_plan), default={})
    if err or not isinstance(case_plan, dict):
        raise SystemExit(f"Cannot load case_plan: {err}")

    lexicon = load_resource_json("hedge_and_causality_lexicon.json")
    rules = load_resource_json("chinglish_and_register_rules.json")

    docs: List[Dict[str, Any]] = []
    read_warnings: List[str] = list(case_plan.get("warnings", []))

    for mf in case_plan.get("manuscript_files", []):
        p = Path(mf["path"])
        txt, fmt, warn = read_document_any(p)
        if warn:
            read_warnings.append(warn)
        if txt.strip():
            docs.append({"rel_path": mf["rel_path"], "format": fmt, "text": txt})

    if not docs and case_plan.get("inline_text"):
        docs.append({"rel_path": "case.json:inline_text", "format": "inline", "text": case_plan["inline_text"]})

    segments: List[Dict[str, Any]] = []
    p_counter = 0
    for doc in docs:
        blocks = parse_paragraphs(doc["text"])
        for blk in blocks:
            p_counter += 1
            pid = f"P{p_counter:03d}"
            sents = [blk["text"]] if blk["is_heading"] else split_sentences(blk["text"])
            for s_idx, sent in enumerate(sents, start=1):
                sid = f"{pid}.S{s_idx:02d}"
                spans = extract_protected_spans(sent)
                ep = analyze_epistemic_and_polarity(sent, lexicon)
                diags = diagnose_sentence_issues(sent, rules)
                segments.append(
                    {
                        "sentence_id": sid,
                        "paragraph_id": pid,
                        "doc_rel_path": doc["rel_path"],
                        "doc_format": doc["format"],
                        "section": blk["section"],
                        "is_heading": blk["is_heading"],
                        "line_range": [blk["start_line"], blk["end_line"]],
                        "original": sent,
                        "protected_spans": spans,
                        "epistemic": ep,
                        "diagnostics": diags,
                    }
                )

    user_terms, glossary_conflicts = parse_glossary_files(case_plan.get("glossary_files", []), case_plan.get("inline_glossary"))
    mined = mine_terminology_and_acronyms(segments)

    glossary_resolved = {
        "user_terms": user_terms,
        "glossary_conflicts": glossary_conflicts,
        "mined_acronyms": mined["mined_acronyms"],
        "acronym_issues": mined["acronym_issues"],
        "hyphen_drift_candidates": mined["hyphen_drift_candidates"],
    }

    pre_audit = {
        "total_documents": len(docs),
        "total_paragraphs": p_counter,
        "total_sentences": len(segments),
        "sentences_with_diagnostics": sum(1 for s in segments if s["diagnostics"]),
        "sentences_with_hedges": sum(1 for s in segments if s["epistemic"]["hedge_tier"] is not None),
        "glossary_term_count": len(user_terms),
        "glossary_conflict_count": len(glossary_conflicts),
        "acronym_issue_count": len(mined["acronym_issues"]),
        "hyphen_drift_count": len(mined["hyphen_drift_candidates"]),
        "read_warnings": read_warnings,
    }

    save_json(out_dir / "source_segments.json", {"segments": segments, "docs": [{"rel_path": d["rel_path"], "format": d["format"]} for d in docs]})
    save_json(out_dir / "glossary_resolved.json", glossary_resolved)
    save_json(out_dir / "pre_audit.json", pre_audit)

    # Compact brief for agent authoring
    brief_lines = [
        f"# Polish Brief — {case_plan.get('case_id')}",
        f"- Genre: `{case_plan['signals']['genre']}` ({case_plan['signals']['genre_confidence']})",
        f"- Target Language: `{case_plan['signals']['language']}` | Intensity: `{case_plan['signals']['edit_intensity']}`",
        f"- Trigger Guard `is_non_polish_qa`: `{case_plan['signals']['is_non_polish_qa']}`",
        f"- Total Documents: {len(docs)} | Paragraphs: {p_counter} | Sentences: {len(segments)}",
        f"- User Glossary Terms: {len(user_terms)} | Glossary Conflicts: {len(glossary_conflicts)}",
        f"- Acronym Issues: {len(mined['acronym_issues'])} | Hyphen/Variant Drifts: {len(mined['hyphen_drift_candidates'])}",
        "",
    ]
    if read_warnings:
        brief_lines.append("## Read Warnings & Missing Inputs")
        for w in read_warnings:
            brief_lines.append(f"- {w}")
        brief_lines.append("")
    if glossary_conflicts:
        brief_lines.append("## Glossary Conflicts (MUST surface in author_confirmation_items)")
        for gc in glossary_conflicts:
            brief_lines.append(f"- {gc['conflict_id']} ({gc['key']}): {json.dumps(gc['candidates'], ensure_ascii=False)}")
        brief_lines.append("")
    if mined["acronym_issues"] or mined["hyphen_drift_candidates"]:
        brief_lines.append("## Detected Terminology & Acronym Issues")
        for ai in mined["acronym_issues"]:
            brief_lines.append(f"- {ai['type']}: `{ai['acronym']}` -> {json.dumps(ai, ensure_ascii=False)}")
        for hd in mined["hyphen_drift_candidates"]:
            brief_lines.append(f"- Variant drift `{hd['concept_key']}`: {json.dumps(hd['variants'], ensure_ascii=False)}")
        brief_lines.append("")
    brief_lines.append("## Sentence Preview (first 80 sentences)")
    for s in segments[:80]:
        diag_tags = ",".join(sorted({d["category"] for d in s["diagnostics"]}))
        tag_str = f" [DIAG:{diag_tags}]" if diag_tags else ""
        tier = s["epistemic"]["hedge_tier"]
        tier_str = f" [HEDGE:T{tier}]" if tier else ""
        brief_lines.append(f"- **{s['sentence_id']}** ({s['section']}){tier_str}{tag_str}: {s['original']}")

    save_text(out_dir / "polish_brief.md", "\n".join(brief_lines) + "\n")


if __name__ == "__main__":
    main()
