from __future__ import annotations

import json
from typing import Dict, List

from base_classes import InteractionAction
from memex_mcp.client import get_or_create_client, MCPToolSpec


class McpRegisterToolsAction(InteractionAction):
    """Register discovered MCP tools as first-class tools for this session.

    Usage:
      - register mcp tools <server> [--tools name1,name2] [--alias]
        • If --tools omitted, registers all discovered tools on the server.
        • --alias additionally creates pretty aliases (if no conflicts).
    """

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    def run(self, args=None):
        # Parse simple positional tokens for CLI command usage
        args = args or []
        server = None
        tools_csv = None
        alias = False
        for tok in args:
            s = str(tok)
            if s == '--alias':
                alias = True
            elif s.startswith('--tools'):
                # --tools=foo,bar or separate value
                if '=' in s:
                    tools_csv = s.split('=', 1)[1]
            elif server is None:
                server = s

        if not server:
            self._emit('error', "register mcp tools: missing <server> name")
            return

        client = get_or_create_client(self.session)
        tool_map = client.list_tools(server)
        tools: List[MCPToolSpec] = tool_map.get(server, []) if isinstance(tool_map, dict) else []
        if not tools:
            self._emit('warning', f"No tools discovered on server '{server}'.")
            return

        filter_set = None
        if tools_csv:
            filter_set = {t.strip() for t in tools_csv.split(',') if t.strip()}

        # Build dynamic tool specs
        dynamic = self.session.get_user_data('__dynamic_tools__') or {}
        added = []
        # Build a set of existing tool names to guard alias collisions
        existing_names = set()
        try:
            registry = getattr(self.session, '_registry', None)
            if registry:
                for name in registry.list_available_actions() or []:
                    cls = registry.get_action_class(name)
                    if hasattr(cls, 'tool_name'):
                        n = str(cls.tool_name() or '').strip().lower()
                        if n:
                            existing_names.add(n)
        except Exception:
            existing_names = set()

        for t in tools:
            if filter_set and t.name not in filter_set:
                continue
            spec = self._to_command_spec(server, t)
            key = spec['name']
            dynamic[key] = spec
            added.append(key)
            # Optional alias (pretty name without namespace)
            if alias:
                alias_key = t.name
                if alias_key not in dynamic and (alias_key not in existing_names):
                    alias_spec = dict(spec)
                    alias_spec['name'] = alias_key
                    dynamic[alias_key] = alias_spec

        self.session.set_user_data('__dynamic_tools__', dynamic)

        # Emit summary only when MCP debug is enabled to avoid noisy startup output
        try:
            debug = bool(self.session.get_option('MCP', 'debug', fallback=False))
        except Exception:
            debug = False
        if added and debug:
            self._emit('status', f"Registered {len(added)} MCP tool(s): {', '.join(added)}")
        elif not added and debug:
            self._emit('warning', "No tools were registered (check filters or conflicts).")

    # ---- helpers -----------------------------------------------------------
    def _to_command_spec(self, server: str, tool: MCPToolSpec) -> Dict:
        schema = tool.input_schema or {"type": "object", "properties": {}}
        props = schema.get('properties') or {}
        required = schema.get('required') or []
        # Compose a namespaced key to avoid collisions
        key = f"mcp:{server}/{tool.name}"
        return {
            'name': key,
            'description': tool.description or f"Remote tool {tool.name} from {server}",
            'args': list(props.keys()),
            'required': list(required),
            'schema': {'properties': props},
            'auto_submit': True,
            'function': {
                'type': 'action',
                'name': 'mcp_proxy_tool',
                'fixed_args': {'server': server, 'tool': tool.name},
            },
        }

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
