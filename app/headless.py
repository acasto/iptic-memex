"""Explicit headless session assembly, shared by completion and agent workflows."""

from component_registry import ComponentRegistry
from app.runtime_services import configure_runtime, install_tool_selection
from app.session_services import HeadlessServices


def build_headless_session(config_manager, options=None, *, builder=None,
                           tools_enabled=False, default_prompt=False, memory=False, services=None):
    """Resolve noninteractive defaults; optional services stay disabled."""
    from session import Session

    from app.project import project_options, bind_project
    project, options, sources = project_options(options, interactive=False)
    if not options.get('model'):
        model = config_manager.base_config.get('AGENT', 'default_model', fallback=None)
        if model:
            options['model'] = model
    if not tools_enabled:
        options['tool_mode'] = 'none'
    config = config_manager.create_session_config(options)
    registry = ComponentRegistry(config)
    session = Session(config, registry)
    try:
        bind_project(session, project, sources)
        services = services or HeadlessServices()
        session._builder = builder
        session.current_model = options.get('model')
        session.file_memory_enabled = services.file_memory
        session.required_tools = services.required_tools
        session.before_provider_call = services.before_call
        if tools_enabled:
            install_tool_selection(session, 'AGENT')
            session.write_policy = session._normalize_write_policy(
                options.get('agent_writes') or config.base_config.get('AGENT', 'writes_policy', fallback='deny'))
        configure_runtime(session)
        session.utils.logger.update_base_context({'session_uid': session.session_uid})
        if services.configure:
            services.configure(session)
        if memory and services.file_memory is not False:
            from core.memory_files import prepare_memory
            prepare_memory(session, interactive=False)
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
        source = options.get('prompt', None if default_prompt else '')
        if source is None:
            source = registry.get_prompt_resolver().resolve() or ''
        session.headless_prompt_source = source
        if session.add_context('prompt', source) is None:
            raise ValueError('Could not load prompt context')
        return session

    except BaseException:
        session.close()
        raise
