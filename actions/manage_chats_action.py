from base_classes import StepwiseAction, Completed
import os
import re
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet


class ManageChatsAction(StepwiseAction):
    """Manage saving, listing, loading chats across CLI/Web/TUI via Stepwise.

    - Headless path (Web/TUI friendly): when args is a dict with mode in
      {'list','save','load'}, execute without prompts and return a structured
      payload compatible with the previous web API.
    - Interactive path: use ui.ask_* prompts. In CLI these block; in Web/TUI
      they raise InteractionNeeded and the server will resume. Minimal state is
      kept on the instance to support multi-step prompts.
    """

    def __init__(self, session):
        self.session = session
        self.chat = session.get_context('chat')
        self._state: dict = {}

    # Stepwise entrypoints
    def start(self, args=None, content=None) -> Completed:
        if isinstance(args, dict) and (args.get('mode')):
            return Completed(self._handle_headless(args))

        # Support legacy list-args shortcuts from user commands (e.g., ['list'], ['save','full'], ['save','last', '3'])
        if isinstance(args, (list, tuple)) and args:
            try:
                cmd = str(args[0] or '').strip().lower()
            except Exception:
                cmd = ''
            if cmd == 'list':
                # Emit list to UI for chat mode and return structured payload
                try:
                    self.list_chats()
                except Exception:
                    pass
                return Completed({'ok': True, 'mode': 'list', 'chats': self._list_chats()})
            if cmd == 'save':
                include_context = False
                turns = None
                try:
                    if len(args) > 1 and str(args[1]).lower() == 'full':
                        include_context = True
                    if len(args) > 1 and str(args[1]).lower() == 'last':
                        turns = 'last'
                    if len(args) > 3 and str(args[2]).lower() == 'last' and str(args[3]).isdigit():
                        turns = ('last', int(args[3]))
                except Exception:
                    pass
                return Completed(self._handle_headless({'mode': 'save', 'include_context': include_context, 'turns': turns}))

        choices = ['List chats', 'Save chat', 'Load chat', 'Quit']
        sel = self.session.ui.ask_choice('Manage chats:', choices, default=choices[0])
        return self._dispatch_choice(sel)

    def resume(self, state_token: str, response) -> Completed:
        if isinstance(response, dict) and 'response' in response:
            response = response['response']

        phase = self._state.get('phase')
        if phase == 'save_wait_filename':
            self._state['filename'] = str(response or '')
            self._state['phase'] = 'save_wait_include'
            inc = self.session.ui.ask_bool('Include context in save?', default=False)
            if inc is None:
                return Completed({'ok': True})
            return self._complete_save(include_context=bool(inc))
        if phase == 'save_wait_include':
            return self._complete_save(include_context=bool(response))
        if phase == 'load_wait_filename':
            filename = str(response or '')
            return self._complete_load(filename)

        return self._dispatch_choice(str(response or ''))

    # Interactive helpers
    def _dispatch_choice(self, sel: str) -> Completed:
        sel = str(sel)
        if sel == 'List chats':
            items = self._list_chats()
            try:
                if not items:
                    self.session.ui.emit('status', {'message': 'No chat files found.'})
                else:
                    self.session.ui.emit('status', {'message': f"Found {len(items)} chat file(s)."})
            except Exception:
                pass
            return Completed({'ok': True, 'mode': 'list', 'chats': items})
        if sel == 'Save chat':
            self._state = {'phase': 'save_wait_filename'}
            params = self.session.get_params()
            chat_format = params.get('chat_format', 'md')
            default_filename = f"chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.{chat_format}"
            fname = self.session.ui.ask_text('Filename to save:', default=default_filename)
            if fname is None:
                return Completed({'ok': True})
            return self.resume('__implicit__', fname)
        if sel == 'Load chat':
            self._state = {'phase': 'load_wait_filename'}
            params = self.session.get_params()
            chats_directory = params.get('chats_directory', 'chats')
            fname = self.session.ui.ask_text(f'Filename to load (from {chats_directory}):')
            if fname is None:
                return Completed({'ok': True})
            return self._complete_load(str(fname or ''))
        return Completed({'ok': True, 'quit': True})

    def _complete_save(self, *, include_context: bool) -> Completed:
        params = self.session.get_params()
        chat_format = params.get('chat_format', 'md')
        filename = self._state.get('filename')
        if not filename:
            filename = f"chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.{chat_format}"
        if '.' in str(filename):
            provided_ext = str(filename).rsplit('.', 1)[-1].lower()
            if provided_ext in {'md', 'txt', 'pdf'}:
                chat_format = provided_ext
        if not str(filename).lower().endswith(f'.{chat_format}'):
            filename = f"{filename}.{chat_format}"
        chats_directory = params.get('chats_directory', 'chats')
        os.makedirs(chats_directory, exist_ok=True)
        full_path = os.path.join(chats_directory, filename)
        try:
            self._save_chat_to_file(full_path, include_context, turns=None)
            try:
                self.session.ui.emit('status', {'message': f"Chat saved to {full_path}"})
            except Exception:
                pass
            return Completed({'ok': True, 'mode': 'save', 'saved': True, 'filename': filename, 'path': full_path})
        except Exception as e:
            return Completed({'ok': False, 'mode': 'save', 'error': str(e)})

    def _complete_load(self, filename: str) -> Completed:
        params = self.session.get_params()
        chats_directory = params.get('chats_directory', 'chats')
        if not filename:
            return Completed({'ok': False, 'mode': 'load', 'error': 'missing_file'})
        full_path = filename if os.path.isabs(filename) else os.path.join(chats_directory, str(filename))
        if not os.path.isfile(full_path):
            return Completed({'ok': False, 'mode': 'load', 'error': 'not_found', 'filename': filename})
        try:
            self._load_chat_from_file(full_path)
            msgs = []
            turns = self.chat.get('all')
            for t in turns:
                if t['role'] in ('user', 'assistant'):
                    msgs.append({'role': t['role'], 'text': t['message'] or ''})
            try:
                self.session.ui.emit('status', {'message': f"Chat loaded from {full_path}"})
            except Exception:
                pass
            return Completed({'ok': True, 'mode': 'load', 'loaded': True, 'filename': os.path.basename(full_path), 'path': full_path, 'messages': msgs})
        except Exception as e:
            return Completed({'ok': False, 'mode': 'load', 'error': str(e)})

    # Headless dict path (no prompts)
    def _handle_headless(self, args: dict) -> dict:
        mode = str(args.get('mode') or '').strip().lower()
        if mode == 'save':
            params = self.session.get_params()
            chats_directory = params.get('chats_directory', 'chats')
            os.makedirs(chats_directory, exist_ok=True)
            include_context = bool(args.get('include_context', False))
            turns = args.get('turns')
            chat_format = params.get('chat_format', 'md')
            filename = args.get('filename') or args.get('file')
            if not filename:
                filename = f"chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.{chat_format}"
            allowed_exts = {'md', 'txt', 'pdf'}
            provided_ext = None
            try:
                if '.' in str(filename):
                    provided_ext = str(filename).rsplit('.', 1)[-1].lower()
            except Exception:
                provided_ext = None
            if provided_ext in allowed_exts:
                chat_format = provided_ext
            if not str(filename).lower().endswith(f".{chat_format}"):
                filename = f"{filename}.{chat_format}"
            full_path = os.path.join(chats_directory, filename)
            if os.path.exists(full_path) and not bool(args.get('overwrite')):
                return {'ok': False, 'error': 'exists', 'exists': True, 'filename': filename, 'path': full_path}
            try:
                self._save_chat_to_file(full_path, include_context, turns)
                try:
                    self.session.ui.emit('status', {'message': f'Chat saved to {full_path}'})
                except Exception:
                    pass
                return {'ok': True, 'saved': True, 'filename': filename, 'path': full_path}
            except Exception as e:
                return {'ok': False, 'error': str(e)}
        if mode == 'list':
            return {'ok': True, 'chats': self._list_chats()}
        if mode == 'load':
            params = self.session.get_params()
            chats_directory = params.get('chats_directory', 'chats')
            file_arg = args.get('file') or args.get('filename')
            if not file_arg:
                return {'ok': False, 'error': 'missing_file'}
            full_path = file_arg if os.path.isabs(file_arg) else os.path.join(chats_directory, str(file_arg))
            if not os.path.isfile(full_path):
                return {'ok': False, 'error': 'not_found', 'filename': file_arg}
            try:
                self._load_chat_from_file(full_path)
                msgs = []
                turns = self.chat.get('all')
                for t in turns:
                    if t['role'] in ('user', 'assistant'):
                        msgs.append({'role': t['role'], 'text': t['message'] or ''})
                try:
                    self.session.ui.emit('status', {'message': f'Chat loaded from {full_path}'})
                except Exception:
                    pass
                return {'ok': True, 'loaded': True, 'filename': os.path.basename(full_path), 'path': full_path, 'messages': msgs}
            except Exception as e:
                return {'ok': False, 'error': str(e)}
        return {'ok': False, 'error': 'invalid_mode'}

    # File helpers
    def _save_chat_to_file(self, filename, include_context=False, turns=None):
        chat_format = os.path.splitext(filename)[1][1:]
        content = self._format_chat_content(chat_format, include_context, turns)
        if chat_format in ['md', 'txt']:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
        elif chat_format == 'pdf':
            self._save_as_pdf(filename, content)

    def _format_chat_content(self, chat_format, include_context=False, turns=None):
        params = self.session.get_params()
        content = ""
        # Handle message selection
        if isinstance(turns, tuple) and turns[0] == "last":
            num_messages = turns[1]
            conversation = self.chat.get()[-num_messages:]
        elif turns == "last" or turns == "1":
            conversation = self.chat.get()[-1:]
        else:
            conversation = self.chat.get()

        if chat_format in ['md', 'txt', 'pdf']:
            content += "Chat Session\n\n"
            content += f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            content += f"Model: {params.get('model', 'Unknown')}\n\n"
            content += "---\n\n"
            for turn in conversation:
                role = turn['role'].capitalize()
                message = turn['message']
                turn_context = None
                if include_context and 'context' in turn and turn['context']:
                    try:
                        turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])
                    except Exception:
                        turn_context = None
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
            try:
                self.session.ui.emit('warning', {'message': 'Loading from PDF is not supported. Please use markdown or text files.'})
            except Exception:
                pass
        else:
            try:
                self.session.ui.emit('warning', {'message': f'Unsupported format for loading: {chat_format}'})
            except Exception:
                pass

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

    # Helper for web list mode
    def _list_chats(self):
        params = self.session.get_params()
        chats_directory = params.get('chats_directory', 'chats')
        if not os.path.isdir(chats_directory):
            return []
        items = []
        for f in os.listdir(chats_directory):
            if f.endswith(('.md', '.txt', '.pdf')):
                p = os.path.join(chats_directory, f)
                try:
                    mtime = os.path.getmtime(p)
                except Exception:
                    mtime = 0.0
                items.append({'name': f, 'filename': f, 'path': p, 'mtime': mtime})
        try:
            items.sort(key=lambda x: (x.get('mtime') or 0.0, x.get('name') or ''), reverse=True)
        except Exception:
            pass
        return items
