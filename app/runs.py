"""Application entry points for isolated completion and agent work."""

from uuid import uuid4

from app.agent import run_agent
from app.completion import run_completion as complete
from app.run_helpers import _normalize_trace, attach_contexts, child_options, status_tags_enabled
from core.cancellation import CancellationToken
from core.runtime import RunOutcome


def build_subsession(builder, *, overrides=None):
    """Build an isolated, event-based session; never substitute a UI."""
    return builder.build(mode='internal', **dict(overrides or {}))


def run_completion(*, builder=None, overrides=None, contexts=None, message='', capture='text',
                   trace=None, session=None, stream=False, stdin_as_message=False,
                   on_event=None, cancellation=None, interactions=None, outer_session=None):
    """Execute a completion with inherited cancellation and an explicit trace."""
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
            raise ValueError('A session builder is required for an internal completion')
        session = builder.build(mode='completion', **child_options(outer_session, overrides))
    if session is outer_session:
        raise ValueError('Internal runs require a separate session')
    try:
        correlation = _normalize_trace(trace)
        if outer_session is not None:
            correlation['outer_session_uid'] = outer_session.session_uid
        if correlation:
            session.utils.logger.update_base_context(correlation)
        return complete(session=session, message=message, contexts=contexts, stream=stream,
                        capture=capture, stdin_as_message=stdin_as_message, on_event=on_event,
                        cancellation=token, interactions=interactions)
    finally:
        if owned:
            session.close()
