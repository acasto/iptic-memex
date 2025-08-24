from base_classes import InteractionAction


class AskAiToolAction(InteractionAction):
    """Ask a question to another AI model"""

    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    # ---- Dynamic tool registry metadata (optional for user tools) ----
    @classmethod
    def tool_name(cls) -> str:
        return 'ask_ai'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['model', 'question'],
            'description': (
                "Ask a secondary AI model a question. Provide 'question'; optional 'model' selects which backend."
            ),
            'required': [],
            'schema': {
                'properties': {
                    'model': {"type": "string", "description": "Model alias (e.g., 'claude')."},
                    'question': {"type": "string", "description": "Question text; 'content' is appended if provided."},
                    'content': {"type": "string", "description": "Optional extra text appended to the question."}
                }
            },
            'auto_submit': True,
        }

    def run(self, args, content=""):
        model = args.get('model', 'claude')
        question = args.get('question', '')
        question += '\n' + content if content else ''

        ai_command = [f'memex', '-m', model, '-f', '-']
        output = self.temp_file_runner.run(ai_command, question)

        self.session.add_context('assistant', {
            'name': 'assistant_context',
            'content': output
        })

# echo "Give a brief summary of the above file, including a summary of its code structure. List the names of any classes, methods, propertes, or functions." \|
# memex -m llama-3b -f actions/assistant_commands_action.py -f -
