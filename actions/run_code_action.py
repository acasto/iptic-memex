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

        recent_turns = self.chat.get()[-n:]
        code_blocks = self.extract_code_blocks(recent_turns)

        if not code_blocks:
            print("No code blocks found in the recent messages.")
            return

        if len(code_blocks) > 1:
            selected_block = self.select_code_block(code_blocks)
        else:
            selected_block = code_blocks[0]

        if selected_block:
            self.run_code_block(selected_block)

    def extract_code_blocks(self, turns):
        code_blocks = []
        for turn in turns:
            if turn['role'] == 'assistant':
                blocks = re.findall(r'```(\w+)?\n(.*?)```', turn['message'], re.DOTALL)
                code_blocks.extend(blocks)
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
            return

        language, code = code_block
        if not language:
            language = self.guess_language(code)

        if language not in self.supported_languages:
            print(f"Unsupported language: {language}")
            return

        print(f"Language: {language}")
        print("Code to be executed:")
        print(code)
        confirm = input("Do you want to run this code? (y/n): ")
        if confirm.lower() != 'y':
            print("Code execution cancelled.")
            return

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
        except subprocess.TimeoutExpired:
            print("Execution timed out after 30 seconds.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")
        finally:
            os.unlink(temp_file_path)

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
