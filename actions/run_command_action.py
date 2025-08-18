from base_classes import StepwiseAction, Completed
import subprocess
import shlex


class RunCommandAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    # Stepwise entry
    def start(self, args=None, content: str = "") -> Completed:
        # Determine command: args can be dict with 'command' or a list/string
        command = None
        if isinstance(args, dict):
            command = args.get('command') or args.get('cmd')
        elif isinstance(args, (list, tuple)) and args:
            command = " ".join(str(a) for a in args)
        elif isinstance(args, str):
            command = args

        if not command:
            # Prompt for command
            command = self.session.ui.ask_text("Enter command to run:")

        command = (command or "").strip()
        if not command:
            self.session.ui.emit('status', {'message': 'Command execution cancelled.'})
            return Completed({'ok': True, 'cancelled': True})

        output = self._execute_command(command)
        self._emit_result_preview(output)

        # In blocking UIs (CLI), offer to save; in non-blocking, return payload
        saved = False
        context_name = None
        if getattr(self.session.ui.capabilities, 'blocking', False):
            if self.session.ui.ask_bool("Save this output to context?", default=False):
                context_name = self.session.ui.ask_text("Context name:", default="Command Output")
                self._save_output(context_name, output)
                saved = True
                self.session.ui.emit('status', {'message': f"Output saved as '{context_name}'"})

        return Completed({'ok': True, 'command': command, 'output': output, 'saved': saved, 'context_name': context_name})

    # Resume after ask_* in non-blocking UIs
    def resume(self, state_token: str, response) -> Completed:
        # If resuming from the command prompt, response is the command string
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        command = str(response or '').strip()
        if not command:
            return Completed({'ok': True, 'cancelled': True})
        output = self._execute_command(command)
        self._emit_result_preview(output)
        # Do not prompt for save in non-blocking flows; return payload
        return Completed({'ok': True, 'command': command, 'output': output, 'saved': False})

    # --- Helpers ---------------------------------------------------------
    def _execute_command(self, command: str) -> str:
        try:
            args = shlex.split(command)
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            output = f"Stdout:\n{result.stdout}\n\nStderr:\n{result.stderr}"
            return output
        except subprocess.TimeoutExpired:
            return "Execution timed out after 30 seconds."
        except Exception as e:
            return f"An error occurred: {e}"

    def _emit_result_preview(self, output: str) -> None:
        try:
            self.session.ui.emit('status', {'message': '\nExecution Result:'})
            preview = output if len(output) <= 2000 else (output[:2000] + '...')
            self.session.ui.emit('status', {'message': preview})
        except Exception:
            pass

    def _save_output(self, context_name: str, output: str) -> None:
        self.session.add_context('multiline_input', {
            'name': context_name or 'Command Output',
            'content': output,
        })
