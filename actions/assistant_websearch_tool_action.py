from session_handler import InteractionAction
import subprocess
import os


class AssistantWebsearchToolAction(InteractionAction):
    """
    Action for handling web search operations
    """
    def __init__(self, session):
        self.session = session

    def run(self, args: dict, content: str = ""):
            self._handle_search(args['query'])
            return

    def _handle_search(self, query):
        try:
            search_model = self.session.conf.get_option('TOOLS', 'search_model')

            result = subprocess.run(f'echo "{query}" | memex -m {search_model} -f -',
                                    shell=True, capture_output=True, text=True)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            self.session.add_context('assistant', {
                'name': f'Search Results',
                'content': summary
            })
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to get file summary: {str(e)}'
            })


