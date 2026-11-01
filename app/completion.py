"""Resolve and assemble tool-free completion sessions outside core execution."""

from component_registry import ComponentRegistry
from core.runtime import RunRequest, execute
from core.runtime.events import EventChannel
from app.session_output import EventOutput


def configure_completion(session):
    """Install explicit tool and diagnostic services, without choosing a UI."""
    if not hasattr(session, 'runtime_events'):
        session.set_option('tool_mode', 'none')
        session.runtime_events = EventChannel()
        output = EventOutput(session.runtime_events)
        utils = session.utils
        if hasattr(utils, 'replace_output'):
            utils.replace_output(output)
        else:
            utils.output = output


def build_completion_session(config_manager, options=None, *, builder=None):
    """Resolve noninteractive defaults; optional services stay disabled."""
    from session import Session

    options = dict(options or {})
    if not options.get('model'):
        model = config_manager.base_config.get('AGENT', 'default_model', fallback=None)
        if model:
            options['model'] = model
    options['tool_mode'] = 'none'
    config = config_manager.create_session_config(options)
    registry = ComponentRegistry(config)
    session = Session(config, registry)
    session._builder = builder
    session.current_model = options.get('model')
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
                   stdin_as_message=False, on_event=None, cancellation=None):
    """Translate completion configuration into the generic execution request."""
    configure_completion(session)
    limit = session.get_option('AGENT', 'large_input_limit', fallback=None)
    limit = int(limit) if limit is not None and str(limit).strip() else None
    return execute(session, RunRequest(message=message, stream=stream,
                                      capture_raw=capture == 'raw', input_limit=limit,
                                      stdin_as_message=stdin_as_message,
                                      contexts=tuple(contexts or ())),
                   on_event=on_event, cancellation=cancellation)
