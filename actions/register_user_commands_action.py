from base_classes import InteractionAction


class RegisterUserCommandsAction(InteractionAction):
    """
    Default implementation for registering custom user commands.
    Users can copy this to their user actions directory and modify it to add custom commands.

    How it works
    - Return a dict mapping user command strings (what the user types) to configs.
    - Your returned dict is shallow-merged into the built‑ins from actions/user_commands_action.py,
      so you can override just the description or function target without redefining everything.

    Command entry shape
      {
        "command name": {
          "description": "Short help text",
          "function": {
            "type": "action" | "method",
            "name": "action_or_method_name",
            "args": [optional, list or string],
            "method": "optional_method_name_on_action"  # when calling a specific action method
          }
        }
      }

    Notes
    - If "type" is "action", the action class is loaded dynamically. If that action class defines
      a @classmethod can_run(session) that returns False, the command is hidden automatically.
    - If "type" is "method", the method is invoked on UserCommandsAction.
    - For action calls, you may pass positional args via a list or a single string in "args".

    Examples
    1) Add a new command that runs a custom action:
        return {
            "hello world": {
                "description": "Say hello",
                "function": {"type": "action", "name": "hello_world"}
            }
        }

    2) Override description of an existing command (partial merge):
        return {
            "load file": {
                "description": "Load a file into context (customized)"
            }
        }
    """

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Return a dictionary of custom commands to register.

        Returns:
            dict: Mapping of command names to command configurations.
                 Example format:
                 {
                     "command_name": {
                         "description": "Command description",
                         "function": {
                             "type": "action",
                             "name": "action_name",
                             "args": ["optional", "args"]
                         }
                     }
                 }
        """
        return {}
