from base_classes import InteractionAction
import os


class ClearChatAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        pass

    def run(self, args=None):
        """
        Clears stuff from the chat state
        """
        if not args or len(args) == 0:  # if no args, do nothing
            return True
        elif args[0] == 'chat':
            self.clear_chat()
        elif args[0] == 'screen':
            self._clear_screen()
        elif args[0] == 'last':
            if len(args) > 1 and args[1].isdigit():
                self.remove_last_messages(int(args[1]))
            else:
                self.remove_last_message()
        elif args[0] == 'first':
            if len(args) > 1 and args[1].isdigit():
                self.remove_first_messages(int(args[1]))
            else:
                self.remove_first_message()
        return True

    def clear_chat(self):
        """
        Clear the entire chat context
        """
        self.session.get_context('chat').clear()
        self.session.get_provider().reset_usage()
        self._clear_screen()
        try:
            self.session.ui.emit('status', {'message': 'Chat history has been cleared.'})
        except Exception:
            pass

    def remove_last_message(self):
        """
        Remove the last message from the chat
        """
        chat_context = self.session.get_context('chat')
        if chat_context.remove_last_message():
            try:
                self.session.ui.emit('status', {'message': 'Last message removed.'})
            except Exception:
                pass
            self.session.get_action('reprint_chat').run()
        else:
            try:
                self.session.ui.emit('status', {'message': 'No messages to remove.'})
            except Exception:
                pass

    def remove_last_messages(self, n):
        """
        Remove the last n messages from the chat
        """
        chat_context = self.session.get_context('chat')
        removed = chat_context.remove_messages(n)
        if removed > 0:
            try:
                self.session.ui.emit('status', {'message': f'Last {removed} message(s) removed.'})
            except Exception:
                pass
            self.session.get_action('reprint_chat').run()
        else:
            try:
                self.session.ui.emit('status', {'message': 'No messages to remove.'})
            except Exception:
                pass

    def remove_first_message(self):
        """
        Remove the first message from the chat
        """
        chat_context = self.session.get_context('chat')
        if chat_context.remove_first_message():
            try:
                self.session.ui.emit('status', {'message': 'First message removed.'})
            except Exception:
                pass
            self.session.get_action('reprint_chat').run()
        else:
            try:
                self.session.ui.emit('status', {'message': 'No messages to remove.'})
            except Exception:
                pass

    def remove_first_messages(self, n):
        """
        Remove the first n messages from the chat
        """
        chat_context = self.session.get_context('chat')
        removed = chat_context.remove_first_messages(n)
        if removed > 0:
            try:
                self.session.ui.emit('status', {'message': f'First {removed} message(s) removed.'})
            except Exception:
                pass
            self.session.get_action('reprint_chat').run()
        else:
            try:
                self.session.ui.emit('status', {'message': 'No messages to remove.'})
            except Exception:
                pass

    @staticmethod
    def _clear_screen():
        try:
            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')
        except Exception:
            pass
