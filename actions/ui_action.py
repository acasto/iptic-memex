from session_handler import InteractionAction
import os
import sys
import time


class UiAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        if args is None:
            args = []
        if len(args) > 0 and args[0] == 'reprint':
            self.reprint_conversation()

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

    @staticmethod
    def show_spinner(duration, message="Loading"):
        """
        Display a spinning cursor for the specified duration.

        :param duration: Time in seconds to show the spinner
        :param message: Optional message to display alongside the spinner
        """
        spinners = ['|', '/', '-', '\\']
        end_time = time.time() + duration

        print(f"{message}... ", end='', flush=True)

        while time.time() < end_time:
            for spinner in spinners:
                sys.stdout.write(spinner)
                sys.stdout.flush()
                time.sleep(0.1)
                sys.stdout.write('\b')

        print("Done!")
