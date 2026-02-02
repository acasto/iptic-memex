import click
import json
import os
import sys
from config_manager import ConfigManager
from session import SessionBuilder


@click.group(invoke_without_command=True)
@click.option('-c', '--conf', default=None, help='Path to a custom configuration file')
@click.option('-m', '--model', default='', help='Model to use for completion')
@click.option('-p', '--prompt', default='', help='Filename from the prompt directory')
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
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, verbose, raw, file, steps, agent_writes, no_agent_status_tags, agent_output, tools, mcp_enable, mcp_disable, mcp_servers, base_dir):
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
    
    # Handle file mode (completion/agent based on steps)
    if file:
        # Build session for completion mode first; we may switch to Agent below
        try:
            session = builder.build(mode='completion', **options)
        except RuntimeError as e:
            raise click.ClickException(str(e))
        ctx.obj['SESSION'] = session

        # Add file contexts
        for f in file:
            if is_image_file(f):
                session.add_context('image', f)
            else:
                session.add_context('file', f)

        # Route based on steps: Agent Mode when >1, else Completion
        # Determine agent defaults from config when CLI flags are not provided
        cfg = ctx.obj['CONFIG_MANAGER'].base_config if ctx.obj.get('CONFIG_MANAGER') else None
        cfg_steps = 1
        cfg_writes = 'deny'
        if cfg and cfg.has_section('AGENT'):
            try:
                cfg_steps = int(cfg.get('AGENT', 'default_steps', fallback='1'))
            except (TypeError, ValueError):
                cfg_steps = 1
            cfg_writes = cfg.get('AGENT', 'writes_policy', fallback='deny')

        requested_steps = options.get('steps') if 'steps' in options else None
        effective_steps = int(requested_steps) if requested_steps is not None else cfg_steps

        if effective_steps and int(effective_steps) > 1:
            from modes.agent_mode import AgentMode
            mode = AgentMode(
                session,
                steps=int(effective_steps),
                writes_policy=(options.get('agent_writes') if 'agent_writes' in options else cfg_writes),
                use_status_tags=not options.get('no_agent_status_tags', False),
                output_mode=options.get('agent_output'),
            )
            # When verbose, print the effective agent tools before starting
            if options.get('agent_debug', False):
                try:
                    act = session.get_action('assistant_commands')
                    names = sorted(act.commands or {}) if act and hasattr(act, 'commands') else []
                    header = 'Agent tools:'
                    line = f"{header} " + (", ".join(names) if names else "(none)")
                    session.utils.output.write(line)
                except Exception:
                    pass
        else:
            from modes.completion_mode import CompletionMode
            mode = CompletionMode(session)
        mode.start()
        return
    
    # if no subcommand was invoked, show the help (since we're using invoke_without_command=True)
    if ctx.invoked_subcommand is None:
        raise click.UsageError(cli.get_help(ctx))


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--resume', default=None, help='Resume session (most recent if no value)')
def chat(ctx, file, resume):
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})
    
    # Build session for chat mode
    try:
        session = builder.build(mode='chat', **options)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    ctx.obj['SESSION'] = session
    
    _maybe_resume_session(session, resume=resume)

    # Add file contexts if provided
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
@click.option('--resume', default=None, help='Resume session (most recent if no value)')
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
@click.option('--resume', default=None, help='Resume session (most recent if no value)')
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
@click.option('--from-stdin', 'from_stdin', is_flag=True, default=False, help='Read runner snapshot JSON from stdin')
@click.option('--no-hooks', 'no_hooks', is_flag=True, default=False, help='Disable hooks for this run')
@click.option('--json', 'json_output', is_flag=True, default=False, help='Return JSON result (for external runner)')
def agent(ctx, file, from_stdin, no_hooks, json_output):
    """Run non-interactive agent mode (supports external runner snapshots)."""
    builder = ctx.obj['BUILDER']
    options = dict(ctx.obj.get('OPTIONS', {}))

    cfg = ctx.obj.get('CONFIG_MANAGER').base_config if ctx.obj.get('CONFIG_MANAGER') else None
    cfg_steps = 1
    if cfg and cfg.has_section('AGENT'):
        try:
            cfg_steps = int(cfg.get('AGENT', 'default_steps', fallback='1'))
        except (TypeError, ValueError):
            cfg_steps = 1

    requested_steps = options.get('steps')
    effective_steps = int(requested_steps) if requested_steps is not None else cfg_steps
    if effective_steps <= 0:
        effective_steps = 1

    if from_stdin and file:
        raise click.ClickException("Cannot combine --from-stdin with -f/--file.")

    snapshot = None
    chat_seed = None
    contexts = None
    trace = None
    if from_stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            raise click.ClickException("No snapshot provided on stdin.")
        try:
            snapshot = json.loads(raw)
        except Exception as exc:
            raise click.ClickException(f"Failed to parse snapshot JSON: {exc}") from exc

    if snapshot:
        try:
            params = snapshot.get('params') if isinstance(snapshot, dict) else None
            if isinstance(params, dict):
                merged = dict(params)
                merged.update(options)  # CLI overrides win
                options = merged
        except Exception:
            pass
        try:
            chat_seed = snapshot.get('chat_seed') if isinstance(snapshot, dict) else None
        except Exception:
            chat_seed = None
        try:
            from core.runner_seed import snapshot_to_contexts
            contexts = snapshot_to_contexts(snapshot)
        except Exception:
            contexts = None
        try:
            trace = snapshot.get('trace') if isinstance(snapshot, dict) else None
        except Exception:
            trace = None

    if from_stdin or json_output:
        try:
            from core.mode_runner import run_agent
            res = run_agent(
                builder=builder,
                steps=effective_steps,
                overrides=options,
                contexts=contexts,
                output=options.get('agent_output'),
                verbose_dump=bool(options.get('agent_debug', False)),
                chat_seed=chat_seed,
                disable_hooks=bool(no_hooks),
                trace=trace,
            )
        except Exception as exc:
            if json_output:
                print(json.dumps({"last_text": None, "error": str(exc)}))
                return
            raise

        if json_output:
            print(json.dumps({"last_text": res.last_text, "error": None}))
        else:
            if res.last_text:
                print(res.last_text)
        return

    # Standard CLI agent mode (single-step agents are allowed)
    try:
        session = builder.build(mode='completion', **options)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    if no_hooks:
        try:
            session.set_flag("hooks_disabled", True)
        except Exception:
            pass

    if file:
        for f in file:
            if is_image_file(f):
                session.add_context('image', f)
            else:
                session.add_context('file', f)

    from modes.agent_mode import AgentMode
    mode = AgentMode(
        session,
        steps=int(effective_steps),
        writes_policy=(options.get('agent_writes') if 'agent_writes' in options else 'deny'),
        use_status_tags=not options.get('no_agent_status_tags', False),
        output_mode=options.get('agent_output'),
    )
    if options.get('agent_debug', False):
        try:
            act = session.get_action('assistant_commands')
            names = sorted(act.commands or {}) if act and hasattr(act, 'commands') else []
            header = 'Agent tools:'
            line = f"{header} " + (", ".join(names) if names else "(none)")
            session.utils.output.write(line)
        except Exception:
            pass
    mode.start()


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
    try:
        from core.session_persistence import apply_session_data, latest_session_path, load_session_data, resolve_session_path
    except Exception:
        return

    target = ""
    if isinstance(resume, str) and resume and resume != "__last__":
        target = resume
    if not target:
        target = latest_session_path(session)
    path = resolve_session_path(session, target) if target else ""
    if not path:
        try:
            session.utils.output.warning("No saved session found to resume.")
        except Exception:
            pass
        return
    try:
        data = load_session_data(path)
    except Exception:
        try:
            session.utils.output.warning("Failed to load session data.")
        except Exception:
            pass
        return
    kind = (data.get("kind") or "session").lower()
    fork = (kind == "checkpoint")
    apply_session_data(session, data, fork=fork)
    try:
        msg = f"Resumed session from {path}"
        if fork:
            msg += " (forked from checkpoint)"
        session.ui.emit('status', {'message': msg})
    except Exception:
        pass


# take care of business
if __name__ == "__main__":
    def _normalize_resume_argv(argv: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(argv):
            arg = argv[i]
            if arg == "--resume":
                next_arg = argv[i + 1] if i + 1 < len(argv) else None
                if next_arg is None or (isinstance(next_arg, str) and next_arg.startswith("-")):
                    out.extend(["--resume", "__last__"])
                    i += 1
                    continue
            out.append(arg)
            i += 1
        return out

    cli(obj={}, args=_normalize_resume_argv(sys.argv[1:]))
