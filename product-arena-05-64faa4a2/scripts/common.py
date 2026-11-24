#!/usr/bin/env python3
"""Shared utilities for sci-methods-writer: safe multi-format I/O, stdlib fallbacks,
parameter canonicalization, and numeric/unit equivalence checking.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parents[1]
RESOURCES_DIR = SKILL_ROOT / "resources"


def load_resource_json(rel_path: str) -> Dict[str, Any]:
    """Load a JSON resource bundled inside submission/resources/."""
    p = RESOURCES_DIR / rel_path
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_json_safe(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """Read and parse JSON safely without raising."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
        return json.loads(raw), None
    except FileNotFoundError:
        return None, f"file_not_found: {path}"
    except PermissionError as exc:
        return None, f"permission_denied: {path} ({exc})"
    except Exception as exc:
        return None, f"json_parse_error: {path} ({exc})"


def save_json(path: Path, data: Any) -> None:
    """Atomically write UTF-8 JSON with parent directory creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def posix_rel(path: Path, root: Path) -> str:
    """Return a stable POSIX-style relative path string."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def read_text_safe(
    path: Path,
    max_bytes: int = 2_000_000,
    head_bytes: int = 300_000,
    tail_bytes: int = 200_000,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Read text defensively with encoding fallback, binary guard, and huge-file bounding.

    Returns: (text, warning_code_or_none, error_message_or_none)
    """
    try:
        st = path.stat()
    except PermissionError as exc:
        return "", "permission_denied", f"Permission denied reading {path.name}: {exc}"
    except FileNotFoundError:
        return "", "file_not_found", f"File not found: {path}"
    except OSError as exc:
        return "", "os_error", f"OS error inspecting {path.name}: {exc}"

    if st.st_size == 0:
        return "", "empty_file", None

    warning: Optional[str] = None
    try:
        with path.open("rb") as f:
            if st.st_size > max_bytes:
                head = f.read(head_bytes)
                f.seek(max(0, st.st_size - tail_bytes))
                tail = f.read(tail_bytes)
                raw = head + b"\n\n... [TRUNCATED MIDDLE OF LARGE FILE] ...\n\n" + tail
                warning = f"large_file_truncated:{st.st_size}_bytes"
            else:
                raw = f.read()
    except PermissionError as exc:
        return "", "permission_denied", f"Permission denied opening {path.name}: {exc}"
    except OSError as exc:
        return "", "read_error", f"Failed reading {path.name}: {exc}"

    # Binary guard (null bytes in non-UTF16 payload)
    sample = raw[:4096]
    if b"\x00" in sample and not (sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff")):
        return "", "binary_or_corrupt_file", f"Binary null bytes detected in {path.name}; skipped text decoding."

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig" and enc != "utf-8":
                warning = f"non_utf8_decoded:{enc}" if not warning else f"{warning};non_utf8:{enc}"
            return text, warning, None
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement", None


def parse_scalar(raw: str) -> Any:
    """Convert a simple YAML/INI/CLI scalar string to Python int/float/bool/None/str."""
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"[+-]?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", s):
        try:
            return float(s)
        except ValueError:
            pass
    # Inline list like [42, 43, 44]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part) for part in inner.split(",")]
    return s


def strip_inline_comment(val: str) -> str:
    """Strip unquoted '#' comments from YAML/INI value strings."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(val):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or val[idx - 1].isspace():
                return val[:idx].strip()
    return val.strip()


