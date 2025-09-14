from __future__ import annotations

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client


class McpLoadAction(InteractionAction):
    """Load a configured MCP server and register its tools.

    Usage:
      - mcp load <server>
    Honors per-server allowed_tools and auto_alias.
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
        server = str(args[0]).strip() if args else ''
        if not server:
            self._emit('error', "Usage: /mcp load <server>")
            return

        # Read server config
        try:
            from memex_mcp.bootstrap import _get_cfg_sections, _normalize_server_entry, _coerce_bool
            glob, raw = _get_cfg_sections(self.session)
        except Exception:
            glob, raw = {}, {}

        if server not in raw:
            self._emit('error', f"Unknown MCP server in config: '{server}'")
            return

        s = _normalize_server_entry(server, raw[server])

        # Connect app-side servers (http/stdio)
        try:
            client = get_or_create_client(self.session)
            if s.get('transport') == 'http':
                client.connect_http(server, s.get('url') or '', headers=s.get('headers') or {})
            elif s.get('transport') == 'stdio':
                client.connect_stdio(server, s.get('command') or '')
            elif s.get('transport') == 'provider':
                # For provider-only servers, just wire provider pass-through
                from memex_mcp.bootstrap import _apply_provider_passthrough
                _apply_provider_passthrough(self.session, {server: s})
                self._emit('status', f"Prepared provider pass-through for MCP server '{server}'.")
                return
            else:
                self._emit('error', f"Unsupported transport for server '{server}': {s.get('transport')}")
                return
        except Exception as e:
            self._emit('error', f"Failed to connect MCP server '{server}': {e}")
            return

        # Decide alias behavior (global default true; per-server override)
        try:
            global_alias = _coerce_bool(glob.get('auto_alias'), True)
        except Exception:
            global_alias = True
        try:
            srv_alias = s.get('auto_alias')
            do_alias = global_alias if srv_alias is None else bool(srv_alias)
        except Exception:
            do_alias = True

        # Register tools (optional filter)
        allow_list = list(s.get('allowed_tools') or [])
        try:
            registrar = self.session.get_action('mcp_register_tools')
            reg_args = [server]
            if allow_list:
                reg_args.append('--tools=' + ','.join(allow_list))
            if do_alias:
                reg_args.append('--alias')
            registrar.run(reg_args)
        except Exception as e:
            self._emit('error', f"Connected but failed to register tools for '{server}': {e}")
            return

        # Update summary
        try:
            summary = self.session.get_user_data('__mcp_autoload__') or {}
            summary[server] = {'autoload': True, 'alias': bool(do_alias)}
            self.session.set_user_data('__mcp_autoload__', summary)
        except Exception:
            pass

        self._emit('status', f"MCP server '{server}' loaded and tools registered (alias={'on' if do_alias else 'off'}).")

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
            try:
                self.session.utils.output.info(msg)
            except Exception:
                pass
