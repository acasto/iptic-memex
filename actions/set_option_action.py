from session_handler import InteractionAction


class SetOptionAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args: list = None):
        print(self.session.get_params())
        if len(args) == 0:
            self.tc.run('option')
            while True:
                option = input(f"Enter option name (or q to exit): ")
                if option == 'q':
                    self.tc.run('chat')
                    break
                if option in self.session.get_params():
                    value = input(f"Enter value for {option}: ")
                    print()
                    self.session.set_option(option, value)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Option {option} not found.")
            return

        if len(args) == 1:  # No value provided
            print(f"Usage: set option {args[0]} <value>")
            return

        option, *value = args
        if option in self.session.get_params():
            value = ' '.join(value) if value else ''  # Join remaining args into a single value
            self.session.set_option(option, value)
            print(f"Option {option} set to {value}\n")
        else:
            print(f"Option {option} not found.")