def flatten_dict(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    """Flatten nested dicts into (dotted.key, leaf_value) pairs."""
    items: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_k = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                items.extend(flatten_dict(v, full_k))
            else:
                items.append((full_k, v))
    elif prefix:
        items.append((prefix, obj))
    return items


def find_key_line(lines: List[str], leaf_key: str, start_hint: int = 0) -> int:
    """Find 1-based line number for a key name in YAML/JSON/TOML text."""
    escaped = re.escape(leaf_key)
    pat = re.compile(rf'(?:^|\s|["\']){escaped}["\']?\s*[:=]')
    for idx in range(start_hint, len(lines)):
        if pat.search(lines[idx]):
            return idx + 1
    for idx in range(0, min(start_hint, len(lines))):
        if pat.search(lines[idx]):
            return idx + 1
    return 1


def parse_yaml_with_lines(text: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse YAML into flat entries with 1-based line numbers (PyYAML or stdlib fallback)."""
    lines = text.splitlines()
    entries: List[Dict[str, Any]] = []
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            cursor = 0
            for dotted, val in flatten_dict(data):
                leaf = dotted.split(".")[-1]
                ln = find_key_line(lines, leaf, cursor)
                cursor = max(cursor, ln - 1)
                raw_line = lines[ln - 1].strip() if 1 <= ln <= len(lines) else f"{leaf}: {val}"
                entries.append({"key_path": dotted, "leaf_key": leaf, "value": val, "line": ln, "raw_line": raw_line})
            return entries, None
    except ImportError:
        pass
    except Exception as exc:
        # Fall back to line parser if YAML has minor syntax quirks
        fallback_entries = _parse_yaml_fallback(lines)
        if fallback_entries:
            return fallback_entries, f"yaml_fallback_used: {exc}"
        return [], f"yaml_parse_error: {exc}"

    return _parse_yaml_fallback(lines), None


def _parse_yaml_fallback(lines: List[str]) -> List[Dict[str, Any]]:
    """Pure-stdlib indentation-aware YAML key:value parser."""
    stack: List[Tuple[int, str]] = []
    entries: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            continue
        if stripped.startswith("- "):
            continue
        m = re.match(r"^(\s*)([A-Za-z0-9_.\-/]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        indent = len(m.group(1).replace("\t", "  "))
        key = m.group(2).strip()
        val_str = strip_inline_comment(m.group(3))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not val_str:
            stack.append((indent, key))
        else:
            full_key = ".".join([k for _, k in stack] + [key])
            entries.append(
                {
                    "key_path": full_key,
                    "leaf_key": key,
                    "value": parse_scalar(val_str),
                    "line": idx,
                    "raw_line": stripped,
                }
            )
    return entries


def parse_json_with_lines(text: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse JSON config into flattened key entries with line numbers."""
    lines = text.splitlines()
    try:
        data = json.loads(text)
    except Exception as exc:
        return [], f"json_parse_error: {exc}"
    if not isinstance(data, dict):
        return [], None
    entries: List[Dict[str, Any]] = []
    cursor = 0
    for dotted, val in flatten_dict(data):
        leaf = dotted.split(".")[-1]
        ln = find_key_line(lines, leaf, cursor)
        cursor = max(cursor, ln - 1)
        raw_line = lines[ln - 1].strip() if 1 <= ln <= len(lines) else f"{leaf}: {val}"
        entries.append({"key_path": dotted, "leaf_key": leaf, "value": val, "line": ln, "raw_line": raw_line})
    return entries, None


def parse_toml_or_ini_with_lines(text: str, is_toml: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse TOML (stdlib tomllib) or INI/.env key=value lines with line numbers."""
    lines = text.splitlines()
    if is_toml:
        try:
            import tomllib  # Python 3.11+

            data = tomllib.loads(text)
            entries: List[Dict[str, Any]] = []
            cursor = 0
            for dotted, val in flatten_dict(data):
                leaf = dotted.split(".")[-1]
                ln = find_key_line(lines, leaf, cursor)
                cursor = max(cursor, ln - 1)
                raw_line = lines[ln - 1].strip() if 1 <= ln <= len(lines) else f"{leaf} = {val}"
                entries.append({"key_path": dotted, "leaf_key": leaf, "value": val, "line": ln, "raw_line": raw_line})
            return entries, None
        except Exception:
            pass

    section = ""
    entries = []
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        sec_m = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_m:
            section = sec_m.group(1).strip()
            continue
        kv = re.match(r"^(?:export\s+)?([A-Za-z0-9_.\-]+)\s*[:=]\s*(.+)$", stripped)
        if kv:
            k = kv.group(1).strip()
            v = parse_scalar(strip_inline_comment(kv.group(2)))
            full_k = f"{section}.{k}" if section else k
            entries.append({"key_path": full_k, "leaf_key": k, "value": v, "line": idx, "raw_line": stripped})
    return entries, None


def parse_docx_paragraphs(path: Path) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Extract paragraphs and tables from DOCX (python-docx or stdlib zipfile+XML fallback)."""
    blocks: List[Dict[str, Any]] = []
    try:
        import docx  # type: ignore

        doc = docx.Document(str(path))
        idx = 1
        for p in doc.paragraphs:
            t = p.text.strip()
            if t:
                blocks.append({"block_id": f"p{idx:03d}", "kind": "paragraph", "text": t})
                idx += 1
        for t_idx, tbl in enumerate(doc.tables, start=1):
            rows = []
            for row in tbl.rows:
                rows.append(" | ".join(c.text.strip().replace("\n", " ") for c in row.cells))
            if rows:
                blocks.append({"block_id": f"t{t_idx:02d}", "kind": "table", "text": "\n".join(rows)})
        return blocks, None
    except ImportError:
        pass
    except Exception as exc:
        return [], f"docx_parse_error: {exc}"

    # Pure-stdlib DOCX XML fallback
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        idx = 1
        for p in root.findall(".//w:p", ns):
            texts = [el.text for el in p.findall(".//w:t", ns) if el.text]
            joined = "".join(texts).strip()
            if joined:
                blocks.append({"block_id": f"p{idx:03d}", "kind": "paragraph", "text": joined})
                idx += 1
        return blocks, None
    except Exception as exc:
        return [], f"docx_corrupt_or_unreadable: {exc}"


def parse_pdf_pages(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract per-page text from PDF (PyMuPDF preferred, pdftotext CLI fallback)."""
    warnings: List[str] = []
    pages: List[Dict[str, Any]] = []

    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(str(path))
        if doc.is_encrypted and doc.needs_pass:
            doc.close()
            return [], [f"pdf_encrypted_requires_password:{path.name}"]
        for pno in range(len(doc)):
            page = doc[pno]
            txt = (page.get_text("text") or "").strip()
            status = "ok"
            if len(txt) < 30:
                status = "scanned_or_low_text"
                warnings.append(f"pdf_low_text_page:{path.name}:p{pno + 1}")
            pages.append({"page": pno + 1, "status": status, "text": txt})
        doc.close()
        return pages, warnings
    except ImportError:
        pass
    except Exception as exc:
        warnings.append(f"pymupdf_error:{path.name}:{exc}")

    # pdftotext fallback
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            res = subprocess.run([pdftotext, str(path), "-"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=25)
            if res.returncode == 0 and res.stdout.strip():
                raw_pages = res.stdout.split("\f")
                for pno, txt in enumerate(raw_pages, start=1):
                    t = txt.strip()
                    if not t and pno == len(raw_pages):
                        continue
                    status = "ok" if len(t) >= 30 else "scanned_or_low_text"
                    pages.append({"page": pno, "status": status, "text": t})
                return pages, warnings
            warnings.append(f"pdftotext_failed:{path.name}:{res.stderr.strip()[:120]}")
        except Exception as exc:
            warnings.append(f"pdftotext_exception:{path.name}:{exc}")
    else:
        warnings.append(f"pdf_backend_unavailable:{path.name}")

    return pages, warnings


def build_alias_lookup(alias_cfg: Dict[str, Any]) -> Dict[str, str]:
    """Invert canonical_aliases dict into lower-case alias -> canonical_key."""
    lookup: Dict[str, str] = {}
    for canonical, aliases in (alias_cfg.get("canonical_aliases") or {}).items():
        lookup[canonical.lower()] = canonical
        for a in aliases:
            lookup[str(a).lower()] = canonical
    return lookup


def canonicalize_param_key(raw_key: str, alias_lookup: Dict[str, str]) -> str:
    """Normalize dotted/dashed/camelCase parameter names to a canonical parameter key."""
    if not raw_key:
        return ""
    leaf = raw_key.split(".")[-1].strip().lstrip("-")
    # camelCase -> snake_case
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", leaf).lower()
    snake = re.sub(r"[\s\-]+", "_", snake).strip("_")
    if snake in alias_lookup:
        return alias_lookup[snake]
    if leaf.lower() in alias_lookup:
        return alias_lookup[leaf.lower()]
    # Check full path suffix if compound (e.g., optimizer.lr -> lr)
    for part in reversed(re.split(r"[./:]", raw_key.lower())):
        cleaned = re.sub(r"[\s\-]+", "_", part.lstrip("-")).strip("_")
        if cleaned in alias_lookup:
            return alias_lookup[cleaned]
    return snake


SCI_UNICODE_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:[×xX*]\s*10\s*\^?\s*\{?\s*([+-]?\d+)\s*\}?)\s*(.*)$"
)
SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
NUM_UNIT_RE = re.compile(
    r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.*)$"
)


def parse_numeric_and_unit(raw: Any) -> Tuple[Optional[float], str]:
    """Parse scalar or 'number unit' strings (including 3×10⁻⁴, 3e-4, 80%, 500 °C)."""
    if isinstance(raw, bool) or raw is None:
        return None, ""
    if isinstance(raw, (int, float)):
        return float(raw), ""
    s = str(raw).strip().translate(SUPERSCRIPT_MAP)
    if not s:
        return None, ""

    m_sci = SCI_UNICODE_RE.match(s)
    if m_sci:
        try:
            val = float(m_sci.group(1)) * (10.0 ** int(m_sci.group(2)))
            return val, m_sci.group(3).strip()
        except ValueError:
            pass

    m = NUM_UNIT_RE.match(s)
    if m:
        num_s = m.group(1).replace(",", "")
        try:
            val = float(num_s)
            unit = m.group(2).strip().rstrip(".,;)")
            return val, unit
        except ValueError:
            pass
    return None, ""


def normalize_unit_value(
    val: float,
    unit: str,
    unit_conversions: Dict[str, Dict[str, float]],
) -> Tuple[float, str]:
    """Convert value + unit to canonical SI dimension when recognized."""
    u = (unit or "").strip().replace("℃", "°C").replace("° C", "°C")
    if u in ("°C", "C", "degC"):
        return val + 273.15, "temp_k"
    if u in ("K", "kelvin"):
        return val, "temp_k"
    if u in ("%", "percent", "pct"):
        return val / 100.0, "ratio"
    u_low = u.lower()
    for dim, table in (unit_conversions or {}).items():
        if u in table:
            return val * float(table[u]), dim
        if u_low in table:
            return val * float(table[u_low]), dim
    return val, u_low


def values_equivalent(
    val_a: Any,
    unit_a: str,
    val_b: Any,
    unit_b: str,
    unit_conversions: Dict[str, Dict[str, float]],
    rel_tol: float = 1e-3,
) -> bool:
    """Check whether two parameter values (with optional units) represent the same quantity."""
    # Exact string/bool comparison first
    if isinstance(val_a, bool) or isinstance(val_b, bool):
        return str(val_a).strip().lower() == str(val_b).strip().lower()

    na, ua_in = parse_numeric_and_unit(val_a)
    nb, ub_in = parse_numeric_and_unit(val_b)
    ua = (unit_a or ua_in or "").strip()
    ub = (unit_b or ub_in or "").strip()

    if na is not None and nb is not None:
        # Direct numerical match if units match or one unit omitted
        if ua.lower() == ub.lower() or not ua or not ub:
            if math.isclose(na, nb, rel_tol=rel_tol, abs_tol=1e-9):
                return True
        # Unit-converted match (e.g., 30 min == 1800 s, 80% == 0.8, 500 °C == 773.15 K)
        ca, da = normalize_unit_value(na, ua, unit_conversions)
        cb, db = normalize_unit_value(nb, ub, unit_conversions)
        if da == db and math.isclose(ca, cb, rel_tol=rel_tol, abs_tol=1e-6):
            return True
        # Percent vs bare decimal (e.g., "80%" vs 0.80)
        if (da == "ratio" and not ub) or (db == "ratio" and not ua):
            if math.isclose(ca, cb, rel_tol=rel_tol, abs_tol=1e-6):
                return True
        return False

    sa = re.sub(r"\s+", " ", str(val_a).strip()).lower()
    sb = re.sub(r"\s+", " ", str(val_b).strip()).lower()
    return sa == sb
