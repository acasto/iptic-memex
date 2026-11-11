"""CLI adapters for task experiments, separate from session execution."""

import json
from pathlib import Path

import click

from app.project import resolve_project
from workspace.experiment import load_profile, load_strategy, snapshot, validate_experiment
from workspace.task_runner import TaskRunner
from workspace.tasks import TaskStore
from workspace.state import read_json


def register_task_commands(workspace, paths, task_message):
    @workspace.group('task')
    def task():
        """Run selectable experiments and inspect durable task attempts."""

    def result_output(ctx, result, json_output):
        click.echo(json.dumps(result, ensure_ascii=False) if json_output else result.get('answer', ''))
        if result['status'] != 'completed':
            click.echo(f"Task status: {result['status']}" +
                       (f" — {result['error']}" if result.get('error') else ''), err=True)
        if result['status'] == 'cancelled':
            ctx.exit(130)
        if result['status'] not in ('completed', 'partial'):
            ctx.exit(1)

    def started(record):
        click.echo(f"Task: {record['task_id']}\nAttempt: {record['run_id']}\n"
                   f"Saved in: {record['directory']}", err=True)

    @task.command('start')
    @click.argument('question', required=False)
    @click.option('--brief', type=click.Path(exists=True, dir_okay=False))
    @click.option('--strategy', default='directed', help='directed, fixed, adaptive, single, or TOML file.')
    @click.option('--profile', type=click.Path(exists=True, dir_okay=False), default=None)
    @click.option('--directory', default=None, help='Workspace records directory.')
    @click.option('--calls', type=click.IntRange(min=1), default=None)
    @click.option('--timeout', type=click.FloatRange(min=0, min_open=True), default=None)
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def start(ctx, question, brief, strategy, profile, directory, calls, timeout, json_output):
        """Start a foreground task; strategy and profile files are independent."""
        try:
            options = dict(ctx.obj['OPTIONS'])
            if 'steps' in options or 'prompt' in options:
                raise ValueError('Task experiments use --calls and --strategy, not --steps/--prompt')
            if ctx.obj.get('FILES'):
                raise ValueError('Use --brief for the task; refer to project sources in the brief')
            if brief and (question is not None or ctx.obj.get('MESSAGE') is not None
                          or ctx.obj.get('STDIN_MESSAGE')):
                raise ValueError('Supply the task once: question, --brief, or message/stdin')
            if question is not None and ctx.obj.get('MESSAGE') is not None:
                raise ValueError('Supply the task once')
            text = Path(brief).read_text(encoding='utf-8') if brief else task_message(ctx, question)
            repo, store = paths(ctx, directory)
            if options.get('base_directory') and options.get('project_root'):
                base = Path(options['base_directory']).expanduser().resolve()
                if base != repo:
                    raise ValueError('Task --base-dir and --project must select the same root')
            project = resolve_project(repo)
            cfg = ctx.obj['CONFIG_MANAGER'].base_config
            selected, strategy_origin = load_strategy(strategy)
            default_model = project.agent_model or cfg.get('AGENT', 'default_model', fallback=None)
            tool_option = options.get('active_tools_agent')
            explicit_tools = (None if tool_option is None else
                              () if tool_option == '__none__' else tuple(tool_option.split(',')))
            effective, profile_origin = load_profile(profile, defaults={
                'model': default_model,
            }, overrides={'model': options.get('model'), 'tools': explicit_tools,
                          'calls': calls, 'timeout': timeout,
                          'writes': options.get('agent_writes'),
                          'max_tokens': int(options['max_tokens']) if options.get('max_tokens') else None})
            validate_experiment(selected, effective)
            config = ctx.obj['CONFIG_MANAGER'].create_session_config()
            for model in (effective.model, effective.synthesis_model):
                if model and not config.normalize_model_name(model):
                    raise ValueError(f'Unknown model: {model}')
            if not effective.model:
                raise ValueError('Select a model in the profile, project, configuration, or with -m')
            runner = TaskRunner(store)
            record = runner.create(text, project, snapshot(
                selected, effective, strategy_origin=strategy_origin, profile_origin=profile_origin),
                config_file=ctx.obj.get('CONFIG_FILE'))
            result_output(ctx, runner.run(record['id'], on_started=started), json_output)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @task.command('list')
    @click.option('--directory', default=None)
    @click.pass_context
    def list_tasks(ctx, directory):
        """List saved tasks without starting models."""
        _, store = paths(ctx, directory)
        for record in TaskStore(store).list():
            click.echo(f"{record['id']}  {record['status']}  {record['brief'][:100].splitlines()[0]}")

    @task.command('inspect')
    @click.argument('task_id')
    @click.option('--directory', default=None)
    @click.pass_context
    def inspect_task(ctx, task_id, directory):
        """Show task inputs, attempts, and latest saved state without model calls."""
        try:
            _, store = paths(ctx, directory)
            record = TaskStore(store).inspect(task_id)
            if record['attempts']:
                run_id = record['attempts'][-1]['run_id']
                record['latest_state'] = read_json(store.run_dir(run_id) / 'task-state.json')
                record['latest_result'] = read_json(store.run_dir(run_id) / 'task-result.json')
                attempt = read_json(store.run_dir(run_id) / 'run.json')
                if experiment_run := attempt.get('experiment_run_id'):
                    record['experiment'] = store.snapshot(experiment_run)
            click.echo(json.dumps(record, ensure_ascii=False, indent=2))
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @task.command('cancel')
    @click.argument('task_id')
    @click.option('--directory', default=None)
    @click.pass_context
    def cancel_task(ctx, task_id, directory):
        """Request cancellation of a live task from another terminal."""
        try:
            _, store = paths(ctx, directory)
            tasks = TaskStore(store)
            record = tasks.inspect(task_id)
            if record['status'] not in ('pending', 'running'):
                click.echo(f"Task is already {record['status']}; no cancellation needed.")
                return
            (tasks.directory(task_id) / 'cancel').touch()
            click.echo('Cancellation requested; the supervisor will stop the task.')
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @task.command('repeat')
    @click.argument('task_id')
    @click.option('--directory', default=None)
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def repeat(ctx, task_id, directory, json_output):
        """Explicitly execute the saved brief/settings again as a NEW attempt."""
        try:
            if ctx.obj.get('MESSAGE') or ctx.obj.get('FILES') or ctx.obj.get('STDIN_MESSAGE'):
                raise ValueError('Repeat uses the saved task inputs')
            if set(ctx.obj['OPTIONS']) - {'project_root', 'base_directory'}:
                raise ValueError('Repeat uses saved settings; start a new task to change them')
            repo, store = paths(ctx, directory)
            record = TaskStore(store).read(task_id)
            if str(repo) != record['spec']['project']['root']:
                raise ValueError('Select the task project root explicitly with --project')
            result_output(ctx, TaskRunner(store).run(task_id, on_started=started), json_output)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
