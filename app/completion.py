"""Resolve terminal task policy and assemble sessions outside core execution."""

from component_registry import ComponentRegistry
from core.runtime import RunRequest, execute
from app.runtime_services import configure_runtime, install_tool_selection


def configure_completion(session):
    """Install explicit tool and diagnostic services, without choosing a UI."""
    if not hasattr(session, 'runtime_events'):
        if not getattr(session, 'completion_tools_enabled', False):
            session.set_option('tool_mode', 'none')
        configure_runtime(session)


def build_completion_session(config_manager, options=None, *, builder=None):
    """Resolve noninteractive defaults; optional services stay disabled."""
    from session import Session

    options = dict(options or {})
    if not options.get('model'):
        model = config_manager.base_config.get('AGENT', 'default_model', fallback=None)
        if model:
            options['model'] = model
    tools_enabled = options.get('active_tools_agent') not in (None, '', '__none__')
    if not tools_enabled:
        options['tool_mode'] = 'none'
    config = config_manager.create_session_config(options)
    registry = ComponentRegistry(config)
    session = Session(config, registry)
    session._builder = builder
    session.current_model = options.get('model')
    session.completion_tools_enabled = tools_enabled
    if tools_enabled:
        install_tool_selection(session, 'AGENT')
        session.write_policy = session._normalize_write_policy(
            options.get('agent_writes') or config.base_config.get('AGENT', 'writes_policy', fallback='deny'))
    configure_completion(session)
    params = session.get_params()
    provider_name = params.get('provider')
    if not provider_name:
        raise RuntimeError('No provider configured')
    if not config.base_config.getboolean(provider_name, 'active', fallback=False):
        raise RuntimeError(f"Provider '{provider_name}' is not active for model '{params.get('model')}'. "
                           'Enable it in config.ini.')
    provider_class = registry.load_provider_class(provider_name)
    if provider_class is None:
        raise RuntimeError(f"Could not load provider '{provider_name}'")
    session.provider = provider_class(session)
    # A blank base prompt still passes through the normal supplemental addenda.
    # Completion does not implicitly acquire the interactive system prompt.
    if session.add_context('prompt', options.get('prompt', '')) is None:
        raise ValueError('Could not load prompt context')
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
