"""Adaptive decisions, shared budgets and process cleanup with an offline model."""

import json
from pathlib import Path
import sys

import pytest

from workspace.adaptive import AdaptiveEngine
from workspace.engine import WorkspaceSettings
from workspace.state import WorkspaceStore


BOOTSTRAP = r'''
from configparser import ConfigParser
from pathlib import Path
import os, runpy, time
from component_registry import ComponentRegistry
from config_manager import ConfigManager
from workspace import runtime
config = ConfigManager.__new__(ConfigManager)
config.base_config = ConfigParser()
config.base_config.read_dict({
 'DEFAULT': {'template_handler': 'none'},
 'AGENT': {'default_model': 'offline', 'active_tools': 'file'},
 'Offline': {'active': 'true'},
})
config.models = ConfigParser()
config.models.read_dict({'offline': {'provider': 'Offline', 'model_name': 'offline'}})
class Provider:
 def __init__(self, session): self.session, self.calls, self.tools = session, 0, []
 def chat(self):
  self.calls += 1
  self.tools = []
  if self.session.get_params().get('test_helper'):
   return 'Helper evidence'
  research = getattr(self.session, 'research', None)
  access = getattr(self.session, 'workspace', None)
  def tool(name, **args):
   self.tools.append({'id': str(self.calls) + '-' + str(len(self.tools)),
                      'name': name, 'arguments': args})
  if research:
   if self.calls == 1 and self.session.get_params().get('use_helper'):
    helper = self.session.run_internal_completion(message='Helper task', overrides={'test_helper': True})
    assert helper.status == 'completed'
   state = research.state
   own = research.store.notes(run_id=research.run_id, exclude_worker='branch-1')
   if self.session.get_params().get('endless'):
    tool('research', operation='status')
   elif self.calls == 1:
    tool('research', operation='brief', content='Inspect the behavior and independently check a consequential discrepancy')
    tool('research', operation='question', question='Does the original source support the claim?', purpose='Could change our recommendation')
   elif self.calls == 2:
    tool('workspace', operation='publish', title='Initial evidence', content='Initial original source finding', publication_id='initial')
   elif self.calls == 3 and state['strategy'] != 'single':
    question = next(iter(state['questions']))
    tool('research', operation='investigate', question_id=question, references=own[0]['id'], calls=3)
   elif self.calls == 4 and state['strategy'] != 'single':
    if self.session.get_params().get('crash_lead'): os._exit(9)
    tool('research', operation='wait')
   elif self.calls == 5 and state['strategy'] != 'single':
    evidence = research.store.notes(run_id=research.run_id)
    peer = next(n for n in evidence if n['worker'] == 'branch-1')
    tool('research', operation='question', question_id=next(iter(state['questions'])),
         status='resolved', references=peer['id'], content='Changed recommendation after independent source check')
   else:
    tool('research', operation='conclude', status='sufficient', references=own[0]['id'],
         content='Source evidence addresses the brief; recommendation revised based on focused check')
   return ''
  if access:
   if self.session.get_params().get('slow_branch'):
    time.sleep(30)
   if self.calls == 1:
    tool('file', mode='read', file='source.txt')
   elif self.calls == 2:
    tool('workspace', operation='publish', title='Independent check',
         content='Original source check changes the initial recommendation', publication_id='checked')
   else:
    return 'Focused result %%DONE%%'
   return ''
  message = self.session.get_context('chat').get('all')[0]['message']
  assert 'Research goal' in message
  return 'Grounded adaptive answer'
 def get_tool_calls(self): return self.tools
 def get_finish_reason(self): return 'tool_calls' if self.tools else 'stop'
 def cleanup(self):
  Path(self.session.get_option('TOOLS', 'base_directory'), 'closed-' + self.session.session_uid).touch()
runtime.ConfigManager = lambda filename: config
ComponentRegistry.load_provider_class = lambda self, name: Provider
runpy.run_module('workspace.worker', run_name='__main__')
'''


