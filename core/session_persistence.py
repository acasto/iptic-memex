from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional


_SAFE_SIMPLE = (str, int, float, bool)

_PARAM_ALLOWLIST = {
    "context_sent",
    "model",
    "prompt",
    "tool_mode",
    "base_directory",
    "active_tools_agent",
    "use_mcp",
    "available_mcp",
    "agent_output_mode",
    "agent_debug",
    "temperature",
    "max_tokens",
    "stream",
}


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, _SAFE_SIMPLE):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(v) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                continue
            out[k] = _safe_json_value(v)
        return out
    try:
        json.dumps(value)
        return value
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _extract_turn_context(session, turn: dict) -> List[dict]:
    items: List[dict] = []
    ctx_items = turn.get("context") or []
    if not isinstance(ctx_items, list):
        return items
    for idx, entry in enumerate(ctx_items):
        if not isinstance(entry, dict):
            continue
        ctx_type = entry.get("type")
        ctx_obj = entry.get("context")
        if not ctx_type or not ctx_obj:
            continue
        try:
            data = ctx_obj.get() if hasattr(ctx_obj, "get") else None
        except Exception:
            data = None
        if data is None:
            continue
        items.append(
            {
                "type": str(ctx_type),
                "idx": idx,
                "data": _safe_json_value(data),
            }
        )
    return items


def _serialize_turn(session, turn: dict) -> dict:
    out: Dict[str, Any] = {}
    for key, value in turn.items():
        if key == "context":
            continue
        out[key] = _safe_json_value(value)
    ctx = _extract_turn_context(session, turn)
    if ctx:
        out["context"] = ctx
    return out


def _serialize_contexts(session) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for ctx_type, ctx_list in (session.context or {}).items():
        if ctx_type in ("chat", "prompt"):
            continue
        items: List[dict] = []
        for idx, ctx in enumerate(ctx_list or []):
            try:
                data = ctx.get() if hasattr(ctx, "get") else None
            except Exception:
                data = None
            if data is None:
                continue
            items.append({"id": idx, "data": _safe_json_value(data)})
        if items:
            out[str(ctx_type)] = items
    return out


def _select_params(session) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    try:
        current = session.get_params() or {}
    except Exception:
        current = {}
    for key in _PARAM_ALLOWLIST:
        val = current.get(key)
        if isinstance(val, _SAFE_SIMPLE):
            params[key] = val
    return params


def generate_session_id(prefix: str = "sess") -> str:
    try:
        token = uuid.uuid4().hex[:8]
    except Exception:
        token = "00000000"
    return f"{prefix}_{int(_now_ts())}_{token}"


