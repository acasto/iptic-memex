from base_classes import InteractionAction


class LoadMultilineAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args=None):
        self.tc.deactivate_completion()
        ui = self.session.get_action('ui')
        print(ui.color_wrap("Entering multiline input mode. Press Ctrl+C when finished.", 'cyan'))
        print()
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except KeyboardInterrupt:
            print(ui.color_wrap("\nMultiline input completed.", 'cyan'))

        if not lines:
            print(ui.color_wrap("No input provided. Exiting multiline mode.", 'red'))
            return True

        while True:
            name = input(ui.color_wrap("Enter a name for this multiline input (or 'q' to quit): ", "cyan")).strip()
            if name.lower() == 'q':
                print(ui.color_wrap("Exiting multiline mode without saving.", 'red'))
                return True
            if name:
                break
            print(ui.color_wrap("Name cannot be empty. Please try again.", 'red'))

        content = '\n'.join(lines)
        context_data = {
            'name': name,
            'content': content
        }
        self.session.add_context('multiline_input', context_data)
        self.tc.run('chat')
        return
