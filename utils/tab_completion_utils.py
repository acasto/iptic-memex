from __future__ import annotations

import os
import sys
from typing import Any, Optional
from pathlib import Path


class TabCompletionHandler:
    """
    Handles tab completion functionality for various contexts like paths, models, and chat commands.
    """

    def __init__(self, config: Any, output_handler: Optional[Any] = None) -> None:
        self.config = config
        self.output = output_handler
        self._session = None  # Will be set when needed

    def set_session(self, session: Any) -> None:
        """Set the session for operations that require session context"""
        self._session = session

    def run(self, completer: str = "path") -> None:
        """
        Enables tab completion with the specified completer
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.set_completer_delims('\t\n')
            completer_method = getattr(self, f"{completer}_completer", None)
            if completer_method:
                readline.set_completer(completer_method)
                readline.parse_and_bind("tab: complete")

    @staticmethod
    def deactivate_completion() -> None:
        """
        Disables tab completion
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.parse_and_bind('tab: self-insert')

    def chat_completer(self, text: str, state: int) -> Optional[str]:
        """Tab completion for chat commands"""
        if not self._session:
            return None
        options = self._session.get_action('user_commands').get_commands()
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    def chat_path_completer(self, text: str, state: int) -> Optional[str]:
        """Tab completion for chat session file paths"""
        if not self._session:
            return None

        if text.startswith('~'):
            text = os.path.expanduser(text)

        chats_directory = self._session.get_params().get('chats_directory', 'chats')
        chats_directory = os.path.expanduser(chats_directory)

        if not text or os.path.isdir(text):
            directory = chats_directory if not text else text
            files_and_dirs = [os.path.join(directory, x) for x in os.listdir(directory)]
        else:
            directory = os.path.dirname(text) or chats_directory
            files_and_dirs = [os.path.join(directory, x) for x in os.listdir(directory)
                              if x.startswith(os.path.basename(text))]

        options = [x for x in files_and_dirs if x.endswith(('.md', '.txt', '.pdf')) or os.path.isdir(x)]

        try:
            return options[state]
        except IndexError:
            return None

    def model_completer(self, text: str, state: int) -> Optional[str]:
        """Tab completion for model selection"""
        if not self._session:
            return None
        options = list(self._session.list_models().keys())
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    def option_completer(self, text: str, state: int) -> Optional[str]:
        """Tab completion for session options"""
        if not self._session:
            return None
        options = list(self._session.get_params().keys())
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    @staticmethod
    def image_completer(text, state):
        """Tab completion for image files only"""
        if text.startswith('~'):  # if text begins with '~' expand it
            text = os.path.expanduser(text)

        if os.path.isdir(os.path.dirname(text)):
            files_and_dirs = [str(Path(os.path.dirname(text)) / x) for x in os.listdir(os.path.dirname(text))]
        else:  # will catch CWD and empty inputs
            files_and_dirs = os.listdir(os.getcwd())

        # Find options that match and are either directories or supported image files
        options = [x for x in files_and_dirs if x.startswith(text) and
                   (os.path.isdir(x) or any(x.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')))]

        # Add a slash to directories
        options = [f"{x}/" if os.path.isdir(x) else x for x in options]

        try:
            return options[state]
        except IndexError:
            return None

    @staticmethod
    def file_path_completer(text: str, state: int) -> Optional[str]:
        """Tab completion for file paths"""
        if text.startswith('~'):
            text = os.path.expanduser(text)

        if os.path.isdir(os.path.dirname(text)):
            files_and_dirs = [str(Path(os.path.dirname(text)) / x)
                              for x in os.listdir(os.path.dirname(text))]
        else:
            files_and_dirs = os.listdir(os.getcwd())

        options = [x for x in files_and_dirs if x.startswith(text)]
        options = [f"{x}/" if os.path.isdir(x) else x for x in options]

        try:
            return options[state]
        except IndexError:
            return None
