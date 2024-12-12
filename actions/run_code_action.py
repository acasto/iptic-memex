from session_handler import InteractionAction
import re
import subprocess
import tempfile
import os


class RunCodeAction(InteractionAction):
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

    def run(self, args=None):
        n = 1  # Default to last message
        if args and len(args) > 0:
            try:
                n = int(args[0])
            except ValueError:
                print("Invalid argument. Using default.")

        # turns = self.chat.get()[-n:]
        turns = self.chat.get()
        code_blocks = self.extract_code_blocks(turns)

        if not code_blocks:
            print("No code blocks found in the recent messages.")
            return

        if len(code_blocks) > 1:
            selected_block = self.select_code_block(code_blocks)
        else:
            selected_block = code_blocks[0]

        if selected_block:
            output = self.run_code_block(selected_block)
            if output:
                self.offer_to_save_output(output)

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
        print("Multiple code blocks found. Please select one (or 'q' to quit):")
        for i, block in enumerate(code_blocks, 1):
            preview = self.create_preview(block)
            print(f"{i}.{preview}\n")

        while True:
            choice = input("Enter the number of the code block you want to run: ")
            if choice.lower() == 'q':
                return None
            try:
                choice_num = int(choice)
                if 1 <= choice_num <= len(code_blocks):
                    return code_blocks[choice_num - 1]
                else:
                    print("Invalid choice. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number or 'q' to quit.")

    def run_code_block(self, code_block):
        if code_block is None:
            print("Code execution cancelled.")
            return None

        language, code = code_block
        if not language:
            language = self.guess_language(code)

        if language not in self.supported_languages:
            print(f"Unsupported language: {language}")
            return None

        print(f"Language: {language}")
        print("Code to be executed:")
        print(code)
        confirm = input("Do you want to run this code? (y/n): ")
        if confirm.lower() != 'y':
            print("Code execution cancelled.")
            return None

        lang_info = self.supported_languages[language]
        with tempfile.NamedTemporaryFile(mode='w', suffix=lang_info['extension'], delete=False) as temp_file:
            temp_file.write(code)
            temp_file_path = temp_file.name

        try:
            command = [lang_info['command'], temp_file_path]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            print("\nExecution Result:")
            print(result.stdout)
            if result.stderr:
                print("Errors:")
                print(result.stderr)
            return f"Stdout:\n{result.stdout}\n\nStderr:\n{result.stderr}"
        except subprocess.TimeoutExpired:
            print("Execution timed out after 30 seconds.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")
        finally:
            os.unlink(temp_file_path)

        return None

    def offer_to_save_output(self, output):
        save_output = input("Do you want to save this output to context? (y/n): ")
        if save_output.lower() == 'y':
            context_name = input("Enter a name for this output context (default: 'Code Output'): ") or "Code Output"
            self.session.add_context('multiline_input', {
                'name': context_name,
                'content': output
            })
            print(f"Output saved to context as '{context_name}'")

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
