import sys
from session_handler import InteractionContext


class PromptContext(InteractionContext):
    """
    Class for processing system prompts
    """

    def __init__(self, session, prompt=None):
        """
        Initialize the file context
        :param prompt: the data to process
        """
        self.session = session
        self.prompt = {}  # dictionary to hold the file name and content
        self.proces_prompt(prompt)

    def proces_prompt(self, prompt):
        """
        Process a file from either a path or stdin
        """
        params = self.session.get_params()
        if prompt is not None:
            # if file is coming from stdin read it in
            if prompt == '-':
                self.prompt['name'] = 'stdin'
                self.prompt['content'] = sys.stdin.read()
                return

            # if prompt is a file in prompt_directory check and make sure it exists and return it
            if 'prompt_directory' not in params:
                prompt_directory = self.session.conf.get_option('DEFAULT', 'prompt_directory')
            else:
                prompt_directory = params.get('prompt_directory')
            prompt_file = self.session.utils.fs.resolve_file_path(prompt, prompt_directory, '.txt')
            if prompt_file is not None:
                with open(prompt_file, 'r') as f:
                    self.prompt['name'] = prompt_file
                    self.prompt['content'] = f.read()
                    return

            # if prompt is a file check and make sure it exists and return it
            prompt_file = self.session.utils.fs.resolve_file_path(prompt)
            if prompt_file is not None:
                with open(prompt_file, 'r') as f:
                    self.prompt['name'] = prompt_file
                    self.prompt['content'] = f.read()
                    return

            # if prompt is a string but "none" or "false" return
            if prompt.lower() in ['none', 'false']:
                self.prompt['name'] = 'none'
                self.prompt['content'] = ''
                return

            # if it seems like the user meant to specify a file, but it doesn't exist, raise an error
            if prompt.endswith('.txt') or ' ' not in prompt:
                print(f"\nCould not find the prompt file: {prompt}\n")
                sys.exit(1)

            # if prompt is not a file but a string check and make sure it's not empty and return it
            if prompt.strip() != '':
                self.prompt['name'] = 'string'
                self.prompt['content'] = prompt
                return

        # if none of the above conditions are met use the default prompt
        self.prompt['name'] = 'default'
        self.prompt['content'] = self.session.conf.get_default_prompt()

    def get(self):
        return self.prompt
