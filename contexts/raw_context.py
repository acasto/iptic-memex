import sys
from session_handler import InteractionContext
from helpers import resolve_file_path


class RawContext(InteractionContext):
    """
    Class for handling files that go into context in a raw form (no processing)
    This can be useful for persisting things like projects where we want to avoid nesting
    """

    def __init__(self, session, file=None):
        """
        Initialize the file context
        :param file:
        """
        self.session = session
        self.file = {}  # dictionary to hold the file name and content
        self.proces_file(file)

    def proces_file(self, file):
        """
        Process a file from either a path or stdin
        """
        # if file is coming from stdin read it in
        if file == '-':
            self.file['name'] = 'stdin'
            self.file['content'] = sys.stdin.read()

        # else try to open and read it
        file_path = resolve_file_path(file)
        if file_path is not None:
            with open(file_path, 'r') as f:
                self.file = {'name': file, 'content': f.read()}

    def get(self):
        """
        Get a formated string of the file content ready to be inserted into the chat
        """
        return self.file
