import click
import json
import os
import sys
from contextlib import nullcontext, redirect_stdout
from config_manager import ConfigManager
from session import SessionBuilder


def _command_files(ctx, files):
    """Combine root and command attachments while consuming stdin at most once."""
    combined = tuple(ctx.obj.get('FILES', ())) + tuple(files)
    if combined.count('-') > 1:
        raise click.UsageError('Stdin can only be attached once.')
    return combined


def _task_message(ctx, message=None, stdin_message=False, files=()):
    """Resolve explicit task input separately from the configured system prompt."""
    task = message if message is not None else ctx.obj.get('MESSAGE')
    read_stdin = stdin_message or ctx.obj.get('STDIN_MESSAGE', False)
    if read_stdin:
        if task is not None or '-' in files:
            raise click.UsageError('--stdin-message cannot be combined with --message or -f -.')
        task = sys.stdin.read()
    if task is not None and not task.strip():
        raise click.UsageError('Task text must not be empty.')
    return task


def _agent_settings(ctx, options):
    cfg = ctx.obj['CONFIG_MANAGER'].base_config
    try:
        default_steps = cfg.getint('AGENT', 'default_steps', fallback=1)
    except ValueError:
        default_steps = 1
    steps = options.get('steps', default_steps)
    if int(steps) <= 0:
        raise click.UsageError('--steps must be at least 1.')
    policy = options.get('agent_writes', cfg.get('AGENT', 'writes_policy', fallback='deny'))
    return int(steps), policy


def _render_exception(ctx, exc, *, json_output=False):
    from core.mode_runner import ModeResult
    _render_result(ctx, ModeResult(last_text=None, raw=None, turns=0, cost=None,
                                  usage=None, events=[], status='failed', error=str(exc),
                                  stop_reason='error'), json_output=json_output)


def _render_result(ctx, result, *, json_output=False, raw=False, streamed=False, output='final'):
    """Map shared outcomes to CLI output and process exit status."""
    status = getattr(result, 'status', 'completed')
    error = getattr(result, 'error', None)
    if json_output:
        click.echo(json.dumps({
            'last_text': getattr(result, 'last_text', None), 'error': error,
            'status': status, 'stop_reason': getattr(result, 'stop_reason', None),
            'truncated': getattr(result, 'truncated', False),
            'turns': getattr(result, 'turns', 0), 'usage': getattr(result, 'usage', None),
            'cost': getattr(result, 'cost', None),
        }, ensure_ascii=False))
    elif status != 'completed':
        click.echo(error or f'Run {status}', err=True)
    elif not streamed and output != 'none':
        value = getattr(result, 'raw' if raw else 'last_text', None)
        if value is not None:
            if raw and not isinstance(value, str):
                value = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
                value = json.dumps(value, ensure_ascii=False, default=str)
            click.echo(value)
    if status != 'completed':
        ctx.exit(1)


def _run_agent_cli(ctx, *, file=(), message=None, snapshot=None, no_hooks=False, json_output=False):
    """Adapt all CLI agent routes to the same core pipeline and input contract."""
    from core.mode_runner import (
        run_agent, _attach_contexts, _agent_status_tags_enabled, _build_subsession,
    )
    from modes.agent_mode import AgentMode
    options = dict((snapshot or {}).get('params') or {})
    options.update(ctx.obj.get('OPTIONS', {}))
    steps, policy = _agent_settings(ctx, options)
    cfg = ctx.obj['CONFIG_MANAGER'].base_config
    output = options.get('agent_output', cfg.get('AGENT', 'output', fallback='final'))
    try:
        if json_output or snapshot is not None:
            from core.runner_seed import snapshot_to_contexts
            contexts = list(snapshot_to_contexts(snapshot)) if snapshot is not None else []
            contexts.extend(('image' if is_image_file(f) else 'file', f) for f in file)
            with redirect_stdout(sys.stderr):
                session = _build_subsession(ctx.obj['BUILDER'], overrides=options)
            if output == 'full' and not json_output:
                session.utils.output.set_stream(sys.stdout)
            with redirect_stdout(sys.stderr):
                result = run_agent(
                    session=session, steps=steps, overrides=options,
                    contexts=contexts, message=message, writes_policy=policy,
                    output='final' if json_output else output,
                    verbose_dump=bool(options.get('agent_debug')),
                    chat_seed=(snapshot or {}).get('chat_seed'),
                    disable_hooks=no_hooks, trace=(snapshot or {}).get('trace'),
                )
        else:
            with redirect_stdout(sys.stderr):
                session = ctx.obj['BUILDER'].build(mode='completion', **options)
                ctx.obj['SESSION'] = session
                if no_hooks:
                    session.set_flag('hooks_disabled', True)
                _attach_contexts(session, [
                    ('image' if is_image_file(f) else 'file', f) for f in file
                ])
                mode = AgentMode(session, steps=steps, writes_policy=policy,
                                 use_status_tags=_agent_status_tags_enabled(session, options),
                                 output_mode=output, message=message)
            if output == 'full' and hasattr(session.utils.output, '_stream'):
                session.utils.output._stream = sys.stdout
            with nullcontext() if output == 'full' else redirect_stdout(sys.stderr):
                result = mode.start(emit_output=False)
    except Exception as exc:
        _render_exception(ctx, exc, json_output=json_output)
    _render_result(ctx, result, json_output=json_output, raw=bool(options.get('raw_completion')),
                   output=output, streamed=output == 'full' and not json_output)


