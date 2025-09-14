from base_classes import StepwiseAction, Completed


class LoadProjectAction(StepwiseAction):

    def __init__(self, session):
        self.session = session

    def start(self, args=None, content=None) -> Completed:
        blocking = bool(getattr(self.session.ui.capabilities, 'blocking', False))
        # Show current contexts summary to assist selection
        try:
            self.session.get_action('process_contexts').process_contexts_for_user()
        except Exception:
            pass

        choices = [
            'Add a file',
            'Add a pdf',
            'Add a sheet',
            'Add a doc',
            'Add multiline input',
            'Add web content',
            'Add code snippet (Python)',
            'Remove context item',
            'Save project',
            'Quit',
        ]

        if blocking:
            # Loop until user quits or saves
            while True:
                sel = self.session.ui.ask_choice('Project loader:', choices, default=choices[0])
                if sel == 'Add a file':
                    self.session.get_action('load_file').run()
                elif sel == 'Add a pdf':
                    self.session.get_action('load_pdf').run()
                elif sel == 'Add a sheet':
                    self.session.get_action('load_sheet').run()
                elif sel == 'Add a doc':
                    self.session.get_action('load_doc').run()
                elif sel == 'Add multiline input':
                    self.session.get_action('load_multiline').run()
                elif sel == 'Add web content':
                    self.session.get_action('fetch_from_web').run()
                elif sel == 'Add code snippet (Python)':
                    self.session.get_action('fetch_code_snippet').run()
                elif sel == 'Remove context item':
                    self.session.get_action('clear_context').run()
                elif sel == 'Save project':
                    return self._save_project_blocking()
                elif sel == 'Quit':
                    return Completed({'ok': True, 'quit': True})
                # Reprint contexts between iterations
                try:
                    self.session.get_action('process_contexts').process_contexts_for_user()
                except Exception:
                    pass
        else:
            # Single selection in Web/TUI: ask once and dispatch; the sub-action handles its own stepwise flow
            sel = self.session.ui.ask_choice('Project loader:', choices, default=choices[0])
            return Completed({'ok': True, 'selection': sel})

    def resume(self, state_token: str, response) -> Completed:
        # Handle Web/TUI selection
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        sel = str(response or '')
        if not sel:
            return Completed({'ok': True, 'cancelled': True})
        mapping = {
            'Add a file': 'load_file',
            'Add a pdf': 'load_pdf',
            'Add a sheet': 'load_sheet',
            'Add a doc': 'load_doc',
            'Add multiline input': 'load_multiline',
            'Add web content': 'fetch_from_web',
            'Add code snippet (Python)': 'fetch_code_snippet',
            'Remove context item': 'clear_context',
        }
        if sel in mapping:
            try:
                action = self.session.get_action(mapping[sel])
                # Always use run() as the public entrypoint
                action.run({})
                try:
                    self.session.ui.emit('status', {'message': f"Ran: {sel}"})
                except Exception:
                    pass
            except Exception:
                try:
                    self.session.ui.emit('error', {'message': f"Failed to run: {sel}"})
                except Exception:
                    pass
            return Completed({'ok': True, 'selection': sel, 'resumed': True})
        elif sel == 'Save project':
            # Minimal Web path: ask for fields sequentially
            name = self.session.ui.ask_text('Project Name:')
            desc = self.session.ui.ask_text('Description:')
            self.session.add_context('project', {'name': name, 'content': desc})
            try:
                self.session.ui.emit('status', {'message': f"Project '{name}' saved."})
            except Exception:
                pass
            return Completed({'ok': True, 'saved': True, 'name': name})
        else:
            return Completed({'ok': True, 'quit': True})

    def _save_project_blocking(self) -> Completed:
        name = self.session.ui.ask_text('Project Name:')
        desc = self.session.ui.ask_text('Description:')
        # Preview
        try:
            self.session.ui.emit('status', {'message': f"You have created the following project:\n\nProject Name: {name}\nDescription/Notes: {desc}"})
        except Exception:
            pass
        if self.session.ui.ask_bool('Would you like to save this project?', default=True):
            self.session.add_context('project', {'name': name, 'content': desc})
            return Completed({'ok': True, 'saved': True, 'name': name})
        else:
            # Optionally clear contexts
            if self.session.ui.ask_bool('Clear all contexts?', default=False):
                try:
                    self.session.get_action('clear_context').run({'target': 'all'})
                except Exception:
                    pass
            return Completed({'ok': True, 'saved': False})
