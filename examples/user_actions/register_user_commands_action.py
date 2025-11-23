from base_classes import InteractionAction


class RegisterUserCommandsAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Register custom user commands (example/template).

        Shape expected by UserCommandsRegistryAction:
          "command sub": {"type": "action"|"builtin", "name": "action_name", "args": [...]}
        """
        return {
            # /debug storage → examples/user_actions/debug_storage_action.py
            "debug storage": {
                "type": "action",
                "name": "debug_storage",
                "help": "Inspect and edit persisted storage",
            },
            # /debug reload → examples/user_actions/debug_reload_action.py
            "debug reload": {
                "type": "action",
                "name": "debug_reload",
                "help": "Reload action/context caches without restarting",
            },
            # "load summary": {
            #     "type": "action",
            #     "name": "brave_summary",
            #     "help": "Load a summary from the web",
            # },
        }
