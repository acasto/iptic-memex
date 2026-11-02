"""One execution lifecycle for submitted tasks and their tool exchanges.

No frontend, renderer, hooks, agent loop, or workspace coordination lives here.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from base_classes import InteractionNeeded
from core.cancellation import CancellationToken
from core.input_limits import compute_context_tokens, count_text_tokens
from core.runtime.contracts import RunEvent, RunOutcome, RunRequest
from core.runtime.provider import invoke, optional
from core.runtime.interaction import InteractionService
from core.runtime.tools import (action_services, collect_calls, record_result,
                                run_call, take_contexts)


def _record_input(session, request):
    action = session.get_action('process_contexts')
    contexts = list(action.get_contexts(session)) if action else []
    message = request.message
    if request.stdin_as_message:
        for index, context in enumerate(contexts):
            data = context['context'].get()
            if data.get('name') == 'stdin':
                message = data.get('content', '')
                contexts.pop(index)
                break
    chat = session.get_context('chat') or session.add_context('chat')
    if chat is None:
        raise ValueError('Could not create chat context')
    meta = {'id': uuid4().hex, 'index': len(chat.get('all')) + 1,
            'role': 'user', 'kind': 'user'}
    setter = getattr(session, 'set_user_data', None)
    if setter:
        setter('__turn_meta__', meta)
    builder = session.get_action('build_turn_prompt')
    turn_prompt = builder.run(meta) if builder else ''
    if turn_prompt:
        meta['turn_prompt_text'] = turn_prompt
        context = session.create_context('assistant', {'name': 'turn_status', 'content': turn_prompt})
        if context is None:
            raise ValueError('Could not create turn prompt context')
        contexts.append({'type': 'assistant', 'context': context})
    tokens = _input_tokens(session, chat, contexts, message)
    if request.input_limit and request.input_limit > 0 and tokens > request.input_limit:
        raise InputLimitExceeded(tokens, request.input_limit)
    chat.add(message, 'user', contexts, extra={'meta': meta})
    # Ownership moves to the chat turn, retaining original context objects.
    for kind in list(getattr(session, 'context', {})):
        if kind not in ('chat', 'prompt'):
            session.remove_context_type(kind)
    return chat


def _input_tokens(session, chat, contexts=(), message=''):
    prompt = session.get_context('prompt')
    tokens = compute_context_tokens(session, contexts) + count_text_tokens(session, message)
    for turn in chat.get('all'):
        tokens += count_text_tokens(session, turn.get('message', ''))
        if turn.get('role') != 'tool':
            tokens += compute_context_tokens(session, turn.get('context'))
    if prompt:
        tokens += count_text_tokens(session, prompt.get().get('content', ''))
    return tokens


class InputLimitExceeded(ValueError):
    def __init__(self, tokens, limit):
        super().__init__(f'Input size ({tokens} tokens) exceeds configured limit ({limit})')
        self.tokens, self.limit = tokens, limit


def execute(session, request: RunRequest, *, on_event=None, cancellation=None,
            interactions=None, before_run=None, before_call=None, after_run=None) -> RunOutcome:
    """Own a submitted task through bounded provider/tool exchanges.

    Separate sessions can run concurrently. Responses to input can arrive on
    another thread through InteractionService; no frontend drives continuation.
    """
    if request.max_calls < 1:
        raise ValueError('max_calls must be positive')
    if getattr(session, '_closed', False):
        raise RuntimeError('Session is closed')
    lock = getattr(session, '_execution_lock', None)
    if lock is None:
        lock = session._execution_lock = Lock()
    if not lock.acquire(blocking=False):
        raise RuntimeError('Session already has an active run')
    token = cancellation if cancellation is not None else CancellationToken()
    result = RunOutcome(run_id=uuid4().hex)
    session._active_run_id = result.run_id
    observer = on_event
    sequence = 0

    def emit(kind, data):
        nonlocal sequence, observer
        sequence += 1
        event = RunEvent(result.run_id, getattr(session, 'session_uid', None), sequence,
                         datetime.now(timezone.utc).isoformat(), kind, dict(data))
        if kind != 'assistant_delta':
            result.events.append(event)
        if observer:
            try:
                observer(event)
            except KeyboardInterrupt:
                observer = None
                token.cancel('Run cancelled')
            except Exception as exc:
                observer = None
                emit('warning', {'code': 'observer_failed', 'message': str(exc) or type(exc).__name__})

    def fail(status, reason, error):
        result.status, result.stop_reason, result.error = status, reason, error

    def metric(method):
        try:
            return optional(provider, method)
        except Exception as exc:
            emit('warning', {'code': 'metric_unavailable', 'method': method,
                             'message': str(exc) or type(exc).__name__})
            return None

    service = interactions or InteractionService()
    provider = None
    channel = getattr(session, 'runtime_events', None)
    overrides = getattr(getattr(session, 'config', None), 'overrides', None)
    stream_overridden = isinstance(overrides, dict) and 'stream' in overrides
    original_override = overrides.get('stream') if stream_overridden else None
    old_stream = None
    changed_stream = False
    stack = ExitStack()
    try:
        emit('run_started', {})
        if channel:
            stack.enter_context(channel.bind(emit))
        service.bind(emit, token)
        stack.callback(service.unbind)
        stack.enter_context(action_services(session, service))
        session.set_flag('turn_cancelled', False)
        setter = getattr(session, 'set_user_data', None)
        if setter:
            setter('__turn_cancel__', token)
        if token.is_cancelled():
            fail('cancelled', 'cancelled', token.reason() or 'Run cancelled')
        else:
            if before_run:
                before_run(session)
            effective_tools = getattr(session, 'get_effective_tool_mode', None)
            if not request.execute_tools and effective_tools and effective_tools() != 'none':
                raise ValueError('Tool-free execution requires tools to be disabled')
            for kind, value in request.contexts:
                if session.add_context(kind, value) is None:
                    raise ValueError(f'Could not load {kind} context: {value}')
            chat = _record_input(session, request)
            emit('context', {'action': 'prepared'})
            provider = session.get_provider()
            if provider is None:
                raise RuntimeError('No provider available')
            old_stream = session.get_params().get('stream')
            session.set_option('stream', request.stream)
            changed_stream = True
            for index in range(request.max_calls):
                if token.is_cancelled():
                    fail('cancelled', 'cancelled', token.reason() or 'Run cancelled')
                    break
                if before_call:
                    before_call(session, index + 1)
                updates = take_contexts(session)
                if updates:
                    if result.turns == 0:
                        chat.get('all')[-1].setdefault('context', []).extend(updates)
                    else:
                        chat.add('', 'context', updates, extra={'meta': {'kind': 'context_update'}})
                    emit('context', {'action': 'updated', 'count': len(updates)})
                tokens = _input_tokens(session, chat)
                if request.input_limit and request.input_limit > 0 and tokens > request.input_limit:
                    raise InputLimitExceeded(tokens, request.input_limit)
                if token.is_cancelled():
                    fail('cancelled', 'cancelled', token.reason() or 'Run cancelled')
                    break
                call_id = f'{result.run_id}:{index + 1}'
                result.turns += 1
                call_result = invoke(session, request, token, emit, call_id)
                processor = call_result.processor
                result.last_text, result.sanitized = processor.display, processor.sanitized
                fail(call_result.status, call_result.reason, call_result.error)
                result.truncated = result.truncated or call_result.truncated
                calls, native, registry = (collect_calls(session, provider, processor.sanitized)
                                            if request.execute_tools else ([], [], None))
                extra = dict(optional(provider, 'get_assistant_metadata') or {})
                reasoning = optional(provider, 'get_current_reasoning')
                if reasoning:
                    extra['reasoning_content'] = reasoning
                if native:
                    extra['tool_calls'] = native
                chat.add(processor.raw, 'assistant', extra=extra)
                emit('assistant_message', {'call_id': call_id, 'text': result.last_text,
                                           'intermediate': bool(calls)})
                if not calls and call_result.reason in ('tool_calls', 'tool_use'):
                    fail('incomplete', 'unexpected_tool_call', 'Provider requested tools during a tool-free run')
                if not calls:
                    break
                for call in calls:
                    if result.status != 'completed' or token.is_cancelled():
                        record_result(chat, call, {'ok': False, 'skipped': True,
                                                   'error': result.error or 'Run cancelled'})
                        continue
                    try:
                        result.tool_calls += 1
                        payload, contexts = run_call(session, call, registry, service, emit)
                    except InteractionNeeded:
                        fail('needs_input', 'needs_input', 'Run requires input')
                        payload, contexts = {'ok': False, 'needs_input': True}, take_contexts(session)
                    except (KeyboardInterrupt, InterruptedError):
                        token.cancel('Run cancelled')
                        fail('cancelled', 'cancelled', 'Run cancelled')
                        payload, contexts = {'ok': False, 'cancelled': True}, take_contexts(session)
                    result.tool_failures += int(payload.get('ok') is False)
                    record_result(chat, call, payload, contexts)
                if token.is_cancelled():
                    fail('cancelled', 'cancelled', token.reason() or 'Run cancelled')
                if result.status != 'completed':
                    break
                if index + 1 == request.max_calls:
                    fail('incomplete', 'call_limit', 'Provider call limit reached before tool follow-up')
    except InputLimitExceeded as exc:
        fail('failed', 'large_input_limit_exceeded', str(exc))
        emit('error', {'code': result.stop_reason, 'tokens': exc.tokens, 'limit': exc.limit,
                       'message': result.error})
    except InteractionNeeded as exc:
        fail('needs_input', 'needs_input', 'Run requires input')
        request_id = getattr(exc, 'runtime_request_id', exc.state_token)
        if not any(event.type == 'interaction_requested' and event.data.get('id') == request_id
                   for event in result.events):
            emit('interaction_requested', {'id': request_id, 'kind': exc.kind, 'spec': exc.spec})
    except (KeyboardInterrupt, Exception) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt) or token.is_cancelled()
        fail('cancelled' if cancelled else 'failed', 'cancelled' if cancelled else 'error',
             'Run cancelled' if cancelled else str(exc) or type(exc).__name__)
        if cancelled:
            token.cancel(result.error)
        emit('error', {'message': result.error, 'code': result.stop_reason})
    finally:
        try:
            if after_run:
                after_run(session, result)
            if provider is not None:
                result.usage, result.cost = metric('get_usage'), metric('get_cost')
                if request.capture_raw:
                    result.raw = optional(provider, 'get_full_response')
                emit('usage', {'usage': result.usage, 'cost': result.cost})
        except (KeyboardInterrupt, Exception) as exc:
            if isinstance(exc, KeyboardInterrupt) or token.is_cancelled():
                token.cancel('Run cancelled')
                fail('cancelled', 'cancelled', 'Run cancelled')
            else:
                fail('failed', 'finalization_error', str(exc) or type(exc).__name__)
            emit('error', {'message': result.error, 'code': result.stop_reason})
        finally:
            try:
                if changed_stream:
                    session.set_option('stream', old_stream)
                    if isinstance(overrides, dict):
                        if stream_overridden:
                            overrides['stream'] = original_override
                        else:
                            overrides.pop('stream', None)
                emit('run_finished', {'status': result.status, 'stop_reason': result.stop_reason,
                                      'error': result.error, 'truncated': result.truncated})
            finally:
                stack.close()
                session._active_run_id = None
                lock.release()
    return result
