from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from base_classes import InteractionAction, InteractionNeeded


class AssistantFsHandlerAction(InteractionAction):
    """
    Provides a secure filesystem interface for assistant tools with path validation.
    Acts as a security layer between assistant tools and the core filesystem utilities.
    """
    def __init__(self, session):
        self.session = session
        self.fs = session.utils.fs

        # Get base directory configuration (allow CLI override via session.get_option)
        base_dir = session.get_option('TOOLS', 'base_directory', fallback='working')
        if base_dir == 'working' or base_dir == '.':
            self._base_dir = os.path.abspath(os.getcwd())
        else:
            self._base_dir = os.path.abspath(os.path.expanduser(base_dir))

    def validate_path(self, file_path: str, must_exist: Optional[bool] = None) -> Optional[str]:
        """Validate and resolve path with safety checks."""
        if not file_path:
            return None

        try:
            expanded = os.path.expanduser(file_path)
            if not os.path.isabs(expanded):
                candidate = os.path.join(self._base_dir, expanded)
            else:
                candidate = expanded

            resolved_path = os.path.realpath(os.path.abspath(candidate))

            try:
                common_path = os.path.commonpath([resolved_path, self._base_dir])
            except ValueError:
                common_path = None

            if not common_path or common_path != self._base_dir:
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path {file_path} is outside allowed directory'
                })
                return None

            if must_exist is True and not os.path.exists(resolved_path):
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path does not exist: {file_path}'
                })
                return None
            if must_exist is False and os.path.exists(resolved_path):
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path already exists: {file_path}'
                })
                return None

            return resolved_path

        except Exception as exc:
            self.session.add_context('assistant', {
                'name': 'fs_error',
                'content': f'Error validating path: {exc}'
            })
            return None

    def resolve_path(self, file_path: str, must_exist: Optional[bool] = None) -> Optional[str]:
        """Alias for validate_path for backward compatibility"""
        return self.validate_path(file_path, must_exist)

    def read_file(self, file_path: str, binary: bool = False, encoding: str = 'utf-8'):
        """Read a file with path validation"""
        resolved_path = self.validate_path(file_path)
        if resolved_path is None:
            return None
        return self.fs.read_file(resolved_path, binary=binary, encoding=encoding)

    def _confirm(self, prompt: str, default: bool = False) -> bool:
        """Ask for confirmation via the UI adapter; fallback to CLI input if needed."""
        ui = getattr(self.session, 'ui', None)
        if ui and hasattr(ui, 'ask_bool'):
            from base_classes import InteractionNeeded
            try:
                return bool(ui.ask_bool(prompt, default=default))
            except InteractionNeeded:
                # Propagate to Web/TUI so the mode can respond with needs_interaction
                raise
            except Exception:
                # Fall through to CLI input on non-interaction errors
                pass
        # Fallback to legacy CLI input handler
        return bool(self.session.utils.input.get_bool(prompt, default=default))

    def write_file(self, file_path: str, content, binary=False, encoding='utf-8',
                   create_dirs=False, append=False, force=False):
        """Write to a file with path validation and optional confirmation"""
        resolved_path = self.validate_path(file_path)
        if resolved_path is None:
            return False

        # If creating dirs, validate the parent directory
        if create_dirs:
            parent_dir = self.validate_path(os.path.dirname(resolved_path))
            if parent_dir is None:
                return False

        # Show diff if requested and file exists
        show_diff = self.session.get_tools().get('show_diff_with_confirm', False)
        # Allow actions to suppress helper-side diff printing when they present
        # a diff in the confirmation modal.
        try:
            if bool(self.session.get_user_data('__suppress_fs_diff__')):
                show_diff = False
        except Exception:
            pass
        if show_diff and os.path.exists(resolved_path) and not binary and not append:
            try:
                original_content = self.read_file(file_path, binary=False, encoding=encoding)
                if original_content is not None:
                    diff_text = self._generate_diff(original_content, content, file_path)
                    if diff_text:
                        self.session.utils.output.write(f"Proposed changes for {file_path}:\n{diff_text}")
                    else:
                        self.session.utils.output.write("No changes detected between original and new content")
            except Exception as e:
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Error generating diff: {str(e)}'
                })

        # Check if the file exists and get confirmation if needed. Showing a diff should
        # not force a confirmation when the user has disabled confirmations.
        needs_confirm = bool(self.session.get_tools().get('write_confirm', True))
        if os.path.exists(resolved_path):
            if not force and needs_confirm:
                self.session.utils.output.stop_spinner()
                mode = "append to" if append else "overwrite"
                try:
                    if not self._confirm(f"File {file_path} exists. Confirm {mode}? [y/N]: ", default=False):
                        self.session.add_context('assistant', {
                            'name': 'fs_info',
                            'content': f'File operation cancelled by user'
                        })
                        return False
                except InteractionNeeded:
                    # Re-raise so calling action can handle it
                    raise
        elif not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            try:
                if not self._confirm(f"Confirm write to new file {file_path}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'File operation cancelled by user'
                    })
                    return False
            except InteractionNeeded:
                # Re-raise so calling action can handle it
                raise

        # Determine mode based on binary and append flags
        if binary:
            mode = 'ab' if append else 'wb'
            kwargs = {}
            written_content = content
        else:
            mode = 'a' if append else 'w'
            kwargs = {'encoding': encoding}

            # Handle newlines in text append mode
            if append and isinstance(content, str):
                try:
                    with open(resolved_path, 'r', encoding=encoding) as f:
                        f.seek(0, 2)  # Seek to end
                        if f.tell() > 0:  # If the file is not empty
                            f.seek(f.tell() - 1, 0)  # Go back one char
                            if f.read(1) != '\n':
                                content = '\n' + content
                except FileNotFoundError:
                    pass  # New file, no need to check

                # Ensure content ends with the newline
                if not content.endswith('\n'):
                    content += '\n'

            # For non-append writes, optionally ensure trailing newline at EOF
            if not append and isinstance(content, str):
                try:
                    ensure_nl = self.session.get_params().get(
                        'ensure_trailing_newline',
                        self.session.get_tools().get('ensure_trailing_newline', False)
                    )
                    if ensure_nl and not content.endswith('\n'):
                        content += '\n'
                except Exception:
                    pass
            written_content = content

        # Create directory if needed
        if create_dirs:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)

        # Write the file
        try:
            with open(resolved_path, mode, **kwargs) as f:
                f.write(content)
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'fs_error',
                'content': f'Failed to write {file_path}: {str(e)}'
            })
            return False

        # Verify write landed on disk (best-effort)
        try:
            if binary:
                with open(resolved_path, 'rb') as rf:
                    new_data = rf.read()
                ok = (new_data.endswith(written_content) if append else new_data == written_content)
            else:
                with open(resolved_path, 'r', encoding=encoding) as rf:
                    new_text = rf.read()
                ok = (new_text.endswith(written_content) if append else new_text == written_content)
            if not ok:
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Verification failed after writing {file_path}. Content mismatch.'
                })
                return False
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'fs_error',
                'content': f'Verification failed for {file_path}: {e}'
            })
            return False

        return True

    def delete_file(self, file_path: str, force: bool = False) -> bool:
        """Delete a file with path validation and confirmation"""
        resolved_path = self.validate_path(file_path)
        if resolved_path is None:
            return False

        needs_confirm = self.session.get_tools().get('write_confirm', True)
        if not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            try:
                if not self._confirm(f"Confirm delete file {file_path}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'File deletion cancelled by user'
                    })
                    return False
            except InteractionNeeded:
                # Re-raise so calling action can handle it
                raise

        return self.fs.delete_file(resolved_path)

    def delete_directory(self, dir_path: str, recursive: bool = False, force: bool = False) -> bool:
        """Delete a directory with path validation and confirmation"""
        resolved_path = self.validate_path(dir_path)
        if resolved_path is None:
            return False

        needs_confirm = self.session.get_tools().get('write_confirm', True)
        if not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            operation = "recursively delete" if recursive else "delete"
            try:
                if not self._confirm(f"Confirm {operation} directory {dir_path}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'Directory deletion cancelled by user'
                    })
                    return False
            except InteractionNeeded:
                # Re-raise so calling action can handle it
                raise

        return self.fs.delete_directory(resolved_path, recursive)

    def rename(self, old_path: str, new_path: str, force: bool = False) -> bool:
        """Rename/move a file or directory with path validation and confirmation"""
        resolved_old = self.validate_path(old_path)
        if resolved_old is None:
            return False

        resolved_new = self.validate_path(new_path)
        if resolved_new is None:
            return False

        needs_confirm = self.session.get_tools().get('write_confirm', True)
        if not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            try:
                if not self._confirm(f"Confirm rename {old_path} to {new_path}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'Rename operation cancelled by user'
                    })
                    return False
            except InteractionNeeded:
                # Re-raise so calling action can handle it
                raise

        return self.fs.rename(resolved_old, resolved_new)

    def copy(self, src_path: str, dst_path: str, force: bool = False) -> bool:
        """Copy a file or directory with path validation and confirmation"""
        resolved_src = self.validate_path(src_path)
        if resolved_src is None:
            return False

        resolved_dst = self.validate_path(dst_path)
        if resolved_dst is None:
            return False

        needs_confirm = self.session.get_tools().get('write_confirm', True)
        if not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            try:
                if not self._confirm(f"Confirm copy {src_path} to {dst_path}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'Copy operation cancelled by user'
                    })
                    return False
            except InteractionNeeded:
                # Re-raise so calling action can handle it
                raise

        return self.fs.copy(resolved_src, resolved_dst)

    def run(self, args=None, content=None):
        """
        Not used - this action provides methods for other actions to use.
        """
        pass

    def _generate_diff(self, original: str, new: str, filename: str) -> str:
        """Generate a unified diff between original and new content"""
        try:
            import difflib
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f'a/{filename}',
                tofile=f'b/{filename}',
                lineterm=''
            )
            return ''.join(diff)
        except Exception:
            return ""
