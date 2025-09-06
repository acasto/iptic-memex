from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import urllib.parse
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
    sdk: Any = field(default=None, repr=False)


class MCPClient:
    """Session-scoped minimal MCP client facade.

    Notes:
    - This is a stub that records configured servers and allows the app to
      develop against a stable API surface. Real protocol I/O can be added
      later (using the official SDK) without touching callers.
    - All methods avoid importing optional deps so the base install stays lean.
    """

    def __init__(self, *, use_sdk: bool = False) -> None:
        self._servers: Dict[str, MCPServerConnection] = {}
        self.use_sdk = use_sdk

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
            if self._sdk_enabled() and not conn.tools:
                try:
                    conn.tools = self._sdk_list_tools(conn)
                except Exception:
                    pass
            if conn.transport == 'http' and not conn.tools and conn.url:
                try:
                    tools = self._http_list_tools(conn)
                    conn.tools = tools
                except Exception:
                    pass
            return {server: list(conn.tools)}
        out: Dict[str, List[MCPToolSpec]] = {}
        for name, conn in self._servers.items():
            if self._sdk_enabled() and not conn.tools:
                try:
                    conn.tools = self._sdk_list_tools(conn)
                except Exception:
                    pass
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

        if self._sdk_enabled():
            try:
                return self._sdk_call_tool(conn, tool_name, arguments)
            except Exception:
                pass

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
        if self._sdk_enabled():
            try:
                return self._sdk_fetch_resource(conn, uri)
            except Exception:
                pass
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

    # -- SDK helpers (optional) ----------------------------------------------
    def _sdk_enabled(self) -> bool:
        if not self.use_sdk:
            return False
        try:
            import importlib
            # Prefer official python SDK if installed under either name
            return importlib.util.find_spec('mcp') is not None or importlib.util.find_spec('modelcontextprotocol') is not None
        except Exception:
            return False

    def _sdk_get_handles(self):
        """Attempt to import SDK modules; returns a dict of constructors or raises ImportError."""
        # Try modern layout first
        try:
            import mcp
            from mcp.client import Client as MCPClientSDK  # type: ignore
            try:
                from mcp.transport.http import HTTPTransport  # type: ignore
            except Exception:
                HTTPTransport = None
            try:
                from mcp.transport.stdio import StdioTransport  # type: ignore
            except Exception:
                StdioTransport = None
            return {'Client': MCPClientSDK, 'HTTPTransport': HTTPTransport, 'StdioTransport': StdioTransport, 'ns': 'mcp'}
        except Exception:
            pass
        # Alternative package name
        import importlib
        m = importlib.import_module('modelcontextprotocol')  # type: ignore
        # Best-effort: attempt to locate likely symbols
        Client = getattr(getattr(m, 'client', m), 'Client', None)
        HTTPTransport = getattr(getattr(getattr(m, 'transport', None), 'http', None), 'HTTPTransport', None)
        StdioTransport = getattr(getattr(getattr(m, 'transport', None), 'stdio', None), 'StdioTransport', None)
        if Client is None:
            raise ImportError('MCP SDK Client not found')
        return {'Client': Client, 'HTTPTransport': HTTPTransport, 'StdioTransport': StdioTransport, 'ns': 'modelcontextprotocol'}

    def _sdk_ensure_session(self, conn: MCPServerConnection):
        if conn.sdk is not None:
            return conn.sdk
        handles = self._sdk_get_handles()
        Client = handles['Client']
        HTTPTransport = handles.get('HTTPTransport')
        StdioTransport = handles.get('StdioTransport')
        # Build a client based on transport
        if conn.transport == 'http' and conn.url and HTTPTransport is not None:
            client = Client(transport=HTTPTransport(conn.url, headers=conn.headers or {}))
        elif conn.transport == 'stdio' and conn.cmd and StdioTransport is not None:
            client = Client(transport=StdioTransport(command=conn.cmd))
        else:
            # Transport not supported by SDK
            raise RuntimeError('SDK transport unavailable for this connection')
        # Some clients may need an explicit connect/init
        try:
            if hasattr(client, 'connect'):
                client.connect()
        except Exception:
            pass
        conn.sdk = client
        return client

    def _sdk_list_tools(self, conn: MCPServerConnection) -> List[MCPToolSpec]:
        client = self._sdk_ensure_session(conn)
        # Try common method names
        tools = []
        resp = None
        try:
            if hasattr(client, 'list_tools'):
                resp = client.list_tools()
        except Exception:
            resp = None
        if resp is None:
            return []
        items = resp if isinstance(resp, list) else (resp.get('tools') if isinstance(resp, dict) else [])
        for t in items:
            try:
                name = t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)
                desc = t.get('description') if isinstance(t, dict) else getattr(t, 'description', '')
                schema = t.get('inputSchema') if isinstance(t, dict) else getattr(t, 'input_schema', {})
                if name:
                    tools.append(MCPToolSpec(name=name, description=desc or '', input_schema=schema or {}))
            except Exception:
                continue
        return tools

    def _sdk_call_tool(self, conn: MCPServerConnection, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        client = self._sdk_ensure_session(conn)
        # Common method names: call_tool(name, args) → returns dict with 'content'
        if hasattr(client, 'call_tool'):
            return client.call_tool(tool_name, arguments)  # type: ignore
        # Fallback; try an attribute
        fn = getattr(client, 'call', None)
        if callable(fn):
            return fn(tool_name, arguments)
        raise RuntimeError('SDK client does not support tool calls')

    def _sdk_fetch_resource(self, conn: MCPServerConnection, uri: str) -> Dict[str, Any]:
        client = self._sdk_ensure_session(conn)
        # Common method names: get_resource(uri) → {'name','content','metadata'}
        if hasattr(client, 'get_resource'):
            return client.get_resource(uri)  # type: ignore
        # Alternative: fetch_resource
        fn = getattr(client, 'fetch_resource', None)
        if callable(fn):
            return fn(uri)
        raise RuntimeError('SDK client does not support resource fetch')


# -- Session helpers ----------------------------------------------------------

def get_or_create_client(session) -> MCPClient:
    """Return a session-scoped MCPClient, creating it on first use.

    Respects [MCP].use_sdk flag when instantiating the client.
    """
    key = '__mcp_client__'
    cli = session.get_user_data(key)
    if not isinstance(cli, MCPClient):
        use_sdk = False
        try:
            use_sdk = bool(session.get_option('MCP', 'use_sdk', fallback=False))
        except Exception:
            use_sdk = False
        cli = MCPClient(use_sdk=use_sdk)
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
