import os
import sys
from session_handler import InteractionAction
from pathlib import Path


class TabCompletionAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def activate_completion(self, completer="path"):
        """
        Enables tab completion, defaults to file path completion
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.set_completer_delims('\t\n')
            # set the completer to completer if it exists
            readline.set_completer(getattr(self, f"{completer}_completer"))
            readline.parse_and_bind("tab: complete")

    @staticmethod
    def deactivate_completion():
        """
        Disables tab completion by making tab insert a tab
        """
        if sys.platform in ['linux', 'darwin']:
            import readline
            readline.parse_and_bind('tab: self-insert')

    def chat_completer(self, text, state):
        options = self.session.get_action('process_subcommands').get_commands()
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    def chat_path_completer(self, text, state):
        """
        Enables tab completion for chat session file paths
        """
        if text.startswith('~'):  # if text begins with '~' expand it
            text = os.path.expanduser(text)

        # Get the chats directory
        chats_directory = self.session.get_params().get('chats_directory', 'chats')
        chats_directory = os.path.expanduser(chats_directory)

        # If the input is empty or just a directory, list files in the chats directory
        if not text or os.path.isdir(text):
            directory = chats_directory if not text else text
            files_and_dirs = [os.path.join(directory, x) for x in os.listdir(directory)]
        else:
            directory = os.path.dirname(text) or chats_directory
            files_and_dirs = [os.path.join(directory, x) for x in os.listdir(directory) if x.startswith(os.path.basename(text))]

        # Filter for chat files (md, txt, pdf)
        options = [x for x in files_and_dirs if x.endswith(('.md', '.txt', '.pdf')) or os.path.isdir(x)]

        try:
            return options[state]
        except IndexError:
            return None

    def model_completer(self, text, state):
        """
        Enables tab completion for active models
        """
        # build a list from the keys in list_models()
        options = [x for x in self.session.list_models().keys()]
        # if an element in options starts with text, return it
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    def option_completer(self, text, state):
        """
        Enables tab completion for active options
        """
        # build a list from the params
        options = [x for x in self.session.get_params().keys()]
        # if an element in options starts with text, return it
        try:
            return [x for x in options if x.startswith(text)][state]
        except IndexError:
            return None

    @staticmethod
    def file_path_completer(text, state):
        """
        Enables tab completion for file paths
        """
        if text.startswith('~'):  # if text begins with '~' expand it
            text = os.path.expanduser(text)
        if os.path.isdir(os.path.dirname(text)):
            files_and_dirs = [str(Path(os.path.dirname(text)) / x) for x in os.listdir(os.path.dirname(text))]
        else:  # will catch CWD and empty inputs
            files_and_dirs = os.listdir(os.getcwd())

        # find the options that match
        options = [x for x in files_and_dirs if x.startswith(text)]

        # Add a slash to directories
        options = [f"{x}/" if os.path.isdir(x) else x for x in options]

        # return the option at the current state
        try:
            return options[state]
        except IndexError:
            return None

    def run(self, completer="path"):
        """
        Run the tab completion action
        """
        self.activate_completion(completer)
