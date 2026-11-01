"""Chat task policy above the same runtime used for terminal completion."""

from app.runtime_services import configure_runtime, install_tool_selection
from core.runtime import RunRequest, execute


def configure_chat(session):
    """Keep terminal services between runs; route execution diagnostics to events."""
    configure_runtime(session, idle_output=session.utils.output)
    install_tool_selection(session, 'TOOLS')
    if 'agent_writes' in (session.config.overrides or {}):
        session.write_policy = session._normalize_write_policy(session.config.overrides['agent_writes'])


def run_chat_turn(session, message, *, on_event=None, interactions=None, cancellation=None):
    """Submit a chat task with tool continuation owned entirely by execution."""
    limit = session.get_option('TOOLS', 'large_input_limit', fallback=None)
    limit = int(limit) if limit is not None and str(limit).strip() else None
    calls = int(session.get_option('TOOLS', 'auto_submit_max_turns', fallback=6) or 6)
    if not session.get_option('TOOLS', 'allow_auto_submit', fallback=True):
        calls = 1

    def before_call(active, index):
        if index != 1:
            return
        from core.hooks import run_hooks
        if not active.get_user_data('__hooks_session_started__', False):
            active.set_user_data('__hooks_session_started__', True)
            run_hooks(active, 'session_start', {'input_text': message})
        run_hooks(active, 'pre_turn', {'input_text': message})
        # Hooks already attach their outputs to the current chat turn. They must
        # not leak again into the next tool result as pending contexts.
        from core.runtime.tools import take_contexts
        take_contexts(active)

    def after_run(active, outcome):
        if outcome.turns and outcome.status != 'cancelled':
            from core.hooks import run_hooks
            run_hooks(active, 'post_turn', {'last_display': outcome.last_text or '',
                                           'last_sanitized': outcome.sanitized or ''})

    return execute(session, RunRequest(message=message, stream=bool(session.get_params().get('stream')),
                                      execute_tools=True, max_calls=max(1, calls), input_limit=limit),
                   on_event=on_event, interactions=interactions, cancellation=cancellation,
                   before_call=before_call, after_run=after_run)
