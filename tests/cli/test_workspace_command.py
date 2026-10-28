from __future__ import annotations

import configparser
import json
from types import SimpleNamespace

from click.testing import CliRunner

import main
from core.workspace_engine import WorkspaceEngine


def configure(monkeypatch, tmp_path):
    class Config:
        def __init__(self, _conf=None):
            self.base_config = configparser.ConfigParser()
            self.base_config['AGENT'] = {'default_model': 'glm-5.3'}
            self.base_config['TOOLS'] = {'base_directory': str(tmp_path)}
            self.base_config['WORKSPACE'] = {'agents': '2', 'max_steps_per_agent': '12'}

        def create_session_config(self, *_args):
            return SimpleNamespace(normalize_model_name=lambda name: name if name in
                                   ('glm-5.3', 'other-model') else None)

    monkeypatch.setattr(main, 'ConfigManager', Config)
    captured = {}

    def run(self, question, **_kwargs):
        captured.update(question=question, settings=self.settings, overrides=self.overrides)
        return {'id': 'test-run', 'status': 'completed', 'answer': 'Result',
                'workers': [], 'directory': str(tmp_path)}

    monkeypatch.setattr(WorkspaceEngine, 'run', run)
    return captured


def test_workspace_run_inherits_default_and_accepts_question(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path)
    result = CliRunner().invoke(main.cli, ['workspace', 'run', 'Find model selection'])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'Result'
    assert captured['question'] == 'Find model selection'
    assert captured['settings'].model is None  # SessionBuilder selects AGENT.default_model.
    assert captured['settings'].agents == 2
    assert captured['settings'].writes_policy == 'deny'
    assert captured['overrides']['use_mcp'] is False


def test_explicit_model_tools_and_limits_reach_workers(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path)
    result = CliRunner().invoke(main.cli, [
        '-m', 'other-model', '--tools', 'file', '--steps', '4', 'workspace', 'run',
        '--agents', '3', '--json', '--message', 'Question',
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['answer'] == 'Result'
    assert captured['settings'].model == 'other-model'
    assert captured['settings'].steps == 4
    assert captured['settings'].agents == 3
    assert captured['overrides']['active_tools_agent'] == 'file'


def test_question_input_conflicts_are_reported(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main.cli, ['workspace', 'run']).exit_code == 2
    result = runner.invoke(main.cli, ['workspace', 'run', 'one', '--message', 'two'])
    assert result.exit_code == 2
    assert 'once' in result.output
    result = runner.invoke(main.cli, ['workspace', 'run', '--stdin-message'], input='Question')
    assert result.exit_code == 0


def test_status_reads_saved_state_without_constructing_an_engine(monkeypatch, tmp_path):
    from core.workspace_state import WorkspaceStore

    configure(monkeypatch, tmp_path)
    store = WorkspaceStore(tmp_path / '.memex-workspace')
    run = store.create_run('Question', {})
    store.update_run(run['id'], status='completed', phase='finished')

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Monitoring must not construct an engine')

    monkeypatch.setattr(WorkspaceEngine, '__init__', forbidden)
    result = CliRunner().invoke(main.cli, ['workspace', 'status', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['question'] == 'Question'
