from session_handler import InteractionAction


class ProcessContextsAction(InteractionAction):
    """
    Class for processing contexts
    """

    def __init__(self, session):
        self.session = session

    def run(self, user_input=None):
        """
        Process the contexts that have been loaded into the session
        """
        contexts = []  # Note: we do it this way to account for more than just files (e.g. web scrapings)

        if self.session.get_context('file'):  # todo: we'll need to revisit this with additional contexts
            contexts.extend(self.session.get_context('file'))

        # Let the user know what context(s) we are working with
        if len(contexts) > 0:
            for context in contexts:
                print(f"In context: {context.get()['name']}")
            print()
        return contexts
