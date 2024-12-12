from session_handler import InteractionAction
import re
import subprocess


class AssistantSubcommandsAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.commands = {
            "DATETIME": {
                "args": [],
                "function": {"type": "method", "name": "handle_datetime_command"}
            },
            # Just an example, probably not recommended to let assistant run arbitrary bash commands
            # "BASH_COMMAND": {
            #    "args": ["command"],
            #    "function": {"type": "method", "name": "handle_bash_command"}
            # },
            "UPTIME": {
                "args": [],
                "function": {"type": "method", "name": "handle_uptime_command"}
            },
            "ASK_AI": {
                "args": ["model", "question"],
                "function": {"type": "method", "name": "handle_ask_ai_command"}
            }
            # Add more commands here
        }

    def run(self, response: str = None):
        # Parse commands in the response
        parsed_commands = self.parse_commands(response, self.commands)

        # Process commands
        for command in parsed_commands:
            if command['command'] in self.commands:
                command_info = self.commands[command['command']]
                if command_info["function"]["type"] == "method":
                    method = getattr(self, command_info["function"]["name"])
                    method(command['args'])
                else:
                    action = self.session.get_action(command_info["function"]["name"])
                    action.run(command['args'])

        # Final processing: check for code blocks and reprint if necessary
        if '```' in response:
            if 'highlighting' in self.params and self.params['highlighting'] is True:
                self.session.get_action('reprint_chat').run()

    @staticmethod
    def parse_commands(text, commands):
        # Regular expression to match commands with or without arguments
        # pattern = r'###([A-Z0-9_]+)###'
        pattern = r'(?<!["\'`])###([A-Z0-9_]+)###(?!["\'`])'

        # Find all commands in the text
        matches = re.finditer(pattern, text)

        command_stack = []

        for match in matches:
            command_name = match.group(1)

            # Check if the command is valid
            if command_name in commands:
                # Check if the command has arguments
                if commands[command_name]["args"]:
                    # Look for arguments
                    start_index = match.end()
                    args_str = ""
                    for char in text[start_index:]:
                        if char == "#":
                            break
                        args_str += char
                    # Parse arguments
                    args_dict = {}
                    pairs = re.findall(r'(\w+)="([^"]*)"|(\w+)=(\S+)', args_str)
                    for pair in pairs:
                        key, value, key2, value2 = pair
                        if key:
                            args_dict[key] = value
                        else:
                            args_dict[key2] = value2
                    # Add command with arguments to the stack
                    command_stack.append({
                        'command': command_name,
                        'args': args_dict
                    })
                else:
                    # If the command has no arguments, add it to the stack as is
                    command_stack.append({
                        'command': command_name,
                        'args': {}
                    })
            else:
                # If the command is not valid, skip it
                continue

        return command_stack

    def handle_datetime_command(self, args=None):
        # Add the current date and time to the assistant context
        from datetime import datetime
        timestamp = "Current date and time: " + datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        self.session.add_context('assistant', {'name': 'assistant_context', 'content': timestamp})

    def handle_uptime_command(self, args=None):
        # Run the uptime command and add the output to the context
        output = subprocess.run('uptime', shell=True, capture_output=True, text=True)
        if output.stdout:
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': output.stdout})
        if output.stderr:
            self.session.add_context('assistant', {'name': 'assistant_context', 'content': output.stderr})

    def handle_ask_ai_command(self, args):
        # Handle the ask_ai command
        model = args.get('model', 'claude')
        question = args.get('question', '')
        # Get output by running the bash command: echo "<question>" | memex -m <model> -f -"
        output = subprocess.run(f'echo "{question}" | memex -m {model} -f -', shell=True, capture_output=True, text=True)
        self.session.add_context('assistant', {'name': 'assistant_context', 'content': output.stdout})

    def handle_bash_command(self, args):
        # Run the bash command and add the output to the context
        command = args['command']
        output = subprocess.run(command, shell=True, capture_output=True, text=True)
        if output.stdout:
            self.session.add_context('multiline_input', {
                'name': 'Bash Output',
                'content': output.stdout
            })
        if output.stderr:
            self.session.add_context('multiline_input', {
                'name': 'Bash Error',
                'content': output.stderr
            })
