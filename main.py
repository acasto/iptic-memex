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
@click.pass_context
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, verbose, raw, file):
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
    if raw:
        options['raw_completion'] = True
        # Disable streaming if raw output is requested
        options['stream'] = False
    
    # Store options for later use
    ctx.obj['OPTIONS'] = options
    
    # Handle file mode (completion mode)
    if len(file) > 0:
        # Build session for completion mode
        session = builder.build(mode='completion', **options)
        ctx.obj['SESSION'] = session
        
        # Add file contexts
        for f in file:
            if is_image_file(f):
                session.add_context('image', f)
            else:
                session.add_context('file', f)
        
        # Start completion mode
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
        from tui.mode import TextualMode
        mode = TextualMode(session, builder)
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