def _run_completion_cli(ctx, *, file=(), message=None):
    from modes.completion_mode import CompletionMode
    from core.mode_runner import _attach_contexts
    options = ctx.obj.get('OPTIONS', {})
    try:
        with redirect_stdout(sys.stderr):
            session = ctx.obj['BUILDER'].build(mode='completion', **options)
            ctx.obj['SESSION'] = session
            _attach_contexts(session, [
                ('image' if is_image_file(f) else 'file', f) for f in file
            ])
            mode = CompletionMode(session, message=message)
        stream = bool(options.get('stream')) and not options.get('raw_completion')
        if stream and hasattr(session.utils.output, '_stream'):
            session.utils.output._stream = sys.stdout
        with nullcontext() if stream else redirect_stdout(sys.stderr):
            result = mode.start(emit_output=False)
    except Exception as exc:
        _render_exception(ctx, exc)
    _render_result(ctx, result, raw=bool(options.get('raw_completion')), streamed=stream)


@click.group(invoke_without_command=True)
@click.option('-c', '--conf', default=None, help='Path to a custom configuration file')
@click.option('-m', '--model', default='', help='Model to use for completion')
@click.option('-p', '--prompt', '--system-prompt', default='',
              help='System prompt: prompt name, file, chain, or literal text')
@click.option('--message', default=None, help='User task text for a completion or agent run')
@click.option('--stdin-message', is_flag=True, help='Read user task text from stdin')
@click.option('-t', '--temperature', default='', help='Temperature to use for completion')
@click.option('-l', '--max-tokens', default='', help='Maximum number of tokens to use for completion')
@click.option('-s', '--stream', default=False, is_flag=True, help='Stream the completion events')
@click.option('-v', '--verbose', default=False, is_flag=True, help='Show session parameters')
@click.option('-r', '--raw', default=False, is_flag=True, help='Return raw response in completion mode')
@click.option('-f', '--file', multiple=True, help='File to use for completion')
@click.option('--steps', type=int, default=None, help='Number of assistant turns (Agent Mode when >1)')
@click.option('--agent-writes', type=click.Choice(['deny', 'dry-run', 'allow']), default=None, help='Agent write policy for file tools')
@click.option('--no-agent-status-tags', is_flag=True, default=False, help='Disable per-turn <status> tag injection')
@click.option('--agent-output', type=click.Choice(['final', 'full', 'none']), default=None, help='Agent output mode: final (default), full, or none')
@click.option('--tools', default=None, help='Agent tools allowlist (CSV). Use "None" to disable all tools.')
@click.option('--mcp', 'mcp_enable', is_flag=True, default=False, help='Enable MCP for non-interactive runs (Agent/Completion)')
@click.option('--no-mcp', 'mcp_disable', is_flag=True, default=False, help='Disable MCP for non-interactive runs (Agent/Completion)')
@click.option('--mcp-servers', default=None, help='Limit MCP servers in non-interactive runs (CSV labels)')
@click.option('--base-dir', default=None, help='Override [TOOLS].base_directory (workspace root) for file/cmd tools')
@click.pass_context
def cli(ctx, conf, model, prompt, message, stdin_message, temperature, max_tokens, stream, verbose, raw, file, steps, agent_writes, no_agent_status_tags, agent_output, tools, mcp_enable, mcp_disable, mcp_servers, base_dir):
    """
    the main entry point for the CLI click interface
    """
    ctx.ensure_object(dict)  # set up the context object to be passed around
    
    # Create config manager and session builder
    config_manager = ConfigManager(conf)
    builder = SessionBuilder(config_manager)
    ctx.obj['CONFIG_MANAGER'] = config_manager
    ctx.obj['BUILDER'] = builder
    
    # Build session options from CLI parameters
    options = {}
    if model:
        options['model'] = model
    if prompt:
        options['prompt'] = prompt
    if temperature:
        options['temperature'] = temperature
    if max_tokens:
        options['max_tokens'] = max_tokens
    if stream:
        # Explicit CLI override to stream; modes can detect this via overrides
        options['stream'] = True
    if verbose:
        ctx.obj['VERBOSE'] = verbose
        # Enable agent-mode debug dumps when verbose is set
        options['agent_debug'] = True
    if raw:
        options['raw_completion'] = True
        # Disable streaming if raw output is requested
        options['stream'] = False
    # Agent mode options (stored for later routing)
    if steps is not None:
        options['steps'] = int(steps)
    if agent_writes is not None:
        options['agent_writes'] = agent_writes
    if no_agent_status_tags:
        options['no_agent_status_tags'] = True
    if agent_output:
        options['agent_output'] = agent_output
    # Agent tools allowlist: CSV, or literal 'None' to disable all tools
    if tools is not None:
        tval = str(tools).strip()
        if tval.lower() == 'none':
            # Sentinel that will not match any real tool name; parsed as allowlist
            options['active_tools_agent'] = '__none__'
        elif tval:
            options['active_tools_agent'] = tval
    # MCP gating for non-interactive runs
    if mcp_enable and not mcp_disable:
        options['use_mcp'] = True
    elif mcp_disable and not mcp_enable:
        options['use_mcp'] = False
    if mcp_servers:
        options['available_mcp'] = str(mcp_servers).strip()
    # Filesystem base dir override for tools (maps to [TOOLS].base_directory)
    if base_dir:
        options['base_directory'] = base_dir
    
    # Validate model early if provided (fail fast on invalid model)
    if options.get('model'):
        # Create a temporary session config to validate/normalize
        session_config = config_manager.create_session_config()
        normalized = session_config.normalize_model_name(options['model'])
        if not normalized:
            raise click.ClickException(
                f"Unknown model '{options['model']}'. Run 'python main.py list-models' to see available models."
            )
        # Use normalized display name internally
        options['model'] = normalized

    # Store options for later use
    ctx.obj['OPTIONS'] = options
    
    ctx.obj['FILES'] = tuple(file)
    ctx.obj['MESSAGE'] = message
    ctx.obj['STDIN_MESSAGE'] = stdin_message
    # Subcommands own execution. Root inputs are forwarded, never run here first.
    if ctx.invoked_subcommand is not None:
        if (file or message is not None or stdin_message) and ctx.invoked_subcommand not in (
                'agent', 'chat', 'tui', 'web'):
            raise click.UsageError('Task inputs require a completion or agent command.')
        if (message is not None or stdin_message) and ctx.invoked_subcommand != 'agent':
            raise click.UsageError('--message/--stdin-message is supported by agent and root runs.')
        return
    if file or message is not None or stdin_message:
        file = _command_files(ctx, ())
        task = _task_message(ctx, files=file)
        steps, _ = _agent_settings(ctx, options)
        if steps > 1:
            _run_agent_cli(ctx, file=file, message=task)
        else:
            _run_completion_cli(ctx, file=file, message=task)
        return
    raise click.UsageError(cli.get_help(ctx))


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--resume', default=None, is_flag=False, flag_value='__pick__',
              help='Pick a saved session, or resume an explicit ID/path')
