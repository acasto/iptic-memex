import sys
from session_handler import InteractionHandler
from helpers import resolve_file_path


class FileContext(InteractionHandler):
    """
    Class for handling files that go into context
    """

    def __init__(self, file, conf):
        """
        Initialize the file context
        :param file: the data to process
        """
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

    def start(self):
        """
        Start the file context
        :param data: the data to process
        """
        return self.file
