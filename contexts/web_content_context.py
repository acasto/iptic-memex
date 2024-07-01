from session_handler import InteractionContext


class WebContentContext(InteractionContext):
    """
    Class for handling fetched web content
    """

    def __init__(self, conf, content=None):
        self.conf = conf
        self.web_content = {'name': 'Fetched Web Content', 'content': content}

    def get(self):
        return self.web_content