@click.option('--latest', '--last', is_flag=True,
              help='With --resume, load the most recently saved session without prompting')
def chat(ctx, file, resume, latest):
    if latest and resume is None:
        raise click.UsageError('--latest/--last requires --resume')
    if latest and resume != '__pick__':
        raise click.UsageError('--latest/--last cannot be combined with a session ID/path')
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})
    
    # Build session for chat mode
    try:
        session = builder.build(mode='chat', **options)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    ctx.obj['SESSION'] = session
    
    _maybe_resume_session(session, resume='__last__' if latest else resume)

    # Add file contexts if provided
    file = _command_files(ctx, file)
    if file:
        for f in file:
            session.add_context('file', f)
    
    # Start chat mode
    from modes.chat_mode import ChatMode
    mode = ChatMode(session, builder)
    mode.start()
    return


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--resume', default=None, is_flag=False, flag_value='__last__',
              help='Resume session (most recent if no value)')
def tui(ctx, file, resume):
    """Start TUI (Terminal User Interface) mode"""
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})
    
    # Build session for TUI mode
    try:
        session = builder.build(mode='tui', **options)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    ctx.obj['SESSION'] = session
    
    _maybe_resume_session(session, resume=resume)

    # Add file contexts if provided
    file = _command_files(ctx, file)
    if file:
        for f in file:
            session.add_context('file', f)
    
    # Start TUI mode
    try:
        from modes.tui_mode import TUIMode
        mode = TUIMode(session, builder)
        mode.start()
    except ImportError as e:
        if 'textual' in str(e).lower():
            click.echo("Error: TUI mode requires the 'textual' library.")
            click.echo("Install with: pip install textual")
        else:
            click.echo(f"Error importing TUI components: {e}")
    except Exception as e:
        click.echo(f"Error starting TUI mode: {e}")
        import traceback
        traceback.print_exc()


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--resume', default=None, is_flag=False, flag_value='__last__',
              help='Resume session (most recent if no value)')
