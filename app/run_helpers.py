"""Shared context, model-profile, and trace helpers for application workflows."""

from typing import Any, Dict

_SIMPLE = (str, int, float, bool)


def _normalize_trace(trace: Any) -> Dict[str, Any]:
    """Return a minimal, safe correlation context for subsessions.

    Expected keys (best-effort):
      - trace_id: shared across a request lifecycle
      - parent_span_id: span in the outer session that spawned this subsession
      - outer_session_uid: session_uid of the outer session
    """
    if not isinstance(trace, dict):
        return {}
    out: Dict[str, Any] = {}
    # Only propagate correlation keys that should survive across session boundaries.
    # Do NOT propagate session-scoped identity like session_uid/ui_mode; those must
    # remain the subsession's own values.
    allow_prefixes = ("hook_", "provider_", "request_", "response_")
    allow_keys = {"trace_id", "parent_span_id", "outer_session_uid"}
    for k, v in trace.items():
        if not isinstance(k, str) or not isinstance(v, _SIMPLE):
            continue
        if k in allow_keys or k.startswith(allow_prefixes):
            out[k] = v
    # If the caller gave us a current span_id, treat it as the parent in the subsession.
    if "parent_span_id" not in out and isinstance(trace.get("span_id"), str):
        out["parent_span_id"] = trace.get("span_id")
    return out


def attach_contexts(session, contexts):
    """Attach explicit context inputs, failing rather than dropping unreadable inputs."""
    for kind, value in contexts or ():
        if session.add_context(kind, value) is None:
            raise ValueError(f'Could not load {kind} context: {value}')


def child_options(outer_session, overrides):
    """Inherit sandbox and service restrictions, keeping model/tool choices separate."""
    options = dict(overrides or {})
    if outer_session is None:
        return options
    from app.execution_scope import child_scope
    from app.session_services import HeadlessServices
    scope = child_scope(outer_session, options)
    if scope.allowed_models is not None and not options.get('model'):
        options['model'] = outer_session.get_params().get('model')
    for key, value in outer_session.get_tools().items():
        if key.endswith('_tool'):
            if key in options and options[key] != value:
                raise ValueError('Child runs cannot replace tool implementations')
            options[key] = value
    options.update(base_directory=scope.root, agent_writes=scope.writes,
                   docker_always_ephemeral=True, cmd_tool='assistant_docker_tool')
    project = getattr(outer_session, 'project', None)
    if project is not None:
        options['project_context'] = project
    boundary = (getattr(outer_session, 'child_call_boundary', None) or
                getattr(outer_session, 'before_provider_call', None))
    options['services'] = HeadlessServices(file_memory=False, before_call=boundary,
                                           execution_scope=scope)
    return options


def status_tags_enabled(session, overrides=None):
    if (overrides or {}).get('no_agent_status_tags'):
        return False
    from utils.tool_args import get_bool
    return get_bool({'value': session.get_option('AGENT', 'status_tags', fallback=True)}, 'value', True)
