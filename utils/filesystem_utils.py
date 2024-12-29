from __future__ import annotations

import os
from pathlib import Path
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

    def is_path_in_base(self, base_dir: str, check_path: str) -> bool:
        """
        Verify if check_path is located within or under base_dir.
        Works cross-platform on Windows, macOS, and Linux.
        Handles:
        - Path expansion (e.g., '~' for home directory)
        - Environment variables (e.g., $HOME, %USERPROFILE%)
        - Relative paths (e.g., ../test.py)
        - Symbolic links

        Args:
            base_dir: The base directory path to check against
            check_path: The path to verify (absolute or relative)

        Returns:
            bool: True if check_path is within base_dir, False otherwise
        """
        try:
            # Handle empty or None paths
            if not base_dir or not check_path:
                return False

            # Expand user paths and environment variables
            base_expanded = os.path.expandvars(os.path.expanduser(base_dir))
            check_expanded = os.path.expandvars(os.path.expanduser(check_path))

            # Convert to absolute paths
            base_path = Path(base_expanded).resolve()

            # If check_path is relative, make it absolute relative to current working directory
            check = Path(check_expanded)
            if not check.is_absolute():
                check = (Path.cwd() / check).resolve()
            else:
                check = check.resolve()

            # Check if the base_path exists and is a directory
            if not base_path.exists() or not base_path.is_dir():
                if self.output:
                    self.output.error(f"Base directory not found or not a directory: {base_dir}")
                return False

            # Handle case-sensitivity based on platform
            if os.name == 'nt':  # Windows
                return str(check).lower().startswith(str(base_path).lower())
            else:  # Unix-like systems
                return str(check).startswith(str(base_path))

        except Exception as e:
            if self.output:
                self.output.error(f"Error checking path containment: {str(e)}")
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

            with open(file_path, mode, **kwargs) as f:
                f.write(content)
            return True

        except Exception as e:
            if self.output:
                self.output.error(f"Error writing file {file_path}: {str(e)}")
            return False
