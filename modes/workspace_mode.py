"""CLI adapter for persistent workspace runs and independent status inspection."""

from __future__ import annotations

import json
from pathlib import Path
import time

import click

from workspace.engine import WorkspaceEngine, WorkspaceSettings
from workspace.report import run_report
from workspace.state import WorkspaceStore, TERMINAL_STATES


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
    if limit := _render_limits(snapshot):
        lines.append(limit)
    if research := snapshot.get('research'):
        budget = research['budget']
        lines.append(f"Research: {research['strategy']}; calls used/reserved/total: "
                     f"{budget['used'] + budget['synthesis_calls']}/"
                     f"{budget['reserved']}/{budget['total_calls']}")
        for question in research['questions'].values():
            lines.append(f"  {question['status']}: {question['question']}")
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


def _render_limits(record: dict) -> str:
    total = record.get('run_timeout_seconds')
    if total is None:
        return ''
    if record.get('kind') == 'synthesis':
        return f'Synthesis time limit: {total:g}s'
    if record.get('kind') == 'research':
        return (f"Research budget: {record['total_calls']} total provider calls, including synthesis; "
                f"{total:g}s total; {record['synthesis_timeout_seconds']:g}s synthesis reserve.\n"
                f"Models: research={record['model']}; "
                f"synthesis={record['synthesis_model'] or record['model']}")
    reserved = record.get('synthesis_timeout_seconds', 0)
    return (f"Limits: {record.get('steps', '?')} steps/worker; worker phase up to "
            f'{total - reserved:g}s; total {total:g}s '
            f'(reserves {reserved:g}s for synthesis)')


def _engine(ctx, store, settings, options):
    cfg = ctx.obj['CONFIG_MANAGER'].base_config
    default = cfg.get('AGENT', 'default_model', fallback=None) or cfg.defaults().get('default_model')
    for model in (settings.model or default, settings.synthesis_model):
        if model and not ctx.obj['CONFIG_MANAGER'].create_session_config().normalize_model_name(model):
            raise ValueError(f"Unknown model '{model}'; configure it in models.ini or select -m.")
    # ConfigParser.get() also inherits DEFAULT.max_tokens, which would turn an
    # absent workspace override into a forced limit that masks model settings.
    max_tokens = cfg._sections.get('WORKSPACE', {}).get('max_tokens', '').strip()
    if max_tokens:
        options.setdefault('max_tokens', int(max_tokens))
    options.setdefault('use_mcp', False)
    return WorkspaceEngine(store, settings, config_file=ctx.obj.get('CONFIG_FILE'),
                           overrides=options)


def _started(store):
    def notify(record):
        click.echo(f"Workspace run: {record['id']}\nSaved in: {store.run_dir(record['id'])}",
                   err=True)
        if limit := _render_limits(record):
            click.echo(limit, err=True)
    return notify


def _answer(ctx, result, json_output):
    click.echo(json.dumps(result, ensure_ascii=False) if json_output else result['answer'])
    if not json_output and result['status'] != 'completed':
        click.echo(f"Run status: {result['status']}; inspect workspace status for details.", err=True)
    if result['status'] == 'cancelled':
        ctx.exit(130)
    if result['status'] not in ('completed', 'partial'):
        ctx.exit(1)


def _reject_task_inputs(ctx):
    if (ctx.obj.get('FILES') or ctx.obj.get('MESSAGE') is not None
            or ctx.obj.get('STDIN_MESSAGE')):
        raise click.UsageError('Task inputs require workspace run.')


