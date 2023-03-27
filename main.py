import os
import sys
import click
import io
from configparser import ConfigParser
from api_handler import OpenAIHandler
from interaction_handler import FileCompletion, Completion, Chat

@click.group(invoke_without_command=True)
@click.option('-c', '--conf', help='Path to a custom configuration file')
@click.option('-m', '--model', help='Model to use for completion')
@click.option('-p', '--prompt', help='Filename from the prompt directory')
@click.option('-t', '--temperature', help='Temperature to use for completion')
@click.option('-l', '--max-tokens', help='Maximum number of tokens to use for completion')
@click.option('-s', '--stream', is_flag=True, help='Stream the completion events')
@click.option('-v', '--verbose', is_flag=True, help='Show session parameters')
@click.option('-f', '--file', help='File to use for completion')
@click.pass_context
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, verbose, file):
    """
    the main entry point for the CLI click interface
    :param ctx: the context object that we can use to pass around information
    :param conf: a path to a custom configuration file
    :param model: the model to use for completion
    :param prompt: the prompt file to use for completion
    :param temperature: temperature to use for completion
    :param max_tokens: maximum number of tokens to use for completion
    :param stream: stream the completion events
    :param file: file to use for completion (file mode)
    :param verbose: show session parameters
    :return: none (this is a click entry point)
    """
    ctx.ensure_object(dict) # set up the context object to be passed around

    # load the configuration file(s)
    ctx.obj['CONF'] = get_config(conf)

    # start building up the session object with the information we have
    ctx.obj['SESSION'] = {}
    if model is not None:
        if model in get_models(ctx.obj['CONF']):
            ctx.obj['SESSION']['model'] = model
        else:
            raise click.UsageError(f'Invalid model: {model}')
    if temperature is not None:
        ctx.obj['SESSION']['temperature'] = temperature
    if max_tokens is not None:
        ctx.obj['SESSION']['max_tokens'] = max_tokens
    if stream:
        ctx.obj['SESSION']['stream'] = stream
    if prompt is not None:
        ctx.obj['SESSION']['prompt'] = get_prompt(ctx.obj['CONF'], prompt)
    if verbose:
        ctx.obj['SESSION']['verbose'] = True

    # if we're in file mode take care of that now
    if file is not None:
        # if the file is '-', read from stdin
        if file == '-':
            ctx.obj['SESSION']['prompt'] = sys.stdin.read()
        else:
            file_path = resolve_file_path(file)
            with open(file_path, 'rt') as f:
                # if there's already a prompt in the session, append the file to it
                if prompt in ctx.obj['SESSION']:
                    ctx.obj['SESSION']['prompt'] += f.read()
                else:
                    ctx.obj['SESSION']['prompt'] = f.read()
        session = get_session(ctx, 'completion')
        completion = FileCompletion(session)
        completion.start(ctx.obj['SESSION']['prompt'])
        return

    # if no subcommand was invoked, show the help (since we're using invoke_without_command=True)
    if ctx.invoked_subcommand is None:
        raise click.UsageError(cli.get_help(ctx))

@cli.command()
@click.pass_context
def ask(ctx):
    session = get_session(ctx, 'completion')
    prompt = session['prompt']
    if 'verbose' in session and session['verbose']:
        print_session_info(session)
    completion = Completion(session)
    completion.start(prompt)


@cli.command()
@click.pass_context
@click.option('-f', '--chat-file', type=click.Path(), help="Load a chat log from a file")
def chat(ctx, chat_file):
    conf = ctx.obj['CONF']
    session = get_session(ctx, 'chat')
    prompt = session['prompt']
    session['chats_extension'] = conf['DEFAULT']['chats_extension']
    if chat_file is not None:
        session['load_chat'] = resolve_file_path(chat_file, conf['DEFAULT']['chats_directory'])
    if 'verbose' in session and session['verbose']:
        print_session_info(session)
    chat_session = Chat(session)
    chat_session.start(prompt)

@cli.command()
@click.pass_context
@click.option('-p', '--providers', is_flag=True, help="Show providers along with models")
def list_models(ctx, providers):
    conf = ctx.obj['CONF']
    models = get_models(conf)
    for model in models:
        if providers:
            print(model +' ('+ get_provider_from_model(conf, model) + ')')
        else:
            print(model)

@cli.command()
@click.pass_context
def list_prompts(ctx):
    conf = ctx.obj['CONF']
    path = resolve_directory_path(conf['DEFAULT']['prompt_directory'])
    for prompt in os.listdir(path):
        click.echo(prompt)


@cli.command()
@click.pass_context
def list_chats(ctx):
    conf = ctx.obj['CONF']
    path = resolve_directory_path(conf['DEFAULT']['chats_directory'])
    for chat_session in os.listdir(path):
        click.echo(chat_session)

