import os
from session_handler import InteractionAction


class ClearChatAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Clears stuff from the chat state
        """
        if len(args) == 0:  # if no args, clear the chat
            return True
        elif args[0] == 'chat':
            self.clear_chat()
        elif args[0] == 'screen':
            self.clear_screen()
        elif args[0] == 'last':
            if len(args) > 1 and args[1].isdigit():
                self.remove_messages(int(args[1]))
            else:
                self.remove_last_message()
        return True

    def clear_chat(self):
        """
        Clear the entire chat context
        """
        self.session.get_context('chat').clear()
        self.clear_screen()
        print(f"Chat history has been cleared.\n")

    def remove_last_message(self):
        """
        Remove the last message from the chat
        """
        chat_context = self.session.get_context('chat')
        if chat_context.remove_last_message():
            print("Last message removed.")
            self.reprint_conversation()
        else:
            print(f"No messages to remove.\n")

    def remove_messages(self, n):
        """
        Remove the last n messages from the chat
        """
        chat_context = self.session.get_context('chat')
        removed = chat_context.remove_messages(n)
        if removed > 0:
            print(f"Last {removed} message(s) removed.")
            self.reprint_conversation()
        else:
            print(f"No messages to remove.\n")

    def reprint_conversation(self):
        """
        Clear the screen and reprint the conversation
        """
        self.clear_screen()
        chat_context = self.session.get_context('chat')
        params = self.session.get_params()
        formatted_conversation = chat_context.get_formatted_conversation(
            params['user_label'],
            params['response_label']
        )
        print(formatted_conversation)

    @staticmethod
    def clear_screen():
        """
        Clear the screen
        """
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')
        return True
