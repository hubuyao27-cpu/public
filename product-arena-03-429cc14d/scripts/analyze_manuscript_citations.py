#!/usr/bin/env python3
"""Step 3: Analyze manuscript zones, extract existing citation points, detect anomalies, and score uncited claims."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    first_author_surname,
    load_json_safe,
    normalize_title,
    read_document_any,
    save_json,
    save_text,
)


def split_sentences_simple(text: str) -> List[str]:
    """Split paragraph text into sentences without breaking decimals or abbreviations."""
    s = text.strip()
    if not s:
        return []
    if s.startswith("#") or s.startswith("```"):
        return [s]
    masked = re.sub(r"\d+\.\d+", lambda m: m.group(0).replace(".", "__DOT__"), s)
    for abbr in ("Fig.", "Figs.", "Eq.", "Eqs.", "Tab.", "et al.", "e.g.", "i.e.", "vs.", "Dr.", "Prof.", "No."):
        masked = masked.replace(abbr, abbr.replace(".", "__DOT__"))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff\"'(\[])|(?<=[。！？])\s*", masked)
    return [p.replace("__DOT__", ".").strip() for p in parts if p.replace("__DOT__", ".").strip()]


def expand_numeric_list(inner: str) -> List[int]:
    """Expand '1, 3-5' -> [1, 3, 4, 5]."""
    nums: List[int] = []
    for part in re.split(r"[,，]", inner):
        p = part.strip()
        m_rng = re.match(r"^(\d+)\s*[-–—]\s*(\d+)$", p)
        if m_rng:
            a, b = int(m_rng.group(1)), int(m_rng.group(2))
            if a <= b and (b - a) <= 50:
                nums.extend(range(a, b + 1))
        elif p.isdigit():
            nums.append(int(p))
    return nums


def classify_zone(line: str, current_section: str) -> str:
    """Determine structural zone of a paragraph."""
    s = line.strip()
    low_sec = current_section.lower()
    if re.match(r"^(?:fig\.?|figure|图)\s*\d+", s, flags=re.IGNORECASE):
        return "figure_caption"
    if re.match(r"^(?:tab\.?|table|表)\s*\d+", s, flags=re.IGNORECASE):
        return "table_caption"
    if s.startswith("|") and "|" in s[1:]:
        return "table_cell"
    if re.match(r"^(?:\[\^\d+\]|\^\d+|note:|注[:：])", s, flags=re.IGNORECASE):
        return "footnote"
    if "appendix" in low_sec or "附录" in current_section or "supporting" in low_sec:
        return "appendix"
    return "body"


def extract_citation_markers(sent: str) -> List[Dict[str, Any]]:
    """Extract all in-text citation markers with spans and parsed keys/numbers."""
    markers: List[Dict[str, Any]] = []
    # Placeholders
    for m in re.finditer(r"\[(?:REF|\?|CITE|待引用|引用待补)\]", sent, flags=re.IGNORECASE):
        markers.append({"kind": "placeholder", "raw": m.group(0), "start": m.start(), "end": m.end(), "nums": [], "keys": [], "author_year": None})

    # LaTeX \cite{a,b}
    for m in re.finditer(r"\\cite[tp]?(?:\[[^\]]*\])?\{([^}]+)\}", sent):
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        markers.append({"kind": "latex_cite", "raw": m.group(0), "start": m.start(), "end": m.end(), "nums": [], "keys": keys, "author_year": None})

    # Superscript numeric ^[1,2]^
    for m in re.finditer(r"\^\[?(\d+(?:\s*[-–—,]\s*\d+)*)\]?\^", sent):
        nums = expand_numeric_list(m.group(1))
        if nums:
            markers.append({"kind": "superscript_numeric", "raw": m.group(0), "start": m.start(), "end": m.end(), "nums": nums, "keys": [], "author_year": None})

    # Bracket numeric [1] or [1, 3-5] (avoid matching years like [2021] or units)
    for m in re.finditer(r"(?<!\^)\[(\d+(?:\s*[-–—,]\s*\d+)*)\](?!\^|\()", sent):
        nums = expand_numeric_list(m.group(1))
        if nums and all(1 <= x <= 999 for x in nums):
            markers.append({"kind": "bracket_numeric", "raw": m.group(0), "start": m.start(), "end": m.end(), "nums": nums, "keys": [], "author_year": None})

    # Author-year (Smith et al., 2021; Lee & Kim, 2020) or Chinese （张三等，2021）
    for m in re.finditer(r"[（(]([^()（）]{3,90}?(?:19\d{2}|20\d{2})[a-z]?[^()（）]{0,20})[）)]", sent):
        inner = m.group(1)
        # Skip plain equation/figure tags
        if re.match(r"^(?:Fig|Table|Eq|p\.|n\s*=)", inner, flags=re.IGNORECASE):
            continue
        sub_cites = [x.strip() for x in re.split(r"[;；]", inner) if re.search(r"(?:19\d{2}|20\d{2})", x)]
        if sub_cites:
            parsed_ay = []
            for sc in sub_cites:
                yr_m = re.search(r"(19\d{2}|20\d{2})([a-z])?", sc)
                yr = yr_m.group(1) if yr_m else ""
                auth = sc[: yr_m.start()].strip(" ,，&和与等et al.") if yr_m else ""
                first_tok = re.split(r"[\s,，&]+", auth)[0] if auth else ""
                parsed_ay.append({"raw_sub": sc, "surname": first_tok, "year": yr})
            markers.append({"kind": "author_year", "raw": m.group(0), "start": m.start(), "end": m.end(), "nums": [], "keys": [], "author_year": parsed_ay})

    # Deduplicate overlapping spans (keep earliest/longest)
    markers.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    filtered: List[Dict[str, Any]] = []
    last_end = -1
    for mk in markers:
        if mk["start"] >= last_end:
            filtered.append(mk)
            last_end = mk["end"]
    return filtered


def resolve_marker_to_refs(marker: Dict[str, Any], canonical_entries: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Map an extracted marker to canonical REF-IDs, returning (resolved_ids, issues)."""
    resolved: List[str] = []
    issues: List[str] = []

    if marker["kind"] == "placeholder":
        return [], ["Placeholder citation marker requires source matching."]

    if marker["kind"] in ("bracket_numeric", "superscript_numeric"):
        for num in marker["nums"]:
            matches = [e["ref_id"] for e in canonical_entries if num in e.get("all_orig_nums", [])]
            if matches:
                resolved.append(matches[0])
            else:
                issues.append(f"Unresolved numeric citation [{num}] not found in library/embedded references.")
        return resolved, issues

    if marker["kind"] == "latex_cite":
        for key in marker["keys"]:
            matches = [e["ref_id"] for e in canonical_entries if key in e.get("all_cite_keys", [])]
            if matches:
                resolved.append(matches[0])
            else:
                issues.append(f"Unresolved LaTeX cite key '{key}'.")
        return resolved, issues

    if marker["kind"] == "author_year" and marker["author_year"]:
        for ay in marker["author_year"]:
            surn = ay["surname"].lower()
            yr = ay["year"]
            exact = [
                e["ref_id"]
                for e in canonical_entries
                if e.get("year") == yr and surn and (surn in first_author_surname(e.get("authors", [])) or first_author_surname(e.get("authors", [])) in surn)
            ]
            if len(exact) == 1:
                resolved.append(exact[0])
            elif len(exact) > 1:
                resolved.append(exact[0])
                issues.append(f"Ambiguous author-year citation ({ay['raw_sub']}) matches {len(exact)} entries: {exact}.")
            else:
                # Check if author exists with different year (metadata mismatch!)
                same_auth = [
                    e for e in canonical_entries
                    if surn and (surn in first_author_surname(e.get("authors", [])) or first_author_surname(e.get("authors", [])) in surn)
                ]
                if same_auth:
                    issues.append(f"Year mismatch for '{ay['surname']}': cited {yr}, library has {[ (e['ref_id'], e['year']) for e in same_auth ]}.")
                else:
                    issues.append(f"Unresolved author-year citation: {ay['raw_sub']}.")
        return resolved, issues

    return resolved, issues