@cli.command()
@click.pass_context
def list_config(ctx):
    conf = ctx.obj['CONF']
    print()
    for section in conf.sections(): # dump the settings in the ini format
        print(f'[ {section} ]')
        for option in conf.options(section):
            value = conf.get(section, option)
            print(f'{option} = {value}')
        print()

#######################
#  Helper functions   #
#######################

def get_config(config_file=None):
    """
    read the config files and return a ConfigParser object
    :param config_file: optional path to a custom config file
    :return: ConfigParser object
    """
    # get the default config file path and make sure it exists
    default_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
    if not os.path.exists(default_config_file):
        raise FileNotFoundError(f'Could not find the default config file at {default_config_file}')
    # instantiate the config parser and read the config files
    config = ConfigParser()
    config.read(default_config_file)
    # get the user config location from the default config file and check and read it
    user_config = resolve_file_path(config['DEFAULT']['user_config'])
    if user_config is None:
        raise FileNotFoundError(f'Could not find the user config file at ' + config['DEFAULT']['user_config'])
    config.read(user_config)
    # if a custom config file was specified, check and read it
    if config_file is not None:
        file = resolve_file_path(config_file)
        if file is None:
            raise FileNotFoundError(f'Could not find the custom config file at {config_file}')
        config.read(config_file) # read the custom config file
    return config

def get_prompt(conf, prompt_file=None):
    """
    get the requested prompt
    :param conf: ConfigParser object
    :param prompt_file: optional path to a custom prompt file
    todo: add support for mode specific default prompts
    """
    if prompt_file is not None:
        if not os.path.isabs(conf['DEFAULT']['prompt_directory']):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), conf['DEFAULT']['prompt_directory'])
        else:
            path = conf['DEFAULT']['prompt_directory']
        if not os.path.isabs(prompt_file):
            prompt_file = path + '/' + prompt_file
        try:
            with open(prompt_file, 'r') as f:
                prompt = f.read()
        except FileNotFoundError:
            raise click.UsageError(f"Warning: Prompt file not found: {prompt_file}")
    return prompt

def get_models(conf):
    """
    returns a list of available models in the config files
    :param conf: ConfigParser object
    :return: list of models
    """
    models = []
    for provider in conf.sections():
        models += get_models_for_provider(conf, provider)
    return models

def get_models_for_provider(conf, provider):
    """
    builds up a list of models for a given provider, combines mode specific and general models
    :param conf: ConfigParser object
    :param provider: name of the provider
    :return: list of models
    """
    models = []
    if conf.has_option(provider, 'completion_models'):
        models += [model.strip() for model in conf.get(provider, 'completion_models').split(',')]
    if conf.has_option(provider, 'chat_models'):
        models += [model.strip() for model in conf.get(provider, 'chat_models').split(',')]
    return models

def get_provider_from_model(conf, model):
    """
    returns the provider for a given model
    :param conf: ConfigParser object
    :param model: name of the model
    :return: name of the provider
    """
    for provider in conf.sections():
        models_available_list = []
        models_available_list += get_models_for_provider(conf, provider)
        if model in models_available_list:
            return provider
    return None

def get_endpoint_from_model(conf, model):
    """
    returns the endpoint for a given model
    :param conf: ConfigParser object
    :param model: name of the model
    :return: endpoint url
    """
    provider = get_provider_from_model(conf, model)
    if provider is None:
        return None
    mode = get_mode_from_model(conf, model)
    if conf.has_option(provider, mode+'_endpoint'):
        return conf.get(provider, mode+'_endpoint')
    return None

def get_mode_from_model(conf, model):
    """
    returns the mode for a given model
    :param conf: ConfigParser object
    :param model: name of the model
    :return: mode
    """
    provider = get_provider_from_model(conf, model)
    if provider is None:
        return None
    if conf.has_option(provider, 'completion_models'):
        if model in [model.strip() for model in conf.get(provider, 'completion_models').split(',')]:
            return 'completion'
    if conf.has_option(provider, 'chat_models'):
        if model in [model.strip() for model in conf.get(provider, 'chat_models').split(',')]:
            return 'chat'
    return None

def get_default_provider(conf):
    """
    returns the default provider from the config file
    works with both 'default_provider = <provider>' and 'default = True' methods
    :param conf: ConfigParser object
    :return: name of the default provider or None
    """
    if conf.has_option('DEFAULT', 'default_provider'):
        return conf.get('DEFAULT', 'default_provider')
    for provider in conf.sections():
        if conf.has_option(provider, 'default') and conf.getboolean(provider, 'default'):
            return provider
    return None