def register_workspace_commands(cli, task_message, is_image_file) -> None:
    """Register thin CLI entry points without changing other modes."""

    @cli.group()
    def workspace():
        """Run agents against persistent shared findings; inspect saved activity."""

    @workspace.command('research')
    @click.argument('question', required=False)
    @click.option('--brief', type=click.Path(exists=True, dir_okay=False),
                  help='Read the task brief from a UTF-8 file (separate from system prompt).')
    @click.option('-f', '--file', 'files', multiple=True,
                  type=click.Path(exists=True, dir_okay=False), help='Supporting attachment.')
    @click.option('--message', default=None)
    @click.option('--stdin-message', is_flag=True)
    @click.option('--directory', type=click.Path(path_type=str))
    @click.option('--repository', type=click.Path(exists=True, file_okay=False))
    @click.option('--strategy', type=click.Choice(['adaptive', 'single']), default='adaptive')
    @click.option('--calls', type=click.IntRange(min=3), default=None,
                  help='Shared provider-call ceiling including branches and synthesis.')
    @click.option('--timeout', type=click.FloatRange(min=0, min_open=True), default=None)
    @click.option('--synthesis-timeout', type=click.FloatRange(min=0, min_open=True), default=None)
    @click.option('--synthesis-model', default=None)
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def research(ctx, question, brief, files, message, stdin_message, directory, repository,
                 strategy, calls, timeout, synthesis_timeout, synthesis_model, json_output):
        """Pursue a brief with one continuing investigation and optional focused branches."""
        from workspace.adaptive import AdaptiveEngine
        try:
            supplied = [value for value in (question, message, ctx.obj.get('MESSAGE'))
                        if value is not None]
            if len(supplied) > 1 or (brief and (supplied or stdin_message or
                                               ctx.obj.get('STDIN_MESSAGE'))):
                raise click.UsageError('Provide the task once: --brief, question, or message input.')
            task = (Path(brief).read_text(encoding='utf-8') if brief else
                    task_message(ctx, supplied[0] if supplied else None,
                                 stdin_message=stdin_message))
            if not task or not task.strip():
                raise click.UsageError('Provide a nonempty brief or question.')
            attachments = tuple(ctx.obj.get('FILES', ())) + files
            if '-' in attachments:
                raise click.UsageError('Use --stdin-message for stdin task text; attachments need paths.')
            contexts = [('image' if is_image_file(path) else 'file', str(Path(path).resolve()))
                        for path in attachments]
            repo, store = _paths(ctx, directory, repository)
            cfg = ctx.obj['CONFIG_MANAGER'].base_config
            options = dict(ctx.obj['OPTIONS'])
            if 'steps' in options:
                raise click.UsageError('Research uses a shared --calls budget, not per-worker --steps.')
            model = options.get('model') or cfg.get('AGENT', 'default_model', fallback=None)
            settings = WorkspaceSettings(
                repository=str(repo), model=model,
                synthesis_model=synthesis_model or (model if options.get('model') else
                    cfg.get('WORKSPACE', 'synthesis_model', fallback='') or model),
                writes_policy=options.pop('agent_writes', 'deny'),
                run_timeout_seconds=timeout if timeout is not None else cfg.getfloat(
                    'WORKSPACE', 'run_timeout_seconds', fallback=600),
                synthesis_timeout_seconds=synthesis_timeout if synthesis_timeout is not None
                    else cfg.getfloat('WORKSPACE', 'synthesis_timeout_seconds', fallback=90),
                synthesis_prompt=cfg.get('WORKSPACE', 'synthesis_prompt', fallback='workspace/synthesize'),
                synthesis_max_chars=cfg.getint('WORKSPACE', 'synthesis_max_chars', fallback=48000),
            )
            # Reuse config/model validation and the normal noninteractive defaults.
            lead_prompt = options.pop('prompt', 'workspace/research')
            resolved = _engine(ctx, store, settings, options)
            engine = AdaptiveEngine(
                store, settings, config_file=resolved.config_file, overrides=resolved.overrides,
                total_calls=calls if calls is not None else cfg.getint(
                    'WORKSPACE', 'research_total_calls', fallback=32),
                strategy=strategy, contexts=contexts,
                lead_prompt=lead_prompt,
            )
            _answer(ctx, engine.run(task, on_started=_started(store)), json_output)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

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
                synthesis_model=(synthesis_model or (None if options.get('model') else
                                                    setting('synthesis_model', '') or None)),
                run_timeout_seconds=(timeout if timeout is not None
                                     else float(setting('run_timeout_seconds', 300))),
                synthesis_timeout_seconds=(synthesis_timeout if synthesis_timeout is not None
                                          else float(setting('synthesis_timeout_seconds', 90))),
                writes_policy=options.pop('agent_writes', setting('writes_policy', 'deny')),
                worker_prompt=options.pop('prompt', setting('worker_prompt', 'workspace/contribute')),
                synthesis_prompt=setting('synthesis_prompt', 'workspace/synthesize'),
                synthesis_max_chars=int(setting('synthesis_max_chars', 48000)),
            )
            engine = _engine(ctx, store, settings, options)
            _answer(ctx, engine.run(task, on_started=_started(store)), json_output)
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
            _reject_task_inputs(ctx)
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

    @workspace.command('synthesize')
    @click.argument('run_id')
    @click.option('--directory', type=click.Path(path_type=str))
    @click.option('--timeout', type=click.FloatRange(min=0, min_open=True), default=None,
                  help='Time limit for this synthesis alone (seconds).')
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def synthesize(ctx, run_id, directory, timeout, json_output):
        """Answer again from frozen findings in a new run; -m selects the model."""
        try:
            _reject_task_inputs(ctx)
            repo, store = _paths(ctx, directory)
            cfg = ctx.obj['CONFIG_MANAGER'].base_config
            options = dict(ctx.obj['OPTIONS'])
            settings = WorkspaceSettings(
                repository=str(repo), model=options.get('model'),
                synthesis_model=(None if options.get('model') else
                                 cfg.get('WORKSPACE', 'synthesis_model', fallback='') or None),
                synthesis_prompt=options.pop('prompt', cfg.get(
                    'WORKSPACE', 'synthesis_prompt', fallback='workspace/synthesize')),
                synthesis_max_chars=cfg.getint('WORKSPACE', 'synthesis_max_chars', fallback=48000),
                run_timeout_seconds=(timeout if timeout is not None else cfg.getfloat(
                    'WORKSPACE', 'synthesis_timeout_seconds', fallback=90)),
            )
            engine = _engine(ctx, store, settings, options)
            _answer(ctx, engine.synthesize(run_id, on_started=_started(store)), json_output)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc

    @workspace.command('report')
    @click.argument('run_id', required=False)
    @click.option('--directory', type=click.Path(path_type=str))
    @click.option('--json', 'json_output', is_flag=True)
    @click.pass_context
    def report(ctx, run_id, directory, json_output):
        """Read saved timing, usage, tool calls and citation metrics; no model calls."""
        try:
            _reject_task_inputs(ctx)
            _, store = _paths(ctx, directory)
            result = run_report(store, run_id)
            if json_output:
                click.echo(json.dumps(result, ensure_ascii=False))
                return
            click.echo(f"Run: {result['run_id']} — {result['status']}")
            if result['source_run']:
                click.echo(f"Source run: {result['source_run']}")
            click.echo(f"Wall time: {result['wall_seconds']} seconds")
            click.echo(f"Tokens across calls: {json.dumps(result['usage'])}")
            click.echo(f"Notes: {result['notes']} ({result['findings']} findings); "
                       f"explicit peer citation links: {result['peer_citation_edges']}")
            for worker in result['workers']:
                click.echo(f"  {worker['worker']}: {worker['model'] or 'model unrecorded'}; "
                           f"turns={worker['turns']}; tools={worker['tool_calls']}; "
                           f"notes delivered/read={worker['notes_delivered']}/{worker['notes_read']}")
                for failure in worker['workspace_errors']:
                    click.echo(f"    workspace {failure['operation']} failed: {failure['error']}")
            if result['synthesis_input_sha256']:
                click.echo(f"Synthesis evidence: {result['synthesis_notes_included']} notes; "
                           f"{result['synthesis_notes_deduplicated']} identical texts grouped")
                if result['synthesis_notes_current'] is not None:
                    click.echo(f"  {result['synthesis_notes_current']} from this investigation; "
                               f"{result['synthesis_notes_historical']} historical")
                click.echo(f"Synthesis input SHA-256: {result['synthesis_input_sha256']}")
            click.echo('Delivery and citations record activity; they do not establish correctness.')
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
