import sys
from base_classes import InteractionContext


class FileContext(InteractionContext):
    """
    Class for handling files that go into context
    """

    def __init__(self, session, file=None):
        """
        Initialize the file context
        :param file: can be a string (filename or '-') or a file-like object
        """
        self.session = session
        self.file = {}  # dictionary to hold the file name and content
        self.process_file(file)

    def process_file(self, file):
        """
        Process input from either a filename/path, stdin marker '-', or a file-like object.
        """
        # Accept pre-extracted dicts (name/content and optional metadata)
        if isinstance(file, dict):
            name = file.get('name') or 'Unnamed File'
            content = file.get('content') or ''
            out = {'name': name, 'content': content}
            if 'metadata' in file:
                out['metadata'] = file['metadata']
            self.file = out
            return

        if hasattr(file, 'read'):
            # file is a file-like object (e.g. from click.File('r'))
            self.file['name'] = getattr(file, 'name', 'stream')
            self.file['content'] = file.read()
            return

        if file == '-':
            # file is the stdin marker
            self.file['name'] = 'stdin'
            self.file['content'] = sys.stdin.read()
            return

        # Otherwise, file should be a string filename/path
        file_path = self.session.utils.fs.resolve_file_path(file)
        if file_path is not None:
            with open(file_path, 'r') as f:
                self.file = {'name': file, 'content': f.read()}

    def get(self):
        """
        Get a formatted dict of the file content ready to be inserted into the chat
        """
        return self.file
