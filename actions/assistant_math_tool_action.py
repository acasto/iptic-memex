from base_classes import InteractionAction
from utils.tool_args import get_str


class AssistantMathToolAction(InteractionAction):
    """
    Action for handling math operations
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        return 'math'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['bc_flags', 'expression'],
            'description': (
                "Evaluate arithmetic with the 'bc' calculator. Provide the expression; optional 'bc_flags' like '-l' "
                "enable math library or scale."
            ),
            'required': ['expression'],
            'schema': {
                'properties': {
                    'bc_flags': {"type": "string", "description": "Flags for bc (e.g., '-l' for math library)."},
                    'expression': {"type": "string", "description": "Expression to evaluate; if omitted, 'content' is used."},
                    'content': {"type": "string", "description": "Expression fallback when 'expression' is not set."}
                }
            },
            'auto_submit': True,
        }

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
