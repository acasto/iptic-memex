from __future__ import annotations

import configparser
from types import SimpleNamespace

import pytest

from actions.assistant_docker_tool_action import AssistantDockerToolAction
from actions.assistant_fs_handler_action import AssistantFsHandlerAction
from actions.prompt_template_memory_file_action import PromptTemplateMemoryFileAction
from config_manager import SessionConfig
from core import memory_files
from core.session_builder import SessionBuilder


def make_session(tmp_path, **settings):
    base = tmp_path / 'workspace'
    base.mkdir(exist_ok=True)
    config = configparser.ConfigParser()
    config['DEFAULT'] = {'template_handler': 'prompt_template_memory_file'}
    config['TOOLS'] = {'base_directory': str(base)}
    config['MEMORY'] = {
        'active': 'true', 'directory': str(tmp_path / 'memory'),
        'backup_directory': str(tmp_path / 'backups'), 'backup_count': '2',
        **{key: str(value) for key, value in settings.items()},
    }
    config['PROMPTS'] = {'default': 'false'}
    manager = SimpleNamespace(
        base_config=config,
        create_session_config=lambda options: SessionConfig(
            config, configparser.ConfigParser(), options),
    )
    return SessionBuilder(manager)


def entries(root):
    return sorted(p for p in root.iterdir() if p.name.startswith('memory-'))


def test_templates_read_global_project_and_live_edits(tmp_path):
    session = make_session(tmp_path).build(mode='internal')
    root = memory_files.memory_directory(session)
    (root / 'MEMORY.md').write_text('Global preference', encoding='utf-8')
    project = root / 'projects' / 'iptic-memex'
    project.mkdir(parents=True)
    (project / 'MEMORY.md').write_text('Project decision', encoding='utf-8')
    handler = PromptTemplateMemoryFileAction(session)
    assert handler.run('{{memory_directory}} {{memory}} {{memory:iptic-memex}}') == (
        f'{root} Global preference Project decision')
    (root / 'MEMORY.md').write_text('Updated preference', encoding='utf-8')
    assert handler.run('{{memory}}') == 'Updated preference'
    assert handler.run('{{memory:missing}}') == ''
    # File templates must not instantiate the SQL action or a storage connection.
    assert 'assistant_memory_tool' not in session._registry._action_cache


def test_templates_bound_reads_and_reject_escape(tmp_path):
    session = make_session(tmp_path, max_chars=5).build(mode='internal')
    root = memory_files.memory_directory(session)
    (root / 'MEMORY.md').write_text('123456789', encoding='utf-8')
    assert memory_files.read_memory(session).startswith('12345\n…')
    with pytest.raises(ValueError):
        memory_files.read_memory(session, '../../outside')
    (root / 'MEMORY.md').unlink()
    outside = tmp_path / 'private.md'
    outside.write_text('outside', encoding='utf-8')
    (root / 'MEMORY.md').symlink_to(outside)
    with pytest.raises(ValueError):
        memory_files.read_memory(session)


@pytest.mark.parametrize('mode', ['chat', 'tui', 'web'])
def test_only_interactive_starts_rotate_backups(tmp_path, mode):
    builder = make_session(tmp_path)
    session = builder.build(mode='internal')
    root = memory_files.memory_directory(session)
    entry = root / 'MEMORY.md'
    entry.write_text('Before', encoding='utf-8')
    backups = tmp_path / 'backups'
    for noninteractive in ('completion', 'internal'):
        builder.build(mode=noninteractive)
        assert not backups.exists()
    builder.build(mode=mode)
    assert (entries(backups)[0] / 'MEMORY.md').read_text() == 'Before'
    for content in ('Second', 'Third'):
        entry.write_text(content, encoding='utf-8')
        builder.build(mode=mode)
    assert len(entries(backups)) == 2
    assert [(p / 'MEMORY.md').read_text() for p in entries(backups)] == ['Second', 'Third']
    entry.unlink()
    builder.build(mode=mode)
    assert len(entries(backups)) == 2  # Empty/deleted memory cannot evict recovery copies.


def test_failed_copy_preserves_backups(tmp_path, monkeypatch):
    builder = make_session(tmp_path, backup_count=1)
    session = builder.build(mode='internal')
    (memory_files.memory_directory(session) / 'MEMORY.md').write_text('Recover me')
    builder.build(mode='chat')
    originals = entries(tmp_path / 'backups')

    def failed_copy(*args, **kwargs):
        raise OSError('Disk full')

    monkeypatch.setattr(memory_files.shutil, 'copytree', failed_copy)
    result = builder.build(mode='internal')
    events = []
    result.ui = SimpleNamespace(emit=lambda kind, data: events.append((kind, data)))
    memory_files.prepare_memory(result, interactive=True)
    assert entries(tmp_path / 'backups') == originals
    assert list((tmp_path / 'backups').iterdir()) == originals
    assert events[0][0] == 'warning'
    assert 'Disk full' in events[0][1]['message']


