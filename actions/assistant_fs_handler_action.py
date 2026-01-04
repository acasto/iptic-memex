from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional, Dict, List, Tuple

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
            self._base_dir = os.path.realpath(os.path.abspath(os.getcwd()))
        else:
            self._base_dir = os.path.realpath(os.path.abspath(os.path.expanduser(base_dir)))

        # Precompute allowlisted roots (base_dir RW + optional extra RO/RW roots).
        self._root_policies: Dict[str, str] = self._build_root_policies()

    @staticmethod
    def _split_csv(value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            out: List[str] = []
            for v in value:
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    out.append(s)
            return out
        s = str(value).strip()
        if not s:
            return []
        return [p.strip() for p in s.split(",") if p.strip()]

    @staticmethod
    def _coerce_bool(value: object, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if not s:
            return default
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off"):
            return False
        return default

    @staticmethod
    def _app_root() -> str:
        # Repo/package root (same reference point as ConfigManager.resolve_directory_path).
        try:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            return os.path.abspath(os.getcwd())

    def _resolve_root(self, root: str) -> Optional[str]:
        """Resolve a configured root to an absolute realpath.

        - Absolute paths are used as-is (after expanduser).
        - Relative paths are interpreted relative to base_directory.
        """
        if not root:
            return None
        try:
            expanded = os.path.expanduser(str(root).strip())
        except Exception:
            expanded = str(root).strip()
        if not expanded:
            return None
        if os.path.isabs(expanded):
            return os.path.realpath(os.path.abspath(expanded))
        try:
            return os.path.realpath(os.path.abspath(os.path.join(self._base_dir, expanded)))
        except Exception:
            return None

    def _resolve_skill_root(self, root: str) -> Optional[str]:
        """Resolve a skills directory token to a concrete host path."""
        token = (root or "").strip()
        if not token:
            return None

        # Convention: shipped skills live alongside main.py (app root).
        if token in ("skills", "./skills"):
            return os.path.realpath(os.path.abspath(os.path.join(self._app_root(), "skills")))

        # Convention: project skills live in the sandbox base dir.
        if token in (".skills", "./.skills"):
            return os.path.realpath(os.path.abspath(os.path.join(self._base_dir, ".skills")))

        # Otherwise treat like a normal root (abs or base_dir-relative).
        return self._resolve_root(token)

    def _build_root_policies(self) -> Dict[str, str]:
        """Return mapping of root_realpath -> mode ('rw'|'ro')."""
        policies: Dict[str, str] = {}

        # Base directory is always RW.
        policies[self._base_dir] = "rw"

        # User-configurable extra roots (advanced).
        try:
            ro_raw = self.session.get_option("TOOLS", "extra_ro_roots", fallback=None)
        except Exception:
            ro_raw = None
        try:
            rw_raw = self.session.get_option("TOOLS", "extra_rw_roots", fallback=None)
        except Exception:
            rw_raw = None
        ro_roots = [self._resolve_root(r) for r in self._split_csv(ro_raw)]
        rw_roots = [self._resolve_root(r) for r in self._split_csv(rw_raw)]

        # Implicit allowlist: skills directories are treated as RO by default so
        # the model can read SKILL.md / references. Users can override by adding
        # the same path to extra_rw_roots.
        try:
            skills_dirs_raw = self.session.get_option("SKILLS", "directories", fallback=None)
        except Exception:
            skills_dirs_raw = None
        implicit_skill_roots = [self._resolve_skill_root(r) for r in self._split_csv(skills_dirs_raw)]

        for r in implicit_skill_roots:
            if not r:
                continue
            # Do not downgrade an existing RW policy.
            if policies.get(r) == "rw":
                continue
            policies.setdefault(r, "ro")

        for r in ro_roots:
            if not r:
                continue
            if policies.get(r) == "rw":
                continue
            policies.setdefault(r, "ro")

        for r in rw_roots:
            if not r:
                continue
            policies[r] = "rw"

        # Drop empty roots defensively
        return {k: v for (k, v) in policies.items() if k}

    def get_allowed_roots(self) -> List[Dict[str, str]]:
        """Return effective allowlisted roots (for tooling like Docker)."""
        items: List[Tuple[str, str]] = []
        for root, mode in (self._root_policies or {}).items():
            if mode not in ("ro", "rw"):
                continue
            items.append((root, mode))
        # Sort shortest->longest so more specific roots come last (useful for mounts).
        items.sort(key=lambda it: len(it[0]))
        return [{"path": r, "mode": m} for (r, m) in items]

    def _mode_for_path(self, resolved_path: str) -> Optional[str]:
        """Return effective mode ('ro'|'rw') for a resolved absolute path."""
        best_root = None
        best_len = -1
        best_mode = None
        for root, mode in (self._root_policies or {}).items():
            if not root:
                continue
            try:
                common = os.path.commonpath([resolved_path, root])
            except Exception:
                continue
            if common != root:
                continue
            if len(root) > best_len:
                best_root = root
                best_len = len(root)
                best_mode = mode
        return best_mode

    def validate_path(
        self,
        file_path: str,
        must_exist: Optional[bool] = None,
        *,
        operation: str = "read",
    ) -> Optional[str]:
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

            mode = self._mode_for_path(resolved_path)
            if mode is None:
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path {file_path} is outside allowed roots'
                })
                return None

            op = (operation or "read").strip().lower()
            if op in ("write", "rw") and mode != "rw":
                self.session.add_context('assistant', {
                    'name': 'fs_error',
                    'content': f'Path {file_path} is read-only'
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
        resolved_path = self.validate_path(file_path, operation="read")
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
        resolved_path = self.validate_path(file_path, operation="write")
        if resolved_path is None:
            return False

        # If creating dirs, validate the parent directory
        if create_dirs:
            parent_dir = self.validate_path(os.path.dirname(resolved_path), operation="write")
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
        resolved_path = self.validate_path(file_path, operation="write")
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
        resolved_path = self.validate_path(dir_path, operation="write")
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
        resolved_old = self.validate_path(old_path, operation="write")
        if resolved_old is None:
            return False

        resolved_new = self.validate_path(new_path, operation="write")
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
        resolved_src = self.validate_path(src_path, operation="read")
        if resolved_src is None:
            return False

        resolved_dst = self.validate_path(dst_path, operation="write")
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
