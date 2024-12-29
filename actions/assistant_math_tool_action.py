from session_handler import InteractionAction


class AssistantMathToolAction(InteractionAction):
    """
    Action for handling math operations
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    def run(self, args: dict, content: str = ""):
        bc_flags = args.get('bc_flags', '')
        bc_expression = args.get('expression', content)
        bc_command = ['bc'] + bc_flags.split()
        output = self.temp_file_runner.run(bc_command, bc_expression)
        self.session.add_context('assistant', {
            'name': 'math tool',
            'content': output
        })
