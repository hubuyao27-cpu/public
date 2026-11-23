#!/usr/bin/env python3
"""Step 2: Deterministically parse & index all literature, bibliographies, drafts, and own-paper evidence.

Produces:
- /app/work/source_ledger.json (machine-verifiable citations, conflicts, own evidence, pre-gaps, grounded numbers)
- /app/work/evidence_digest.md  (compact token-efficient digest for drafting & revision)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    extract_docx_text,
    extract_first_author_surname,
    extract_numeric_tokens,
    extract_pdf_text,
    load_json_safe,
    normalize_ws,
    parse_bibtex_text,
    parse_markdown_or_text_refs,
    parse_ris_text,
    read_text_safe,
    save_json,
)

CLICHE_PATTERNS = [
    r"近年来[^。\n]{0,25}(?:受到|引起)[^。\n]{0,15}广泛关注",
    r"随着[^。\n]{0,30}(?:快速发展|迅猛发展|不断进步|日新月异)",
    r"具有重要(?:理论和实际|科学|现实|研究)?意义",
    r"in recent years,?.{0,40}attracted\s+(?:widespread|increasing|great)\s+attention",
    r"with the rapid\s+(?:development|advancement|growth)\s+of",
    r"has become a hot\s+(?:topic|research\s+area)",
]


def _read_document_lines(item: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Read a source file into [(locator, text_line)] plus warnings."""
    path = Path(item["path"])
    rel = item["rel_path"]
    ext = item["ext"]
    warnings: List[str] = []
    lines_out: List[Tuple[str, str]] = []

    if ext == ".pdf":
        pages, pwarns = extract_pdf_text(path)
        warnings.extend(pwarns)
        for p in pages:
            pno = p["page"]
            for idx, ln in enumerate(str(p.get("text") or "").splitlines(), start=1):
                if ln.strip():
                    lines_out.append((f"{rel}:p{pno}_L{idx}", ln.strip()))
        return lines_out, warnings

    if ext == ".docx":
        dlines, derr = extract_docx_text(path)
        if derr:
            warnings.append(f"{rel}:{derr}")
        for idx, ln in enumerate(dlines, start=1):
            lines_out.append((f"{rel}:L{idx}", ln))
        return lines_out, warnings

    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pt", ".pth", ".ckpt", ".bin", ".npy", ".npz", ".zip"):
        return [], warnings

    txt, err = read_text_safe(path)
    if err:
        warnings.append(f"{rel}:{err}")
        return [], warnings
    for idx, ln in enumerate((txt or "").splitlines(), start=1):
        if ln.strip():
            lines_out.append((f"{rel}:L{idx}", ln.rstrip()))
    return lines_out, warnings


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())


def _extract_refs_from_csv(text: str, rel: str) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    delim = "\t" if rel.lower().endswith(".tsv") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        return refs
    cols = {c.lower().strip(): c for c in reader.fieldnames}
    has_bib_cols = any(k in cols for k in ("title", "authors", "author", "doi", "cite_key", "key"))
    if not has_bib_cols:
        return refs
    for ridx, row in enumerate(reader, start=2):
        getv = lambda *names: next((str(row[cols[n]]).strip() for n in names if n in cols and row.get(cols[n])), "")
        title = getv("title", "paper_title")
        authors = getv("authors", "author")
        year = getv("year", "pub_year")
        venue = getv("venue", "journal", "booktitle", "conference")
        doi = getv("doi", "url", "arxiv", "id")
        ck = getv("cite_key", "key", "ref_id") or f"{extract_first_author_surname(authors) or 'Ref'}{year}_{ridx}"
        if title or authors or doi:
            refs.append(
                {
                    "cite_key": ck,
                    "authors": authors,
                    "year": year,
                    "title": title,
                    "venue": venue,
                    "doi_or_id": doi,
                    "abstract": getv("abstract", "summary", "findings", "limitation", "limitations", "notes"),
                    "source_locator": f"{rel}:L{ridx}",
                }
            )
    return refs


