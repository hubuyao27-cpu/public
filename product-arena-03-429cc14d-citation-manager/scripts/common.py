#!/usr/bin/env python3
"""Shared pure-Python utilities for citation-reference-manager.

Stdlib-first with graceful optional wrappers for pymupdf/fitz, python-docx,
pyyaml, and system CLI tools (pdftotext, soffice). All file I/O enforces UTF-8.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
DOI_STRICT_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")
ARXIV_NEW_RE = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v(\d+))?", re.I)
ARXIV_OLD_RE = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)([a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v(\d+))?", re.I)
ARXIV_BARE_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v(\d+))?$")

COMPOUND_ZH_SURNAMES = {
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫",
    "万俟", "闻人", "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台",
    "皇甫", "宗政", "濮阳", "公冶", "太叔", "申屠", "公孙", "慕容",
    "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于", "司空", "闾丘",
    "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐",
    "段干", "百里", "呼延", "东郭", "南门", "羊舌", "微生", "公户",
    "公玉", "公仪", "梁丘", "公仲", "公上", "公门", "公山", "公坚",
    "左丘", "公伯", "西门", "公祖", "第五", "公乘", "贯丘", "公皙",
    "南荣", "东里", "东宫", "仲长", "子书", "子桑", "即墨", "达奚",
    "褚师", "吴铭",
}

NAME_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "das", "la", "le", "du", "st.", "al"}

STOPWORDS_EN = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "by", "from",
    "at", "as", "is", "are", "was", "were", "be", "been", "being", "that", "this", "these",
    "those", "using", "based", "via", "towards", "study", "analysis", "novel", "new",
    "approach", "method", "methods", "results", "effect", "effects", "model", "models",
    "system", "systems", "between", "into", "over", "under", "across", "during", "than",
}


def posix_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def load_json_safe(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text_safe(path: Path) -> Tuple[str, str, Optional[str]]:
    """Read text with UTF-8/GB18030/Latin-1 fallback and binary/corruption checks."""
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return "", "unreadable", f"read_error:{exc}"

    if len(raw) == 0:
        return "", "empty", "empty_file"

    sample = raw[:4096]
    if b"\x00" in sample:
        return "", "binary", "binary_or_null_bytes_detected"

    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            warn = None if enc in ("utf-8-sig", "utf-8") else f"decoded_with_{enc}"
            return text, enc, warn
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace"), "utf-8-replace", "decoding_replacement_used"


def detect_script_lang(text: str) -> str:
    if not text:
        return "en"
    zh_cnt = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_cnt = len(re.findall(r"[A-Za-z]", text))
    if zh_cnt == 0:
        return "en"
    if zh_cnt >= 3 and en_cnt <= zh_cnt * 4:
        return "zh"
    if zh_cnt >= 2 and en_cnt > zh_cnt * 4:
        return "mixed"
    return "zh" if zh_cnt >= en_cnt else "en"


def strip_latex_accents_and_braces(text: str) -> str:
    if not text:
        return ""
    s = str(text).strip()
    s = s.replace("---", "-").replace("--", "-").replace("``", '"').replace("''", '"')
    s = re.sub(r"\\[`'^\"~=.uvHtcdb]\s*\{?([A-Za-z])\}?", r"\1", s)
    for _ in range(3):
        s2 = re.sub(r"\{([^{}]*)\}", r"\1", s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_doi(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi\s*:\s*", "", s, flags=re.I)
    s = s.strip().rstrip(".,;)]}")
    m = DOI_RE.search(s)
    if m:
        s = m.group(1)
    s = s.rstrip(".,;)]}")
    return s.lower()


def is_valid_doi(doi: Optional[str]) -> bool:
    norm = normalize_doi(doi)
    return bool(norm and DOI_STRICT_RE.match(norm))


def normalize_arxiv(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    for pat in (ARXIV_NEW_RE, ARXIV_OLD_RE, ARXIV_BARE_RE):
        m = pat.search(s)
        if m:
            return m.group(1).lower()
    return ""


def normalize_url(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = str(raw).strip().rstrip(".,;)")
    if s.lower().startswith(("http://", "https://", "ftp://")):
        return s
    return ""


def normalize_title(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = strip_latex_accents_and_braces(raw)
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize_for_similarity(text: str) -> List[str]:
    norm = normalize_title(text)
    if not norm:
        return []
    tokens: List[str] = []
    for part in norm.split():
        if re.search(r"[\u4e00-\u9fff]", part):
            chars = re.findall(r"[\u4e00-\u9fff]", part)
            if len(chars) <= 2:
                tokens.extend(chars)
            else:
                tokens.extend("".join(chars[i : i + 2]) for i in range(len(chars) - 1))
            alnum = re.findall(r"[a-z0-9]+", part)
            tokens.extend(alnum)
        else:
            if len(part) > 1 and part not in STOPWORDS_EN:
                tokens.append(part)
    return tokens


def title_similarity(t1: Optional[str], t2: Optional[str]) -> float:
    n1 = normalize_title(t1)
    n2 = normalize_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    c1 = n1.replace(" ", "")
    c2 = n2.replace(" ", "")
    seq_ratio = max(
        SequenceMatcher(None, n1, n2).ratio(),
        SequenceMatcher(None, c1, c2).ratio(),
    )
    tok1 = set(tokenize_for_similarity(t1 or ""))
    tok2 = set(tokenize_for_similarity(t2 or ""))
    if tok1 and tok2:
        jaccard = len(tok1 & tok2) / len(tok1 | tok2)
        return round(0.55 * seq_ratio + 0.45 * jaccard, 4)
    return round(seq_ratio, 4)


def _is_corporate_author(raw: str) -> bool:
    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        return True
    low = s.lower()
    corp_cues = (
        "organization", "committee", "consortium", "institute", "university",
        "ministry", "association", "society", "group", "team", "standard",
        "委员会", "研究院", "标准", "中心", "部", "局", "学会", "协会", "课题组",
    )
    return any(c in low for c in corp_cues) and ("," not in s)


def parse_author_name(raw: str) -> Dict[str, Any]:
    s = strip_latex_accents_and_braces(raw).strip()
    if not s:
        return {"raw": "", "family": "", "given": "", "initials": "", "initials_compact": "", "is_cjk": False, "surname_key": "", "is_corporate": False}

    is_corp = _is_corporate_author(raw)
    s_clean = s.strip("{}").strip()

    if is_corp:
        return {
            "raw": s_clean,
            "family": s_clean,
            "given": "",
            "initials": "",
            "initials_compact": "",
            "is_cjk": detect_script_lang(s_clean) == "zh",
            "surname_key": normalize_title(s_clean),
            "is_corporate": True,
        }

    if re.search(r"[\u4e00-\u9fff]", s_clean) and not re.search(r"[A-Za-z]{2,}", s_clean):
        compact = re.sub(r"\s+", "", s_clean)
        family = compact[:2] if len(compact) >= 3 and compact[:2] in COMPOUND_ZH_SURNAMES else compact[:1]
        given = compact[len(family):]
        return {
            "raw": compact,
            "family": family,
            "given": given,
            "initials": "",
            "initials_compact": "",
            "is_cjk": True,
            "surname_key": family,
            "is_corporate": False,
        }

    if "," in s_clean:
        parts = [p.strip() for p in s_clean.split(",", 1)]
        family = parts[0]
        given = parts[1] if len(parts) > 1 else ""
    else:
        tokens = s_clean.split()
        if len(tokens) == 1:
            family, given = tokens[0], ""
        else:
            # Handle "SURNAME FM" or "Surname F M" or "Surname F." (trailing initials)
            def _is_initial_tok(tok: str) -> bool:
                cleaned = tok.replace(".", "").replace("-", "")
                return 1 <= len(cleaned) <= 2 and cleaned.isupper()

            if len(tokens[0].replace(".", "")) > 2 and all(_is_initial_tok(t) for t in tokens[1:]):
                family = tokens[0]
                given = " ".join(tokens[1:])
            else:
                idx = len(tokens) - 1
                while idx - 1 >= 1 and tokens[idx - 1].lower() in NAME_PARTICLES:
                    idx -= 1
                family = " ".join(tokens[idx:])
                given = " ".join(tokens[:idx])

    given_clean = re.sub(r"[.]", " ", given)
    given_parts = re.split(r"[\s-]+", given_clean.strip())
    initials_list: List[str] = []
    for gp in given_parts:
        gp = gp.strip()
        if not gp:
            continue
        if gp.isupper() and len(gp) <= 3 and len(given_parts) == 1 and "." not in given and " " not in given.strip():
            initials_list.extend(list(gp))
        else:
            initials_list.append(gp[0].upper())

    family_norm = family.title() if family.isupper() and len(family) > 2 else family
    surname_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", family.lower())
    return {
        "raw": s_clean,
        "family": family_norm,
        "given": given.strip(),
        "initials": " ".join(initials_list),
        "initials_compact": "".join(initials_list),
        "is_cjk": False,
        "surname_key": surname_key,
        "is_corporate": False,
    }


def split_authors_string(raw: Any) -> List[str]:
    def _clean(items: List[str]) -> List[str]:
        return [
            p.strip() for p in items
            if p.strip() and p.strip().lower().rstrip(".") not in ("et al", "等", "and", "others")
        ]

    if raw is None:
        return []
    if isinstance(raw, list):
        out: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                fam = str(item.get("family") or item.get("last") or "").strip()
                giv = str(item.get("given") or item.get("first") or "").strip()
                lit = str(item.get("literal") or item.get("name") or "").strip()
                if fam and giv:
                    out.append(f"{fam}, {giv}")
                elif fam or lit:
                    out.append(fam or lit)
            elif str(item).strip():
                out.extend(split_authors_string(str(item)))
        return _clean(out)

    s = str(raw).strip()
    if not s:
        return []
    if re.search(r"\s+and\s+", s, flags=re.I) and "、" not in s:
        return _clean(re.split(r"\s+and\s+", s, flags=re.I))
    if ";" in s or "；" in s or "、" in s:
        return _clean(re.split(r"[;；、]+", s))
    if "," in s and re.search(r"[\u4e00-\u9fff]", s):
        parts = _clean(s.split(","))
        if all(len(p) <= 6 for p in parts):
            return parts
    if "," in s:
        comma_parts = _clean(s.split(","))
        if len(comma_parts) >= 2:
            if all(" " in p for p in comma_parts):
                return comma_parts
            if len(comma_parts) == 2:
                return [s]
    return _clean([s])


ZH_SURNAME_PINYIN = {
    "王": "wang", "李": "li", "张": "zhang", "刘": "liu", "陈": "chen", "杨": "yang", "黄": "huang",
    "赵": "zhao", "周": "zhou", "吴": "wu", "徐": "xu", "孙": "sun", "胡": "hu", "朱": "zhu",
    "高": "gao", "林": "lin", "何": "he", "郭": "guo", "马": "ma", "罗": "luo", "梁": "liang",
    "宋": "song", "郑": "zheng", "谢": "xie", "韩": "han", "唐": "tang", "冯": "feng", "于": "yu",
    "董": "dong", "萧": "xiao", "程": "cheng", "曹": "cao", "袁": "yuan", "邓": "deng", "许": "xu",
    "傅": "fu", "沈": "shen", "曾": "zeng", "彭": "peng", "吕": "lu", "苏": "su", "卢": "lu",
    "蒋": "jiang", "蔡": "cai", "贾": "jia", "丁": "ding", "魏": "wei", "薛": "xue", "叶": "ye",
    "阎": "yan", "余": "yu", "潘": "pan", "杜": "du", "戴": "dai", "夏": "xia", "钟": "zhong",
    "汪": "wang", "田": "tian", "任": "ren", "姜": "jiang", "范": "fan", "方": "fang", "石": "shi",
    "姚": "yao", "谭": "tan", "廖": "liao", "邹": "zou", "熊": "xiong", "金": "jin", "陆": "lu",
    "郝": "hao", "孔": "kong", "白": "bai", "崔": "cui", "康": "kang", "毛": "mao", "邱": "qiu",
    "秦": "qin", "江": "jiang", "史": "shi", "顾": "gu", "侯": "hou", "邵": "shao", "孟": "meng",
    "龙": "long", "万": "wan", "段": "duan", "雷": "lei", "钱": "qian", "汤": "tang", "尹": "yin",
    "黎": "li", "易": "yi", "常": "chang", "武": "wu", "乔": "qiao", "贺": "he", "赖": "lai",
    "龚": "gong", "文": "wen", "欧阳": "ouyang", "司马": "sima", "上官": "shangguan", "诸葛": "zhuge",
}


def bilingual_surname_compatible(authors1: List[str], authors2: List[str]) -> bool:
    if not authors1 or not authors2:
        return False
    p1 = parse_author_name(authors1[0])
    p2 = parse_author_name(authors2[0])
    if p1["is_cjk"] == p2["is_cjk"]:
        return p1["surname_key"] == p2["surname_key"] and bool(p1["surname_key"])
    zh_p, en_p = (p1, p2) if p1["is_cjk"] else (p2, p1)
    py = ZH_SURNAME_PINYIN.get(zh_p["family"], "")
    return bool(py and py == en_p["surname_key"])


def author_overlap(authors1: List[str], authors2: List[str]) -> float:
    s1 = {parse_author_name(a)["surname_key"] for a in authors1 if a.strip()} - {""}
    s2 = {parse_author_name(a)["surname_key"] for a in authors2 if a.strip()} - {""}
    if not s1 or not s2:
        return 0.0
    return round(len(s1 & s2) / min(len(s1), len(s2)), 4)


def parse_year(raw: Any) -> Tuple[Optional[int], Optional[str]]:
    if raw is None or str(raw).strip() == "":
        return None, None
    s = str(raw).strip()
    m = re.search(r"(\d{4})", s)
    if not m:
        return None, f"unparseable_year:{s}"
    yr = int(m.group(1))
    if yr < 1800 or yr > 2028:
        return yr, f"suspect_out_of_range_year:{yr}"
    return yr, None


def parse_page_range(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {"raw": "", "start": "", "end": "", "start_int": None, "end_int": None, "valid": True, "normalized": ""}
    s = str(raw).strip()
    s = re.sub(r"^(?:pp?\.\s*|第\s*)", "", s, flags=re.I)
    s = re.sub(r"\s*页$", "", s)
    s = s.replace("—", "-").replace("–", "-").replace("--", "-")
    s = re.sub(r"\s*-\s*", "-", s).strip()
    if not s:
        return {"raw": "", "start": "", "end": "", "start_int": None, "end_int": None, "valid": True, "normalized": ""}
    if "-" in s:
        p1, p2 = s.split("-", 1)
        p1, p2 = p1.strip(), p2.strip()
        i1 = int(p1) if p1.isdigit() else None
        i2 = int(p2) if p2.isdigit() else None
        valid = True
        if i1 is not None and i2 is not None and i2 < i1:
            valid = False
        return {"raw": str(raw).strip(), "start": p1, "end": p2, "start_int": i1, "end_int": i2, "valid": valid, "normalized": f"{p1}-{p2}"}
    i1 = int(s) if s.isdigit() else None
    return {"raw": str(raw).strip(), "start": s, "end": "", "start_int": i1, "end_int": i1, "valid": True, "normalized": s}


def page_ranges_conflict(p1: Dict[str, Any], p2: Dict[str, Any]) -> bool:
    s1, e1 = p1.get("start_int"), p1.get("end_int")
    s2, e2 = p2.get("start_int"), p2.get("end_int")
    if s1 is not None and s2 is not None:
        e1_eff = e1 if e1 is not None else s1
        e2_eff = e2 if e2 is not None else s2
        return e1_eff < s2 or e2_eff < s1
    n1, n2 = p1.get("normalized", ""), p2.get("normalized", "")
    if n1 and n2 and n1 != n2:
        return True
    return False


def extract_numbers(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?", text)


def extract_pdf_text_safe(path: Path, max_pages: Optional[int] = None) -> Dict[str, Any]:
    """Extract text page-by-page using PyMuPDF (preferred) or pdftotext CLI."""
    result: Dict[str, Any] = {
        "path": path.name,
        "pages": [],
        "full_text": "",
        "page_count": 0,
        "encrypted": False,
        "is_scanned_empty": False,
        "error": None,
        "extractor": None,
    }
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore

        doc = fitz.open(str(path))
        if getattr(doc, "is_encrypted", False) and not doc.authenticate(""):
            result["encrypted"] = True
            result["error"] = "pdf_encrypted_password_required"
            doc.close()
            return result
        total = len(doc)
        result["page_count"] = total
        limit = min(total, max_pages) if max_pages else total
        pages_out = []
        for idx in range(limit):
            txt = doc[idx].get_text("text") or ""
            pages_out.append({"page_num": idx + 1, "text": txt})
        doc.close()
        result["pages"] = pages_out
        result["full_text"] = "\n\n".join(f"[Page {p['page_num']}]\n{p['text']}" for p in pages_out)
        result["extractor"] = "pymupdf"
        if total > 0 and len("".join(p["text"].strip() for p in pages_out)) < 30:
            result["is_scanned_empty"] = True
        return result
    except ImportError:
        pass
    except Exception as exc:
        result["error"] = f"pymupdf_error:{exc}"

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0:
                txt = proc.stdout or ""
                pages_split = txt.split("\x0c")
                pages_out = [{"page_num": i + 1, "text": p} for i, p in enumerate(pages_split) if p.strip() or i == 0]
                result["pages"] = pages_out
                result["page_count"] = len(pages_out)
                result["full_text"] = txt
                result["extractor"] = "pdftotext"
                if len(txt.strip()) < 30:
                    result["is_scanned_empty"] = True
                return result
            result["error"] = f"pdftotext_rc_{proc.returncode}:{proc.stderr.strip()[:200]}"
            return result
        except Exception as exc:
            result["error"] = f"pdftotext_error:{exc}"
            return result

    if not result["error"]:
        result["error"] = "no_pdf_extractor_available"
    return result


def extract_docx_blocks(path: Path) -> Dict[str, Any]:
    """Extract paragraphs, headings, tables, footnotes, and superscript citations from DOCX."""
    out: Dict[str, Any] = {
        "blocks": [],
        "footnotes": [],
        "error": None,
        "extractor": "zip_xml",
    }
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    W = f"{{{W_NS}}}"

    def _run_text(r_elem: ET.Element) -> str:
        t_parts: List[str] = []
        for child in r_elem:
            if child.tag == f"{W}t":
                if child.text:
                    t_parts.append(child.text)
            elif child.tag == f"{W}tab":
                t_parts.append("\t")
            elif child.tag == f"{W}br":
                t_parts.append("\n")
            elif child.tag == f"{W}footnoteReference":
                fid = child.attrib.get(f"{W}id", "")
                if fid:
                    t_parts.append(f"[fn:{fid}]")
        txt = "".join(t_parts)
        if not txt:
            return ""
        rpr = r_elem.find(f"{W}rPr")
        if rpr is not None:
            va = rpr.find(f"{W}vertAlign")
            if va is not None and va.attrib.get(f"{W}val") == "superscript":
                stripped = txt.strip()
                if stripped and not (stripped.startswith("^") or stripped.startswith("[")):
                    if re.fullmatch(r"[\[\]0-9,\-–—\s;]+", stripped):
                        inner = stripped.strip("[] ")
                        return f"^[{inner}]"
                return f"^[{stripped}]" if stripped else txt
        return txt

    def _para_text(p_elem: ET.Element) -> Tuple[str, str]:
        pstyle = ""
        ppr = p_elem.find(f"{W}pPr")
        if ppr is not None:
            ps = ppr.find(f"{W}pStyle")
            if ps is not None:
                pstyle = ps.attrib.get(f"{W}val", "")
        parts: List[str] = []
        for child in p_elem:
            if child.tag == f"{W}r":
                parts.append(_run_text(child))
            elif child.tag == f"{W}hyperlink":
                for sub in child.findall(f".//{W}r"):
                    parts.append(_run_text(sub))
        return "".join(parts).strip(), pstyle

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                out["error"] = "docx_missing_document_xml"
                return out
            doc_xml = ET.fromstring(zf.read("word/document.xml"))
            body = doc_xml.find(f"{W}body")
            if body is not None:
                for child in body:
                    if child.tag == f"{W}p":
                        txt, pstyle = _para_text(child)
                        if txt:
                            btype = "heading" if "heading" in pstyle.lower() or pstyle.lower().startswith("h") else "paragraph"
                            out["blocks"].append({"kind": btype, "style": pstyle, "text": txt})
                    elif child.tag == f"{W}tbl":
                        rows_text: List[str] = []
                        for tr in child.findall(f"{W}tr"):
                            cells: List[str] = []
                            for tc in tr.findall(f"{W}tc"):
                                cell_ps = [_para_text(p)[0] for p in tc.findall(f"{W}p")]
                                cells.append(" ".join(c for c in cell_ps if c))
                            if any(cells):
                                rows_text.append(" | ".join(cells))
                        if rows_text:
                            out["blocks"].append({"kind": "table", "style": "Table", "text": "\n".join(rows_text)})

            if "word/footnotes.xml" in zf.namelist():
                fn_xml = ET.fromstring(zf.read("word/footnotes.xml"))
                for fn in fn_xml.findall(f"{W}footnote"):
                    fid = fn.attrib.get(f"{W}id", "")
                    f_type = fn.attrib.get(f"{W}type", "")
                    if f_type in ("separator", "continuationSeparator"):
                        continue
                    ps = [_para_text(p)[0] for p in fn.findall(f"{W}p")]
                    ftext = " ".join(p for p in ps if p).strip()
                    if ftext:
                        out["footnotes"].append({"id": fid, "text": ftext})
        return out
    except zipfile.BadZipFile:
        out["error"] = "corrupt_docx_bad_zip"
        return out
    except Exception as exc:
        out["error"] = f"docx_parse_error:{exc}"
        return out
