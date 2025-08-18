from base_classes import StepwiseAction, Completed


class LoadMultilineAction(StepwiseAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def start(self, args=None, content: str = "") -> Completed:
        self.tc.deactivate_completion()
        blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)

        if blocking and not content:
            # Restore legacy behavior: capture lines until Ctrl+C
            try:
                self.session.ui.emit('status', {'message': 'Entering multiline input mode. Press Ctrl+C when finished.'})
            except Exception:
                pass
            lines = []
            try:
                while True:
                    line = self.session.utils.input.get_input(prompt="")
                    lines.append(line)
            except KeyboardInterrupt:
                pass
            text = "\n".join(lines).rstrip("\n")
        else:
            # Non-blocking (Web/TUI) or content provided: ask in one go
            text = content if content else self.session.ui.ask_text("Enter multiline text:", multiline=True)
            text = str(text or "").rstrip("\n")

        if not text.strip():
            try:
                self.session.ui.emit('warning', {'message': 'No input provided. Exiting multiline mode.'})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        # Ask for a name
        name = self.session.ui.ask_text("Enter a name for this multiline input:", default="Multiline Input")
        if str(name).strip().lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        self.session.add_context('multiline_input', {'name': str(name).strip() or 'Multiline Input', 'content': text})
        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': 'Multiline input saved to context.'})
        except Exception:
            pass
        return Completed({'ok': True, 'saved': True, 'name': str(name).strip() or 'Multiline Input'})

    def resume(self, state_token: str, response) -> Completed:
        # If first ask returned text, resume will be text; ask for name next.
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        text = str(response or '')
        if not text.strip():
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})
        # Now prompt for name
        name = self.session.ui.ask_text("Enter a name for this multiline input:", default="Multiline Input")
        if str(name).strip().lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})
        self.session.add_context('multiline_input', {'name': str(name).strip() or 'Multiline Input', 'content': text})
        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': 'Multiline input saved to context.'})
        except Exception:
            pass
        return Completed({'ok': True, 'saved': True, 'name': str(name).strip() or 'Multiline Input', 'resumed': True})
