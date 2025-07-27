import click
from session_handler import SessionHandler


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
# @click.option( '-f', '--file', multiple=True, type=click.File('r'), help='File to use for completion')
@click.pass_context
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, verbose, raw, file):
    """
    the main entry point for the CLI click interface
    :param ctx: the context object that we can use to pass around information
    :param conf: a path to a custom configuration file
    :param model: the model to use for completion
    :param prompt: the prompt file to use for completion
    :param temperature: temperature to use for completion
    :param max_tokens: maximum number of tokens to use
    :param stream: stream the completion events
    :param verbose: show session parameters
    :param raw: return raw response in completion mode
    :param file: file to use for completion (file mode)
    :return: none (this is a click entry point)
    """
    ctx.ensure_object(dict)  # set up the context object to be passed around
    session = SessionHandler(conf)  # start up a session handler
    ctx.obj['SESSION'] = session

    # Update session parameters only if they are provided
    if prompt:
        # Create a temporary PromptContext to resolve the chain
        session.set_option('prompt', prompt)
        session.add_context('prompt', prompt)
    if model:
        session.set_option('model', model)
    if temperature:
        session.set_option('temperature', temperature)
    if max_tokens:
        session.set_option('max_tokens', max_tokens)
    if stream:
        session.set_option('cli_stream', True)  # differentiate from config stream option
    if verbose:
        ctx.obj['VERBOSE'] = verbose

    # In the CLI function, modify the file handling:
    if len(file) > 0:
        if raw:
            session.set_option('raw_completion', True)
            # Disable streaming if raw output is requested
            session.set_option('stream', False)
        # loop through the files and read them in appending the content to the message
        for f in file:
            if is_image_file(f):
                session.add_context('image', f)
            else:
                session.add_context('file', f)

        session.start_mode("completion")

        return

    # if no subcommand was invoked, show the help (since we're using invoke_without_command=True)
    if ctx.invoked_subcommand is None:
        raise click.UsageError(cli.get_help(ctx))


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
def chat(ctx, file):
    session = ctx.obj['SESSION']

    # if we have files to read in, do that now
    if len(file) > 0:
        # loop through the files and read them in appending the content to the message
        for f in file:
            session.add_context('file', f)

    session.start_mode("chat")
    return


@cli.command()
@click.pass_context
@click.option('-a', '--all', 'showall', is_flag=True, help="Show all models")
@click.option('-d', '--details', is_flag=True, help="Show model details")
def list_models(ctx, showall, details):
    """
    list the available models
    :param ctx: click context
    :param showall: show all models
    :param details: show model details
    """
    session = ctx.obj['SESSION']
    if showall:
        models = session.list_models(showall=True)
    else:
        models = session.list_models(showall=False)
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
    :param ctx: click context
    :param showall: show all providers
    """
    session = ctx.obj['SESSION']
    models = session.list_models(showall=False)

    # get the provider of the default model
    default_model = ''
    default_provider = ''
    for model, options in models.items():
        if 'default' in options and options['default'] == 'True':
            default_model = model
            default_provider = options['provider']

    if showall:
        providers = session.list_providers(showall=True)
    else:
        providers = session.list_providers(showall=False)

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
    :param ctx: click context
    """
    session = ctx.obj['SESSION']
    prompts = session.list_prompts()
    if prompts is not None:
        for prompt in prompts:
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
