from __future__ import annotations

from typing import Any, Dict

from base_classes import InteractionAction, Completed


class TuiCommandsAction(InteractionAction):
    """
    TUI adapter for user commands.

    Provides a structure similar to WebCommandsAction so the TUI palette/forms can
    fetch available commands and execute them with structured args.
    """

    def __init__(self, session):
        self.session = session
        self.registry = session.get_action('user_commands_registry')

    def start(self, args: Dict[str, Any] | None = None, content: Any | None = None) -> Completed:
        args = args or {}
        op = (args.get('op') or '').strip()
        if op == 'list':
            specs = self.registry.get_specs('tui')
            return Completed({'ok': True, 'specs': specs})
        if op == 'execute':
            path = list(args.get('path') or [])
            a = dict(args.get('args') or {})
            ok, err = self.registry.execute(path, a, interactivity='allow_prompts')
            return Completed({'ok': ok, 'error': err})
        return Completed({'ok': False, 'error': 'Invalid op'})

