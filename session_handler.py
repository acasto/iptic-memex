from config_handler import ConfigHandler


class SessionHandler:
    """
    Class for handling the current session
    """

    def __init__(self, conf: ConfigHandler):
        self.conf = conf
        self.conf.start_session_conf()
        self.session = []

    def start_interaction(self, interaction: str):
        """
        Start an interaction with the user
        :param interaction: the interaction to start
        """
        if interaction == 'completion':
            print("we will do a completion")
        elif interaction == 'chat':
            print("we will do a chat")
        elif interaction == 'ask':
            print("ask your questions fool")
