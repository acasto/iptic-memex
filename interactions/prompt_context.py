import sys
from session_handler import InteractionHandler
from helpers import resolve_file_path


class PromptContext(InteractionHandler):
    """
    Class for handling files that go into context
    """

    def __init__(self, prompt):
        """
        Initialize the file context
        :param prompt: the data to process
        """
        self.prompt = {}  # dictionary to hold the file name and content
        self.proces_file(prompt)

    def proces_file(self, prompt):
        """
        Process a file from either a path or stdin
        """
        # if file is coming from stdin read it in
        if prompt == '-':
            self.prompt['name'] = 'stdin'
            self.prompt['content'] = sys.stdin.read()

        # else try to open and read it
        file_path = resolve_file_path(prompt)
        if file_path is not None:
            with open(file_path, 'r') as f:
                self.prompt = {'name': prompt, 'content': f.read()}

    def start(self, data):
        pass
