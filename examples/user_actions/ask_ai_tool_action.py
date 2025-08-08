from base_classes import InteractionAction


class AskAiToolAction(InteractionAction):
    """Ask a question to another AI model"""

    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

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
