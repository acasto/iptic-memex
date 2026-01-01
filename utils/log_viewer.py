from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional


def _app_root() -> str:
    # Same root assumption used by ConfigManager/LoggingHandler: repo root where main.py lives.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_log_dir(cfg) -> str:
    raw = None
    try:
        raw = cfg.get("LOG", "dir", fallback="logs")
    except Exception:
        raw = "logs"
    raw = os.path.expanduser(str(raw or "logs"))
    return raw if os.path.isabs(raw) else os.path.join(_app_root(), raw)


def resolve_log_path(cfg) -> str:
    """Resolve the configured log file path from ConfigParser-like cfg."""
    log_dir = _resolve_log_dir(cfg)
    file_val = None
    try:
        file_val = cfg.get("LOG", "file", fallback="memex.log")
    except Exception:
        file_val = "memex.log"
    file_val = os.path.expanduser(str(file_val or "memex.log"))
    return file_val if os.path.isabs(file_val) else os.path.join(log_dir, file_val)


def list_log_files(base_path: str) -> List[str]:
    """List base log file and rotated siblings, sorted by mtime ascending."""
    base_path = os.path.expanduser(str(base_path))
    log_dir = os.path.dirname(base_path) or "."
    base_name = os.path.basename(base_path)
    root, ext = os.path.splitext(base_name)

    files: List[str] = []
    try:
        for name in os.listdir(log_dir):
            if name == base_name:
                files.append(os.path.join(log_dir, name))
                continue
            if ext and not name.endswith(ext):
                continue
            if name.startswith(root + ".") or name.startswith(root + "-"):
                files.append(os.path.join(log_dir, name))
    except Exception:
        # Fall back to base file only.
        return [base_path]

    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0.0

    files = sorted(set(files), key=_mtime)
    if base_path not in files:
        files.append(base_path)
    return files


@dataclass
class ParsedLine:
    raw: str
    payload: Optional[Dict[str, Any]]


def _parse_jsonl_line(line: str) -> ParsedLine:
    raw = line.rstrip("\n")
    if not raw.strip():
        return ParsedLine(raw=raw, payload=None)
    try:
        obj = json.loads(raw)
    except Exception:
        return ParsedLine(raw=raw, payload=None)
    if not isinstance(obj, dict):
        return ParsedLine(raw=raw, payload=None)
    return ParsedLine(raw=raw, payload=obj)


def iter_log_lines(paths: Iterable[str]) -> Iterator[ParsedLine]:
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    pl = _parse_jsonl_line(line)
                    if pl.raw.strip():
                        yield pl
        except FileNotFoundError:
            continue
        except Exception:
            continue


def _match(payload: Dict[str, Any], *, where: Dict[str, str]) -> bool:
    if not where:
        return True

    ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    for key, expected in where.items():
        # Support "ctx.foo" and "data.foo"; otherwise try ctx then data then top-level.
        if key.startswith("ctx."):
            val = ctx.get(key[4:])
        elif key.startswith("data."):
            val = data.get(key[5:])
        else:
            val = ctx.get(key)
            if val is None:
                val = data.get(key)
            if val is None:
                val = payload.get(key)
        if val is None:
            return False
        if str(val) != expected:
            return False
    return True


def _format_line(payload: Dict[str, Any]) -> str:
    ts = payload.get("ts") or ""
    event = payload.get("event") or ""
    comp = payload.get("component") or ""
    asp = payload.get("aspect") or ""
    sev = payload.get("severity") or ""
    ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    trace_id = (ctx.get("trace_id") or "")[:8]
    span_kind = ctx.get("span_kind") or ""
    span_id = (ctx.get("span_id") or "")[:8]
    hook = ctx.get("hook_name") or ""
    tool = ctx.get("tool_call_id") or ""
    model = data.get("model") or ""
    provider = data.get("provider") or ""
    resp_id = data.get("response_id") or data.get("request_id") or ""

    extras = []
    if model:
        extras.append(f"model={model}")
    if provider:
        extras.append(f"provider={provider}")
    if hook:
        extras.append(f"hook={hook}")
    if tool:
        extras.append(f"tool={tool}")
    if resp_id:
        extras.append(f"rid={resp_id}")

    base = f"{ts} {sev} {asp}:{event} {comp}"
    corr = f" trace={trace_id} span={span_kind}:{span_id}" if (trace_id or span_id) else ""
    extra_s = (" " + " ".join(extras)) if extras else ""
    return base + corr + extra_s


def tail_events(
    *,
    base_path: str,
    lines: int = 50,
    where: Optional[Dict[str, str]] = None,
    json_output: bool = False,
) -> List[str]:
    dq: deque[str] = deque(maxlen=max(1, int(lines or 50)))
    paths = list_log_files(base_path)
    for pl in iter_log_lines(paths):
        if pl.payload is None:
            continue
        if not _match(pl.payload, where=where or {}):
            continue
        dq.append(pl.raw if json_output else _format_line(pl.payload))
    return list(dq)


def show_events(
    *,
    base_path: str,
    limit: int = 200,
    where: Optional[Dict[str, str]] = None,
    json_output: bool = False,
) -> List[str]:
    out: List[str] = []
    paths = list_log_files(base_path)
    for pl in iter_log_lines(paths):
        if pl.payload is None:
            continue
        if not _match(pl.payload, where=where or {}):
            continue
        out.append(pl.raw if json_output else _format_line(pl.payload))
        if limit and len(out) >= int(limit):
            break
    return out

