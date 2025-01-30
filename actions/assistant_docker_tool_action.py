from session_handler import InteractionAction
import subprocess
import shlex
import os


class AssistantDockerToolAction(InteractionAction):
    """
    Command tool that provides a sandboxed Docker environment for command execution
    """
    def __init__(self, session):
        self.session = session
        self.token_counter = session.get_action('count_tokens')
        self.fs_handler = session.get_action('assistant_fs_handler')
        self._default_timeout = float(session.conf.get_option('TOOLS', 'timeout', fallback=15))

        # Get Docker configuration
        self.docker_image = session.conf.get_option('TOOLS', 'docker_image', fallback='ubuntu:latest')
        self.docker_run_options = session.conf.get_option('TOOLS', 'docker_run_options', fallback='')

        # Get base directory configuration
        base_dir = session.conf.get_option('TOOLS', 'base_directory', fallback='working')
        if base_dir == 'working':
            self.base_dir = os.getcwd()
        elif base_dir == '.':
            self.base_dir = os.getcwd()
        else:
            self.base_dir = os.path.expanduser(base_dir)

    def create_docker_command(self, command_str):
        """Create a Docker command that will run the given command"""
        if not command_str.strip():
            return None

        # Build the docker command
        docker_cmd = ["docker", "run", "--rm"]
        
        # Add any custom Docker run options
        if self.docker_run_options:
            docker_cmd.extend(shlex.split(self.docker_run_options))
        
        # Mount the current working directory as /workspace in the container
        docker_cmd.extend(["-v", f"{self.base_dir}:/workspace:ro"])
        
        # Set working directory to /workspace
        docker_cmd.extend(["-w", "/workspace"])
        
        # Add the image name
        docker_cmd.append(self.docker_image)
        
        # Add the shell command to run
        docker_cmd.extend(["/bin/bash", "-c", command_str])
        
        return docker_cmd

    def execute_command(self, command_str):
        """Execute a command through Docker"""
        if not command_str.strip():
            return False, "Empty command"

        try:
            # Create the Docker command
            docker_cmd = self.create_docker_command(command_str)
            if not docker_cmd:
                return False, "Invalid command"
            
            # Execute the Docker command
            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            try:
                output, error = process.communicate(timeout=self._default_timeout)
                if process.returncode != 0:
                    # For non-zero exit codes, combine output and error
                    combined_output = output + "\n" + error if output else error
                    return False, combined_output.strip()
                return True, output.strip()
            except subprocess.TimeoutExpired:
                process.kill()
                return False, f"Command timed out after {self._default_timeout} seconds"

        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)

    def run(self, args: dict, content: str = ""):
        """Main entry point for command execution"""
        command = args.get('command', '').strip()
        raw_args = args.get('arguments', '').strip()

        # Combine command and arguments
        full_command = f"{command} {raw_args}".strip()

        # Execute and handle output
        success, output = self.execute_command(full_command)

        if success:
            if not output:
                self.session.add_context('assistant', {
                    'name': 'command_output',
                    'content': "(Command executed successfully with no output)"
                })
            else:
                # Check output size
                token_count = self.token_counter.count_tiktoken(output)
                max_input = self.session.conf.get_option('TOOLS', 'max_input', fallback=4000)

                if token_count > max_input:
                    msg = (f"Output exceeds maximum token limit ({max_input}). "
                          "Try limiting output with head/tail or grep.")
                    self.session.add_context('assistant', {
                        'name': 'assistant_feedback', 
                        'content': msg
                    })
                    # Return truncated output with warning
                    truncated_output = output[:output.find('\n', len(output)//2)]
                    self.session.add_context('assistant', {
                        'name': 'command_output',
                        'content': f"{truncated_output}\n[Output truncated due to length...]"
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
