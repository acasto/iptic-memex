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
        ui = self.session.get_action('ui')
        response = self.session.get_action('print_response')

        # Start the chat session loop
        tc.run('chat')
        while True:

            # process the contexts first
            contexts = pc.process_contexts_for_user()
            try:
                # Get the users input
                first_line = True
                user_input = []
                while True:
                    prompt = f"{ui.color_wrap(self.params['user_label'], self.params['user_label_color'])} " if first_line else ""
                    line = input(prompt)
                    first_line = False
                    if line.rstrip() == r"\\":
                        user_input.append(line[:-1])
                    elif line.endswith("\\"):
                        user_input.append(line[:-1])  # Strip trailing backslash
                    else:
                        user_input.append(line)
                        break
                full_input = " ".join(user_input).rstrip()  # Reconstruct input, removing trailing spaces
                user_input = full_input
                # user_input = input(f"{user_label} ")
                print()

                # Process any subcommands (will return True if no subcommands are found)
                if sc.run(user_input):
                    continue

            except (KeyboardInterrupt, EOFError):
                # make sure user really wants to quit
                print()
                input(ui.color_wrap("Hit Ctrl-C again to quit or Enter to continue.", 'red'))
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
