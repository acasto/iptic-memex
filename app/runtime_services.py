"""Install session services without inferring an execution mode in core."""

from app.session_output import EventOutput
from core.runtime.events import EventChannel


def configure_runtime(session, *, idle_output=None):
    """Use event diagnostics during runs and optional terminal output between runs."""
    if not hasattr(session, 'runtime_events'):
        session.runtime_events = EventChannel()
        output = EventOutput(session.runtime_events, idle_output)
        utils = session.utils
        if hasattr(utils, 'replace_output'):
            utils.replace_output(output)
        else:
            utils.output = output


def install_tool_selection(session, section):
    """Resolve the tool profile at registry access, including later config edits."""
    def split(value):
        return tuple(part.strip().lower() for part in str(value or '').split(',') if part.strip())

    def resolve():
        override = (session.config.overrides or {}).get('active_tools_agent')
        if override is not None:
            active = () if str(override).lower() == '__none__' else split(override)
        else:
            value = session.get_option(section, 'active_tools', fallback=None)
            if not value and section != 'TOOLS':
                value = session.get_option('TOOLS', 'active_tools', fallback=None)
            active = split(value) if value else None
        blocked = split(session.get_option('AGENT', 'blocked_tools', fallback=None)) if section == 'AGENT' else ()
        inactive = split(session.get_option('TOOLS', 'inactive_tools', fallback=None)) if section == 'TOOLS' else ()
        return active, set(blocked) | set(inactive)

    session.resolve_tool_selection = resolve
