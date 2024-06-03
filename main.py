import sys
import click
from session_handler import SessionHandler


@click.group(invoke_without_command=True)
@click.option('-c', '--conf', help='Path to a custom configuration file')
@click.option('-m', '--model', help='Model to use for completion')
@click.option('-p', '--prompt', help='Filename from the prompt directory')
@click.option('-t', '--temperature', help='Temperature to use for completion')
@click.option('-l', '--max-tokens', help='Maximum number of tokens to use for completion')
@click.option('-s', '--stream', is_flag=True, help='Stream the completion events')
@click.option('-v', '--verbose', is_flag=True, help='Show session parameters')
@click.option('-f', '--file', multiple=True, help='File to use for completion')
@click.pass_context
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, verbose, file):
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
    :param file: file to use for completion (file mode)
    :return: none (this is a click entry point)
    """
    ctx.ensure_object(dict)  # set up the context object to be passed around
    ctx.obj['CONF'] = SessionHandler(conf)  # start up the ConfigHandler

    # if user specified a prompt file, check if we need to read it from stdin and then pass to ConfigHandler
    if prompt is not None:
        if prompt == '-':
            ctx.obj['CONF'].set_user_option('prompt', sys.stdin.read())
        else:
            ctx.obj['CONF'].set_user_option('prompt', prompt)

    # if user specified a model, check if it's in the models file and then pass to ConfigHandler
    if model is not None:
        if ctx.obj['CONF'].valid_model(model):
            ctx.obj['CONF'].set_user_option('model', model)
        else:
            raise click.UsageError(f'Invalid model: {model}')

    # set user supplied temperature
    if temperature is not None:
        ctx.obj['CONF'].set_user_option('temperature', temperature)

    # set user supplied max_tokens
    if max_tokens is not None:
        ctx.obj['CONF'].set_user_option('max_tokens', max_tokens)

    # if the user specified the stream flag, set it in the ConfigHandler
    if stream:
        ctx.obj['CONF'].set_user_option('stream', True)

    # this is a cli specific flag
    if verbose:
        ctx.obj['VERBOSE'] = True

    # todo: finsh file mode once we have the session handler and interaction handler working
    # if we're in file mode take care of that now
    if len(file) > 0:
        # loop through the files and read them in appending the content to the message
        for f in file:
            # if the file is '-', read from stdin
            if f == '-':
                ctx.obj['CONF'].add_file(sys.stdin.read())
            else:
                ctx.obj['CONF'].add_file(f)

        ctx.obj['CONF'].set_user_option('interactive', False)  # we're not in interactive mode

        session = SessionHandler(ctx.obj['CONF'])
        session.start_interaction("completion")
        return

    # if no subcommand was invoked, show the help (since we're using invoke_without_command=True)
    if ctx.invoked_subcommand is None:
        raise click.UsageError(cli.get_help(ctx))


@cli.command()
@click.pass_context
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('-u', '--url', multiple=True, help='URL to include in prompt (ask questions about URL)')
@click.option('--id', 'css_id', help='CSS ID selector of text to scrape from URL')
@click.option('--class', 'css_class', help='CSS class selector of text to scrape from URL')
def ask(ctx, file, url, css_id, css_class):
    # if we have files to read in, do that now
    if len(file) > 0:
        # loop through the files and read them in appending the content to the message
        for f in file:
            # if the file is '-', read from stdin
            if f == '-':
                ctx.obj['CONF'].add_file(sys.stdin.read())
            else:
                ctx.obj['CONF'].add_file(f)
    # if len(url) > 0:
    #     # check so that -u will work alongside -f
    #     if 'load_file' not in session:
    #         session['load_file'] = []
    #     if 'load_file_name' not in session:
    #         session['load_file_name'] = []
    #     for u in url:
    #         # check prefix
    #         if not u.startswith('http') and not u.startswith('https'):
    #             u = 'https://' + u
    #         # make a request to URL
    #         response = requests.get(u)
    #         # parse the response
    #         soup = BeautifulSoup(response.text, 'html.parser')
    #         text = None
    #         if css_id is not None:
    #             text = soup.find(id=css_id).get_text()
    #         elif css_class is not None:
    #             text = soup.find(class_=css_class).get_text()
    #         # extract text less unnecessary newlines and whitespace
    #         if text is None:
    #             text = '\n'.join([line.strip() for line in soup.get_text().split('\n') if line.strip()])
    #         else:
    #             text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
    #         session['load_file'].append(text)
    #         session['load_file_name'].append(u)
    # if 'verbose' in session and session['verbose']:
    #     print_session_info(session)
    # call the completion handler since we're in ask mode
    # completion = interaction_handler.Completion(session)
    # completion.start(prompt)
    session = SessionHandler(ctx.obj['CONF'])
    session.start_interaction("ask")
    return


@cli.command()
@click.pass_context
@click.option('-s', '--session', 'load_chat', type=click.Path(), help="Load a saved chat session from a file")
@click.option('-f', '--file', multiple=True, help='File to include in prompt (ask questions about file)')
@click.option('-u', '--url', multiple=True, help='URL to include in prompt (ask questions about URL)')
@click.option('--id', 'css_id', help='CSS ID selector of text to scrape from URL')
@click.option('--class', 'css_class', help='CSS class selector of text to scrape from URL')
def chat(ctx, load_chat, file, url, css_id, css_class):
    # if we have files to read in, do that now
    if len(file) > 0:
        # loop through the files and read them in appending the content to the message
        for f in file:
            # if the file is '-', read from stdin
            if f == '-':
                ctx.obj['CONF'].add_file(sys.stdin.read())
            else:
                ctx.obj['CONF'].add_file(f)
    # if len(url) > 0:
    #     # check so that -u will work alongside -f
    #     if 'load_file' not in session:
    #         session['load_file'] = []
    #     if 'load_file_name' not in session:
    #         session['load_file_name'] = []
    #     for u in url:
    #         # check prefix
    #         if not u.startswith('http') and not u.startswith('https'):
    #             u = 'https://' + u
    #         # make a request to URL
    #         response = requests.get(u)
    #         # parse the response
    #         soup = BeautifulSoup(response.text, 'html.parser')
    #         text = None
    #         if css_id is not None:
    #             text = soup.find(id=css_id).get_text()
    #         elif css_class is not None:
    #             text = soup.find(class_=css_class).get_text()
    #         # extract text less unnecessary newlines and whitespace
    #         if text is None:
    #             text = '\n'.join([line.strip() for line in soup.get_text().split('\n') if line.strip()])
    #         else:
    #             text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
    #         session['load_file'].append(text)
    #         session['load_file_name'].append(u)
    # session['chats_extension'] = conf['DEFAULT']['chats_extension']
    # if load_chat is not None:
    #     session['load_chat'] = resolve_file_path(load_chat, conf['DEFAULT']['chats_directory'])
    # if 'verbose' in session and session['verbose']:
    #     print_session_info(session)
    # chat_session = interaction_handler.Chat(session)
    # chat_session.start(prompt)
    session = SessionHandler(ctx.obj['CONF'])
    session.start_interaction("chat")
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
    if showall:
        models = ctx.obj['CONF'].list_models(showall=True)
    else:
        models = ctx.obj['CONF'].list_models(showall=False)
    for section, options in models.items():
        if details:
            print()
            print(f'[ {section} ]')
            for option, value in options.items():
                print(f'{option} = {value}')
            # print()
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
    if showall:
        providers = ctx.obj['CONF'].list_providers(showall=True)
    else:
        providers = ctx.obj['CONF'].list_providers(showall=False)
    for provider in providers:
        print(provider)


@cli.command()
@click.pass_context
def list_prompts(ctx):
    """
    list the available prompts
    :param ctx: click context
    """
    prompts = ctx.obj['CONF'].list_prompts()
    if prompts is not None:
        for prompt in prompts:
            print(prompt)
    else:
        print("No prompts available")


@cli.command()
@click.pass_context
def list_chats(ctx):
    """
    list the available chats
    :param ctx: click context
    """
    chats = ctx.obj['CONF'].list_chats()
    if chats is not None:
        for session in chats:
            print(session)
    else:
        print("No chats available")


@cli.command()
@click.pass_context
def list_files(ctx):
    """
    list the available files
    :param ctx: click context
    """
    files = ctx.obj['CONF'].list_files()
    if files is not None:
        for file in files:
            print(file)
    else:
        print("No files available")


# take care of business
if __name__ == "__main__":
    cli(obj={})