def _extract_refs_from_json(data: Any, prefix: str) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            authors = ""
            if isinstance(item.get("authors"), list):
                authors = "; ".join(str(a) for a in item["authors"])
            elif isinstance(item.get("author"), list):
                authors = "; ".join(
                    f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                    if isinstance(a, dict)
                    else str(a)
                    for a in item["author"]
                )
            else:
                authors = str(item.get("authors") or item.get("author") or "")
            year = str(item.get("year") or "")
            if not year and isinstance(item.get("issued"), dict):
                dp = item["issued"].get("date-parts")
                if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                    year = str(dp[0][0])
            venue = str(item.get("venue") or item.get("journal") or item.get("container-title") or item.get("booktitle") or "")
            doi = str(item.get("doi") or item.get("DOI") or item.get("url") or item.get("arxiv") or "")
            ck = str(item.get("cite_key") or item.get("key") or item.get("id") or f"{extract_first_author_surname(authors) or 'Ref'}{year}_{idx+1}")
            abs_parts = [str(item.get(k)) for k in ("abstract", "summary", "conclusion", "findings", "limitation", "limitations", "applicability", "notes") if item.get(k)]
            if title or authors or doi or abs_parts:
                refs.append(
                    {
                        "cite_key": ck,
                        "authors": authors,
                        "year": year,
                        "title": title,
                        "venue": venue,
                        "doi_or_id": doi,
                        "abstract": " | ".join(abs_parts),
                        "source_locator": f"{prefix}[{idx}]",
                    }
                )
    elif isinstance(data, dict):
        for k in ("references", "papers", "literature", "bibliography", "items", "entries"):
            if k in data and isinstance(data[k], list):
                refs.extend(_extract_refs_from_json(data[k], f"{prefix}#{k}"))
    return refs


def _extract_prose_card_metadata(lines: List[Tuple[str, str]], rel: str) -> Optional[Dict[str, Any]]:
    """Extract structured reference metadata if a markdown/txt/PDF file describes a single paper."""
    if not lines:
        return None
    head_text = "\n".join(ln for _, ln in lines[:40])
    title_m = re.search(r"(?:^|\n)\s*(?:#+\s*|Title\s*[:：]\s*|题目\s*[:：]\s*)(.+)", head_text, re.I)
    author_m = re.search(r"(?:^|\n)\s*(?:Authors?|作者)\s*[:：]\s*(.+)", head_text, re.I)
    year_m = re.search(r"(?:^|\n)\s*(?:Year|年份)\s*[:：]\s*((?:19|20)\d{2})", head_text, re.I)
    venue_m = re.search(r"(?:^|\n)\s*(?:Venue|Journal|Conference|期刊|会议|发表于)\s*[:：]\s*(.+)", head_text, re.I)
    doi_m = re.search(r"(10\.\d{4,9}/[^\s,;]+|arXiv:\d{4}\.\d{4,5})", head_text, re.I)
    ck_m = re.search(r"(?:^|\n)\s*(?:CiteKey|Key|引用键)\s*[:：]\s*(\S+)", head_text, re.I)

    if not (author_m or title_m or doi_m):
        return None
    title = title_m.group(1).strip() if title_m else ""
    authors = author_m.group(1).strip() if author_m else ""
    year = year_m.group(1) if year_m else ""
    if not year:
        ym2 = re.search(r"\b((?:19|20)\d{2})\b", head_text[:500])
        year = ym2.group(1) if ym2 else ""
    venue = venue_m.group(1).strip() if venue_m else ""
    doi = doi_m.group(1).rstrip(".") if doi_m else ""
    ck = ck_m.group(1).strip() if ck_m else f"{extract_first_author_surname(authors) or Path(rel).stem}{year}"
    snippet_lines = [f"[{loc}] {ln}" for loc, ln in lines[:12]]
    return {
        "cite_key": ck,
        "authors": authors,
        "year": year,
        "title": title,
        "venue": venue,
        "doi_or_id": doi,
        "abstract": " ".join(ln for _, ln in lines[:18])[:900],
        "source_locator": lines[0][0],
        "snippets": snippet_lines,
    }


