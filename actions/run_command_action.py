from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional

from base_classes import Completed, StepwiseAction


class RunCommandAction(StepwiseAction):
    """Slash command that runs a shell command and optionally saves the output."""

    def __init__(self, session):
        self.session = session
        self._state: Dict[str, Any] = {}

    # ---- Stepwise entry points ---------------------------------------------
    def start(self, args: Optional[Dict[str, Any]] = None, content: str = "") -> Completed:
        self._state.clear()
        command = self._extract_command(args, content)
        if not command:
            # Ask the user for a command; non-blocking UIs will raise InteractionNeeded.
            self._state['phase'] = 'awaiting_command'
            command = self.session.ui.ask_text('Enter command to run:')
        return self._handle_command_input(command)

    def resume(self, state_token: str, response: Any) -> Completed:
        value = self._normalize_response(response)
        phase = self._state.get('phase')

        if phase == 'awaiting_command':
            return self._handle_command_input(value)
        if phase == 'confirm_save':
            return self._handle_save_decision(value)
        if phase == 'ask_context_name':
            return self._finalize_save(value)

        # Unknown phase; treat as cancelled to avoid loops.
        return self._complete(cancelled=True)

    # ---- Internal helpers --------------------------------------------------
    def _extract_command(self, args: Optional[Any], content: str) -> str:
        if isinstance(args, dict):
            command = args.get('command') or args.get('cmd')
            if not command and 'arguments' in args:
                base_cmd = args.get('command', '')
                arguments = args.get('arguments', '')
                command = f"{base_cmd} {arguments}".strip()
            if command:
                return str(command).strip()
        elif isinstance(args, (list, tuple)) and args:
            return " ".join(str(a) for a in args).strip()
        elif isinstance(args, str) and args.strip():
            return args.strip()

        return (content or "").strip()

    def _handle_command_input(self, command: Any) -> Completed:
        command_str = (command or "").strip() if isinstance(command, str) else str(command or '').strip()
        if not command_str:
            try:
                self.session.ui.emit('status', {'message': 'Command execution cancelled.'})
            except Exception:
                pass
            return self._complete(cancelled=True)

        self._state['last_command'] = command_str
        self._state['phase'] = None

        output = self._execute_command(command_str)
        self._state['last_output'] = output
        self._emit_result_preview(output)

        return self._prompt_to_save()

    def _prompt_to_save(self) -> Completed:
        self._state['phase'] = 'confirm_save'
        decision = self.session.ui.ask_bool('Save this output to context?', default=False)
        return self._handle_save_decision(decision)

    def _handle_save_decision(self, decision: Any) -> Completed:
        should_save = self._parse_bool(decision)
        if not should_save:
            return self._complete(saved=False)

        self._state['phase'] = 'ask_context_name'
        name = self.session.ui.ask_text('Context name:', default='Command Output')
        return self._finalize_save(name)

    def _finalize_save(self, name: Any) -> Completed:
        output = self._state.get('last_output', '')
        command = self._state.get('last_command', '')
        context_name = str(name or '').strip() or 'Command Output'
        self._save_output(context_name, output)
        try:
            self.session.ui.emit('status', {'message': f"Output saved as '{context_name}'"})
        except Exception:
            pass
        return self._complete(saved=True, context_name=context_name, command=command, output=output)

    def _complete(self, saved: bool = False, context_name: Optional[str] = None,
                  command: Optional[str] = None, output: Optional[str] = None,
                  cancelled: bool = False) -> Completed:
        payload = {
            'ok': True,
            'saved': saved,
            'cancelled': cancelled,
            'command': command or self._state.get('last_command', ''),
            'output': output if output is not None else self._state.get('last_output', ''),
        }
        if context_name:
            payload['context_name'] = context_name
        self._state.clear()
        return Completed(payload)

    def _execute_command(self, command: str) -> str:
        shell = os.environ.get('SHELL') or '/bin/bash'
        try:
            result = subprocess.run(
                command,
                shell=True,
                executable=shell,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.getcwd(),
            )
        except subprocess.TimeoutExpired:
            return f"Command timed out after 60 seconds: {command}"
        except FileNotFoundError:
            return f"Command not found: {command}"
        except PermissionError:
            return f"Permission denied: {command}"
        except Exception as exc:
            return f"Error executing command '{command}': {exc}"

        parts = []
        if result.stdout:
            parts.append(f"STDOUT:\n{result.stdout.rstrip()}")
        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr.rstrip()}")
        if result.returncode != 0:
            parts.append(f"Exit code: {result.returncode}")
        if not parts:
            return f"Command completed successfully (exit code {result.returncode})"
        return "\n\n".join(parts)

    def _emit_result_preview(self, output: str) -> None:
        try:
            preview = output
            if len(output) > 2000:
                preview = output[:2000] + f"\n... ({len(output) - 2000} more characters)"
            self.session.ui.emit('status', {'message': f'\nCommand Output:\n{preview}'})
        except Exception:
            pass

    def _save_output(self, context_name: str, output: str) -> None:
        try:
            self.session.add_context('multiline_input', {
                'name': context_name or 'Command Output',
                'content': output,
            })
        except Exception:
            pass

    @staticmethod
    def _normalize_response(response: Any) -> Any:
        if isinstance(response, dict):
            if 'response' in response:
                return response['response']
            if 'value' in response:
                return response['value']
        return response

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {'1', 'y', 'yes', 'true', 'on'}:
                return True
            if normalized in {'0', 'n', 'no', 'false', 'off', ''}:
                return False
        return bool(value)
