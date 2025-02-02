from session_handler import InteractionAction
import subprocess
import shlex
import os
from typing import Optional


class AssistantCmdToolAction(InteractionAction):
    """
    Enhanced command tool with support for piping allowed commands
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')
        self.token_counter = session.get_action('count_tokens')
        self.fs_handler = session.get_action('assistant_fs_handler')
        self._default_timeout = float(session.get_tools().get('timeout', 15))

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
            'tr': {'args': [], 'exclude_opts': []},
            'wget': {'args': ['url'], 'exclude_opts': []},
            'curl': {'args': ['url'], 'exclude_opts': []},
            'echo': {'args': [], 'exclude_opts': []},
            'date': {'args': [], 'exclude_opts': []},
            'du': {'args': [], 'exclude_opts': []},
            'whois': {'args': ['domain'], 'exclude_opts': []},
            'openssl': {'args': ['command'], 'exclude_opts': []},
            'dig': {'args': ['domain'], 'exclude_opts': []},
            'unzip': {'args': ['file'], 'exclude_opts': []},
            'zip': {'args': ['file'], 'exclude_opts': []},
            'tar': {'args': ['file'], 'exclude_opts': []}
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
                resolved_path = self.fs_handler.resolve_path(arg, must_exist=False)
                if resolved_path is None:
                    return False, f"Path '{arg}' is not allowed"

        return True, None

    def execute_pipeline(self, pipeline):
        """Execute a validated command pipeline"""
        if not pipeline:  # Add check for empty pipeline
            return False, "Empty pipeline"

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

            # Get final output from last process with timeout
            if not processes:  # Add check for empty processes list
                return False, "No processes created"

            try:
                output, error = processes[-1].communicate(timeout=self._default_timeout)
                if processes[-1].returncode != 0:
                    return False, error or "Process failed with no error message"
                return True, output
            except subprocess.TimeoutExpired:
                for p in processes:
                    p.kill()
                return False, f"Command pipeline timed out after {self._default_timeout} seconds"

        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        finally:
            for p in processes:
                try:
                    p.kill()
                except (OSError, subprocess.SubprocessError):
                    continue

    def run(self, args: Optional[dict] = None, content: str = "") -> None:
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
            limit = int(self.session.get_tools().get('large_input_limit', 4000))

            if token_count > limit:
                if self.session.get_tools().get('confirm_large_input', True):
                    self.session.set_flag('auto_submit', False)
                    self.session.utils.output.write(f"File exceeds token limit ({limit}) for assistant. Auto-submit disabled.")
                    self.session.add_context('assistant', {
                        'name': 'command_output',
                        'content': output
                    })
                else:
                    self.session.add_context('assistant', {
                        'name': 'command_error',
                        'content': f"Output size ({token_count} tokens) exceeds limit of {limit}."
                    })
            else:
                self.session.add_context('assistant', {
                    'name': 'command_output',
                    'content': output
                })
        else:
            self.session.add_context('assistant', {
                'name': 'command_error',
                'content': f"Error: {output}"
            })
