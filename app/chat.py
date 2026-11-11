"""Chat task policy above the same runtime used for terminal completion."""

from app.runtime_services import configure_runtime, install_tool_selection
from core.runtime import RunRequest, execute
from core.runtime.contracts import RunStopped
from core.runtime.execution import input_tokens
from utils.tool_args import get_bool


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
        if index == 1:
            prepare_hooks(active)
        tokens = input_tokens(active, active.get_context('chat'))
        if limit and limit > 0 and tokens > limit:
            warning = f'Input size ({tokens} tokens) exceeds recommended limit ({limit}).'
            active.runtime_events.emit('warning', {'message': warning})
            confirm = get_bool({'value': active.get_option(
                'TOOLS', 'confirm_large_input', fallback=True)}, 'value', True)
            if index > 1 and confirm:
                if not active.interactions.ask_bool(warning + ' Continue tool follow-up?',
                                                    default=False):
                    raise RunStopped('input_declined', 'Tool follow-up declined by user')

    def prepare_hooks(active):
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
                                      execute_tools=True, max_calls=max(1, calls)),
                   on_event=on_event, interactions=interactions, cancellation=cancellation,
                   before_call=before_call, after_run=after_run)
