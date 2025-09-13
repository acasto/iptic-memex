from __future__ import annotations

from base_classes import StepwiseAction, Completed
import os
import tempfile
from typing import Any, Dict
from utils.tool_args import get_str, get_bool
from core.mode_runner import run_completion


class AssistantFileToolAction(StepwiseAction):
    """
    File operations with optional confirmations, stepwise-capable for Web/TUI.
    Modes: read, write, append, edit, summarize, delete, rename, copy
    """

    def __init__(self, session):
        self.session = session
        self.fs_handler = session.get_action('assistant_fs_handler')
        self.token_counter = session.get_action('count_tokens')
        # Legacy subprocess runner (memex) no longer required; internal runs preferred

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        return 'file'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['mode', 'file', 'new_name', 'recursive', 'block', 'desc'],
            'description': (
                "Read or modify files in the workspace. Modes: read, write, append, edit, summarize, delete, "
                "rename, copy. Use 'content' for write/append/edit."
            ),
            'required': ['mode', 'file'],
            'schema': {
                'properties': {
                    'mode': {"type": "string", "enum": [
                        'read', 'write', 'append', 'edit', 'summarize', 'delete', 'rename', 'copy'
                    ], "description": "Operation to perform."},
                    'file': {"type": "string", "description": "Target file path (relative to workspace)."},
                    'new_name': {"type": "string", "description": "New name/path for rename or copy."},
                    'recursive': {"type": "boolean", "description": "When deleting, remove directories recursively if true."},
                    'block': {"type": "string", "description": "Identifier of a %BLOCK:...% to append to 'content'."},
                    'content': {"type": "string", "description": "Content to write/append or edit instructions (for edit mode)."},
                    'desc': {"type": "string", "description": "Optional short description for UI/status; ignored by execution.", "default": ""}
                }
            },
            'auto_submit': True,
        }


    # ---- Stepwise protocol -------------------------------------------------
    def start(self, args: Dict, content: str = "") -> Completed:
        mode = (get_str(args, 'mode', '') or '').lower()
        filename = get_str(args, 'file', '') or ''
        if not filename:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No filename provided'})
            return Completed({'ok': False, 'error': 'No filename provided'})

        handlers = {
            'read': self._handle_read,
            'write': lambda f: self._handle_write(f, content),
            'edit': lambda f: self._handle_edit(f, content),
            'append': lambda f: self._handle_append(f, content),
            'summarize': self._handle_summary,
            'delete': lambda f: self._handle_delete(f, bool(get_bool(args, 'recursive', False))),
            'rename': lambda f: self._handle_rename(f, get_str(args, 'new_name')),
            'copy': lambda f: self._handle_copy(f, get_str(args, 'new_name')),
        }
        handler = handlers.get(mode)
        if not handler:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Invalid mode: {mode}'})
            return Completed({'ok': False, 'error': f'Invalid mode: {mode}'})
        if mode in ('rename', 'copy') and not get_str(args, 'new_name'):
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

                mode = (get_str(args, 'mode', '') or '').lower()
                filename = get_str(args, 'file') or get_str(args, 'path') or ''
                if mode in ('write', 'append'):
                    if mode == 'write':
                        self._handle_write(filename, content, force=True)
                    else:
                        self._handle_append(filename, content, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'confirmed': True})
                if mode == 'delete':
                    recursive = bool(get_bool(args, 'recursive', False))
                    self._handle_delete(filename, recursive, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'confirmed': True})
                if mode == 'rename':
                    new_name = get_str(args, 'new_name')
                    self._handle_rename(filename, new_name, force=True)
                    return Completed({'ok': True, 'mode': mode, 'file': filename, 'new_name': new_name, 'confirmed': True})
                if mode == 'copy':
                    new_name = get_str(args, 'new_name')
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
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return

        kind = self._detect_kind(resolved_path)
        try:
            if kind == 'pdf':
                helper = self.session.get_action('read_pdf')
                if helper:
                    ok = bool(helper.process(resolved_path, fs_handler=self.fs_handler))
                    if ok:
                        return
            elif kind == 'docx':
                helper = self.session.get_action('read_docx')
                if helper:
                    ok = bool(helper.process(resolved_path, fs_handler=self.fs_handler))
                    if ok:
                        return
            elif kind == 'xlsx':
                helper = self.session.get_action('read_sheet')
                if helper:
                    ok = bool(helper.process(resolved_path, fs_handler=self.fs_handler))
                    if ok:
                        return
            elif kind == 'image':
                helper = self.session.get_action('read_image')
                if helper:
                    ok = bool(helper.process(resolved_path, fs_handler=self.fs_handler))
                    if ok:
                        return

            # Default: treat as text and add as file context via dict to avoid unrestricted read
            content = self.fs_handler.read_file(resolved_path, binary=False)
            if content is None:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to read file: {filename}'})
                return
            name = os.path.basename(resolved_path)
            self.session.add_context('file', {'name': name, 'content': str(content)})
            try:
                self.session.ui.emit('status', {'message': f'Loaded file: {filename}'})
            except Exception:
                pass
        except Exception:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Error loading file: {filename}'})

    def _detect_kind(self, path: str) -> str:
        p = path.lower()
        if p.endswith('.pdf'):
            return 'pdf'
        if p.endswith('.docx'):
            return 'docx'
        if p.endswith('.xlsx'):
            return 'xlsx'
        if p.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')):
            return 'image'
        return 'file'

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
        success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=True)
        msg = 'Content appended successfully' if success else f'Failed to append to file: {filename}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_summary(self, filename: str) -> None:
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return
        try:
            tools = self.session.get_tools()
            summary_prompt = tools.get('summary_prompt')
            summary_model = tools.get('summary_model')
            if not summary_model:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No summary model configured'})
                return
            builder = getattr(self.session, '_builder', None)
            if builder is None:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'Internal builder unavailable for summary'})
                return
            res = run_completion(
                builder=builder,
                overrides={'model': summary_model, 'prompt': summary_prompt} if summary_prompt else {'model': summary_model},
                contexts=[('file', resolved_path)],
                message='',
                capture='text',
            )
            summary = res.last_text or ''
            self.session.add_context('assistant', {'name': f'Summary of: {filename}', 'content': summary})
        except Exception as e:
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
        edited_content = self._run_edit_subprocess(edit_model, prompt_content)
        if edited_content is not None:
            self._process_and_confirm_edit(filename, original_content, edited_content)

    def _run_edit_subprocess(self, edit_model: str, prompt_content: str) -> str | None:
        try:
            builder = getattr(self.session, '_builder', None)
            if builder is None:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'Internal builder unavailable for edit'})
                return None
            res = run_completion(
                builder=builder,
                overrides={'model': edit_model},
                contexts=None,
                message=prompt_content,
                capture='text',
            )
            return (res.last_text or '').strip()
        except Exception as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Edit LLM failed: {e}'})
            return None

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
