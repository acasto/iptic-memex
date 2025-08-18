from base_classes import InteractionAction
from actions.assistant_output_action import AssistantOutputAction  # only for non-stream filter path


class ReprintChatAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Reprints the chat conversation.
        - Default: apply configured output filters to assistant messages
          (mirrors streaming display behavior) and respect `context_sent`.
        - With 'raw': bypass filters but still respect `context_sent` (rolling window).
        - With 'all': fetch full history and still apply filters.
        - With 'raw all': bypass filters and print the full history exactly as stored.
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
            # 'raw' means bypass filters; 'all' means fetch entire history
            bypass_filters = ('raw' in tokens)
            fetch_all = ('all' in tokens)

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

        # In non-blocking UIs (Web/TUI), emit the full formatted text as a single status message
        if not getattr(self.session.ui.capabilities, 'blocking', False):
            try:
                self.session.ui.emit('status', {'message': formatted})
            except Exception:
                pass
            return

        if self.session.get_params()['highlighting']:
            print(ui.format_code_block(formatted))
        else:
            print(formatted)
