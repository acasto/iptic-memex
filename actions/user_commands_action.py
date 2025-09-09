from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional

from base_classes import InteractionAction


def _split_slash(line: str) -> List[str]:
    """Split a slash-command line like '/load file "foo bar.txt" --opt=1'.
    Returns tokens without the leading slash. Tolerates partial quotes.
    """
    s = (line or '').lstrip()
    if not s.startswith('/'):
        return []
    try:
        return shlex.split(s[1:], posix=True)
    except Exception:
        # Fall back to naive split if a partial quote confuses shlex
        return s[1:].strip().split()


class UserCommandsAction(InteractionAction):
    """
    Slash command interface for chat, web, and TUI.
    - Explicit '/cmd [sub] [args]' parsing; no scanning of free-form text.
    - Single registry as the source of truth for dispatch and tab completion.
    - Subcommand-level gating via Action.can_run(session).
    """

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.chat = session.get_context('chat')

        # Build the command registry and apply gating
        self.registry: Dict[str, Dict[str, Any]] = self._build_registry()
        self._merge_user_commands()
        self._gate_unavailable()

    def _evaluate_gate(self, cmd: str, sub: str, argv: List[str], handler: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Evaluate per-handler gate, if any.
        Returns (ok, reason).
        """
        gate = handler.get('gate') if isinstance(handler, dict) else None
        if not gate:
            return True, None
        # Action-level can_run with arguments
        if isinstance(gate, dict) and gate.get('type') == 'action_can_run':
            aname = gate.get('name')
            aargs = list(gate.get('args') or [])
            try:
                class_name = ''.join(w.capitalize() for w in aname.split('_')) + 'Action'
                mod = __import__(f'actions.{aname}_action', fromlist=[class_name])
                action_class = getattr(mod, class_name)
                can_run = getattr(action_class, 'can_run', None)
                if callable(can_run):
                    try:
                        res = can_run(self.session, *aargs)
                    except TypeError:
                        # Fallback to older signature
                        res = can_run(self.session)
                    # Normalize return
                    if isinstance(res, tuple) and len(res) >= 1:
                        ok = bool(res[0])
                        reason = str(res[1]) if len(res) > 1 and isinstance(res[1], str) else None
                        return ok, reason
                    return (bool(res), None)
            except Exception:
                return True, None
        # Callable gates can be added later
        return True, None

    def _invoke_action_completion(self, spec: Dict[str, Any], prefix: str) -> List[str]:
        """Invoke an action class method to produce completion options.
        Expects spec like {'type':'action_method','name':<action>,'method':<method>,'args':[...]}.
        """
        try:
            aname = spec.get('name')
            mname = spec.get('method')
            aargs = list(spec.get('args') or [])
            class_name = ''.join(w.capitalize() for w in aname.split('_')) + 'Action'
            mod = __import__(f'actions.{aname}_action', fromlist=[class_name])
            action_class = getattr(mod, class_name)
            fn = getattr(action_class, mname)
            # Prefer class/static method taking (session, *args, prefix)
            try:
                out = fn(self.session, *aargs, prefix)
            except TypeError:
                # Try instance method on the action
                inst = self.session.get_action(aname)
                fn2 = getattr(inst, mname, None)
                if callable(fn2):
                    out = fn2(*aargs, prefix)
                else:
                    out = []
            return list(out or [])
        except Exception:
            return []

    # ----- Registry construction -------------------------------------------------
    def _complete_file_paths(self, prefix: str) -> List[str]:
        import os
        from pathlib import Path
        text = prefix or ''
        if text.startswith('~'):
            text = os.path.expanduser(text)
        dirname = os.path.dirname(text) or '.'
        try:
            entries = [str(Path(dirname) / x) for x in os.listdir(dirname)]
        except Exception:
            return []
        opts = [e + ('/' if os.path.isdir(e) else '') for e in entries if e.startswith(text)]
        return sorted(opts)

    def _complete_chat_paths(self, prefix: str) -> List[str]:
        import os
        from pathlib import Path
        text = prefix or ''
        if text.startswith('~'):
            text = os.path.expanduser(text)
        chats_dir = self.session.get_params().get('chats_directory', 'chats')
        chats_dir = os.path.expanduser(chats_dir)
        base_dir = os.path.dirname(text) or chats_dir
        try:
            entries = [str(Path(base_dir) / x) for x in os.listdir(base_dir)]
        except Exception:
            return []
        exts = ('.md', '.txt', '.pdf')
        opts = [e for e in entries if e.startswith(text) and (os.path.isdir(e) or any(e.endswith(ext) for ext in exts))]
        return sorted(opts)

    def _complete_models(self, prefix: str) -> List[str]:
        try:
            return sorted([m for m in self.session.list_models().keys() if m.startswith(prefix or '')])
        except Exception:
            return []

    def _complete_options(self, prefix: str) -> List[str]:
        try:
            return sorted([k for k in self.session.get_params().keys() if k.startswith(prefix or '')])
        except Exception:
            return []

    def _complete_tools(self, prefix: str) -> List[str]:
        try:
            return sorted([k for k in self.session.get_tools().keys() if k.startswith(prefix or '')])
        except Exception:
            return []

    def _build_registry(self) -> Dict[str, Dict[str, Any]]:
        """Define the canonical command registry."""
        reg: Dict[str, Dict[str, Any]] = {
            'help': {
                'help': 'Show available commands',
                'sub': {
                    '': {'type': 'builtin', 'name': 'handle_help'},
                },
            },
            'quit': {
                'help': 'Quit the chat',
                'aliases': ('exit',),
                'sub': {
                    '': {'type': 'builtin', 'name': 'handle_quit'},
                },
            },
            'load': {
                'help': 'Load resources into context',
                'sub': {
                    'project':   {'type': 'action', 'name': 'load_project'},
                    'file':      {'type': 'action', 'name': 'load_file', 'complete': self._complete_file_paths},
                    'raw':       {'type': 'action', 'name': 'load_raw'},
                    'rag':       {'type': 'action', 'name': 'load_rag'},
                    'code':      {'type': 'action', 'name': 'fetch_code_snippet'},
                    'multiline': {'type': 'action', 'name': 'load_multiline'},
                    'web':       {'type': 'action', 'name': 'fetch_from_web'},
                    'chat':      {'type': 'action', 'name': 'manage_chats', 'args': ['load'], 'complete': self._complete_chat_paths},
                    # MCP helpers via load group for convenience
                    'mcp':          {'type': 'action', 'name': 'mcp_connect'},
                    'mcp-demo':     {'type': 'action', 'name': 'mcp_demo'},
                    'mcp-resource': {'type': 'action', 'name': 'mcp_fetch_resource'},
                },
            },
            'clear': {
                'help': 'Clear chat, context, or screen',
                'sub': {
                    'context': {'type': 'action', 'name': 'clear_context'},
                    'chat':    {'type': 'action', 'name': 'clear_chat', 'args': ['chat']},
                    'last':    {'type': 'action', 'name': 'clear_chat', 'args': ['last']},
                    'first':   {'type': 'action', 'name': 'clear_chat', 'args': ['first']},
                    'screen':  {'type': 'action', 'name': 'clear_chat', 'args': ['screen']},
                },
            },
            'reprint': {
                'help': 'Reprint conversation',
                'sub': {
                    '':        {'type': 'action', 'name': 'reprint_chat'},
                    'all':     {'type': 'action', 'name': 'reprint_chat', 'args': ['all']},
                    'raw':     {'type': 'action', 'name': 'reprint_chat', 'args': ['raw']},
                    'raw-all': {'type': 'action', 'name': 'reprint_chat', 'args': ['raw', 'all']},
                },
            },
            'show': {
                'help': 'Show settings, models, usage, cost, tools, contexts, chats',
                'sub': {
                    'settings':       {'type': 'action', 'name': 'show', 'args': ['settings']},
                    'tool-settings':  {'type': 'action', 'name': 'show', 'args': ['tool-settings']},
                    'models':         {'type': 'action', 'name': 'show', 'args': ['models']},
                    'messages':       {'type': 'action', 'name': 'show', 'args': ['messages']},
                    'usage':          {'type': 'action', 'name': 'show', 'args': ['usage']},
                    'cost':           {'type': 'action', 'name': 'show', 'args': ['cost']},
                    'contexts':       {'type': 'action', 'name': 'show', 'args': ['contexts']},
                    'tools':          {'type': 'action', 'name': 'show', 'args': ['tools']},
                    'chats':          {'type': 'action', 'name': 'manage_chats', 'args': ['list']},
                },
            },
            'set': {
                'help': 'Set options or model',
                'sub': {
                    'option':       {'type': 'action', 'name': 'set_option', 'complete': self._complete_options},
                    'option-tools': {'type': 'action', 'name': 'set_option', 'args': ['tools'], 'complete': self._complete_tools},
                    'model':        {'type': 'action', 'name': 'set_model', 'complete': self._complete_models},
                    # Set web search model via action method (AssistantWebsearchToolAction.set_search_model)
                    'search':       {'type': 'action', 'name': 'assistant_websearch_tool', 'method': 'set_search_model', 'complete': self._complete_models},
                    # Shortcuts
                    'stream': {
                        'type': 'action', 'name': 'setting_shortcuts', 'args': ['stream'],
                        'gate': {'type': 'action_can_run', 'name': 'setting_shortcuts', 'args': ['stream']},
                        'complete': {'type': 'action_method', 'name': 'setting_shortcuts', 'method': 'complete_values', 'args': ['stream']},
                    },
                    'reasoning': {
                        'type': 'action', 'name': 'setting_shortcuts', 'args': ['reasoning'],
                        'gate': {'type': 'action_can_run', 'name': 'setting_shortcuts', 'args': ['reasoning']},
                        'complete': {'type': 'action_method', 'name': 'setting_shortcuts', 'method': 'complete_values', 'args': ['reasoning']},
                    },
                    'temperature': {
                        'type': 'action', 'name': 'setting_shortcuts', 'args': ['temperature'],
                        'gate': {'type': 'action_can_run', 'name': 'setting_shortcuts', 'args': ['temperature']},
                        'complete': {'type': 'action_method', 'name': 'setting_shortcuts', 'method': 'complete_values', 'args': ['temperature']},
                    },
                    'top_p': {
                        'type': 'action', 'name': 'setting_shortcuts', 'args': ['top_p'],
                        'gate': {'type': 'action_can_run', 'name': 'setting_shortcuts', 'args': ['top_p']},
                        'complete': {'type': 'action_method', 'name': 'setting_shortcuts', 'method': 'complete_values', 'args': ['top_p']},
                    },
                },
            },
            'save': {
                'help': 'Save chat or code',
                'sub': {
                    'chat': {'type': 'action', 'name': 'manage_chats', 'args': ['save']},
                    'last': {'type': 'action', 'name': 'manage_chats', 'args': ['save', False, 'last']},
                    'full': {'type': 'action', 'name': 'manage_chats', 'args': ['save', 'full']},
                    'code': {'type': 'action', 'name': 'save_code'},
                },
            },
            'export': {
                'help': 'Export chat',
                'sub': {
                    'chat': {'type': 'action', 'name': 'manage_chats', 'args': ['export']},
                },
            },
            'mcp': {
                'help': 'MCP helpers',
                'sub': {
                    '':           {'type': 'action', 'name': 'mcp'},
                    'tools':      {'type': 'action', 'name': 'mcp', 'args': ['tools']},
                    'resources':  {'type': 'action', 'name': 'mcp', 'args': ['resources']},
                    'provider':   {'type': 'action', 'name': 'mcp', 'args': ['provider-mcp']},
                    'status':     {'type': 'action', 'name': 'mcp', 'args': ['status']},
                    'register-tools':   {'type': 'action', 'name': 'mcp_register_tools'},
                    'unregister-tools': {'type': 'action', 'name': 'mcp_unregister_tools'},
                    'discover':   {'type': 'action', 'name': 'mcp_discover'},
                },
            },
            'run': {
                'help': 'Run code or shell',
                'sub': {
                    'code':    {'type': 'action', 'name': 'run_code'},
                    'command': {'type': 'action', 'name': 'run_command'},
                },
            },
            'rag': {
                'help': 'RAG maintenance',
                'sub': {
                    'update': {'type': 'action', 'name': 'rag_update'},
                    'status': {'type': 'action', 'name': 'rag_status'},
                },
            },
        }

        # Apply aliases (e.g., /exit → /quit)
        for name, cfg in list(reg.items()):
            for alias in cfg.get('aliases', ()):
                reg[alias] = cfg
        return reg

    def _merge_user_commands(self) -> None:
        """Preserve custom user command injection by mapping "verb noun" keys into the registry."""
        try:
            user_commands = self.session.get_action('register_user_commands')
        except Exception:
            user_commands = None
        if not user_commands:
            return
        try:
            new_commands = user_commands.run()
        except Exception:
            new_commands = None
        if not (isinstance(new_commands, dict) and new_commands):
            return
        for legacy_key, cfg in new_commands.items():
            parts = str(legacy_key or '').strip().split()
            if not parts:
                continue
            cmd = parts[0]
            sub = '-'.join(parts[1:]) if len(parts) > 1 else ''
            node = self.registry.setdefault(cmd, {'help': '', 'sub': {}})
            subs = node.setdefault('sub', {})
            if isinstance(cfg, dict):
                subs[sub] = cfg

    def _gate_unavailable(self) -> None:
        """Remove subcommands whose underlying Action.can_run(session) == False."""
        for cmd_name, cmd_info in list(self.registry.items()):
            subs = dict(cmd_info.get('sub', {}))
            for sub_name, sub_info in list(subs.items()):
                if sub_info.get('type') != 'action':
                    continue
                try:
                    action_name = sub_info['name']
                    class_name = ''.join(w.capitalize() for w in action_name.split('_')) + 'Action'
                    mod = __import__(f'actions.{action_name}_action', fromlist=[class_name])
                    action_class = getattr(mod, class_name)
                    if hasattr(action_class, 'can_run') and action_class.can_run(self.session) is False:
                        del self.registry[cmd_name]['sub'][sub_name]
                except Exception:
                    # If import fails, leave as-is (may be user-supplied or optional)
                    pass

    # ----- Public API for chat/web/TUI -----------------------------------------
    def get_commands(self) -> list[str]:
        """Return top-level commands for simple completion/help."""
        names = sorted({f'/{name}' for name in self.registry.keys()})
        return names

    def complete(self, line: str, cursor: int, fragment: str) -> List[str]:
        """Return completion candidates for the current token.
        Only handles lines starting with '/'.
        """
        before = (line or '')[:cursor]
        if not before.lstrip().startswith('/'):
            return []

        tokens = _split_slash(before)
        end_with_space = (len(before) > 0 and before[-1].isspace())

        # No tokens yet: suggest top-level commands
        if not tokens:
            return [c for c in self.get_commands() if c.startswith(fragment or '')]

        # Completing the first token (command)
        if len(tokens) == 1 and not end_with_space:
            opts = [f'/{name}' for name in sorted(self.registry.keys())]
            return [o for o in opts if o.startswith(fragment or '')]

        # Identify command spec
        cmd = tokens[0]
        spec = self.registry.get(cmd)
        if not spec:
            # Suggest closest matches
            opts = [f'/{name}' for name in sorted(self.registry.keys())]
            return [o for o in opts if o.startswith(fragment or '')]

        subs = spec.get('sub', {})

        # If we just typed the command and a space, list subcommands (honor gating)
        if len(tokens) == 1 and end_with_space:
            gated = []
            for sname in sorted(list(subs.keys())):
                ok, _ = self._evaluate_gate(cmd, sname, [], subs.get(sname))
                if ok:
                    gated.append(sname)
            return [s for s in gated if s.startswith(fragment or '')]

        # Completing subcommand name (filter by gating)
        if len(tokens) == 2 and not end_with_space:
            gated = []
            for sname in sorted(list(subs.keys())):
                ok, _ = self._evaluate_gate(cmd, sname, [], subs.get(sname))
                if ok:
                    gated.append(sname)
            return [s for s in gated if s.startswith(fragment or '')]

        # Subcommand identified; delegate to sub-specific completer if present
        sub = ''
        if len(tokens) >= 2:
            if tokens[1] in subs:
                sub = tokens[1]
            else:
                # Unknown sub; suggest
                sub_opts = sorted(list(subs.keys()))
                return [s for s in sub_opts if s.startswith(fragment or '')]

        handler = subs.get(sub)
        if not handler:
            return []
        # Enforce fine-grained gating with current argv
        ok, _reason = self._evaluate_gate(cmd, sub, tokens[2:], handler)
        if not ok:
            return []
        completer = handler.get('complete')
        if callable(completer):
            return [c for c in completer(fragment or '') if c.startswith(fragment or '')]
        # Support action-provided completion methods
        if isinstance(completer, dict) and completer.get('type') == 'action_method':
            opts = self._invoke_action_completion(completer, fragment or '')
            return [o for o in (opts or []) if isinstance(o, str) and (o.startswith(fragment or ''))]
        return []

    def match(self, user_input: str) -> Optional[Dict[str, Any]]:
        """Parse a slash command and return a dispatch spec.
        Returns None if not a slash command.
        """
        s = (user_input or '').strip()
        if not s:
            return None
        # Allow escaping with double slash: treat as normal text
        if s.startswith('//'):
            return None
        if not s.startswith('/'):
            return None

        tokens = _split_slash(s)
        if not tokens:
            return None
        cmd = tokens[0]
        spec = self.registry.get(cmd)
        if not spec:
            return {
                'kind': 'error',
                'message': f"Unknown command '/{cmd}'",
            }
        argv = tokens[1:]
        sub = ''
        if argv:
            if argv[0] in spec['sub']:
                sub = argv[0]
                argv = argv[1:]
            elif '' not in spec['sub']:
                return {
                    'kind': 'error',
                    'message': f"Usage: /{cmd} <{', '.join(sorted(spec['sub'].keys()))}> …",
                }
        handler = spec['sub'].get(sub)
        if not handler:
            return {
                'kind': 'error',
                'message': f"No handler for '/{cmd} {sub}'",
            }
        ok, reason = self._evaluate_gate(cmd, sub, argv, handler)
        if not ok:
            return {'kind': 'error', 'message': reason or 'Command not applicable here.'}
        fixed = handler.get('args', [])
        if isinstance(fixed, str):
            fixed = [fixed]
        all_args = list(fixed) + list(argv)
        if handler.get('type') == 'builtin':
            return {
                'kind': 'builtin',
                'method': handler.get('name'),
                'args': all_args,
                'cmd': cmd,
                'sub': sub,
            }
        if handler.get('type') == 'action':
            return {
                'kind': 'action',
                'action': handler.get('name'),
                'method': handler.get('method'),  # optional method on action
                'args': all_args,
                'cmd': cmd,
                'sub': sub,
            }
        return {
            'kind': 'error',
            'message': 'Invalid command handler configuration.',
        }

    def run(self, user_input: str = None) -> bool | None:
        """Dispatch a slash command. Returns True if handled, None otherwise."""
        parsed = self.match(user_input or '')
        if not parsed:
            return None
        if parsed.get('kind') == 'error':
            self._emit_error(parsed.get('message') or 'Invalid command')
            return True

        kind = parsed.get('kind')
        args = parsed.get('args') or []

        if kind == 'builtin':
            method_name = parsed.get('method')
            if not method_name:
                self._emit_error('Invalid builtin handler')
                return True
            method = getattr(self, method_name, None)
            if callable(method):
                method(*args)
            else:
                self._emit_error(f"Unknown builtin method '{method_name}'")
            return True

        if kind == 'action':
            action_name = parsed.get('action')
            method_name = parsed.get('method')
            action = self.session.get_action(action_name)
            if not action:
                self._emit_error(f"Unknown action '{action_name}'")
                return True
            if method_name:
                # Prefer instance method; fallback to class/staticmethod(session, *args)
                fn = getattr(action, method_name, None)
                if callable(fn):
                    fn(*args)
                    return True
                cls = action.__class__
                fn2 = getattr(cls, method_name, None)
                if callable(fn2):
                    fn2(self.session, *args)
                    return True
                self._emit_error(f"Handler method '{method_name}' not found for action '{action_name}'")
                return True
            # Simple action call with argv
            action.run(args)
            return True

        # Fallback for unexpected kinds
        self._emit_error('Invalid command specification')
        return True

    # ----- Builtins and helpers -----------------------------------------------
    def handle_help(self, *maybe_cmd):
        # Non-blocking UIs: emit compact help
        if not getattr(self.session.ui.capabilities, 'blocking', False):
            lines = ["Commands:"]
            for name in sorted(self.registry.keys()):
                desc = self.registry[name].get('help', '')
                lines.append(f"/{name} - {desc}")
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass
            return

        # CLI: pretty with optional /help <cmd>
        import shutil
        _ = shutil.get_terminal_size((80, 20)).columns
        if maybe_cmd:
            cmd = str(maybe_cmd[0])
            spec = self.registry.get(cmd)
            if not spec:
                print(f"No such command '/{cmd}'\n")
                return
            print(f"/{cmd} — {spec.get('help','')}")
            subs = spec.get('sub', {})
            if subs:
                print('  Subcommands:')
                for sub in sorted(subs.keys()):
                    ok, _ = self._evaluate_gate(cmd, sub, [], subs.get(sub))
                    if ok:
                        print(f"    {sub or '(default)'}")
            print()
            return

        print('Commands:\n')
        names = sorted(self.registry.keys())
        col = max(len('/' + n) for n in names) + 2
        for name in names:
            desc = self.registry[name].get('help', '')
            print(f"/{name:<{col-1}} {desc}")
        print()

    def handle_quit(self):
        if self.session.handle_exit():
            quit()

    def _emit_info(self, msg: str):
        try:
            self.session.utils.output.info(msg)
        except Exception:
            try:
                self.session.ui.emit('status', {'message': msg})
            except Exception:
                print(msg)

    def _emit_error(self, msg: str):
        try:
            self.session.utils.output.error(msg)
        except Exception:
            try:
                self.session.ui.emit('error', {'message': msg})
            except Exception:
                print(msg)
