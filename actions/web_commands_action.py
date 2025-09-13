from __future__ import annotations

from typing import Any, Dict

from base_classes import InteractionAction, Completed


class WebCommandsAction(InteractionAction):
    """
    Web adapter for user commands.

    Exposes a simple op-based interface for the web API layer or frontend to call via
    /api/action/start with { action:'web_commands', args:{op: 'list'|'execute', ...} }.
    """

    def __init__(self, session):
        self.session = session
        self.registry = session.get_action('user_commands_registry')

    # Web actions use start() directly; return Completed for consistency with action routes
    def start(self, args: Dict[str, Any] | None = None, content: Any | None = None) -> Completed:
        args = args or {}
        op = (args.get('op') or '').strip()
        if op == 'list':
            specs = self.registry.get_specs('web')
            return Completed({'ok': True, 'specs': specs})
        if op == 'execute':
            path = list(args.get('path') or [])
            a = dict(args.get('args') or {})
            ok, err = self.registry.execute(path, a, interactivity='no_prompts')
            return Completed({'ok': ok, 'error': err})
        return Completed({'ok': False, 'error': 'Invalid op'})
