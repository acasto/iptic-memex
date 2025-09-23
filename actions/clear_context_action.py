from base_classes import StepwiseAction, Completed, InteractionNeeded


class ClearContextAction(StepwiseAction):
    """Remove a specific context or clear all (Stepwise)."""

    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')

    def start(self, args=None, content: str = "") -> Completed:
        args = args or []
        # Handle 'all' argument directly
        if (isinstance(args, str) and args.lower() == 'all') or (isinstance(args, (list, tuple)) and args and str(args[0]).lower() == 'all') or (isinstance(args, dict) and str(args.get('target', '')).lower() == 'all'):
            self._clear_all()
            return Completed({'ok': True, 'cleared': 'all'})

        contexts = self._get_contexts()
        if not contexts:
            try:
                self.session.ui.emit('status', {'message': 'No contexts to clear.'})
            except Exception:
                pass
            return Completed({'ok': True, 'cleared': 0})

        # If index provided in args
        index = None
        if isinstance(args, (list, tuple)) and args:
            try:
                index = int(args[0])
            except Exception:
                index = None
        elif isinstance(args, dict) and 'index' in args:
            try:
                index = int(args.get('index'))
            except Exception:
                index = None

        if index is None:
            # Build options for selection and raise InteractionNeeded
            options = []
            for i, c in enumerate(contexts):
                name = (c['context'].get().get('name') if hasattr(c['context'], 'get') else str(c['context']))
                try:
                    content_text = c['context'].get().get('content', '') if hasattr(c['context'], 'get') else ''
                    tokens = self.token_counter.count_tiktoken(content_text) if content_text else 0
                except Exception:
                    tokens = 0
                options.append(f"{i}: {name} ({tokens} tokens)")
            
            raise InteractionNeeded(
                kind='choice',
                spec={
                    'prompt': 'Select a context to remove:',
                    'options': options,
                    'default': options[0] if options else None
                },
                state_token='awaiting_selection'
            )

        if index is None or index < 0 or index >= len(contexts):
            try:
                self.session.ui.emit('status', {'message': 'Invalid context index.'})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'invalid_index'})

        # Remove the selected context
        self.session.remove_context_item(contexts[index]['type'], contexts[index]['idx'])
        try:
            self.session.ui.emit('status', {'message': 'Context removed.'})
        except Exception:
            pass
        return Completed({'ok': True, 'cleared': 1, 'index': index})

    def resume(self, state_token: str, response) -> Completed:
        if state_token == 'awaiting_selection':
            # Resume accepts selected option label and removes it
            if isinstance(response, dict):
                response = response.get('response') or response.get('value')
            
            if not response:
                try:
                    self.session.ui.emit('status', {'message': 'Context clear cancelled.'})
                except Exception:
                    pass
                return Completed({'ok': True, 'cancelled': True})
            
            try:
                index = int(str(response).split(':', 1)[0])
            except Exception:
                return Completed({'ok': False, 'error': 'invalid_index'})
            
            contexts = self._get_contexts()
            if index < 0 or index >= len(contexts):
                return Completed({'ok': False, 'error': 'invalid_index'})
            
            self.session.remove_context_item(contexts[index]['type'], contexts[index]['idx'])
            try:
                self.session.ui.emit('status', {'message': 'Context removed.'})
            except Exception:
                pass
            return Completed({'ok': True, 'cleared': 1, 'index': index, 'resumed': True})
        
        return Completed({'ok': False, 'error': f'Unknown state token: {state_token}'})

    def _get_contexts(self):
        pc = self.session.get_action('process_contexts')
        return pc.get_contexts(self.session) if pc else []

    def _clear_all(self):
        contexts = self._get_contexts()
        # Remove from the end to keep indices stable
        for c in sorted(contexts, key=lambda x: x['idx'], reverse=True):
            self.session.remove_context_item(c['type'], c['idx'])
        try:
            self.session.ui.emit('status', {'message': 'All contexts cleared.'})
        except Exception:
            pass
