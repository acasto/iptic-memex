from base_classes import StepwiseAction, Completed
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple, Union
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet


ChatTurns = Union[None, str, Tuple[str, Optional[int]]]


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
        self._state: Dict[str, Any] = {}

    # Stepwise entrypoints
    def start(self, args=None, content=None) -> Completed:
        if isinstance(args, dict) and args.get('mode'):
            return Completed(self._handle_headless(dict(args)))

        cli_spec = self._parse_cli_args(args)
        if cli_spec:
            mode = cli_spec['mode']
            if mode == 'list':
                items = self._list_chats()
                try:
                    if not items:
                        self.session.ui.emit('status', {'message': 'No chat files found.'})
                    else:
                        self.session.ui.emit('status', {'message': f"Found {len(items)} chat file(s)."})
                except Exception:
                    pass
                return Completed({'ok': True, 'mode': 'list', 'chats': items})
            if mode == 'load':
                filename = cli_spec.get('filename')
                if filename:
                    return self._complete_load(filename)
                # Fall back to interactive prompt when filename missing
                return self._start_load_interactive()
            if mode == 'save':
                if cli_spec.get('headless'):
                    payload = {
                        'mode': 'save',
                        'filename': cli_spec.get('filename'),
                        'include_context': cli_spec.get('include_context', False),
                        'turns': cli_spec.get('turns'),
                        'overwrite': cli_spec.get('overwrite', False),
                    }
                    return Completed(self._handle_headless(payload))
                return self._begin_save_flow(cli_spec)

        choices = ['List chats', 'Save chat', 'Load chat', 'Quit']
        sel = self.session.ui.ask_choice('Manage chats:', choices, default=choices[0])
        return self._dispatch_choice(sel)

    def resume(self, state_token: str, response) -> Completed:
        if isinstance(response, dict) and 'response' in response:
            response = response['response']

        phase = self._state.get('phase')
        if phase == 'await_filename':
            return self._handle_filename_response(response)
        if phase == 'await_include_context':
            return self._handle_include_context_response(response)
        if phase == 'await_overwrite':
            return self._handle_overwrite_response(response)
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
            params = self.session.get_params() or {}
            chat_format = params.get('chat_format', 'md')
            return self._begin_save_flow({'mode': 'save', 'chat_format': chat_format})
        if sel == 'Load chat':
            self._state = {'phase': 'load_wait_filename'}
            params = self.session.get_params()
            chats_directory = params.get('chats_directory', 'chats')
            fname = self.session.ui.ask_text(f'Filename to load (from {chats_directory}):')
            if fname is None:
                return Completed({'ok': True})
            return self._complete_load(str(fname or ''))
        return Completed({'ok': True, 'quit': True})

    def _begin_save_flow(self, spec: Dict[str, Any]) -> Completed:
        params = self.session.get_params() or {}
        chat_format = spec.get('chat_format') or params.get('chat_format', 'md') or 'md'
        chats_directory = params.get('chats_directory', 'chats')

        self._state = {
            'mode': 'save',
            'phase': None,
            'chat_format': chat_format,
            'chats_directory': chats_directory,
            'filename': spec.get('filename'),
            'include_context': spec.get('include_context'),
            'turns': spec.get('turns'),
            'overwrite': bool(spec.get('overwrite', False)),
        }

        if self._state.get('turns') is not None and self._state.get('filename') is None:
            # When saving partial transcripts (e.g., /save last) keep legacy headless flow
            payload = {
                'mode': 'save',
                'turns': self._state['turns'],
                'include_context': bool(self._state.get('include_context', False)),
                'overwrite': bool(self._state.get('overwrite', False)),
            }
            return Completed(self._handle_headless(payload))

        return self._continue_save_flow()

    def _continue_save_flow(self) -> Completed:
        filename = self._state.get('filename')
        if not filename:
            return self._prompt_for_filename()

        if not self._state.get('full_path'):
            normalized_name, chat_format, full_path = self._normalize_filename(filename)
            self._state['filename'] = normalized_name
            self._state['chat_format'] = chat_format
            self._state['full_path'] = full_path

        if self._state.get('include_context') is None:
            include_default = bool(self._state.get('turns'))
            return self._prompt_for_include_context(default=include_default)

        if not self._state.get('overwrite') and os.path.exists(self._state['full_path']):
            return self._prompt_for_overwrite()

        return self._finalize_save()

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
                        ctx_items = turn['context'] or []
                        # Filter out synthetic turn_status contexts so they are
                        # not persisted in saved chat transcripts.
                        filtered_ctx = []
                        for c in ctx_items:
                            try:
                                ctx_obj = c.get('context')
                                meta = ctx_obj.get() if ctx_obj else None
                                if isinstance(meta, dict) and meta.get('name') == 'turn_status':
                                    continue
                            except Exception:
                                pass
                            filtered_ctx.append(c)
                        if filtered_ctx:
                            turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(filtered_ctx)
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

    # ----- save flow helpers -------------------------------------------------

    def _parse_cli_args(self, args: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(args, (list, tuple)) or not args:
            return None
        tokens: Iterable[Any] = [a for a in args if a is not None]
        tokens = list(tokens)
        if not tokens:
            return None
        cmd = str(tokens[0]).strip().lower()
        if cmd == 'list':
            return {'mode': 'list'}
        if cmd == 'load':
            filename = str(tokens[1]).strip() if len(tokens) > 1 else None
            return {'mode': 'load', 'filename': filename}
        if cmd != 'save':
            return None

        include_context: Optional[bool] = None
        turns: ChatTurns = None
        filename: Optional[str] = None
        overwrite = False

        remainder = list(tokens[1:])
        idx = 0
        while idx < len(remainder):
            token = remainder[idx]
            if isinstance(token, bool) and include_context is None:
                include_context = token
                idx += 1
                continue
            token_str = str(token).strip()
            token_lower = token_str.lower()
            if token_lower == 'full':
                include_context = True
                idx += 1
                continue
            if token_lower == 'overwrite':
                overwrite = True
                idx += 1
                continue
            if token_lower in {'true', 'false'} and include_context is None:
                include_context = (token_lower == 'true')
                idx += 1
                continue
            if token_lower == 'last':
                count = None
                if idx + 1 < len(remainder):
                    next_tok = remainder[idx + 1]
                    if self._is_int_like(next_tok):
                        count = int(next_tok)
                        idx += 1
                turns = ('last', count) if count else 'last'
                idx += 1
                continue
            if self._is_int_like(token) and turns and isinstance(turns, tuple) and turns[1] is None:
                turns = (turns[0], int(token))
                idx += 1
                continue
            if filename is None and token_str:
                filename = token_str
            idx += 1

        spec: Dict[str, Any] = {
            'mode': 'save',
            'filename': filename,
            'include_context': include_context,
            'turns': turns,
            'overwrite': overwrite,
        }

        if turns is not None and filename is None:
            spec['headless'] = True

        return spec

    def _default_filename(self, chat_format: str) -> str:
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        return f"chat_{ts}.{chat_format}"

    def _prompt_for_filename(self) -> Completed:
        self._state['phase'] = 'await_filename'
        chat_format = self._state.get('chat_format', 'md')
        default_name = self._default_filename(chat_format)
        response = self.session.ui.ask_text('Filename to save:', default=default_name)
        return self._handle_filename_response(response)

    def _handle_filename_response(self, response: Any) -> Completed:
        self._state['phase'] = None
        if response is None:
            return Completed({'ok': False, 'mode': 'save', 'cancelled': True})
        filename = str(response).strip()
        if not filename:
            # Re-prompt for filename when empty
            return self._prompt_for_filename()

        self._state['filename'] = filename
        self._state.pop('full_path', None)
        return self._continue_save_flow()

    def _prompt_for_include_context(self, *, default: bool = False) -> Completed:
        self._state['phase'] = 'await_include_context'
        response = self.session.ui.ask_bool('Include context in save?', default=default)
        return self._handle_include_context_response(response)

    def _handle_include_context_response(self, response: Any) -> Completed:
        self._state['phase'] = None
        if response is None:
            return Completed({'ok': False, 'mode': 'save', 'cancelled': True})
        self._state['include_context'] = bool(response)
        return self._continue_save_flow()

    def _prompt_for_overwrite(self) -> Completed:
        self._state['phase'] = 'await_overwrite'
        display_name = self._state.get('filename')
        prompt = f"File '{display_name}' already exists. Overwrite?"
        response = self.session.ui.ask_bool(prompt, default=False)
        return self._handle_overwrite_response(response)

    def _handle_overwrite_response(self, response: Any) -> Completed:
        self._state['phase'] = None
        if response is None:
            return Completed({'ok': False, 'mode': 'save', 'cancelled': True})
        if not bool(response):
            # User declined overwrite; ask for a new filename
            self._state['filename'] = None
            self._state.pop('full_path', None)
            return self._prompt_for_filename()
        self._state['overwrite'] = True
        return self._continue_save_flow()

    def _finalize_save(self) -> Completed:
        try:
            include_context = bool(self._state.get('include_context', False))
            turns = self._state.get('turns')
            full_path = self._state.get('full_path')
            if not full_path:
                normalized_name, chat_format, full_path = self._normalize_filename(self._state['filename'])
                self._state['filename'] = normalized_name
                self._state['chat_format'] = chat_format
                self._state['full_path'] = full_path
            os.makedirs(os.path.dirname(full_path) or '.', exist_ok=True)
            self._save_chat_to_file(full_path, include_context, turns)
            try:
                self.session.ui.emit('status', {'message': f"Chat saved to {full_path}"})
            except Exception:
                pass
            payload = {
                'ok': True,
                'mode': 'save',
                'saved': True,
                'filename': self._state.get('filename'),
                'path': full_path,
                'include_context': include_context,
            }
            self._state = {}
            return Completed(payload)
        except Exception as exc:
            return Completed({'ok': False, 'mode': 'save', 'error': str(exc)})

    def _normalize_filename(self, filename: str) -> Tuple[str, str, str]:
        params = self.session.get_params() or {}
        chats_directory = self._state.get('chats_directory') or params.get('chats_directory', 'chats')
        chat_format = self._state.get('chat_format', params.get('chat_format', 'md') or 'md')
        name = filename.strip()
        expanded = os.path.expanduser(name)
        if os.path.isabs(expanded):
            base_dir = os.path.dirname(expanded)
            basename = os.path.basename(expanded)
        else:
            base_dir = chats_directory
            basename = name
        provided_ext = None
        if '.' in basename:
            provided_ext = basename.rsplit('.', 1)[-1].lower()
        allowed_exts = {'md', 'txt', 'pdf'}
        if provided_ext in allowed_exts:
            chat_format = provided_ext
        if not basename.lower().endswith(f'.{chat_format}'):
            basename = f"{basename}.{chat_format}"
        if os.path.isabs(expanded):
            full_path = os.path.normpath(os.path.join(base_dir, basename))
        else:
            full_path = os.path.normpath(os.path.join(os.path.expanduser(base_dir), basename))
        return basename, chat_format, full_path

    def _is_int_like(self, value: Any) -> bool:
        try:
            int(value)
            return True
        except (TypeError, ValueError):
            return False

    def _start_load_interactive(self) -> Completed:
        self._state = {'phase': 'load_wait_filename'}
        params = self.session.get_params()
        chats_directory = params.get('chats_directory', 'chats')
        fname = self.session.ui.ask_text(f'Filename to load (from {chats_directory}):')
        if fname is None:
            return Completed({'ok': True})
        return self._complete_load(str(fname or ''))
