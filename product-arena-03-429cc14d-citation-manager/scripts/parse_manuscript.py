#!/usr/bin/env python3
"""Parse academic manuscripts (.md, .txt, .docx, .tex, .html, .pdf) into structured blocks & sentences.

Features:
- Preserves section hierarchy and block types: body, figure_caption, table_caption,
  table_cell, footnote, appendix, and separates end-of-document reference section.
- Extracts all in-text citation tokens (numeric brackets/ranges, superscripts, Unicode
  superscripts, LaTeX \\cite-family commands, Author-Year parenthetical & narrative,
  and citation placeholders like [REF], [?], [待引用]).
- Flags paragraph-end citation dumps (where multi-sentence paragraphs stack citations only
  on the final sentence) and unreferenced claim candidates (numbers, comparisons, studies).
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    detect_script_lang,
    extract_docx_blocks,
    extract_numbers,
    extract_pdf_text_safe,
    load_json_safe,
    read_text_safe,
    save_json,
)

UNICODE_SUP_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")

REF_HEADER_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:参考文献|主要参考文献|references|bibliography|works\s+cited|literature\s+cited)\s*[:：]?\s*$",
    re.I,
)
APPENDIX_HEADER_RE = re.compile(r"^(?:#{1,4}\s*)?(?:appendix|appendices|附录|补充材料)\b", re.I)
FIG_CAPTION_RE = re.compile(r"^(?:\*\*)?(?:fig(?:ure)?\.?|图)\s*[A-Za-z0-9.\-]+", re.I)
TAB_CAPTION_RE = re.compile(r"^(?:\*\*)?(?:table|tab\.?|表)\s*[A-Za-z0-9.\-]+", re.I)

# Citation token extraction patterns
RE_SUP_HTML = re.compile(r"<sup>\s*\[?([0-9,\s\-–—]+)\]?\s*</sup>", re.I)
RE_SUP_MD = re.compile(r"\^\[([0-9,\s\-–—;]+)\]")
RE_SUP_UNI = re.compile(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[⁻,][⁰¹²³⁴⁵⁶⁷⁸⁹]+)*)")
RE_LATEX_CITE = re.compile(r"\\(?:cite|citep|citet|parencite|textcite|autocite|supercite)(?:\[[^\]]*\])?\{([^}]+)\}")
RE_BRACKET_NUM = re.compile(r"(?<!\^)(?<!\[)\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()")
RE_PLACEHOLDER = re.compile(r"(\[(?:REF|CITE|CITATION(?:\s+NEEDED)?|\?|待引用|待补引|引用|未核验引用[^\]]*|TODO[^\]]*)\])", re.I)
RE_AUTHOR_YEAR_PAREN = re.compile(
    r"[（(]\s*([A-Z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff.\-'\s&＆,，]*(?:et\s+al\.|等)?\s*[,，]\s*(?:19|20)\d{2}[a-z]?"
    r"(?:\s*[;；]\s*[A-Z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff.\-'\s&＆,，]*(?:et\s+al\.|等)?\s*[,，]\s*(?:19|20)\d{2}[a-z]?)*)\s*[)）]"
)
RE_AUTHOR_YEAR_NARR = re.compile(
    r"\b([A-Z][A-Za-z'\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+|\s+et\s+al\.)?)\s*\(((?:19|20)\d{2}[a-z]?)\)"
    r"|([\u4e00-\u9fff]{2,4}(?:等|和[\u4e00-\u9fff]{2,4})?)\s*[（(]((?:19|20)\d{2}[a-z]?)[)）]"
)

ATTRIBUTION_CUE_RE = re.compile(
    r"(?:previous\s+(?:work|stud(?:y|ies))|prior\s+(?:research|work)|it\s+has\s+been\s+shown|studies\s+(?:show|demonstrate|report)|"
    r"proposed\s+by|introduced\s+by|according\s+to|reported\s+that|outperform(?:s|ed)?|benchmark|"
    r"研究表明|已有研究|前人研究|文献报道|文献指出|提出了一种|相比(?:现有|传统|基线)|显著优于|准确率(?:达到|为)|p\s*[<≤=]\s*0\.\d)",
    re.I,
)


def expand_numeric_spec(spec: str) -> List[int]:
    s = spec.replace("–", "-").replace("—", "-").replace("~", "-").replace(";", ",")
    nums: List[int] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a_str, b_str = p.split("-", 1)
            a_str, b_str = a_str.strip(), b_str.strip()
            if a_str.isdigit() and b_str.isdigit():
                a, b = int(a_str), int(b_str)
                if a <= b and (b - a) <= 100:
                    nums.extend(range(a, b + 1))
                else:
                    nums.extend([a, b])
        elif p.isdigit():
            nums.append(int(p))
    return nums


def extract_citations_from_sentence(sent: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    occupied_spans: List[Tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= oa or a >= ob) for oa, ob in occupied_spans)

    for m in RE_PLACEHOLDER.finditer(sent):
        hits.append({
            "kind": "placeholder",
            "raw": m.group(1),
            "span": [m.start(), m.end()],
            "nums": [],
            "keys": [],
            "author_years": [],
        })
        occupied_spans.append((m.start(), m.end()))

    for m in RE_LATEX_CITE.finditer(sent):
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        hits.append({
            "kind": "latex_cite",
            "raw": m.group(0),
            "span": [m.start(), m.end()],
            "nums": [],
            "keys": keys,
            "author_years": [],
        })
        occupied_spans.append((m.start(), m.end()))

    for m in RE_SUP_HTML.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        nums = expand_numeric_spec(m.group(1))
        if nums:
            hits.append({
                "kind": "numeric_superscript_html",
                "raw": m.group(0),
                "span": [m.start(), m.end()],
                "nums": nums,
                "keys": [],
                "author_years": [],
            })
            occupied_spans.append((m.start(), m.end()))

    for m in RE_SUP_MD.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        nums = expand_numeric_spec(m.group(1))
        if nums:
            hits.append({
                "kind": "numeric_superscript_md",
                "raw": m.group(0),
                "span": [m.start(), m.end()],
                "nums": nums,
                "keys": [],
                "author_years": [],
            })
            occupied_spans.append((m.start(), m.end()))

    for m in RE_BRACKET_NUM.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        # Avoid false-positive [1] if immediately followed by link or definition
        nums = expand_numeric_spec(m.group(1))
        if nums:
            hits.append({
                "kind": "numeric_bracket",
                "raw": m.group(0),
                "span": [m.start(), m.end()],
                "nums": nums,
                "keys": [],
                "author_years": [],
            })
            occupied_spans.append((m.start(), m.end()))

    for m in RE_SUP_UNI.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        conv = m.group(1).translate(UNICODE_SUP_MAP)
        nums = expand_numeric_spec(conv)
        if nums:
            hits.append({
                "kind": "numeric_superscript_unicode",
                "raw": m.group(0),
                "span": [m.start(), m.end()],
                "nums": nums,
                "keys": [],
                "author_years": [],
            })
            occupied_spans.append((m.start(), m.end()))

    for m in RE_AUTHOR_YEAR_PAREN.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        inner = m.group(1)
        items = [x.strip() for x in re.split(r"[;；]", inner) if x.strip()]
        hits.append({
            "kind": "author_year_paren",
            "raw": m.group(0),
            "span": [m.start(), m.end()],
            "nums": [],
            "keys": [],
            "author_years": items,
        })
        occupied_spans.append((m.start(), m.end()))

    for m in RE_AUTHOR_YEAR_NARR.finditer(sent):
        if _overlaps(m.start(), m.end()):
            continue
        author = m.group(1) or m.group(3)
        year = m.group(2) or m.group(4)
        if author and year:
            hits.append({
                "kind": "author_year_narrative",
                "raw": m.group(0),
                "span": [m.start(), m.end()],
                "nums": [],
                "keys": [],
                "author_years": [f"{author.strip()}, {year}"],
            })
            occupied_spans.append((m.start(), m.end()))

    hits.sort(key=lambda x: x["span"][0])
    return hits


def split_sentences(text: str) -> List[str]:
    """Split English/Chinese/mixed academic prose into sentences without breaking decimals or et al."""
    if not text or not text.strip():
        return []
    s = re.sub(r"\s+", " ", text.strip())
    # Protect common abbreviations and decimals
    prot = s
    prot = re.sub(r"\b(et\s+al|Fig|Figs|Tab|Eq|Eqs|Dr|Prof|No|Vol|pp|vs|e\.g|i\.e)\.", r"\1<DOT>", prot)
    prot = re.sub(r"(\d)\.(\d)", r"\1<DEC>\2", prot)
    prot = re.sub(r"([A-Z])\.([A-Z])\.", r"\1<DOT>\2<DOT>", prot)

    cite_tail = r"(?:\s*(?:<sup>[^<]+</sup>|\^\[[^\]]+\]|\[[^\]]+\]))?"
    marked = re.sub(rf"([。！？!?]{cite_tail})\s+", r"\1<SBREAK>", prot)
    marked = re.sub(rf"(\.{cite_tail})\s+", r"\1<SBREAK>", marked)
    marked = re.sub(rf"([。！？]{cite_tail})(?=[^\s。！？])", r"\1<SBREAK>", marked)

    refined: List[str] = []
    for sp in marked.split("<SBREAK>"):
        restored = sp.replace("<DOT>", ".").replace("<DEC>", ".").strip()
        if restored:
            refined.append(restored)
    return refined if refined else [s]


def parse_markdown_or_text_manuscript(text: str, src_rel: str) -> Dict[str, Any]:
    lines = text.splitlines()
    blocks: List[Dict[str, Any]] = []
    inline_refs_lines: List[str] = []
    footnotes_lines: List[str] = []

    current_section = "Preamble"
    in_refs = False
    in_appendix = False
    buf: List[str] = []
    buf_start = 1

    def flush_buf(end_line: int) -> None:
        if not buf:
            return
        para = "\n".join(buf).strip()
        buf.clear()
        if not para:
            return
        first = para.splitlines()[0].strip()
        if FIG_CAPTION_RE.match(first):
            btype = "figure_caption"
        elif TAB_CAPTION_RE.match(first):
            btype = "table_caption"
        elif first.startswith("|") and "|" in first[1:]:
            btype = "table_cell"
        elif in_appendix:
            btype = "appendix"
        else:
            btype = "body"
        blocks.append({
            "block_id": f"B{len(blocks) + 1:03d}",
            "section": current_section,
            "block_type": btype,
            "text": para,
            "line_start": buf_start,
            "line_end": end_line,
            "source_file": src_rel,
        })

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Footnote definitions in Markdown: [^1]: ...
        if re.match(r"^\[\^[^\]]+\]:\s+", stripped):
            flush_buf(idx - 1)
            footnotes_lines.append(stripped)
            blocks.append({
                "block_id": f"B{len(blocks) + 1:03d}",
                "section": current_section,
                "block_type": "footnote",
                "text": stripped,
                "line_start": idx,
                "line_end": idx,
                "source_file": src_rel,
            })
            continue

        # Reference section heading
        if REF_HEADER_RE.match(stripped):
            flush_buf(idx - 1)
            in_refs = True
            in_appendix = False
            continue

        # Heading line
        if stripped.startswith("#"):
            flush_buf(idx - 1)
            heading_clean = stripped.lstrip("#").strip()
            if APPENDIX_HEADER_RE.match(heading_clean):
                in_refs = False
                in_appendix = True
                current_section = heading_clean
            elif in_refs:
                # Top-level or major heading after References ends reference block
                if APPENDIX_HEADER_RE.match(heading_clean) or re.match(r"^(?:appendix|附录|致谢|acknowledg)", heading_clean, re.I):
                    in_refs = False
                    current_section = heading_clean
                else:
                    inline_refs_lines.append(stripped)
                    continue
            else:
                current_section = heading_clean
            blocks.append({
                "block_id": f"B{len(blocks) + 1:03d}",
                "section": current_section,
                "block_type": "heading",
                "heading_level": len(stripped) - len(stripped.lstrip("#")),
                "text": stripped,
                "line_start": idx,
                "line_end": idx,
                "source_file": src_rel,
            })
            continue

        if in_refs:
            if stripped:
                inline_refs_lines.append(stripped)
            continue

        if not stripped:
            flush_buf(idx - 1)
            buf_start = idx + 1
        else:
            if not buf:
                buf_start = idx
            buf.append(line)

    flush_buf(len(lines))
    return {
        "blocks": blocks,
        "inline_refs_lines": inline_refs_lines,
        "footnotes": footnotes_lines,
        "format": "markdown_or_text",
    }


def parse_latex_manuscript(text: str, src_rel: str) -> Dict[str, Any]:
    lines = text.splitlines()
    blocks: List[Dict[str, Any]] = []
    inline_refs_lines: List[str] = []

    current_section = "Preamble"
    in_refs = False
    in_appendix = False
    buf: List[str] = []
    buf_start = 1

    def flush_buf(end_line: int) -> None:
        if not buf:
            return
        para = "\n".join(buf).strip()
        buf.clear()
        if not para:
            return
        btype = "body"
        if "\\caption{" in para or "\\begin{figure}" in para:
            btype = "figure_caption"
        elif "\\begin{table}" in para:
            btype = "table_caption"
        elif in_appendix:
            btype = "appendix"
        blocks.append({
            "block_id": f"B{len(blocks) + 1:03d}",
            "section": current_section,
            "block_type": btype,
            "text": para,
            "line_start": buf_start,
            "line_end": end_line,
            "source_file": src_rel,
        })

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if "\\begin{thebibliography}" in stripped:
            flush_buf(idx - 1)
            in_refs = True
            continue
        if "\\end{thebibliography}" in stripped:
            in_refs = False
            continue
        if "\\appendix" in stripped:
            flush_buf(idx - 1)
            in_appendix = True
        m_sec = re.match(r"\\(section|subsection|subsubsection)\*?\{([^}]+)\}", stripped)
        if m_sec:
            flush_buf(idx - 1)
            current_section = m_sec.group(2).strip()
            blocks.append({
                "block_id": f"B{len(blocks) + 1:03d}",
                "section": current_section,
                "block_type": "heading",
                "heading_level": {"section": 1, "subsection": 2, "subsubsection": 3}[m_sec.group(1)],
                "text": stripped,
                "line_start": idx,
                "line_end": idx,
                "source_file": src_rel,
            })
            continue
        if in_refs:
            if stripped:
                inline_refs_lines.append(stripped)
            continue
        if not stripped:
            flush_buf(idx - 1)
            buf_start = idx + 1
        else:
            if not buf:
                buf_start = idx
            buf.append(line)
    flush_buf(len(lines))
    return {"blocks": blocks, "inline_refs_lines": inline_refs_lines, "footnotes": [], "format": "latex"}


def parse_docx_manuscript(path: Path, src_rel: str) -> Dict[str, Any]:
    raw_docx = extract_docx_blocks(path)
    if raw_docx.get("error"):
        return {"blocks": [], "inline_refs_lines": [], "footnotes": [], "format": "docx", "error": raw_docx["error"]}
    blocks: List[Dict[str, Any]] = []
    inline_refs_lines: List[str] = []
    current_section = "Preamble"
    in_refs = False
    in_appendix = False
    for idx, blk in enumerate(raw_docx.get("blocks", []), start=1):
        txt = blk["text"].strip()
        if REF_HEADER_RE.match(txt):
            in_refs = True
            continue
        if blk["kind"] == "heading":
            if APPENDIX_HEADER_RE.match(txt):
                in_refs = False
                in_appendix = True
            elif in_refs:
                in_refs = False
            current_section = txt
            blocks.append({
                "block_id": f"B{len(blocks) + 1:03d}",
                "section": current_section,
                "block_type": "heading",
                "heading_level": 2,
                "text": f"## {txt}",
                "line_start": idx,
                "line_end": idx,
                "source_file": src_rel,
            })
            continue
        if in_refs:
            inline_refs_lines.extend(ln.strip() for ln in txt.splitlines() if ln.strip())
            continue
        btype = "body"
        if blk["kind"] == "table":
            btype = "table_cell"
        elif FIG_CAPTION_RE.match(txt):
            btype = "figure_caption"
        elif TAB_CAPTION_RE.match(txt):
            btype = "table_caption"
        elif in_appendix:
            btype = "appendix"
        blocks.append({
            "block_id": f"B{len(blocks) + 1:03d}",
            "section": current_section,
            "block_type": btype,
            "text": txt,
            "line_start": idx,
            "line_end": idx,
            "source_file": src_rel,
        })
    for fn in raw_docx.get("footnotes", []):
        blocks.append({
            "block_id": f"B{len(blocks) + 1:03d}",
            "section": "Footnotes",
            "block_type": "footnote",
            "text": f"[fn:{fn['id']}] {fn['text']}",
            "line_start": 0,
            "line_end": 0,
            "source_file": src_rel,
        })
    return {"blocks": blocks, "inline_refs_lines": inline_refs_lines, "footnotes": raw_docx.get("footnotes", []), "format": "docx"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse manuscript into structured blocks, sentences, and citation markers.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--out-dir", default="/app/work/manuscript")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    input_dir = Path(case_plan.get("input_dir", "/input"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ms_files = [f for f in case_plan.get("files", []) if f.get("role") == "manuscript"]
    all_blocks: List[Dict[str, Any]] = []
    all_inline_refs: List[Dict[str, Any]] = []
    parse_warnings: List[str] = []
    primary_format = "markdown"

    for f in ms_files:
        p = input_dir / f["rel_path"]
        ext = f["ext"].lower()
        if ext == ".docx":
            res = parse_docx_manuscript(p, f["rel_path"])
            primary_format = "docx"
        elif ext == ".tex":
            txt, enc, warn = read_text_safe(p)
            if warn:
                parse_warnings.append(f"{f['rel_path']}: {warn}")
            res = parse_latex_manuscript(txt, f["rel_path"])
            primary_format = "latex"
        elif ext == ".pdf":
            pdf_res = extract_pdf_text_safe(p)
            if pdf_res.get("error"):
                parse_warnings.append(f"{f['rel_path']}: {pdf_res['error']}")
            res = parse_markdown_or_text_manuscript(pdf_res.get("full_text", ""), f["rel_path"])
            primary_format = "pdf"
        elif ext in (".html", ".htm"):
            txt, enc, warn = read_text_safe(p)
            if warn:
                parse_warnings.append(f"{f['rel_path']}: {warn}")
            clean_txt = html.unescape(re.sub(r"<(?!/?sup\b)[^>]+>", " ", txt))
            res = parse_markdown_or_text_manuscript(clean_txt, f["rel_path"])
            primary_format = "html"
        else:
            txt, enc, warn = read_text_safe(p)
            if warn:
                parse_warnings.append(f"{f['rel_path']}: {warn}")
            res = parse_markdown_or_text_manuscript(txt, f["rel_path"])
            primary_format = "markdown" if ext in (".md", ".markdown") else "text"

        if res.get("error"):
            parse_warnings.append(f"{f['rel_path']}: {res['error']}")
        all_blocks.extend(res.get("blocks", []))
        for line in res.get("inline_refs_lines", []):
            all_inline_refs.append({"source_file": f["rel_path"], "raw": line})

    # Fallback: extract inline manuscript from case.json if no external manuscript files exist
    inline_ms_sources: List[str] = []
    if not ms_files:
        cmeta = case_plan.get("case_metadata") or {}
        for key in ("manuscript", "manuscript_text", "draft", "draft_text", "text", "body", "paper", "article", "sections"):
            val = cmeta.get(key)
            if not val:
                continue
            if isinstance(val, list):
                txt = "\n\n".join(
                    (f"## {item.get('heading') or item.get('section')}\n{item.get('text') or item.get('content') or ''}"
                     if isinstance(item, dict) else str(item))
                    for item in val
                )
            elif isinstance(val, dict):
                txt = "\n\n".join(f"## {hk}\n{hv}" for hk, hv in val.items() if hv)
            else:
                txt = str(val)
            if txt.strip():
                loc = f"case.json#{key}"
                inline_ms_sources.append(loc)
                if "\\section" in txt or "\\begin{document}" in txt or "\\cite" in txt:
                    res = parse_latex_manuscript(txt, loc)
                    primary_format = "latex"
                else:
                    res = parse_markdown_or_text_manuscript(txt, loc)
                    primary_format = "markdown"
                all_blocks.extend(res.get("blocks", []))
                for line in res.get("inline_refs_lines", []):
                    all_inline_refs.append({"source_file": loc, "raw": line})
                break

        raw_ir = cmeta.get("inline_references")
        if isinstance(raw_ir, str) and raw_ir.strip():
            for ln in raw_ir.splitlines():
                if ln.strip():
                    all_inline_refs.append({"source_file": "case.json#inline_references", "raw": ln.strip()})

    # Sentence segmentation & citation/placement signal detection
    sentences: List[Dict[str, Any]] = []
    paragraph_end_clusters: List[Dict[str, Any]] = []
    unreferenced_claim_candidates: List[Dict[str, Any]] = []
    caption_citations: List[Dict[str, Any]] = []
    placeholders: List[Dict[str, Any]] = []
    sections_order: List[str] = []

    for blk in all_blocks:
        sec = blk.get("section", "Preamble")
        if sec not in sections_order:
            sections_order.append(sec)
        if blk["block_type"] == "heading":
            blk["sentence_ids"] = []
            continue

        raw_sents = split_sentences(blk["text"])
        blk_sids: List[str] = []
        blk_sents_objs: List[Dict[str, Any]] = []
        for s_txt in raw_sents:
            sid = f"S{len(sentences) + 1:03d}"
            cites = extract_citations_from_sentence(s_txt)
            nums_in_sent = extract_numbers(s_txt)
            has_attribution = bool(ATTRIBUTION_CUE_RE.search(s_txt))
            has_placeholder = any(c["kind"] == "placeholder" for c in cites)
            has_real_cite = any(c["kind"] != "placeholder" for c in cites)

            sobj = {
                "sentence_id": sid,
                "block_id": blk["block_id"],
                "section": sec,
                "block_type": blk["block_type"],
                "source_file": blk["source_file"],
                "line_start": blk["line_start"],
                "line_end": blk["line_end"],
                "text": s_txt,
                "language": detect_script_lang(s_txt),
                "citations": cites,
                "has_citation": has_real_cite,
                "has_placeholder": has_placeholder,
                "numbers_in_sentence": nums_in_sent,
                "has_attribution_cue": has_attribution,
            }
            sentences.append(sobj)
            blk_sents_objs.append(sobj)
            blk_sids.append(sid)

            if has_placeholder:
                placeholders.append({"sentence_id": sid, "section": sec, "text": s_txt})
            if has_real_cite and blk["block_type"] in ("figure_caption", "table_caption", "table_cell", "footnote", "appendix"):
                caption_citations.append({
                    "sentence_id": sid,
                    "block_type": blk["block_type"],
                    "section": sec,
                    "citations": [c["raw"] for c in cites if c["kind"] != "placeholder"],
                })
            if not has_real_cite and not has_placeholder and (
                has_attribution or (len(nums_in_sent) >= 1 and blk["block_type"] == "body" and len(s_txt) > 25)
            ):
                unreferenced_claim_candidates.append({
                    "sentence_id": sid,
                    "section": sec,
                    "reason": "attribution_cue_or_quantitative_claim_without_citation",
                    "text": s_txt,
                })

        blk["sentence_ids"] = blk_sids

        # Detect paragraph-end citation cluster (e.g., S001, S002 uncited with claims; S003 has 2+ citations)
        if blk["block_type"] == "body" and len(blk_sents_objs) >= 2:
            last_s = blk_sents_objs[-1]
            earlier = blk_sents_objs[:-1]
            last_cite_count = sum(
                max(len(c["nums"]), len(c["keys"]), len(c["author_years"]), 1)
                for c in last_s["citations"]
                if c["kind"] != "placeholder"
            )
            earlier_uncited_claims = [
                es["sentence_id"] for es in earlier if not es["has_citation"] and (es["has_attribution_cue"] or es["numbers_in_sentence"])
            ]
            if last_cite_count >= 2 and earlier_uncited_claims:
                paragraph_end_clusters.append({
                    "block_id": blk["block_id"],
                    "section": sec,
                    "last_sentence_id": last_s["sentence_id"],
                    "last_citations": [c["raw"] for c in last_s["citations"]],
                    "earlier_uncited_claim_sentences": earlier_uncited_claims,
                })

    parsed = {
        "case_id": case_plan.get("case_id", ""),
        "manuscript_files": [f["rel_path"] for f in ms_files] + inline_ms_sources,
        "primary_format": primary_format,
        "sections_order": sections_order,
        "blocks": all_blocks,
        "sentences": sentences,
        "inline_references_raw": all_inline_refs,
        "signals": {
            "paragraph_end_clusters": paragraph_end_clusters,
            "unreferenced_claim_candidates": unreferenced_claim_candidates,
            "caption_and_special_citations": caption_citations,
            "placeholders": placeholders,
        },
        "parse_warnings": parse_warnings,
        "stats": {
            "total_blocks": len(all_blocks),
            "total_sentences": len(sentences),
            "cited_sentences": sum(1 for s in sentences if s["has_citation"]),
            "placeholder_sentences": len(placeholders),
            "paragraph_end_clusters": len(paragraph_end_clusters),
            "unreferenced_claim_candidates": len(unreferenced_claim_candidates),
            "inline_references_lines": len(all_inline_refs),
        },
    }
    save_json(out_dir / "manuscript_parsed.json", parsed)

    # Compact digest for LLM Step 4
    digest_lines = [
        f"# Manuscript Claims & Citation Digest (`{parsed['case_id']}`)",
        "",
        f"- **Manuscript files**: {parsed['manuscript_files']}",
        f"- **Total blocks**: {parsed['stats']['total_blocks']} | **Sentences**: {parsed['stats']['total_sentences']} | **Cited sentences**: {parsed['stats']['cited_sentences']}",
        f"- **Placeholders**: {parsed['stats']['placeholder_sentences']} | **Paragraph-end clusters**: {parsed['stats']['paragraph_end_clusters']} | **Unreferenced claim candidates**: {parsed['stats']['unreferenced_claim_candidates']}",
        "",
        "## Placement Anomalies & Special Anchors",
    ]
    if paragraph_end_clusters:
        for pc in paragraph_end_clusters:
            digest_lines.append(f"- **Paragraph-end cluster** in `{pc['block_id']}` ({pc['section']}): `{pc['last_sentence_id']}` holds {pc['last_citations']} while earlier sentences {pc['earlier_uncited_claim_sentences']} carry uncited claims.")
    if placeholders:
        for ph in placeholders:
            digest_lines.append(f"- **Placeholder** at `{ph['sentence_id']}` ({ph['section']}): {ph['text']}")
    if caption_citations:
        for cc in caption_citations:
            digest_lines.append(f"- **Caption/Footnote/Appendix cite** at `{cc['sentence_id']}` (`{cc['block_type']}`): {cc['citations']}")
    digest_lines.append("")
    digest_lines.append("## Sentence-by-Sentence View")
    for s in sentences:
        cstr = ", ".join(c["raw"] for c in s["citations"]) if s["citations"] else "NONE"
        flags = []
        if s["has_placeholder"]:
            flags.append("PLACEHOLDER")
        if not s["has_citation"] and (s["has_attribution_cue"] or s["numbers_in_sentence"]):
            flags.append("NEEDS_CHECK_NO_CITE")
        if s["block_type"] != "body":
            flags.append(s["block_type"].upper())
        flag_str = f" [{' | '.join(flags)}]" if flags else ""
        digest_lines.append(f"- **`{s['sentence_id']}`** ({s['section']} | `{s['source_file']}:L{s['line_start']}`){flag_str} | Cites: `{cstr}`")
        digest_lines.append(f"  > {s['text']}")

    (out_dir / "manuscript_claims_digest.md").write_text("\n".join(digest_lines) + "\n", encoding="utf-8")
    print(json.dumps(parsed["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