def engine(tmp_path, *, calls=14, strategy='adaptive', **overrides):
    (tmp_path / 'source.txt').write_text('Original source')
    settings = WorkspaceSettings(repository=str(tmp_path), model='offline',
                                 run_timeout_seconds=8, synthesis_timeout_seconds=2)
    return AdaptiveEngine(WorkspaceStore(tmp_path / 'workspace'), settings,
                          total_calls=calls, strategy=strategy,
                          worker_command=[sys.executable, '-c', BOOTSTRAP], overrides=overrides)


def test_discovery_requests_a_branch_and_changes_the_question_before_synthesis(tmp_path):
    runtime = engine(tmp_path)
    result = runtime.run('Investigate the source')
    assert result['status'] == 'completed', result
    research = result['research']
    assert len(research['branches']) == 1
    assert next(iter(research['questions'].values()))['status'] == 'resolved'
    assert research['branches'][0]['calls'] == 3
    assert research['budget']['lead_calls'] == 6
    assert research['budget']['used'] + research['budget']['synthesis_calls'] == 10
    assert research['budget']['reserved'] == 0
    decisions = research['decisions']
    assert any(d['action'] == 'branch_requested' and d['evidence'] for d in decisions)
    assert any(d['action'] == 'goal_assessed' for d in decisions)
    assert all(worker.get('tool_failures', 0) == 0 for worker in result['workers'])
    directory = Path(result['directory'])
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    replay = runtime.synthesize(result['id'])
    assert replay['status'] == 'completed', replay
    assert replay['synthesis_input_sha256'] == result['synthesis_input_sha256']
    assert {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before
    assert len(list(tmp_path.glob('closed-*'))) == 4


def test_single_agent_has_same_state_and_tools_without_branches(tmp_path):
    result = engine(tmp_path, strategy='single').run('Investigate')
    assert result['status'] == 'completed', result
    assert not result['research']['branches']
    assert result['research']['budget']['lead_calls'] == 3


def test_call_exhaustion_is_partial_and_synthesis_counts_against_the_same_ceiling(tmp_path):
    result = engine(tmp_path, calls=5, endless=True).run('Investigate')
    assert result['status'] == 'failed'  # No findings to synthesize.
    state = result['research']
    assert state['budget']['used'] == 4
    assert state['budget']['synthesis_calls'] == 0
    assert state['assessment']['status'] == 'partial'
    assert not state['branches']


def test_timeout_cleans_up_focused_branch_and_preserves_partial_evidence(tmp_path):
    runtime = engine(tmp_path, slow_branch=True)
    runtime.settings.run_timeout_seconds = 5
    runtime.settings.synthesis_timeout_seconds = 1
    result = runtime.run('Investigate')
    assert result['status'] == 'partial', result
    assert result['research']['budget']['reserved'] == 0
    assert len(list(tmp_path.glob('closed-*'))) == 3
    assert all(w['status'] != 'running' for w in result['workers'])


def test_input_validation_happens_before_creating_run_records(tmp_path):
    runtime = engine(tmp_path)
    runtime.contexts = [('file', str(tmp_path / 'missing.txt'))]
    with pytest.raises(ValueError, match='Attachment'):
        runtime.run('Question')
    assert not runtime.store.root.exists()


def test_lead_crash_does_not_leave_an_independent_branch_running(tmp_path):
    result = engine(tmp_path, crash_lead=True, slow_branch=True).run('Investigate')
    assert result['status'] == 'partial', result
    assert all(worker['status'] not in ('running', 'queued') for worker in result['workers'])
    assert result['research']['budget']['reserved'] == 0
    branch = result['research']['branches'][0]
    assert branch['calls'] <= branch['allowance']


def test_nested_model_helpers_consume_the_same_research_budget(tmp_path):
    result = engine(tmp_path, strategy='single', use_helper=True).run('Investigate')
    assert result['status'] == 'completed', result
    budget = result['research']['budget']
    assert budget['helper_calls'] == 1
    assert budget['lead_calls'] == 3
    assert budget['used'] + budget['synthesis_calls'] == 5
