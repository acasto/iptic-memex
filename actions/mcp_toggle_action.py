from __future__ import annotations

from base_classes import InteractionAction


class McpToggleAction(InteractionAction):
    """Enable or disable MCP globally for the session.

    Usage: /mcp on | /mcp off
    - on: set [MCP].active=true and autoload configured servers
    - off: unload all app-side servers and set [MCP].active=false
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session, target: str | None = None):
        try:
            active = bool(session.get_option('MCP', 'active', fallback=False))
        except Exception:
            active = False
        t = (target or '').strip().lower() if target else None
        if t == 'on':
            return (not active, 'Already enabled' if active else None)
        if t == 'off':
            return (active, 'Already disabled' if not active else None)
        return True

    def run(self, args=None):
        args = list(args or [])
        if not args:
            self._emit('error', "Usage: /mcp <on|off>")
            return
        cmd = str(args[0]).strip().lower()
        if cmd not in ('on', 'off'):
            self._emit('error', "Usage: /mcp <on|off>")
            return

        enable = (cmd == 'on')

        # Idempotent: if no change, just inform
        was_active = bool(self.session.get_option('MCP', 'active', fallback=False))
        if enable and was_active:
            self._emit('status', 'MCP already enabled; no changes made.')
            return
        if (not enable) and (not was_active):
            self._emit('status', 'MCP already disabled; no changes made.')
            return

        # Store override in session config for [MCP].active
        try:
            self.session.config.set_option('active', enable)
            self.session.config.overrides.setdefault('MCP', type('o', (), {}))
            setattr(self.session.config.overrides['MCP'], 'active', enable)
        except Exception:
            pass

        # Apply effect
        try:
            if enable:
                from memex_mcp.bootstrap import autoload_mcp
                autoload_mcp(self.session)
                self._emit('status', 'MCP enabled and autoloaded.')
            else:
                try:
                    action = self.session.get_action('mcp_unload')
                    action.run(['all'])
                except Exception:
                    pass
                self._emit('status', 'MCP disabled and all servers unloaded.')
        except Exception:
            pass

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

