from base_classes import StepwiseAction, Completed
import re
import subprocess
import tempfile
import os


class RunCodeAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.chat = session.get_context('chat')
        self.preview_chars = 50
        self.preview_lines = 1
        self.supported_languages = {
            'python': {'extension': '.py', 'command': 'python'},
            'bash': {'extension': '.sh', 'command': 'bash'},
            # Add more languages here in the future
        }

    def start(self, args=None, content=None) -> Completed:
        n = 1
        if isinstance(args, (list, tuple)) and args:
            try:
                n = int(args[0])
            except Exception:
                n = 1
        elif isinstance(args, dict):
            try:
                n = int(args.get('n', 1))
            except Exception:
                n = 1

        turns = self.chat.get()
        code_blocks = self.extract_code_blocks(turns[-(n*10):] if turns else [])

        if not code_blocks:
            try:
                self.session.ui.emit('status', {'message': 'No code blocks found in the recent messages.'})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'no_blocks'})

        if len(code_blocks) > 1:
            # Build choice labels with previews
            options = []
            mapping = {}
            for i, block in enumerate(code_blocks, 1):
                label = self.create_preview(block)
                key = f"{i}"
                mapping[key] = block
                options.append(key + ': ' + label.replace('\n', ' '))
            choice = self.session.ui.ask_choice('Multiple code blocks found. Select one:', options, default=options[0])
            # Extract index
            try:
                idx = int(str(choice).split(':', 1)[0]) - 1
                selected_block = code_blocks[idx]
            except Exception:
                selected_block = code_blocks[0]
        else:
            selected_block = code_blocks[0]

        output = self.run_code_block(selected_block)
        if output:
            # Ask to save only in blocking UIs
            if getattr(self.session.ui.capabilities, 'blocking', False):
                if self.session.ui.ask_bool('Save this output to context?', default=False):
                    name = self.session.ui.ask_text('Context name:', default='Code Output')
                    self._save_output(name, output)
                    try:
                        self.session.ui.emit('status', {'message': f"Output saved as '{name}'"})
                    except Exception:
                        pass
            return Completed({'ok': True, 'saved': False, 'output': output})
        return Completed({'ok': False, 'error': 'execution_failed'})

    def resume(self, state_token: str, response) -> Completed:
        # For Web/TUI: first resume is from ask_choice selection label
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        # Recompute blocks and choose based on label index
        turns = self.chat.get()
        code_blocks = self.extract_code_blocks(turns)
        try:
            idx = int(str(response).split(':', 1)[0]) - 1
            selected_block = code_blocks[idx]
        except Exception:
            selected_block = code_blocks[0] if code_blocks else None
        if not selected_block:
            return Completed({'ok': False, 'error': 'no_blocks'})
        output = self.run_code_block(selected_block)
        return Completed({'ok': True, 'output': output})

    def extract_code_blocks(self, turns):
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

    def create_preview(self, block):
        language, code = block
        lines = code.split('\n')
        preview_lines = lines[:self.preview_lines]
        preview = '\n'.join('    ' + (line[:self.preview_chars] + '...' if len(line) > self.preview_chars else line) for line in preview_lines)
        if len(lines) > self.preview_lines:
            preview += f"\n    ... ({len(lines)} lines total)"
        return f"Language: {language or 'unspecified'}\n{preview}"

    def select_code_block(self, code_blocks):
        # Legacy path unused; selection handled via ask_choice in start/resume
        return None

    def run_code_block(self, code_block):
        if code_block is None:
            try:
                self.session.ui.emit('status', {'message': 'Code execution cancelled.'})
            except Exception:
                pass
            return None

        language, code = code_block
        if not language:
            language = self.guess_language(code)

        if language not in self.supported_languages:
            try:
                self.session.ui.emit('error', {'message': f"Unsupported language: {language}"})
            except Exception:
                pass
            return None

        # Confirm execution in blocking UI only
        if getattr(self.session.ui.capabilities, 'blocking', False):
            try:
                self.session.ui.emit('status', {'message': f"Language: {language}"})
                self.session.ui.emit('status', {'message': 'Code to be executed:'})
                self.session.ui.emit('status', {'message': code})
            except Exception:
                pass
            confirm = self.session.ui.ask_bool('Do you want to run this code?', default=False)
            if not confirm:
                try:
                    self.session.ui.emit('status', {'message': 'Code execution cancelled.'})
                except Exception:
                    pass
                return None

        lang_info = self.supported_languages[language]
        with tempfile.NamedTemporaryFile(mode='w', suffix=lang_info['extension'], delete=False) as temp_file:
            temp_file.write(code)
            temp_file_path = temp_file.name

        try:
            command = [lang_info['command'], temp_file_path]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            output = f"Stdout:\n{result.stdout}\n\nStderr:\n{result.stderr}"
            try:
                self.session.ui.emit('status', {'message': '\nExecution Result:'})
                self.session.ui.emit('status', {'message': result.stdout})
                if result.stderr:
                    self.session.ui.emit('warning', {'message': 'Errors:'})
                    self.session.ui.emit('status', {'message': result.stderr})
            except Exception:
                pass
            return output
        except subprocess.TimeoutExpired:
            try:
                self.session.ui.emit('error', {'message': 'Execution timed out after 30 seconds.'})
            except Exception:
                pass
        except Exception as e:
            try:
                self.session.ui.emit('error', {'message': f"An error occurred: {str(e)}"})
            except Exception:
                pass
        finally:
            os.unlink(temp_file_path)

        return None

    def offer_to_save_output(self, output):
        # Deprecated in Stepwise path; kept for compatibility if referenced elsewhere
        pass
    def guess_language(self, code):
        # Simple language guessing based on common patterns
        if code.strip().startswith('#!/bin/bash') or 'echo' in code:
            return 'bash'
        elif 'import' in code or 'def' in code or 'print(' in code:
            return 'python'
        else:
            return 'python'  # Default to Python if unsure

    def set_preview_options(self, chars=None, lines=None):
        if chars is not None:
            self.preview_chars = chars
        if lines is not None:
            self.preview_lines = lines
