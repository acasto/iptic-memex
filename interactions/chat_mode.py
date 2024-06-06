from session_handler import InteractionHandler


class ChatMode(InteractionHandler):

    def __init__(self, conf):
        self.conf = conf

    def start(self, session):
        conf = self.conf
        print("We're in the chat mode and getting into trouble!")