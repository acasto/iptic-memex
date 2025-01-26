from session_handler import InteractionAction
import tempfile
import subprocess


class AssistantCmdHandlerAction(InteractionAction):
    """Handles execution of commands using temporary files with timeout support."""

    def __init__(self, session):
        self.session = session
        self._default_timeout = self._get_default_timeout()

    def _get_default_timeout(self):
        """Get default timeout from config"""
        return float(self.session.conf.get_option('TOOLS', 'timeout', fallback=15))

    def run(self, command, content, mode='w+', encoding='utf-8', timeout=None):
        """
        Execute a command using a temporary file for input/output.

        Args:
            command: Command to execute (list format for subprocess)
            content: Content to write to temp file
            mode: File mode for temp file
            encoding: File encoding
            timeout: Command-specific timeout override (in seconds)

        Returns:
            Command output or error message
        """
        kwargs = {'mode': mode}
        if 'b' not in mode and encoding:
            kwargs['encoding'] = encoding

        # Use provided timeout or fall back to default
        effective_timeout = timeout if timeout is not None else self._default_timeout

        try:
            with tempfile.NamedTemporaryFile(**kwargs) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                temp_file.seek(0)
                try:
                    output = subprocess.run(
                        command,
                        stdin=temp_file,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=effective_timeout
                    )
                    return output.stdout.strip()
                except subprocess.TimeoutExpired:
                    return f"Error: Command {' '.join(command)} timed out after {effective_timeout} seconds"
                except subprocess.CalledProcessError as e:
                    return f"Error: Command {' '.join(command)} failed with exit status {e.returncode}\n{e.stderr.strip()}"
        except OSError as e:
            raise OSError(f"Error creating or deleting temporary file: {e}")
