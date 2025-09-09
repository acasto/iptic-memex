from __future__ import annotations

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client


class McpUnloadAction(InteractionAction):
    """Unload a configured MCP server: disconnect and remove its dynamic tools.

    Usage:
      - mcp unload <server>
      - mcp unload all
    Also removes provider pass-through entry for http servers.
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        try:
            return bool(session.get_option('MCP', 'active', fallback=False))
        except Exception:
            return False

    def run(self, args=None):
        args = list(args or [])
        target = str(args[0]).strip() if args else ''
        if not target:
            self._emit('error', "Usage: /mcp unload <server|all>")
            return

        client = get_or_create_client(self.session)

        if target.lower() == 'all':
            names = [s.name for s in client.list_servers()]
            for name in names:
                self._unload_one(name, client)
            self._emit('status', f"Unloaded {len(names)} MCP server(s).")
            return

        self._unload_one(target, client)
        self._emit('status', f"MCP server '{target}' unloaded.")

    def _unload_one(self, server: str, client) -> None:
        # Remove dynamic tools for this server (both canonical and alias entries)
        try:
            dynamic = self.session.get_user_data('__dynamic_tools__') or {}
            remaining = {}
            removed = 0
            for key, spec in (dynamic or {}).items():
                try:
                    # Prefer the function.fixed_args.server pin to identify server ownership
                    fn = spec.get('function') if isinstance(spec, dict) else None
                    fixed = fn.get('fixed_args') if isinstance(fn, dict) else None
                    srv = (fixed or {}).get('server') if isinstance(fixed, dict) else None
                    if srv == server:
                        removed += 1
                        continue
                    # Fallback: canonical key prefix mcp:<server>/
                    if isinstance(key, str) and key.startswith(f"mcp:{server}/"):
                        removed += 1
                        continue
                    remaining[key] = spec
                except Exception:
                    remaining[key] = spec
            self.session.set_user_data('__dynamic_tools__', remaining)
        except Exception:
            pass

        # Disconnect app-side server
        try:
            client.disconnect(server)
        except Exception:
            pass

        # Remove from provider pass-through mapping
        try:
            params = self.session.get_params() or {}
            current = params.get('mcp_servers')
            mapping = {}
            if isinstance(current, dict):
                mapping.update({str(k): str(v) for k, v in current.items()})
            elif isinstance(current, str) and current.strip():
                for item in current.split(','):
                    item = item.strip()
                    if '=' in item:
                        k, v = item.split('=', 1)
                        mapping[k.strip()] = v.strip()
            if server in mapping:
                del mapping[server]
                csv_val = ', '.join(f"{k}={v}" for k, v in mapping.items())
                self.session.config.set_option('mcp_servers', csv_val)
        except Exception:
            pass

        # Update summary
        try:
            summary = self.session.get_user_data('__mcp_autoload__') or {}
            if server in summary:
                del summary[server]
            self.session.set_user_data('__mcp_autoload__', summary)
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

