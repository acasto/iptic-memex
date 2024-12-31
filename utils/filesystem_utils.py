from __future__ import annotations

import os
from typing import Optional, Any, Union


class FileSystemHandler:
    """
    Handles filesystem operations like path resolution and directory management.
    """

    def __init__(self, config: Any, output_handler: Optional[Any] = None) -> None:
        """
        Initialize filesystem handler with user configuration.
        Optionally accepts an output handler for error messaging.
        """
        self.config = config
        self.output = output_handler
        self._main_dir = os.path.dirname(os.path.abspath(__file__))

    def resolve_file_path(
            self,
            file_name: str,
            base_dir: Optional[str] = None,
            extension: Optional[str] = None
    ) -> Optional[str]:
        """
        Resolves the absolute path to a file based on filename and optional parameters.

        Args:
            file_name: Name of the file to resolve the path to
            base_dir: Optional base directory to resolve the path from
            extension: Optional extension to append to the file name

        Returns:
            Absolute path to the file or None if not found
        """
        try:
            if file_name is None:
                return None

            # Handle base directory
            if base_dir is None:
                base_dir = os.getcwd()
            elif not os.path.isabs(base_dir):
                base_dir = os.path.abspath(os.path.join(self._main_dir, base_dir))
            base_dir = os.path.expanduser(base_dir)

            if not os.path.isdir(base_dir):
                if self.output:
                    self.output.error(f"Base directory not found: {base_dir}")
                return None

            # Handle file path
            file_name = os.path.expanduser(file_name)
            if os.path.isabs(file_name):
                if os.path.isfile(file_name):
                    return file_name
                elif extension and os.path.isfile(file_name + extension):
                    return file_name + extension
                if self.output:
                    self.output.error(f"File not found: {file_name}")
                return None

            # Try relative path combinations
            full_path = os.path.join(base_dir, file_name)
            if os.path.isfile(full_path):
                return full_path
            elif extension and os.path.isfile(full_path + extension):
                return full_path + extension

            if self.output:
                self.output.error(f"File not found: {full_path}")
            return None

        except Exception as e:
            if self.output:
                self.output.error(f"Error resolving file path: {str(e)}")
            return None

    def resolve_directory_path(self, dir_name: str) -> Optional[str]:
        """
        Resolves the absolute path to a directory.

        Args:
            dir_name: Name of the directory to resolve the path to

        Returns:
            Absolute path to the directory or None if not found
        """
        try:
            dir_name = os.path.expanduser(dir_name)

            if not os.path.isabs(dir_name):
                path = os.path.join(self._main_dir, dir_name)
                if os.path.isdir(path):
                    return path
            elif os.path.isdir(dir_name):
                return dir_name

            if self.output:
                self.output.error(f"Directory not found: {dir_name}")
            return None

        except Exception as e:
            if self.output:
                self.output.error(f"Error resolving directory path: {str(e)}")
            return None

    def ensure_directory(self, dir_path: str) -> bool:
        """
        Ensures a directory exists, creating it if necessary.

        Args:
            dir_path: Path to the directory to ensure exists

        Returns:
            True if directory exists or was created, False on failure
        """
        try:
            dir_path = os.path.expanduser(dir_path)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
            return True
        except Exception as e:
            if self.output:
                self.output.error(f"Error ensuring directory exists: {str(e)}")
            return False

    def read_file(self, file_path: str, binary: bool = False, encoding: str = 'utf-8') -> Optional[Union[str, bytes]]:
        """
        Read a file and return its contents.

        Args:
            file_path: Path to the file to read
            binary: If True, read in binary mode
            encoding: Character encoding to use when reading text files

        Returns:
            File contents as string or bytes, None if error occurs
        """
        try:
            mode = 'rb' if binary else 'r'
            kwargs = {} if binary else {'encoding': encoding}

            with open(file_path, mode, **kwargs) as f:
                return f.read()

        except Exception as e:
            if self.output:
                self.output.error(f"Error reading file {file_path}: {str(e)}")
            return None

    def write_file(self, file_path: str, content: Union[str, bytes], binary: bool = False,
                   encoding: str = 'utf-8', create_dirs: bool = False, append: bool = False) -> bool:
        """
        Write or append content to a file.

        Args:
            file_path: Path to the file to write
            content: Content to write (string or bytes)
            binary: If True, write in binary mode
            encoding: Character encoding to use when writing text files
            create_dirs: If True, create parent directories if they don't exist
            append: If True, append to file instead of overwriting
        Returns:
            True if successful, False otherwise
        """
        try:
            if create_dirs:
                os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

            # Determine mode based on binary and append flags
            if binary:
                mode = 'ab' if append else 'wb'
                kwargs = {}
            else:
                mode = 'a' if append else 'w'
                kwargs = {'encoding': encoding}
                # Add newline in text mode if appending and content doesn't end with one
                if append and isinstance(content, str) and not content.endswith('\n'):
                    content = content + '\n'

            with open(file_path, mode, **kwargs) as f:
                f.write(content)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error writing file {file_path}: {str(e)}")
            return False

    def delete_file(self, file_path: str) -> bool:
        """
        Delete a file.

        Args:
            file_path: Path to file to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            if not os.path.exists(file_path):
                if self.output:
                    self.output.error(f"File not found: {file_path}")
                return False

            os.remove(file_path)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error deleting file {file_path}: {str(e)}")
            return False

    def delete_directory(self, dir_path: str, recursive: bool = False) -> bool:
        """
        Delete a directory.

        Args:
            dir_path: Path to directory to delete
            recursive: If True, recursively delete contents

        Returns:
            True if successful, False otherwise
        """
        try:
            if not os.path.exists(dir_path):
                if self.output:
                    self.output.error(f"Directory not found: {dir_path}")
                return False

            if recursive:
                import shutil
                shutil.rmtree(dir_path)
            else:
                os.rmdir(dir_path)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error deleting directory {dir_path}: {str(e)}")
            return False

    def rename(self, old_path: str, new_path: str) -> bool:
        """
        Rename/move a file or directory.

        Args:
            old_path: Current path
            new_path: New path

        Returns:
            True if successful, False otherwise
        """
        try:
            if not os.path.exists(old_path):
                if self.output:
                    self.output.error(f"Path not found: {old_path}")
                return False

            if os.path.exists(new_path):
                if self.output:
                    self.output.error(f"Target path already exists: {new_path}")
                return False

            os.rename(old_path, new_path)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error renaming {old_path} to {new_path}: {str(e)}")
            return False

    def copy(self, src_path: str, dst_path: str) -> bool:
        """
        Copy a file or directory.

        Args:
            src_path: Source path
            dst_path: Destination path

        Returns:
            True if successful, False otherwise
        """
        try:
            if not os.path.exists(src_path):
                if self.output:
                    self.output.error(f"Source path not found: {src_path}")
                return False

            if os.path.exists(dst_path):
                if self.output:
                    self.output.error(f"Target path already exists: {dst_path}")
                return False

            import shutil
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error copying {src_path} to {dst_path}: {str(e)}")
            return False
