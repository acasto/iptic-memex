from session_handler import InteractionContext
from datetime import datetime


class ChatContext(InteractionContext):
    def __init__(self, session, context_data=None):
        self.context_data = context_data
        self.session = session
        self.conversation = []  # list to hold the file name and content
        self.params = session.get_params()

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

    def get(self, mode=None):
        if mode == "all":
            return self.conversation

        context_sent = self.params.get('context_sent', 'all')
        if context_sent == 'none' or context_sent == 'last_1':
            return self.conversation[-1:] if self.conversation else []
        elif context_sent == 'all':
            return self.conversation
        else:
            parts = context_sent.split('_')
            if len(parts) == 2 and parts[1].isdigit():
                n = int(parts[1])
                if parts[0] == 'first':
                    return self.conversation[:n]
                elif parts[0] == 'last':
                    return self.conversation[-n:]

        # Default to returning all if the option is not recognized
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

    