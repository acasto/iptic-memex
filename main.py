import os
import click
from chat import Chat
from completion import Completion
from configparser import ConfigParser
from api_handler import OpenAIHandler

@click.group()
@click.option('-c', '--conf', help='Path to a custom configuration file')
@click.pass_context
def cli(ctx, conf):
    ctx.ensure_object(dict)
    ctx.obj['CONF'] = get_config(conf)

@cli.command()
@click.pass_context
@click.option('-r', '--raw', help="Don't use a preset prompt")
@click.option('-p', '--prompt', help="Filename from the prompt directory to use for completion")
# @click.argument("source", type=click.File("rt", encoding="utf-8")) # make contingent on --raw
def complete(ctx):
    config_manager = ctx.obj['CONF']

    # Initialize API handler based on config
    api_handler = OpenAIHandler(config_manager)
    completion = Completion(api_handler)
    # Interact with the completion
    completion.start()

@cli.command()
@click.pass_context
def chat(ctx):
    conf = ctx.obj['CONF']
    # Initialize API handler based on config
    api_handler = OpenAIHandler(conf)
    chat_session = Chat(api_handler)
    # Interact with the chat
    chat_session.start()

@cli.command()
@click.pass_context
def dump_config(ctx):
    conf = ctx.obj['CONF']
    print(conf['OPENAI']['api_chat_model']) # get a setting directly
    for section in conf.sections(): # dump the settings in the ini format
        print(f'[ {section} ]')
        for option in conf.options(section):
            value = conf.get(section, option)
            print(f'{option} = {value}')
        print()
    ## check the prompt
    print(get_prompt(conf, 'test.txt'))

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

def get_api_handler(api_type, conf):
    if api_type == "openai":
        return OpenAIHandler(conf)
    # elif api_type == "huggingface":
    #     return HuggingFaceHandler(conf)
    else:
        raise ValueError("Unsupported API type")



if __name__ == "__main__":
    cli(obj={})
