from base_classes import InteractionAction


class RegisterAssistantCommandsAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    @staticmethod
    def run(args=None):
        # Return a dict of custom commands. These shallow-merge into core commands.
        return {
            "ASK_AI": {
                "args": ["model", "question"],
                "function": {"type": "action", "name": "ask_ai_tool"},
                "description": "Ask a secondary AI model a question. Provide 'question'; optional 'model' selects which backend.",
                "schema": {
                    "properties": {
                        "model": {"type": "string", "description": "Model alias (e.g., 'claude')."},
                        "question": {"type": "string", "description": "Question text; 'content' is appended if provided."},
                        "content": {"type": "string", "description": "Optional extra text appended to the question."}
                    }
                }
            },
            "RELOAD": {
                "args": ["target", "targets"],
                'auto_submit': True,
                "function": {"type": "action", "name": "assistant_reload_tool"},
                "description": "Reload one or more action modules by name. Accepts newline-separated names in content or 'target(s)' args.",
                "schema": {
                    "properties": {
                        "target": {"type": "string", "description": "Single action to reload (e.g., 'assistant_file_tool')."},
                        "targets": {"type": "string", "description": "Comma-separated list of actions to reload."},
                        "content": {"type": "string", "description": "Optional newline-separated action names; supports 'all' to clear cache."}
                    }
                }
            },
        }

