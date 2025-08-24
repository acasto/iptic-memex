from base_classes import InteractionAction
from utils.tool_args import get_str


class AssistantMathToolAction(InteractionAction):
    """
    Action for handling math operations
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    def run(self, args: dict, content: str = ""):
        bc_flags = get_str(args or {}, 'bc_flags', '') or ''
        expr = get_str(args or {}, 'expression')
        bc_expression = expr if (expr is not None and expr != '') else content
        bc_command = ['bc'] + (bc_flags.split() if bc_flags else [])
        output = self.temp_file_runner.run(bc_command, bc_expression)
        self.session.add_context('assistant', {
            'name': 'math tool',
            'content': output
        })
