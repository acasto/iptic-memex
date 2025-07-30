from base_classes import InteractionAction


class RegisterAssistantCommandsAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    @staticmethod
    def run(args=None):
        # Return a dict of custom commands
        return {
            "ASK_AI": {
                "args": ["model", "question"],
                "function": {"type": "action", "name": "ask_ai_tool"}
            }
        }


