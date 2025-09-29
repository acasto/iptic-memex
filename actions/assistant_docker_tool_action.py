from base_classes import InteractionAction
import subprocess
import shlex
import os
import shutil
from typing import Tuple, Optional, Set
from utils.tool_args import get_str


class AssistantDockerToolAction(InteractionAction):
    """Command tool that provides a Docker environment for command execution with persistence support"""

    def __init__(self, session) -> None:
        """Initialize the Docker tool action"""
        self.session = session
        self.token_counter = session.get_action('count_tokens')
        self.fs_handler = session.get_action('assistant_fs_handler')
        self._default_timeout = float(session.get_tools().get('timeout', 15))

        # Get base directory configuration (honor CLI override)
        base_dir = session.get_option('TOOLS', 'base_directory', fallback='working')
        self.base_dir = os.getcwd() if base_dir in ('working', '.') else os.path.expanduser(base_dir)

        # Initialize environment settings
        self._tmp_created_dirs: Set[str] = set()
        self._tmp_cleanup_registered: Set[str] = set()
        self._refresh_environment_config()

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        # Shares the 'cmd' tool name; selection is controlled by [TOOLS].cmd_tool
        return 'cmd'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        # Command schema is the same as the local CMD tool
        return {
            'args': ['command', 'arguments', 'desc'],
            'description': (
                "Execute a shell command inside Docker (ephemeral or persistent). Provide the program in 'command' "
                "and optional 'arguments'. Controlled via [TOOLS].docker_env."
            ),
            'required': ['command'],
            'schema': {
                'properties': {
                    'command': {"type": "string", "description": "Program to execute (e.g., 'echo', 'grep')."},
                    'arguments': {"type": "string", "description": "Space-delimited arguments string (quoted as needed)."},
                    'content': {"type": "string", "description": "Optional command string (alternative to args)."},
                    'desc': {"type": "string", "description": "Optional short description for UI/status; ignored by execution.", "default": ""}
                }
            },
            'auto_submit': True,
        }

    def _refresh_environment_config(self) -> None:
        """Refresh Docker environment configuration from current session settings"""
        # Get Docker environment configuration
        desired_env = self.session.get_tools().get('docker_env', 'ephemeral')
        # In Agent Mode, optionally force ephemeral to avoid contention
        try:
            force_ephemeral = bool(self.session.in_agent_mode()) and bool(
                self.session.get_option('AGENT', 'docker_always_ephemeral', True)
            )
        except Exception:
            force_ephemeral = False
        self.docker_env = 'ephemeral' if force_ephemeral else desired_env
        env_section = self.docker_env.upper()
        # Load persistence setting from the environment's config
        self.is_persistent = self.session.get_option(env_section, 'persistent', False)

        # Load Docker-specific configuration
        self.docker_image = self.session.get_option(env_section, 'docker_image', 'ubuntu:latest')
        self.docker_run_options = self.session.get_option(env_section, 'docker_run_options', '')
        self.container_name = None if not self.is_persistent else \
            self.session.get_option(env_section, 'docker_name', f'assistant-{self.docker_env}')

        # Temp directory handling
        default_mount = 'bind' if not self.is_persistent else 'none'
        tmp_mount = self.session.get_option(env_section, 'tmp_mount', default_mount)
        if isinstance(tmp_mount, str):
            cleaned = tmp_mount.strip().lower()
            self.tmp_mount = cleaned or default_mount
        else:
            self.tmp_mount = default_mount
        tmp_value = self.session.get_option(env_section, 'tmp_value', '.assistant-tmp')
        self.tmp_value = tmp_value if isinstance(tmp_value, str) and tmp_value else '.assistant-tmp'
        set_tmpdir_env = self.session.get_option(env_section, 'set_tmpdir_env', True)
        self.set_tmpdir_env = self._coerce_bool(set_tmpdir_env, default=True)

    @staticmethod
    def _coerce_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ('1', 'true', 'yes', 'on'):
                return True
            if lowered in ('0', 'false', 'no', 'off'):
                return False
        return default

    def _tmp_mount_args(self) -> list[str]:
        mode = (self.tmp_mount or 'none').lower()
        if mode == 'none':
            return []
        if mode == 'bind':
            host_base = self.tmp_value or '.assistant-tmp'
            session_id = getattr(self.session, 'session_uid', None) or getattr(self.session, 'user_data', {}).get('session_uid')
            host_base = os.path.abspath(os.path.join(self.base_dir, os.path.expanduser(host_base)))
            host_dir = host_base
            if session_id:
                host_dir = os.path.join(host_base, f"{session_id}.tmp")

            try:
                created = not os.path.isdir(host_dir)
                os.makedirs(host_dir, exist_ok=True)
                if created:
                    self._tmp_created_dirs.add(os.path.realpath(host_dir))
                try:
                    os.chmod(host_dir, 0o1777)
                except Exception:
                    pass
            except Exception:
                # If we cannot ensure the directory, skip mounting to avoid Docker failure
                return []

            if not os.path.isdir(host_dir):
                return []

            self._register_tmp_cleanup(host_dir, host_base)
            return ['-v', f'{host_dir}:/tmp']

        if mode == 'named':
            volume = self.tmp_value or 'assistant-tmp'
            session_id = getattr(self.session, 'session_uid', None) or getattr(self.session, 'user_data', {}).get('session_uid')
            if session_id:
                volume = f"{volume}-{session_id}"
            return ['-v', f'{volume}:/tmp']

        if mode == 'tmpfs':
            return ['--tmpfs', '/tmp:rw,mode=1777']

        return []

    def _register_tmp_cleanup(self, host_dir: str, host_base: str) -> None:
        if not host_dir:
            return
        real_dir = os.path.realpath(host_dir)
        if real_dir in self._tmp_cleanup_registered:
            return
        if real_dir not in self._tmp_created_dirs:
            return
        real_base = os.path.realpath(host_base)
        try:
            common = os.path.commonpath([real_dir, real_base])
        except Exception:
            return
        if common != real_base or real_dir == real_base:
            return

        def _cleanup():
            try:
                if os.path.isdir(real_dir):
                    shutil.rmtree(real_dir)
            except FileNotFoundError:
                pass

        try:
            self.session.register_cleanup_callback(_cleanup)
            self._tmp_cleanup_registered.add(real_dir)
        except Exception:
            pass

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
                mount_ro = self.session.get_option(self.docker_env.upper(), 'mount_readonly', 'false').lower() == 'true'
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

        # Refresh environment config before creating command
        self._refresh_environment_config()

        if not self.is_persistent:
            # Build ephemeral docker command
            docker_cmd = ["docker", "run", "--rm"]
            if self.docker_run_options:
                docker_cmd.extend(shlex.split(self.docker_run_options))

            # Check if read-only mount is specified in config
            mount_ro = self.session.get_option(self.docker_env.upper(), 'mount_readonly', False)
            mount_opts = ':ro' if mount_ro else ''
            docker_cmd.extend(["-v", f"{self.base_dir}:/workspace{mount_opts}"])
            docker_cmd.extend(self._tmp_mount_args())
            if self.set_tmpdir_env:
                docker_cmd.extend(["-e", "TMPDIR=/tmp"])
            docker_cmd.extend(["-w", "/workspace"])
            docker_cmd.append(self.docker_image)
            docker_cmd.extend(["/bin/bash", "-c", command_str])
        else:
            # Build persistent docker exec command
            docker_cmd = ["docker", "exec"]
            if self.set_tmpdir_env:
                docker_cmd.extend(["-e", "TMPDIR=/tmp"])
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
        # Build command from either content or arguments
        command_str = content
        if not command_str and args:
            cmd = get_str(args, 'command', '') or ''
            arguments = get_str(args, 'arguments', '') or ''
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
                self.session.add_context('assistant', {
                    'name': 'command_output',
                    'content': output
                })
        else:
            self.session.add_context('assistant', {
                'name': 'command_error',
                'content': f"Error: {output}"
            })
