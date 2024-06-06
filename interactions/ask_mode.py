from session_handler import InteractionHandler


class AskMode(InteractionHandler):

    def __init__(self, conf):
        self.conf = conf

    def start(self, session):
        conf = self.conf
        print("We're in the ask mode and aking big questions!")