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
@click.option('-p', '--prompt', help='Filename from the prompt directory to use for completion')
@click.option('-t', '--temperature', help='Temperature to use for completion')
@click.option('-l', '--max-tokens', help='Maximum number of tokens to use for completion')
@click.option('-s', '--stream', is_flag=True, help='Stream the completion events')
@click.option('-f', '--file', help='File to use for completion')
@click.pass_context
def cli(ctx, conf, model, prompt, temperature, max_tokens, stream, file):
    ctx.ensure_object(dict)
    ctx.obj['CONF'] = get_config(conf)
    ctx.obj['SESSION'] = {} # start building up the session object where we have enough info
    if model is not None:
        if check_model(ctx.obj['CONF'], model):
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
    if file is not None:
        file_path = resolve_file_path(file)
        with open(file_path, 'rt') as f:
            if prompt in ctx.obj['SESSION']:
                ctx.obj['SESSION']['prompt'] += f.read()
            else:
                ctx.obj['SESSION']['prompt'] = f.read()
        session = get_session(ctx, 'completion')
        completion = FileCompletion(session)
        completion.start(ctx.obj['SESSION']['prompt'])
        return
    if ctx.invoked_subcommand is None:
        raise click.UsageError(cli.get_help(ctx))

@cli.command()
@click.pass_context
@click.option('-v', '--verbose', is_flag=True, help="Show session parameters")
def ask(ctx, verbose):
    session = get_session(ctx, 'completion')
    prompt = session['prompt']
    if verbose:
        print_session_info(session)
    completion = Completion(session)
    completion.start(prompt)


@cli.command()
@click.pass_context
@click.option('-v', '--verbose', is_flag=True, help="Show session parameters")
@click.option('-f', '--chat-file', type=click.Path(), help="Load a chat log from a file")
def chat(ctx, verbose, chat_file):
    conf = ctx.obj['CONF']
    session = get_session(ctx, 'chat')
    prompt = session['prompt']
    session['chats_extension'] = conf['DEFAULT']['chats_extension']
    if chat_file is not None:
        session['load_chat'] = resolve_file_path(chat_file, conf['DEFAULT']['chats_directory'])
    if verbose:
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
    prompts = get_prompts(conf)
    for prompt in prompts:
        print(prompt)

@cli.command()
@click.pass_context
def list_chats(ctx):
    conf = ctx.obj['CONF']
    chats = get_chats(conf)
    for chat_file in chats:
        print(chat_file)

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

# parse the config files and return a ConfigParser object
def get_config(config_file):
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

def get_prompts(conf):
    if not os.path.isabs(conf['DEFAULT']['prompt_directory']):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), conf['DEFAULT']['prompt_directory'])
    else:
        path = conf['DEFAULT']['prompt_directory']
    return [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]

def get_prompt(conf, prompt_file=None):
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

def get_chats(conf):
    path = resolve_directory_path(conf['DEFAULT']['chats_directory'])
    return [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]

def get_providers(conf):
    return conf.sections()

def get_models(conf):
    models = []
    for provider in conf.sections():
        models += get_models_for_provider(conf, provider)
    return models

def get_models_for_provider(conf, provider):
    models = []
    if conf.has_option(provider, 'completion_models'):
        models += [model.strip() for model in conf.get(provider, 'completion_models').split(',')]
    if conf.has_option(provider, 'chat_models'):
        models += [model.strip() for model in conf.get(provider, 'chat_models').split(',')]
    return models

def get_provider_from_model(conf, model):
    for provider in conf.sections():
        models_available_list = []
        models_available_list += get_models_for_provider(conf, provider)
        if model in models_available_list:
            return provider
    return None

def get_endpoint_from_model(conf, model):
    provider = get_provider_from_model(conf, model)
    if provider is None:
        return None
    mode = get_mode_from_model(conf, model)
    if conf.has_option(provider, mode+'_endpoint'):
        return conf.get(provider, mode+'_endpoint')
    return None

def get_mode_from_model(conf, model):
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
    if conf.has_option('DEFAULT', 'default_provider'):
        return conf.get('DEFAULT', 'default_provider')
    for provider in conf.sections():
        if conf.has_option(provider, 'default') and conf.getboolean(provider, 'default'):
            return provider
    return None

def get_default_model(conf, provider, mode):
    ## returns the first listed for the chosen provider for the chosen mode
    if mode == 'completion' or mode == 'complete':
        if conf.has_option(provider, 'completion_models'):
            return [model.strip() for model in conf.get(provider, 'completion_models').split(',')][0]
    elif mode == 'chat':
        if conf.has_option(provider, 'chat_models'):
            return [model.strip() for model in conf.get(provider, 'chat_models').split(',')][0]
    return None

def check_model(conf, model):
    if model in get_models(conf):
        return True

def get_session(ctx, mode):
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
        session['chats_directory'] = get_chats_directory(conf)
    provider_class = globals()[provider + 'Handler']
    session['api_handler'] = provider_class(session)
    return session

def print_session_info(session_object):
    session = session_object
    for key in session:
        exclude = ['api_handler', 'api_key']
        if key not in exclude:
            print(f'{key}: {session[key]}')
    print('-' * 80)

def get_chats_directory(conf):
    if not os.path.isabs(conf['DEFAULT']['chats_directory']):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), conf['DEFAULT']['chats_directory'])
    else:
        path = conf['DEFAULT']['chats_directory']
    return path

def resolve_file_path(file_name, base_dir=None, extension=None):
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
    dir_name = os.path.expanduser(dir_name)
    if not os.path.isabs(dir_name):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), dir_name)
        if os.path.isdir(path):
            return path
    else:
        if os.path.isdir(dir_name):
            return dir_name
    return None

if __name__ == "__main__":
    cli(obj={})
