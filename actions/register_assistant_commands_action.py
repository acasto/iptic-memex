from base_classes import InteractionAction


class RegisterAssistantCommandsAction(InteractionAction):
    """
    Default implementation for registering custom assistant commands.
    Users can copy this to their user actions directory and modify it to add custom commands.
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
                     "COMMAND": {
                         "args": ["arg1", "arg2"],
                         "auto_submit": True,
                         "function": {
                             "type": "action",
                             "name": "action_name"
                         }
                     }
                 }
        """
        return {}