def serialize_session(session, *, kind: str = "session", title: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        chat = session.get_context("chat")
        turns = chat.get("all") if chat else []
    except Exception:
        turns = []

    sid = session_id or getattr(session, "session_uid", None) or generate_session_id("sess")
    created = _now_ts()
    updated = _now_ts()

    payload = {
        "version": 1,
        "id": sid,
        "kind": kind,
        "created": created,
        "updated": updated,
        "title": title or "",
        "params": _select_params(session),
        "chat": [_serialize_turn(session, t) for t in (turns or []) if isinstance(t, dict)],
        "contexts": _serialize_contexts(session),
    }
    return payload


def _resolve_sessions_dir(session, override: Optional[str] = None) -> str:
    if override:
        return os.path.expanduser(override)
    try:
        raw = session.get_option("SESSIONS", "session_directory", fallback="~/.config/iptic-memex/sessions")
        if isinstance(raw, str):
            return os.path.expanduser(raw)
    except Exception:
        pass
    return os.path.expanduser("~/.config/iptic-memex/sessions")


def save_session(session, *, kind: str = "session", title: Optional[str] = None, session_id: Optional[str] = None, directory: Optional[str] = None) -> str:
    sid = session_id or getattr(session, "session_uid", None)
    if not sid:
        sid = generate_session_id("sess" if kind == "session" else "ckpt")
    if kind == "checkpoint":
        sid = generate_session_id("ckpt")

    data = serialize_session(session, kind=kind, title=title, session_id=sid)
    out_dir = _resolve_sessions_dir(session, directory)
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{sid}.ims.json"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def prune_sessions(session, *, kind: str = "session", limit: Optional[int] = None, directory: Optional[str] = None) -> int:
    if limit is None:
        try:
            key = "session_autosave_limit" if kind == "session" else "session_checkpoint_limit"
            raw = session.get_option("SESSIONS", key, fallback=None)
            if isinstance(raw, int):
                limit = raw
            elif isinstance(raw, str) and raw.strip().isdigit():
                limit = int(raw.strip())
        except Exception:
            limit = None
    if not limit or limit <= 0:
        return 0
    items = list_sessions(session, directory=directory)
    filtered = [it for it in items if (it.get("kind") or "session") == kind]
    if len(filtered) <= limit:
        return 0
    removed = 0
    for it in filtered[limit:]:
        path = it.get("path")
        if not path:
            continue
        try:
            os.remove(path)
            removed += 1
        except Exception:
            continue
    return removed


def load_session_data(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sessions(session, directory: Optional[str] = None) -> List[Dict[str, Any]]:
    out_dir = _resolve_sessions_dir(session, directory)
    if not os.path.isdir(out_dir):
        return []
    items: List[Dict[str, Any]] = []
    for fname in os.listdir(out_dir):
        if not fname.endswith(".ims.json"):
            continue
        path = os.path.join(out_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = 0.0
        items.append(
            {
                "id": data.get("id") or fname,
                "kind": data.get("kind") or "session",
                "title": data.get("title") or "",
                "path": path,
                "mtime": mtime,
            }
        )
    items.sort(key=lambda x: (x.get("mtime") or 0.0, x.get("id") or ""), reverse=True)
    return items


def _build_turn_context(session, ctx_items: Iterable[dict]) -> List[dict]:
    out: List[dict] = []
    for idx, entry in enumerate(ctx_items or []):
        if not isinstance(entry, dict):
            continue
        ctx_type = entry.get("type")
        data = entry.get("data")
        if not ctx_type:
            continue
        try:
            ctx_obj = session.create_context(ctx_type, data)
        except Exception:
            ctx_obj = None
        if not ctx_obj:
            continue
        out.append({"type": ctx_type, "idx": idx, "context": ctx_obj})
    return out


def apply_session_data(session, data: Dict[str, Any], *, fork: bool = False) -> None:
    if not isinstance(data, dict):
        return

    session_id = data.get("id")
    if fork or not session_id:
        session_id = generate_session_id("sess")
    try:
        session.session_uid = session_id
        session.set_user_data("session_uid", session_id)
    except Exception:
        pass

    # Apply params (best-effort, safe subset only)
    params = data.get("params")
    if isinstance(params, dict):
        for key, value in params.items():
            if key in _PARAM_ALLOWLIST:
                try:
                    session.set_option(key, value)
                except Exception:
                    pass

    # Clear non-chat/prompt contexts and rebuild session-level contexts
    try:
        for ctx_type in list(session.context.keys()):
            if ctx_type not in ("chat", "prompt"):
                session.remove_context_type(ctx_type)
    except Exception:
        pass

    contexts = data.get("contexts")
    if isinstance(contexts, dict):
        for ctx_type, items in contexts.items():
            if ctx_type in ("chat", "prompt"):
                continue
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                data_val = entry.get("data")
                try:
                    session.add_context(ctx_type, data_val)
                except Exception:
                    continue

    # Rebuild chat
    try:
        chat = session.get_context("chat") or session.add_context("chat")
    except Exception:
        chat = None
    if chat:
        turns_out: List[dict] = []
        turns = data.get("chat")
        if isinstance(turns, list):
            for idx, t in enumerate(turns):
                if not isinstance(t, dict):
                    continue
                ctx_items = _build_turn_context(session, t.get("context") or [])
                t_copy = dict(t)
                t_copy["context"] = ctx_items if ctx_items else None
                # Ensure meta includes id/index
                meta = t_copy.get("meta")
                if not isinstance(meta, dict):
                    meta = {}
                if "id" not in meta:
                    try:
                        meta["id"] = chat._short_id(idx + 1)
                    except Exception:
                        meta["id"] = str(idx + 1)
                if "index" not in meta:
                    meta["index"] = idx + 1
                t_copy["meta"] = meta
                turns_out.append(t_copy)
        try:
            chat.conversation = turns_out
        except Exception:
            pass
