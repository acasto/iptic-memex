from base_classes import InteractionAction
from actions.assistant_output_action import AssistantOutputAction  # only for non-stream filter path


class ReprintChatAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Reprints the chat conversation.
        - By default, applies the configured output filters to assistant messages
          (mirrors streaming display behavior).
        - If invoked with argument 'all', bypasses filters and prints raw messages
          exactly as stored (previous behavior).
        """
        ui = self.session.get_action('ui')
        chat = self.session.get_context('chat')
        params = self.session.get_params()

        # clear the screen
        ui.clear_screen()

        # Determine whether to bypass filters and/or fetch full history
        bypass_filters = False
        fetch_all = False
        if args:
            tokens = []
            if isinstance(args, list):
                tokens = [str(a).lower() for a in args]
            elif isinstance(args, str):
                tokens = [args.lower()]
            bypass_filters = 'all' in tokens
            fetch_all = 'all' in tokens

        formatted = ""
        turns = chat.get('all' if fetch_all else None)
        for turn in turns:
            if turn['role'] == 'user':
                label = ui.color_wrap(params['user_label'], params['user_label_color'])
                message = turn['message'] or ''
            else:
                label = ui.color_wrap(params['response_label'], params['response_label_color'])
                # Apply output filters unless bypassing
                if bypass_filters:
                    message = turn['message'] or ''
                else:
                    # Use the display pipeline to mirror what the user saw during streaming
                    message = AssistantOutputAction.filter_full_text(turn['message'] or '', self.session)

            formatted += f"{label} {message}\n\n"

        if self.session.get_params()['highlighting']:
            print(ui.format_code_block(formatted))
        else:
            print(formatted)
