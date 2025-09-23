from __future__ import annotations

from base_classes import StepwiseAction, Completed, InteractionNeeded
import os
from typing import Any, Dict, Optional
from utils.tool_args import get_str, get_bool
from core.mode_runner import run_completion


class AssistantFileToolAction(StepwiseAction):
    """
    File operations with optional confirmations, stepwise-capable for Web/TUI.
    Modes: read, write, append, edit, summarize, delete, rename, copy
    """

    MARKITDOWN_EXTENSIONS: tuple[str, ...] = (
        '.pdf',
        '.docx',
        '.xlsx',
        '.xls',
        '.pptx',
        '.msg',
        '.mp3',
        '.wav',
    )

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
                "rename, copy. Use 'content' for write/append/edit. "
                "Can read any text based file as well as pdf, docx, xlsx, xls, pptx, msg, mp3, wav, jpg, png. "
                "For editing longer files consider using a programmatic approach. "
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

    def run(self, *args, **kwargs):
        return super().run(*args, **kwargs)

    # ---- Stepwise protocol -------------------------------------------------
    def start(self, args: Dict, content: str = "") -> Completed:
        # Store args and content for potential resume
        self._current_args = args
        self._current_content = content
        
        mode = (get_str(args, 'mode', '') or '').lower()
        filename = get_str(args, 'file', '') or ''
        if not filename:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'No filename provided'})
            return Completed({'ok': False, 'error': 'No filename provided'})

        return self._execute_operation(mode, filename, args, content)

    def resume(self, state_token: str, response: Any) -> Completed:
        # Handle resumed operations after user confirmation
        try:
            # Extract the response value
            user_response = self._normalize_response(response)
            decision = self._parse_bool_response(user_response)

            # Treat explicit False or None as cancellation
            if decision is False or decision is None:
                self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Operation cancelled by user'})
                return Completed({'ok': True, 'cancelled': True})
            
            # Re-execute the operation with force=True to skip confirmation
            args = getattr(self, '_current_args', {})
            content = getattr(self, '_current_content', '')
            mode = (get_str(args, 'mode', '') or '').lower()
            filename = get_str(args, 'file', '') or ''
            
            return self._execute_operation(mode, filename, args, content, force=True)
            
        except Exception as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Resume error: {str(e)}'})
            return Completed({'ok': False, 'error': str(e)})

    def _execute_operation(self, mode: str, filename: str, args: Dict, content: str, force: bool = False) -> Completed:
        """Execute the file operation, potentially raising InteractionNeeded for confirmations."""
        try:
            # Central preflight confirmation so all UIs behave consistently
            if not force and self._needs_confirmation(mode, filename, args):
                blocking = True
                try:
                    blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
                except Exception:
                    blocking = True
                if mode in {'write', 'append'}:
                    preview = self._build_write_diff_preview(filename, content, append=(mode == 'append'))
                    if blocking:
                        try:
                            self.session.utils.output.write(preview)
                        except Exception:
                            pass
                        ans = False
                        try:
                            ans = bool(self.session.ui.ask_bool('Confirm?', default=False))
                        except Exception:
                            ans = False
                        if not ans:
                            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Operation cancelled by user'})
                            return Completed({'ok': True, 'cancelled': True})
                        force = True
                    else:
                        prompt = (preview + "\n\nConfirm?") if preview else self._build_confirm_prompt(mode, filename, args)
                        raise InteractionNeeded('bool', {'prompt': prompt, 'default': False}, state_token='file_operation_confirm')
                else:
                    # delete/rename/copy generic confirm
                    if blocking:
                        ans = False
                        try:
                            ans = bool(self.session.ui.ask_bool(self._build_confirm_prompt(mode, filename, args), default=False))
                        except Exception:
                            ans = False
                        if not ans:
                            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Operation cancelled by user'})
                            return Completed({'ok': True, 'cancelled': True})
                        force = True
                    else:
                        prompt = self._build_confirm_prompt(mode, filename, args)
                        raise InteractionNeeded('bool', {'prompt': prompt, 'default': False}, state_token='file_operation_confirm')
            handlers = {
                'read': lambda f: self._handle_read(f),
                'write': lambda f: self._handle_write(f, content, force=force),
                'edit': lambda f: self._handle_edit(f, content, force=force),
                'append': lambda f: self._handle_append(f, content, force=force),
                'summarize': lambda f: self._handle_summary(f),
                'delete': lambda f: self._handle_delete(f, bool(get_bool(args, 'recursive', False)), force=force),
                'rename': lambda f: self._handle_rename(f, get_str(args, 'new_name'), force=force),
                'copy': lambda f: self._handle_copy(f, get_str(args, 'new_name'), force=force),
            }
            
            handler = handlers.get(mode)
            if not handler:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Invalid mode: {mode}'})
                return Completed({'ok': False, 'error': f'Invalid mode: {mode}'})
                
            if mode in ('rename', 'copy') and not get_str(args, 'new_name'):
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'New name required for {mode} operation'})
                return Completed({'ok': False, 'error': f'New name required for {mode} operation'})
            # Suppress helper-side diff prints so diffs only appear in the confirmation modal
            try:
                self.session.set_user_data('__suppress_fs_diff__', True)
            except Exception:
                pass
            try:
                handler(filename)
            finally:
                try:
                    self.session.set_user_data('__suppress_fs_diff__', False)
                except Exception:
                    pass
            return Completed({'ok': True, 'mode': mode, 'file': filename})
            
        except InteractionNeeded as need:
            # Re-raise with our own state token so we can resume properly
            raise InteractionNeeded(
                kind=need.kind,
                spec=need.spec,
                state_token='file_operation_confirm'
            )

    # ---- Handlers ----------------------------------------------------------
    def _handle_read(self, filename: str) -> None:
        resolved_path = self.fs_handler.resolve_path(filename)
        if resolved_path is None:
            return

        kind = self._detect_kind(resolved_path)
        try:
            if kind == 'markitdown':
                if self._run_markitdown(resolved_path):
                    # MarkItDown added a file context; emit a single tool-scoped context event here
                    try:
                        import os as _os
                        try:
                            display_name = _os.path.relpath(_os.path.abspath(resolved_path), _os.getcwd())
                        except Exception:
                            display_name = os.path.basename(resolved_path)
                        abs_path = _os.path.abspath(resolved_path)
                        # Prefer MarkItDown index
                        md_idx = self.session.get_user_data('__markitdown_index__') or {}
                        tokens = 0
                        if isinstance(md_idx, dict) and abs_path in md_idx:
                            tokens = int((md_idx.get(abs_path) or {}).get('token_count') or 0)
                        if not tokens:
                            tokens = self._tokens_for_original_path(abs_path) or self._tokens_for_file_name(display_name)
                        if not tokens:
                            # Fallback to the most recent file context
                            try:
                                files = self.session.get_contexts('file') or []
                                if files:
                                    d = files[-1].get('context').get()
                                    meta = d.get('metadata') if isinstance(d, dict) and isinstance(d.get('metadata'), dict) else {}
                                    tokens = int(meta.get('token_count') or 0)
                                    if not tokens:
                                        content = d.get('content') if isinstance(d, dict) else None
                                        if isinstance(content, str) and content:
                                            tokens = int(self.token_counter.count_tiktoken(content))
                            except Exception:
                                pass
                        try:
                            self.session.utils.logger.action_detail(
                                'assistant_file_emit_context',
                                {
                                    'name': display_name,
                                    'tokens': tokens,
                                    'mode': 'markitdown',
                                },
                                component='actions.assistant_file_tool',
                            )
                        except Exception:
                            pass
                        self.session.ui.emit('context', {
                            'message': f'Added file: {display_name}' + (f' ({tokens} tokens)' if tokens else ''),
                            'origin': 'tool',
                            'kind': 'file',
                            'name': display_name,
                            'tokens': tokens,
                            'action': 'add',
                        })
                    except Exception:
                        pass
                    return
            elif kind == 'image':
                helper = self.session.get_action('read_image')
                if helper:
                    ok = bool(helper.process(resolved_path, fs_handler=self.fs_handler))
                    if ok:
                        # Image added to context by helper; emit a context event
                        try:
                            name = os.path.basename(resolved_path)
                            self.session.ui.emit('context', {
                                'message': f'Added image: {name}',
                                'origin': 'tool',
                                'kind': 'image',
                                'name': name,
                                'action': 'add',
                            })
                        except Exception:
                            pass
                        return

            # Default: treat as text and add as file context via dict to avoid unrestricted read
            content = self.fs_handler.read_file(resolved_path, binary=False)
            if content is None:
                self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to read file: {filename}'})
                return
            import os as _os
            try:
                display_name = _os.path.relpath(_os.path.abspath(resolved_path), _os.getcwd())
            except Exception:
                display_name = os.path.basename(resolved_path)
            self.session.add_context('file', {'name': display_name, 'content': str(content)})
            try:
                # Emit structured context event with token count
                tokens = 0
                try:
                    if self.token_counter:
                        tokens = int(self.token_counter.count_tiktoken(str(content)))
                except Exception:
                    tokens = 0
                try:
                    self.session.ui.emit('context', {
                        'message': f'Added file: {display_name}' + (f' ({tokens} tokens)' if tokens else ''),
                        'origin': 'tool',
                        'kind': 'file',
                        'name': display_name,
                        'tokens': tokens,
                        'action': 'add',
                    })
                except Exception:
                    pass
                self.session.utils.output.info(f'Loaded file: {filename}')
            except Exception:
                pass
        except Exception:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Error loading file: {filename}'})

    @classmethod
    def _detect_kind(cls, path: str) -> str:
        p = path.lower()
        if p.endswith(cls.MARKITDOWN_EXTENSIONS):
            return 'markitdown'
        if p.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')):
            return 'image'
        return 'file'

    def _run_markitdown(self, path: str) -> bool:
        try:
            helper = self.session.get_action('markitdown')
        except Exception:
            helper = None
        if not helper:
            return False
        try:
            return bool(helper.process(path, fs_handler=self.fs_handler))
        except Exception:
            return False

    def _tokens_for_file_name(self, name: str) -> int:
        try:
            if not self.token_counter:
                return 0
            files = self.session.get_contexts('file') or []
            for item in reversed(files):
                ctx = item.get('context') if isinstance(item, dict) else None
                data = ctx.get() if hasattr(ctx, 'get') else None
                if isinstance(data, dict) and data.get('name') == name:
                    content = data.get('content') or ''
                    if content:
                        return int(self.token_counter.count_tiktoken(str(content)))
            return 0
        except Exception:
            return 0

    def _tokens_for_original_path(self, abs_path: str) -> int:
        try:
            if not self.token_counter:
                return 0
            files = self.session.get_contexts('file') or []
            for item in reversed(files):
                ctx = item.get('context') if isinstance(item, dict) else None
                data = ctx.get() if hasattr(ctx, 'get') else None
                if not isinstance(data, dict):
                    continue
                meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
                if meta.get('original_file') == abs_path:
                    content = data.get('content') or ''
                    if content:
                        return int(self.token_counter.count_tiktoken(str(content)))
            # Fallback: basename match for markitdown entries
            base = os.path.basename(abs_path)
            for item in reversed(files):
                ctx = item.get('context') if isinstance(item, dict) else None
                data = ctx.get() if hasattr(ctx, 'get') else None
                if not isinstance(data, dict):
                    continue
                meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
                if meta.get('converter') == 'markitdown':
                    orig = str(meta.get('original_file') or '')
                    if orig.endswith(base):
                        content = data.get('content') or ''
                        if content:
                            return int(self.token_counter.count_tiktoken(str(content)))
            return 0
        except Exception:
            return 0


    def _handle_write(self, filename: str, content: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            # Suppress helper-side diff printing if we surfaced a diff in a modal
            try:
                if not force and bool(self.session.get_tools().get('write_confirm', True)):
                    self.session.set_user_data('__suppress_fs_diff__', True)
            except Exception:
                pass
            try:
                success = self.fs_handler.write_file(filename, content, create_dirs=True, force=force)
            finally:
                try:
                    self.session.set_user_data('__suppress_fs_diff__', False)
                except Exception:
                    pass
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
            self.session.set_user_data('__suppress_fs_diff__', True)
        except Exception:
            pass
        try:
            success = self.fs_handler.write_file(filename, content, create_dirs=True, force=True)
        finally:
            try:
                self.session.set_user_data('__suppress_fs_diff__', False)
            except Exception:
                pass
        msg = 'File written successfully' if success else f'Failed to write file: {filename}'
        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': msg})

    def _handle_append(self, filename: str, content: str, force: bool = False) -> None:
        policy = self.session.get_agent_write_policy()
        if policy is None:
            try:
                if not force and bool(self.session.get_tools().get('write_confirm', True)):
                    self.session.set_user_data('__suppress_fs_diff__', True)
            except Exception:
                pass
            try:
                success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=force)
            finally:
                try:
                    self.session.set_user_data('__suppress_fs_diff__', False)
                except Exception:
                    pass
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
            self.session.set_user_data('__suppress_fs_diff__', True)
        except Exception:
            pass
        try:
            success = self.fs_handler.write_file(filename, content, append=True, create_dirs=True, force=True)
        finally:
            try:
                self.session.set_user_data('__suppress_fs_diff__', False)
            except Exception:
                pass
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

    def _handle_edit(self, filename: str, edit_request: str, force: bool = False) -> None:
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
        if edited_content is None:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': 'Edit model returned no output'})
            return
        self._process_and_confirm_edit(filename, original_content, edited_content, force=force)

    def _run_edit_subprocess(self, edit_model: str, prompt_content: str) -> str | None:
        """Run the edit request via an internal completion and surface any errors to the UI."""
        try:
            # Prefer the session helper (ensures builder availability)
            res = self.session.run_internal_completion(
                message=prompt_content,
                overrides={'model': edit_model},
                contexts=None,
                capture='text',
            )
            # Surface internal runner events (e.g., large input gate) to the UI
            try:
                for ev in (res.events or []):
                    et = ev.get('type') or 'status'
                    msg = ev.get('message') or ev.get('text')
                    if msg:
                        self.session.ui.emit(et, {'message': str(msg)})
            except Exception:
                pass
            return (res.last_text or '').strip()
        except Exception as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Edit LLM failed: {e}'})
            return None

    def _process_and_confirm_edit(self, filename: str, original_content: str, edited_content: str, *, force: bool = False) -> None:
        if not edited_content:
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Edit produced empty output; no changes applied'})
            return
        if original_content == edited_content:
            self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'No changes detected in edited file'})
            return
        policy = self.session.get_agent_write_policy()
        if policy is None:
            # Confirm before writing edited content
            try:
                wc = self.session.get_tools().get('write_confirm', True)
            except Exception:
                wc = True
            if (not force) and bool(wc):
                # Build diff preview
                try:
                    diff_text = self.fs_handler._generate_diff(original_content, edited_content, filename)
                except Exception:
                    diff_text = None
                preview = diff_text or '(no diff)'
                blocking = True
                try:
                    blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
                except Exception:
                    blocking = True
                if blocking:
                    try:
                        self.session.utils.output.write(f"Proposed edit to {filename}:\n\n{preview}")
                    except Exception:
                        pass
                    ans = False
                    try:
                        ans = bool(self.session.ui.ask_bool('Confirm apply edits?', default=False))
                    except Exception:
                        ans = False
                    if not ans:
                        self.session.add_context('assistant', {'name': 'file_tool_result', 'content': 'Operation cancelled by user'})
                        return
                    force = True
                else:
                    raise InteractionNeeded('bool', {'prompt': f"Proposed edit to {filename}:\n\n{preview}\n\nConfirm apply edits?", 'default': False}, state_token='file_operation_confirm')
            self._handle_write(filename, edited_content, force=force)
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
        self._handle_write(filename, edited_content, force=force)

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

    @staticmethod
    def _normalize_response(response: Any) -> Any:
        if isinstance(response, dict):
            if 'response' in response:
                return response['response']
            if 'value' in response:
                return response['value']
        return response

    @staticmethod
    def _parse_bool_response(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {'yes', 'y', 'true', '1', 'on'}:
                return True
            if normalized in {'no', 'n', 'false', '0', 'off', ''}:
                return False
        return bool(value)

    # ---- Confirmation helpers ---------------------------------------------
    def _needs_confirmation(self, mode: str, filename: str, args: Dict) -> bool:
        try:
            wc = self.session.get_tools().get('write_confirm', True)
        except Exception:
            wc = True
        # Parse truthy/falsey robustly
        needs = bool(get_bool({'v': wc}, 'v', True))
        if not needs:
            return False
        # Only confirm for mutating ops; for 'edit' we confirm after generating a diff
        return mode in {'write', 'append', 'delete', 'rename', 'copy'}

    def _build_confirm_prompt(self, mode: str, filename: str, args: Dict) -> str:
        try:
            resolved = self.fs_handler.resolve_path(filename) or filename
        except Exception:
            resolved = filename
        if mode == 'write':
            try:
                exists = bool(resolved and os.path.exists(resolved))
            except Exception:
                exists = False
            action = 'overwrite' if exists else 'write to new file'
            return f"Confirm {action} {filename}?"
        if mode == 'append':
            return f"Confirm append to {filename}?"
        if mode == 'edit':
            return f"Confirm apply edits and overwrite {filename}?"
        if mode == 'delete':
            recursive = bool(get_bool(args, 'recursive', False))
            return f"Confirm {'recursively delete directory' if recursive else 'delete'} {filename}?"
        if mode == 'rename':
            new_name = get_str(args, 'new_name') or ''
            return f"Confirm rename {filename} to {new_name}?"
        if mode == 'copy':
            new_name = get_str(args, 'new_name') or ''
            return f"Confirm copy {filename} to {new_name}?"
        return f"Confirm operation '{mode}' on {filename}?"

    def _build_write_diff_preview(self, filename: str, new_content: str, *, append: bool) -> str:
        try:
            current = self.fs_handler.read_file(filename) or ''
        except Exception:
            current = ''
        cand = new_content
        if append and isinstance(new_content, str):
            try:
                needs_leading_nl = (len(current) > 0 and (not current.endswith('\n')))
            except Exception:
                needs_leading_nl = False
            cand = (('\n' if needs_leading_nl else '') + new_content)
            if not cand.endswith('\n'):
                cand += '\n'
            cand = current + cand
        try:
            diff_text = self.fs_handler._generate_diff(current, cand, filename)
        except Exception:
            diff_text = None
        preview = diff_text or ("New file: " + filename if not current else "(no changes detected)")
        try:
            if isinstance(preview, str):
                lines = preview.splitlines()
                if len(lines) > 200:
                    preview = "\n".join(lines[:200]) + f"\n… (truncated {len(lines)-200} lines)"
                if len(preview) > 8000:
                    preview = preview[:8000] + f"\n… (truncated {len(preview)-8000} chars)"
        except Exception:
            pass
        return f"Proposed changes for {filename}:\n\n{preview}"
