from session_handler import InteractionAction
import subprocess
import tempfile
import os
from typing import Optional, Dict, Any


class AssistantCompletionAction(InteractionAction):
    """Handles completion requests for various assistant tools"""

    def __init__(self, session):
        self.session = session
        self._default_model = self.session.conf.get_option('TOOLS', 'completion_model', fallback=None)
        self.token_counter = session.get_action('count_tokens')

    def run(self, content: str, prompt: Optional[str] = None,
            model: Optional[str] = None, completion_args: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Run a completion request
        """
        try:
            # Check content size
            token_count = self.token_counter.count_tiktoken(content)
            max_input = self.session.conf.get_option('TOOLS', 'max_input', fallback=4000)

            if token_count > max_input:
                self.session.add_context('assistant', {
                    'name': 'completion_error',
                    'content': f'Content exceeds maximum token limit ({max_input})'
                })
                return None

            # Write content to temp file
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
                temp_file.write(content)
                temp_file.flush()

                try:
                    cmd = ['memex']

                    # Add model if specified
                    if model:
                        cmd.extend(['-m', model])
                    elif self._default_model:
                        cmd.extend(['-m', self._default_model])

                    # Add prompt if specified
                    if prompt:
                        cmd.extend(['-p', prompt])

                    # Add any additional completion arguments
                    if completion_args:
                        for key, value in completion_args.items():
                            cmd.extend([f'--{key}', str(value)])

                    # Add file argument
                    cmd.extend(['-f', temp_file.name])

                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    return result.stdout if result.returncode == 0 else None

                finally:
                    # Clean up temp file
                    try:
                        os.unlink(temp_file.name)
                    except OSError:
                        pass

        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'completion_error',
                'content': f'Completion failed: {str(e)}'
            })
            return None
        