def _extract_draft_citations(text: str) -> List[str]:
    markers: List[str] = []
    # [1], [1, 2], [1-3], [R01], [Smith2023]
    for m in re.finditer(r"\[([A-Za-z0-9_,\s;-]+)\]", text):
        inner = m.group(1).strip()
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT")):
            continue
        if re.match(r"^\d+\s*[-–]\s*\d+$", inner):
            markers.append(f"[{inner}]")
            continue
        for part in re.split(r"[,;]", inner):
            p = part.strip()
            if p and (re.match(r"^R?\d{1,3}$", p) or re.match(r"^[A-Za-z][A-Za-z0-9_:-]{2,30}$", p)):
                markers.append(p)
    # \cite{k1, k2}
    for m in re.finditer(r"\\cite[a-z]*\{([^}]+)\}", text):
        for k in m.group(1).split(","):
            if k.strip():
                markers.append(k.strip())
    # (Author, 2023)
    for m in re.finditer(r"\(([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?),?\s*((?:19|20)\d{2}[a-z]?)\)", text):
        markers.append(f"{m.group(1).split()[0]}{m.group(2)}")
    return markers


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest heterogeneous writing materials into source_ledger.json and evidence_digest.md")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--out-ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--out-digest", default="/app/work/evidence_digest.md")
    ap.add_argument("--out-skeleton", default=None, help="Optional output path for pre-populated section_payload_skeleton.json")
    args = ap.parse_args()

    plan, err = load_json_safe(Path(args.case_plan))
    if err or not isinstance(plan, dict):
        raise SystemExit(f"Failed to load case_plan: {err}")

    input_dir = Path(plan["input_dir"])
    case_path = Path(plan["case_json_path"]) if plan.get("case_json_path") else None
    case_data: Dict[str, Any] = {}
    if case_path and case_path.exists():
        cd, _ = load_json_safe(case_path)
        if isinstance(cd, dict):
            case_data = cd

    warnings: List[str] = list(plan.get("access_warnings") or [])
    raw_ref_records: List[Dict[str, Any]] = []
    own_paper_snippets: List[Dict[str, str]] = []
    literature_snippets: List[Dict[str, Any]] = []
    draft_texts: List[Tuple[str, str]] = []
    grounded_numbers: List[Dict[str, Any]] = []
    figure_table_mentions: List[Dict[str, str]] = []

    # 1. Process inline materials in case.json (and fallback inline blocks in task.md)
    if case_data:
        inline_refs = _extract_refs_from_json(case_data, "case.json")
        raw_ref_records.extend(inline_refs)
        for k in ("draft", "manuscript", "intro_draft", "existing_draft", "related_work"):
            if isinstance(case_data.get(k), str) and case_data[k].strip():
                draft_texts.append((f"case.json#{k}", case_data[k]))
        for k in (
            "methods", "experiments", "results", "evaluation", "ablation", "metrics",
            "own_results", "figures", "tables", "contributions", "own_paper",
            "notes", "background", "context", "abstract", "manuscript",
        ):
            val = case_data.get(k)
            if not val:
                continue
            txt = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            own_paper_snippets.append({"locator": f"case.json#{k}", "text": txt[:1500]})
            for tok in extract_numeric_tokens(txt, filter_structural=False):
                grounded_numbers.append({"locator": f"case.json#{k}", **tok})
            for fm in re.finditer(r"(?:Fig(?:ure)?|Table|Tab\.|图|表)\s*\.?\s*[A-Za-z0-9.-]+", txt, re.I):
                figure_table_mentions.append({"marker": fm.group(0), "locator": f"case.json#{k}"})

    # Fallback: parse inline BibTeX / RIS / numbered references or own metrics inside task.md
    task_md_path = Path(plan["task_md_path"]) if plan.get("task_md_path") else None
    if task_md_path and task_md_path.exists():
        t_txt, t_err = read_text_safe(task_md_path)
        if t_txt:
            for tok in extract_numeric_tokens(t_txt, filter_structural=False):
                grounded_numbers.append({"locator": "task.md", **tok})
            if not raw_ref_records:
                if "@" in t_txt and re.search(r"@\w+\s*\{", t_txt):
                    raw_ref_records.extend(parse_bibtex_text(t_txt, "task.md"))
                elif "TY  -" in t_txt and "ER  -" in t_txt:
                    raw_ref_records.extend(parse_ris_text(t_txt, "task.md"))
                else:
                    from common import parse_markdown_or_text_refs
                    task_ref_recs = parse_markdown_or_text_refs(t_txt, "task.md")
                    if task_ref_recs:
                        raw_ref_records.extend(task_ref_recs)

    # 2. Process all files in inventory (bibliography files FIRST so notes enrich/align rather than create stub refs)
    bucket_order = {"bibliography": 0, "literature_source": 1, "own_paper_evidence": 2, "own_paper_draft": 3, "other_text": 4, "binary_or_figure": 5}
    ordered_inv = sorted(plan.get("inventory") or [], key=lambda it: bucket_order.get(it.get("bucket", "other_text"), 9))
    for item in ordered_inv:
        bucket = item.get("bucket")
        rel = item.get("rel_path", "")
        if bucket == "control":
            continue
        p = Path(item["path"])
        ext = item.get("ext", "")

        if bucket == "binary_or_figure":
            figure_table_mentions.append({"marker": Path(rel).name, "locator": rel})
            continue

        # Raw text / structured files
        if ext == ".bib":
            txt, rerr = read_text_safe(p)
            if rerr:
                warnings.append(f"{rel}:{rerr}")
            elif txt:
                raw_ref_records.extend(parse_bibtex_text(txt, rel))
                for ln_no, ln in enumerate(txt.splitlines(), start=1):
                    for tok in extract_numeric_tokens(ln, filter_structural=False):
                        grounded_numbers.append({"locator": f"{rel}:L{ln_no}", **tok})
            continue

        if ext == ".ris":
            txt, rerr = read_text_safe(p)
            if rerr:
                warnings.append(f"{rel}:{rerr}")
            elif txt:
                raw_ref_records.extend(parse_ris_text(txt, rel))
            continue

        if ext == ".json":
            jdata, jerr = load_json_safe(p)
            if jerr:
                warnings.append(f"{rel}:{jerr}")
            elif jdata is not None:
                jrefs = _extract_refs_from_json(jdata, rel)
                if jrefs:
                    raw_ref_records.extend(jrefs)
                jtxt = json.dumps(jdata, ensure_ascii=False)
                for tok in extract_numeric_tokens(jtxt, filter_structural=False):
                    grounded_numbers.append({"locator": rel, **tok})
                if bucket == "own_paper_evidence":
                    own_paper_snippets.append({"locator": rel, "text": jtxt[:1500]})
            continue

        if ext in (".csv", ".tsv"):
            txt, rerr = read_text_safe(p)
            if rerr:
                warnings.append(f"{rel}:{rerr}")
            elif txt:
                crefs = _extract_refs_from_csv(txt, rel)
                if crefs:
                    raw_ref_records.extend(crefs)
                else:
                    own_paper_snippets.append({"locator": f"{rel}:L1", "text": txt[:1500]})
                for ln_no, ln in enumerate(txt.splitlines(), start=1):
                    for tok in extract_numeric_tokens(ln, filter_structural=False):
                        grounded_numbers.append({"locator": f"{rel}:L{ln_no}", **tok})
            continue

        # Text/MD/TeX/DOCX/PDF
        lines, lwarns = _read_document_lines(item)
        warnings.extend(lwarns)
        if not lines:
            continue
        full_txt = "\n".join(ln for _, ln in lines)

        for loc, ln in lines:
            for tok in extract_numeric_tokens(ln, filter_structural=False):
                grounded_numbers.append({"locator": loc, **tok})
            for fm in re.finditer(r"(?:Fig(?:ure)?|Table|Tab\.|图|表)\s*\.?\s*[A-Za-z0-9.-]+", ln, re.I):
                figure_table_mentions.append({"marker": fm.group(0), "locator": loc})

        if bucket == "own_paper_draft":
            draft_texts.append((rel, full_txt))
        elif bucket == "own_paper_evidence":
            own_paper_snippets.append({"locator": lines[0][0], "text": "\n".join(f"[{loc}] {ln}" for loc, ln in lines[:45])})
        else:
            # Literature or bibliography text file: try extracting explicit ref list + single-paper metadata
            mrefs = parse_markdown_or_text_refs(full_txt, rel)
            if mrefs:
                raw_ref_records.extend(mrefs)
            # Only treat the whole file as a single paper card when it isn't a multi-ref notes list
            if len(mrefs) <= 1 and not re.search(r"^(?:#\s*文献笔记|-\s*\[R\d+\])", full_txt, re.M):
                card = _extract_prose_card_metadata(lines, rel)
                if card:
                    raw_ref_records.append(card)
            literature_snippets.append(
                {
                    "file": rel,
                    "first_locator": lines[0][0],
                    "line_count": len(lines),
                    "preview_lines": [f"[{loc}] {ln}" for loc, ln in lines[:30]],
                }
            )

    # 3. Deduplicate references & detect cross-source metadata conflicts
    conflicts: List[Dict[str, Any]] = []
    catalog: List[Dict[str, Any]] = []
    by_key: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    by_doi: Dict[str, Dict[str, Any]] = {}
    conf_idx = 0

    for rec in raw_ref_records:
        ck = (rec.get("cite_key") or "").strip()
        ntitle = _norm_title(rec.get("title") or "")
        ndoi = (rec.get("doi_or_id") or "").strip().lower()
        match = None
        if ck and ck in by_key:
            match = by_key[ck]
        elif ndoi and ndoi in by_doi:
            match = by_doi[ndoi]
        elif ntitle and len(ntitle) >= 12 and ntitle in by_title:
            match = by_title[ntitle]

        if match is None:
            item_entry = {
                "ref_id": f"R{len(catalog)+1:02d}",
                "cite_key": ck or f"R{len(catalog)+1:02d}",
                "authors": rec.get("authors", ""),
                "first_author_surname": extract_first_author_surname(rec.get("authors", "")),
                "year": str(rec.get("year") or ""),
                "title": rec.get("title", ""),
                "venue": rec.get("venue", ""),
                "doi_or_id": rec.get("doi_or_id", ""),
                "abstract_or_notes": rec.get("abstract", ""),
                "source_locators": [rec.get("source_locator", "")],
            }
            catalog.append(item_entry)
            if item_entry["cite_key"]:
                by_key[item_entry["cite_key"]] = item_entry
            if ndoi:
                by_doi[ndoi] = item_entry
            if ntitle and len(ntitle) >= 12:
                by_title[ntitle] = item_entry
        else:
            loc = rec.get("source_locator", "")
            if loc and loc not in match["source_locators"]:
                match["source_locators"].append(loc)
            # Check conflicts on year / venue / doi
            for field in ("year", "doi_or_id"):
                v_old = str(match.get(field) or "").strip()
                v_new = str(rec.get(field) or "").strip()
                if v_old and v_new and v_old.lower() != v_new.lower():
                    conf_idx += 1
                    conflicts.append(
                        {
                            "conflict_id": f"CONF-{conf_idx:02d}",
                            "item": f"{match['ref_id']} ({match['cite_key']}) {field}",
                            "sources_compared": [
                                {"source_locator": match["source_locators"][0], "source_tier": "primary_record", "value": v_old},
                                {"source_locator": loc, "source_tier": "secondary_record", "value": v_new},
                            ],
                            "adopted_value_or_status": "requires_author_confirmation",
                            "rationale": f"Conflicting {field} across bibliography/notes sources ({v_old} vs {v_new}).",
                            "needs_author_confirmation": True,
                        }
                    )
            # Enrich missing fields
            for field in ("authors", "year", "title", "venue", "doi_or_id", "abstract_or_notes"):
                src_k = "abstract" if field == "abstract_or_notes" else field
                if not match.get(field) and rec.get(src_k):
                    match[field] = rec[src_k]
            if not match["first_author_surname"] and match["authors"]:
                match["first_author_surname"] = extract_first_author_surname(match["authors"])

    # Assign recency roles (foundational / representative / recent)
    years_int = sorted({int(r["year"]) for r in catalog if re.match(r"^(19|20)\d{2}$", str(r.get("year") or ""))})
    max_y = max(years_int) if years_int else 2026
    min_y = min(years_int) if years_int else 2020
    for r in catalog:
        missing = [f for f in ("authors", "year", "title", "venue") if not r.get(f)]
        r["missing_metadata_fields"] = missing
        if re.match(r"^(19|20)\d{2}$", str(r.get("year") or "")):
            yi = int(r["year"])
            if yi >= max_y - 2:
                r["recency_role"] = "recent"
            elif yi <= min_y + 2 and len(years_int) >= 3:
                r["recency_role"] = "foundational"
            else:
                r["recency_role"] = "representative"
        else:
            r["recency_role"] = "representative"

    # 4. Audit existing draft if provided
    draft_audit: Dict[str, Any] = {
        "has_draft": bool(draft_texts),
        "cliches_detected": [],
        "unresolved_citations": [],
        "citation_dumps": [],
    }
    known_keys = {r["cite_key"].lower() for r in catalog} | {r["ref_id"].lower() for r in catalog}
    for r in catalog:
        if r["first_author_surname"] and r["year"]:
            known_keys.add(f"{r['first_author_surname']}{r['year']}".lower())

    for dloc, dtxt in draft_texts:
        for pat in CLICHE_PATTERNS:
            for cm in re.finditer(pat, dtxt, re.I):
                draft_audit["cliches_detected"].append({"locator": dloc, "phrase": cm.group(0)})
        for dm in re.finditer(r"\[(\d+)\s*[-–]\s*(\d+)\]", dtxt):
            if int(dm.group(2)) - int(dm.group(1)) >= 2:
                draft_audit["citation_dumps"].append({"locator": dloc, "marker": dm.group(0)})
        for mk in _extract_draft_citations(dtxt):
            if mk.startswith("[") and "-" in mk:
                continue
            if mk.isdigit():
                idx_num = int(mk)
                if idx_num < 1 or idx_num > len(catalog):
                    draft_audit["unresolved_citations"].append({"locator": dloc, "marker": mk})
            elif mk.lower() not in known_keys:
                draft_audit["unresolved_citations"].append({"locator": dloc, "marker": mk})

    # 5. Pre-audit structural gaps
    pre_gaps: List[Dict[str, Any]] = []
    g_idx = 0
    if not catalog:
        g_idx += 1
        pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "empty_literature_library", "detail": "No verifiable references found in /input/."})
    if not own_paper_snippets and not figure_table_mentions:
        g_idx += 1
        pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "missing_own_paper_evidence", "detail": "No own-paper methods, figures, tables, or experimental metrics found in /input/."})
    for r in catalog:
        if r["missing_metadata_fields"]:
            g_idx += 1
            pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "incomplete_reference_metadata", "detail": f"{r['ref_id']} ({r['cite_key']}) missing: {', '.join(r['missing_metadata_fields'])}"})
    for uc in draft_audit["unresolved_citations"]:
        g_idx += 1
        pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "unresolved_draft_citation", "detail": f"Draft marker '{uc['marker']}' at {uc['locator']} has no source entry."})
    if plan["signals"].get("citation_style") == "unspecified":
        g_idx += 1
        pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "unspecified_citation_style", "detail": "Target journal/citation format not specified in task or case.json."})

    ledger = {
        "case_id": plan["case_id"],
        "references_catalog": catalog,
        "own_paper_evidence": own_paper_snippets,
        "figure_table_mentions": figure_table_mentions,
        "literature_snippets": literature_snippets,
        "draft_audit": draft_audit,
        "conflicts": conflicts,
        "pre_gaps": pre_gaps,
        "grounded_numbers_index": grounded_numbers,
        "access_warnings": warnings,
    }
    save_json(Path(args.out_ledger), ledger)

    # 6. Write compact evidence_digest.md
    dlines: List[str] = [
        f"# Evidence Digest (`{plan['case_id']}`)",
        "",
        "## 1. Task & Output Contract",
        f"- **Task Mode**: `{plan['signals']['task_mode']}` | **Language**: `{plan['signals']['language']}` | **Citation Style**: `{plan['signals']['citation_style']}`",
        f"- **Requested Formats**: `{plan['signals']['requested_formats']}` | **Venue**: `{plan['signals']['audience_or_venue'] or 'Unspecified'}`",
        f"- **Required `output_contract` Keys**: `{plan['output_contract'].get('required_keys', [])}`",
        "",
        f"## 2. Verified Reference Catalog ({len(catalog)} entries)",
    ]
    if catalog:
        for r in catalog:
            miss_s = f" | MISSING: {r['missing_metadata_fields']}" if r["missing_metadata_fields"] else ""
            dlines.append(
                f"- **{r['ref_id']} (`{r['cite_key']}`)** [{r['recency_role']}]: {r['authors'] or '?'} ({r['year'] or '?'}). "
                f"*{r['title'] or '?'}*. {r['venue'] or '?'}. DOI/ID: `{r['doi_or_id'] or 'N/A'}` | Locators: `{r['source_locators']}`{miss_s}"
            )
            if r.get("abstract_or_notes"):
                dlines.append(f"  - *Notes*: {r['abstract_or_notes'][:320]}")
    else:
        dlines.append("- *NONE FOUND — do NOT fabricate references.*")

    dlines.extend(["", f"## 3. Own-Paper Evidence ({len(own_paper_snippets)} snippets, {len(figure_table_mentions)} fig/table markers)"])
    for sn in own_paper_snippets[:12]:
        dlines.append(f"### `{sn['locator']}`")
        dlines.append(sn["text"][:900])
        dlines.append("")
    if not own_paper_snippets:
        dlines.append("- *No own-paper experimental/method files discovered.*")

    if draft_texts:
        dlines.extend(["", "## 4. Existing Draft Audit"])
        dlines.append(f"- Clichés detected: `{draft_audit['cliches_detected']}`")
        dlines.append(f"- Citation dumps detected: `{draft_audit['citation_dumps']}`")
        dlines.append(f"- Unresolved citations: `{draft_audit['unresolved_citations']}`")

    dlines.extend(["", f"## 5. Conflicts ({len(conflicts)}) & Pre-Audit Gaps ({len(pre_gaps)})"])
    for c in conflicts:
        dlines.append(f"- **{c['conflict_id']}**: {c['item']} -> {c['rationale']}")
    for g in pre_gaps:
        dlines.append(f"- **{g['gap_id']}** (`{g['issue']}`): {g['detail']}")
    if warnings:
        dlines.extend(["", "## 6. File Access Warnings"])
        for w in warnings:
            dlines.append(f"- `{w}`")

    out_dig = Path(args.out_digest)
    out_dig.parent.mkdir(parents=True, exist_ok=True)
    out_dig.write_text("\n".join(dlines) + "\n", encoding="utf-8", newline="\n")

    # 7. Auto-emit ready-to-fill section_payload_skeleton.json to cut LLM boilerplate turns
    out_skel = Path(args.out_skeleton) if getattr(args, "out_skeleton", None) else (out_dig.parent / "section_payload_skeleton.json")
    has_material = bool(catalog or own_paper_snippets or draft_texts)
    skeleton_refs = [
        {
            "ref_id": r["ref_id"],
            "cite_key": r.get("cite_key") or r["ref_id"],
            "authors": r.get("authors") or "",
            "year": r.get("year") or "",
            "title": r.get("title") or "",
            "venue": r.get("venue") or "",
            "doi_or_id": r.get("doi_or_id") or "",
            "recency_role": r.get("recency_role") or "representative",
            "source_locator": (r.get("source_locators") or ["unknown"])[0],
            "verification_status": (
                "metadata_conflict"
                if any(r["ref_id"].lower() in json.dumps(c, ensure_ascii=False).lower() for c in conflicts)
                else ("partial_metadata" if r.get("missing_metadata_fields") else "verified")
            ),
            "formatted_citation": "",
        }
        for r in catalog
    ]
    skeleton_confirm = []
    for i, g in enumerate(pre_gaps, start=1):
        iss = str(g.get("issue") or "missing_metadata")
        if iss == "incomplete_reference_metadata":
            loc, sev = "references", "important"
        elif iss == "unspecified_citation_style":
            loc, sev = "global_format", "recommended"
        elif iss == "missing_own_paper_evidence":
            loc, sev = "global_own_paper", "important"
        elif iss in ("empty_literature_library", "unresolved_draft_citation"):
            loc = "manuscript_draft" if iss == "unresolved_draft_citation" else "global_literature"
            sev = "critical"
        else:
            loc, sev = g.get("location", "global"), "important"
        skeleton_confirm.append(
            {
                "gap_id": g.get("gap_id", f"GAP-{i:02d}"),
                "severity": sev,
                "location_in_draft": loc,
                "issue_type": iss,
                "question": g.get("detail", ""),
                "what_was_checked": f"All files in {plan.get('input_dir', '/input')}",
            }
        )
    skeleton = {
        "case_id": plan["case_id"],
        "status": "completed" if has_material else "insufficient_material",
        "language": plan["signals"]["language"],
        "citation_style": plan["signals"]["citation_style"],
        "task_mode": plan["signals"]["task_mode"],
        "title": str(case_data.get("title") or "Introduction and Related Work"),
        "manuscript_markdown": "",
        "research_background": {
            "domain_lineage": "",
            "concrete_problem": "",
            "technical_constraints": [],
            "why_now": "",
            "evidence_locators": [],
            "citation_ids": [],
        },
        "related_work_groups": [],
        "comparison_matrix": [],
        "research_gaps": [],
        "contributions": [],
        "claim_citation_map": [],
        "references": skeleton_refs,
        "excluded_references": [],
        "conflicts": conflicts,
        "author_confirmation_items": skeleton_confirm,
        "change_records": [],
        "uncertainty": list(warnings),
        "contract_overrides": {},
    }
    save_json(out_skel, skeleton)
    print(json.dumps({"status": "ok", "references": len(catalog), "conflicts": len(conflicts), "pre_gaps": len(pre_gaps), "skeleton": str(out_skel)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
