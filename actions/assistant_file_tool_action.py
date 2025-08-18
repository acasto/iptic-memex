from __future__ import annotations

from base_classes import StepwiseAction, Completed
import subprocess
import os
import tempfile
from typing import Any, Dict


class AssistantFileToolAction(StepwiseAction):
    """
    File operations with optional confirmations, stepwise-capable for Web/TUI.
    Modes: read, write, append, edit, summarize, delete, rename, copy
    """

    def __init__(self, session):
        self.session = session
        self.fs_handler = session.get_action('assistant_fs_handler')
        self.token_counter = session.get_action('count_tokens')
        self.memex_runner = session.get_action('memex_runner')

    # ---- Stepwise protocol -------------------------------------------------
    def start(self, args: Dict, content: str = "") -> Completed:
        mode = str(args.get('mode', '')).lower()
        filename = args.get('file', '')
        if not filename:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No filename provided'})
            return Completed({'ok': False, 'error': 'No filename provided'})

        handlers = {
            'read': self._handle_read,
            'write': lambda f: self._handle_write(f, content),
            'edit': lambda f: self._handle_edit(f, content),
            'append': lambda f: self._handle_append(f, content),
            'summarize': self._handle_summary,
            'delete': lambda f: self._handle_delete(f, str(args.get('recursive', '')).lower() == 'true'),
            'rename': lambda f: self._handle_rename(f, args.get('new_name')),
            'copy': lambda f: self._handle_copy(f, args.get('new_name')),
        }
        handler = handlers.get(mode)
        if not handler:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Invalid mode: {mode}'})
            return Completed({'ok': False, 'error': f'Invalid mode: {mode}'})
        if mode in ('rename', 'copy') and not args.get('new_name'):
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'New name required for {mode} operation'})
            return Completed({'ok': False, 'error': f'New name required for {mode} operation'})
        handler(filename)
        return Completed({'ok': True, 'mode': mode, 'file': filename})

    def resume(self, state_token: str, response: Any) -> Completed:
        try:
            if isinstance(response, dict) and 'state' in response:
                state = response.get('state') or {}
                args = (state.get('args') or {})
                content = (state.get('content') or '')
                user_resp = response.get('response')
                if isinstance(user_resp, bool) and not user_resp:
                    self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'User canceled operation'})
                    return Completed({'ok': True, 'canceled': True})

                mode = str(args.get('mode', '')).lower()
                filename = args.get('file') or args.get('path') or ''
                if mode in ('write', 'append'):
                    if mode == 'write':
                        self._handle_write(filename, content, force=True)
                    else:
                        self._handle_append(filename, content, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'confirmed': True})
                if mode == 'delete':
                    recursive = str(args.get('recursive', '')).lower() == 'true'
                    self._handle_delete(filename, recursive, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'confirmed': True})
                if mode == 'rename':
                    new_name = args.get('new_name')
                    self._handle_rename(filename, new_name, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'new_name': new_name, 'confirmed': True})
                if mode == 'copy':
                    new_name = args.get('new_name')
                    self._handle_copy(filename, new_name, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'new_name': new_name, 'confirmed': True})
                if mode == 'read':
                    if filename:
                        self.session.add_context('file', filename)
                        try:
                            self.session.ui.emit('status', {'message': f'Loaded file: {filename}'})
                        except Exception:
                            pass
                        return Completed({'ok': True, 'file': filename, 'confirmed': True})
                    return Completed({'ok': False, 'error': 'Missing filename on resume'})
        except Exception:
            pass
        return Completed({'ok': True, 'resumed': True})

    # ---- Handlers ----------------------------------------------------------
    def _handle_read(self, filename: str) -> None:
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Reading {filename}...'})
        except Exception:
            pass
        content = self.fs_handler.read_file(filename)
        if content is None:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to read file: {filename}'})
            return

        token_count = self.token_counter.count_tiktoken(content)
        limit = int(self.session.get_tools().get('large_input_limit', 4000))
        if token_count > limit:
            if self.session.get_tools().get('confirm_large_input', True):
                confirmed = self.session.ui.ask_bool(f"File exceeds token limit ({limit}). Load anyway?", default=False)
                if not confirmed:
                    self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'User canceled large file load'})
                    return
            else:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'File exceeds token limit ({limit}). Consider using head/tail commands instead.'})
                return

        self.session.add_context('file', filename)
        try:
            self.session.ui.emit('status', {'message': f'Loaded file: {filename}'})
        except Exception:
            pass

    def _handle_write(self, filename: str, content: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.write_file(filename, content, create_dirs=True, force=force)
            msg = 'File written successfully' if success else f'Failed to write file: {filename}'
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Writes are disabled by policy; output a unified diff of changes you would apply.'})
            return
        if policy == 'dry-run':
            try:
                resolved = self.fs_handler.resolve_path(filename, must_exist=True)
            except TypeError:
                resolved = self.fs_handler.resolve_path(filename)
                if resolved and not os.path.exists(resolved):
                    resolved = None
            original = self.fs_handler.read_file(filename) or '' if resolved else ''
            try:
                diff_text = self.fs_handler._generate_diff(original, content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': diff_text or 'No changes (dry-run)'})
            return
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Writing {filename}...'})
        except Exception:
            pass
        success = self.fs_handler.write_file(filename, content, create_dirs=True, force=True)
        msg = 'File written successfully' if success else f'Failed to write file: {filename}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_append(self, filename: str, content: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=force)
            msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Appends are disabled by policy; output a unified diff of appended changes.'})
            return
        if policy == 'dry-run':
            try:
                resolved = self.fs_handler.resolve_path(filename, must_exist=True)
            except TypeError:
                resolved = self.fs_handler.resolve_path(filename)
                if resolved and not os.path.exists(resolved):
                    resolved = None
            original = self.fs_handler.read_file(filename) or '' if resolved else ''
            new_content = original + ('' if original.endswith('\n') or not isinstance(content, str) else '\n') + content
            try:
                diff_text = self.fs_handler._generate_diff(original, new_content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': diff_text or 'No changes (dry-run append)'})
            return
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Appending to {filename}...'})
        except Exception:
            pass
        success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=True)
        msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_summary(self, filename: str) -> None:
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return
        try:
            summary_prompt = self.session.get_tools().get('summary_prompt')
            summary_model = self.session.get_tools().get('summary_model')
            if not summary_model:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No summary model configured'})
                return
            try:
                self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Summarizing {filename}...'})
            except Exception:
                pass
            result = self.memex_runner.run('-m', summary_model, '-p', summary_prompt, '-f', resolved_path)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
            token_count = self.token_counter.count_tiktoken(summary)
            limit = int(self.session.get_tools().get('large_input_limit', 4000))
            if token_count > limit:
                if self.session.get_tools().get('confirm_large_input', True):
                    self.session.set_flag('auto_submit', False)
                else:
                    self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Summary exceeds token limit ({limit}). Try using a different summarization approach.'})
                return
            self.session.add_context('assistant', {'name': f'Summary of: {filename}', 'content': summary})
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to get file summary: {str(e)}'})

    def _handle_delete(self, filename: str, recursive: bool = False, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            if os.path.isdir(filename):
                success = self.fs_handler.delete_directory(filename, recursive, force=force)
            else:
                success = self.fs_handler.delete_file(filename, force=force)
            msg = 'Delete operation successful' if success else f'Failed to delete: {filename}'
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Deletes are disabled by policy; describe intended deletion instead.'})
            return
        if policy == 'dry-run':
            kind = 'directory' if os.path.isdir(filename) else 'file'
            msg = f"Would delete {kind} {filename}{' recursively' if (kind=='directory' and recursive) else ''}"
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if os.path.isdir(filename):
            success = self.fs_handler.delete_directory(filename, recursive, force=True)
        else:
            success = self.fs_handler.delete_file(filename, force=True)
        msg = 'Delete operation successful' if success else f'Failed to delete: {filename}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_rename(self, old_name: str, new_name: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.rename(old_name, new_name, force=force)
            msg = 'Rename operation successful' if success else f'Failed to rename {old_name} to {new_name}'
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Renames are disabled by policy; describe intended rename instead.'})
            return
        if policy == 'dry-run':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': f'Would rename {old_name} to {new_name}'})
            return
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Renaming {old_name} -> {new_name}...'})
        except Exception:
            pass
        success = self.fs_handler.rename(old_name, new_name, force=True)
        msg = 'Rename operation successful' if success else f'Failed to rename {old_name} to {new_name}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_copy(self, filename: str, new_name: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            success = self.fs_handler.copy(filename, new_name, force=force)
            msg = 'Copy operation successful' if success else f'Failed to copy {filename} to {new_name}'
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Copies are disabled by policy; describe intended copy instead.'})
            return
        if policy == 'dry-run':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': f'Would copy {filename} to {new_name}'})
            return
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Copying {filename} -> {new_name}...'})
        except Exception:
            pass
        success = self.fs_handler.copy(filename, new_name, force=True)
        msg = 'Copy operation successful' if success else f'Failed to copy {filename} to {new_name}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_edit(self, filename: str, edit_request: str) -> None:
        original_content = self.fs_handler.read_file(filename)
        if original_content is None:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to read file or empty: {filename}'})
            return
        edit_model = self.session.get_tools().get('edit_model')
        if not edit_model:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No edit model configured'})
            return
        conversation_history = self._get_formatted_conversation_history()
        prompt_content = self._build_edit_prompt(original_content, conversation_history, edit_request)
        try:
            self.session.ui.emit('progress', {'progress': 0.1, 'message': f'Running edit LLM for {filename}...'})
        except Exception:
            pass
        edited_content = self._run_edit_subprocess(edit_model, prompt_content)
        if edited_content is not None:
            self._process_and_confirm_edit(filename, original_content, edited_content)

    def _run_edit_subprocess(self, edit_model: str, prompt_content: str) -> str | None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
                temp_file.write(prompt_content)
                temp_path = temp_file.name
            result = self.memex_runner.run('-m', edit_model, '-f', temp_path, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Edit LLM failed: {e.stderr}'})
            return None
        except Exception as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'An unexpected error occurred during edit subprocess: {str(e)}'})
            return None
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _process_and_confirm_edit(self, filename: str, original_content: str, edited_content: str) -> None:
        if not edited_content:
            return
        if original_content == edited_content:
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'No changes detected in edited file'})
            return
        policy = self.session.get_agent_write_policy()
        if policy is None:
            self._handle_write(filename, edited_content)
            return
        if policy == 'deny':
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Writes are disabled by policy; output a unified diff of the edit you would apply.'})
            return
        if policy == 'dry-run':
            try:
                diff_text = self.fs_handler._generate_diff(original_content, edited_content, filename)
            except Exception:
                diff_text = None
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': diff_text or 'No changes (dry-run edit)'})
            return
        self._handle_write(filename, edited_content)

    # ---- Helpers -----------------------------------------------------------
    def _get_formatted_conversation_history(self) -> str:
        chat_context = self.session.get_context('chat')
        if not chat_context:
            return "## Conversation History\nNo conversation history available."
        history = chat_context.get('all')[-4:]
        if not history:
            return "## Conversation History\nNo conversation history available."
        formatted = ['## Conversation History']
        for msg in history:
            role = msg.get('role', 'unknown').capitalize()
            message = msg.get('message', '')
            formatted.append(f"[{role}]: {message}")
        return '\n'.join(formatted)

    @staticmethod
    def _build_edit_prompt(original_content: str, conversation_history: str, edit_request: str) -> str:
        intro_section = (
            "You are a code editor. Your task is to apply the requested changes to the provided file and output the complete modified file.\n\n"
            "    You will receive:\n"
            "    1. The content of the original file.\n"
            "    2. A few turns of conversation history between the user and the assistant to provide some context for the requested changes.\n"
            "    3. The requested changes to be applied to the oringinal file."
        )
        original_file_section = (
            "=== ORIGINAL FILE ===\n"
            "{original_content}\n"
            "=== END ORIGINAL FILE ==="
        )
        conversation_section = (
            "=== CONVERSATION CONTEXT ===\n"
            "{conversation_history}\n"
            "=== END CONVERSATION CONTEXT ==="
        )
        changes_section = (
            "=== REQUESTED CHANGES ===\n"
            "{edit_request}\n"
            "=== END REQUESTED CHANGES ==="
        )
        instructions_section = (
            "=== INSTRUCTIONS ===\n"
            "Output ONLY the complete modified file with the requested changes applied. Do not include any explanations, comments about the changes, or additional formatting or adjustments. "
            "Be sure to preserve original whitespace, newlines, and indentation. Just return the raw file content with edits applied."
        )
        prompt_parts = [
            intro_section,
            original_file_section,
            # conversation_section,  # optional
            changes_section,
            instructions_section,
        ]
        prompt = "\n\n".join(prompt_parts)
        return prompt.format(original_content=original_content, conversation_history=conversation_history, edit_request=edit_request)
