from __future__ import annotations

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client
import hashlib
import json
import time


class McpAction(InteractionAction):
    """Unified MCP command: list/info and status.

    Subcommands (user commands wire these up):
      - mcp                     -> list servers
      - mcp tools              -> list tools per server
      - mcp resources          -> list resources per server
      - mcp provider           -> show provider MCP pass-through config
      - mcp status             -> status/doctor (SDK, servers, probes)

    Back-compat aliases (accepted if typed):
      - mcp doctor             -> status
      - mcp list [tools|resources] -> handled like above
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        args = list(args or [])
        # Normalize args from strings and drop optional leading 'list'
        if len(args) == 1 and isinstance(args[0], str):
            args = [args[0]]
        if args and str(args[0]).lower() == 'list':
            args = args[1:]

        sub = str(args[0]).lower() if args else 'servers'
        if sub in ('servers', 'server', ''):
            return self._list_servers()
        if sub in ('tools', 'tool'):
            return self._list_tools()
        if sub in ('resources', 'resource'):
            return self._list_resources()
        if sub in ('provider', 'provider-mcp', 'provider_mcp', 'config'):
            return self._print_provider_mcp()
        if sub in ('status', 'doctor'):
            return self._doctor()

        # Fallback: treat unknown subcommand as server listing
        return self._list_servers()

    # --- list helpers -------------------------------------------------------
    def _print(self, msg: str) -> None:
        try:
            blocking = bool(self.session.ui and getattr(self.session.ui.capabilities, 'blocking', False))
        except Exception:
            blocking = True
        if not blocking and self.session.ui:
            self.session.ui.emit('status', {'message': msg})
        else:
            self.session.utils.output.write(msg)

    def _print_kv_list(self, title: str, data: dict):
        lines = [title + ":"]
        if not data:
            lines.append("  (none)")
        else:
            for k, v in data.items():
                if isinstance(v, (list, tuple)):
                    v_str = ", ".join(v) if v else "(none)"
                else:
                    v_str = str(v)
                lines.append(f"  - {k}: {v_str}")
        self._print("\n".join(lines))

    def _list_servers(self):
        client = get_or_create_client(self.session)
        servers = client.list_servers()

        def _fmt(s):
            target = s.url or s.cmd or ''
            return f"[{s.transport}] {target}" if target else f"[{s.transport}]"

        info = {s.name: _fmt(s) for s in servers}
        self._print_kv_list("App-side MCP servers", info)

    def _list_tools(self):
        client = get_or_create_client(self.session)
        data = client.list_tools()
        # Annotate with auto-register/alias flags when available
        auto = {}
        try:
            auto = self.session.get_user_data('__mcp_autoreg__') or {}
        except Exception:
            auto = {}
        # Build display with per-server flags
        titled = {}
        for server, tools in (data or {}).items():
            try:
                flags = auto.get(server) or {}
                ar = 'yes' if flags.get('auto_register') else 'no'
                aa = 'yes' if flags.get('auto_alias') else 'no'
                display = f"{server} [auto_register={ar} alias={aa}]"
            except Exception:
                display = server
            titled[display] = [t.name for t in (tools or [])]
        self._print_kv_list("App-side MCP tools", titled)

    def _list_resources(self):
        client = get_or_create_client(self.session)
        data = client.list_resources()
        self._print_kv_list("App-side MCP resources", {k: [r.uri for r in v] for k, v in data.items()})

    # --- provider pass-through ---------------------------------------------
    def _provider_mcp_lines(self) -> list[str]:
        lines: list[str] = []
        try:
            params = self.session.get_params() or {}
            # Provider capability and global gate
            prov = getattr(self.session, 'get_provider', lambda: None)()
            try:
                supported = bool(getattr(prov, 'supports_mcp_passthrough', lambda: False)())
            except Exception:
                supported = False
            try:
                active = bool(self.session.get_option('MCP', 'active', fallback=False))
            except Exception:
                active = False

            servers_val = params.get('mcp_servers')
            servers: dict[str, str] = {}
            if isinstance(servers_val, dict):
                servers = {str(k): str(v) for k, v in servers_val.items() if k and v}
            elif isinstance(servers_val, str) and servers_val.strip():
                for item in servers_val.split(','):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        k = k.strip(); v = v.strip()
                        if k and v:
                            servers[k] = v

            lines = ["Provider MCP (pass-through):",
                     f"  - supported: {supported}",
                     f"  - active: {active}"]
            if not servers:
                lines.append("  - servers: (none)")
            else:
                lines.append("  - servers:")
                for label, url in servers.items():
                    token_present = False
                    hdr_key = f'mcp_headers_{label}'
                    headers_val = params.get(hdr_key)
                    headers_obj = None
                    if isinstance(headers_val, dict):
                        headers_obj = headers_val
                    elif isinstance(headers_val, str) and headers_val.strip().startswith('{'):
                        import json as _json
                        try:
                            parsed = _json.loads(headers_val)
                            if isinstance(parsed, dict):
                                headers_obj = parsed
                        except Exception:
                            headers_obj = None
                    if isinstance(headers_obj, dict):
                        for k, v in headers_obj.items():
                            if str(k).lower() == 'authorization' and str(v).strip():
                                token_present = True
                                break
                    for tkey in (f'mcp_token_{label}', f'mcp_authorization_{label}'):
                        if isinstance(params.get(tkey), str) and params.get(tkey).strip():
                            token_present = True
                            break
                    allowed_raw = params.get(f'mcp_allowed_{label}', '') or ''
                    allowed = ','.join([s.strip() for s in str(allowed_raw).split(',') if s.strip()]) if allowed_raw else ''
                    approval = params.get(f'mcp_require_approval_{label}') or params.get('mcp_require_approval') or ''
                    details = [f"approval={approval or 'default'}", f"token={'present' if token_present else 'none'}"]
                    if allowed:
                        details.append(f"allowed=[{allowed}]")
                    lines.append(f"    - {label}: {url}  ({', '.join(details)})")
            # If active but unsupported, add a short note to reduce confusion
            if active and not supported:
                lines.append("  - note: current provider does not support pass-through MCP")
        except Exception:
            lines = ["Provider MCP (pass-through): (error)"]
        return lines

    def _print_provider_mcp(self):
        self._print("\n".join(self._provider_mcp_lines()))

    # --- doctor/status ------------------------------------------------------
    def _doctor(self):
        client = get_or_create_client(self.session)
        lines = ["MCP Status (app + provider):"]
        # Config
        try:
            use_sdk = bool(self.session.get_option('MCP', 'use_sdk', fallback=False))
        except Exception:
            use_sdk = False
        lines.append(f"- use_sdk: {use_sdk}")
        # SDK detection + version (best-effort)
        sdk_enabled = False
        sdk_version = None
        try:
            sdk_enabled = getattr(client, 'use_sdk', False) and client._sdk_enabled()
            if sdk_enabled:
                import importlib
                if importlib.util.find_spec('mcp') is not None:
                    m = importlib.import_module('mcp')
                    sdk_version = getattr(m, '__version__', 'present')
                elif importlib.util.find_spec('modelcontextprotocol') is not None:
                    m = importlib.import_module('modelcontextprotocol')
                    sdk_version = getattr(m, '__version__', 'present')
        except Exception:
            sdk_enabled = False
            sdk_version = None
        lines.append(f"- sdk_available: {sdk_enabled}")
        if sdk_version:
            lines.append(f"- sdk_version: {sdk_version}")

        # Servers
        servers = client.list_servers() or []
        if not servers:
            lines.append("- app_servers: (none)")
        else:
            lines.append("- app_servers:")
            for s in servers:
                t = f"{s.transport}:{s.url or s.cmd}"
                via = 'sdk' if (sdk_enabled and s.sdk is not None) else 'http' if s.transport == 'http' else 'stdio'
                lines.append(f"  • {s.name}  [{t}]  via={via}")
        # Round-trip checks (best-effort)
        try:
            if servers:
                s = servers[0]
                # Tool echo.say
                try:
                    toolmap = client.list_tools(s.name) or {}
                    tools = toolmap.get(s.name) or []
                    names = {getattr(t, 'name', '') for t in tools}
                except Exception:
                    names = set()
                if 'echo.say' in names:
                    started = time.time()
                    out = client.call_tool(s.name, 'echo.say', {'text': 'ping'})
                    ms = int((time.time() - started) * 1000)
                    digest = hashlib.sha1(json.dumps(out, sort_keys=True).encode('utf-8')).hexdigest()[:8]
                    lines.append(f"- round_trip: server={s.name} tool=echo.say time_ms={ms} hash={digest}")
                # Demo resource, if present
                try:
                    if any(getattr(r, 'uri', None) == 'guides/welcome' for r in getattr(s, 'resources', []) ):
                        item = client.fetch_resource(s.name, 'guides/welcome')
                        content = (item or {}).get('content') or ''
                        preview = content[:80].replace('\n', ' ')
                        lines.append(f"- resource_probe: {len(content)} bytes; preview='{preview}'")
                except Exception:
                    pass
        except Exception:
            pass

        # Append provider pass-through details
        lines.append("")
        lines.extend(self._provider_mcp_lines())

        self._print("\n".join(lines))
