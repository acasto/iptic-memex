from base_classes import InteractionAction
import os
import re
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet


class ManageChatsAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.params = session.get_params()
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.chat = session.get_context('chat')

    def run(self, args=None):
        if not args:
            print("Please specify a command: save, load, or list")
            return

        command = args[0]
        if command == "save":
            include_context = args[1] == "full" if len(args) > 1 else False
            turns = args[2] if len(args) > 2 else None

            # Handle numeric argument for "last"
            if len(args) > 3 and args[2] == "last" and args[3].isdigit():
                turns = ("last", int(args[3]))
            elif len(args) > 2 and args[2] == "last":
                turns = "last"

            self.save_chat(include_context, turns)
        elif command == "load":
            self.load_chat()
        elif command == "list":
            self.list_chats()
        elif command == "export":
            export_format = args[1] if len(args) > 1 else 'pdf'
            self.export_chat(export_format)
        else:
            print(f"Unknown command: {command}")

    def save_chat(self, include_context=False, turns=None):
        self.tc.run("chat_path")
        chat_format = self.params.get('chat_format', 'md')
        default_filename = f"chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.{chat_format}"
        chats_directory = self.params.get('chats_directory', 'chats')

        # Ensure chats_directory exists
        os.makedirs(chats_directory, exist_ok=True)

        full_path = None
        while True:
            print("Enter a filename to save the chat to (q to exit)")
            print(f"(press enter for default: {default_filename})")
            filename = input("> Filename: ")
            if filename.lower() in ["exit", "quit", "q"]:
                self.tc.run("chat")  # Reset tab completion
                return
            if filename == "":
                filename = default_filename

            # Allow user to change format
            if '.' in filename:
                chat_format = filename.split('.')[-1]

            if chat_format not in ['md', 'txt', 'pdf']:
                print(f"Unsupported format: {chat_format}. Please use md, txt, or pdf.")
                continue

            if not filename.endswith(f".{chat_format}"):
                filename += f".{chat_format}"

            full_path = os.path.join(chats_directory, filename)
            if os.path.exists(full_path):
                overwrite = input(f"File {filename} already exists. Overwrite? (y/n): ")
                if overwrite.lower() != 'y':
                    continue
            break

        if full_path:
            try:
                self._save_chat_to_file(full_path, include_context, turns)
                print(f"Chat saved to {full_path}")
            except Exception as e:
                print(f"Error saving chat: {str(e)}")
        else:
            print("Failed to determine a valid file path.")

        self.tc.run("chat")  # Reset tab completion

    def export_chat(self, export_format='pdf'):
        self.tc.run("chat_path")
        chats_directory = self.params.get('chats_directory', 'chats')

        # Ensure chats_directory exists
        os.makedirs(chats_directory, exist_ok=True)

        if export_format not in ['md', 'txt', 'pdf']:
            print(f"Unsupported format: {export_format}. Using default format: pdf")
            export_format = 'pdf'

        default_filename = f"chat_export_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.{export_format}"

        while True:
            print("Enter a filename to export the chat to (q to exit)")
            print(f"(press enter for default: {default_filename})")
            filename = input("> Filename: ")
            if filename.lower() in ["exit", "quit", "q"]:
                self.tc.run("chat")  # Reset tab completion
                return
            if filename == "":
                filename = default_filename

            if not filename.endswith(f".{export_format}"):
                filename += f".{export_format}"

            full_path = os.path.join(chats_directory, filename)
            if os.path.exists(full_path):
                overwrite = input(f"File {filename} already exists. Overwrite? (y/n): ")
                if overwrite.lower() != 'y':
                    continue
            break

        if full_path:
            try:
                self._save_chat_to_file(full_path)
                print(f"Chat exported to {full_path}")
            except Exception as e:
                print(f"Error exporting chat: {str(e)}")
        else:
            print("Failed to determine a valid file path.")

        self.tc.run("chat")  # Reset tab completion

    def _save_chat_to_file(self, filename, include_context=False, turns=None):
        chat_format = os.path.splitext(filename)[1][1:]
        content = self._format_chat_content(chat_format, include_context, turns)

        if chat_format in ['md', 'txt']:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
        elif chat_format == 'pdf':
            self._save_as_pdf(filename, content)

    def _format_chat_content(self, chat_format, include_context=False, turns=None):
        content = ""

        # Handle message selection
        if isinstance(turns, tuple) and turns[0] == "last":
            # Handle "last N" case
            num_messages = turns[1]
            conversation = self.chat.get()[-num_messages:]
        elif turns == "last" or turns == "1":
            conversation = self.chat.get()[-1:]
        else:
            conversation = self.chat.get()

        if chat_format in ['md', 'txt', 'pdf']:
            content += "Chat Session\n\n"
            content += f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            content += f"Model: {self.params.get('model', 'Unknown')}\n\n"
            content += "---\n\n"

            for turn in conversation:
                role = turn['role'].capitalize()
                message = turn['message']
                turn_context = None
                if include_context and 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])
                if chat_format == 'md':
                    if turn_context:
                        content += f"## {role}\n\n```\n{turn_context}\n```\n\n\n{message}\n\n"
                    else:
                        content += f"## {role}\n\n{message}\n\n"
                else:
                    if turn_context:
                        content += f"{role}:\n\n--------\n{turn_context}\n\n--------\n\n\n{message}\n\n"
                    else:
                        content += f"{role}:\n{message}\n\n"

        return content

    @staticmethod
    def _save_as_pdf(filename, content):
        doc = SimpleDocTemplate(filename, pagesize=letter)
        styles = getSampleStyleSheet()
        flowables = []

        for line in content.split('\n'):
            if line.startswith('##'):
                flowables.append(Paragraph(line[3:], styles['Heading2']))
            elif line.strip() == '---':
                flowables.append(Spacer(1, 12))
            else:
                flowables.append(Paragraph(line, styles['BodyText']))
            flowables.append(Spacer(1, 6))

        doc.build(flowables)

    def load_chat(self):
        self.tc.run("chat_path")
        chats_directory = self.params.get('chats_directory', 'chats')

        full_path = None
        while True:
            print("Enter a filename to load or type 'list' to see available chats (q to exit)")
            filename = input("> Load Chat: ")
            if filename.lower() in ["exit", "quit", "q"]:
                self.tc.run("chat")  # Reset tab completion
                return
            if filename.lower() == "list":
                self.list_chats()
                continue

            full_path = os.path.join(chats_directory, filename)
            if os.path.isfile(full_path):
                break
            else:
                print(f"File not found: {filename}")

        if full_path:
            try:
                self._load_chat_from_file(full_path)
                print(f"Chat loaded from {full_path}")
                self.session.get_action('reprint_chat').run()

            except Exception as e:
                print(f"Error loading chat: {str(e)}")
        else:
            print("Failed to determine a valid file path.")

        self.tc.run("chat")  # Reset tab completion

    def _load_chat_from_file(self, filename):
        chat_format = os.path.splitext(filename)[1][1:]
        if chat_format in ['md', 'txt']:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
            messages = self._parse_chat_content(content, chat_format)
            self.chat.clear()
            for message in messages:
                self.chat.add(message['content'], message['role'])
        elif chat_format == 'pdf':
            print("Loading from PDF is not supported. Please use markdown or text files.")
        else:
            print(f"Unsupported format for loading: {chat_format}")

    # noinspection PyTypeChecker
    @staticmethod
    def _parse_chat_content(content, chat_format):
        messages = []
        if chat_format == 'md':
            sections = re.split(r'\n(?=## (?:User|Assistant))', content)
            for section in sections:
                if section.strip():
                    match = re.match(r'## (User|Assistant)\n(.*)', section, re.DOTALL)
                    if match:
                        role, message = match.groups()
                        messages.append({'role': role.lower(), 'content': message.strip()})
        else:  # txt format
            pattern = re.compile(r'^(User|Assistant):\n(.*?)(?=\n(?:User|Assistant):|$)', re.DOTALL | re.MULTILINE)
            for match in pattern.finditer(content):
                role, message = match.groups()
                messages.append({'role': role.lower(), 'content': message.strip()})

        return messages

    def list_chats(self):
        chats_directory = self.params.get('chats_directory', 'chats')
        if not os.path.isdir(chats_directory):
            print(f"Chats directory not found: {chats_directory}")
            return

        print(f"Chats in {chats_directory}:")
        chat_files = [f for f in os.listdir(chats_directory) if f.endswith(('.md', '.txt', '.pdf'))]
        if not chat_files:
            print("No chat files found.")
        else:
            for file in chat_files:
                print(f"- {file}")
