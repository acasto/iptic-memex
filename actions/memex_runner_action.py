import subprocess
import sys
import os
from base_classes import InteractionAction

class MemexRunnerAction(InteractionAction):
    """An action for running the memex tool as a subprocess."""

    def __init__(self, session):
        self.session = session
        self._main_py_path = self._find_main_py()

    def _find_main_py(self):
        """Find the absolute path to main.py at the project root."""
        # The action file is in /actions, so two levels up is the project root.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_py_path = os.path.join(project_root, 'main.py')
        if not os.path.exists(main_py_path):
            return None
        return main_py_path

    def run(self, *args, **kwargs):
        """
        Runs the memex command with the given arguments.

        :param args: A list of arguments to pass to the memex command (e.g., ['-m', 'model', '-f', 'file']).
        :param kwargs: Additional keyword arguments for subprocess.run (e.g., capture_output=True).
        :return: The result of the subprocess.run call.
        """
        if not self._main_py_path:
            raise FileNotFoundError("Could not find main.py at the project root.")

        command = [sys.executable, self._main_py_path] + list(args)

        # Default kwargs for subprocess, can be overridden
        default_kwargs = {
            'capture_output': True,
            'text': True,
            'check': False
        }
        final_kwargs = {**default_kwargs, **kwargs}

        try:
            result = subprocess.run(command, **final_kwargs)
            return result
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            try:
                self.session.ui.emit('error', {'message': f"Error running memex command: {e}"})
            except Exception:
                pass
            return None
