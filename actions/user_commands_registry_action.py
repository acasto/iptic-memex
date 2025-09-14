from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from base_classes import InteractionAction


class UserCommandsRegistryAction(InteractionAction):
    """
    Canonical registry + dispatcher for user commands (mode-agnostic).

    Responsibilities
    - Build the command registry (with gating and user extensions).
    - Provide specs for UIs (chat/web/tui) via get_specs(for_mode).
    - Execute commands via execute(path,args), mapping to actions.

    Notes
    - Stays in actions/ to keep user extensibility (mirrors assistant_commands).
    - Web and TUI adapters consume this action instead of parsing slash strings.
    - Chat adapter uses this registry for help/choices and dispatches via execute().
    """

    # ----- lifecycle -----------------------------------------------------------
    def __init__(self, session):
        self.session = session
        self.registry: Dict[str, Dict[str, Any]] = self._build_registry()
        self._merge_user_commands()
        self._gate_unavailable()

    # ----- registry construction ----------------------------------------------
    def _build_registry(self) -> Dict[str, Dict[str, Any]]:
        # Full registry derived from the legacy user_commands_action
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
                    'file':      {'type': 'action', 'name': 'load_file', 'complete': {'type': 'builtin', 'name': 'file_paths'}},
                    'raw':       {'type': 'action', 'name': 'load_raw'},
                    'rag':       {'type': 'action', 'name': 'load_rag'},
                    'code':      {'type': 'action', 'name': 'fetch_code_snippet'},
                    'multiline': {'type': 'action', 'name': 'load_multiline'},
                    'web':       {'type': 'action', 'name': 'fetch_from_web'} ,
                    'chat':      {'type': 'action', 'name': 'manage_chats', 'args': ['load'], 'complete': {'type': 'builtin', 'name': 'chat_paths'}},
                    # MCP helpers via load group for convenience
                    'mcp':          {'type': 'action', 'name': 'mcp_connect'},
                    'mcp-demo':     {'type': 'action', 'name': 'mcp_demo'},
                    'mcp-resource': {'type': 'action', 'name': 'mcp_fetch_resource'},
                },
            },
            # Shortcut: `/file` behaves like `/load file`
            'file': {
                'help': 'Load file into context',
                'sub': {
                    '': {'type': 'action', 'name': 'load_file', 'complete': {'type': 'builtin', 'name': 'file_paths'}},
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
                    'option':       {'type': 'action', 'name': 'set_option', 'complete': {'type': 'builtin', 'name': 'options'}},
                    'option-tools': {'type': 'action', 'name': 'set_option', 'args': ['tools'], 'complete': {'type': 'builtin', 'name': 'tools'}},
                    'model':        {'type': 'action', 'name': 'set_model', 'complete': {'type': 'builtin', 'name': 'models'}},
                    'search':       {
                        'type': 'action', 'name': 'assistant_websearch_tool', 'method': 'set_search_model',
                        'complete': {'type': 'builtin', 'name': 'models'},
                    },
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
                    'on':         {
                        'type': 'action', 'name': 'mcp_toggle', 'args': ['on'],
                        'gate': {'type': 'action_can_run', 'name': 'mcp_toggle', 'args': ['on']},
                    },
                    'off':        {
                        'type': 'action', 'name': 'mcp_toggle', 'args': ['off'],
                        'gate': {'type': 'action_can_run', 'name': 'mcp_toggle', 'args': ['off']},
                    },
                    'load':       {'type': 'action', 'name': 'mcp_load'},
                    'unload':     {'type': 'action', 'name': 'mcp_unload'},
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
        """Merge user-contributed commands from register_user_commands_action."""
        try:
            reg_action = self.session.get_action('register_user_commands')
        except Exception:
            reg_action = None
        if not reg_action:
            return
        try:
            new_commands = reg_action.run()
        except Exception:
            new_commands = None
        if not isinstance(new_commands, dict):
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
        """Remove subcommands with Action.can_run(session) == False."""
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
                    pass

    # ----- public API ----------------------------------------------------------
    def get_specs(self, for_mode: str | None = None) -> Dict[str, Any]:
        """Return gated specs with UI hints and resolved dynamic choices.

        Args:
            for_mode: optional mode hint ('chat'|'web'|'tui') for UI tweaks.

        Returns:
            dict: { commands: [ {path, label, help, subs:[...]} ] }
        """
        items: List[Dict[str, Any]] = []
        for cmd_name, cmd_info in sorted(self.registry.items()):
            subs_spec = []
            for sub_name, sub_info in sorted(cmd_info.get('sub', {}).items()):
                # Evaluate per-sub gate (e.g., mcp on/off)
                if not self._evaluate_gate(sub_info):
                    continue
                entry = {
                    'sub': sub_name,
                    'type': sub_info.get('type'),
                    'action': sub_info.get('name'),
                    'method': sub_info.get('method'),
                    'args': sub_info.get('args') or [],
                    # Preserve completion metadata so chat/web can offer arg completions
                    'complete': sub_info.get('complete'),
                    'ui': self._resolve_ui_hints(sub_info),
                }
                subs_spec.append(entry)
            items.append({
                'command': cmd_name,
                'help': cmd_info.get('help', ''),
                'subs': subs_spec,
            })
        return {'commands': items, 'mode': for_mode or ''}

    def _evaluate_gate(self, handler: Dict[str, Any]) -> bool:
        gate = handler.get('gate') if isinstance(handler, dict) else None
        if not gate:
            return True
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
                        res = can_run(self.session)
                    if isinstance(res, tuple):
                        return bool(res[0])
                    return bool(res)
            except Exception:
                return True
        return True

    def _resolve_ui_hints(self, sub_info: Dict[str, Any]) -> Dict[str, Any]:
        ui = dict(sub_info.get('ui') or {})
        # Resolve dynamic choices if requested
        # Normalize dynamic model choices for simple UIs
        choices = ui.get('choices') if isinstance(ui.get('choices'), list) else None
        if choices is None and isinstance(ui.get('choices'), dict) and ui['choices'].get('type') == 'dynamic':
            method = ui['choices'].get('method')
            if method in ('__dynamic_models', 'models'):
                try:
                    ui['choices'] = sorted(list(self.session.list_models().keys()))
                except Exception:
                    ui['choices'] = []
        return ui

    def execute(self, path: List[str], args: Dict[str, Any] | None = None, interactivity: str = 'allow_prompts') -> Tuple[bool, Optional[str]]:
        """Execute a command by normalized path and arg dict.

        Args:
            path: [command, sub?]; sub can be '' for default handler.
            args: normalized arguments (positional mapping not currently used).
            interactivity: 'allow_prompts'|'no_prompts' (reserved; Stepwise will raise if needed).

        Returns:
            (ok, error_message)
        """
        args = args or {}
        if not path:
            return False, 'Missing command path'
        cmd = path[0]
        sub = path[1] if len(path) > 1 else ''
        spec = self.registry.get(cmd)
        if not spec:
            return False, f"Unknown command '{cmd}'"
        handler = spec.get('sub', {}).get(sub)
        if not handler:
            return False, f"No handler for '{cmd} {sub}'"

        # Fixed argv support (map dict to argv for backward compatibility)
        fixed = handler.get('args', [])
        if isinstance(fixed, str):
            fixed = [fixed]
        # For now, only positional argv from 'args' + optional 'argv' in args dict
        argv = list(fixed)
        extra_argv = args.get('argv') if isinstance(args, dict) else None
        if isinstance(extra_argv, list):
            argv.extend([str(x) for x in extra_argv])

        htype = handler.get('type')
        if htype == 'builtin':
            name = handler.get('name')
            # Minimal builtins for help/quit so adapters can also route here if desired
            if name == 'handle_help':
                try:
                    lines = ["Commands:"]
                    for c in sorted(self.registry.keys()):
                        lines.append(f"/{c} - {self.registry[c].get('help','')}")
                    self.session.ui.emit('status', {'message': "\n".join(lines)})
                except Exception:
                    pass
                return True, None
            if name == 'handle_quit':
                try:
                    if self.session.handle_exit():
                        quit()
                except Exception:
                    pass
                return True, None
            return False, 'Unknown builtin'

        if htype == 'action':
            action_name = handler.get('name')
            if not action_name:
                return False, 'Invalid action mapping'
            action = self.session.get_action(action_name)
            if not action:
                return False, f"Unknown action '{action_name}'"
            method_name = handler.get('method')
            if method_name:
                fn = getattr(action, method_name, None)
                if callable(fn):
                    fn(*argv)
                    return True, None
                # classmethod fallback
                cls = action.__class__
                fn2 = getattr(cls, method_name, None)
                if callable(fn2):
                    fn2(self.session, *argv)
                    return True, None
                return False, f"Handler method '{method_name}' not found for action '{action_name}'"
            res = action.run(argv)
            # Best-effort: if the action returned a chats list, surface it in CLI via UI emits
            try:
                payload = None
                if hasattr(res, 'payload'):
                    payload = getattr(res, 'payload', None)
                elif isinstance(res, dict):
                    payload = res
                if isinstance(payload, dict) and isinstance(payload.get('chats'), list):
                    chats = payload.get('chats') or []
                    try:
                        self.session.ui.emit('status', {'message': 'Saved chats:'})
                        if not chats:
                            self.session.ui.emit('status', {'message': '(none)'})
                        for it in chats:
                            name = (it.get('name') or it.get('filename') or it.get('path') or '') if isinstance(it, dict) else str(it)
                            self.session.ui.emit('status', {'message': f"- {name}"})
                    except Exception:
                        pass
            except Exception:
                pass
            return True, None

        return False, 'Invalid handler type'

    # InteractionAction entrypoint (optional)
    def run(self, args=None):
        """Expose list/execute via action start for web/tui if desired.

        args.op: 'list'|'execute'
        args.path: [command, sub?]
        args.args: dict
        """
        args = args or {}
        op = (args.get('op') or '').strip()
        if op == 'list':
            return {'ok': True, 'specs': self.get_specs(args.get('for_mode'))}
        if op == 'execute':
            ok, err = self.execute(list(args.get('path') or []), dict(args.get('args') or {}))
            return {'ok': ok, 'error': err}
        return {'ok': False, 'error': 'Invalid op'}
