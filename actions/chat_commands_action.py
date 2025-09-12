from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional

from base_classes import InteractionAction


def _split_slash(line: str) -> List[str]:
    s = (line or '').lstrip()
    if not s.startswith('/'):
        return []
    try:
        return shlex.split(s[1:], posix=True)
    except Exception:
        return s[1:].strip().split()


class ChatCommandsAction(InteractionAction):
    """Chat-mode adapter for user commands.

    - Parses slash strings and dispatches via the shared registry.
    - Provides completion helpers for CLI tab completion.
    - Keeps chat UX first-class while reusing a single source of truth.
    """

    def __init__(self, session):
        self.session = session
        self.registry = session.get_action('user_commands_registry')

    # ----- completion helpers --------------------------------------------------
    def get_commands(self) -> List[str]:
        specs = self.registry.get_specs('chat') if self.registry else {'commands': []}
        return sorted([f"/{c['command']}" for c in specs.get('commands', [])])

    def complete(self, line: str, cursor: int, fragment: str) -> List[str]:
        before = (line or '')[:cursor]
        if not before.lstrip().startswith('/'):
            return []
        tokens = _split_slash(before)
        end_with_space = (len(before) > 0 and before[-1].isspace())
        specs = self.registry.get_specs('chat') if self.registry else {'commands': []}
        reg = {c['command']: c for c in specs.get('commands', [])}

        # No tokens: show top-level commands
        if not tokens:
            return [x for x in self.get_commands() if x.startswith(fragment or '')]

        # Completing first token
        if len(tokens) == 1 and not end_with_space:
            opts = [f"/{name}" for name in reg.keys()]
            return [o for o in sorted(opts) if o.startswith(fragment or '')]

        # Identify command spec
        cmd = tokens[0]
        spec = reg.get(cmd)
        if not spec:
            opts = [f"/{name}" for name in reg.keys()]
            return [o for o in sorted(opts) if o.startswith(fragment or '')]

        subs = {s['sub']: s for s in (spec.get('subs') or [])}
        # If just typed the command and a space
        if len(tokens) == 1 and end_with_space:
            subnames = [s for s in subs.keys()]
            return [s for s in sorted(subnames) if s.startswith(fragment or '')]

        # Completing subcommand name
        if len(tokens) == 2 and not end_with_space:
            return [s for s in sorted(subs.keys()) if s.startswith(fragment or '')]

        # args: try UI hints, then handler-provided completion specs
        if len(tokens) >= 2:
            sub = tokens[1] if tokens[1] in subs else ''
            handler = subs.get(sub)
            if handler:
                ui = handler.get('ui') or {}
                choices = ui.get('choices') if isinstance(ui.get('choices'), list) else []
                if choices:
                    return [c for c in choices if str(c).startswith(fragment or '')]
                # Handler-driven completion
                comp = handler.get('complete')
                if isinstance(comp, dict):
                    if comp.get('type') == 'action_method':
                        opts = self._invoke_action_completion(comp, fragment or '')
                        return [o for o in (opts or []) if isinstance(o, str) and o.startswith(fragment or '')]
                    if comp.get('type') == 'builtin':
                        name = comp.get('name')
                        if name == 'file_paths':
                            return [c for c in self._complete_file_paths(fragment or '') if c.startswith(fragment or '')]
                        if name == 'chat_paths':
                            return [c for c in self._complete_chat_paths(fragment or '') if c.startswith(fragment or '')]
                        if name == 'models':
                            return [c for c in self._complete_models(fragment or '') if c.startswith(fragment or '')]
                        if name == 'options':
                            return [c for c in self._complete_options(fragment or '') if c.startswith(fragment or '')]
                        if name == 'tools':
                            return [c for c in self._complete_tools(fragment or '') if c.startswith(fragment or '')]
        return []

    # ----- parser + dispatcher -------------------------------------------------
    def match(self, user_input: str) -> Optional[Dict[str, Any]]:
        s = (user_input or '').strip()
        if not s or not s.startswith('/'):
            return None
        tokens = _split_slash(s)
        if not tokens:
            return None
        cmd = tokens[0]
        argv = tokens[1:]
        specs = self.registry.get_specs('chat') if self.registry else {'commands': []}
        reg = {c['command']: c for c in specs.get('commands', [])}
        cspec = reg.get(cmd)
        if not cspec:
            return {'kind': 'error', 'message': f"Unknown command '/{cmd}'"}
        subs = {s['sub']: s for s in (cspec.get('subs') or [])}
        sub = ''
        if argv:
            if argv[0] in subs:
                sub = argv[0]
                argv = argv[1:]
            elif '' not in subs:
                return {'kind': 'error', 'message': f"Usage: /{cmd} <{', '.join(sorted(subs.keys()))}> …"}
        handler = subs.get(sub)
        if not handler:
            return {'kind': 'error', 'message': f"No handler for '/{cmd} {sub}'"}
        # Build normalized invocation for run(), but also preserve legacy shape for web routes
        if handler.get('type') == 'action':
            fixed = handler.get('args') or []
            if isinstance(fixed, str):
                fixed = [fixed]
            all_args = list(fixed) + list(argv)
            # Legacy-compatible keys for web route preflight (action/method/args)
            return {
                'kind': 'action',
                'path': [cmd, sub],
                'argv': argv,
                'action': handler.get('action'),
                'method': handler.get('method'),
                'args': all_args,
            }
        if handler.get('type') == 'builtin':
            # Keep legacy builtin shape for web routes
            return {
                'kind': 'builtin',
                'path': [cmd, sub],
                'argv': argv,
                'method': 'handle_' + (cmd if cmd != 'help' else 'help'),
                'args': list(argv),
            }
        return {'kind': 'error', 'message': 'Invalid command handler configuration.'}

    def run(self, user_input: str = None) -> bool | None:
        """Dispatch a slash command. Returns True if handled, None otherwise."""
        parsed = self.match(user_input or '')
        if not parsed:
            return None
        if parsed.get('kind') == 'error':
            self._emit_error(parsed.get('message') or 'Invalid command')
            return True
        path = parsed.get('path') or []
        argv = parsed.get('argv') or []
        # Route via registry executor, passing argv as args.argv for compat
        ok, err = self.registry.execute(path, {'argv': argv}, interactivity='allow_prompts')
        if not ok and err:
            self._emit_error(err)
        return True

    # ----- minimal builtins for web preflight -------------------------------
    def handle_help(self, *maybe_cmd):
        specs = self.registry.get_specs('chat') if self.registry else {'commands': []}
        if maybe_cmd:
            # Print single command help to UI
            name = str(maybe_cmd[0])
            item = next((c for c in specs.get('commands', []) if c.get('command') == name), None)
            if item:
                subs = ', '.join(sorted([s.get('sub') or '(default)' for s in item.get('subs', [])]))
                try:
                    self.session.ui.emit('status', {'message': f"/{name} — {item.get('help','')}\nSubcommands: {subs}"})
                except Exception:
                    pass
                return
        # List all
        try:
            lines = ["Commands:"]
            for c in specs.get('commands', []):
                lines.append(f"/{c['command']} - {c.get('help','')}")
            self.session.ui.emit('status', {'message': "\n".join(lines)})
        except Exception:
            pass

    def handle_quit(self):
        if self.session.handle_exit():
            quit()

    # ----- helpers -------------------------------------------------------------
    def _emit_error(self, msg: str):
        try:
            self.session.utils.output.error(msg)
        except Exception:
            try:
                self.session.ui.emit('error', {'message': msg})
            except Exception:
                print(msg)

    # ----- helper completions borrowed from legacy ---------------------------
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

    def _invoke_action_completion(self, spec: Dict[str, Any], prefix: str) -> List[str]:
        try:
            aname = spec.get('name')
            mname = spec.get('method')
            aargs = list(spec.get('args') or [])
            class_name = ''.join(w.capitalize() for w in aname.split('_')) + 'Action'
            mod = __import__(f'actions.{aname}_action', fromlist=[class_name])
            action_class = getattr(mod, class_name)
            fn = getattr(action_class, mname)
            try:
                out = fn(self.session, *aargs, prefix)
            except TypeError:
                inst = self.session.get_action(aname)
                fn2 = getattr(inst, mname, None)
                out = fn2(*aargs, prefix) if callable(fn2) else []
            return list(out or [])
        except Exception:
            return []
