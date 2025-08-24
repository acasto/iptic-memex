from __future__ import annotations

from typing import Iterable, Optional, Tuple


def copy_contexts(
    src_session,
    dest_session,
    *,
    types: Optional[Iterable[str]] = None,
    include_chat: bool = False,
) -> None:
    """Copy contexts from src to dest.

    - types: filter by context types (e.g., ('file','image','assistant'))
    - include_chat: when True, also copies chat turns (user/assistant) naively
    """
    wanted = set(t.lower() for t in (types or [])) if types else None
    for ctx_type, ctx_list in (src_session.context or {}).items():
        if ctx_type in ('prompt', 'chat'):
            continue
        if wanted and ctx_type not in wanted:
            continue
        for ctx in (ctx_list or []):
            data = ctx.get()
            dest_session.add_context(ctx_type, data)

    if include_chat:
        src = src_session.get_context('chat')
        if not src:
            return
        turns = src.get() or []
        dest = dest_session.get_context('chat') or dest_session.add_context('chat')
        for t in turns:
            try:
                dest.add(t.get('message') or '', t.get('role') or 'user', t.get('context') or [])
            except Exception:
                continue

