from session_handler import InteractionContext
from datetime import datetime


class ChatContext(InteractionContext):
    def __init__(self, conf, session=None):
        self.conf = conf  # ConfigHandler object
        self.session = session
        self.conversation = []  # list to hold the file name and content

    def add(self, message, role='user', context=None):
        if message is None:
            message = ''
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

    def remove_last_message(self):
        """
        Remove the last message from the conversation.
        :return: True if a message was removed, False otherwise
        """
        if self.conversation:
            self.conversation.pop()
            return True
        return False

    def remove_messages(self, n):
        """
        Remove the last n messages from the conversation.
        :param n: number of messages to remove
        :return: number of messages actually removed
        """
        if n > 0:
            removed = min(n, len(self.conversation))
            self.conversation = self.conversation[:-removed]
            return removed
        return 0

    def remove_first_message(self):
        """
        Remove the first message from the conversation.
        :return: True if a message was removed, False otherwise
        """
        if self.conversation:
            self.conversation.pop(0)
            return True
        return False

    def remove_first_messages(self, n):
        """
        Remove the first n messages from the conversation.
        :param n: number of messages to remove
        :return: number of messages actually removed
        """
        if n > 0:
            removed = min(n, len(self.conversation))
            self.conversation = self.conversation[removed:]
            return removed
        return 0

    def get_formatted_conversation(self, user_label, response_label):
        """
        Return a formatted string of the conversation.
        :param user_label: label for user messages
        :param response_label: label for assistant messages
        :return: formatted conversation string
        """
        formatted = ""
        for turn in self.conversation:
            label = user_label if turn['role'] == 'user' else response_label
            formatted += f"{label} {turn['message']}\n\n"
        return formatted
