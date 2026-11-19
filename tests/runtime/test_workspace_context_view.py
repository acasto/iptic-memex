"""Exercise read compaction through actual tool calls, archive access and provider views."""
import json

import pytest

from component_registry import ComponentRegistry
from workspace.agent_tool import WorkspaceAgentsToolAction
from workspace.context_view import ReadCompaction
from workspace.experiment import ExecutionProfile
from workspace.watch import event_line
from tests.runtime.test_task_service import make_service, finish


def test_compact_read_retrieve_and_restore(builder, tmp_path, monkeypatch):
    evidence = 'specific early evidence\n' * 200
    (tmp_path / 'evidence.txt').write_text(evidence)
    service = make_service(builder, tmp_path, ExecutionProfile(
        model='worker-model', tools=('file',), compact_reads=True))
    observed = []

    class Provider:
        def __init__(self, session):
            self.session = session
            self.tools = []

        def chat(self):
            turns = self.session.get_context('chat').get()
            bodies = [t['message'] for t in turns if t.get('tool_call_id') == 'read']
            observed.append(bodies)
            step = len(observed)
            if step == 1:
                name, args, call_id = 'file', {'mode': 'read', 'file': 'evidence.txt'}, 'read'
            elif step == 2:
                name, args, call_id = 'compact_reads', {
                    'file': 'evidence.txt', 'summary': 'Found evidence.'}, 'compact'
            elif step == 3:
                receipt = json.loads(next(t['message'] for t in turns
                                          if t.get('tool_call_id') == 'compact'))
                name, args, call_id = 'agents', {'operation': 'history_read', 'history_read': '', 'target': 'self',
                    'start': receipt['sources'][0]['history_index'], 'limit': 1}, 'recall'
            elif step == 4:
                name, args, call_id = 'agents', {'operation': 'restore_reads'}, 'restore'
            else:
                self.tools = []
                return 'Finished'
            self.tools = [{'id': call_id, 'name': name, 'arguments': args}]
            return ''

        def get_tool_calls(self):
            return self.tools

        def get_usage(self):
            return {'total_in': 99000, 'turn_in': len(observed) * 10, 'turn_cached': 2}

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *_: Provider)
    try:
        agent = service.start('Preserve this user decision.')
        assert finish(service, agent)['agents'][0]['status'] == 'completed'
        assert evidence in observed[1][0] and observed[1] == observed[4]
        assert 'Agent note' in observed[2][0] and evidence not in observed[2][0]
        assert observed[2] == observed[3]
        session = service._agents[agent]['host'].session
        raw = session.get_context('chat').get('all')
        assert raw[0]['message'] == 'Preserve this user decision.'
        recall = json.loads(next(t['message'] for t in raw if t.get('tool_call_id') == 'recall'))
        assert evidence in json.loads(recall['content'])[0]['message']
        events = [json.loads(line) for line in
                  (service.store.run_dir(service.run_id) / agent / 'events.jsonl').read_text().splitlines()]
        before = [e for e in events if e['event'] == 'provider_started']
        assert before[1]['estimated_input_tokens'] > before[2]['estimated_input_tokens']
        assert 'file evidence.txt' in event_line(before[1])
        after = [e for e in events if e['event'] == 'provider_finished']
        assert [e['turn_in'] for e in after] == [10, 20, 30, 40, 50]
        assert '2 cached' in event_line(after[-1])
        session.read_compaction = None
        assert 'restore_reads' not in WorkspaceAgentsToolAction.tool_spec(session)['schema']['properties']['operation']['enum']
        assert not WorkspaceAgentsToolAction(session).start({'operation': 'restore_reads'}).payload['ok']
    finally:
        service.close()


def test_compaction_only_accepts_whole_successful_text_read_batches():
    def call(i):
        return {'id': str(i), 'name': 'file', 'arguments': {'mode': 'read', 'file': str(i)}}
    def result(i):
        return {'role': 'tool', 'tool_call_id': str(i), 'meta': {'id': str(i)},
                'context': [{'type': 'file'}], 'message': 'original' * 500}
    batch = {'role': 'assistant', 'tool_calls': [call(1), call(2)]}
    view = ReadCompaction()
    for last in ([], [dict(result(2), is_error=True)], [dict(result(2), role='user')],
                 [dict(result(2), context=[{'type': 'image'}])], [result(3)]):
        with pytest.raises(ValueError, match='No completed'):
            view.compact([batch, result(1), *last], '1', 'Note')
    turns = [batch, result(1), result(2), {'role': 'user', 'message': 'decision'}]
    receipt = view.compact(turns, '2', 'Note')
    projected = view.project(turns)
    assert [r['history_index'] for r in receipt['sources']] == [1, 2]
    assert [r['tool_call_id'] for r in projected[1:3]] == ['1', '2']
    assert projected[0] is batch and projected[-1] is turns[-1]
    assert all(row['context'] == [] for row in projected[1:3])
