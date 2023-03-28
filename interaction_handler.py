import os
import json
import readline
import re
import time
import click
from datetime import datetime
from abc import ABC, abstractmethod
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import TerminalFormatter
from pygments.util import ClassNotFound
from pygments.lexers import guess_lexer

class InteractionHandler(ABC):
    """
    Abstract class for interaction handlers
    """
    @abstractmethod
    def start(self, message):
        pass

class Completion(InteractionHandler):
    """
    Completion interaction handler
    """
    def __init__(self, session):
        self.session = session
        self.api_handler = session['api_handler']

    def start(self, prompt):
        label = self.session['response_label']
        if self.session['interactive']:
            prompt += " User: "
            prompt += input("You: ")

        if self.session['stream']:
            if 'mode' in self.session and self.session['mode'] == 'chat':
                messages = [{"role": "user", "content": prompt}]
                response = self.api_handler.stream_chat(messages)
            else:
                response = self.api_handler.stream_complete(prompt)

            completion_text = ''

            print(f"{label}: ", end="", flush=True)

            # if in interactive mode do some syntax highlighting
            if self.session['interactive']:
                for i, part in enumerate(process_streamed_response(response)):
                    if i < 4:
                        part = part.lstrip()
                    click.echo(part, nl=False)
                    completion_text += part
                    if 'stream_delay' in self.session:
                        time.sleep(self.session['stream_delay'])
            else:
                # iterate through the stream of events, lstrip() the first couple events to avoid the weird newline
                for i, event in enumerate(response):
                    if i < 2:
                        click.echo(event.lstrip(), nl=False)
                    else:
                        click.echo(event, nl=False)
                if 'stream_delay' in self.session:
                    time.sleep(self.session['stream_delay'])

            click.echo() # finish with a newline
        else:
            if 'mode' in self.session and self.session['mode'] == 'chat':
                messages = [{"role": "user", "content": prompt}]
                response = self.api_handler.chat(messages)
            else:
                response = self.api_handler.complete(prompt)
            # format code blocks if in interactive mode
            if self.session['interactive']:
                code_block_regex = re.compile(r'```(.+?)```', re.DOTALL)
                response = code_block_regex.sub(
                 lambda match: format_code_block(match.group(1)), response)
            click.echo(f"{label}: "  + response)



