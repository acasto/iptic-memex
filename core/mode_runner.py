from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.turns import TurnRunner, TurnOptions
from core.input_limits import compute_context_tokens, count_text_tokens, check_noninteractive_gate
from core.null_ui import NullUI


@dataclass
class ModeResult:
    last_text: Optional[str]
    raw: Optional[str]
    turns: int
    cost: Optional[Dict[str, Any]]
    usage: Optional[Dict[str, Any]]
    events: List[Dict[str, Any]]


def _attach_contexts(session, contexts: Optional[Iterable[Tuple[str, Any]]]) -> None:
    if not contexts:
        return
    for kind, value in contexts:
        try:
            session.add_context(kind, value)
        except Exception:
            continue


def _build_subsession(
    builder,
    *,
    overrides: Optional[Dict[str, Any]] = None,
):
    # Respect [AGENT].default_model when model is not explicitly provided
    eff_overrides: Dict[str, Any] = dict(overrides or {})

    # Build a fresh session with overrides; attach a NullUI to avoid stdout
    session = builder.build(mode='internal', **eff_overrides)
    try:
        session.ui = NullUI()
    except Exception:
        pass
    return session


def run_completion(
    *,
    builder,
    overrides: Optional[Dict[str, Any]] = None,
    contexts: Optional[Iterable[Tuple[str, Any]]] = None,
    message: str = '',
    capture: str = 'text',  # 'text' | 'raw'
) -> ModeResult:
    """Run a one-shot completion internally using TurnRunner.

    - Builds a subsession from overrides (model/prompt/etc.)
    - Attaches provided contexts
    - Runs a single non-stream assistant turn and returns last_text
    - If capture='raw' and provider exposes get_full_response, include raw
    """
    sess = _build_subsession(builder, overrides=overrides)
    # Disable official tools for single-shot internal completion to save tokens
    try:
        sess.set_option('tool_mode', 'none')
    except Exception:
        pass
    # Mark as non-interactive completion (no MCP autoload to avoid overhead)
    try:
        sess.set_flag('completion_mode', True)
    except Exception:
        pass
    _attach_contexts(sess, contexts)

    # Ensure chat context exists
    if not sess.get_context('chat'):
        sess.add_context('chat')

    # Central non-interactive input gate (contexts + message)
    try:
        proc = sess.get_action('process_contexts')
        ctxs = proc.get_contexts(sess) if proc else []
    except Exception:
        ctxs = []
    total_tokens = compute_context_tokens(sess, ctxs) + count_text_tokens(sess, message or '')
    gate = check_noninteractive_gate(sess, total_tokens)
    if not gate.get('ok', True):
        limit = gate.get('limit')
        tokens = gate.get('tokens')
        # Emit a structured error event for callers
        try:
            sess.ui.emit('error', {
                'code': 'large_input_limit_exceeded',
                'tokens': tokens,
                'limit': limit,
                'mode': 'completion',
            })
        except Exception:
            pass
        # Add assistant context for visibility
        try:
            sess.add_context('assistant', {
                'name': 'large_input_limit_exceeded',
                'content': f'Input size ({tokens} tokens) exceeds non-interactive limit ({limit}). Aborting run.'
            })
        except Exception:
            pass
        # Build result with collected events
        events = []
        try:
            events = list(getattr(sess.ui, 'events', []) or [])
        except Exception:
            events = []
        return ModeResult(last_text='Input exceeds configured limit', raw=None, turns=0, cost=None, usage=None, events=events)

    runner = TurnRunner(sess)
    res = runner.run_user_turn(message or "", options=TurnOptions(stream=False, suppress_context_print=True))

    raw = None
    if capture == 'raw':
        prov = sess.get_provider()
        if prov and hasattr(prov, 'get_full_response'):
            try:
                raw = prov.get_full_response()
            except Exception:
                raw = None

    # Cost/usage (best-effort)
    cost = None
    usage = None
    try:
        prov = sess.get_provider()
        if prov and hasattr(prov, 'get_cost'):
            cost = prov.get_cost()
        if prov and hasattr(prov, 'running_usage'):
            usage = getattr(prov, 'running_usage')
    except Exception:
        pass

    events = []
    try:
        events = list(getattr(sess.ui, 'events', []) or [])
    except Exception:
        events = []

    return ModeResult(last_text=res.last_text, raw=raw, turns=res.turns_executed, cost=cost, usage=usage, events=events)


