"""Bounded agent work using the same runtime as chat and completion."""

from contextlib import nullcontext
import json
import re
from uuid import uuid4

from app.headless import build_headless_session
from app.run_helpers import _normalize_trace, child_options, status_tags_enabled
from app.runtime_services import configure_runtime, install_tool_selection
from core.cancellation import CancellationToken
from core.runtime import RunOutcome, RunRequest, execute
from utils.tool_args import get_bool


FINISH = re.compile(r'%%(?:DONE|COMPLETED|COMPLETE)%%', re.IGNORECASE)


def build_agent_session(config_manager, options=None, *, builder=None, default_prompt=False,
                        services=None):
    """Assemble the noninteractive tool profile without UI or implicit hooks."""
    return build_headless_session(config_manager, options, builder=builder,
                                  tools_enabled=True, default_prompt=default_prompt, memory=True,
                                  services=services)


def configure_agent(session, writes_policy=None):
    """Resolve execution services; no in_agent_mode or display flags are required."""
    configure_runtime(session)
    install_tool_selection(session, 'AGENT')
    policy = writes_policy or session.get_option('AGENT', 'writes_policy', fallback='deny')
    session.write_policy = session._normalize_write_policy(policy)
    session.ephemeral_commands = bool(get_bool(
        {'value': session.get_option('AGENT', 'docker_always_ephemeral', fallback=True)}, 'value', True))


def _prepare_prompt(session):
    extra = ('Finish signal: When you are done with the task, '
             'output the token %%DONE%% as the last line.')
    if session.write_policy == 'deny':
        extra += ('\n\nWrite policy: File writes are disabled. Do not modify files. '
                  'Provide unified diffs if changes are needed.')
    elif session.write_policy == 'dry-run':
        extra += ('\n\nWrite policy: Dry run. Do not modify files. '
                  'Provide the unified diffs you would apply.')
    prompt = session.get_context('prompt')
    if prompt is None:
        prompt = session.add_context('prompt', '')
    data = prompt.get()
    content = data.get('content', '')
    previous = session.get_user_data('__agent_prompt__')
    unchanged = (previous and previous['context'] is prompt
                 and previous['rendered'] == content)
    base = previous['base'] if unchanged else content
    rendered = base + ('\n\n' if base else '') + extra
    data['content'] = rendered
    session.set_user_data('__agent_prompt__', {'context': prompt, 'base': base, 'rendered': rendered})


def run_agent(*, builder=None, steps, overrides=None, contexts=None, output=None,
              verbose_dump=False, outer_session=None, chat_seed=None, disable_hooks=False,
              trace=None, session=None, message=None, writes_policy=None, status_tags=None,
              on_event=None, cancellation=None, before_call=None, interactions=None):
    """Run bounded tool work, with isolated helper sessions and shared outcomes.

    on_event observes RunEvents. before_call is an explicit context-update service
    and runs inside the runtime's session lock before every provider invocation.
    """
    steps = int(steps)
    if steps < 1:
        raise ValueError('steps must be positive')
    token = cancellation if cancellation is not None else CancellationToken()
    if outer_session is not None:
        parent = outer_session.get_cancellation_token()
        if parent is not None:
            parent.register_cleanup(lambda: token.cancel(parent.reason() or 'Parent run cancelled'))
        if token.is_cancelled():
            return RunOutcome(run_id=uuid4().hex, status='cancelled', stop_reason='cancelled',
                              error=token.reason() or 'Run cancelled')
    owned = session is None
    if owned:
        if builder is None:
            raise ValueError('A session builder is required for an internal run')
        session = builder.build(mode='internal', **child_options(outer_session, overrides))
    if session is outer_session:
        raise ValueError('Internal runs require a separate session')
    try:
        configure_runtime(session)
        correlation = _normalize_trace(trace)
        if outer_session is not None:
            if not correlation:
                correlation = _normalize_trace(outer_session.utils.logger.get_context())
            correlation['outer_session_uid'] = outer_session.session_uid
        logger = session.utils.logger

        def setup(active):
            configure_agent(active, writes_policy or (overrides or {}).get('agent_writes'))
            if disable_hooks:
                active.set_flag('hooks_disabled', True)
            seed = chat_seed
            if outer_session is not None:
                from core.context_transfer import copy_contexts
                from core.runner_seed import build_chat_seed
                copy_contexts(outer_session, active, include_chat=False)
                if seed is None:
                    seed = build_chat_seed(outer_session)
            if seed:
                from core.runner_seed import apply_chat_seed
                apply_chat_seed(active, seed)
                # Assembly precedes seed transfer. Re-render the original source so
                # system templates, as well as per-turn templates, can see the seed.
                prompt = active.get_context('prompt')
                if prompt is not None and hasattr(active, 'headless_prompt_source'):
                    prompt.process_content(active.headless_prompt_source)
            if correlation:
                logger.update_base_context(correlation)
            from memex_mcp.bootstrap import autoload_mcp
            use_mcp = get_bool({
                'value': active.get_option('AGENT', 'use_mcp', fallback=False),
            }, 'value', False)
            active.mcp_autoload_policy = {
                'enabled': use_mcp,
                'servers': active.get_option('AGENT', 'available_mcp', fallback=''),
            }
            autoload_mcp(active)
            _prepare_prompt(active)

        tags = status_tags if status_tags is not None else status_tags_enabled(session, overrides)
        mode = output or session.get_option('AGENT', 'output', fallback='final') or 'final'
        limit = session.get_option('AGENT', 'large_input_limit', fallback=None)
        limit = int(limit) if limit is not None and str(limit).strip() else None

        def prepare(active, index):
            if boundary := getattr(active, 'before_provider_call', None):
                boundary(active, index)
            if before_call:
                before_call(active, index)
            if tags:
                active.add_context('agent', {
                    'name': 'agent_status', 'content': f'<status>Turn {index} of {steps}</status>',
                })
            if verbose_dump:
                if index == 1:
                    prompt = active.get_context('prompt').get().get('content', '')
                    active.runtime_events.emit('debug', {'message': 'SYSTEM PROMPT\n' + prompt})
                provider = active.get_provider()
                messages = provider.get_messages() if provider and hasattr(provider, 'get_messages') else []
                active.runtime_events.emit('debug', {
                    'message': f'BEFORE TURN {index}\n' +
                               json.dumps(messages, default=str, ensure_ascii=False),
                })

        def finish(active, outcome):
            if outcome.status == 'completed' and outcome.stop_reason in ('stop', 'end_turn', None):
                outcome.stop_reason = 'sentinel' if FINISH.search(outcome.last_text or '') else 'no_tools'
            elif outcome.status == 'incomplete' and outcome.stop_reason == 'call_limit':
                outcome.stop_reason = 'steps'
            if mode != 'full' and outcome.last_text:
                outcome.last_text = FINISH.sub('', outcome.last_text).rstrip()

        # Rendering policy does not control whether the provider streams its response.
        request = RunRequest(message=message or '', stdin_as_message=message is None,
                             contexts=tuple(contexts or ()), execute_tools=True, max_calls=steps,
                             stream=bool(session.get_params().get('stream')), input_limit=limit,
                             capture_raw=bool(session.get_params().get('raw_completion')))
        span = (logger.span('internal_agent', trace_id=correlation['trace_id'],
                            parent_span_id=correlation.get('parent_span_id'))
                if correlation.get('trace_id') else nullcontext())
        with span:
            return execute(session, request, on_event=on_event, cancellation=token,
                           interactions=interactions, before_run=setup,
                           before_call=prepare, after_run=finish)
    finally:
        if owned:
            session.close()
