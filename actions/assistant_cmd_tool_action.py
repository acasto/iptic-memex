from session_handler import InteractionAction
import subprocess
import shlex
import os


class AssistantCmdToolAction(InteractionAction):
    """
    Enhanced command tool with support for piping allowed commands
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')
        self.token_counter = self.session.get_action('count_tokens')
        self.fs = session.utils.fs

        # Get base directory configuration
        base_dir = session.conf.get_option('TOOLS', 'base_directory', fallback='working')
        if base_dir == 'working':
            self.base_dir = os.getcwd()
        elif base_dir == '.':
            self.base_dir = os.getcwd()
        else:
            self.base_dir = os.path.expanduser(base_dir)

        # Define allowed commands with their validators and handlers
        self.allowed_commands = {
            'find': {'args': ['path', 'options'], 'exclude_opts': ['-exec', '-ok']},
            'ls': {'args': ['path'], 'exclude_opts': []},
            'pwd': {'args': [], 'exclude_opts': []},
            'cat': {'args': ['file'], 'exclude_opts': []},
            'grep': {'args': ['pattern', 'file'], 'exclude_opts': []},
            'head': {'args': ['file'], 'exclude_opts': []},
            'tail': {'args': ['file'], 'exclude_opts': []},
            'wc': {'args': [], 'exclude_opts': []},
            'sort': {'args': [], 'exclude_opts': []},
            'uniq': {'args': [], 'exclude_opts': []},
            'cut': {'args': [], 'exclude_opts': []},
            'tr': {'args': [], 'exclude_opts': []}
        }

    @staticmethod
    def parse_pipeline(cmd_string):
        """Parse a command pipeline into individual commands"""
        if '|' not in cmd_string:
            return [cmd_string]

        # Split the command by pipes while preserving quoted strings
        commands = []
        current_cmd = []
        tokens = shlex.split(cmd_string, posix=True)

        for token in tokens:
            if token == '|':
                if current_cmd:
                    commands.append(' '.join(current_cmd))
                    current_cmd = []
            else:
                current_cmd.append(token)

        if current_cmd:
            commands.append(' '.join(current_cmd))

        return commands

    def validate_command(self, cmd_parts):
        """Validate a single command from the pipeline"""
        if not cmd_parts:
            return False, "Empty command"

        cmd_name = cmd_parts[0]
        if cmd_name not in self.allowed_commands:
            return False, f"Command '{cmd_name}' not allowed"

        # Validate command-specific arguments and options
        cmd_config = self.allowed_commands[cmd_name]
        options = [arg for arg in cmd_parts[1:] if arg.startswith('-')]
        non_options = [arg for arg in cmd_parts[1:] if not arg.startswith('-')]

        # Check for options that aren't allowed
        for opt in options:
            if opt in cmd_config['exclude_opts']:
                return False, f"Option '{opt}' not allowed for {cmd_name}"

        # Check if any arguments look like paths and validate them
        for arg in non_options:
            if os.path.sep in arg or arg.startswith('~'):
                # Try to resolve the path
                try:
                    resolved_path = os.path.abspath(os.path.expanduser(arg))
                    if not self.fs.is_path_in_base(self.base_dir, resolved_path):
                        return False, f"Path '{arg}' is outside allowed directory"
                except Exception as e:
                    return False, f"Error validating path '{arg}': {str(e)}"

        return True, None

    def execute_pipeline(self, pipeline):
        """Execute a validated command pipeline"""
        processes = []
        prev_process = None

        try:
            for i, cmd in enumerate(pipeline):
                cmd_parts = shlex.split(cmd)
                valid, error = self.validate_command(cmd_parts)
                if not valid:
                    return False, error

                # Set up the process
                stdin = prev_process.stdout if prev_process else None
                stdout = subprocess.PIPE if i < len(pipeline) - 1 else subprocess.PIPE

                process = subprocess.Popen(
                    cmd_parts,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=subprocess.PIPE,
                    text=True
                )

                processes.append(process)
                if prev_process and prev_process.stdout:
                    prev_process.stdout.close()
                prev_process = process

            # Get final output from last process
            output, error = processes[-1].communicate()

            if processes[-1].returncode != 0:
                return False, error

            return True, output

        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        finally:
            for p in processes:
                try:
                    p.kill()
                except (OSError, subprocess.SubprocessError):
                    continue

    def run(self, args: dict, content: str = ""):
        """Main entry point for command execution"""
        command = args.get('command', '').strip()
        raw_args = args.get('arguments', '').strip()

        # Combine command and arguments
        full_command = f"{command} {raw_args}".strip()

        # Parse the pipeline
        pipeline = self.parse_pipeline(full_command)

        # Execute and handle output
        success, output = self.execute_pipeline(pipeline)

        if success:
            # Check output size
            token_count = self.token_counter.count_tiktoken(output)
            max_input = self.session.conf.get_option('TOOLS', 'max_input', fallback=4000)

            if token_count > max_input:
                msg = f"Output exceeds maximum token limit ({max_input}). Try limiting output with head/tail."
                self.session.add_context('assistant', {'name': 'assistant_feedback', 'content': msg})
            else:
                self.session.add_context('assistant', {'name': 'command_output', 'content': output})
        else:
            self.session.add_context('assistant', {'name': 'command_error', 'content': f"Error: {output}"})
