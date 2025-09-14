from base_classes import StepwiseAction, Completed
import os
import re


class SaveCodeAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.chat = session.get_context('chat')
        # Tab completion remains useful in CLI; guard usage
        try:
            self.tc = session.utils.tab_completion
            self.tc.set_session(session)
        except Exception:
            self.tc = None
        self.preview_chars = 50  # Default number of characters per line in preview
        self.preview_lines = 1  # Default number of lines in preview
        # Stepwise state across prompts
        self._state = {}

    def start(self, args=None, content=None) -> Completed:
        # Parse optional preview settings from list args
        if isinstance(args, (list, tuple)) and args:
            try:
                if len(args) > 1:
                    self.preview_chars = int(args[1])
                if len(args) > 2:
                    self.preview_lines = int(args[2])
            except Exception:
                try:
                    self.session.ui.emit('warning', {'message': 'Invalid arguments. Using defaults.'})
                except Exception:
                    pass

        turns = self.chat.get()
        code_blocks = self.extract_code_blocks(turns)
        if not code_blocks:
            try:
                self.session.ui.emit('status', {'message': 'No code blocks found in the recent messages.'})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'no_code_blocks'})

        # Optional headless selection via args dict
        selected_idx = None
        dest_file = None
        overwrite = None
        create_dirs = None
        if isinstance(args, dict):
            try:
                if 'index' in args:
                    selected_idx = int(args.get('index'))
                dest_file = args.get('file')
                overwrite = bool(args.get('overwrite')) if args.get('overwrite') is not None else None
                create_dirs = bool(args.get('create_dirs')) if args.get('create_dirs') is not None else None
            except Exception:
                pass

        if selected_idx is not None:
            if 1 <= selected_idx <= len(code_blocks):
                self._state['selected_block'] = code_blocks[selected_idx - 1]
            else:
                return Completed({'ok': False, 'error': 'invalid_index'})
        elif len(code_blocks) > 1:
            # Ask user to pick a block
            options = []
            for i, block in enumerate(code_blocks, 1):
                options.append(f"{i}.\n{self.create_preview(block[1])}")
            self._state['code_blocks'] = code_blocks
            choice = self.session.ui.ask_choice('Select a code block to save:', options, default=options[0])
            # CLI returns string; in Web/TUI this raises InteractionNeeded and resume will handle it
            return self._handle_block_choice(choice)
        else:
            self._state['selected_block'] = code_blocks[0]

        # Ask for file path if not provided
        if not dest_file:
            if self.tc:
                try: self.tc.run('file_path')
                except Exception: pass
            dest_file = self.session.ui.ask_text('Enter the file path to save the code:')
        return self._attempt_save(dest_file, overwrite=overwrite, create_dirs=create_dirs)

    def resume(self, state_token: str, response) -> Completed:
        # Normalize response
        if isinstance(response, dict) and 'response' in response:
            response = response['response']

        phase = self._state.get('phase')
        if phase == 'choose_block':
            return self._handle_block_choice(str(response or ''))
        if phase == 'ask_path':
            return self._attempt_save(str(response or ''), overwrite=self._state.get('overwrite'), create_dirs=self._state.get('create_dirs'))
        if phase == 'confirm_mkdir':
            if bool(response):
                try:
                    os.makedirs(self._state.get('dest_dir'), exist_ok=True)
                except Exception as e:
                    try:
                        self.session.ui.emit('error', {'message': f'Failed to create directory: {e}'})
                    except Exception:
                        pass
                    return Completed({'ok': False, 'error': 'mkdir_failed'})
                # Retry save
                return self._attempt_save(self._state.get('dest_file'), overwrite=self._state.get('overwrite'), create_dirs=True)
            else:
                return Completed({'ok': False, 'cancelled': True})
        if phase == 'confirm_overwrite':
            if bool(response):
                return self._do_write(self._state.get('dest_file'), True)
            else:
                # Ask for a different path
                self._state['phase'] = 'ask_path'
                path = self.session.ui.ask_text('Enter a different file path to save the code:')
                return self.resume('__implicit__', path)

        # Fallback: if no phase, treat response as initial file path
        return self._attempt_save(str(response or ''))

    @staticmethod
    def extract_code_blocks(turns):
        # Combine all assistant messages into one text to handle multi-turn code blocks
        assistant_messages = []
        for turn in turns:
            if turn['role'] == 'assistant':
                assistant_messages.append(turn['message'])
        combined_text = "\n".join(assistant_messages)

        # We'll parse line-by-line to correctly handle code fences
        # We consider a code fence to be a line that consists of:
        #   ``` or ```<language>
        # and nothing else on that line (ignoring whitespace).
        # This helps avoid confusion with triple backticks appearing inside the code.
        lines = combined_text.split('\n')
        code_blocks = []
        inside_code = False
        code_language = ''
        code_content = []

        fence_pattern = re.compile(r'^\s*```(\w+)?\s*$')

        for line in lines:
            fence_match = fence_pattern.match(line)
            if fence_match:
                if not inside_code:
                    # Starting a code block
                    inside_code = True
                    code_language = fence_match.group(1) if fence_match.group(1) else ''
                    code_content = []
                else:
                    # Ending a code block
                    inside_code = False
                    code_blocks.append((code_language, "\n".join(code_content)))
                    code_language = ''
                    code_content = []
            else:
                if inside_code:
                    code_content.append(line)

        # If for some reason we ended while still inside a code block (unlikely but possible),
        # we won't add it as it's incomplete.
        return code_blocks

    def create_preview(self, block_content):
        lines = str(block_content).split('\n')
        preview_lines = lines[:self.preview_lines]
        preview = '\n'.join(
            '    ' + (line[:self.preview_chars] + '...' if len(line) > self.preview_chars else line) for line in
            preview_lines)
        if len(lines) > self.preview_lines:
            preview += f"\n    ... ({len(lines)} lines total)"
        return preview

    # --- stepwise helpers ---
    def _handle_block_choice(self, choice: str) -> Completed:
        code_blocks = self._state.get('code_blocks') or []
        if not code_blocks:
            return Completed({'ok': False, 'error': 'no_blocks'})
        # Choice text starts with "<i>."; extract index
        idx = None
        try:
            idx = int(str(choice).split('.', 1)[0])
        except Exception:
            pass
        if not idx or idx < 1 or idx > len(code_blocks):
            # Re-ask
            self._state['phase'] = 'choose_block'
            options = []
            for i, block in enumerate(code_blocks, 1):
                options.append(f"{i}.\n{self.create_preview(block[1])}")
            sel = self.session.ui.ask_choice('Select a code block to save:', options, default=options[0])
            return self._handle_block_choice(sel)
        self._state['selected_block'] = code_blocks[idx - 1]
        # Ask for file path next
        if self.tc:
            try: self.tc.run('file_path')
            except Exception: pass
        self._state['phase'] = 'ask_path'
        path = self.session.ui.ask_text('Enter the file path to save the code:')
        return self._attempt_save(path)

    def _attempt_save(self, file_path: str, *, overwrite=None, create_dirs=None) -> Completed:
        if not file_path:
            return Completed({'ok': False, 'cancelled': True})
        self._state['dest_file'] = file_path
        directory = os.path.dirname(file_path)
        # Handle mkdir prompt when needed
        if directory and not os.path.exists(directory):
            if create_dirs is None:
                self._state.update({'phase': 'confirm_mkdir', 'dest_dir': directory})
                yn = self.session.ui.ask_bool(f"Directory {directory} doesn't exist. Create it?", default=True)
                return self.resume('__implicit__', yn)
            if not create_dirs:
                return Completed({'ok': False, 'error': 'missing_dir'})
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                try:
                    self.session.ui.emit('error', {'message': f'Failed to create directory: {e}'})
                except Exception:
                    pass
                return Completed({'ok': False, 'error': 'mkdir_failed'})

        # Handle overwrite prompt
        if os.path.exists(file_path) and not overwrite:
            self._state['phase'] = 'confirm_overwrite'
            yn = self.session.ui.ask_bool(f"File {file_path} already exists. Overwrite?", default=False)
            return self.resume('__implicit__', yn)
        return self._do_write(file_path, bool(overwrite))

    def _do_write(self, file_path: str, overwrite: bool) -> Completed:
        # Extract code content
        block = self._state.get('selected_block')
        if not block:
            return Completed({'ok': False, 'error': 'no_block_selected'})
        _, content = block
        try:
            # Safe write (overwrite True ignores existence check, otherwise file may still exist but user confirmed earlier)
            with open(file_path, 'w') as f:
                f.write(content)
            try:
                self.session.ui.emit('status', {'message': f'Code saved to {file_path}'})
            except Exception:
                pass
            # Reset tab completion mode back to chat
            try:
                if self.tc: self.tc.run('chat')
            except Exception:
                pass
            return Completed({'ok': True, 'saved': True, 'file': file_path})
        except Exception as e:
            try:
                self.session.ui.emit('error', {'message': f'Error saving file: {str(e)}'})
            except Exception:
                pass
            return Completed({'ok': False, 'error': str(e)})

    def set_preview_options(self, chars=None, lines=None):
        if chars is not None:
            self.preview_chars = chars
        if lines is not None:
            self.preview_lines = lines
