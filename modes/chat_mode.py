from session_handler import InteractionMode


class ChatMode(InteractionMode):
    """
    Interaction handler for chat mode
    """

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()

        # Initialize a chat context object
        session.add_context('chat')  # initialize a chat context object
        self.chat = session.get_context('chat')  # get the chat context object

    def start(self):
        # Setup some core actions
        tc = self.session.get_action('tab_completion')
        sc = self.session.get_action('process_subcommands')
        pc = self.session.get_action('process_contexts')
        response = self.session.get_action('print_response')

        # Start the chat session loop
        tc.run('chat')
        while True:

            # process the contexts first
            contexts = pc.run()

            try:
                # Get the users input
                user_input = input(f"{self.params['user_label']} ")
                print()

                # Process any subcommands (will return True if no subcommands are found)
                if sc.run(user_input):
                    continue

            except (KeyboardInterrupt, EOFError):
                # make sure user really wants to quit
                print()
                input("Hit Ctrl-C again to quit or Enter to continue.")
                print()
                tc.run('chat')  # in case tab completion was modified before the interrupt
                continue

            # Add the question to the chat context
            self.chat.add(user_input, 'user', contexts)
            for context in list(self.session.get_context().keys()):
                if context != 'prompt' and context != 'chat':  # Ignore the prompt and chat contexts
                    self.session.remove_context_type(context)   # remove the context for this turn
            del contexts  # clear contexts now that we have saved them to the chat context

            # Start the response
            response.run()

            print()
