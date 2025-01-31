from session_handler import InteractionAction
import subprocess
import shlex
import os
from typing import Tuple, Optional


class AssistantDockerToolAction(InteractionAction):
    """Command tool that provides a Docker environment for command execution with persistence support"""

    def __init__(self, session) -> None:
        """Initialize the Docker tool action"""
        self.session = session
        self.token_counter = session.get_action('count_tokens')
        self.fs_handler = session.get_action('assistant_fs_handler')
        self._default_timeout = float(session.get_tools().get('timeout', 15))

        # Get Docker environment configuration
        self.docker_env = session.get_tools().get('docker_env', 'ephemeral')
        env_section = self.docker_env.upper()
        # Load persistence setting from the environment's config
        self.is_persistent = self.session.conf.get_option(env_section, 'persistent', False)

        # Get base directory configuration
        base_dir = session.get_tools().get('base_directory', 'working')
        self.base_dir = os.getcwd() if base_dir in ('working', '.') else os.path.expanduser(base_dir)

        # Load mode-specific configuration
        self._load_mode_config()

    def _load_mode_config(self) -> None:
        """Load configuration based on selected Docker environment"""
        env_section = self.docker_env.upper()
        self.docker_image = self.session.conf.get_option(env_section, 'docker_image', 'ubuntu:latest')
        self.docker_run_options = self.session.conf.get_option(env_section, 'docker_run_options', '')
        self.container_name = None if not self.is_persistent else \
            self.session.conf.get_option(env_section, 'docker_name', f'assistant-{self.docker_env}')

    def _check_container_exists(self) -> bool:
        """Check if the named container exists and is running"""
        if not self.container_name:
            return False

        try:
            cmd = ["docker", "ps", "-q", "-f", f"name={self.container_name}"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return bool(result.stdout.strip())
        except subprocess.SubprocessError:
            return False

    def _start_persistent_container(self) -> Tuple[bool, Optional[str]]:
        """Start a new persistent container if it doesn't exist"""
        try:
            if not self._check_container_exists():
                docker_cmd = ["docker", "run", "-d", "--name", self.container_name]

                # Add custom Docker run options
                if self.docker_run_options:
                    docker_cmd.extend(shlex.split(self.docker_run_options))

                # Check if read-only mount is specified in config
                mount_ro = self.session.conf.get_option(self.docker_env.upper(), 'mount_readonly', 'false').lower() == 'true'
                mount_opts = ':ro' if mount_ro else ''

                # Add volume mount and working directory
                docker_cmd.extend(["-v", f"{self.base_dir}:/workspace{mount_opts}"])
                docker_cmd.extend(["-w", "/workspace"])
                docker_cmd.append(self.docker_image)
                docker_cmd.extend(["/bin/bash", "-c", "tail -f /dev/null"])  # Keep container running

                try:
                    subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as e:
                    if "already in use" in e.stderr:
                        # Container might exist but not be running
                        try:
                            subprocess.run(["docker", "start", self.container_name], check=True)
                        except subprocess.CalledProcessError as start_error:
                            return False, f"Failed to start existing container: {start_error.stderr}"
                    else:
                        return False, f"Failed to create container: {e.stderr}"
            return True, None  # Container is now running (either existed or was started)
        except subprocess.SubprocessError as e:
            return False, f"Docker command failed: {str(e)}"

    def create_docker_command(self, command_str: str) -> Optional[list]:
        """Create a Docker command based on the current mode"""
        if not command_str.strip():
            return None

        if not self.is_persistent:
            # Build ephemeral docker command
            docker_cmd = ["docker", "run", "--rm"]
            if self.docker_run_options:
                docker_cmd.extend(shlex.split(self.docker_run_options))

            # Check if read-only mount is specified in config
            mount_ro = self.session.conf.get_option(self.docker_env.upper(), 'mount_readonly', False)
            mount_opts = ':ro' if mount_ro else ''
            docker_cmd.extend(["-v", f"{self.base_dir}:/workspace{mount_opts}"])
            docker_cmd.extend(["-w", "/workspace"])
            docker_cmd.append(self.docker_image)
            docker_cmd.extend(["/bin/bash", "-c", command_str])
        else:
            # Build persistent docker exec command
            docker_cmd = ["docker", "exec"]
            docker_cmd.extend(["-w", "/workspace"])
            docker_cmd.append(self.container_name)
            docker_cmd.extend(["/bin/bash", "-c", command_str])

        return docker_cmd

    def execute_command(self, command_str: str) -> Tuple[bool, str]:
        """Execute a command through Docker based on current mode"""
        if not command_str.strip():
            return False, "Empty command"

        try:
            if self.is_persistent:
                # Ensure persistent container is running
                success, error = self._start_persistent_container()
                if not success:
                    return False, f"Failed to start container {self.container_name}: {error}"

            docker_cmd = self.create_docker_command(command_str)
            if not docker_cmd:
                return False, "Invalid command"

            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            try:
                output, error = process.communicate(timeout=self._default_timeout)
                if process.returncode != 0:
                    combined_output = output + "\n" + error if output else error
                    return False, combined_output.strip()
                return True, output.strip()
            except subprocess.TimeoutExpired:
                process.kill()
                return False, f"Command timed out after {self._default_timeout} seconds"

        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)

    # Currently unused, but kept for potential future use
    # We might want to do some cleaning up eventually, but need to consider the level
    # of persistence we want to maintain
    def cleanup(self) -> None:
        """Clean up any persistent containers on exit"""
        if self.is_persistent and self.container_name:
            try:
                # Check if container exists before attempting removal
                result = subprocess.run(
                    ["docker", "ps", "-a", "-q", "-f", f"name={self.container_name}"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.stdout.strip():
                    # Stop container if it's running
                    subprocess.run(["docker", "stop", self.container_name],
                                   stderr=subprocess.DEVNULL,
                                   check=False)
                    # Remove container
                    subprocess.run(["docker", "rm", "-f", self.container_name],
                                   stderr=subprocess.DEVNULL,
                                   check=False)
            except subprocess.SubprocessError:
                # Best effort cleanup - don't raise if it fails
                pass

    def setup(self) -> None:
        """Set up the action - required by InteractionAction

        Verifies docker is available and the specified image exists.
        For persistent environments, ensures the container is created.
        """
        try:
            # Check if docker is available
            subprocess.run(["docker", "--version"], check=True, capture_output=True)

            if self.is_persistent:
                # For persistent environments, try to set up the container
                success, error = self._start_persistent_container()
                if not success:
                    raise RuntimeError(f"Failed to set up persistent container: {error}")
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"Docker is not available: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Setup failed: {str(e)}")

    def run(self, args: Optional[dict] = None, content: str = "") -> None:
        """Process and execute commands from either content or command/arguments"""
        # Build command from either content or command/arguments
        command_str = content
        if not command_str and args:
            cmd = args.get('command', '').strip()
            arguments = args.get('arguments', '').strip()
            command_str = f"{cmd} {arguments}".strip()

        if not command_str:
            self.session.add_context('assistant', {
                'name': 'command_error',
                'content': "No command provided"
            })
            return

        success, output = self.execute_command(command_str)

        if success:
            if not output:
                self.session.add_context('assistant', {
                    'name': 'command_output',
                    'content': "(Command executed successfully with no output)"
                })
            else:
                token_count = self.token_counter.count_tiktoken(output)
                max_input = self.session.get_tools().get('max_input', 4000)

                if token_count > max_input:
                    msg = (f"Output exceeds maximum token limit ({max_input}). "
                           "Try limiting output with head/tail or grep.")
                    self.session.add_context('assistant', {
                        'name': 'assistant_feedback',
                        'content': msg
                    })
                    truncated_output = output[:output.find('\n', len(output) // 2)]
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
