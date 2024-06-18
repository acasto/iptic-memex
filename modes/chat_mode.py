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
        response = self.session.get_action('print_response')

        # Start the chat session loop
        tc.run('new_chat')
        while True:

            # Get contexts that have been loaded into the session
            contexts = []  # Note: we do it this way to account for more than just files (e.g. web scrapings)

            if self.session.get_context('file'):  # todo: we'll need to revisit this with additional contexts
                contexts.extend(self.session.get_context('file'))

            # Let the user know what context(s) we are working with
            if len(contexts) > 0:
                for context in contexts:
                    print(f"In context: {context.get()['name']}")
                print()

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
                continue

            # Add the question to the chat context
            self.chat.add(user_input, 'user', contexts)
            self.session.remove_context('file')  # remove the file from the SessionHandler
            del contexts  # clear contexts now that we have saved them to the chat context

            # Start the response
            response.run()

            print()
