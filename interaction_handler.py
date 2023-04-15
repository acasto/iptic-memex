import os
import sys
import json
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
import tiktoken

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
        message = ""

        if self.session['interactive']:
            message = input("You: ")
        else:
            if 'message' in self.session:
                message = self.session['message']

        if self.session['stream']:
            if 'mode' in self.session and self.session['mode'] == 'chat':
                if 'load_file' in self.session:
                    messages = [{"role": "system", "content": prompt +" "+ message}]
                    for file in self.session['load_file']:
                        messages.append({"role": "user", "content": "context: " + file})
                else:
                    messages = [{"role": "system", "content": prompt },{"role": "user", "content": message}]
                response = self.api_handler.stream_chat(messages)
            else:
                if 'load_file' in self.session:
                    prompt = prompt
                    for file in self.session['load_file']:
                        prompt = prompt +" context: "+ file
                response = self.api_handler.stream_complete(prompt +" "+ message)

            if self.session['interactive']:
                click.echo(f"{label}: ", nl=False)

            # if in interactive mode do some syntax highlighting
            completion_text = ''
            if self.session['interactive']:
                for i, part in enumerate(process_streamed_response(response)):
                    if i < 0:
                        part = part.lstrip()
                    click.echo(part, nl=False)
                    completion_text += part
                    if 'stream_delay' in self.session:
                        time.sleep(self.session['stream_delay'])
            else:
                # iterate through the stream of events, lstrip() the first couple events to avoid the weird newline
                for i, event in enumerate(response):
                    if i < 1:
                        click.echo(event.lstrip(), nl=False)
                    else:
                        click.echo(event, nl=False)
                if 'stream_delay' in self.session:
                    time.sleep(self.session['stream_delay'])

            click.echo() # finish with a newline
        else:
            if 'mode' in self.session and self.session['mode'] == 'chat':
                if 'load_file' in self.session:
                    messages = [{"role": "system", "content": prompt}]
                    for file in self.session['load_file']:
                        messages.append({"role": "user", "content": f"context: {file}"})
                    messages.append({"role": "user", "content": message})

                else:
                    messages = [{"role": "system", "content": prompt },{"role": "user", "content": message}]
                response = self.api_handler.chat(messages)
            else:
                if 'load_file' in self.session:
                    prompt = prompt
                    for file in self.session['load_file']:
                        prompt = prompt +" context: "+ file
                response = self.api_handler.complete(prompt +" "+ message)
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
        commands = ["save", "load", "clear", "quit", "exit", "help", "?", "show messages", "show tokens"]
        if 'load_chat' in self.session:
            messages = self.load_chat(self.session['load_chat'])
            if messages is not None:
                for message in messages[1:]:
                    print(f"{message['role'].capitalize()}: {message['content']}\n")
        elif 'load_file' in self.session:
            # if loading files go through the list and append each as a {role: user, content: context: filename} message
            messages = [{"role": "system", "content": prompt}]
            for file in self.session['load_file']:
                messages.append({"role": "user", "content": "context: " + file})
            print("Loaded " + str(count_tokens(messages, self.session['model'])) + " tokens into context")
        else:
            messages = [{"role": "system", "content": prompt}]
        while True:
            ## compare the token count of hte current message to the context_window size and warn if the difference
            ## is smaller than max_tokens
            if 'context_window' in self.session:
                tokens_count = count_tokens(messages, self.session['model'])
                tokens_left = int(self.session['context_window']) - tokens_count
                if tokens_left < int(self.session['max_tokens']):
                    #print("\033[91m" + "Warning: the context window is smaller than max_tokens. This may result in incomplete responses." + "\033[0m")
                    print("\033[91m" + "Tokens left in context window: " + str(tokens_left) + "\033[0m")


            user_input = input("You: ")

            if user_input.strip() in commands:
                if user_input.strip() == "quit" or user_input.strip() == "exit":
                    break
                if user_input.strip() == "save":
                    self.activate_completion()
                    default_filename = "chat_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + self.session['chats_extension']
                    while True:
                        filename = input(f"Enter a filename ({default_filename}): ")
                        if filename == "list" or filename == "ls":
                            self.list_chats(self.session['chats_directory'])
                            continue
                        else:
                            break
                    if filename == "exit" or filename == "quit":
                        continue
                    if filename == "":
                        filename = default_filename
                    if not filename.endswith(self.session['chats_extension']):
                        filename += self.session['chats_extension']
                    self.save_chat(messages, filename)
                    continue
                if user_input.strip() == "load":
                    self.activate_completion()
                    while True:
                        filename = input("Enter a filename: ")
                        if filename == "list" or filename == "ls":
                            self.list_chats(self.session['chats_directory'])
                            continue
                        else:
                            break
                    if filename == "exit" or filename == "quit":
                        continue
                    if not filename.endswith(self.session['chats_extension']):
                        filename += self.session['chats_extension']
                    loading = self.load_chat(filename)
                    if loading is not None:
                        messages = loading
                        # print messages skipping the first 'system' message
                        for message in messages[1:]:
                            click.echo(f"{message['role'].capitalize()}: {message['content']}\n")
                    continue
                if user_input.strip() == "clear":
                    messages = [{"role": "system", "content": prompt}]
                    os.system('cls' if os.name == 'nt' else 'clear')
                    click.echo("Context cleared")
                    continue
                if user_input.strip() == "show messages":
                    click.echo(messages)
                    continue
                if user_input.strip() == "show tokens":
                    print(str(count_tokens(messages, self.session['model'])) + " tokens in session")
                    continue

                if user_input.strip() == "help" or user_input.strip() == "?":
                    click.echo("Commands:")
                    click.echo("save - save the chat history to a file")
                    click.echo("load - load a chat history from a file")
                    click.echo("clear - clear the context and start over")
                    click.echo("show messages - dump session messages")
                    click.echo("show tokens - show number of tokens in session")
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
        include = [ 'model', 'temperature', 'max_tokens', 'endpoint' ]
        for parameter in session_info:
            if parameter in include:
                contents += f"{parameter}: {session_info[parameter]}\n"
        contents += '_' * 80 + '\n'
        for i, message in enumerate(messages):
            if 'load_file' in self.session:
                if 0 < i < len(self.session['load_file']) + 1:
                    contents += f"user: *** MISSING CONTEXT *** {self.session['load_file_name'][i-1]} not present.\n\n\n"
                    continue
            contents += f"{message['role']}: {self.remove_ansi_codes(message['content'])}\n\n\n"
        with open(filename, "w") as f:
            f.write(contents)

    def load_chat(self, filename):
        if not os.path.isabs(filename):
            filename = os.path.join(self.session['chats_directory'], filename)
        if not os.path.exists(filename) or os.path.isdir(filename):
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

    @staticmethod
    def remove_ansi_codes(text):
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    @staticmethod
    def list_chats(directory):
        if not os.path.isabs(directory):
            directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), directory)
        files = os.listdir(directory)
        for file in files:
            print(file)

    def activate_completion(self):
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.set_completer(self.directory_completer)
            readline.parse_and_bind("tab: complete")

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
    # Extract the content inside the triple backticks and the language specifier (if any)
    match = re.match(r"^```(\w+)?\n(.*?)\n?```$", code_block, re.DOTALL)
    if match:
        language = match.group(1)
        code_content = match.group(2)
    else:
        language = None
        code_content = code_block

    try:
        if language:
            # If the language is PHP, we need to enable startinline
            if language.lower() == "php":
                lexer = get_lexer_by_name(language, startinline=True)
            else:
                lexer = get_lexer_by_name(language, stripall=True)
        else:
            lexer = guess_lexer(code_content)
    except ClassNotFound:
        lexer = get_lexer_by_name("text", stripall=True)

    formatter = TerminalFormatter()
    highlighted_code = highlight(code_content, lexer, formatter)

    # Prepend and append triple backticks and language specifier (if any)
    formatted_code_block = f"```{language or ''}\n{highlighted_code}```"
    return formatted_code_block


