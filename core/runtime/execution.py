"""One tool-free execution lifecycle for completion callers.

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
from core.runtime.response import ResponseProcessor


def _optional(provider, method):
    fn = getattr(provider, method, None)
    return fn() if fn else None


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
    prompt = session.get_context('prompt')
    tokens = compute_context_tokens(session, contexts) + count_text_tokens(session, message)
    for turn in chat.get('all'):
        tokens += count_text_tokens(session, turn.get('message', ''))
        tokens += compute_context_tokens(session, turn.get('context'))
    if prompt:
        tokens += count_text_tokens(session, prompt.get().get('content', ''))
    if request.input_limit and tokens > request.input_limit:
        raise InputLimitExceeded(tokens, request.input_limit)
    chat.add(message, 'user', contexts)
    # Ownership moves to the chat turn, retaining original context objects.
    for kind in list(getattr(session, 'context', {})):
        if kind not in ('chat', 'prompt'):
            session.remove_context_type(kind)
    return chat


class InputLimitExceeded(ValueError):
    def __init__(self, tokens, limit):
        super().__init__(f'Input size ({tokens} tokens) exceeds configured limit ({limit})')
        self.tokens, self.limit = tokens, limit


def execute(session, request: RunRequest, *, on_event=None, cancellation=None) -> RunOutcome:
    """Execute one provider call, emitting ordered events and returning its outcome.

    Cancellation is cooperative. Callers may run this blocking function on their
    own worker thread and cancel the supplied token. Concurrent runs on one
    session are rejected; independent sessions do not share a lock.
    """
    lock = getattr(session, '_execution_lock', None)
    if lock is None:
        lock = session._execution_lock = Lock()
    if not lock.acquire(blocking=False):
        raise RuntimeError('Session already has an active run')
    token = cancellation if cancellation is not None else CancellationToken()
    result = RunOutcome(run_id=uuid4().hex)
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

    processor = None
    provider = None
    stream = None
    chat = None
    old_stream = None
    changed_stream = False
    overrides = getattr(getattr(session, 'config', None), 'overrides', None)
    stream_overridden = isinstance(overrides, dict) and 'stream' in overrides
    original_stream_override = overrides.get('stream') if stream_overridden else None
    channel = getattr(session, 'runtime_events', None)
    stack = ExitStack()

    def metric(method):
        try:
            return _optional(provider, method)
        except Exception as exc:
            emit('warning', {'code': 'metric_unavailable', 'method': method,
                             'message': str(exc) or type(exc).__name__})
            return None

    try:
        emit('run_started', {})
        if channel:
            stack.enter_context(channel.bind(emit))
        session.set_flag('turn_cancelled', False)
        setter = getattr(session, 'set_user_data', None)
        if setter:
            setter('__turn_cancel__', token)
        if token.is_cancelled():
            result.status, result.stop_reason = 'cancelled', 'cancelled'
            result.error = token.reason() or 'Run cancelled'
        else:
            effective_tools = getattr(session, 'get_effective_tool_mode', None)
            if effective_tools and effective_tools() != 'none':
                raise ValueError('Tool-free execution requires tools to be disabled')
            for kind, value in request.contexts:
                if session.add_context(kind, value) is None:
                    raise ValueError(f'Could not load {kind} context: {value}')
            chat = _record_input(session, request)
            emit('context', {'action': 'prepared'})
            if token.is_cancelled():
                raise InterruptedError('Run cancelled')
            provider = session.get_provider()
            if provider is None:
                raise RuntimeError('No provider available')
            processor = ResponseProcessor(session, emit)
            old_stream = session.get_params().get('stream')
            session.set_option('stream', request.stream)
            changed_stream = True
            emit('provider_started', {'call_id': result.run_id + ':1', 'stream': request.stream})
            if token.is_cancelled():
                raise InterruptedError('Run cancelled')
            result.turns = 1
            if request.stream:
                stream = iter(provider.stream_chat())
                while not token.is_cancelled() and not session.get_flag('turn_cancelled'):
                    chunk = next(stream, None)
                    if chunk is None:
                        break
                    if token.is_cancelled() or session.get_flag('turn_cancelled'):
                        break
                    if not isinstance(chunk, str):
                        raise TypeError('Provider stream must yield text')
                    visible = processor.feed(chunk)
                    if visible:
                        emit('assistant_delta', {'text': visible})
            else:
                text = provider.chat()
                if text is not None and not isinstance(text, str):
                    raise TypeError('Provider completion must return text')
                processor.feed(text or '')
            reason = _optional(provider, 'get_finish_reason')
            cancelled = token.is_cancelled() or session.get_flag('turn_cancelled')
            result.stop_reason = reason
            if cancelled or reason == 'cancelled':
                result.status, result.stop_reason = 'cancelled', 'cancelled'
                result.error = token.reason() or 'Run cancelled'
            elif reason in ('length', 'max_tokens', 'pause_turn'):
                result.status = 'incomplete'
                result.truncated = reason in ('length', 'max_tokens')
                result.error = f'Provider stopped: {reason}'
            elif reason in ('error', 'failed', 'content_filter', 'refusal', 'incomplete'):
                result.status = 'failed'
                result.error = processor.raw or f'Provider stopped: {reason}'
            elif reason in ('tool_calls', 'tool_use'):
                result.status, result.stop_reason = 'incomplete', 'unexpected_tool_call'
                result.error = 'Provider requested tools during a tool-free run'
    except InputLimitExceeded as exc:
        result.status, result.stop_reason, result.error = 'failed', 'large_input_limit_exceeded', str(exc)
        emit('error', {'code': result.stop_reason, 'tokens': exc.tokens, 'limit': exc.limit,
                       'message': result.error})
    except InteractionNeeded as exc:
        result.status, result.stop_reason = 'needs_input', 'needs_input'
        result.error = 'Run requires input'
        emit('interaction_requested', {'kind': exc.kind, 'spec': exc.spec})
    except (KeyboardInterrupt, Exception) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt) or token.is_cancelled()
        result.status = 'cancelled' if cancelled else 'failed'
        result.stop_reason = 'cancelled' if cancelled else 'error'
        result.error = 'Run cancelled' if cancelled else str(exc) or type(exc).__name__
        if cancelled:
            token.cancel(result.error)
        emit('error', {'message': result.error, 'code': result.stop_reason})
    finally:
        try:
            if stream is not None and hasattr(stream, 'close'):
                try:
                    stream.close()
                except Exception as exc:
                    emit('warning', {'code': 'stream_close_failed', 'message': str(exc)})
            if changed_stream:
                session.set_option('stream', old_stream)
                if isinstance(overrides, dict):
                    if stream_overridden:
                        overrides['stream'] = original_stream_override
                    else:
                        overrides.pop('stream', None)
            if processor is not None:
                result.last_text, result.sanitized = processor.display, processor.sanitized
                tail = processor.finish()
                if tail and request.stream:
                    emit('assistant_delta', {'text': tail})
                result.last_text, result.sanitized = processor.display, processor.sanitized
                if chat is not None and result.turns:
                    extra = _optional(provider, 'get_assistant_metadata') or {}
                    reasoning = _optional(provider, 'get_current_reasoning')
                    if reasoning:
                        extra = dict(extra, reasoning_content=reasoning)
                    chat.add(processor.raw, 'assistant', extra=extra)
                emit('assistant_message', {'text': result.last_text})
            if provider is not None:
                result.usage = metric('get_usage')
                result.cost = metric('get_cost')
                if request.capture_raw:
                    result.raw = _optional(provider, 'get_full_response')
                emit('provider_finished', {'call_id': result.run_id + ':1',
                                           'status': result.status, 'stop_reason': result.stop_reason})
                emit('usage', {'usage': result.usage, 'cost': result.cost})
        except Exception as exc:
            result.status, result.stop_reason = 'failed', 'finalization_error'
            result.error = str(exc) or type(exc).__name__
            emit('error', {'message': result.error, 'code': result.stop_reason})
        finally:
            try:
                emit('run_finished', {'status': result.status, 'stop_reason': result.stop_reason,
                                      'error': result.error, 'truncated': result.truncated})
            finally:
                stack.close()
                lock.release()
    return result
