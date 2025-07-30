from base_classes import InteractionAction


class SetOptionAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def _convert_value(self, option: str, value: str):
        """Convert value to appropriate type based on parameter."""
        # Type mappings for common parameters
        type_mappings = {
            'max_tokens': int,
            'temperature': float,
            'top_p': float,
            'frequency_penalty': float,
            'presence_penalty': float,
            'stream': lambda x: x.lower() == 'true',
            'highlighting': lambda x: x.lower() == 'true',
            'colors': lambda x: x.lower() == 'true'
        }

        try:
            if option in type_mappings:
                return type_mappings[option](value)
            return value
        except (ValueError, TypeError):
            print(f"Invalid value type for {option}. Using default string value.")
            return value

    def run(self, args: list = None):
        if len(args) == 0:
            self.tc.run('option')  # Default to params mode
            while True:
                option = input(f"Enter option name (or q to exit): ")
                if option == 'q':
                    self.tc.run('chat')
                    break
                if option in self.session.get_params():
                    value = input(f"Enter value for {option}: ")
                    print()
                    converted_value = self._convert_value(option, value)
                    self.session.set_option(option, converted_value)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Option {option} not found.")
            return

        if args[0] == 'tools':
            self.tc.run('tools')
            while True:
                option = input(f"Enter tools option name (or q to exit): ")
                if option == 'q':
                    self.tc.run('chat')
                    break
                if option in self.session.get_tools():
                    value = input(f"Enter value for {option}: ")
                    print()
                    self.session.set_option(option, value, mode='tools')
                    self.tc.run('chat')
                    break
                else:
                    print(f"Option {option} not found.")
            return

        if len(args) == 1:  # No value provided
            print(f"Usage: set option {args[0]} <value>")
            return

        option, *value = args
        value = ' '.join(value) if value else ''  # Join remaining args into a single value

        if args[0] == 'tools':
            if option in self.session.get_tools():
                self.session.set_option(option, value, mode='tools')
                print(f"Tool option {option} set to {value}\n")
            else:
                print(f"Tool option {option} not found.")
        else:
            if option in self.session.get_params():
                converted_value = self._convert_value(option, value)  # Convert the value
                self.session.set_option(option, converted_value)     # Use converted value
                print(f"Option {option} set to {converted_value}\n")  # Show converted value
            else:
                print(f"Option {option} not found.")