@click.option('--host', default=None, help='Host interface to bind (overrides config)')
@click.option('--port', type=int, default=None, help='Port to bind (overrides config)')
def web(ctx, file, resume, host, port):
    """Start Web mode (local browser UI)"""
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})

    # Build session for Web mode
    try:
        session = builder.build(mode='web', **options)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    ctx.obj['SESSION'] = session

    _maybe_resume_session(session, resume=resume)

    # Add file contexts if provided
    file = _command_files(ctx, file)
    if file:
        for f in file:
            session.add_context('file', f)

    # Start Web mode
    try:
        from modes.web_mode import WebMode
        mode = WebMode(session, builder, host=host, port=port)
        mode.start()
    except ImportError as e:
        click.echo(f"Error importing Web components: {e}")
    except Exception as e:
        click.echo(f"Error starting Web mode: {e}")
        import traceback
        traceback.print_exc()


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--from-stdin', 'from_stdin', is_flag=True, default=False,
              help='Read runner snapshot JSON from stdin (not task text)')
@click.option('--message', default=None, help='User task text')
@click.option('--stdin-message', is_flag=True, help='Read user task text from stdin')
@click.option('--no-hooks', 'no_hooks', is_flag=True, default=False, help='Disable hooks for this run')
@click.option('--json', 'json_output', is_flag=True, default=False, help='Return one JSON run result')
def agent(ctx, file, from_stdin, message, stdin_message, no_hooks, json_output):
    """Run non-interactive agent mode with files, task text, or a runner snapshot."""
    files = _command_files(ctx, file)
    try:
        if from_stdin and (files or message is not None or ctx.obj.get('MESSAGE') is not None
                           or stdin_message or ctx.obj.get('STDIN_MESSAGE')):
            raise click.UsageError('Cannot combine snapshot stdin with files or task text.')
        task = _task_message(ctx, message, stdin_message, files)
        snapshot = None
        if from_stdin:
            raw = sys.stdin.read()
            if not raw.strip():
                raise click.UsageError('No snapshot provided on stdin.')
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict):
                raise click.UsageError('Runner snapshot must be a JSON object.')
        _run_agent_cli(ctx, file=files, message=task, snapshot=snapshot,
                       no_hooks=no_hooks, json_output=json_output)
    except (click.ClickException, ValueError) as exc:
        if json_output:
            _render_exception(ctx, exc, json_output=True)
        raise


