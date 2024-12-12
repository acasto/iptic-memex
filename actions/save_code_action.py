from session_handler import InteractionAction
import os
import re


class SaveCodeAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.chat = session.get_context('chat')
        self.tc = session.get_action('tab_completion')
        self.preview_chars = 50  # Default number of characters per line in preview
        self.preview_lines = 1   # Default number of lines in preview

    def run(self, args=None):
        n = 1  # Default to last message
        if args and len(args) > 0:
            try:
                n = int(args[0])
                if len(args) > 1:
                    self.preview_chars = int(args[1])
                if len(args) > 2:
                    self.preview_lines = int(args[2])
            except ValueError:
                print("Invalid arguments. Using defaults.")

        #turns = self.chat.get()[-n:]
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
            self.save_code_block(selected_block)

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
        lines = str(block).split('\n')
        preview_lines = lines[:self.preview_lines]
        preview = '\n'.join('    ' + (line[:self.preview_chars] + '...' if len(line) > self.preview_chars else line) for line in preview_lines)
        if len(lines) > self.preview_lines:
            preview += f"\n    ... ({len(lines)} lines total)"
        return preview

    def select_code_block(self, code_blocks):
        print("Multiple code blocks found. Please select one (or 'q' to quit):")
        print()
        for i, block in enumerate(code_blocks, 1):
            preview = self.create_preview(block)
            print(f"{i}.{preview}\n")

        while True:
            choice = input("Enter the number of the code block you want to save: ")
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

    def save_code_block(self, code_block):
        if code_block is None:
            print("Code saving cancelled.")
            return

        self.tc.run("file_path")  # Activate path completion mode

        while True:
            file_path = input("Enter the file path to save the code (or 'q' to quit): ")
            if file_path.lower() == 'q':
                print("Code saving cancelled.")
                break

            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                create_dir = input(f"Directory {directory} doesn't exist. Create it? (y/n): ")
                if create_dir.lower() == 'y':
                    os.makedirs(directory)
                else:
                    continue

            if os.path.exists(file_path):
                overwrite = input(f"File {file_path} already exists. Overwrite? (y/n): ")
                if overwrite.lower() != 'y':
                    continue

            try:
                with open(file_path, 'w') as f:
                    f.write(code_block)
                print(f"Code saved to {file_path}")
                break
            except Exception as e:
                print(f"Error saving file: {str(e)}")

        self.tc.run("chat")  # Reset to chat completion mode

    def set_preview_options(self, chars=None, lines=None):
        if chars is not None:
            self.preview_chars = chars
        if lines is not None:
            self.preview_lines = lines
