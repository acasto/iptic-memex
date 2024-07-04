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
            for key, value in sorted_params:
                if key == 'api_key':
                    value = '********'
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

