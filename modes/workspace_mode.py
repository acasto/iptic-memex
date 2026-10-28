"""CLI adapter for persistent workspace runs and independent status inspection."""

from __future__ import annotations

import json
from pathlib import Path
import time

import click

from core.workspace_engine import WorkspaceEngine, WorkspaceSettings
from core.workspace_state import WorkspaceStore, TERMINAL_STATES


def _paths(ctx, directory: str | None, repository: str | None = None):
    cfg = ctx.obj['CONFIG_MANAGER'].base_config
    repo = repository or ctx.obj['OPTIONS'].get('base_directory')
    repo = repo or cfg.get('TOOLS', 'base_directory', fallback='working')
    repo = Path.cwd() if repo in ('working', '.') else Path(repo).expanduser().resolve()
    location = directory or cfg.get('WORKSPACE', 'directory', fallback='.memex-workspace')
    location = Path(location).expanduser()
    if not location.is_absolute():
        location = repo / location
    return repo, WorkspaceStore(location)


def _render_status(snapshot: dict) -> str:
    lines = [f"Run: {snapshot['id']} — {snapshot['status']} ({snapshot['phase']})",
             f"Question: {snapshot['question']}", f"Saved in: {snapshot['directory']}"]
    for worker in snapshot['workers']:
        step = f"step {worker['step']}/{worker.get('max_steps', '?')}" if 'step' in worker else ''
        action = worker.get('tool') if worker.get('phase') == 'tool_start' else worker.get('phase')
        lines.append(f"  {worker['worker']}: {worker.get('status', '?')}  {step}  "
                     f"{action or ''}  {worker.get('model') or ''}")
        if worker.get('error'):
            lines.append(f"    {worker['error']}")
    if snapshot.get('error'):
        lines.append(f"Notice: {snapshot['error']}")
    return '\n'.join(lines)


def register_workspace_commands(cli, task_message) -> None:
    """Register thin CLI entry points without changing other modes."""

    @cli.group()
    def workspace():
        """Run agents against persistent shared findings; inspect saved activity."""

    @workspace.command('run')
    @click.argument('question', required=False)
    @click.option('--message', default=None, help='Question (alternative to the argument).')
    @click.option('--stdin-message', is_flag=True, help='Read the question from stdin.')
    @click.option('--directory', type=click.Path(path_type=str), help='Persistent workspace directory.')
    @click.option('--repository', type=click.Path(exists=True, file_okay=False),
                  help='Repository exposed to the agents (read-only by default).')
    @click.option('--agents', type=click.IntRange(1, 32), default=None)
    @click.option('--steps', type=click.IntRange(min=1), default=None, help='Model turns per worker.')
    @click.option('--timeout', type=click.FloatRange(min=0, min_open=True), default=None,
                  help='Total run timeout in seconds, including reserved synthesis time.')
    @click.option('--synthesis-timeout', type=click.FloatRange(min=0, min_open=True), default=None,
                  help='Seconds reserved for synthesis within the total run timeout.')
    @click.option('--synthesis-model', default=None, help='Optional final-answer model override.')
    @click.option('--json', 'json_output', is_flag=True, help='Return the saved run and answer as JSON.')
    @click.pass_context
    def run(ctx, question, message, stdin_message, directory, repository, agents, steps,
            timeout, synthesis_timeout, synthesis_model, json_output):
        """Investigate a question with concurrent agents and a fresh final-answer pass."""
        try:
            if ctx.obj.get('FILES'):
                raise click.UsageError(
                    'Workspace mode uses --repository; file attachments are not supported.')
            supplied = [value for value in (question, message, ctx.obj.get('MESSAGE'))
                        if value is not None]
            if len(supplied) > 1:
                raise click.UsageError('Provide the question once, as an argument or --message.')
            task = task_message(ctx, supplied[0] if supplied else None,
                                stdin_message=stdin_message)
            if not task:
                raise click.UsageError('Provide a question or use --stdin-message.')
            repo, store = _paths(ctx, directory, repository)
            cfg = ctx.obj['CONFIG_MANAGER'].base_config
            options = dict(ctx.obj['OPTIONS'])

            def setting(name, default):
                return cfg.get('WORKSPACE', name, fallback=default)

            settings = WorkspaceSettings(
                repository=str(repo), agents=agents if agents is not None else int(setting('agents', 2)),
                steps=steps if steps is not None else int(options.pop('steps',
                                                        setting('max_steps_per_agent', 12))),
                model=options.get('model'),
                synthesis_model=synthesis_model or setting('synthesis_model', '') or None,
                run_timeout_seconds=(timeout if timeout is not None
                                     else float(setting('run_timeout_seconds', 300))),
                synthesis_timeout_seconds=(synthesis_timeout if synthesis_timeout is not None
                                          else float(setting('synthesis_timeout_seconds', 90))),
                writes_policy=options.pop('agent_writes', setting('writes_policy', 'deny')),
                worker_prompt=options.pop('prompt', setting('worker_prompt', 'workspace/contribute')),
                synthesis_prompt=setting('synthesis_prompt', 'workspace/synthesize'),
                synthesis_max_chars=int(setting('synthesis_max_chars', 48000)),
            )
            default_model = (cfg.get('AGENT', 'default_model', fallback=None)
                             or cfg.defaults().get('default_model'))
            for model in (settings.model or default_model, settings.synthesis_model):
                if model and not ctx.obj['CONFIG_MANAGER'].create_session_config().normalize_model_name(model):
                    raise ValueError(
                        f"Unknown model '{model}'; configure it in models.ini or select -m.")
            options.setdefault('max_tokens', int(setting('max_tokens', 4096)))
            options.setdefault('use_mcp', False)
            engine = WorkspaceEngine(store, settings, config_file=ctx.obj.get('CONFIG_FILE'),
                                     overrides=options)

            def started(record):
                click.echo(f"Workspace run: {record['id']}\nSaved in: {store.run_dir(record['id'])}",
                           err=True)

            result = engine.run(task, on_started=started)
            if json_output:
                click.echo(json.dumps(result, ensure_ascii=False))
            else:
                click.echo(result['answer'])
                if result['status'] != 'completed':
                    click.echo(f"Run status: {result['status']}; inspect workspace status for details.",
                               err=True)
            if result['status'] == 'cancelled':
                ctx.exit(130)
            if result['status'] not in ('completed', 'partial'):
                ctx.exit(1)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @workspace.command('status')
    @click.argument('run_id', required=False)
    @click.option('--directory', type=click.Path(path_type=str), help='Persistent workspace directory.')
    @click.option('--watch', is_flag=True, help='Refresh until the run finishes; Ctrl-C closes the monitor.')
    @click.option('--interval', type=click.FloatRange(min=0.1), default=1.0, show_default=True)
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def status(ctx, run_id, directory, watch, interval, json_output):
        """Inspect a run (newest by default) without starting models."""
        try:
            if (ctx.obj.get('FILES') or ctx.obj.get('MESSAGE') is not None
                    or ctx.obj.get('STDIN_MESSAGE')):
                raise click.UsageError('Task inputs require workspace run, not status.')
            _, store = _paths(ctx, directory)
            last = None
            while True:
                snapshot = store.snapshot(run_id)
                run_id = snapshot['id']
                rendered = (json.dumps(snapshot, ensure_ascii=False) if json_output
                            else _render_status(snapshot))
                if rendered != last:
                    if watch and not json_output and click.get_text_stream('stdout').isatty():
                        click.clear()
                    click.echo(rendered)
                    last = rendered
                if not watch or snapshot['status'] in TERMINAL_STATES:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            return
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
