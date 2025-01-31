from session_handler import InteractionAction


class ShowAction(InteractionAction):
    """
    A general action for showing/listing things to the user
    """
    def __init__(self, session):
        self.session = session

    def run(self, args: list = None):
        """
        Show things to the user
        """
        if args is None:
            return

        if args[0] == 'settings':
            settings = self.session.get_session_state()
            sorted_params = sorted(settings['params'].items())
            print("Params:")
            print("---------------------------------")
            for key, value in sorted_params:
                if key == 'api_key':
                    value = '********'
                print(f"{key}: {value}")
            print()

        if args[0] == 'tool-settings':
            tools = self.session.get_tools()
            sorted_tools = sorted(tools.items())
            print("Tools:")
            print("---------------------------------")
            for key, value in sorted_tools:
                print(f"{key}: {value}")
            print()

        if args[0] == 'models':
            for section, options in self.session.list_models().items():
                print(section)
            print()

        if args[0] == 'messages':
            if len(args) > 1 and args[1] == 'all':
                messages = self.session.get_context('chat').get('all')
            else:
                messages = self.session.get_provider().get_messages()
            for message in messages:
                print(message)
                print()

        if args[0] == 'usage':
            usage = self.session.get_provider().get_usage()
            print(usage)
            print()

        if args[0] == 'contexts':
            contexts = self.session.get_action('process_contexts').get_contexts(self.session)
            if len(contexts) == 0:
                print(f"No contexts to clear.\n")
                return True

            for idx, context in enumerate(contexts):
                print(f"[{idx}] {context['context'].get()['name']}")
                print(f"Content: {context['context'].get()['content']}")
            print()
