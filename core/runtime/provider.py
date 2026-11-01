"""Normalize one provider invocation, retaining partial output and native state."""

from dataclasses import dataclass

from base_classes import InteractionNeeded
from core.runtime.response import ResponseProcessor


def optional(provider, method):
    fn = getattr(provider, method, None)
    return fn() if fn else None


@dataclass
class ProviderResult:
    processor: ResponseProcessor
    status: str = 'completed'
    reason: str | None = None
    error: str | None = None
    truncated: bool = False


def invoke(session, request, token, emit, call_id):
    """Consume streamed/nonstreamed text without rendering or executing tools."""
    provider = session.get_provider()
    result = ProviderResult(ResponseProcessor(session, emit))
    stream = None
    emit('provider_started', {'call_id': call_id, 'stream': request.stream})
    try:
        if token.is_cancelled():
            raise InterruptedError('Run cancelled')
        if request.stream:
            stream = iter(provider.stream_chat())
            while not token.is_cancelled() and not session.get_flag('turn_cancelled'):
                text = next(stream, None)
                if text is None:
                    break
                if token.is_cancelled() or session.get_flag('turn_cancelled'):
                    break
                if not isinstance(text, str):
                    raise TypeError('Provider stream must yield text')
                visible = result.processor.feed(text)
                if visible:
                    emit('assistant_delta', {'call_id': call_id, 'text': visible})
        else:
            text = provider.chat()
            if text is not None and not isinstance(text, str):
                raise TypeError('Provider completion must return text')
            result.processor.feed(text or '')
        result.reason = optional(provider, 'get_finish_reason')
        if token.is_cancelled() or session.get_flag('turn_cancelled') or result.reason == 'cancelled':
            result.status, result.reason, result.error = 'cancelled', 'cancelled', 'Run cancelled'
        elif result.reason in ('length', 'max_tokens', 'pause_turn'):
            result.status = 'incomplete'
            result.truncated = result.reason in ('length', 'max_tokens')
            result.error = f'Provider stopped: {result.reason}'
        elif result.reason in ('error', 'failed', 'content_filter', 'refusal', 'incomplete'):
            result.status = 'failed'
            result.error = result.processor.raw or f'Provider stopped: {result.reason}'
    except InteractionNeeded:
        result.status, result.reason, result.error = 'needs_input', 'needs_input', 'Run requires input'
        raise
    except (KeyboardInterrupt, Exception) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt) or token.is_cancelled()
        result.status, result.reason = ('cancelled', 'cancelled') if cancelled else ('failed', 'error')
        result.error = 'Run cancelled' if cancelled else str(exc) or type(exc).__name__
        if cancelled:
            token.cancel(result.error)
    finally:
        if stream is not None and hasattr(stream, 'close'):
            try:
                stream.close()
            except Exception as exc:
                emit('warning', {'code': 'stream_close_failed', 'message': str(exc)})
        tail = result.processor.finish()
        if tail and request.stream:
            emit('assistant_delta', {'call_id': call_id, 'text': tail})
        emit('provider_finished', {'call_id': call_id, 'status': result.status,
                                   'stop_reason': result.reason})
    return result
