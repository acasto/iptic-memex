from base_classes import StepwiseAction, Completed


class SetModelAction(StepwiseAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def start(self, args=None, content: str = "") -> Completed:
        models_active = self.session.list_models() or {}
        models_all = self.session.list_models(showall=True) or {}
        model_names_active = (
            list(models_active.keys())
            if isinstance(models_active, dict)
            else list(models_active or [])
        )
        model_names_all = (
            list(models_all.keys())
            if isinstance(models_all, dict)
            else list(models_all or [])
        )

        # Accept pre-specified model via args
        chosen = None
        if isinstance(args, (list, tuple)) and args:
            chosen = " ".join(str(a) for a in args)
        elif isinstance(args, dict):
            chosen = args.get('model') or args.get('name')

        if not chosen:
            self.tc.run('model')
            # Use a choice prompt to avoid typos
            options = model_names_active or model_names_all
            default = options[0] if options else None
            chosen = self.session.ui.ask_choice("Select a model:", options, default=default)

        chosen = str(chosen)
        if chosen.lower() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'cancelled': True})

        normalized = self.session.normalize_model_name(chosen)
        if normalized:
            before = self.session.get_params().get('model')
            self.session.switch_model(normalized)
            after = self.session.get_params().get('model')
            if after != normalized and before != normalized:
                try:
                    self.session.ui.emit('error', {'message': f"Could not switch to '{normalized}'."})
                except Exception:
                    pass
                self.tc.run('chat')
                return Completed({'ok': False, 'error': 'switch_failed', 'model': normalized})
            self.tc.run('chat')
            try:
                self.session.ui.emit('status', {'message': f"Switched model to {normalized}"})
            except Exception:
                pass
            return Completed({'ok': True, 'model': normalized})
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