def process_streamed_response(response):
    buffer = []
    backtick_buffer = []
    inside_code_block = False

    for event in response:
        for char in event:
            if char == '`':
                backtick_buffer.append(char)
                if len(backtick_buffer) == 3:
                    # Include the triple backticks in the buffer
                    if inside_code_block:
                        buffer.extend(backtick_buffer)
                    inside_code_block = not inside_code_block
                    if not inside_code_block:
                        code_block = ''.join(buffer)
                        formatted_code_block = format_code_block(code_block)
                        yield formatted_code_block
                        buffer = []
                    else:
                        # Start a new code block with the triple backticks
                        buffer.extend(backtick_buffer)
                    backtick_buffer = []
            else:
                if inside_code_block:
                    buffer.append(char)
                else:
                    yield char

                # Reset backtick buffer
                backtick_buffer = []

    # Check if there's an unclosed code block
    if inside_code_block:
        # Close the code block with three backticks
        buffer.extend(['\n', '`', '`', '`'])
        code_block = ''.join(buffer)
        formatted_code_block = format_code_block(code_block)
        yield formatted_code_block
    # Flush remaining backticks if any
    elif backtick_buffer:
        yield ''.join(backtick_buffer)


def count_tokens(messages, model="gpt-3.5-turbo-0301"):
    """Returns the number of tokens used"""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        print("Warning: model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo":
        # print("Warning: gpt-3.5-turbo may change over time. Returning num tokens assuming gpt-3.5-turbo-0301.")
        return count_tokens(messages, model="gpt-3.5-turbo-0301")
    elif model == "gpt-4":
        # print("Warning: gpt-4 may change over time. Returning num tokens assuming gpt-4-0314.")
        return count_tokens(messages, model="gpt-4-0314")
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif model == "gpt-4-0314":
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        raise NotImplementedError(f"""count_tokens() is not implemented for model {model}. See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens.""")

    num_tokens = 0
    # if we're given a str for a completion prompt, convert it to a list
    if not isinstance(messages, list):
        messages = [messages]
    # process list of messages
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "role":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens
