from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MCPToolSpec:
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResource:
    uri: str
    title: Optional[str] = None
    mime_type: Optional[str] = None


@dataclass
class MCPServerConnection:
    name: str
    transport: str  # 'http' | 'stdio'
    url: Optional[str] = None
    cmd: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    connected: bool = False
    tools: List[MCPToolSpec] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)


class MCPClient:
    """Session-scoped minimal MCP client facade.

    Notes:
    - This is a stub that records configured servers and allows the app to
      develop against a stable API surface. Real protocol I/O can be added
      later (using the official SDK) without touching callers.
    - All methods avoid importing optional deps so the base install stays lean.
    """

    def __init__(self) -> None:
        self._servers: Dict[str, MCPServerConnection] = {}

    # -- Connection management -------------------------------------------------
    def connect_http(self, name: str, url: str, headers: Optional[Dict[str, str]] = None) -> MCPServerConnection:
        conn = MCPServerConnection(name=name, transport='http', url=url, headers=headers or {}, connected=True)
        # Discovery is a no-op in the stub; leave tools/resources empty
        self._servers[name] = conn
        return conn

    def connect_stdio(self, name: str, cmd: str) -> MCPServerConnection:
        conn = MCPServerConnection(name=name, transport='stdio', cmd=cmd, connected=True)
        self._servers[name] = conn
        return conn

    def disconnect(self, name: str) -> None:
        if name in self._servers:
            del self._servers[name]

    def list_servers(self) -> List[MCPServerConnection]:
        return list(self._servers.values())

    def get_server(self, name: str) -> Optional[MCPServerConnection]:
        return self._servers.get(name)

    # -- Discovery -------------------------------------------------------------
    def list_tools(self, server: Optional[str] = None) -> Dict[str, List[MCPToolSpec]]:
        if server:
            conn = self._servers.get(server)
            return {server: list(conn.tools) if conn else []}
        return {name: list(conn.tools) for name, conn in self._servers.items()}

    def list_resources(self, server: Optional[str] = None) -> Dict[str, List[MCPResource]]:
        if server:
            conn = self._servers.get(server)
            return {server: list(conn.resources) if conn else []}
        return {name: list(conn.resources) for name, conn in self._servers.items()}

    # -- Invocation ------------------------------------------------------------
    def call_tool(self, server: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Proxy a tool call to the named server.

        Stub behavior: raise NotImplementedError to make the call site explicit.
        Real implementation should adapt the SDK result to a content parts list.
        """
        raise NotImplementedError("MCP client is a stub; enable real transport before calling tools.")


# -- Session helpers ----------------------------------------------------------

def get_or_create_client(session) -> MCPClient:
    """Return a session-scoped MCPClient, creating it on first use."""
    key = '__mcp_client__'
    cli = session.get_user_data(key)
    if not isinstance(cli, MCPClient):
        cli = MCPClient()
        session.set_user_data(key, cli)
    return cli

