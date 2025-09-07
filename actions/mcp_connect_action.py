from __future__ import annotations

from base_classes import InteractionAction
from utils.tool_args import get_str
from memex_mcp.client import get_or_create_client


class McpConnectAction(InteractionAction):
    """Connect to an MCP server (http or stdio) and store it in session scope.

    Usage via user commands (when enabled):
      - load mcp http <name> <url>
      - load mcp stdio <name> <cmd>
    Without args, prompts interactively (CLI) or emits an interaction request.
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        args = args or []
        # Parse positional args when invoked as a user command
        transport = None
        name = None
        target = None

        if isinstance(args, (list, tuple)) and args:
            if len(args) >= 3:
                transport, name = args[0], args[1]
                target = ' '.join(args[2:]).strip()
            elif len(args) == 1 and isinstance(args[0], str):
                # Single arg may be a URL; derive name from host
                maybe = args[0].strip()
                transport = 'http' if maybe.startswith('http') else 'stdio'
                name = (maybe.split('//')[-1].split('/')[0] if transport == 'http' else 'local')
                target = maybe

        # Fall back to interactive prompts when needed
        if not transport:
            try:
                transport = (self.session.ui.ask_choice("MCP transport?", ["http", "stdio"]) or "http").strip()
            except Exception:
                transport = 'http'
        if not name:
            try:
                name = (self.session.ui.ask_text("Server name (short id):") or '').strip() or 'mcp'
            except Exception:
                name = 'mcp'
        if not target:
            try:
                prompt = "Server URL:" if transport == 'http' else "Command to run (stdio):"
                target = (self.session.ui.ask_text(prompt) or '').strip()
            except Exception:
                target = ''

        if not target:
            self._emit_status(f"Missing target for MCP {transport} connection.")
            return

        client = get_or_create_client(self.session)
        if transport == 'http':
            conn = client.connect_http(name=name, url=target)
            self._emit_status(f"Connected MCP server '{name}' over HTTP: {target}")
        else:
            conn = client.connect_stdio(name=name, cmd=target)
            self._emit_status(f"Connected MCP server '{name}' over stdio: {target}")

        # Persist a small summary into session user data for listing
        servers = [s.name for s in client.list_servers()]
        self.session.set_user_data('__mcp_servers__', servers)

        # Optionally wire provider pass-through for OpenAI Responses when using HTTP
        try:
            if transport == 'http':
                # Only prepare provider pass-through when supported and [MCP] is active
                prov = getattr(self.session, 'get_provider', lambda: None)()
                try:
                    supported = bool(getattr(prov, 'supports_mcp_passthrough', lambda: False)())
                except Exception:
                    supported = False
                try:
                    active = bool(self.session.get_option('MCP', 'active', fallback=False))
                except Exception:
                    active = False
                if supported and active:
                    params = self.session.get_params() or {}
                    # mcp_servers: append/merge this server
                    current = params.get('mcp_servers')
                    mapping = {}
                    if isinstance(current, dict):
                        mapping.update({str(k): str(v) for k, v in current.items()})
                    elif isinstance(current, str) and current.strip():
                        for item in current.split(','):
                            if '=' in item:
                                k, v = item.split('=', 1)
                                mapping[k.strip()] = v.strip()
                    mapping[name] = target
                    # Store back as CSV label=url for readability
                    csv_val = ', '.join(f"{k}={v}" for k, v in mapping.items())
                    self.session.config.set_option('mcp_servers', csv_val)
                    self._emit_status("Provider pass-through prepared: added this MCP server to provider config.")
                elif not supported:
                    self._emit_status("Current provider does not support MCP pass-through; app-side connection only.")
                else:
                    self._emit_status("[MCP] inactive; app-side connection only (enable [MCP].active to pass-through).")
        except Exception:
            pass

    # --- helpers ------------------------------------------------------------
    def _emit_status(self, msg: str):
        try:
            if self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False) is False:
                self.session.ui.emit('status', {'message': msg})
            else:
                self.session.utils.output.info(msg)
        except Exception:
            # Last resort
            print(msg)
