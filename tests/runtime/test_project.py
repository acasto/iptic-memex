"""Project assembly contracts, using the offline provider fixture."""

import pytest

from app.project import resolve_project
from app.run_helpers import child_options


def project_file(root, text):
    folder = root / '.memex'
    folder.mkdir()
    (folder / 'project.toml').write_text(text)


@pytest.mark.parametrize('mode', ['chat', 'completion', 'internal'])
def test_project_settings_apply_before_prompt_setup(builder, tmp_path, mode):
    project_file(tmp_path, 'model = "explicit-model"\nagent_model = "explicit-model"\n'
                 'supplemental_prompt = "Project instructions"\n')
    session = builder.build(mode=mode, project_root=str(tmp_path))
    try:
        assert session.project.root == tmp_path
        assert session.get_params()['model'] == 'explicit-model'
        assert session.get_option('TOOLS', 'base_directory') == str(tmp_path)
        assert 'Project instructions' in session.get_action('build_system_addenda').run()
        assert session.project_sources['model'] == str(tmp_path / '.memex/project.toml')
    finally:
        session.close()


def test_explicit_options_win_and_shared_config_is_not_modified(builder, tmp_path):
    project_file(tmp_path, 'agent_model = "explicit-model"\n')
    session = builder.build(mode='completion', project_root=str(tmp_path), model='worker-model',
                            base_directory='/explicit/sandbox')
    try:
        assert session.get_params()['model'] == 'worker-model'
        assert session.get_option('TOOLS', 'base_directory') == '/explicit/sandbox'
        assert builder.config_manager.base_config.get('AGENT', 'default_model') == 'worker-model'
    finally:
        session.close()


def test_child_retains_project_when_cwd_changes(builder, tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    parent = builder.build(mode='completion', project_root=str(root))
    monkeypatch.chdir(tmp_path)
    child = builder.build(mode='internal', **child_options(parent, {}))
    try:
        assert child.project == parent.project
        assert child.get_option('TOOLS', 'base_directory') == str(root)
    finally:
        child.close()
        parent.close()


@pytest.mark.parametrize('text', ['tools = "cmd"', '[MCP]\nautoload = true',
                                  'model = 123', 'base_directory = "/"'])
def test_unsupported_project_settings_rejected(tmp_path, text):
    project_file(tmp_path, text)
    with pytest.raises(ValueError):
        resolve_project(tmp_path)


def test_project_config_cannot_follow_symlink_outside_root(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'project.toml').write_text('name = "outside"')
    (root / '.memex').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='within'):
        resolve_project(root)
