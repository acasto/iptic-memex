"""Resolve terminal task policy and assemble sessions outside core execution."""

from core.runtime import RunRequest, execute
from app.runtime_services import configure_runtime


def configure_completion(session):
    """Install explicit tool and diagnostic services, without choosing a UI."""
    if not hasattr(session, 'runtime_events'):
        if not getattr(session, 'completion_tools_enabled', False):
            session.set_option('tool_mode', 'none')
        configure_runtime(session)


def build_completion_session(config_manager, options=None, *, builder=None):
    """Resolve completion defaults without initializing a frontend."""
    from app.headless import build_headless_session
    options = dict(options or {})
    enabled = options.get('active_tools_agent') not in (None, '', '__none__')
    session = build_headless_session(config_manager, options, builder=builder, tools_enabled=enabled)
    session.completion_tools_enabled = enabled
    session.mcp_autoload_policy = {'enabled': False}
    return session


def run_completion(*, session, message='', contexts=None, stream=False, capture='text',
                   stdin_as_message=False, on_event=None, cancellation=None, interactions=None):
    """Translate completion configuration into the generic execution request."""
    configure_completion(session)
    limit = session.get_option('AGENT', 'large_input_limit', fallback=None)
    limit = int(limit) if limit is not None and str(limit).strip() else None
    tools = getattr(session, 'completion_tools_enabled', False)
    calls = int(session.get_option('TOOLS', 'auto_submit_max_turns', fallback=6) or 6) if tools else 1
    return execute(session, RunRequest(message=message, stream=stream,
                                      capture_raw=capture == 'raw', input_limit=limit,
                                      stdin_as_message=stdin_as_message,
                                      contexts=tuple(contexts or ()), execute_tools=tools,
                                      max_calls=max(1, calls)),
                   on_event=on_event, cancellation=cancellation, interactions=interactions)
