from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from core.workspace_engine import WorkspaceEngine, WorkspaceSettings
from core.workspace_state import WorkspaceStore


def test_notes_are_persistent_and_references_are_checked(tmp_path):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('Where is the model selected?', {})
    first = store.publish(run['id'], 'worker-1', 'Model selection', 'Evidence: core/session_builder.py')
    second = store.publish(run['id'], 'worker-2', 'Checked model selection', 'Confirmed the evidence',
                           [first['id']])
    reloaded = WorkspaceStore(tmp_path)
    assert reloaded.read_note(second['id'])['references'] == [first['id']]
    assert reloaded.notes('session_builder')[0]['id'] == first['id']
    with pytest.raises(ValueError, match='Unknown contribution'):
        store.publish(run['id'], 'worker-2', 'Bad reference', 'Unsupported claim', ['missing'])
    with pytest.raises(ValueError, match='identifier'):
        store.read_note('../../outside')
    assert len(store.notes()) == 2


def test_note_reads_reject_escaping_symlinks(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace')
    run = store.create_run('question', {})
    note = store.publish(run['id'], 'worker-1', 'Evidence', 'Original')
    path = store.root / 'notes' / (note['id'] + '.md')
    path.unlink()
    outside = tmp_path / 'outside.txt'
    outside.write_text('private')
    path.symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        store.read_note(note['id'])


# Real processes and filesystem records establish that work can cross workers
# before either completes. No LLM endpoint is required for this lifecycle test.
FAKE_WORKER = '''
import json, os, sys, time
from core.workspace_state import WorkspaceStore, atomic_write, now
spec = json.load(sys.stdin)
store = WorkspaceStore(spec['directory'])
run, worker = spec['run_id'], spec['worker']
store.update_worker(run, worker, status='running', pid=os.getpid(), started_at=now())
behavior = spec.get('overrides', {}).get('test_behavior', '')
if behavior == 'crash' and worker == 'worker-1':
    sys.exit(9)
if behavior == 'slow' and spec['phase'] != 'synthesis':
    time.sleep(30)
if behavior == 'slow-findings' and spec['phase'] != 'synthesis':
    store.publish(run, worker, 'Evidence before timeout', 'A useful intermediate result')
    time.sleep(30)
if spec['phase'] == 'synthesis':
    assert 'PRIVATE WORKER HISTORY' not in spec['message']
    assert 'observed peer checkpoint' in spec['message'] or behavior in ('crash', 'slow-findings')
    atomic_write(store.run_dir(run) / 'synthesis-input.txt', spec['message'])
    store.update_worker(run, worker, status='completed', last_text='A grounded answer', usage={})
elif behavior == 'crash':
    store.publish(run, worker, 'Surviving evidence', 'Useful partial finding')
    store.update_worker(run, worker, status='completed', last_text='Useful partial finding')
elif worker == 'worker-1':
    note = store.publish(run, worker, 'Checkpoint', 'The selected model comes from session_builder')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any('observed peer checkpoint' in n['content'] for n in store.notes(run_id=run)):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError('The second worker could not consume the first contribution')
    atomic_write(store.run_dir(run) / worker / 'private.txt', 'PRIVATE WORKER HISTORY')
    store.update_worker(run, worker, status='completed', last_text='First worker complete')
else:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        notes = store.notes('session_builder', run_id=run)
        if notes:
            store.publish(run, worker, 'Peer evidence checked', 'observed peer checkpoint',
                          [notes[0]['id']])
            break
        time.sleep(0.02)
    else:
        raise RuntimeError('No shared evidence arrived')
    store.update_worker(run, worker, status='completed', last_text='Second worker complete')
'''


def engine(tmp_path, behavior='', **settings):
    # The engine runs child processes from the application root, preserving imports.
    command = [sys.executable, '-c', FAKE_WORKER]
    options = WorkspaceSettings(repository=str(tmp_path), run_timeout_seconds=12,
                                synthesis_timeout_seconds=3, **settings)
    return WorkspaceEngine(WorkspaceStore(tmp_path / 'workspace'), options,
                           worker_command=command, overrides={'test_behavior': behavior})


def test_workers_exchange_evidence_before_completion_and_synthesis_is_fresh(tmp_path):
    runtime = engine(tmp_path)
    result = runtime.run('Where is model selection?')
    assert result['status'] == 'completed'
    assert result['answer'] == 'A grounded answer'
    notes = runtime.store.notes(run_id=result['id'])
    assert any(note['references'] for note in notes)
    assert (Path(result['directory']) / 'answer.md').read_text().strip() == result['answer']
    assert len(result['workers']) == 3
    later = runtime.run('Where is model selection?')
    assert later['id'] != result['id']
    assert len(runtime.store.notes()) == 4


def test_one_crashed_worker_leaves_a_partial_answer(tmp_path):
    result = engine(tmp_path, 'crash').run('Question')
    assert result['status'] == 'partial'
    assert result['answer'] == 'A grounded answer'
    failed = next(w for w in result['workers'] if w['worker'] == 'worker-1')
    assert failed['status'] == 'failed'
    assert 'exit 9' in failed['error']


def test_timeout_stops_workers_and_persists_failure(tmp_path):
    runtime = engine(tmp_path, 'slow')
    runtime.settings.run_timeout_seconds = 0.6
    runtime.settings.synthesis_timeout_seconds = 0.2
    started = time.monotonic()
    result = runtime.run('Question')
    assert time.monotonic() - started < 7
    assert result['status'] == 'failed'
    assert all(w['status'] == 'timed_out' for w in result['workers'])
    assert all(process.poll() is not None for process, _ in runtime._children.values())


def test_invalid_settings_do_not_start_a_run(tmp_path):
    runtime = engine(tmp_path, agents=0)
    with pytest.raises(ValueError):
        runtime.run('Question')
    assert not runtime.store.root.exists()


def test_timeout_preserves_findings_and_reserves_synthesis_time(tmp_path):
    runtime = engine(tmp_path, 'slow-findings')
    runtime.settings.run_timeout_seconds = 2
    runtime.settings.synthesis_timeout_seconds = 0.8
    result = runtime.run('Question')
    assert result['status'] == 'partial'
    assert result['answer'] == 'A grounded answer'
    assert [w['status'] for w in result['workers'] if w['worker'] != 'synthesis'] == [
        'timed_out', 'timed_out']


def test_interrupt_stops_all_started_workers(tmp_path, monkeypatch):
    runtime = engine(tmp_path, 'slow')

    def interrupt(*_args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runtime, '_wait', interrupt)
    result = runtime.run('Question')
    assert result['status'] == 'cancelled'
    assert all(p.poll() is not None for p, _ in runtime._children.values())
    assert all(w['status'] == 'cancelled' for w in result['workers'])


def test_launch_failure_does_not_leave_a_queued_worker(tmp_path):
    runtime = engine(tmp_path)
    runtime.worker_command = [str(tmp_path / 'missing-executable')]
    result = runtime.run('Question')
    assert result['status'] == 'failed'
    assert result['workers'][0]['status'] == 'failed'


def test_monitor_detects_dead_engine_without_modifying_saved_state(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('question', {})
    before = (store.run_dir(run['id']) / 'run.json').read_text()

    def missing(*_args):
        raise ProcessLookupError()

    monkeypatch.setattr(os, 'kill', missing)
    assert store.snapshot(run['id'])['status'] == 'interrupted'
    assert (store.run_dir(run['id']) / 'run.json').read_text() == before
