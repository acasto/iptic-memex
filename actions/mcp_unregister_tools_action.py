from __future__ import annotations

from base_classes import InteractionAction


class McpUnregisterToolsAction(InteractionAction):
    """Remove previously registered dynamic MCP tools.

    Usage:
      - unregister mcp tools               # remove all
      - unregister mcp tools <pattern>     # remove matching (substring match)
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        args = args or []
        pat = None
        if isinstance(args, (list, tuple)) and args:
            pat = str(args[0]).strip()

        dynamic = self.session.get_user_data('__dynamic_tools__') or {}
        if not dynamic:
            self._emit('status', 'No dynamic MCP tools are registered.')
            return

        if not pat:
            self.session.set_user_data('__dynamic_tools__', {})
            self._emit('status', 'Unregistered all dynamic MCP tools.')
            return

        removed = []
        remaining = {}
        for k, v in dynamic.items():
            if pat in k:
                removed.append(k)
            else:
                remaining[k] = v
        self.session.set_user_data('__dynamic_tools__', remaining)
        if removed:
            self._emit('status', f"Unregistered {len(removed)} tool(s): {', '.join(removed)}")
        else:
            self._emit('warning', f"No dynamic tools matched pattern '{pat}'.")

    def _emit(self, level: str, msg: str) -> None:
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit(level, {'message': msg})
            else:
                out = self.session.utils.output
                if level == 'error':
                    out.error(msg)
                elif level == 'warning':
                    out.warning(msg)
                else:
                    out.info(msg)
        except Exception:
            print(msg)

