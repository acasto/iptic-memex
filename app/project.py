"""Resolve inert project preferences before application session assembly."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project inputs; these preferences do not grant resource access."""

    root: Path
    name: str
    config_path: Path | None = None
    model: str | None = None
    agent_model: str | None = None
    supplemental_prompt: str = ''


def resolve_project(root=None):
    """Read the optional, strictly allowlisted .memex/project.toml file."""
    path = Path(root or Path.cwd()).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f'Project root is not a directory: {path}')
    config = path / '.memex' / 'project.toml'
    if not config.exists():
        return ProjectContext(path, path.name)
    if not config.resolve().is_relative_to(path):
        raise ValueError('Project configuration must reside within the project root')
    data = tomllib.loads(config.read_text(encoding='utf-8'))
    allowed = {'name', 'model', 'agent_model', 'supplemental_prompt'}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f'Unsupported project settings: {", ".join(sorted(unknown))}')
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError(f'Project setting {key} must be text')
    return ProjectContext(path, data.get('name', path.name), config,
                          data.get('model') or None, data.get('agent_model') or None,
                          data.get('supplemental_prompt', '').strip())


def project_options(options, *, interactive):
    """Separate project metadata from provider options and apply preferences."""
    resolved = dict(options or {})
    project = resolved.pop('project_context', None)
    root = resolved.pop('project_root', None)
    if project is None:
        project = resolve_project(root)
    elif not isinstance(project, ProjectContext):
        raise ValueError('project_context must be a resolved ProjectContext')
    sources = {'project_root': 'explicit' if root else 'inherited' if
               (options or {}).get('project_context') else 'cwd'}
    model = project.model if interactive else project.agent_model
    if resolved.get('model'):
        sources['model'] = 'invocation'
    elif model:
        resolved['model'] = model
        sources['model'] = str(project.config_path)
    else:
        sources['model'] = 'configured default'
    # Resolve only the existing "working" convention, never an explicit sandbox.
    return project, resolved, sources


def bind_project(session, project, sources):
    """Install project context before prompt construction without global mutation."""
    session.project = project
    session.project_sources = dict(sources)
    base = session.get_option('TOOLS', 'base_directory', fallback='working')
    if base in (None, '', '.', 'working'):
        session.set_option('base_directory', str(project.root))
