from session_handler import InteractionContext
from datetime import datetime
from helpers import resolve_file_path


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
        self.conversation = []  # list to hold the file name and content
        # if session is not None:
        #     self.load_session(session)

    def add(self, message, role='user', context=None):
        """
        Add a message to the conversation
        :param message: the message to add
        :param role: the role of the message (user or assistant)
        :param context: any context objects to add
        """
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

    # def load_session(self, session):
    #     """
    #     Process a file from either a path or stdin
    #     """
    #     if session is not None:
    #         # if session is a file in chats_directory check and make sure it exists and return it
    #         chats_directory = self.conf.get_setting('DEFAULT', 'chats_directory')
    #         chats_extension = self.conf.get_setting('DEFAULT', 'chats_extension')
    #         session_file = resolve_file_path(session, chats_directory, chats_extension)
    #         if session_file is not None:
    #             with open(session_file, 'r') as f:
    #                 self.session = f.read()
    #                 return
