from configparser import ConfigParser

import pytest
from click.testing import CliRunner

import main
from component_registry import ComponentRegistry
from config_manager import ConfigManager


@pytest.mark.parametrize('resume', [['--resume'], ['--resume', 'saved'], ['--resume', '--last']])
def test_private_chat_rejects_resume(resume):
    result = CliRunner().invoke(main.cli, ['chat', '--private', *resume])
    assert result.exit_code == 2
    assert '--private cannot be combined with --resume' in result.output


def test_private_chat_keeps_turns_in_memory_with_persistence_enabled(tmp_path, monkeypatch):
    import core.hooks
    import memex_mcp.bootstrap

    memory = tmp_path / 'memory'
    memory.mkdir()
    (memory / 'MEMORY.md').write_text('Persisted fact')
    db = tmp_path / 'db' / 'store.sqlite'
    sessions = tmp_path / 'sessions'
    chats = tmp_path / 'chats'
    logs = tmp_path / 'logs'
    backups = tmp_path / 'backups'
    config = ConfigManager.__new__(ConfigManager)
    config.base_config = ConfigParser()
    config.models = ConfigParser()
    config.base_config.read_dict({
        'DEFAULT': {'provider': 'Fake', 'user_db': str(db), 'chats_directory': str(chats),
                    'template_handler': 'prompt_template_memory_file', 'tool_mode': 'official',
                    'user_label': 'You', 'user_label_color': 'blue',
                    'response_label': 'Assistant', 'response_label_color': 'green'},
        'Fake': {'active': 'true'},
        'LOG': {'active': 'true', 'dir': str(logs)},
        'MEMORY': {'active': 'true', 'directory': str(memory), 'backup_directory': str(backups)},
        'SESSIONS': {'session_autosave': 'true', 'session_directory': str(sessions)},
        'MCP': {'active': 'true'},
    })
    monkeypatch.setattr(main, 'ConfigManager', lambda _conf: config)
    hook_phases, mcp_checks, history, prompts = [], [], [], []
    monkeypatch.setattr(core.hooks, '_parse_hook_names',
                        lambda session, phase: hook_phases.append(phase) or [])
    monkeypatch.setattr(memex_mcp.bootstrap, '_get_cfg_sections',
                        lambda session: mcp_checks.append(session) or ({}, {}))

    class Provider:
        def __init__(self, session):
            self.session = session

        def chat(self):
            turns = self.session.get_context('chat').get('all')
            inputs = [turn['message'] for turn in turns if turn['role'] == 'user']
            history.append(inputs)
            prompt = self.session.get_context('prompt')
            prompts.append(prompt.get()['content'] if prompt else '')
            return f'Seen {len(inputs)} turn(s)'

        def get_usage(self):
            return {'total_in': 10, 'total_out': 3}

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: Provider)
    runner = CliRunner()
    result = runner.invoke(main.cli, ['-p', 'Memory: {{memory}}', 'chat', '--private'], input=(
        'First question\nFollow-up\n/save chat scratch\n/save checkpoint\n'
        '/save code\n/workspace attach\n/load session\n/quit\n'
    ))
    assert result.exit_code == 0, result.output
    assert 'Private session' in result.output
    assert 'Seen 2 turn(s)' in result.output
    assert result.output.count('unavailable in a private session') == 5
    assert history == [['First question'], ['First question', 'Follow-up']]
    assert prompts == ['Memory: ', 'Memory: ']
    assert not hook_phases
    assert not mcp_checks
    assert all(not path.exists() for path in (db.parent, chats, logs, backups, sessions))
    assert (memory / 'MEMORY.md').read_text() == 'Persisted fact'

    result = runner.invoke(main.cli, ['chat'], input='Normal chat\n/quit\n')
    assert result.exit_code == 0, result.output
    assert list(sessions.glob('*.ims.json'))
    assert db.is_file()
    assert logs.is_dir()
    assert backups.is_dir()
    assert hook_phases == ['session_start', 'pre_turn', 'post_turn', 'session_end']
    assert len(mcp_checks) == 1