@cli.command()
@click.pass_context
@click.option('-a', '--all', 'showall', is_flag=True, help="Show all models")
@click.option('-d', '--details', is_flag=True, help="Show model details")
def list_models(ctx, showall, details):
    """
    list the available models
    """
    config_manager = ctx.obj.get('CONFIG_MANAGER')
    if not config_manager:
        # Create a temporary config manager if none exists
        config_manager = ConfigManager()
    
    # Note: active_only=True means show only active models (default behavior)
    # active_only=False means show all models (when --all flag is used)
    if showall:
        models = config_manager.list_models(active_only=False)
    else:
        models = config_manager.list_models(active_only=True)
    
    for section in sorted(models.keys()):
        options = models[section]
        if details:
            print()
            print(f'[ {section} ]')
            for option, value in options.items():
                print(f'{option} = {value}')
        else:
            if 'default' in options and options['default'] == 'True':
                print(f'{section} (default)')
            else:
                print(section)


@cli.command()
@click.option('-a', '--all', 'showall', is_flag=True, help="Show all providers")
@click.pass_context
def list_providers(ctx, showall):
    """
    list the available providers
    """
    config_manager = ctx.obj.get('CONFIG_MANAGER')
    if not config_manager:
        # Create a temporary config manager if none exists
        config_manager = ConfigManager()
    
    models = config_manager.list_models(active_only=True)
    
    # get the provider of the default model
    default_model = ''
    default_provider = ''
    for model, options in models.items():
        if 'default' in options and options['default'] == 'True':
            default_model = model
            default_provider = options['provider']
    
    # Note: active_only=True means show only active providers (default behavior)
    # active_only=False means show all providers (when --all flag is used)  
    if showall:
        providers = config_manager.list_providers(active_only=False)
    else:
        providers = config_manager.list_providers(active_only=True)
    
    # list_providers returns a dict, but we just want the keys (provider names)
    for provider in providers:
        if provider == default_provider:
            print(f'{provider} (default w/ {default_model})')
        else:
            print(provider)


@cli.command()
@click.pass_context
def list_prompts(ctx):
    """
    list the available prompts
    """
    config_manager = ctx.obj.get('CONFIG_MANAGER')
    if not config_manager:
        # Create a temporary config manager if none exists
        config_manager = ConfigManager()
    
    # Use ConfigManager's list_prompts method directly
    prompts = config_manager.list_prompts()
    
    if prompts:
        for prompt in sorted(prompts):
            print(prompt)
    else:
        print("No prompts available")


@cli.command()
@click.pass_context
def list_sessions(ctx):
    """List saved sessions."""
    config_manager = ctx.obj.get('CONFIG_MANAGER')
    if not config_manager:
        config_manager = ConfigManager()
    options = dict(ctx.obj.get('OPTIONS', {}))

    try:
        from component_registry import ComponentRegistry
        from session import Session
        from ui.cli import CLIUI
    except Exception as exc:
        raise click.ClickException(f"Failed to initialize session: {exc}") from exc

    session_config = config_manager.create_session_config(options)
    registry = ComponentRegistry(session_config)
    session = Session(session_config, registry)
    session.ui = CLIUI(session)

    action = session.get_action('manage_sessions')
    if not action:
        raise click.ClickException("manage_sessions action not available.")
    result = action.run(['list'])
    if hasattr(result, 'payload'):
        result = result.payload
    if isinstance(result, dict) and result.get('ok') is False:
        err = result.get('error') or 'unknown error'
        raise click.ClickException(f"Failed to list sessions: {err}")


@cli.group()
@click.pass_context
def logs(ctx):
    """Inspect JSONL logs (supports rotation)."""
    # Keep group for subcommands
    return


def _logs_where(trace, session_uid, outer_session_uid, hook, tool_call_id, event, aspect):
    where = {}
    if trace:
        where["trace_id"] = str(trace).strip()
    if session_uid:
        where["session_uid"] = str(session_uid).strip()
    if outer_session_uid:
        where["outer_session_uid"] = str(outer_session_uid).strip()
    if hook:
        where["hook_name"] = str(hook).strip()
    if tool_call_id:
        where["tool_call_id"] = str(tool_call_id).strip()
    if event:
        where["event"] = str(event).strip()
    if aspect:
        where["aspect"] = str(aspect).strip()
    return where


