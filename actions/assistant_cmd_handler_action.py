from session_handler import InteractionAction
import tempfile
import subprocess


class AssistantCmdHandlerAction(InteractionAction):
    """Handles execution of commands using temporary files."""
    
    def __init__(self, session):
        self.session = session
    
    def run(self, command, content, mode='w+', encoding='utf-8'):
        """
        Execute a command using a temporary file for input/output.
        
        Args:
            command: Command to execute (list format for subprocess)
            content: Content to write to temp file
            mode: File mode for temp file
            encoding: File encoding
            
        Returns:
            Command output or error message
        """
        kwargs = {'mode': mode}
        if 'b' not in mode and encoding:
            kwargs['encoding'] = encoding

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
                        check=True
                    )
                    return output.stdout.strip()
                except subprocess.CalledProcessError as e:
                    return f"Error: Command {' '.join(command)} failed with exit status {e.returncode}\n{e.stderr.strip()}"
        except OSError as e:
            raise OSError(f"Error creating or deleting temporary file: {e}")
