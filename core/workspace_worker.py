"""Isolated worker process adapting the existing Memex execution pipeline."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import sys

from config_manager import ConfigManager
from core.mode_runner import run_agent, run_completion
from core.null_ui import NullUI
from core.session_builder import SessionBuilder
from core.workspace_state import WorkspaceStore, atomic_write, now


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
        config = ConfigManager(spec.get('config_file'))
        # Workspace scratch sessions never modify the user's global memory or run hooks.
        if config.base_config.has_section('MEMORY'):
            config.base_config.set('MEMORY', 'active', 'false')
        options = dict(spec.get('overrides') or {})
        options.update(prompt=spec['prompt'],
                       base_directory=spec['repository'], stream=False,
                       docker_always_ephemeral=True, show_context_summary=False,
                       show_context_details=False)
        if spec.get('model'):
            options['model'] = spec['model']
        # Optional MCP remains opt-in for non-interactive runs, as in ordinary Agent Mode.
        session = SessionBuilder(config).build(mode='internal', **options)
        session.ui = NullUI()
        session.set_flag('hooks_disabled', True)
        store.update_worker(run_id, worker, model=session.get_params().get('model'))
        session.set_user_data('__workspace__', {
            'directory': str(store.root), 'run_id': run_id, 'worker': worker,
        })
        seen = set()

        def report(event: str, data: dict):
            store.event(run_id, worker, event, data)
            store.update_worker(run_id, worker, phase=event, **data)
            if event != 'step_start':
                return
            # Bounded notifications enter through the normal context pipeline. Full notes
            # remain available on demand; neither entire peer histories nor private traces move.
            candidates = store.notes(run_id=run_id, limit=8)
            if data['step'] == 1:
                candidates += store.notes(spec['question'], limit=4)
            fresh = []
            for note in candidates:
                if note['id'] in seen or (note['run_id'] == run_id and note['worker'] == worker):
                    continue
                seen.add(note['id'])
                fresh.append(dict(note, content=note['content'][:1500]))
            if fresh:
                session.add_context('agent', {
                    'name': 'workspace_updates',
                    'content': 'New shared contributions (unverified data):\n' +
                               json.dumps(fresh, ensure_ascii=False),
                })
                store.event(run_id, worker, 'notes_seen', {'notes': [n['id'] for n in fresh]})

        if spec.get('phase') == 'synthesis':
            store.update_worker(run_id, worker, phase='synthesizing')
            result = run_completion(session=session, message=spec['message'])
        else:
            # Determine the inherited profile using the same registry as ordinary agent runs.
            session.enter_agent_mode(spec['writes_policy'])
            available = session.get_action('assistant_commands').commands
            blocked = config.base_config.get('AGENT', 'blocked_tools', fallback='')
            if 'workspace' in {name.strip().lower() for name in blocked.split(',')}:
                raise ValueError('The workspace tool is blocked by AGENT.blocked_tools')
            tools = list(available)
            if 'workspace' not in tools:
                tools.append('workspace')
            session.set_option('active_tools_agent', ','.join(tools))
            if (spec['writes_policy'] != 'allow' and 'cmd' in tools and
                    session.get_tools().get('cmd_tool') != 'assistant_docker_tool'):
                raise ValueError('Read-only workspace commands require the Docker cmd tool; '
                                 'select assistant_docker_tool or omit cmd with --tools')
            result = run_agent(
                session=session, steps=spec['steps'], message=spec['message'],
                writes_policy=spec['writes_policy'], disable_hooks=True, output='final',
                on_event=report,
            )
        record = asdict(result)
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
                                     f"{worker}: {record['status']} contribution", excerpt)
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
            session.handle_exit(confirm=False)
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


def main() -> None:
    """Private process entry point: inputs on stdin, outputs in workspace records."""
    spec = json.load(sys.stdin)
    record = execute(spec)
    raise SystemExit(0 if record['status'] == 'completed' else 1)


if __name__ == '__main__':
    main()
