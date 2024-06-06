from session_handler import InteractionHandler


class CompletionMode(InteractionHandler):

    def __init__(self, session, provider):
        self.conf = session.get_session_settings()
        self.provider = provider
        # get the file object from the sessoin settings
        self.file = self.conf['file'][0].start()['content']

    def start(self):
        message = [{'role': 'user', 'content': self.file}]
        print(self.provider.chat(message))


