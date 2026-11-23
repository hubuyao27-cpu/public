#!/usr/bin/env python3
"""Shared utilities for scientific-structured-notes skill scripts.

Provides text normalization, anchor formatting/parsing, numeric and reference cue
extraction, and safe JSON/Markdown I/O using Python standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ANCHOR_PREFIX_RE = re.compile(
    r"^(?P<id>(?:D\d+-)?(?:P\d{3,4}|FIG:[^|\]\n]+|TAB:[^|\]\n]+|EQ:[^|\]\n]+))$"
)

# Matches numbers with optional decimals, scientific notation, or percentages while skipping identifier suffixes like MOF-74
NUMERIC_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_§.])(?<![A-Za-z]-)"
    r"(?P<val>[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?P<pct>\s*%)?"
)

# Patterns that look numeric in prose/notes but represent structural identifiers, not measured values
STRUCTURAL_NUM_CONTEXT_RE = re.compile(
    r"(?:§|Sec(?:tion)?\.?|Fig(?:ure)?\.?|Tab(?:le)?\.?|Eq(?:uation)?\.?|Scheme|"
    r"Page|p\.|pp\.|Ref\.?|Step|Stage|Case|Table|Figure|图|表|式|第|节|页|章)\s*\d+(?:\.\d+)*",
    re.IGNORECASE,
)

BRACKET_CITE_RE = re.compile(r"\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

FIG_REF_RE = re.compile(
    r"\b(?:Fig(?:ure)?\.?|Scheme)\s*(?:S?\d+[a-zA-Z]?)|图\s*\d+[a-zA-Z]?",
    re.IGNORECASE,
)
TAB_REF_RE = re.compile(
    r"\bTable\s*(?:S?\d+[a-zA-Z]?)|表\s*\d+[a-zA-Z]?",
    re.IGNORECASE,
)
EQ_REF_RE = re.compile(
    r"\b(?:Eq(?:uation)?\.?)\s*\(?\s*(?:S?\d+(?:\.\d+)?)\s*\)?|式\s*[（(]?\d+(?:\.\d+)?[）)]?",
    re.IGNORECASE,
)


def load_json(path: str | Path, default: Any = None) -> Any:
    """Read UTF-8 JSON file or return default when absent."""
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path: str | Path, data: Any) -> None:
    """Write formatted UTF-8 JSON file, creating parent directories if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text_safe(path: str | Path) -> str:
    """Read text file with UTF-8 fallback to latin-1/gb18030 and normalize line endings."""
    p = Path(path)
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def repair_hyphenation(text: str) -> str:
    """Join hyphenated line breaks and normalize internal line endings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"([A-Za-z])-\s*\n\s*([a-z])", r"\1\2", text)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse arbitrary whitespace runs into single spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_for_match(text: str) -> str:
    """Normalize text for resilient substring/quote verification."""
    if not text:
        return ""
    s = repair_hyphenation(text)
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
        "−": "-",
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
        "\u00a0": " ",
        "\u200b": "",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def token_set(text: str) -> set[str]:
    """Extract normalized alphanumeric/CJK tokens for fuzzy fallback matching."""
    norm = normalize_for_match(text)
    return set(re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?|[\u4e00-\u9fff]", norm))


def format_anchor(anchor_id: str, page: Optional[int], section: Optional[str]) -> str:
    """Format canonical citation anchor string like [P012 | p.3 | §2.1 Methods]."""
    parts = [anchor_id]
    if page is not None:
        parts.append(f"p.{page}")
    if section:
        parts.append(section)
    return "[" + " | ".join(parts) + "]"


def normalize_anchor_id(raw_id: str) -> str:
    """Extract canonical anchor ID from either bare ID or formatted '[P012 | p.3 | ...]'."""
    s = (raw_id or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    if "|" in s:
        s = s.split("|", 1)[0].strip()
    return s


def extract_cues(text: str) -> Dict[str, List[str]]:
    """Extract numeric, citation, and figure/table/equation references from text."""
    return {
        "figures": sorted(set(m.group(0).strip() for m in FIG_REF_RE.finditer(text))),
        "tables": sorted(set(m.group(0).strip() for m in TAB_REF_RE.finditer(text))),
        "equations": sorted(set(m.group(0).strip() for m in EQ_REF_RE.finditer(text))),
        "citations": sorted(set(m.group(0).strip() for m in BRACKET_CITE_RE.finditer(text))),
    }


def extract_quantitative_numbers(text: str) -> List[str]:
    """Extract likely quantitative values from text while filtering structural IDs/years."""
    if not text:
        return []
    masked = STRUCTURAL_NUM_CONTEXT_RE.sub(" ", text)
    masked = BRACKET_CITE_RE.sub(" ", masked)
    # Remove anchor tags like [P001 | p.1 | §1] before scanning
    masked = re.sub(r"\[(?:D\d+-)?(?:P\d{3,4}|FIG:[^\]]+|TAB:[^\]]+|EQ:[^\]]+)[^\]]*\]", " ", masked)

    nums: List[str] = []
    for m in NUMERIC_TOKEN_RE.finditer(masked):
        raw_val = m.group("val").replace(",", "")
        pct = "%" if m.group("pct") else ""
        # Ignore standalone 4-digit publication years when not followed by % or decimal
        if not pct and YEAR_RE.fullmatch(raw_val):
            continue
        token = f"{raw_val}{pct}"
        if token not in nums:
            nums.append(token)
    return nums


def parse_number_variants(token: str) -> List[str]:
    """Return equivalent textual representations for numeric matching (e.g. '12000' -> ['12000', '12,000'])."""
    clean = token.replace(",", "").strip()
    variants = [clean]
    if clean.endswith("%"):
        num_part = clean[:-1].strip()
        variants.append(num_part)
        try:
            f = float(num_part)
            variants.append(f"{f:g}%")
            variants.append(f"{f:g}")
        except ValueError:
            pass
    else:
        try:
            f = float(clean)
            variants.append(f"{f:g}")
            if f.is_integer() and abs(f) >= 1000:
                variants.append(f"{int(f):,}")
            elif abs(f) >= 1000 and "." in clean:
                int_p, dec_p = clean.split(".", 1)
                try:
                    variants.append(f"{int(int_p):,}.{dec_p}")
                except ValueError:
                    pass
            # Also match percentage form if value is fraction like 0.85 -> 85%
            if 0 < f < 1:
                variants.append(f"{f * 100:g}%")
        except ValueError:
            pass
    return list(dict.fromkeys(variants))


def quote_matches_text(quote: str, target_text: str) -> Tuple[bool, float, str]:
    """Check whether quote is grounded in target_text.

    Returns (matched, overlap_ratio, match_mode).
    """
    nq = normalize_for_match(quote)
    nt = normalize_for_match(target_text)
    if not nq:
        return False, 0.0, "empty_quote"
    if nq in nt:
        return True, 1.0, "exact_substring"

    # Support ellipsis-separated snippets like "first clause ... second clause"
    parts = [p.strip() for p in re.split(r"\.{3}|…", nq) if len(p.strip()) >= 4]
    if len(parts) > 1 and all(p in nt for p in parts):
        return True, 1.0, "ellipsis_segments"

    qt = token_set(nq)
    tt = token_set(nt)
    if not qt:
        return False, 0.0, "no_tokens"
    overlap = len(qt & tt) / len(qt)
    if len(qt) >= 5 and overlap >= 0.85:
        return True, overlap, "high_token_overlap"
    return False, overlap, "mismatch"


def detect_language(text: str) -> str:
    """Roughly classify document language as 'zh', 'en', or 'bilingual'."""
    if not text:
        return "unknown"
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    total = cjk_count + latin_count
    if total == 0:
        return "unknown"
    cjk_ratio = cjk_count / total
    if cjk_ratio > 0.60:
        return "zh"
    if cjk_ratio < 0.10:
        return "en"
    return "bilingual"


def unique_preserve_order(items: Iterable[str]) -> List[str]:
    """Deduplicate strings while preserving first-seen order."""
    seen = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