def score_claim_candidates(sentence: str, canonical_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank canonical library entries against an uncited sentence using lexical + entity overlap."""
    s_tokens = {w for w in normalize_title(sentence).split() if len(w) >= 3}
    cjk_bigrams = {sentence[i : i + 2] for i in range(max(0, len(sentence) - 1)) if re.match(r"[\u4e00-\u9fff]{2}", sentence[i : i + 2])}
    scored: List[Tuple[float, Dict[str, Any], List[str]]] = []
    for e in canonical_entries:
        blob = f"{e.get('title','')} {e.get('abstract','')} {e.get('keywords','')} {e.get('venue','')}"
        e_tokens = {w for w in normalize_title(blob).split() if len(w) >= 3}
        overlap = sorted(s_tokens & e_tokens)
        score = float(len(overlap))
        for bg in cjk_bigrams:
            if bg in blob:
                score += 1.5
                overlap.append(bg)
        fa = first_author_surname(e.get("authors", []))
        if fa and fa in sentence.lower():
            score += 4.0
            overlap.append(f"author:{fa}")
        if e.get("year") and e["year"] in sentence:
            score += 2.0
            overlap.append(f"year:{e['year']}")
        if score > 0:
            scored.append((score, e, overlap[:8]))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "ref_id": e["ref_id"],
            "score": round(sc, 2),
            "title": e["title"],
            "authors": e.get("authors", [])[:3],
            "year": e.get("year", ""),
            "has_abstract": bool(e.get("abstract")),
            "matched_cues": cues,
        }
        for sc, e, cues in scored[:5]
    ]


def needs_citation_heuristic(sent: str, is_heading: bool) -> bool:
    """Flag uncited sentences likely making empirical, comparative, or literature claims."""
    if is_heading or len(sent) < 25:
        return False
    low = sent.lower()
    cues = (
        "previous studies", "prior work", "literature", "reported", "demonstrated", "shown that",
        "widely used", "commonly", "benchmark", "state-of-the-art", "outperform", "compared with",
        "研究表明", "已有研究", "文献报道", "前人", "公认", "广泛采用", "相比", "提出", "发现",
    )
    has_quant = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|ms|s|K|°C|eV|mV|nm|wt%)", sent))
    # Heuristic: literature cue OR quantitative benchmark claim not clearly our own ('our'/'本文'/'本研究')
    is_own = any(w in low for w in ("our ", "we ", "this work", "this study", "本研究", "本文"))
    return any(c in low for c in cues) or (has_quant and not is_own)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze manuscript citations and detect anomalies.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--out-dir", default="/app/work")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_plan, _ = load_json_safe(Path(args.case_plan), default={})
    lib_doc, _ = load_json_safe(Path(args.library), default={})
    canonical_entries: List[Dict[str, Any]] = lib_doc.get("entries", [])

    manuscript_files = case_plan.get("manuscript_files", [])
    raw_text = ""
    primary_rel = ""
    doc_format = "markdown"
    warnings: List[str] = []

    if manuscript_files:
        mf = manuscript_files[0]
        primary_rel = mf["rel_path"]
        raw_text, doc_format, w = read_document_any(Path(mf["path"]))
        if w:
            warnings.append(w)
    elif case_plan.get("inline_manuscript"):
        raw_text = str(case_plan["inline_manuscript"])
        primary_rel = "case.json:inline_manuscript"

    lines = raw_text.splitlines() if raw_text else []
    ref_section_start: Optional[int] = None
    ref_section_end: Optional[int] = None
    ref_heading = ""
    for i, ln in enumerate(lines):
        if re.match(r"^(?:#{1,4}\s*)?(?:references|bibliography|参考文献)\s*$", ln.strip(), flags=re.IGNORECASE):
            ref_section_start = i
            ref_heading = ln.strip()
            ref_section_end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^#{1,3}\s+", lines[j].strip()) or re.match(r"^(?:appendix|附录)\b", lines[j].strip(), flags=re.IGNORECASE):
                    ref_section_end = j
                    break
            break

    # Parse paragraphs excluding the old reference_list block (which will be regenerated cleanly)
    paragraphs: List[Dict[str, Any]] = []
    sentences: List[Dict[str, Any]] = []
    citation_points: List[Dict[str, Any]] = []
    uncited_candidates: List[Dict[str, Any]] = []

    current_section = "Document"
    buf: List[str] = []
    buf_start = 1
    p_idx = 0
    s_idx = 0
    cp_idx = 0

    def _flush_para(end_ln: int, is_heading: bool = False) -> None:
        nonlocal buf, p_idx, s_idx, cp_idx
        if not buf:
            return
        txt = "\n".join(buf).strip()
        buf = []
        if not txt:
            return
        p_idx += 1
        pid = f"P{p_idx:03d}"
        zone = "heading" if is_heading else classify_zone(txt, current_section)
        paragraphs.append({"paragraph_id": pid, "section": current_section, "zone": zone, "is_heading": is_heading, "line_range": [buf_start, end_ln], "text": txt})
        sents = [txt] if is_heading else split_sentences_simple(txt)
        for st in sents:
            s_idx += 1
            sid = f"S{s_idx:03d}"
            markers = [] if is_heading else extract_citation_markers(st)
            cp_ids_here: List[str] = []
            for mk in markers:
                cp_idx += 1
                cpid = f"CP{cp_idx:03d}"
                res_ids, iss = resolve_marker_to_refs(mk, canonical_entries)
                citation_points.append(
                    {
                        "citation_point_id": cpid,
                        "sentence_id": sid,
                        "paragraph_id": pid,
                        "section": current_section,
                        "zone": zone,
                        "marker": mk,
                        "resolved_ref_ids": res_ids,
                        "issues": iss,
                    }
                )
                cp_ids_here.append(cpid)
            sentences.append(
                {
                    "sentence_id": sid,
                    "paragraph_id": pid,
                    "section": current_section,
                    "zone": zone,
                    "is_heading": is_heading,
                    "text": st,
                    "citation_point_ids": cp_ids_here,
                }
            )
            if not markers and needs_citation_heuristic(st, is_heading) and canonical_entries:
                cands = score_claim_candidates(st, canonical_entries)
                if cands:
                    uncited_candidates.append({"sentence_id": sid, "section": current_section, "text": st, "candidates": cands})

    for idx, ln in enumerate(lines):
        if ref_section_start is not None and ref_section_start <= idx < (ref_section_end or len(lines)):
            _flush_para(idx)
            continue
        s = ln.strip()
        md_h = re.match(r"^(#{1,6})\s+(.+)$", s)
        tex_h = re.match(r"^\\(?:part|chapter|section|subsection|subsubsection)\*?\{(.+?)\}", s)
        if md_h or tex_h:
            _flush_para(idx)
            current_section = (md_h.group(2) if md_h else tex_h.group(1)).strip()  # type: ignore[union-attr]
            buf = [s]
            buf_start = idx + 1
            _flush_para(idx + 1, is_heading=True)
            continue
        if s == "":
            _flush_para(idx)
            continue
        if not buf:
            buf_start = idx + 1
        buf.append(ln)
    _flush_para(len(lines))

    # Detect existing numbering order & orphan uncited references
    seen_nums: List[int] = []
    for cp in citation_points:
        for num in cp["marker"].get("nums", []):
            if num not in seen_nums:
                seen_nums.append(num)
    order_anomalies: List[str] = []
    if seen_nums and seen_nums != list(range(1, len(seen_nums) + 1)):
        order_anomalies.append(f"Existing numeric appearance order {seen_nums} is not continuous 1..{len(seen_nums)}.")

    cited_ref_ids = {rid for cp in citation_points for rid in cp["resolved_ref_ids"]}
    uncited_library_refs = [e["ref_id"] for e in canonical_entries if e["ref_id"] not in cited_ref_ids]

    analysis = {
        "primary_manuscript": primary_rel,
        "doc_format": doc_format,
        "has_embedded_ref_section": ref_section_start is not None,
        "ref_heading": ref_heading or ("## 参考文献" if case_plan["signals"]["language"] == "zh" else "## References"),
        "paragraphs": paragraphs,
        "sentences": sentences,
        "citation_points": citation_points,
        "uncited_claim_candidates": uncited_candidates,
        "order_anomalies": order_anomalies,
        "uncited_library_refs": uncited_library_refs,
        "warnings": warnings,
    }
    save_json(out_dir / "manuscript_analysis.json", analysis)

    # Compact brief
    lines_b = [
        f"# Citation Analysis Brief — {case_plan.get('case_id')}",
        f"- Target Style: `{case_plan['signals']['target_style']}` (`{case_plan['signals']['citation_system']}`)",
        f"- Requested Actions: `{case_plan['signals']['requested_actions']}`",
        f"- Manuscript: `{primary_rel}` | Sentences (excl. old ref list): {len(sentences)} | Existing Citation Points: {len(citation_points)}",
        f"- Canonical Library Entries: {len(canonical_entries)} | Uncited Entries: {len(uncited_library_refs)}",
        "",
    ]
    if order_anomalies:
        lines_b.append("## Existing Numbering Anomalies")
        for oa in order_anomalies:
            lines_b.append(f"- {oa}")
        lines_b.append("")
    if citation_points:
        lines_b.append("## Existing Citation Points")
        for cp in citation_points:
            iss = f" | ISSUES: {cp['issues']}" if cp["issues"] else ""
            lines_b.append(f"- **{cp['citation_point_id']}** ({cp['sentence_id']}, `{cp['zone']}`): `{cp['marker']['raw']}` -> {cp['resolved_ref_ids']}{iss}")
        lines_b.append("")
    if uncited_candidates:
        lines_b.append("## Uncited Claims With Candidate Library Matches")
        for uc in uncited_candidates[:30]:
            lines_b.append(f"- **{uc['sentence_id']}** ({uc['section']}): {uc['text']}")
            for c in uc["candidates"][:3]:
                lines_b.append(f"  - `{c['ref_id']}` (score={c['score']}, has_abs={c['has_abstract']}): {c['title']} ({c['year']})")
        lines_b.append("")
    lines_b.append("## Canonical Library Summary")
    for e in canonical_entries:
        lines_b.append(f"- **{e['ref_id']}** [{e['entry_type']}] {e.get('authors', ['?'])[:1]} ({e.get('year','?')}) — {e.get('title','')} | DOI: {e.get('doi') or 'MISSING'} | Missing: {e.get('missing_fields')}")

    save_text(out_dir / "citation_brief.md", "\n".join(lines_b) + "\n")


if __name__ == "__main__":
    main()
