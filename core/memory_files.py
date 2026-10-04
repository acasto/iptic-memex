"""Persistent memory files and recovery copies managed by the application."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from utils.tool_args import get_bool, get_int


def memory_enabled(session) -> bool:
    """Whether the session has opted into file memory."""
    return bool(get_bool({'active': session.get_option('MEMORY', 'active', False)},
                         'active', False))


def _directory(session, option: str, default: str) -> Path:
    value = session.get_option('MEMORY', option, default) or default
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        base = session.get_option('TOOLS', 'base_directory', 'working')
        base = Path.cwd() if base in ('working', '.') else Path(str(base)).expanduser()
        path = base / path
    return path.resolve()


def memory_directory(session) -> Path:
    """Resolve the memory root using the tool base-directory convention."""
    return _directory(session, 'directory', '~/.config/iptic-memex/memory')


def _warning(session, message: str) -> None:
    session.ui.emit('warning', {'message': f'File memory: {message}'})


def read_memory(session, project: str | None = None) -> str:
    """Read a bounded startup entry file, never following paths outside memory."""
    root = memory_directory(session)
    if project is not None:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', project):
            raise ValueError(
                'Project memory names must use letters, digits, hyphens or underscores.')
        path = root / 'projects' / project / 'MEMORY.md'
    else:
        path = root / 'MEMORY.md'
    if not path.resolve().is_relative_to(root):
        raise ValueError('Memory entry resolves outside the memory directory.')
    limit = get_int({'limit': session.get_option('MEMORY', 'max_chars', 6000)},
                    'limit', 6000)
    limit = max(1, limit or 6000)
    try:
        with path.open(encoding='utf-8') as stream:
            content = stream.read(limit + 1)
    except FileNotFoundError:
        return ''
    if len(content) > limit:
        content = content[:limit] + '\n… (memory truncated; read the file for more)'
    return content


_BACKUP_NAME = re.compile(r'memory-\d{8}T\d{12}Z-[0-9a-f]{32}')


def backup_memory(session) -> Path | None:
    """Copy existing memory, then retain N completed snapshots after success.

    This function is called only by interactive session startup. Symlinks are
    copied as links rather than following them into unrelated directories.
    """
    enabled = get_bool({'enabled': session.get_option('MEMORY', 'backup_on_session_start', True)},
                       'enabled', True)
    count = get_int({'count': session.get_option('MEMORY', 'backup_count', 10)}, 'count', 10)
    if not enabled or count is None or count <= 0:
        return None
    root = memory_directory(session)
    if not root.is_dir() or not any(root.iterdir()):
        return None
    destination = _directory(session, 'backup_directory',
                             '~/.config/iptic-memex/memory-backups')
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError('Memory and backup directories must be separate, non-nested directories.')
    # A recovery copy must not be deletable through the agent's exposed roots.
    fs_handler = session.get_action('assistant_fs_handler')
    for allowed in fs_handler.get_allowed_roots():
        allowed_path = Path(allowed['path'])
        if allowed['mode'] == 'rw' and (
            destination.is_relative_to(allowed_path)
            or allowed_path.is_relative_to(destination)
        ):
            raise ValueError(f'Backup directory overlaps writable agent root: {allowed_path}')
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot = destination / f'memory-{stamp}-{uuid.uuid4().hex}'
    temporary = Path(tempfile.mkdtemp(prefix='.memory-pending-', dir=destination))
    try:
        shutil.copytree(root, temporary / 'contents', symlinks=True)
        (temporary / 'contents').rename(snapshot)
    finally:
        shutil.rmtree(temporary)
    snapshots = sorted(p for p in destination.iterdir()
                       if _BACKUP_NAME.fullmatch(p.name) and p.is_dir() and not p.is_symlink())
    for old in snapshots[:-count]:
        shutil.rmtree(old)
    return snapshot


def prepare_memory(session, *, interactive: bool) -> None:
    """Expose persistent memory on all runs; back up only on interactive startup."""
    if not memory_enabled(session):
        return
    if interactive:
        try:
            backup_memory(session)
        except (OSError, ValueError) as exc:
            _warning(session, f'could not create recovery backup: {exc}')
    try:
        # Do not overwrite or recreate entry files: a deleted file may need recovery.
        memory_directory(session).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _warning(session, f'could not create memory directory: {exc}')
