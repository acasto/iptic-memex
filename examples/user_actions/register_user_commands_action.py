from base_classes import InteractionAction


class RegisterUserCommandsAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        # Return a dict of custom commands
        return {
            "debug storage": {
                "description": "Way to test the storage in",
                "function": {
                    "type": "action",
                    "name": "debug_storage"  # Points to my_custom_action.py in user actions dir
                }
            },
            # "load summary": {
            #     "description": "Load a summary from the web",
            #     "function": {"type": "action", "name": "brave_summary"},
            # },
        }
