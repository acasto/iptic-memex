from __future__ import annotations

from typing import Any, Dict, List, Optional


def _get_token_counter(session):
    try:
        return session.get_action('count_tokens')
    except Exception:
        return None


def count_text_tokens(session, text: Optional[str]) -> int:
    """Best-effort token count for a text snippet using the session counter."""
    if not text:
        return 0
    counter = _get_token_counter(session)
    try:
        return int(counter.count_tiktoken(text)) if counter else 0
    except Exception:
        return 0


def compute_context_tokens(session, contexts: Optional[List[Dict[str, Any]]]) -> int:
    """Compute total tokens across provided contexts (excluding images)."""
    if not contexts:
        return 0
    total = 0
    counter = _get_token_counter(session)
    for c in contexts:
        try:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'image':
                continue
            meta = c.get('context').get() if c.get('context') else None
            if isinstance(meta, dict):
                content = meta.get('content', '')
                if content:
                    try:
                        total += int(counter.count_tiktoken(content)) if counter else 0
                    except Exception:
                        continue
        except Exception:
            continue
    return total


def get_interactive_limit(session) -> Optional[int]:
    """Return interactive (Chat/TUI/Web) large input limit, or None if unset."""
    # Prefer config option; fall back to tools dict for test doubles
    try:
        val = session.get_option('TOOLS', 'large_input_limit', fallback=None)
    except Exception:
        val = None
    if val is None:
        try:
            tools = session.get_tools() or {}
            val = tools.get('large_input_limit')
        except Exception:
            val = None
    try:
        return int(val) if val is not None and str(val).strip() != '' else None
    except Exception:
        return None


def get_interactive_confirm(session) -> Optional[bool]:
    """Return confirm_large_input (interactive), or None if unset."""
    # Prefer config option; fall back to tools dict for test doubles
    try:
        val = session.get_option('TOOLS', 'confirm_large_input', fallback=None)
    except Exception:
        val = None
    if val is None:
        try:
            tools = session.get_tools() or {}
            val = tools.get('confirm_large_input')
        except Exception:
            val = None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('true', '1', 'yes', 'on'):
            return True
        if s in ('false', '0', 'no', 'off'):
            return False
    return None


def get_agent_limit(session) -> Optional[int]:
    """Return non-interactive (Agent/Completion) large input limit, or None if unset."""
    try:
        val = session.get_option('AGENT', 'large_input_limit', fallback=None)
        return int(val) if val is not None and str(val).strip() != '' else None
    except Exception:
        return None


def enforce_interactive_gate(session, total_tokens: int) -> Dict[str, Any]:
    """
    Centralized decision for interactive modes.

    Returns a dict with:
      - action: 'none' | 'disable_auto' | 'feedback'
      - limit: int | None
      - tokens: int
    """
    limit = get_interactive_limit(session)
    if limit is None or limit <= 0 or total_tokens <= limit:
        return {'action': 'none', 'limit': limit, 'tokens': total_tokens}

    confirm = get_interactive_confirm(session)
    if confirm is True:
        return {'action': 'disable_auto', 'limit': limit, 'tokens': total_tokens}
    elif confirm is False:
        return {'action': 'feedback', 'limit': limit, 'tokens': total_tokens}
    # If confirm is None (unset), default to a conservative no-op unless a limit is explicitly desired
    return {'action': 'none', 'limit': limit, 'tokens': total_tokens}


def check_noninteractive_gate(session, total_tokens: int) -> Dict[str, Any]:
    """
    Decision for non-interactive modes (internal completion, agent, completion).

    Returns:
      - ok: bool (False if limit exceeded)
      - limit: int | None
      - tokens: int
      - code: 'large_input_limit_exceeded' when not ok
    """
    limit = get_agent_limit(session)
    if limit is None or limit <= 0:
        return {'ok': True, 'limit': limit, 'tokens': total_tokens}
    if total_tokens > limit:
        return {'ok': False, 'limit': limit, 'tokens': total_tokens, 'code': 'large_input_limit_exceeded'}
    return {'ok': True, 'limit': limit, 'tokens': total_tokens}