@logs.command("files")
@click.pass_context
@click.option("--path", "path_override", default=None, help="Override log file path (base).")
def logs_files(ctx, path_override):
    """List log files (base + rotated)."""
    cfg = (ctx.obj.get("CONFIG_MANAGER").base_config if ctx.obj.get("CONFIG_MANAGER") else ConfigManager().base_config)
    from utils.log_viewer import list_log_files, resolve_log_path

    base_path = os.path.expanduser(path_override) if path_override else resolve_log_path(cfg)
    for p in list_log_files(base_path):
        click.echo(p)


@logs.command("tail")
@click.pass_context
@click.option("-n", "--lines", default=50, show_default=True, help="Number of matching events to show.")
@click.option("--path", "path_override", default=None, help="Override log file path (base).")
@click.option("--trace", "trace", default=None, help="Filter by ctx.trace_id.")
@click.option("--session", "session_uid", default=None, help="Filter by ctx.session_uid.")
@click.option("--outer-session", "outer_session_uid", default=None, help="Filter by ctx.outer_session_uid.")
@click.option("--hook", "hook", default=None, help="Filter by ctx.hook_name.")
@click.option("--tool-call-id", "tool_call_id", default=None, help="Filter by ctx.tool_call_id.")
@click.option("--event", "event", default=None, help="Filter by event name.")
@click.option("--aspect", "aspect", default=None, help="Filter by aspect.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Print raw JSONL lines.")
def logs_tail(ctx, lines, path_override, trace, session_uid, outer_session_uid, hook, tool_call_id, event, aspect, json_output):
    """Show the last N matching events across rotated files."""
    cfg = (ctx.obj.get("CONFIG_MANAGER").base_config if ctx.obj.get("CONFIG_MANAGER") else ConfigManager().base_config)
    from utils.log_viewer import resolve_log_path, tail_events

    base_path = os.path.expanduser(path_override) if path_override else resolve_log_path(cfg)
    where = _logs_where(trace, session_uid, outer_session_uid, hook, tool_call_id, event, aspect)
    for line in tail_events(base_path=base_path, lines=lines, where=where, json_output=json_output):
        click.echo(line)


@logs.command("show")
@click.pass_context
@click.option("--limit", default=200, show_default=True, help="Maximum number of matching events to show.")
@click.option("--path", "path_override", default=None, help="Override log file path (base).")
@click.option("--trace", "trace", default=None, help="Filter by ctx.trace_id.")
@click.option("--session", "session_uid", default=None, help="Filter by ctx.session_uid.")
@click.option("--outer-session", "outer_session_uid", default=None, help="Filter by ctx.outer_session_uid.")
@click.option("--hook", "hook", default=None, help="Filter by ctx.hook_name.")
@click.option("--tool-call-id", "tool_call_id", default=None, help="Filter by ctx.tool_call_id.")
@click.option("--event", "event", default=None, help="Filter by event name.")
@click.option("--aspect", "aspect", default=None, help="Filter by aspect.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Print raw JSONL lines.")
def logs_show(ctx, limit, path_override, trace, session_uid, outer_session_uid, hook, tool_call_id, event, aspect, json_output):
    """Show matching events in chronological order across rotated files."""
    cfg = (ctx.obj.get("CONFIG_MANAGER").base_config if ctx.obj.get("CONFIG_MANAGER") else ConfigManager().base_config)
    from utils.log_viewer import resolve_log_path, show_events

    base_path = os.path.expanduser(path_override) if path_override else resolve_log_path(cfg)
    where = _logs_where(trace, session_uid, outer_session_uid, hook, tool_call_id, event, aspect)
    for line in show_events(base_path=base_path, limit=limit, where=where, json_output=json_output):
        click.echo(line)


def is_image_file(filename: str) -> bool:
    """Check if a file is an image based on the extension"""
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')
    return filename.lower().endswith(image_extensions)


def _maybe_resume_session(session, *, resume: str | None) -> None:
    if resume is None:
        return
    action = session.get_action('manage_sessions')
    if not action:
        raise click.ClickException('manage_sessions action not available.')
    if resume == '__pick__':
        action.run(['resume'])
        return
    if not resume or resume == '__last__':
        from core.session_persistence import latest_session_path
        resume = latest_session_path(session)
        if not resume:
            session.ui.emit('warning', {'message': 'No saved session found to resume.'})
            return
    action.run(['resume', resume])


# take care of business
if __name__ == "__main__":
    cli(obj={})
