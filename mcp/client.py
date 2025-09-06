from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import json
import urllib.request
import urllib.error


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
            if not conn:
                return {server: []}
            # Lazy HTTP discovery when tools empty
            if conn.transport == 'http' and not conn.tools and conn.url:
                try:
                    tools = self._http_list_tools(conn)
                    conn.tools = tools
                except Exception:
                    pass
            return {server: list(conn.tools)}
        out: Dict[str, List[MCPToolSpec]] = {}
        for name, conn in self._servers.items():
            if conn.transport == 'http' and not conn.tools and conn.url:
                try:
                    conn.tools = self._http_list_tools(conn)
                except Exception:
                    pass
            out[name] = list(conn.tools)
        return out

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
        conn = self._servers.get(server)
        if not conn:
            raise RuntimeError(f"Unknown MCP server: {server}")

        # Demo local behavior
        if conn.url and 'demo.local' in conn.url:
            if tool_name == 'calc.sum':
                a = float(arguments.get('a', 0))
                b = float(arguments.get('b', 0))
                return {'content': [{'type': 'json', 'value': {'result': a + b}}]}
            if tool_name == 'echo.say':
                return {'content': [{'type': 'text', 'text': str(arguments.get('text', ''))}]}
            return {'content': [{'type': 'text', 'text': 'demo: tool not implemented'}]}

        if conn.transport == 'http' and conn.url:
            return self._http_call_tool(conn, tool_name, arguments)

        raise NotImplementedError("MCP client has no transport for this server.")

    def fetch_resource(self, server: str, uri: str) -> Dict[str, Any]:
        conn = self._servers.get(server)
        if not conn:
            raise RuntimeError(f"Unknown MCP server: {server}")
        # Demo
        if conn.url and 'demo.local' in conn.url:
            if uri == 'guides/welcome':
                return {
                    'name': 'welcome.txt',
                    'content': 'Welcome to the demo MCP server! This is sample content.',
                    'metadata': {'source': f'mcp:{server}', 'uri': uri}
                }
            return {'name': uri, 'content': '', 'metadata': {'source': f'mcp:{server}', 'uri': uri}}
        if conn.transport == 'http' and conn.url:
            return self._http_fetch_resource(conn, uri)
        raise NotImplementedError("MCP client has no transport for this server.")

    # -- HTTP helpers --------------------------------------------------------
    def _http_list_tools(self, conn: MCPServerConnection) -> List[MCPToolSpec]:
        url = urljoin(conn.url.rstrip('/') + '/', 'tools')
        req = urllib.request.Request(url, headers=self._headers(conn))
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        tools = []
        for t in data if isinstance(data, list) else (data.get('tools') or []):
            name = t.get('name')
            desc = t.get('description', '')
            schema = t.get('inputSchema') or t.get('input_schema') or {}
            if name:
                tools.append(MCPToolSpec(name=name, description=desc, input_schema=schema))
        return tools

    def _http_call_tool(self, conn: MCPServerConnection, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url = urljoin(conn.url.rstrip('/') + '/', 'call')
        body = json.dumps({'tool': tool_name, 'arguments': arguments}).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers=self._headers(conn, json_body=True), method='POST')
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _http_fetch_resource(self, conn: MCPServerConnection, uri: str) -> Dict[str, Any]:
        base = conn.url.rstrip('/') + '/'
        url = urljoin(base, 'resource') + f"?uri={urllib.parse.quote(uri)}"
        req = urllib.request.Request(url, headers=self._headers(conn))
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _headers(self, conn: MCPServerConnection, json_body: bool = False) -> Dict[str, str]:
        headers = dict(conn.headers or {})
        if json_body:
            headers.setdefault('Content-Type', 'application/json')
            headers.setdefault('Accept', 'application/json')
        return headers


# -- Session helpers ----------------------------------------------------------

def get_or_create_client(session) -> MCPClient:
    """Return a session-scoped MCPClient, creating it on first use."""
    key = '__mcp_client__'
    cli = session.get_user_data(key)
    if not isinstance(cli, MCPClient):
        cli = MCPClient()
        session.set_user_data(key, cli)
    return cli


# --- Demo helper ------------------------------------------------------------

def inject_demo_server(client: MCPClient, name: str = 'testmcp') -> MCPServerConnection:
    """Create a demo MCP server with a couple of tools/resources for local tests.

    Tools added:
      - calc.sum: {a:number, b:number}
      - echo.say: {text:string}
    """
    conn = MCPServerConnection(name=name, transport='http', url='https://demo.local', connected=True)
    conn.tools = [
        MCPToolSpec(
            name='calc.sum',
            description='Return the sum of two numbers.',
            input_schema={'type': 'object', 'additionalProperties': False, 'required': ['a', 'b'], 'properties': {
                'a': {'type': 'number'},
                'b': {'type': 'number'},
            }},
        ),
        MCPToolSpec(
            name='echo.say',
            description='Echo back provided text.',
            input_schema={'type': 'object', 'additionalProperties': False, 'required': ['text'], 'properties': {
                'text': {'type': 'string'},
            }},
        ),
    ]
    conn.resources = [MCPResource(uri='guides/welcome', title='Welcome', mime_type='text/plain')]
    client._servers[name] = conn
    return conn
