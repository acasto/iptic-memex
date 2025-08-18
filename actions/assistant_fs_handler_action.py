from __future__ import annotations

from base_classes import InteractionAction
import os
from pathlib import Path


class AssistantFsHandlerAction(InteractionAction):
    """
    Provides a secure filesystem interface for assistant tools with path validation.
    Acts as a security layer between assistant tools and the core filesystem utilities.
    """
    def __init__(self, session):
        self.session = session
        self.fs = session.utils.fs

        # Get base directory configuration
        base_dir = session.get_tools().get('base_directory', 'working')
        if base_dir == 'working' or base_dir == '.':
            self._base_dir = os.getcwd()
        else:
            self._base_dir = os.path.expanduser(base_dir)

    def validate_path(self, file_path: str) -> str | None:
        """
        Validate a path is within the allowed base directory.
        Returns the resolved path if valid, None if not.
        """
        try:
            if not file_path:
                return None

            resolved_path = os.path.abspath(os.path.expanduser(file_path))
            if not self.is_path_in_base(self._base_dir, resolved_path):
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path {file_path} is outside allowed directory'
                })
                return None
            return resolved_path
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'fs_error',
                'content': f'Error validating path: {str(e)}'
            })
            return None

    @staticmethod
    def is_path_in_base(base_dir: str, check_path: str) -> bool:
        """
        Verify if check_path is within base_dir, handling symlinks and case sensitivity.
        """
        try:
            # Handle empty paths
            if not base_dir or not check_path:
                return False

            # Resolve paths
            base_path = Path(os.path.expandvars(os.path.expanduser(base_dir))).resolve()
            check = Path(os.path.expandvars(os.path.expanduser(check_path)))

            # Make check path absolute if relative
            if not check.is_absolute():
                check = (Path.cwd() / check).resolve()
            else:
                check = check.resolve()

            # Verify the base is a directory
            if not base_path.exists() or not base_path.is_dir():
                return False

            # Handle case sensitivity based on platform
            if os.name == 'nt':  # Windows
                return str(check).lower().startswith(str(base_path).lower())
            return str(check).startswith(str(base_path))

        except Exception:
            return False

    def read_file(self, file_path: str, binary=False, encoding='utf-8'):
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

        # Check if the file exists and get confirmation if needed
        needs_confirm = show_diff or self.session.get_tools().get('write_confirm', True)
        if os.path.exists(resolved_path):
            if not force and needs_confirm:
                self.session.utils.output.stop_spinner()
                mode = "append to" if append else "overwrite"
                if not self._confirm(f"File {file_path} exists. Confirm {mode}? [y/N]: ", default=False):
                    self.session.add_context('assistant', {
                        'name': 'fs_info',
                        'content': f'File operation cancelled by user'
                    })
                    return False
        elif not force and needs_confirm:
            self.session.utils.output.stop_spinner()
            if not self._confirm(f"Confirm write to new file {file_path}? [y/N]: ", default=False):
                self.session.add_context('assistant', {
                    'name': 'fs_info',
                    'content': f'File operation cancelled by user'
                })
                return False

        # Determine mode based on binary and append flags
        if binary:
            mode = 'ab' if append else 'wb'
            kwargs = {}
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
                except Exception:
                    ensure_nl = False
                if ensure_nl and (len(content) == 0 or not content.endswith('\n')):
                    content = content + '\n'

        return self.fs.write_file(resolved_path, content, binary=binary,
                                  encoding=encoding, create_dirs=create_dirs,
                                  append=append)

    @staticmethod
    def _generate_diff(original_content: str, new_content: str, file_path: str) -> str:
        """Generate unified diff between original and new content"""
        import difflib

        diff_lines = list(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f'{file_path} (original)',
            tofile=f'{file_path} (new)'
        ))

        return ''.join(diff_lines) if diff_lines else None

    def resolve_path(self, path: str, must_exist=True):
        """Resolve and validate a path"""
        resolved_path = self.validate_path(path)
        if resolved_path is None:
            return None

        if must_exist and not os.path.exists(resolved_path):
            self.session.add_context('assistant', {
                'name': 'fs_error',
                'content': f'Path does not exist: {path}'
            })
            return None

        return resolved_path

    def delete_file(self, file_path: str, force: bool = False) -> bool:
        """Delete a file with path validation and confirmation"""
        resolved_path = self.validate_path(file_path)
        if resolved_path is None:
            return False

        if not force and self.session.get_tools().get('write_confirm', True):
            self.session.utils.output.stop_spinner()
            if not self._confirm(f"Confirm delete file {file_path}? [y/N]: ", default=False):
                self.session.add_context('assistant', {
                    'name': 'fs_info',
                    'content': f'File deletion cancelled by user'
                })
                return False

        return self.fs.delete_file(resolved_path)

    def delete_directory(self, dir_path: str, recursive: bool = False, force: bool = False) -> bool:
        """Delete a directory with path validation and confirmation"""
        resolved_path = self.validate_path(dir_path)
        if resolved_path is None:
            return False

        if not force and self.session.get_tools().get('write_confirm', True):
            self.session.utils.output.stop_spinner()
            operation = "recursively delete" if recursive else "delete"
            if not self._confirm(f"Confirm {operation} directory {dir_path}? [y/N]: ", default=False):
                self.session.add_context('assistant', {
                    'name': 'fs_info',
                    'content': f'Directory deletion cancelled by user'
                })
                return False

        return self.fs.delete_directory(resolved_path, recursive)

    def rename(self, old_path: str, new_path: str, force: bool = False) -> bool:
        """Rename/move a file or directory with path validation and confirmation"""
        resolved_old = self.validate_path(old_path)
        if resolved_old is None:
            return False

        resolved_new = self.validate_path(new_path)
        if resolved_new is None:
            return False

        if not force and self.session.get_tools().get('write_confirm', True):
            self.session.utils.output.stop_spinner()
            if not self._confirm(f"Confirm rename {old_path} to {new_path}? [y/N]: ", default=False):
                self.session.add_context('assistant', {
                    'name': 'fs_info',
                    'content': f'Rename operation cancelled by user'
                })
                return False

        return self.fs.rename(resolved_old, resolved_new)

    def copy(self, src_path: str, dst_path: str, force: bool = False) -> bool:
        """Copy a file or directory with path validation and confirmation"""
        resolved_src = self.validate_path(src_path)
        if resolved_src is None:
            return False

        resolved_dst = self.validate_path(dst_path)
        if resolved_dst is None:
            return False

        if not force and self.session.get_tools().get('write_confirm', True):
            self.session.utils.output.stop_spinner()
            if not self._confirm(f"Confirm copy {src_path} to {dst_path}? [y/N]: ", default=False):
                self.session.add_context('assistant', {
                    'name': 'fs_info',
                    'content': f'Copy operation cancelled by user'
                })
                return False

        return self.fs.copy(resolved_src, resolved_dst)

    def run(self, args=None, content=None):
        """
        Not used - this action provides methods for other actions to use.
        """
        pass