class Chat(InteractionHandler):
    """
    Chat interaction handler
    """
    def __init__(self, session):
        self.session = session
        self.api_handler = session['api_handler']

    def start(self, prompt):
        label = self.session['response_label']
        commands = ["save", "load", "quit", "exit", "help", "?"]
        if 'load_chat' in self.session:
            messages = self.load_chat(self.session['load_chat'])
            if messages is not None:
                for message in messages[1:]:
                    print(f"{message['role'].capitalize()}: {message['content']}\n")
        else:
            messages = [{"role": "system", "content": prompt}]
        readline.set_completer(self.directory_completer)
        readline.parse_and_bind("tab: complete")
        while True:
            user_input = input("You: ")

            if user_input.strip() in commands:
                if user_input.strip() == "quit" or user_input.strip() == "exit":
                    break
                if user_input.strip() == "save":
                    default_filename = "chat_" + datetime.now().strftime("%Y-%m-%d_%s") + self.session['chats_extension']
                    filename = input(f"Enter a filename ({default_filename}): ")
                    if filename == "":
                        filename = default_filename
                    if not filename.endswith(self.session['chats_extension']):
                        filename += self.session['chats_extension']
                    self.save_chat(messages, filename)
                    continue
                if user_input.strip() == "load":
                    filename = input("Enter a filename: ")
                    loading = self.load_chat(filename)
                    if loading is not None:
                        messages = loading
                        # print messages skipping the first 'system' message
                        for message in messages[1:]:
                            click.echo(f"{message['role'].capitalize()}: {message['content']}\n")

                    continue
                if user_input.strip() == "help" or user_input.strip() == "?":
                    click.echo("Commands:")
                    click.echo("save - save the chat history to a file")
                    click.echo("load - load a chat history from a file")
                    click.echo("quit - quit the chat")
                    continue

            messages.append({"role": "user", "content": user_input})

            if self.session['stream']:
                response = self.api_handler.stream_chat(messages)

                click.echo(f"\n{label}: ", nl=False)
                completion_text = ''
                for part in process_streamed_response(response):
                    click.echo(part, nl=False)
                    completion_text += part
                    if 'stream_delay' in self.session:
                          time.sleep(self.session['stream_delay'])

                click.echo("\n")  # finish with a newline
                messages.append({"role": "assistant", "content": completion_text})
            else:
                response = self.api_handler.chat(messages)
                code_block_regex = re.compile(r'```(.+?)```', re.DOTALL)
                response = code_block_regex.sub(
                    lambda match: format_code_block(match.group(1)), response)
                click.echo(f"\n{label}: " + response + "\n")
                messages.append({"role": "assistant", "content": response})


    def save_chat(self, messages, filename):
        session_info = self.session
        if not os.path.isabs(filename):
            filename = os.path.join(session_info['chats_directory'], filename)
        contents = str()
        exclude = ['api_key', 'api_handler', 'chats_directory', 'prompt', 'load_chat', 'chats_extension', 'stream', 'stream_delay']
        for parameter in session_info:
            if parameter not in exclude:
                contents += f"{parameter}: {session_info[parameter]}\n"
        contents += '_' * 80 + '\n'
        for message in messages:
            contents += f"{message['role']}: {message['content']}\n\n"
        with open(filename, "w") as f:
            f.write(contents)

    def load_chat(self, filename):
        if not os.path.isabs(filename):
            filename = os.path.join(self.session['chats_directory'], filename)
        if not os.path.exists(filename):
            print("File does not exist.")
            return
        print("Loading chat history...")
        with open(filename, "r") as f:
            contents = f.read()
            split_char = '_'
            min_char = 5
            message_section = re.split(rf'{split_char}{{{min_char},}}', contents)
            pattern = re.compile(r'(system|user|assistant):(.+?)(?=(?:system|user|assistant):|$)', re.DOTALL)
            message_parts = pattern.findall(message_section[1])
            messages = []
            for message in message_parts:
                messages.append({"role": message[0].strip(), "content": message[1].strip()})
            return messages

    def directory_completer(self, text, state):
        if not os.path.isabs(self.session['chats_directory']):
            chats_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.session['chats_directory'])
        else:
            chats_directory = self.session['chats_directory']
        files_and_dirs = os.listdir(chats_directory)
        options = [x for x in files_and_dirs if x.startswith(text)]
        try:
            return options[state]
        except IndexError:
            return None

#######################
#  Helper functions   #
#######################

def format_code_block(code_block):
    try:
        lexer = guess_lexer(code_block)
    except ClassNotFound:
        lexer = get_lexer_by_name("text", stripall=True)
    formatter = TerminalFormatter()
    return highlight(code_block, lexer, formatter)

def process_streamed_response(response):
    buffer = []
    backtick_buffer = []
    inside_code_block = False

    for event in response:
        for char in event:
            if char == '`':
                backtick_buffer.append(char)
            else:
                if len(backtick_buffer) >= 3:
                    inside_code_block = not inside_code_block

                    if not inside_code_block:
                        code_block = ''.join(buffer)
                        formatted_code_block = format_code_block(code_block)
                        yield formatted_code_block
                        buffer = []
                else:
                    # Flush stray backticks
                    if inside_code_block:
                        buffer.extend(backtick_buffer)
                    else:
                        yield ''.join(backtick_buffer)

                # Reset backtick buffer
                backtick_buffer = []

                if inside_code_block:
                    buffer.append(char)
                else:
                    yield char

    # Flush remaining backticks if any
    if backtick_buffer:
        if inside_code_block:
            buffer.extend(backtick_buffer)
        else:
            yield ''.join(backtick_buffer)