def run_agent(
    *,
    builder,
    steps: int,
    overrides: Optional[Dict[str, Any]] = None,
    contexts: Optional[Iterable[Tuple[str, Any]]] = None,
    output: Optional[str] = None,  # 'final'|'full'|'none'
    verbose_dump: bool = False,
    outer_session: Any = None,
    chat_seed: Optional[List[dict]] = None,
    disable_hooks: bool = False,
) -> ModeResult:
    """Run an internal Agent loop using TurnRunner.

    Mirrors modes.agent_mode behavior but avoids stdout and returns results.
    """
    sess = _build_subsession(builder, overrides=overrides)
    _attach_contexts(sess, contexts)
    if disable_hooks:
        try:
            sess.set_flag("hooks_disabled", True)
        except Exception:
            pass

    # When called via Session.run_internal_agent, we may have an outer session.
    # Copy its contexts (optionally including chat) so internal runs can see the
    # same working set as the caller (RAG, memories, prior turns, etc.).
    if outer_session is not None:
        try:
            from core.context_transfer import copy_contexts
            copy_contexts(outer_session, sess, include_chat=False)
        except Exception:
            pass
        if chat_seed is None:
            try:
                from core.runner_seed import build_chat_seed
                chat_seed = build_chat_seed(outer_session)
            except Exception:
                chat_seed = None
    if chat_seed:
        try:
            from core.runner_seed import apply_chat_seed
            apply_chat_seed(sess, chat_seed)
        except Exception:
            pass

    if not sess.get_context('chat'):
        sess.add_context('chat')

    # Seed agent mode semantics similar to AgentMode
    try:
        # Write policy via [AGENT] or overrides; default deny
        policy = (sess.get_option('AGENT', 'writes_policy', fallback='deny'))
        sess.enter_agent_mode(policy)
        if output:
            sess.set_option('agent_output_mode', output)
    except Exception:
        pass

    # Autoload MCP now that agent mode is active (non-interactive gating applies inside)
    try:
        from memex_mcp.bootstrap import autoload_mcp
        autoload_mcp(sess)
    except Exception:
        pass

    # Central non-interactive input gate (contexts only for agent)
    try:
        proc = sess.get_action('process_contexts')
        ctxs = proc.get_contexts(sess) if proc else []
    except Exception:
        ctxs = []
    total_tokens = compute_context_tokens(sess, ctxs)
    gate = check_noninteractive_gate(sess, total_tokens)
    if not gate.get('ok', True):
        limit = gate.get('limit')
        tokens = gate.get('tokens')
        # Emit a structured error event for callers
        try:
            sess.ui.emit('error', {
                'code': 'large_input_limit_exceeded',
                'tokens': tokens,
                'limit': limit,
                'mode': 'agent',
            })
        except Exception:
            pass
        # Add assistant context for visibility
        try:
            sess.add_context('assistant', {
                'name': 'large_input_limit_exceeded',
                'content': f'Input size ({tokens} tokens) exceeds non-interactive limit ({limit}). Aborting run.'
            })
        except Exception:
            pass
        events = []
        try:
            events = list(getattr(sess.ui, 'events', []) or [])
        except Exception:
            events = []
        return ModeResult(last_text='Input exceeds configured limit', raw=None, turns=0, cost=None, usage=None, events=events)

    runner = TurnRunner(sess)

    def _prep_prompt(_s, idx, total, has_stdin):
        # Same finish/write-policy injection as AgentMode
        try:
            finish = "Finish signal: When you are done with the task, output the token %%DONE%% as the last line."
            pol = (_s.get_agent_write_policy() or '').lower()
            if pol == 'deny':
                write_instr = (
                    "Write policy: File writes are disabled. Do not modify files. If changes are needed, provide unified diffs (diff -u) showing exact edits."
                )
            elif pol == 'dry-run':
                write_instr = (
                    "Write policy: Dry run. Do not modify files. Provide the unified diffs (diff -u) you would apply."
                )
            else:
                write_instr = None
            extra = "\n\n" + finish + ("\n\n" + write_instr if write_instr else "")
            prompt_ctx = _s.get_context('prompt')
            if has_stdin and not ('prompt' in getattr(_s.config, 'overrides', {})):
                if prompt_ctx:
                    prompt_ctx.get()['content'] = extra.strip()
                else:
                    _s.add_context('prompt', extra.strip())
                return
            if idx == 1:
                if prompt_ctx:
                    content = prompt_ctx.get().get('content', '')
                    prompt_ctx.get()['content'] = (content + extra)
                else:
                    _s.add_context('prompt', extra.strip())
        except Exception:
            pass

    res = runner.run_agent_loop(
        steps,
        prepare_prompt=_prep_prompt,
        options=TurnOptions(
            agent_output_mode=(output or sess.get_option('AGENT', 'output', fallback='final') or 'final'),
            early_stop_no_tools=True,
            verbose_dump=verbose_dump,
        ),
    )

    # Build result (Agent returns last_text in 'final' mode; 'full' already streamed to NullUI events)
    cost = None
    usage = None
    try:
        prov = sess.get_provider()
        if prov and hasattr(prov, 'get_cost'):
            cost = prov.get_cost()
        if prov and hasattr(prov, 'running_usage'):
            usage = getattr(prov, 'running_usage')
    except Exception:
        pass
    events = []
    try:
        events = list(getattr(sess.ui, 'events', []) or [])
    except Exception:
        events = []
    return ModeResult(last_text=res.last_text, raw=None, turns=res.turns_executed, cost=cost, usage=usage, events=events)
