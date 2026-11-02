"""Adapt workspace sessions to application services and the shared task runtime."""

from app.runs import run_agent, run_completion
from app.session_builder import SessionBuilder
from app.session_services import HeadlessServices
from config_manager import ConfigManager
from workspace.tools import WorkspaceAccess


def build_session(spec, store):
    """Build a scratch worker or tool-free synthesis without editing shared config."""
    synthesis = spec.get('phase') == 'synthesis'
    options = dict(spec.get('overrides') or {})
    options.update(prompt=spec['prompt'], base_directory=spec['repository'], stream=False,
                   docker_always_ephemeral=True, agent_writes=spec['writes_policy'],
                   show_context_summary=False, show_context_details=False)
    if spec.get('model'):
        options['model'] = spec['model']
    if synthesis:
        # A CLI worker tool selection must not grant synthesis tool access.
        options.pop('active_tools_agent', None)
        options['tool_mode'] = 'none'

    def configure(session):
        session.set_flag('hooks_disabled', True)
        if not synthesis:
            session.workspace = WorkspaceAccess(store, spec['run_id'], spec['worker'])

    services = HeadlessServices(file_memory=False,
                                required_tools=() if synthesis else ('workspace',),
                                configure=configure)
    session = SessionBuilder(ConfigManager(spec.get('config_file'))).build(
        mode='completion' if synthesis else 'internal', services=services, **options)
    try:
        if not synthesis:
            # Resolve the live profile, including its hard blocklist, before
            # launching the model. Keep late MCP registration/config changes live.
            session.resolve_tool_selection()
            available = session.get_action('assistant_commands').commands
            if (session.get_agent_write_policy() != 'allow' and 'cmd' in available
                    and session.get_tools().get('cmd_tool') != 'assistant_docker_tool'):
                raise ValueError('Read-only workspace commands require the Docker cmd tool; '
                                 'select assistant_docker_tool or omit cmd with --tools')
        return session
    except BaseException:
        session.close()
        raise


def run(session, spec, *, on_event, before_call):
    """Submit through the common runtime; workspace owns processes and persistence."""
    if spec.get('phase') == 'synthesis':
        return run_completion(session=session, message=spec['message'], on_event=on_event)
    return run_agent(session=session, steps=spec['steps'], message=spec['message'],
                     writes_policy=spec['writes_policy'], disable_hooks=True, output='final',
                     on_event=on_event, before_call=before_call)
