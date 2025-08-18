from base_classes import StepwiseAction, Completed


class SetOptionAction(StepwiseAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def _convert_value(self, option: str, value: str):
        """Convert value to appropriate type based on parameter."""
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
            try:
                self.session.ui.emit('warning', {'message': f"Invalid value type for {option}. Using string value."})
            except Exception:
                pass
            return value

    def start(self, args=None, content: str = "") -> Completed:
        # Parse args
        mode = 'params'
        option = None
        value = None

        if isinstance(args, dict):
            mode = 'tools' if str(args.get('mode', '')).lower() == 'tools' else 'params'
            option = args.get('option')
            value = args.get('value')
        elif isinstance(args, (list, tuple)):
            tokens = [str(a) for a in args]
            if tokens and tokens[0].lower() == 'tools':
                mode = 'tools'
                tokens = tokens[1:]
            if tokens:
                option = tokens[0]
                if len(tokens) > 1:
                    value = ' '.join(tokens[1:])
        elif isinstance(args, str):
            tokens = args.split()
            if tokens and tokens[0].lower() == 'tools':
                mode = 'tools'
                tokens = tokens[1:]
            if tokens:
                option = tokens[0]
                if len(tokens) > 1:
                    value = ' '.join(tokens[1:])

        # Interactive prompts as needed
        if mode == 'tools':
            self.tc.run('tools')
        else:
            self.tc.run('option')

        if not option:
            prompt = "Enter tools option name:" if mode == 'tools' else "Enter option name:"
            option = self.session.ui.ask_text(prompt)

        # Cancel support
        if str(option).strip().lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        # Validate option existence
        valid_pool = self.session.get_tools() if mode == 'tools' else self.session.get_params()
        if option not in valid_pool:
            try:
                self.session.ui.emit('error', {'message': f"Option '{option}' not found in {mode}."})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'not_found', 'option': option, 'mode': mode})

        if value is None:
            value = self.session.ui.ask_text(f"Enter value for {option}:")

        # Apply value (convert on params only)
        if mode == 'tools':
            self.session.set_option(option, value, mode='tools')
        else:
            converted_value = self._convert_value(option, str(value))
            self.session.set_option(option, converted_value)

        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': f"Option {option} set to {value if mode=='tools' else converted_value}"})
        except Exception:
            pass
        return Completed({'ok': True, 'mode': mode, 'option': option, 'value': value if mode=='tools' else converted_value})

    def resume(self, state_token: str, response) -> Completed:
        # Expect structure with prior args in state and a raw response
        mode = 'params'
        option = None
        value = None
        state_args = None

        # Normalize response and extract state
        if isinstance(response, dict):
            state_args = (response.get('state') or {}).get('args') or response.get('args')
            if 'response' in response:
                response = response['response']

        if isinstance(state_args, dict):
            mode = 'tools' if str(state_args.get('mode', '')).lower() == 'tools' else 'params'
            option = state_args.get('option')
            value = state_args.get('value')
        elif isinstance(state_args, (list, tuple)):
            tokens = [str(a) for a in state_args]
            if tokens and tokens[0].lower() == 'tools':
                mode = 'tools'
                tokens = tokens[1:]
            if tokens:
                option = tokens[0]
                if len(tokens) > 1:
                    value = ' '.join(tokens[1:])

        # If option was missing previously, the response is the option
        if not option:
            option = str(response or '')
            # Validate and possibly prompt for value next
            valid_pool = self.session.get_tools() if mode == 'tools' else self.session.get_params()
            if option not in valid_pool:
                try:
                    self.session.ui.emit('error', {'message': f"Option '{option}' not found in {mode}."})
                except Exception:
                    pass
                self.tc.run('chat')
                return Completed({'ok': False, 'error': 'not_found', 'option': option, 'mode': mode})
            # Ask for value now (may raise again)
            value = self.session.ui.ask_text(f"Enter value for {option}:")
        else:
            # Option existed; if value was missing, response is the value
            if value is None:
                value = response

        # Apply
        if mode == 'tools':
            self.session.set_option(option, value, mode='tools')
            applied = value
        else:
            converted_value = self._convert_value(option, str(value))
            self.session.set_option(option, converted_value)
            applied = converted_value

        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': f"Option {option} set to {applied}"})
        except Exception:
            pass
        return Completed({'ok': True, 'mode': mode, 'option': option, 'value': applied, 'resumed': True})
