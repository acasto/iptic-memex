from base_classes import StepwiseAction, Completed


class SetModelAction(StepwiseAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def start(self, args=None, content: str = "") -> Completed:
        models = self.session.list_models()

        # Accept pre-specified model via args
        chosen = None
        if isinstance(args, (list, tuple)) and args:
            chosen = " ".join(str(a) for a in args)
        elif isinstance(args, dict):
            chosen = args.get('model') or args.get('name')

        if not chosen:
            self.tc.run('model')
            # Use a choice prompt to avoid typos
            chosen = self.session.ui.ask_choice("Select a model:", models, default=models[0] if models else None)

        chosen = str(chosen)
        if chosen.lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        if chosen in models:
            self.session.switch_model(chosen)
            self.tc.run('chat')
            try:
                self.session.ui.emit('status', {'message': f"Switched model to {chosen}"})
            except Exception:
                pass
            return Completed({'ok': True, 'model': chosen})
        else:
            try:
                self.session.ui.emit('error', {'message': f"Model '{chosen}' not found."})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'not_found', 'model': chosen})

    def resume(self, state_token: str, response) -> Completed:
        # Expect a choice string or {response: value}
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        return self.start({'model': response})