def test_shared_roots_and_docker_policy(tmp_path):
    session = make_session(tmp_path).build(mode='internal')
    root = memory_files.memory_directory(session)
    roots = session.get_action('assistant_fs_handler').get_allowed_roots()
    assert {'path': str(root), 'mode': 'rw'} in roots
    docker = AssistantDockerToolAction(session)
    assert f'{root}:{root}' in docker._mount_args()
    session.enter_agent_mode('deny')
    assert f'{root}:{root}:ro' in docker._mount_args()
    assert not any('backups' in r['path'] for r in roots)


def test_disabled_feature_and_relative_root(tmp_path):
    session = make_session(tmp_path, active='false').build(mode='chat')
    assert not (tmp_path / 'memory').exists()
    assert PromptTemplateMemoryFileAction(session).run('{{memory}}') == ''
    assert all(r['path'] != str(tmp_path / 'memory')
               for r in AssistantFsHandlerAction(session).get_allowed_roots())
    relative = make_session(tmp_path, directory='notes').build(mode='internal')
    assert memory_files.memory_directory(relative) == tmp_path / 'workspace' / 'notes'


def test_backups_reject_exposed_and_nested_paths(tmp_path):
    for destination in (tmp_path / 'memory' / 'backups', tmp_path / 'workspace' / 'backups'):
        session = make_session(tmp_path, backup_directory=destination).build(mode='internal')
        (memory_files.memory_directory(session) / 'MEMORY.md').write_text('Keep')
        with pytest.raises(ValueError):
            memory_files.backup_memory(session)
        assert not destination.exists()


def test_backups_allow_readonly_parent_with_writable_memory_child(tmp_path):
    builder = make_session(tmp_path)
    # Match a read-only config root containing writable memory and its sibling backups.
    builder.config_manager.base_config['TOOLS']['extra_ro_roots'] = str(tmp_path)
    session = builder.build(mode='internal')
    (memory_files.memory_directory(session) / 'MEMORY.md').write_text('Recover me')
    builder.build(mode='chat')
    snapshot = entries(tmp_path / 'backups')[0]
    assert (snapshot / 'MEMORY.md').read_text() == 'Recover me'
    fs = session.get_action('assistant_fs_handler')
    assert fs.validate_path(str(snapshot / 'MEMORY.md'), operation='read') is not None
    assert fs.validate_path(str(snapshot / 'MEMORY.md'), operation='write') is None
    docker = AssistantDockerToolAction(session)
    assert f'{tmp_path}:{tmp_path}:ro' in docker._mount_args()


def test_backups_reject_writable_descendant(tmp_path):
    builder = make_session(tmp_path)
    exposed = tmp_path / 'backups' / 'exposed'
    builder.config_manager.base_config['TOOLS']['extra_rw_roots'] = str(exposed)
    session = builder.build(mode='internal')
    (memory_files.memory_directory(session) / 'MEMORY.md').write_text('Keep')
    with pytest.raises(ValueError, match='writable agent root'):
        memory_files.backup_memory(session)
    assert not (tmp_path / 'backups').exists()


@pytest.mark.parametrize('settings', [
    {'backup_on_session_start': 'false'}, {'backup_count': 0},
])
def test_backup_can_be_disabled(tmp_path, settings):
    builder = make_session(tmp_path, **settings)
    session = builder.build(mode='internal')
    (memory_files.memory_directory(session) / 'MEMORY.md').write_text('Retain')
    builder.build(mode='chat')
    assert not (tmp_path / 'backups').exists()


def test_prompt_pipeline_uses_file_snapshot(tmp_path):
    builder = make_session(tmp_path)
    session = builder.build(mode='internal')
    entry = memory_files.memory_directory(session) / 'MEMORY.md'
    entry.write_text('Startup fact')
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('Directory: {{memory_directory}}\n{{memory}}')
    builder.config_manager.base_config['DEFAULT']['prompt_directory'] = str(tmp_path)
    builder.config_manager.base_config['PROMPTS']['default'] = 'prompt.txt'
    session = builder.build(mode='chat')
    assert session.get_context('prompt').get()['content'] == (
        f'Directory: {entry.parent}\nStartup fact')
    entry.write_text('Later fact')
    assert session.get_context('prompt').get()['content'].endswith('Startup fact')
