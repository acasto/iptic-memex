"""Application-owned restrictions for nested model sessions."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionScope:
    """A child can use only its inherited filesystem and tool ceiling."""

    root: str
    roots: tuple[tuple[str, str], ...]
    tools: frozenset[str]
    writes: str
    allowed_models: frozenset[str] | None = None


def child_scope(parent, options):
    """Validate requested scope before provider or prompt construction."""
    base = parent.get_option('TOOLS', 'base_directory', fallback='working')
    base = Path.cwd() if base in ('working', '.') else Path(base).expanduser()
    base = base.resolve()
    requested = options.get('base_directory', str(base))
    root = base if requested in ('working', '.') else Path(requested).expanduser()
    root = (root if root.is_absolute() else base / root).resolve()
    if not root.is_relative_to(base):
        raise ValueError('Child base_directory must remain within the parent sandbox')
    project = getattr(parent, 'project', None)
    if 'project_root' in options or ('project_context' in options and
                                   options['project_context'] != project):
        raise ValueError('Child runs cannot select another project')
    if 'services' in options:
        raise ValueError('Child service overrides require application-owned assembly')
    policy = parent.get_agent_write_policy() or 'deny'
    requested_policy = options.get('agent_writes', policy)
    ranks = {'deny': 0, 'dry-run': 1, 'allow': 2}
    if requested_policy not in ranks or ranks[requested_policy] > ranks[policy]:
        raise ValueError('Child write policy cannot exceed parent policy')
    fs = parent.get_action('assistant_fs_handler')
    # Preserve restrictions inside the narrowed root; discard ambient extra roots.
    roots = {str(root): fs._mode_for_path(str(root)) or 'ro'}
    for item in fs.get_allowed_roots():
        path = Path(item['path'])
        if path.is_relative_to(root):
            roots[str(path)] = item['mode']
    tools = set(parent.get_action('assistant_commands').commands)
    # These services have global stores and no project-scoped retrieval contract yet.
    tools.difference_update({'memory', 'ragsearch'})
    if parent.get_tools().get('cmd_tool') != 'assistant_docker_tool':
        tools.discard('cmd')
    inherited = getattr(parent, 'execution_scope', None)
    allowed_models = inherited.allowed_models if inherited else None
    if allowed_models is not None:
        requested_model = options.get('model') or parent.get_params().get('model')
        normalize = parent.config.normalize_model_name
        allowed = {normalize(model) or model for model in allowed_models}
        if (normalize(requested_model) or requested_model) not in allowed:
            raise ValueError('Child model is outside the inherited execution profile')
    return ExecutionScope(str(root), tuple(roots.items()), frozenset(tools), requested_policy,
                          allowed_models)
