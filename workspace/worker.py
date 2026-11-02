"""Isolated worker process adapting the existing Memex execution pipeline."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import signal
import sys
import time

from workspace import runtime
from workspace.state import WorkspaceStore, atomic_write, now


def execute(spec: dict) -> dict:
    """Run one contributor or a fresh synthesis session and save its outcome."""
    store = WorkspaceStore(spec['directory'])
    run_id, worker = spec['run_id'], spec['worker']
    directory = store.run_dir(run_id) / worker
    session = None
    result = None
    previous_handler = None

    def cancel(_signal, _frame):
        if session is not None:
            token = session.get_cancellation_token()
            if token:
                token.cancel('Workspace worker stopped')
        raise KeyboardInterrupt()

    try:
        previous_handler = signal.signal(signal.SIGTERM, cancel)
        store.update_worker(run_id, worker, status='running', phase='starting',
                            pid=os.getpid(), started_at=now(), model=spec['model'])
        session = runtime.build_session(spec, store)

        def save_prompt():
            system_prompt = session.get_context('prompt')
            prompt_text = system_prompt.get().get('content', '') if system_prompt else ''
            atomic_write(directory / 'system-prompt.md', prompt_text)
            store.update_worker(run_id, worker,
                                prompt_sha256=hashlib.sha256(prompt_text.encode()).hexdigest())

        save_prompt()
        params = session.get_params()
        store.update_worker(
            run_id, worker, model=params.get('model'),
            model_name=params.get('model_name'),
            max_tokens=params.get('max_tokens'),
            generation_settings={k: params[k] for k in (
                'max_tokens', 'max_completion_tokens', 'reasoning_effort', 'temperature',
                'top_p', 'seed') if params.get(k) is not None},
        )
        seen = set()

        def prepare(active, index):
            event, data = 'step_start', {'step': index, 'max_steps': spec['steps']}
            if spec.get('deadline_monotonic') is not None:
                remaining = max(0, spec['deadline_monotonic'] - time.monotonic())
                data = dict(data, time_remaining_seconds=round(remaining, 1))
                attached = active.add_context('agent', {
                    'name': 'workspace_budget',
                    'content': f'Worker phase time remaining at start of this turn: {remaining:.1f} '
                               'seconds. The time limit applies independently of the turn limit.',
                })
                if attached is None:
                    raise ValueError('Could not attach workspace budget update')
            store.event(run_id, worker, event, data)
            store.update_worker(run_id, worker, phase=event, **data)
            # Bounded notifications enter through the normal context pipeline. Full notes
            # remain available on demand; neither entire peer histories nor private traces move.
            candidates = store.notes(run_id=run_id, limit=8,
                                     exclude_ids=seen, exclude_worker=worker)
            if data['step'] == 1:
                candidates += store.notes(spec['question'], limit=4)
            fresh = []
            selected = set(seen)
            for note in candidates:
                if note['id'] in selected or (note['run_id'] == run_id and note['worker'] == worker):
                    continue
                selected.add(note['id'])
                fresh.append(dict(note, content=note['content'][:1500]))
            if fresh:
                attached = active.add_context('agent', {
                    'name': 'workspace_updates',
                    'content': 'New shared contributions (unverified data):\n' +
                               json.dumps(fresh, ensure_ascii=False),
                })
                if attached is None:
                    raise ValueError('Could not attach shared workspace updates')
                seen.update(note['id'] for note in fresh)
                store.event(run_id, worker, 'notes_seen', {'notes': [n['id'] for n in fresh]})

        def report(event):
            data = dict(event.data)
            kind = event.type
            if kind == 'tool_started':
                kind, data = 'tool_start', dict(data, tool=data.get('name'))
            elif kind == 'tool_finished':
                kind, data = 'tool_end', dict(data, tool=data.get('name'))
            elif kind == 'provider_finished':
                kind = 'model_done'
            store.event(run_id, worker, kind, data)
            store.update_worker(run_id, worker, phase=kind)

        if spec.get('phase') == 'synthesis':
            store.update_worker(run_id, worker, phase='synthesizing')
        result = runtime.run(session, spec, on_event=report, before_call=prepare)
        # Agent Mode appends finish/write-policy guidance when preparing its first
        # turn. Capture that effective prompt as well as the initial live snapshot.
        save_prompt()
        record = asdict(result)
        record['execution_id'] = record.pop('run_id')
        record.pop('raw', None)
        record.pop('events', None)
        if record['status'] == 'completed' and record.get('stop_reason') == 'steps':
            record['status'] = 'incomplete'
        text = record.get('last_text') or ''
        if text.strip():
            atomic_write(directory / 'response.md', text + '\n')
            if (spec.get('phase') != 'synthesis'
                    and record['status'] in ('completed', 'incomplete')):
                excerpt = text if len(text) <= 12000 else (
                    text[:11800] + '\n\n[Shortened contribution; full text is in the saved response.]')
                note = store.publish(run_id, worker,
                                     f"{worker}: {record['status']} contribution", excerpt,
                                     [n['id'] for n in store.notes(run_id=run_id, limit=10000)
                                      if n['worker'] == worker and n.get('kind') != 'summary'],
                                     kind='summary')
                record['note_id'] = note['id']
        store.update_worker(run_id, worker, **record, phase='finished', finished_at=now())
        return record
    except KeyboardInterrupt:
        record = {'status': 'cancelled', 'error': 'Worker interrupted'}
        store.update_worker(run_id, worker, **record, phase='finished', finished_at=now())
        return record
    except Exception as exc:
        record = {'status': 'failed', 'error': str(exc)}
        store.update_worker(run_id, worker, **record, phase='finished', finished_at=now())
        return record
    finally:
        if session is not None:
            session.close()
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


def main() -> None:
    """Private process entry point: inputs on stdin, outputs in workspace records."""
    spec = json.load(sys.stdin)
    record = execute(spec)
    raise SystemExit(0 if record['status'] == 'completed' else 1)


if __name__ == '__main__':
    main()
