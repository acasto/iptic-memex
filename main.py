import click
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
        options['cli_stream'] = True  # differentiate from config stream option
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
    if 'model' in options and options['model']:
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
    if len(file) > 0:
        # Build session for completion mode first; we may switch to Agent below
        session = builder.build(mode='completion', **options)
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
            except Exception:
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
                    names = sorted(list((act.commands or {}).keys())) if act and hasattr(act, 'commands') else []
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
def chat(ctx, file):
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})
    
    # Build session for chat mode
    session = builder.build(mode='chat', **options)
    ctx.obj['SESSION'] = session
    
    # Add file contexts if provided
    if len(file) > 0:
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
def tui(ctx, file):
    """Start TUI (Terminal User Interface) mode"""
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})
    
    # Build session for TUI mode
    session = builder.build(mode='tui', **options)
    ctx.obj['SESSION'] = session
    
    # Add file contexts if provided
    if len(file) > 0:
        for f in file:
            session.add_context('file', f)
    
    # Start TUI mode
    try:
        from modes.tui_mode import TUIMode
        mode = TUIMode(session, builder)
        mode.start()
    except ImportError as e:
        if 'textual' in str(e).lower():
            print("Error: TUI mode requires the 'textual' library.")
            print("Install with: pip install textual")
        else:
            print(f"Error importing TUI components: {e}")
    except Exception as e:
        print(f"Error starting TUI mode: {e}")
        import traceback
        traceback.print_exc()


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('--host', default=None, help='Host interface to bind (overrides config)')
@click.option('--port', type=int, default=None, help='Port to bind (overrides config)')
def web(ctx, file, host, port):
    """Start Web mode (local browser UI)"""
    # Get builder and options from context
    builder = ctx.obj['BUILDER']
    options = ctx.obj.get('OPTIONS', {})

    # Build session for Web mode
    session = builder.build(mode='web', **options)
    ctx.obj['SESSION'] = session

    # Add file contexts if provided
    if len(file) > 0:
        for f in file:
            session.add_context('file', f)

    # Start Web mode
    try:
        from modes.web_mode import WebMode
        mode = WebMode(session, builder, host=host, port=port)
        mode.start()
    except ImportError as e:
        print("Error importing Web components:", e)
    except Exception as e:
        print(f"Error starting Web mode: {e}")
        import traceback
        traceback.print_exc()


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
    
    for section, options in models.items():
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
    for provider in providers.keys():
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


def is_image_file(filename: str) -> bool:
    """Check if a file is an image based on the extension"""
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')
    return filename.lower().endswith(image_extensions)


# take care of business
if __name__ == "__main__":
    cli(obj={})
