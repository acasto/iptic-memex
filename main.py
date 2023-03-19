import os
import click
from chat import Chat
from completion import Completion
from configparser import ConfigParser
from api_handler import OpenAIHandler

@click.group()
@click.option('-c', '--conf', help='Path to a custom configuration file')
@click.option('-p', '--prompt', help="Filename from the prompt directory to use for completion")
@click.option('-pv', '--provider', help="Provider to use for completion")
@click.option('-m', '--model', help="Model to use for completion")
@click.pass_context
def cli(ctx, conf, prompt, provider, model):
    ctx.ensure_object(dict)
    ctx.obj['CONF'] = get_config(conf)
    if prompt is not None:
        ctx.obj['CONF'].set('DEFAULT', 'prompt', get_prompt(ctx.obj['CONF'], prompt))
    if provider is not None:
        ctx.obj['CONF'].set('DEFAULT', 'default_provider', provider)
    if model is not None:
        ctx.obj['CONF'].set('DEFAULT', 'default_model', model)

@cli.command()
@click.pass_context
@click.option('-r', '--raw', help="Don't use a preset prompt, use the raw input from stdin or a file instead")
@click.argument("source", type=click.File("rt", encoding="utf-8"), required=False) # make contingent on --raw
def complete(ctx, raw, source):
    conf = ctx.obj['CONF']
    session = ctx.obj['SESSION']
    provider = get_default_provider(conf)


    api_handler = OpenAIHandler(session)
    completion = Completion(api_handler)
    completion.start()

@cli.command()
@click.pass_context
def chat(ctx):
    api_handler = OpenAIHandler(ctx.obj['CONF'])
    chat_session = Chat(api_handler)
    chat_session.start()

@cli.command()
@click.pass_context
def dump_config(ctx):
    conf = ctx.obj['CONF']
    print(f'Providers: ', get_providers(conf))
    print(f'Default provider: ', get_default_provider(conf))
    print(f'Models: ', get_models(conf))
    print('Who provides gpt-3.5-turbo? '+get_provider_from_model(conf, 'gpt-3.5-turbo'))
    print('Who provides claude? '+get_provider_from_model(conf, 'claude'))
    print('')
    for section in conf.sections(): # dump the settings in the ini format
        print(f'[ {section} ]')
        for option in conf.options(section):
            value = conf.get(section, option)
            print(f'{option} = {value}')
        print()

def get_config(config_file):
    config = ConfigParser()
    config.read('config.ini') # read the default config file
    user_config = os.path.expanduser('~/.config/iptic-memex/config.ini')
    if os.path.exists(user_config):
        config.read(user_config) # read the user config file (if it exists)
    if config_file is not None:
        config.read(config_file) # read the custom config file
    return config

def get_prompt(conf, prompt_file):
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
        # print(f"Warning: Prompt file not found: {prompt_file}")
        prompt = conf['DEFAULT']['prompt'] # a simple fallback prompt
    return prompt

def get_providers(conf):
    return conf.sections()

def get_models(conf):
    models = []
    for provider in conf.sections():
        if not conf.has_option(provider, 'models_available'):
            continue
        models_available_str = conf.get(provider, 'models_available')
        models_available_list = [model.strip() for model in models_available_str.split(',')]
        models += models_available_list
    return models

def get_provider_from_model(conf, model):
    for provider in conf.sections():
        models_available_str = conf.get(provider, 'models_available')
        models_available_list = [model.strip() for model in models_available_str.split(',')]
        if model in models_available_list:
            return provider
    return None

def get_default_provider(conf):
    if conf.has_option('DEFAULT', 'default_provider'):
        return conf.get('DEFAULT', 'default_provider')
    for provider in conf.sections():
        if conf.has_option(provider, 'default') and conf.getboolean(provider, 'default'):
            return provider
    return None



if __name__ == "__main__":
    cli(obj={})
