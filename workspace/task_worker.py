"""Private task process: one execution owner for retained agents or legacy strategies."""

import json
from pathlib import Path
import signal
import sys
import time

from app.project import ProjectContext
from app.session_builder import SessionBuilder
from config_manager import ConfigManager
from workspace.experiment import ExecutionProfile, Strategy, validate_experiment
from workspace.state import WorkspaceStore, atomic_write, write_json
from workspace.task_service import TaskService


def run_session(spec, store, builder):
    profile, strategy = ExecutionProfile(**spec['profile']), Strategy(**spec['strategy'])
    project = ProjectContext(**{**spec['project'], 'root': Path(spec['project']['root']),
                               'config_path': Path(spec['project']['config_path'])
                               if spec['project'].get('config_path') else None})
    service = TaskService(store, spec['run_id'], builder, project, profile, strategy.prompt,
                          deadline=spec['deadline_monotonic'])
    try:
        root = service.start(spec['brief'])
        while True:
            state = service.status()
            if all(n['status'] not in ('starting', 'running') for n in state['agents']):
                break
            if time.monotonic() >= service.deadline:
                service.cancellation.cancel('Task deadline reached')
                break
            time.sleep(0.05)
        # Wait for terminal persistence, not merely the provider's last response.
        for node in list(service._agents.values()):
            node['done'].wait(timeout=max(0, min(1, service.deadline - time.monotonic())))
        state = service.status()
        root_node = service._agents[root]
        outcome = root_node['outcomes'][-1] if root_node['outcomes'] else {}
        answer = outcome.get('last_text') or ''
        status = outcome.get('status', 'failed')
        if service._persistence_error:
            status = 'failed'
        elif time.monotonic() >= service.deadline:
            status = 'timed_out'
        elif status == 'completed' and any(
                n['status'] != 'completed' or n['pending_inputs'] for n in state['agents']):
            status = 'partial'
        return {'status': status, 'answer': answer, 'error': service._persistence_error or outcome.get('error') or root_node.get('error'),
                'calls_used': state['calls_used'], 'agents': state['agents']}
    finally:
        service.close()


def run_legacy(spec, store):
    """Adapt existing experiments without making their policy a task requirement."""
    from workspace.engine import WorkspaceEngine, WorkspaceSettings
    from workspace.adaptive import AdaptiveEngine
    profile, strategy = ExecutionProfile(**spec['profile']), Strategy(**spec['strategy'])
    timeout = max(0.01, spec['deadline_monotonic'] - time.monotonic())
    settings = WorkspaceSettings(
        repository=spec['project']['root'], agents=strategy.agents,
        steps=max(1, (profile.calls - 1) // strategy.agents),
        model=profile.model, synthesis_model=profile.synthesis_model or profile.model,
        run_timeout_seconds=timeout, synthesis_timeout_seconds=min(90, timeout / 3),
        writes_policy=profile.writes,
        worker_prompt=strategy.prompt if strategy.workflow == 'fixed' else 'workspace/contribute')
    overrides = {'max_tokens': profile.max_tokens,
                 'active_tools_agent': ','.join(profile.tools) or '__none__',
                 'cmd_tool': 'assistant_docker_tool', 'use_mcp': False,
                 'project_snapshot': spec['project']}
    options = dict(config_file=spec.get('config_file'), overrides=overrides,
                   new_process_group=False)
    if strategy.workflow == 'fixed':
        engine = WorkspaceEngine(store, settings, **options)
    else:
        engine = AdaptiveEngine(store, settings, **options, total_calls=profile.calls,
                                strategy=strategy.workflow, lead_prompt=strategy.prompt)
    def started(record):
        store.update_run(spec['run_id'], experiment_run_id=record['id'])
    result = engine.run(spec['brief'], on_started=started)
    return {'status': result['status'], 'answer': result.get('answer', ''),
            'error': result.get('error'), 'experiment_run_id': result['id']}


def execute(spec, *, builder=None):
    store = WorkspaceStore(spec['directory'])
    profile, strategy = ExecutionProfile(**spec['profile']), Strategy(**spec['strategy'])
    validate_experiment(strategy, profile)
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        if strategy.workflow == 'session':
            builder = builder or SessionBuilder(ConfigManager(spec.get('config_file')))
            result = run_session(spec, store, builder)
        else:
            result = run_legacy(spec, store)
    except KeyboardInterrupt:
        result = {'status': 'cancelled', 'answer': '', 'error': 'Task process interrupted'}
    except Exception as exc:
        result = {'status': 'failed', 'answer': '', 'error': str(exc)}
    finally:
        signal.signal(signal.SIGTERM, previous)
    atomic_write(store.run_dir(spec['run_id']) / 'answer.md', result.get('answer', ''))
    write_json(store.run_dir(spec['run_id']) / 'task-result.json', result)
    return result


if __name__ == '__main__':
    execute(json.load(sys.stdin))
