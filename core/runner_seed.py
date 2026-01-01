from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SIMPLE_TYPES = (str, int, float, bool)

# Runner snapshots are ephemeral inputs. Keep params conservative.
_PARAM_ALLOWLIST = {
    # Keep runner snapshots minimal; override explicitly when needed.
    "base_directory",
}


@dataclass
class ExternalAgentResult:
    last_text: Optional[str]
    error: Optional[str] = None


def _safe_meta(meta: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(meta, dict):
        return None
    out: Dict[str, Any] = {}
    for key, value in meta.items():
        if isinstance(value, _SIMPLE_TYPES):
            out[key] = value
    return out or None


def _safe_turn(turn: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(turn, dict):
        return None
    out: Dict[str, Any] = {}
    for key in ("timestamp", "role", "message"):
        if key in turn and turn[key] is not None:
            out[key] = turn[key]
    meta = _safe_meta(turn.get("meta"))
    if meta:
        out["meta"] = meta
    return out or None


def build_chat_seed(session) -> List[Dict[str, Any]]:
    seed: List[Dict[str, Any]] = []
    try:
        chat = session.get_context("chat")
    except Exception:
        chat = None
    turns = []
    if chat:
        try:
            turns = chat.get("all") or []
        except Exception:
            turns = []
    for turn in turns:
        safe = _safe_turn(turn)
        if safe:
            seed.append(safe)
    return seed


def apply_chat_seed(session, seed: Optional[List[Dict[str, Any]]]) -> None:
    if not seed:
        return
    try:
        session.set_user_data("__chat_seed__", seed)
        session.set_flag("use_chat_seed_for_templates", True)
    except Exception:
        pass


def build_runner_snapshot(
    session,
    *,
    overrides: Optional[Dict[str, Any]] = None,
    contexts: Optional[Iterable[Tuple[str, Any]]] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    try:
        current = session.get_params() or {}
    except Exception:
        current = {}
    for key in _PARAM_ALLOWLIST:
        val = current.get(key)
        if isinstance(val, _SIMPLE_TYPES):
            params[key] = val
    if overrides:
        for key, val in overrides.items():
            if isinstance(val, _SIMPLE_TYPES):
                params[key] = val

    ctx_out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for ctx_type, ctx_list in (session.context or {}).items():
            if ctx_type in ("prompt", "chat"):
                continue
            items: List[Dict[str, Any]] = []
            for idx, ctx in enumerate(ctx_list or []):
                try:
                    data = ctx.get()
                except Exception:
                    data = None
                if data is None:
                    continue
                items.append({"id": idx, "data": data})
            if items:
                ctx_out[ctx_type] = items
    except Exception:
        pass

    if contexts:
        for kind, value in contexts:
            try:
                ctx_obj = session.create_context(kind, value)
                data = ctx_obj.get() if ctx_obj else value
            except Exception:
                data = value
            if data is None:
                continue
            items = ctx_out.setdefault(kind, [])
            items.append({"id": len(items), "data": data})

    snapshot = {
        "version": 1,
        "params": params,
        "chat_seed": build_chat_seed(session),
        "contexts": ctx_out,
    }
    # Optional correlation context for external runners. Best-effort only.
    try:
        lg = getattr(getattr(session, "utils", None), "logger", None)
        get_ctx = getattr(lg, "get_context", None)
        ctx = get_ctx() if callable(get_ctx) else {}
        if isinstance(ctx, dict) and ctx.get("trace_id"):
            snapshot["trace"] = {
                "trace_id": ctx.get("trace_id"),
                "parent_span_id": ctx.get("span_id"),
                "outer_session_uid": getattr(session, "session_uid", None),
            }
    except Exception:
        pass
    return snapshot


def snapshot_to_contexts(snapshot: Dict[str, Any]) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    contexts = snapshot.get("contexts") if isinstance(snapshot, dict) else None
    if not isinstance(contexts, dict):
        return out
    for kind, items in contexts.items():
        if not isinstance(items, list):
            continue
        for entry in items:
            if isinstance(entry, dict) and "data" in entry:
                out.append((kind, entry.get("data")))
            else:
                out.append((kind, entry))
    return out
