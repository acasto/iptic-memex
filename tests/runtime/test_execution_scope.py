"""Enforcement of nested-session access independently of model behavior."""
from pathlib import Path
import pytest
from app.run_helpers import child_options


def parent(builder, tmp_path):
    return builder.build(mode='agent', base_directory=str(tmp_path),
                         active_tools_agent='file,cmd,memory,ragsearch',
                         cmd_tool='assistant_docker_tool', agent_writes='deny')


@pytest.mark.parametrize('overrides', [dict(base_directory='/'), dict(agent_writes='allow'),
                                     dict(project_root='/'), dict(services=object())])
def test_child_cannot_expand_authority(builder, tmp_path, overrides):
    session = parent(builder, tmp_path)
    try:
        with pytest.raises(ValueError):
            child_options(session, overrides)
    finally:
        session.close()


def test_symlink_and_relative_escape_rejected(builder, tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'escape').symlink_to(tmp_path, target_is_directory=True)
    session = parent(builder, root)
    try:
        for path in ('..', 'escape'):
            with pytest.raises(ValueError):
                child_options(session, {'base_directory': path})
    finally:
        session.close()


def test_narrowed_child_ignores_extra_roots_and_ambient_memory(builder, tmp_path):
    sub = tmp_path / 'sub'
    sub.mkdir()
    session = parent(builder, tmp_path)
    options = child_options(session, {'base_directory': 'sub', 'extra_rw_roots': '/',
                                     'active_tools_agent': 'file,memory,ragsearch,cmd'})
    child = builder.build(mode='internal', **options)
    try:
        fs = child.get_action('assistant_fs_handler')
        assert fs.validate_path(str(tmp_path / 'outside')) is None
        assert fs.validate_path(str(sub / 'inside')) == str(sub / 'inside')
        tools = child.get_action('assistant_commands').commands
        assert 'memory' not in tools and 'ragsearch' not in tools
        assert child.file_memory_enabled is False
        assert child.get_action('prompt_template_memory').run('{{memory}}') == ''
        with pytest.raises(ValueError):
            child_options(child, {'base_directory': str(tmp_path)})
        docker = child.get_action('assistant_docker_tool')
        assert docker._tmp_mount_args() == ['--tmpfs', '/tmp:rw,mode=1777']
        assert not docker.is_persistent
        assert all(item['path'] == str(sub) for item in fs.get_allowed_roots())
    finally:
        child.close()
        session.close()


def test_child_cannot_reenable_tools_with_dynamic_registration(builder, tmp_path):
    session = parent(builder, tmp_path)
    child = builder.build(mode='internal', **child_options(session, {}))
    try:
        child.set_user_data('__dynamic_tools__', {
            'memory': {'function': {'type': 'action', 'name': 'assistant_memory_tool'}}})
        assert 'memory' not in child.get_action('assistant_commands').commands
        with pytest.raises(ValueError, match='Local shell'):
            child.get_action('assistant_cmd_tool').run({'command': 'pwd'})
    finally:
        child.close()
        session.close()


def test_parent_call_boundary_is_inherited(builder, tmp_path):
    calls = []
    session = parent(builder, tmp_path)
    session.child_call_boundary = lambda active, index: calls.append(index)
    child = builder.build(mode='completion', **child_options(session, {}))
    try:
        from app.completion import run_completion
        run_completion(session=child, message='Hello')
        assert calls == [1]
    finally:
        child.close()
        session.close()
