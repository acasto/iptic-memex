from session_handler import InteractionContext
from datetime import datetime


class ChatContext(InteractionContext):
    """
    Class for processing conversations
    """

    def __init__(self, conf, session=None):
        """
        Initialize the file context
        :param session: the data to process
        """
        self.conf = conf  # ConfigHandler object
        self.session = session
        self.conversation = []  # list to hold the file name and content
        # session might be used for loading a previous conversation
        # if session is not None:
        #     self.load_session(session)

    def add(self, message, role='user', context=None):
        """
        Add a message to the conversation
        :param message: the message to add
        :param role: the role of the message (user or assistant)
        :param context: any context objects to add
        """
        if message is None:  # if no message make sure we have a str and not None
            message = ''
        # let's create a turn object to hold the timestamp, role, message, and any other context objects (e.g. files)
        turn = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'role': role,
            'message': message,
        }
        if context is not None:
            if isinstance(context, list):
                turn['context'] = context
            else:
                turn['context'] = [context]

        self.conversation.append(turn)

    def get(self):
        return self.conversation

    def clear(self):
        self.conversation = []
