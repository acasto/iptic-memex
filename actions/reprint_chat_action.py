from session_handler import InteractionAction


class ReprintChatAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Reprints the chat conversation and applies syntax highlighting if enabled
        """
        ui = self.session.get_action('ui')
        chat = self.session.get_context('chat')
        params = self.session.get_params()

        # clear the screen
        ui.clear_screen()

        formatted = ""
        for turn in chat.get():
            if turn['role'] == 'user':
                label = ui.color_wrap(params['user_label'], params['user_label_color'])
            else:
                label = ui.color_wrap(params['response_label'], params['response_label_color'])
            formatted += f"{label} {turn['message']}\n\n"

        if self.session.get_params()['highlighting']:
            print(ui.format_code_block(formatted))
        else:
            print(formatted)
