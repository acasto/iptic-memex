"""Real worker entry point and shared runtime, using an offline provider."""

from pathlib import Path
import sys

from workspace.engine import WorkspaceEngine, WorkspaceSettings
from workspace.state import WorkspaceStore


BOOTSTRAP = r'''
from configparser import ConfigParser
from pathlib import Path
import runpy
import time
from component_registry import ComponentRegistry
from config_manager import ConfigManager
from workspace import runtime

config = ConfigManager.__new__(ConfigManager)
config.base_config = ConfigParser()
config.base_config.read_dict({
    'DEFAULT': {'template_handler': 'none'},
    'AGENT': {'default_model': 'worker-model', 'active_tools': 'file'},
    'MEMORY': {'active': 'true', 'directory': 'disabled-memory'},
    'Offline': {'active': 'true'},
})
config.models = ConfigParser()
config.models.read_dict({name: {'provider': 'Offline', 'model_name': name, 'tools': 'true'}
                         for name in ('worker-model', 'answer-model')})

class Provider:
    def __init__(self, session):
        self.session = session
        self.calls = 0
    def chat(self):
        self.calls += 1
        if getattr(self.session, 'workspace', None):
            if self.calls == 1:
                return 'PRIVATE WORKER HISTORY'
            if self.session.get_params().get('test_slow'):
                time.sleep(30)
            return 'Contributor answer %%DONE%%'
        assert self.session.get_effective_tool_mode() == 'none'
        assert not self.session.completion_tools_enabled
        messages = self.session.get_context('chat').get('all')
        assert 'offline published evidence' in messages[0]['message']
        assert 'PRIVATE WORKER HISTORY' not in messages[0]['message']
        return 'Offline synthesized answer'
    def get_tool_calls(self):
        if getattr(self.session, 'workspace', None) and self.calls == 1:
            return [{'id': 'publish-1', 'name': 'workspace', 'arguments': {
                'operation': 'publish', 'title': 'Evidence',
                'content': 'offline published evidence'}}]
        return []
    def get_finish_reason(self):
        return 'tool_calls' if getattr(self.session, 'workspace', None) and self.calls == 1 else 'stop'
    def cleanup(self):
        root = Path(self.session.get_option('TOOLS', 'base_directory'))
        (root / ('closed-' + self.session.session_uid)).touch()

runtime.ConfigManager = lambda filename: config
ComponentRegistry.load_provider_class = lambda self, name: Provider
runpy.run_module('workspace.worker', run_name='__main__')
'''


def engine(tmp_path, *, slow=False):
    settings = WorkspaceSettings(repository=str(tmp_path), model='worker-model',
                                 synthesis_model='answer-model', agents=2, steps=3,
                                 run_timeout_seconds=8, synthesis_timeout_seconds=2)
    # Bootstrap only substitutes config/provider. The engine launches the real
    # relocated worker module, its real action, and the shared execution runtime.
    return WorkspaceEngine(WorkspaceStore(tmp_path / 'workspace'), settings,
                           worker_command=[sys.executable, '-c', BOOTSTRAP],
                           overrides={'test_slow': slow, 'active_tools_agent': 'file'})


def test_real_workers_publish_and_fresh_synthesis_replays_without_mutating_source(tmp_path):
    runtime = engine(tmp_path)
    source = runtime.run('Investigate offline')
    assert source['status'] == 'completed', source
    assert source['answer'] == 'Offline synthesized answer'
    assert len(list(tmp_path.glob('closed-*'))) == 3
    assert not (tmp_path / 'disabled-memory').exists()
    workers = {w['worker']: w for w in source['workers']}
    assert workers['worker-1']['tool_calls'] == workers['worker-2']['tool_calls'] == 1
    assert workers['synthesis']['model'] == 'answer-model'
    directory = Path(source['directory'])
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    replay = runtime.synthesize(source['id'])
    assert replay['status'] == 'completed', replay
    assert replay['answer'] == source['answer']
    assert replay['synthesis_input_sha256'] == source['synthesis_input_sha256']
    assert {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before
    assert len(list(tmp_path.glob('closed-*'))) == 4


def test_real_worker_timeout_preserves_published_evidence_and_closes_providers(tmp_path):
    runtime = engine(tmp_path, slow=True)
    runtime.settings.run_timeout_seconds = 2.5
    runtime.settings.synthesis_timeout_seconds = 1
    result = runtime.run('Investigate offline')
    assert result['status'] == 'partial', result
    assert result['answer'] == 'Offline synthesized answer'
    assert all(w['status'] == 'timed_out' for w in result['workers'] if w['worker'] != 'synthesis')
    assert len(runtime.store.notes(run_id=result['id'])) == 2
    assert all(process.poll() is not None for process, _ in runtime._children.values())
    assert len(list(tmp_path.glob('closed-*'))) == 3
