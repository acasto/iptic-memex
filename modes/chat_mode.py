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
        sc = self.session.get_action('process_subcommands')
        pc = self.session.get_action('process_contexts')
        ui = self.session.utils
        tc = ui.tab_completion
        tc.set_session(self.session)  # Set the session for tab completion for dynamic context completion
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
                    prompt = f"{ui.output.style_text(self.params['user_label'], fg=self.params['user_label_color'])} " if first_line else ""
                    line = ui.input.get_input(prompt)
                    first_line = False
                    if line.rstrip() == r"\\":
                        break
                    elif line.endswith("\\"):
                        user_input.append(line[:-1] + "\n")  # Add newline instead of stripping backslash
                    else:
                        user_input.append(line)
                        break  # Exit loop on a line without trailing backslash

                full_input = "".join(user_input).rstrip()  # Join without spaces and remove trailing newline
                user_input = full_input
                ui.output.write()

                # Process any subcommands (will return True if no subcommands are found)
                if sc.run(user_input):
                    continue

            except (KeyboardInterrupt, EOFError):
                try:
                    ui.input.get_input(ui.output.style_text("Hit Ctrl-C again to quit or Enter to continue.", fg='red'), spacing=1)
                    tc.run('chat')  # They hit enter to continue
                    continue
                except (KeyboardInterrupt, EOFError):  # They hit Ctrl-C again
                    ui.output.write()
                    self.session.get_action('persist_stats').run()
                    raise  # Re-raise to exit

            # Add the question to the chat context
            self.chat.add(user_input, 'user', contexts)
            for context in list(self.session.get_context().keys()):
                if context != 'prompt' and context != 'chat':  # Ignore the prompt and chat contexts
                    self.session.remove_context_type(context)   # remove the context for this turn
            del contexts  # clear contexts now that we have saved them to the chat context

            # Start the response
            response.run()

            ui.output.write()