def get_default_model(conf, provider, mode):
    """
    returns the first listed for the chosen provider for the chosen mode
    :param conf: ConfigParser object
    :param provider: name of the provider
    :param mode: mode
    :return: name of the default model or None
    """
    if mode == 'completion' or mode == 'complete':
        if conf.has_option(provider, 'completion_models'):
            return [model.strip() for model in conf.get(provider, 'completion_models').split(',')][0]
    elif mode == 'chat':
        if conf.has_option(provider, 'chat_models'):
            return [model.strip() for model in conf.get(provider, 'chat_models').split(',')][0]
    return None

def get_session(ctx, mode):
    """
    sets up the session and returns it with an 'api_handler' key and a handler instance for the chosen provider
    todo: maybe fetch a dict of parameters from the provider classes so that we don't have to hardcode them all here
          and a new provider could be added without having to change this function
    :param ctx: click context
    :param mode: mode
    :return: session dict
    """
    conf = ctx.obj['CONF']
    session = ctx.obj['SESSION']
    ## finish setting up the session object if needed
    if 'model' in session:
        provider = get_provider_from_model(conf, session['model'])
    else:
        provider = get_default_provider(conf)
        session['model'] = get_default_model(conf, provider, mode)
    if 'temperature' not in session:
        session['temperature'] = conf.getfloat(provider, 'temperature')
    if 'max_tokens' not in session:
        session['max_tokens'] = conf.getint(provider, 'max_tokens')
    if 'stream' not in session:
        session['stream'] = conf.getboolean(provider, 'stream')
    if 'stream_delay' not in session:
        session['stream_delay'] = conf.getfloat(provider, 'stream_delay')
    if 'endpoint' not in session:
        session['endpoint'] = get_endpoint_from_model(conf, session['model'])
    if conf.has_option(provider, 'api_key') and conf.get(provider, 'api_key') != '':
        session['api_key'] = conf.get(provider, 'api_key')
    if 'prompt' not in session:
        session['prompt'] = conf.get(provider, 'fallback_prompt')
    if mode == 'chat' and 'chats_directory' not in session:
        session['chats_directory'] = resolve_directory_path(conf['DEFAULT']['chats_directory'])

    provider_class = globals()[provider + 'Handler']
    session['api_handler'] = provider_class(session)
    return session

def print_session_info(session_object):
    """
    outputs the session parameters to the console for informational purposes (use -v to see this)
    :param session_object: session dict
    :return: None
    """
    session = session_object
    for key in session:
        exclude = ['api_handler', 'api_key', 'verbose']
        if key not in exclude:
            print(f'{key}: {session[key]}')
    print('-' * 80)

def resolve_file_path(file_name, base_dir=None, extension=None):
    """
    works out the path to a file based on the filename and optional base directory and can take an optional extension
    :param file_name: name of the file to resolve the path to
    :param base_dir: optional base directory to resolve the path from
    :param extension: optional extension to append to the file name
    :return: absolute path to the file
    """
    # If base_dir is not specified, use the current working directory
    if base_dir is None:
        base_dir = os.getcwd()
    # If base_dir is a relative path, convert it to an absolute path based on the main.py directory
    elif not os.path.isabs(base_dir):
        main_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.abspath(os.path.join(main_dir, base_dir))
    # Expand user's home directory if base_dir starts with a tilde
    base_dir = os.path.expanduser(base_dir)

    # Check if base_dir exists and is a directory
    if not os.path.isdir(base_dir):
        return None

    # If the file_name is an absolute path, check if it exists
    file_name = os.path.expanduser(file_name)
    if os.path.isabs(file_name):
        if os.path.isfile(file_name):
            return file_name
        elif extension is not None and os.path.isfile(file_name + extension):
            return file_name + extension
    else:
        # If the file_name is a relative path, check if it exists
        full_path = os.path.join(base_dir, file_name)
        if os.path.isfile(full_path):
            return full_path
        elif extension is not None and os.path.isfile(full_path + extension):
            return full_path + extension

        # If the file_name is just a file name, check if it exists in the base directory
        full_path = os.path.join(base_dir, file_name)
        if os.path.isfile(full_path):
            return full_path
        elif extension is not None and os.path.isfile(full_path + extension):
            return full_path + extension

    # If none of the conditions are met, return None
    return None

def resolve_directory_path(dir_name):
    """
    works out the path to a directory
    :param dir_name: name of the directory to resolve the path to
    :return: absolute path to the directory
    """
    dir_name = os.path.expanduser(dir_name)
    if not os.path.isabs(dir_name):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), dir_name)
        if os.path.isdir(path):
            return path
    else:
        if os.path.isdir(dir_name):
            return dir_name
    return None

# take care of business
if __name__ == "__main__":
    cli(obj={})
