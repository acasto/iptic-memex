from session_handler import InteractionAction


class AssistantMemoryToolAction(InteractionAction):
    """
    Action for handling math operations
    """
    def __init__(self, session):
        self.session = session
        self.temp_file_runner = session.get_action('assistant_cmd_handler')

    def run(self, args: dict, content: str = ""):
        content = args.get('memory', content)
        if args.get('action') == 'save':
            output = self.session.utils.fs.write_file(
                '/Users/adam/.config/iptic-memex/memory.txt',
                content,
                append=True
            )
            # Provide feedback about the save operation
            feedback = "Memory successfully saved." if output else "Failed to save memory."
            self.session.add_context('assistant', {'name': 'assistant_feedback', 'content': feedback})

        if args.get('action') == 'read':
            output = self.session.utils.fs.read_file('/Users/adam/.config/iptic-memex/memory.txt')
            if output is not None:
                self.session.add_context('assistant', {'name': 'assistant_context', 'content': output})
            else:
                self.session.add_context('assistant', {'name': 'assistant_feedback', 'content': "Failed to read memory file."})
