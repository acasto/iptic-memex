"""Read-only tailing across concurrent writes and task transitions."""
import json

from workspace.state import WorkspaceStore, now, write_json
from workspace.tasks import TaskStore
from workspace.watch import TaskWatch, event_line


def setup_task(tmp_path):
    store = WorkspaceStore(tmp_path / 'records')
    tasks = TaskStore(store)
    task = tasks.create('Investigate retry behavior', {'strategy': {'workflow': 'session'}})
    run = store.create_run(task['brief'], {'task_id': task['id']})
    task.update(status='running', attempts=[{'run_id': run['id'], 'started_at': now()}])
    tasks.save(task)
    write_json(store.run_dir(run['id']) / 'task-state.json', {
        'calls_used': 1, 'calls_limit': 8, 'agents': {
            'agent-1': {'status': 'running', 'generations': 1}}})
    agent = store.run_dir(run['id']) / 'agent-1'
    agent.mkdir()
    (agent / 'input-1.md').write_text('Read retry code')
    return store, tasks, task, run, agent


def test_watch_reads_complete_records_once_and_does_not_write(tmp_path):
    store, tasks, task, run, agent = setup_task(tmp_path)
    path = agent / 'events.jsonl'
    event = dict(event='tool_started', worker='agent-1', name='cmd',
                 time=now(), args={'command': 'rg', 'arguments': 'retry'})
    line = json.dumps(event).encode() + b'\n'
    path.write_bytes(line[:40])
    viewer = TaskWatch(store, task['id'])
    with tasks.lease(task['id']):
        before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
        lines, done = viewer.poll()
        assert not done
        assert 'Assignment: Read retry code' in '\n'.join(lines)
        assert 'Tool cmd' not in '\n'.join(lines)
        assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
        with path.open('ab') as stream:
            stream.write(line[40:])
        lines, done = viewer.poll()
        assert sum('Tool cmd' in line for line in lines) == 1
        assert 'arguments=retry' in '\n'.join(lines)
        assert viewer.poll()[0] == []
        assert 'elapsed' in '\n'.join(viewer.poll(heartbeat=True)[0])
        task['status'] = 'needs_input'
        tasks.save(task)
        write_json(store.run_dir(run['id']) / 'input-needed.json', {'question': 'Which source?'})
        lines, done = viewer.poll()
        assert done
        assert 'Input needed: Which source?' in lines


def test_tail_is_global_and_follow_discovers_new_agents(tmp_path):
    store, tasks, task, run, agent = setup_task(tmp_path)
    for number in range(5):
        store.event(run['id'], 'agent-1', 'assistant_message', {'text': f'older-{number}'})
    store.event(run['id'], 'agent-2', 'tool_started', {
        'name': 'file', 'args': {'mode': 'read', 'file': 'session.py'}})
    with tasks.lease(task['id']):
        viewer = TaskWatch(store, task['id'], tail=2)
        lines, _ = viewer.poll()
        assert 'older-0' not in '\n'.join(lines)
        assert 'older-4' in '\n'.join(lines)
        assert 'file=session.py' in '\n'.join(lines)
        store.event(run['id'], 'agent-3', 'assistant_message', {'text': 'New worker'})
        assert 'New worker' in '\n'.join(viewer.poll()[0])
        assert viewer.poll()[0] == []


def test_pending_then_new_attempt_and_terminal_replay(tmp_path):
    store = WorkspaceStore(tmp_path)
    tasks = TaskStore(store)
    task = tasks.create('Brief', {})
    viewer = TaskWatch(store, task['id'], tail=0)
    assert not viewer.poll()[1]
    run = store.create_run('Brief', {'task_id': task['id']})
    task.update(status='completed', attempts=[{'run_id': run['id'], 'started_at': now()}])
    tasks.save(task)
    store.event(run['id'], 'agent-1', 'assistant_message', {'text': 'Hidden with tail zero'})
    write_json(store.run_dir(run['id']) / 'task-result.json', {'answer': 'Saved answer'})
    lines, done = viewer.poll()
    assert done
    assert f'Attempt: {run["id"]}' in lines
    assert 'Hidden with tail zero' not in '\n'.join(lines)
    assert 'Result: Saved answer' in lines


def test_render_bounds_text_and_removes_terminal_controls():
    line = event_line(dict(event='tool_finished', name='cmd', worker='agent-1',
                           result={'output': '\x1b[2J\r\n' + 'x' * 9000}, status='completed'))
    assert '\x1b' not in line and '\r' not in line and '\n' not in line
    assert len(line) < 400
    expanded = event_line(dict(event='assistant_message', text='x' * 9000), verbose=True)
    assert 4000 < len(expanded) < 4100


def test_truncated_and_malformed_logs_do_not_break_following(tmp_path):
    store, tasks, task, run, agent = setup_task(tmp_path)
    path = agent / 'events.jsonl'
    path.write_text('not json\n')
    with tasks.lease(task['id']):
        viewer = TaskWatch(store, task['id'])
        assert 'unreadable event' in '\n'.join(viewer.poll()[0])
        path.write_text('')
        assert viewer.poll()[0] == []
        store.event(run['id'], 'agent-1', 'assistant_message', {'text': 'Recovered'})
        assert 'Recovered' in '\n'.join(viewer.poll()[0])
