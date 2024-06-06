import sys
from session_handler import InteractionHandler
from helpers import resolve_file_path


class PromptContext(InteractionHandler):
    """
    Class for processing system prompts
    """

    def __init__(self, prompt, conf):
        """
        Initialize the file context
        :param prompt: the data to process
        """
        self.conf = conf  # ConfigHandler object
        self.prompt = {}  # dictionary to hold the file name and content
        self.proces_prompt(prompt)

    def proces_prompt(self, prompt):
        """
        Process a file from either a path or stdin
        """
        # if file is coming from stdin read it in
        if prompt == '-':
            self.prompt['name'] = 'stdin'
            self.prompt['content'] = sys.stdin.read()
            return

        # if prompt is a file in prompt_directory check and make sure it exists and return it
        prompt_directory = self.conf.get_setting('DEFAULT', 'prompt_directory')
        prompt_file = resolve_file_path(prompt, prompt_directory, '.txt')
        if prompt_file is not None:
            with open(prompt_file, 'r') as f:
                self.prompt['name'] = prompt_file
                self.prompt['content'] = f.read()
                return

        # if prompt is a file check and make sure it exists and return it
        prompt_file = resolve_file_path(prompt)
        if prompt_file is not None:
            with open(prompt_file, 'r') as f:
                self.prompt['name'] = prompt_file
                self.prompt['content'] = f.read()
                return

        # if it seems like the user meant to specify a file, but it doesn't exist, raise an error
        if prompt.endswith('.txt') or ' ' not in prompt:
            raise FileNotFoundError(f'Could not find the prompt file at {prompt}')

        # if prompt is not a file but a string check and make sure it's not empty and return it
        if prompt.strip() != '':
            self.prompt['name'] = 'string'
            self.prompt['content'] = prompt

    def start(self, data):
        pass