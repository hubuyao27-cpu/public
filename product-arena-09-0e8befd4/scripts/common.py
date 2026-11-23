#!/usr/bin/env python3
"""Shared pure-Python standard-library utilities for the Nanopublication Skill."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent


def norm_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def norm_nfkc(text: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def norm_key(text: Optional[str]) -> str:
    s = norm_nfkc(text).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)


def norm_doi(doi: Optional[str]) -> str:
    if not doi:
        return ""
    s = str(doi).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except Exception:
        return ""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    p = config_path or (SKILL_ROOT / "schema" / "config_defaults.json")
    return load_json(p)


def load_predicate_vocab(vocab_path: Optional[Path] = None) -> Dict[str, Any]:
    p = vocab_path or (SKILL_ROOT / "schema" / "predicate_vocab.json")
    return load_json(p)


def tokenize_words(text: Optional[str]) -> List[str]:
    s = norm_nfkc(text).lower()
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?|[\u4e00-\u9fff]", s)


def token_jaccard(a: str, b: str) -> float:
    sa = set(tokenize_words(a))
    sb = set(tokenize_words(b))
    if not sa or not sb:
        return 0.0
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


def text_shingles(text: str, k: int = 5) -> Set[str]:
    words = re.findall(r"[a-z0-9\u4e00-\u9fff]+", norm_nfkc(text).lower())
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(0, min(len(words) - k + 1, 800))}


def set_jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def compute_trusty_nanopub_id(assertion: Dict[str, Any], provenance_core: Dict[str, Any]) -> str:
    """Compute deterministic content-addressed Trusty-style Nanopub URN (`urn:nanopub:RA...`)."""
    payload = {
        "assertion": assertion,
        "provenance_core": provenance_core,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"urn:nanopub:RA{b64}"


def compute_dedup_fingerprint(assertion: Dict[str, Any]) -> str:
    """Compute semantic deduplication fingerprint over subject, predicate, object, polarity, and conditions."""
    subj = norm_key((assertion.get("subject") or {}).get("label", ""))
    pred = norm_key(assertion.get("predicate", ""))
    obj = norm_key((assertion.get("object") or {}).get("label", ""))
    pol = norm_key(assertion.get("polarity", "positive"))
    cond = assertion.get("conditions") or {}
    cond_parts = [
        norm_key(cond.get("experimental_system", "")),
        norm_key(cond.get("comparator", "")),
        ",".join(sorted(norm_key(p) for p in (cond.get("parameters") or []) if norm_key(p))),
        ",".join(sorted(norm_key(q) for q in (cond.get("scope_qualifiers") or []) if norm_key(q))),
    ]
    raw = "|".join([subj, pred, obj, pol, "|".join(cond_parts)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _collapse_ws_with_map(text: str) -> Tuple[str, List[int]]:
    """Collapse whitespace while mapping each collapsed index back to its original character index."""
    collapsed: List[str] = []
    idx_map: List[int] = []
    in_ws = False
    for idx, ch in enumerate(text):
        nfkc = unicodedata.normalize("NFKC", ch)
        if nfkc.isspace():
            if not collapsed:
                continue
            if not in_ws:
                collapsed.append(" ")
                idx_map.append(idx)
                in_ws = True
        else:
            for sub in nfkc:
                collapsed.append(sub)
                idx_map.append(idx)
            in_ws = False
    if collapsed and collapsed[-1] == " ":
        collapsed.pop()
        idx_map.pop()
    return "".join(collapsed), idx_map


def find_verbatim_span(quote: str, haystack: str) -> Dict[str, Any]:
    """Verify whether quote occurs verbatim (or whitespace/NFKC-normalized) inside haystack."""
    q = str(quote or "").strip()
    h = str(haystack or "")
    if not q or not h:
        return {
            "matched": False,
            "match_mode": "unmatched",
            "char_start": None,
            "char_end": None,
            "best_similarity": 0.0,
        }

    exact_idx = h.find(q)
    if exact_idx >= 0:
        return {
            "matched": True,
            "match_mode": "exact",
            "char_start": exact_idx,
            "char_end": exact_idx + len(q),
            "best_similarity": 1.0,
        }

    h_norm, h_map = _collapse_ws_with_map(h)
    q_norm, _ = _collapse_ws_with_map(q)
    if q_norm and q_norm in h_norm:
        pos = h_norm.find(q_norm)
        orig_start = h_map[pos]
        orig_end = h_map[pos + len(q_norm) - 1] + 1
        return {
            "matched": True,
            "match_mode": "ws_normalized",
            "char_start": orig_start,
            "char_end": orig_end,
            "best_similarity": 0.99,
        }

    # Case-insensitive fallback check ONLY for diagnostic reporting (still NOT matched!)
    h_low = h_norm.lower()
    q_low = q_norm.lower()
    if q_low and q_low in h_low:
        pos = h_low.find(q_low)
        orig_start = h_map[pos]
        orig_end = h_map[pos + len(q_low) - 1] + 1
        return {
            "matched": True,
            "match_mode": "case_ws_normalized",
            "char_start": orig_start,
            "char_end": orig_end,
            "best_similarity": 0.97,
        }

    sim = token_jaccard(q, h)
    return {
        "matched": False,
        "match_mode": "unmatched",
        "char_start": None,
        "char_end": None,
        "best_similarity": round(sim, 3),
    }


def extract_numeric_tokens(text: Optional[str]) -> List[str]:
    """Extract numeric literals (including scientific notation, decimals, and ranges) normalized."""
    s = norm_nfkc(text)
    if not s:
        return []
    # Strip chemical subscript digits (e.g. Li_{2}S, CO_2) and unit exponent digits (e.g. cm^-2, m^{2})
    s = re.sub(r"(?<=[A-Za-z\)])_\{?\d+\}?", "", s)
    s = re.sub(r"(?<=[A-Za-z\)])\^\{?[-+]?\d+\}?", "", s)
    # Match numbers like 32.4, -1.5, 1.2e-4, 95% (strip trailing % for number matching)
    matches = re.findall(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s)
    out: List[str] = []
    for m in matches:
        out.append(m.lstrip("+"))
    return out


def normalize_numeric_value(num_str: str) -> str:
    try:
        val = float(num_str)
        if val.is_integer():
            return str(int(val))
        return f"{val:g}"
    except Exception:
        return str(num_str)


NEGATION_PATTERNS = [
    r"\bno\s+significant\b",
    r"\bno\s+statistically\s+significant\b",
    r"\bdid\s+not\b",
    r"\bdoes\s+not\b",
    r"\bdo\s+not\b",
    r"\bnot\s+significantly\b",
    r"\bnot\s+observe\w*\b",
    r"\bfailed\s+to\b",
    r"\bwithout\s+affecting\b",
    r"\bno\s+effect\b",
    r"\bno\s+difference\b",
    r"\bnegligible\b",
    r"\bneither\b",
    r"\bunable\s+to\b",
    r"无显著",
    r"不显著",
    r"未观察到",
    r"未能",
    r"无明显",
    r"没有显著",
]

HEDGING_PATTERNS = [
    r"\bmay\b",
    r"\bmight\b",
    r"\bcould\b",
    r"\bsuggests?\s+that\b",
    r"\bhypothesi[zs]e\w*\b",
    r"\bspeculat\w*\b",
    r"\bpotential\s+(?:benefit|risk|mechanism|role|pathway|cause|effect|application|limitation)\b",
    r"\bpotentially\b",
    r"\bpreliminary\b",
    r"可能",
    r"推测",
    r"假说",
    r"提示可能",
]

CITATION_PATTERNS = [
    r"\[\s*\d+(?:\s*[-,–]\s*\d+)*\s*\]",
    r"\(\s*[A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?,?\s*(?:19|20)\d{2}[a-z]?\s*\)",
    r"\b(?:previous|prior|earlier)\s+(?:stud(?:y|ies)|work|reports?|literature)\b",
    r"\bas\s+previously\s+reported\b",
    r"既往研究",
    r"先前研究",
    r"已有文献",
]


def extract_negation_hedging(text: Optional[str]) -> Dict[str, Any]:
    """Detect negation, epistemic hedging, and prior-work citation markers in text."""
    s = norm_nfkc(text)
    neg_hits: List[str] = []
    hedge_hits: List[str] = []
    cite_hits: List[str] = []
    for pat in NEGATION_PATTERNS:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            neg_hits.append(m.group(0))
    for pat in HEDGING_PATTERNS:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            hedge_hits.append(m.group(0))
    for pat in CITATION_PATTERNS:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            cite_hits.append(m.group(0))
    return {
        "has_negation": bool(neg_hits),
        "negation_cues": neg_hits,
        "has_hedging": bool(hedge_hits),
        "hedging_cues": hedge_hits,
        "has_citation_markers": bool(cite_hits),
        "citation_cues": cite_hits,
    }


def split_sentences(text: str) -> List[str]:
    """Scientific-abbreviation-aware sentence splitter preserving exact substrings."""
    s = str(text or "").strip()
    if not s:
        return []
    # Protect common scientific abbreviations and decimals from splitting
    abbrevs = [
        "Fig.", "Figs.", "Tab.", "Eq.", "Eqs.", "Ref.", "Refs.", "Vol.", "No.",
        "et al.", "e.g.", "i.e.", "vs.", "ca.", "approx.", "Dr.", "Prof.", "Mr.", "Ms.",
        "St.", "cf.", "resp.", "min.", "max.", "wt.", "at.", "mol.",
    ]
    protected = s
    placeholders: List[Tuple[str, str]] = []
    for idx, ab in enumerate(abbrevs):
        ph = f"__ABBR{idx}__"
        if ab in protected:
            protected = protected.replace(ab, ph)
            placeholders.append((ph, ab))

    # Split on sentence-ending punctuation followed by whitespace + uppercase/digit/CJK/bracket
    # or Chinese sentence terminators (。！？)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[\(\"'\u4e00-\u9fff])|(?<=[。！？])\s*", protected)
    out: List[str] = []
    for part in parts:
        restored = part
        for ph, ab in placeholders:
            restored = restored.replace(ph, ab)
        restored = restored.strip()
        if restored:
            out.append(restored)
    return out


def strip_markdown_emphasis(text: str) -> str:
    return re.sub(r"[*_`]+", "", text or "")
