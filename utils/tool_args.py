"""
Argument normalization utilities for actions and tools.

Common issues these helpers address:
- Optional fields arriving as empty strings should be treated as "not provided".
- Multi-value fields may appear as a list/tuple or a comma-separated string.
- Boolean-like values may be strings (e.g., "false") and should not be truthy by accident.

Typical usage:
    name = get_str(args, 'name') or 'Untitled'
    top_k = get_int(args, 'k') or 8
    strict = bool(get_bool(args, 'strict', False))
    tags = get_list(args, 'tags') or []
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional


def get_str(args: dict, key: str, default: Optional[str] = None, *, strip: bool = True, empty_as_none: bool = True) -> Optional[str]:
    try:
        val = args.get(key)
    except Exception:
        val = None
    if val is None:
        return default
    try:
        s = str(val)
    except Exception:
        return default
    if strip:
        s = s.strip()
    if empty_as_none and s == "":
        return default
    return s


def get_int(args: dict, key: str, default: Optional[int] = None) -> Optional[int]:
    try:
        val = args.get(key)
    except Exception:
        return default
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return default
    try:
        return int(val)
    except Exception:
        return default


def get_float(args: dict, key: str, default: Optional[float] = None) -> Optional[float]:
    try:
        val = args.get(key)
    except Exception:
        return default
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return default
    try:
        return float(val)
    except Exception:
        return default


def get_bool(args: dict, key: str, default: Optional[bool] = None) -> Optional[bool]:
    try:
        val = args.get(key)
    except Exception:
        return default
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    try:
        s = str(val).strip().lower()
    except Exception:
        return default
    if s in ("true", "1", "yes", "y", "on"):  # permissive
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def get_list(args: dict, key: str, default: Optional[List[str]] = None, *, sep: str = ",", strip_items: bool = True) -> Optional[List[str]]:
    try:
        val = args.get(key)
    except Exception:
        val = None
    if val is None:
        return default
    out: List[str] = []
    if isinstance(val, (list, tuple)):
        for it in val:
            try:
                s = str(it)
                if strip_items:
                    s = s.strip()
                if s != "":
                    out.append(s)
            except Exception:
                continue
    else:
        try:
            s = str(val)
        except Exception:
            return default
        s = s.strip()
        if s == "":
            return default if default is not None else []
        parts = [p.strip() if strip_items else p for p in s.split(sep)]
        out = [p for p in parts if p != ""]
    return out
