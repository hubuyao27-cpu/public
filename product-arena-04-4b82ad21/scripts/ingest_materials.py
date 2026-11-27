#!/usr/bin/env python3
"""Step 2: Deterministically parse & index all literature, bibliographies, drafts, and own-paper evidence.

Produces:
- /app/work/source_ledger.json (machine-verifiable citations, conflicts, own evidence, pre-gaps, grounded numbers)
- /app/work/evidence_digest.md  (compact token-efficient digest for drafting & revision)
- /app/work/section_payload_skeleton.json (pre-grounded scaffold reducing boilerplate turns)
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
    canonicalize_venue,
    extract_docx_text,
    extract_first_author_surname,
    extract_numeric_tokens,
    extract_pdf_text,
    load_json_safe,
    load_rubric_rules,
    normalize_ws,
    parse_bibtex_text,
    parse_markdown_or_text_refs,
    parse_ris_text,
    read_text_safe,
    save_json,
)


def _tier_rank(tier_name: str, precedence: List[str]) -> int:
    try:
        return precedence.index(tier_name)
    except ValueError:
        return len(precedence)


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
                    "source_tier": "bibtex_or_csl_metadata",
                }
            )
    return refs


def _extract_single_json_ref(item: Dict[str, Any], locator: str, idx: int) -> Optional[Dict[str, Any]]:
    title = str(item.get("title") or "")
    raw_auth = item.get("authors") if item.get("authors") is not None else item.get("author")
    if isinstance(raw_auth, list):
        auth_parts: List[str] = []
        for a in raw_auth:
            if isinstance(a, dict):
                nm = str(a.get("name") or a.get("author") or a.get("full_name") or "").strip()
                if not nm:
                    nm = f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                if nm:
                    auth_parts.append(nm)
            elif a:
                auth_parts.append(str(a).strip())
        authors = "; ".join(auth_parts)
    else:
        authors = str(raw_auth or "")
    year = str(item.get("year") or item.get("publicationYear") or "")
    if not year and isinstance(item.get("issued"), dict):
        dp = item["issued"].get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
            year = str(dp[0][0])
    venue = str(item.get("venue") or item.get("journal") or item.get("container-title") or item.get("booktitle") or item.get("publicationVenue") or "")
    if isinstance(item.get("publicationVenue"), dict):
        venue = str(item["publicationVenue"].get("name") or venue)
    ext_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    doi = str(item.get("doi") or item.get("DOI") or ext_ids.get("DOI") or ext_ids.get("ArXiv") or item.get("url") or item.get("arxiv") or "")
    raw_ck = str(item.get("cite_key") or item.get("key") or item.get("id") or "").strip()
    if not raw_ck or raw_ck.startswith(("http://", "https://")) or len(raw_ck) > 64:
        raw_ck = f"{extract_first_author_surname(authors) or 'Ref'}{year}_{idx+1}"
    abs_parts = [
        str(item.get(k))
        for k in ("abstract", "summary", "tldr", "conclusion", "findings", "limitation", "limitations", "applicability", "notes")
        if item.get(k)
    ]
    if title or authors or doi or abs_parts:
        return {
            "cite_key": raw_ck,
            "entry_type": str(item.get("entry_type") or item.get("type") or "article"),
            "authors": authors,
            "year": year,
            "title": title,
            "venue": venue,
            "doi_or_id": doi,
            "volume": str(item.get("volume") or ""),
            "issue": str(item.get("issue") or item.get("number") or ""),
            "pages": str(item.get("pages") or ""),
            "abstract": " | ".join(abs_parts),
            "source_locator": locator,
            "source_tier": "bibtex_or_csl_metadata",
        }
    return None


def _extract_refs_from_json(data: Any, prefix: str) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            rec = _extract_single_json_ref(item, f"{prefix}[{idx}]", idx)
            if rec:
                refs.append(rec)
    elif isinstance(data, dict):
        found_container = False
        for k in ("references", "papers", "literature", "bibliography", "items", "entries", "citations", "works", "sources", "refs", "studies", "articles", "documents", "records", "corpus"):
            if k in data and isinstance(data[k], list):
                found_container = True
                refs.extend(_extract_refs_from_json(data[k], f"{prefix}#{k}"))
        if not found_container and "output_contract" not in data:
            # Support dict-of-dicts keyed by cite_key (e.g. {"Smith2023": {"title": ...}})
            dict_vals = [(k, v) for k, v in data.items() if isinstance(v, dict) and any(f in v for f in ("title", "authors", "author", "doi", "DOI", "year"))]
            if dict_vals:
                found_container = True
                for idx, (ck, obj) in enumerate(dict_vals):
                    enriched = dict(obj)
                    enriched.setdefault("cite_key", ck)
                    rec = _extract_single_json_ref(enriched, f"{prefix}#{ck}", idx)
                    if rec:
                        refs.append(rec)
        # Single-object reference JSON (e.g., papers/smith2023.json)
        if not found_container and any(k in data for k in ("title", "doi", "DOI", "cite_key")) and "output_contract" not in data:
            rec = _extract_single_json_ref(data, prefix, 0)
            if rec:
                refs.append(rec)
    return refs


def _extract_prose_card_metadata(lines: List[Tuple[str, str]], rel: str, source_tier: str = "structured_notes") -> Optional[Dict[str, Any]]:
    """Extract structured reference metadata if a markdown/txt/PDF file describes a single paper."""
    if not lines:
        return None
    head_text = "\n".join(ln for _, ln in lines[:45])
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
        ym2 = re.search(r"\b((?:19|20)\d{2})\b", head_text[:600])
        year = ym2.group(1) if ym2 else ""
    venue = venue_m.group(1).strip() if venue_m else ""
    doi = doi_m.group(1).rstrip(".") if doi_m else ""
    ck = ck_m.group(1).strip() if ck_m else f"{extract_first_author_surname(authors) or Path(rel).stem}{year}"
    return {
        "cite_key": ck,
        "authors": authors,
        "year": year,
        "title": title,
        "venue": venue,
        "doi_or_id": doi,
        "abstract": " ".join(ln for _, ln in lines[:24])[:1100],
        "source_locator": lines[0][0],
        "source_tier": source_tier,
    }


def _extract_pdf_paper_card(lines: List[Tuple[str, str]], rel: str) -> Optional[Dict[str, Any]]:
    """Extract primary paper metadata from page 1 of a real academic PDF without swallowing its References section."""
    if not lines:
        return None
    explicit = _extract_prose_card_metadata(lines, rel, source_tier="original_paper_or_pdf")
    p1_lines = [(loc, ln) for loc, ln in lines if ":p1_" in loc][:55]
    if not p1_lines:
        p1_lines = lines[:55]
    p1_text = "\n".join(ln for _, ln in p1_lines)

    doi_m = re.search(r"(10\.\d{4,9}/[^\s,;)\]]+|arXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?)", p1_text, re.I)
    doi = re.sub(r"\s+", "", doi_m.group(1)).rstrip(".") if doi_m else ""

    # Prioritize publication-metadata lines (headers/copyright/Published/DOI) before scanning abstract prose
    year = explicit.get("year", "") if explicit else ""
    if not year:
        pub_ym = re.search(
            r"(?:Published|Accepted|Received|Available\s+online|Proceedings|Conference|Journal|©|\(c\)|DOI|arXiv\s*:)[^\n]{0,60}?\b((?:19|20)\d{2})\b",
            p1_text,
            re.I,
        )
        if pub_ym:
            year = pub_ym.group(1)
    if not year and doi.lower().startswith("arxiv:"):
        yy = doi.split(":", 1)[1][:2]
        if yy.isdigit():
            year = f"20{yy}"
    if not year:
        # Check non-abstract top/bottom header lines before falling back to whole page-1 text
        non_abs_top = "\n".join(ln for _, ln in p1_lines[:15] if not re.match(r"^(?:abstract|摘要)\b", ln.strip(), re.I))
        ym = re.search(r"\b((?:19|20)\d{2})\b", non_abs_top) or re.search(r"\b((?:19|20)\d{2})\b", p1_text)
        if ym:
            year = ym.group(1)

    banner_re = re.compile(
        r"^(?:arXiv:|https?://|doi:|ISSN|Vol\.|Volume|Issue|Research\s+Article|Review\s+Article|Original\s+Article|Letter|Communication|Available\s+online|ScienceDirect|Published\s+in|Preprint|Proceedings\s+of|Accepted\s+(?:at|to)|Contents\s+lists\s+available|©|\d+$)",
        re.I,
    )
    title_cand = explicit.get("title", "") if explicit else ""
    authors_cand = explicit.get("authors", "") if explicit else ""
    venue_cand = explicit.get("venue", "") if explicit else ""

    if not venue_cand:
        vm = re.search(
            r"(?:Published\s+(?:as\s+a\s+conference\s+paper\s+at|in)|Proceedings\s+of|Accepted\s+(?:at|to))\s+([^\n]{4,100})",
            p1_text,
            re.I,
        )
        if vm:
            venue_cand = vm.group(1).strip(" .,")

    title_idx = -1
    if not title_cand:
        for idx, (_, ln) in enumerate(p1_lines[:20]):
            s = ln.strip()
            if len(s) < 10 or len(s) > 220 or banner_re.match(s):
                continue
            if re.match(r"^(?:abstract|摘要)\b", s, re.I):
                break
            title_cand = s
            title_idx = idx
            # Join a wrapped second title line if it does not look like authors/affiliation/abstract
            if idx + 1 < len(p1_lines[:20]):
                nxt = p1_lines[idx + 1][1].strip()
                if (
                    4 <= len(nxt) <= 120
                    and not banner_re.match(nxt)
                    and not re.match(r"^(?:abstract|摘要)\b", nxt, re.I)
                    and "@" not in nxt
                    and "," not in nxt
                    and " and " not in nxt.lower()
                    and not any(w in nxt.lower() for w in ("university", "institute", "department", "大学", "学院"))
                ):
                    title_cand = f"{title_cand} {nxt}"
                    title_idx = idx + 1
            break

    if not authors_cand and title_idx >= 0:
        for _, ln in p1_lines[title_idx + 1 : title_idx + 7]:
            s = ln.strip()
            if re.match(r"^(?:abstract|摘要|keywords|1\.?\s+introduction)\b", s, re.I):
                break
            if "@" in s or any(w in s.lower() for w in ("university", "institute", "department", "laboratory", "college", "大学", "学院", "研究所")):
                continue
            if 4 <= len(s) <= 180 and ("," in s or " and " in s.lower() or "、" in s or re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", s)):
                authors_cand = re.sub(r"[∗*†‡§\d]+", "", s).strip(" ,;")
                break

    abs_lines: List[str] = []
    in_abs = False
    for _, ln in lines[:140]:
        s = ln.strip()
        if re.match(r"^(?:abstract|摘要)\s*[:：.-]?\s*(.*)$", s, re.I):
            in_abs = True
            rest = re.sub(r"^(?:abstract|摘要)\s*[:：.-]?\s*", "", s, flags=re.I).strip()
            if rest:
                abs_lines.append(rest)
            continue
        if in_abs:
            if re.match(r"^(?:1\.?\s*introduction|keywords|index\s+terms|引言|关键词)\b", s, re.I) or len(" ".join(abs_lines)) > 1100:
                break
            abs_lines.append(s)

    abstract = " ".join(abs_lines)[:1100] if abs_lines else " ".join(ln for _, ln in p1_lines[:22])[:1100]
    if not (title_cand or authors_cand or doi):
        return None
    surname = extract_first_author_surname(authors_cand)
    stem_clean = re.sub(r"[^A-Za-z0-9_]", "", Path(rel).stem)[:24] or "Paper"
    ck = (explicit.get("cite_key") if explicit and explicit.get("cite_key") else None) or f"{surname or stem_clean}{year}"
    return {
        "cite_key": ck,
        "authors": authors_cand,
        "year": year,
        "title": title_cand,
        "venue": venue_cand,
        "doi_or_id": doi,
        "abstract": abstract,
        "source_locator": p1_lines[0][0],
        "source_tier": "original_paper_or_pdf",
    }


def _extract_literature_section_digest(lines: List[Tuple[str, str]], rel: str, max_chars: int = 1600) -> Dict[str, Any]:
    """Build a multi-section evidence card (Intro/Method/Experiments/Limitations/Conclusion) before References."""
    buckets: Dict[str, List[str]] = {
        "abstract_intro": [],
        "method": [],
        "experiments_metrics": [],
        "limitations_conclusion": [],
    }
    cur_sec = "abstract_intro"
    stop_refs_re = re.compile(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:references|bibliography|acknowledg?ments?|参考文献|致谢)\b", re.I)
    sec_re = re.compile(r"^(?:#+\s*)?(?:(?:\d+(?:\.\d+)?|[IVX]+)\.?\s+)?(abstract|introduction|related\s+work|method(?:ology|s)?|approach|architecture|model|experiment(?:s|al)?|evaluation|results?|ablation|discussion|limitation(?:s)?|conclusion(?:s)?|future\s+work|摘要|引言|方法|实验|结果|讨论|局限性?|结论|总结)\b", re.I)
    cue_re = re.compile(r"(?:limit(?:ation)?|drawback|however|future\s+work|cannot|fail|outperform|achiev|accuracy|f1|mae|rmse|auc|%|％|局限|不足|然而|难以|达到|准确率)", re.I)

    for loc, ln in lines:
        s = ln.strip()
        if len(loc.split(":")) > 1 and ":p1_" not in loc and stop_refs_re.match(s):
            break
        if len(buckets["abstract_intro"]) > 5 and stop_refs_re.match(s):
            break
        sm = sec_re.match(s)
        if sm and len(s) < 90:
            kw = sm.group(1).lower()
            if any(w in kw for w in ("method", "approach", "architecture", "model", "方法")):
                cur_sec = "method"
            elif any(w in kw for w in ("experiment", "evaluation", "result", "ablation", "实验", "结果")):
                cur_sec = "experiments_metrics"
            elif any(w in kw for w in ("limitation", "discussion", "conclusion", "future", "讨论", "局限", "结论", "总结")):
                cur_sec = "limitations_conclusion"
            else:
                cur_sec = "abstract_intro"
            buckets[cur_sec].append(f"[{loc}] {s}")
            continue
        # Capture section openings + quantitative/limitation sentences across full paper
        if cue_re.search(s) or len(buckets[cur_sec]) < 4:
            if len(buckets[cur_sec]) < 8:
                buckets[cur_sec].append(f"[{loc}] {s}")

    rendered_parts: List[str] = []
    for label, key in (
        ("Abstract/Intro", "abstract_intro"),
        ("Method", "method"),
        ("Experiments/Results", "experiments_metrics"),
        ("Limitations/Conclusion", "limitations_conclusion"),
    ):
        items = buckets[key]
        if items:
            rendered_parts.append(f"**{label}**:")
            rendered_parts.extend(items[:6])
    summary_text = "\n".join(rendered_parts)[:max_chars]
    return {
        "file": rel,
        "first_locator": lines[0][0],
        "line_count": len(lines),
        "preview_lines": summary_text.splitlines() if summary_text else [f"[{loc}] {ln}" for loc, ln in lines[:18]],
    }


def _extract_heading_aware_snippet(lines: List[Tuple[str, str]], max_chars: int = 2400) -> str:
    """Capture intro/method/experiment/result/table/figure lines from an own-paper file cleanly."""
    if not lines:
        return ""
    selected: List[str] = []
    seen_idx = set()
    # Always include first 25 lines
    for idx, (loc, ln) in enumerate(lines[:25]):
        selected.append(f"[{loc}] {ln}")
        seen_idx.add(idx)
    # Also prioritize lines mentioning results/tables/figures/metrics/methods/limitations beyond line 25
    key_re = re.compile(
        r"(?:^#+\s+|\\section|\\subsection|table\s*\d|fig(?:ure)?\.?\s*\d|表\s*\d|图\s*\d|%|％|accuracy|f1|mae|rmse|ablation|limitation|method|experiment|result|方法|实验|结果|消融|局限)",
        re.I,
    )
    total = sum(len(s) + 1 for s in selected)
    for idx, (loc, ln) in enumerate(lines[25:], start=25):
        if total >= max_chars:
            break
        if key_re.search(ln):
            entry = f"[{loc}] {ln}"
            selected.append(entry)
            seen_idx.add(idx)
            total += len(entry) + 1
    # Fill remaining budget sequentially if short
    for idx, (loc, ln) in enumerate(lines[25:], start=25):
        if total >= max_chars:
            break
        if idx not in seen_idx:
            entry = f"[{loc}] {ln}"
            selected.append(entry)
            total += len(entry) + 1
    return "\n".join(selected)[:max_chars]


def _extract_draft_citations(text: str) -> List[str]:
    markers: List[str] = []
    for m in re.finditer(r"\[([A-Za-z0-9_,\s;-]+)\]", text):
        inner = m.group(1).strip()
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT")):
            continue
        if re.match(r"^\d+\s*[-–]\s*\d+$", inner):
            markers.append(f"[{inner}]")
            continue
        for part in re.split(r"[,;]", inner):
            p = part.strip()
            if p and (
                re.match(r"^R?\d{1,3}$", p)
                or (re.match(r"^[A-Za-z][A-Za-z0-9_:-]{2,30}$", p) and re.search(r"(?:19|20)\d{2}", p))
            ):
                markers.append(p)
    for m in re.finditer(r"\\cite[a-z]*\{([^}]+)\}", text):
        for k in m.group(1).split(","):
            if k.strip():
                markers.append(k.strip())
    for m in re.finditer(r"\(([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?),?\s*((?:19|20)\d{2}[a-z]?)\)", text):
        markers.append(f"{m.group(1).split()[0]}{m.group(2)}")
    return markers


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest heterogeneous writing materials into source_ledger.json and evidence_digest.md")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--out-ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--out-digest", default="/app/work/evidence_digest.md")
    ap.add_argument("--out-skeleton", default=None, help="Optional output path for pre-populated section_payload_skeleton.json")
    ap.add_argument("--extracted-dir", default=None, help="Optional directory to save lossless [locator]-tagged extracted text files")
    args = ap.parse_args()

    plan, err = load_json_safe(Path(args.case_plan))
    if err or not isinstance(plan, dict):
        raise SystemExit(f"Failed to load case_plan: {err}")

    rubric = load_rubric_rules()
    tier_precedence: List[str] = list(rubric.get("evidence_tier_precedence") or [])
    venue_aliases: Dict[str, str] = dict(rubric.get("venue_aliases") or {})
    cliche_patterns: List[str] = list(rubric.get("cliche_openings_zh") or []) + list(rubric.get("cliche_openings_en") or [])

    extracted_dir = Path(args.extracted_dir) if args.extracted_dir else (Path(args.out_digest).parent / "extracted")
    extracted_dir.mkdir(parents=True, exist_ok=True)
    extracted_files_index: List[Dict[str, Any]] = []

    def _save_lossless_extracted(rel_name: str, lines: List[Tuple[str, str]]) -> str:
        if not lines:
            return ""
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", rel_name).strip("_") or "source"
        if not safe_name.endswith(".md"):
            safe_name = f"{safe_name}.md"
        out_p = extracted_dir / safe_name
        body = [f"# Extracted Source: `{rel_name}`", ""] + [f"[{loc}] {ln}" for loc, ln in lines]
        out_p.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        extracted_files_index.append({"source": rel_name, "extracted_file": safe_name, "lines": len(lines)})
        return safe_name

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

    # 1. Process inline materials in case.json
    if case_data:
        inline_refs = _extract_refs_from_json(case_data, "case.json")
        raw_ref_records.extend(inline_refs)
        for r in inline_refs:
            if r.get("abstract"):
                for tok in extract_numeric_tokens(str(r["abstract"]), filter_structural=False):
                    grounded_numbers.append({"locator": r.get("source_locator", "case.json"), **tok})
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
            own_paper_snippets.append({"locator": f"case.json#{k}", "text": txt[:2400]})
            for tok in extract_numeric_tokens(txt, filter_structural=False):
                grounded_numbers.append({"locator": f"case.json#{k}", **tok})
            for fm in re.finditer(r"(?:Fig(?:ure)?|Table|Tab\.|图|表)\s*\.?\s*[A-Za-z0-9.-]+", txt, re.I):
                figure_table_mentions.append({"marker": fm.group(0), "locator": f"case.json#{k}"})

    # Also parse inline BibTeX / RIS / numbered references & metrics inside task.md (always check, dedup merges overlaps)
    task_md_path = Path(plan["task_md_path"]) if plan.get("task_md_path") else None
    if task_md_path and task_md_path.exists():
        t_txt, _ = read_text_safe(task_md_path)
        if t_txt:
            for tok in extract_numeric_tokens(t_txt, filter_structural=False):
                grounded_numbers.append({"locator": "task.md", **tok})
            if "@" in t_txt and re.search(r"@\w+\s*[{(]", t_txt):
                for rec in parse_bibtex_text(t_txt, "task.md"):
                    rec["source_tier"] = "bibtex_or_csl_metadata"
                    raw_ref_records.append(rec)
            elif "TY  -" in t_txt:
                for rec in parse_ris_text(t_txt, "task.md"):
                    rec["source_tier"] = "bibtex_or_csl_metadata"
                    raw_ref_records.append(rec)
            else:
                for rec in parse_markdown_or_text_refs(t_txt, "task.md"):
                    rec["source_tier"] = "structured_notes"
                    raw_ref_records.append(rec)

    # 2. Process all files in inventory
    bucket_order = {
        "literature_source": 0,
        "bibliography": 1,
        "own_paper_evidence": 2,
        "own_paper_draft": 3,
        "other_text": 4,
        "binary_or_figure": 5,
    }
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

        if ext == ".bib":
            txt, rerr = read_text_safe(p)
            if rerr:
                warnings.append(f"{rel}:{rerr}")
            elif txt:
                parsed_bib = parse_bibtex_text(txt, rel)
                for rec in parsed_bib:
                    rec["source_tier"] = "bibtex_or_csl_metadata"
                    raw_ref_records.append(rec)
                    # Only index numbers from abstract/note (never DOI/year/pages!)
                    if rec.get("abstract"):
                        for tok in extract_numeric_tokens(str(rec["abstract"]), filter_structural=False):
                            grounded_numbers.append({"locator": rec["source_locator"], **tok})
            continue

        if ext == ".ris":
            txt, rerr = read_text_safe(p)
            if rerr:
                warnings.append(f"{rel}:{rerr}")
            elif txt:
                for rec in parse_ris_text(txt, rel):
                    rec["source_tier"] = "bibtex_or_csl_metadata"
                    raw_ref_records.append(rec)
                    if rec.get("abstract"):
                        for tok in extract_numeric_tokens(str(rec["abstract"]), filter_structural=False):
                            grounded_numbers.append({"locator": rec["source_locator"], **tok})
            continue

        if ext == ".json":
            jdata, jerr = load_json_safe(p)
            if jerr:
                warnings.append(f"{rel}:{jerr}")
            elif jdata is not None:
                jrefs = _extract_refs_from_json(jdata, rel)
                if jrefs:
                    raw_ref_records.extend(jrefs)
                    for r in jrefs:
                        if r.get("abstract"):
                            for tok in extract_numeric_tokens(str(r["abstract"]), filter_structural=False):
                                grounded_numbers.append({"locator": r["source_locator"], **tok})
                jtxt = json.dumps(jdata, ensure_ascii=False)
                if not jrefs or bucket == "own_paper_evidence":
                    for tok in extract_numeric_tokens(jtxt, filter_structural=False):
                        grounded_numbers.append({"locator": rel, **tok})
                if bucket == "own_paper_evidence" or (
                    isinstance(jdata, dict) and any(k in jdata for k in ("results", "metrics", "experiments", "ablation"))
                ):
                    own_paper_snippets.append({"locator": rel, "text": jtxt[:2400]})
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
                    own_paper_snippets.append({"locator": f"{rel}:L1", "text": txt[:2400]})
                for ln_no, ln in enumerate(txt.splitlines(), start=1):
                    for tok in extract_numeric_tokens(ln, filter_structural=False):
                        grounded_numbers.append({"locator": f"{rel}:L{ln_no}", **tok})
            continue

        # Text/MD/TeX/DOCX/PDF
        lines, lwarns = _read_document_lines(item)
        warnings.extend(lwarns)
        if not lines:
            continue
        _save_lossless_extracted(rel, lines)
        full_txt = "\n".join(ln for _, ln in lines)

        for loc, ln in lines:
            # Filter structural indices (headings, [1], Fig. 1, years) on raw narrative lines while preserving table/metric specificity
            for tok in extract_numeric_tokens(ln, filter_structural=(bucket not in ("own_paper_evidence", "own_paper_draft"))):
                grounded_numbers.append({"locator": loc, **tok})
            if bucket in ("own_paper_evidence", "own_paper_draft"):
                for fm in re.finditer(r"(?:Fig(?:ure)?|Table|Tab\.|图|表)\s*\.?\s*[A-Za-z0-9.-]+", ln, re.I):
                    figure_table_mentions.append({"marker": fm.group(0), "locator": loc})

        if bucket == "own_paper_draft":
            draft_texts.append((rel, full_txt))
            if any(w in full_txt.lower() for w in ("method", "experiment", "result", "table", "fig", "方法", "实验", "结果")):
                own_paper_snippets.append({"locator": lines[0][0], "text": _extract_heading_aware_snippet(lines, max_chars=3500)})
        elif bucket == "own_paper_evidence":
            own_paper_snippets.append({"locator": lines[0][0], "text": _extract_heading_aware_snippet(lines, max_chars=3500)})
        else:
            if ext == ".pdf" and bucket != "bibliography":
                card = _extract_pdf_paper_card(lines, rel)
                if card:
                    raw_ref_records.append(card)
            else:
                mrefs = parse_markdown_or_text_refs(full_txt, rel)
                for mr in mrefs:
                    mr["source_tier"] = "structured_notes"
                if mrefs:
                    raw_ref_records.extend(mrefs)
                # Extract multi-section paper cards from structured markdown/note files cleanly
                sec_starts = [idx for idx, (_, ln) in enumerate(lines) if re.match(r"^#{1,3}\s+(?:Paper|文献|\[?R?\d+|.+?\((?:19|20)\d{2}\))", ln.strip(), re.I)]
                if len(sec_starts) >= 2:
                    src_tier = "structured_notes"
                    for si, st in enumerate(sec_starts):
                        en = sec_starts[si + 1] if si + 1 < len(sec_starts) else len(lines)
                        card = _extract_prose_card_metadata(lines[st:en], f"{rel}#S{si+1}", source_tier=src_tier)
                        if card:
                            card["source_locator"] = lines[st][0]
                            raw_ref_records.append(card)
                elif len(mrefs) <= 1 and not re.search(r"^(?:#\s*文献笔记|-\s*\[R\d+\])", full_txt, re.M):
                    src_tier = "original_paper_or_pdf" if ext in (".pdf", ".tex", ".docx") else "structured_notes"
                    card = _extract_prose_card_metadata(lines, rel, source_tier=src_tier)
                    if card:
                        raw_ref_records.append(card)
            literature_snippets.append(_extract_literature_section_digest(lines, rel))

    # 3. Deduplicate references in strict evidence_tier_precedence order & detect cross-source conflicts
    raw_ref_records.sort(key=lambda rec: _tier_rank(str(rec.get("source_tier") or "structured_notes"), tier_precedence))

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
        rec_tier = str(rec.get("source_tier") or "structured_notes")
        match = None
        if ndoi and ndoi in by_doi:
            match = by_doi[ndoi]
        elif ck and ck in by_key:
            cand_m = by_key[ck]
            old_nt = _norm_title(cand_m.get("title") or "")
            # Reject cite_key match when both non-empty normalized titles clearly disagree
            if ntitle and old_nt and len(ntitle) >= 12 and len(old_nt) >= 12 and ntitle != old_nt and ntitle not in old_nt and old_nt not in ntitle:
                match = None
                suff = 2
                while f"{ck}_{suff}" in by_key:
                    suff += 1
                ck = f"{ck}_{suff}"
            else:
                match = cand_m
        if match is None and ntitle and len(ntitle) >= 12 and ntitle in by_title:
            match = by_title[ntitle]

        if match is None:
            item_entry = {
                "ref_id": f"R{len(catalog)+1:02d}",
                "cite_key": ck or f"R{len(catalog)+1:02d}",
                "entry_type": str(rec.get("entry_type") or "article"),
                "authors": rec.get("authors", ""),
                "first_author_surname": extract_first_author_surname(rec.get("authors", "")),
                "year": str(rec.get("year") or ""),
                "title": rec.get("title", ""),
                "venue": rec.get("venue", ""),
                "doi_or_id": rec.get("doi_or_id", ""),
                "volume": str(rec.get("volume") or ""),
                "issue": str(rec.get("issue") or ""),
                "pages": str(rec.get("pages") or ""),
                "abstract_or_notes": rec.get("abstract", ""),
                "source_locators": [rec.get("source_locator", "")],
                "source_tier": rec_tier,
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
            # Check conflicts on year / doi_or_id / venue / first-author surname
            for field in ("year", "doi_or_id", "venue", "authors"):
                v_old = str(match.get(field) or "").strip()
                v_new = str(rec.get(field) or "").strip()
                if not (v_old and v_new):
                    continue
                is_conflict = False
                if field in ("year", "doi_or_id"):
                    is_conflict = v_old.lower() != v_new.lower()
                elif field == "venue":
                    c1 = canonicalize_venue(v_old, venue_aliases)
                    c2 = canonicalize_venue(v_new, venue_aliases)
                    if len(c1) >= 4 and len(c2) >= 4 and c1 != c2 and c1 not in c2 and c2 not in c1:
                        is_conflict = True
                elif field == "authors":
                    s1 = extract_first_author_surname(v_old).lower()
                    s2 = extract_first_author_surname(v_new).lower()
                    if len(s1) >= 2 and len(s2) >= 2 and s1 != s2:
                        is_conflict = True
                if is_conflict:
                    conf_idx += 1
                    conflicts.append(
                        {
                            "conflict_id": f"CONF-{conf_idx:02d}",
                            "item": f"{match['ref_id']} ({match['cite_key']}) {field}",
                            "sources_compared": [
                                {"source_locator": match["source_locators"][0], "source_tier": match.get("source_tier", "primary_record"), "value": v_old},
                                {"source_locator": loc, "source_tier": rec_tier, "value": v_new},
                            ],
                            "adopted_value_or_status": "requires_author_confirmation",
                            "rationale": f"Conflicting {field} across sources ({v_old} [{match.get('source_tier')}] vs {v_new} [{rec_tier}]).",
                            "needs_author_confirmation": True,
                        }
                    )
            # Enrich missing fields from secondary record
            for field in ("authors", "year", "title", "venue", "doi_or_id", "volume", "issue", "pages", "abstract_or_notes"):
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
        for pat in cliche_patterns:
            for cm in re.finditer(pat, dtxt, re.I):
                draft_audit["cliches_detected"].append({"locator": dloc, "phrase": cm.group(0)})
        for dm in re.finditer(r"\[(\d+)\s*[-–]\s*(\d+)\]", dtxt):
            if int(dm.group(2)) - int(dm.group(1)) + 1 >= 4:
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
    for mref in plan.get("missing_referenced_files") or []:
        g_idx += 1
        pre_gaps.append({"gap_id": f"GAP-{g_idx:02d}", "issue": "missing_referenced_file", "detail": f"File '{mref}' referenced in case.json does not exist in /input/."})
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

    # Deduplicate grounded_numbers_index to avoid quadratic verifier bottlenecks on multi-page PDFs
    dedup_nums: List[Dict[str, Any]] = []
    seen_num_keys = set()
    for gt in grounded_numbers:
        nk = (round(float(gt["value"]), 6), str(gt.get("unit") or ""), bool(gt.get("has_percent")))
        if nk not in seen_num_keys:
            seen_num_keys.add(nk)
            dedup_nums.append(gt)

    ledger = {
        "case_id": plan["case_id"],
        "references_catalog": catalog,
        "own_paper_evidence": own_paper_snippets,
        "figure_table_mentions": figure_table_mentions,
        "literature_snippets": literature_snippets,
        "extracted_files": extracted_files_index,
        "draft_texts": [{"locator": loc, "text": txt[:6000]} for loc, txt in draft_texts],
        "draft_audit": draft_audit,
        "conflicts": conflicts,
        "pre_gaps": pre_gaps,
        "grounded_numbers_index": dedup_nums,
        "missing_referenced_files": plan.get("missing_referenced_files") or [],
        "access_warnings": warnings,
    }
    save_json(Path(args.out_ledger), ledger)

    # 6. Write compact evidence_digest.md including multi-section literature cards, figures/tables, drafts, and schema hints
    dlines: List[str] = [
        f"# Evidence Digest (`{plan['case_id']}`)",
        "",
        "## 1. Task & Output Contract",
        f"- **Task Mode**: `{plan['signals']['task_mode']}` | **Language**: `{plan['signals']['language']}` | **Citation Style**: `{plan['signals']['citation_style']}`",
        f"- **Requested Formats**: `{plan['signals']['requested_formats']}` | **Venue**: `{plan['signals']['audience_or_venue'] or 'Unspecified'}`",
        f"- **Required `output_contract` Keys**: `{plan['output_contract'].get('required_keys', [])}`",
        f"- **Lossless Extracted Corpus**: `{extracted_dir}` ({len(extracted_files_index)} files — inspect exact quotes/tables via `query_sources.py`)",
        "",
        f"## 2. Verified Reference Catalog ({len(catalog)} entries)",
    ]
    if catalog:
        for r in catalog:
            miss_s = f" | MISSING: {r['missing_metadata_fields']}" if r["missing_metadata_fields"] else ""
            dlines.append(
                f"- **{r['ref_id']} (`{r['cite_key']}`)** [{r['recency_role']} | `{r.get('source_tier', 'source')}`]: "
                f"{r['authors'] or '?'} ({r['year'] or '?'}). *{r['title'] or '?'}*. {r['venue'] or '?'}. "
                f"DOI/ID: `{r['doi_or_id'] or 'N/A'}` | Locators: `{r['source_locators']}`{miss_s}"
            )
            if r.get("abstract_or_notes"):
                dlines.append(f"  - *Notes/Abstract*: {r['abstract_or_notes'][:620]}")
    else:
        dlines.append("- *NONE FOUND — do NOT fabricate references.*")

    if literature_snippets:
        per_file_cap = 28 if len(literature_snippets) <= 12 else 16
        dlines.extend(["", f"## 2b. Literature & Notes Source Excerpts ({len(literature_snippets)} files)"])
        for lsn in literature_snippets:
            dlines.append(f"### `{lsn['file']}` ({lsn['line_count']} lines)")
            dlines.extend(lsn.get("preview_lines", [])[:per_file_cap])
            dlines.append("")

    dlines.extend(["", f"## 3. Own-Paper Evidence ({len(own_paper_snippets)} snippets, {len(figure_table_mentions)} fig/table markers)"])
    for sn in own_paper_snippets[:15]:
        dlines.append(f"### `{sn['locator']}`")
        dlines.append(sn["text"][:3500])
        dlines.append("")
    if figure_table_mentions:
        seen_ft = set()
        ft_strs: List[str] = []
        for ft in figure_table_mentions:
            k = (ft.get("marker", ""), ft.get("locator", ""))
            if k not in seen_ft:
                seen_ft.add(k)
                ft_strs.append(f"`{k[0]}` ({k[1]})")
        dlines.append(f"- **Detected Figures/Tables**: {', '.join(ft_strs[:25])}")
    if not own_paper_snippets and not figure_table_mentions:
        dlines.append("- *No own-paper experimental/method files discovered.*")

    if draft_texts:
        dlines.extend(["", "## 4. Existing Draft Excerpt & Audit"])
        dlines.append(f"- Clichés detected: `{draft_audit['cliches_detected']}`")
        dlines.append(f"- Citation dumps detected: `{draft_audit['citation_dumps']}`")
        dlines.append(f"- Unresolved citations: `{draft_audit['unresolved_citations']}`")
        for dloc, dtxt in draft_texts[:3]:
            dlines.append(f"### Draft `{dloc}`")
            dlines.append(dtxt[:3500])
            dlines.append("")

    dlines.extend(["", f"## 5. Conflicts ({len(conflicts)}) & Pre-Audit Gaps ({len(pre_gaps)})"])
    for c in conflicts:
        dlines.append(f"- **{c['conflict_id']}**: {c['item']} -> {c['rationale']}")
    for g in pre_gaps:
        dlines.append(f"- **{g['gap_id']}** (`{g['issue']}`): {g['detail']}")

    missing_files = plan.get("missing_referenced_files") or []
    if missing_files or warnings:
        dlines.extend(["", "## 6. Missing Referenced Files & Access Warnings"])
        for mf in missing_files:
            dlines.append(f"- `missing_referenced_file:{mf}`")
        for w in warnings:
            dlines.append(f"- `{w}`")

    dlines.extend(
        [
            "",
            "## 7. `section_payload.json` Sub-Schema & Enum Quick Reference",
            "- Tip: Put long prose in `/app/work/manuscript.md` and set `\"manuscript_file\": \"/app/work/manuscript.md\"` (`comparison_matrix` & `claim_citation_map` also auto-sync if partially supplied).",
            "- `related_work_groups[]`: `{group_id, route_name, grouping_axis, comparison_dimensions[], works_covered:[{ref_id, core_conclusion, applicability_conditions, limitations, source_locator}], route_tradeoff_summary}`",
            "- `comparison_matrix[]`: `{ref_id, route, problem_setting, method_route, dataset_and_eval, key_result, applicability_conditions, limitations, source_locator}`",
            "- `research_gaps[]`: `{gap_id, gap_statement, derivation_type:(shared_limitation|uncovered_scenario|inter_study_conflict|author_judgment), supporting_ref_ids[], supporting_evidence_locators[], how_this_work_addresses, attribution:(literature_derived|author_judgment)}`",
            "- `contributions[]`: `{contrib_id, claim_text, scope_qualification, linked_methods[], linked_figures_tables[], linked_metrics[], own_evidence_locators[], verification_status:(verified|partial_evidence|author_confirm_required)}`",
            "- `claim_citation_map[]`: `{claim_id, section, claim_excerpt, citation_ids[], source_locators[], attribution_type:(cited_fact|own_result|author_judgment|author_confirm)}`",
        ]
    )

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
            "entry_type": r.get("entry_type") or "article",
            "authors": r.get("authors") or "",
            "year": r.get("year") or "",
            "title": r.get("title") or "",
            "venue": r.get("venue") or "",
            "doi_or_id": r.get("doi_or_id") or "",
            "volume": r.get("volume") or "",
            "issue": r.get("issue") or "",
            "pages": r.get("pages") or "",
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
        elif iss in ("missing_own_paper_evidence", "missing_referenced_file"):
            loc, sev = "global_own_paper" if iss == "missing_own_paper_evidence" else "global_inputs", "important"
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
        "manuscript_file": "",
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
        "uncertainty": list(warnings) + [f"missing_referenced_file:{mf}" for mf in missing_files],
        "contract_overrides": {},
    }
    save_json(out_skel, skeleton)
    print(json.dumps({"status": "ok", "references": len(catalog), "conflicts": len(conflicts), "pre_gaps": len(pre_gaps), "skeleton": str(out_skel)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
