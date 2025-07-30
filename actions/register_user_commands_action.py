from base_classes import InteractionAction


class RegisterUserCommandsAction(InteractionAction):
    """
    Default implementation for registering custom user commands.
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
