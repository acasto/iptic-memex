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
            if not spec.get('research'):
                from workspace.budget import CallAllowance
                session.call_allowance = CallAllowance(spec['steps'])
                session.before_provider_call = session.call_allowance.before_call
        if spec.get('research'):
            from workspace.research import ResearchCoordinator
            session.research = ResearchCoordinator(spec, store)
            session.child_call_boundary = session.research.helper_call
            session.register_cleanup_callback(session.research.close)

    required = ('workspace', 'research') if spec.get('research') else ('workspace',)
    services = HeadlessServices(file_memory=False,
                                required_tools=() if synthesis else required,
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
    def prepare(active, index):
        research = getattr(active, 'research', None)
        if research:
            research.before_call(active, index)
        before_call(active, index)

    def observe(event):
        research = getattr(session, 'research', None)
        if research:
            research.observe(event)
        on_event(event)

    result = run_agent(session=session, steps=spec['steps'], message=spec['message'],
                       writes_policy=spec['writes_policy'], disable_hooks=True, output='final',
                       on_event=observe, before_call=prepare, contexts=spec.get('contexts'))
    research = getattr(session, 'research', None)
    if research and result.status in ('completed', 'incomplete'):
        research.wait()
        if research.state['assessment'] is None:
            research.state['assessment'] = dict(
                status='partial', content='Lead ended without an explicit coverage assessment')
            research.save()
    return result
