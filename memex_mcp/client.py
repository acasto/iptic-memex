from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
import urllib.parse
import json
import urllib.request
import urllib.error
import random
import time
import asyncio
import inspect
import sys


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

    def __init__(self, *, use_sdk: bool = False, connect_timeout: float | None = None, call_timeout: float | None = None, retries: int = 0, debug: bool = False, http_fallback: bool = False) -> None:
        self._servers: Dict[str, MCPServerConnection] = {}
        self.use_sdk = use_sdk
        self.connect_timeout = float(connect_timeout) if connect_timeout is not None else 10.0
        self.call_timeout = float(call_timeout) if call_timeout is not None else 20.0
        self.retries = max(0, int(retries or 0))
        self.debug = bool(debug)
        # Opt-in generic HTTP JSON fallback (/tools, /call, /resource)
        self.http_fallback = bool(http_fallback)

    # -- Debug helper ---------------------------------------------------------
    def _debug(self, msg: str, exc: Exception | None = None) -> None:
        if not self.debug:
            return
        try:
            line = f"MCP debug: {msg}"
            if exc is not None:
                line += f" [{type(exc).__name__}: {exc}]"
            sys.stderr.write(line + "\n")
        except Exception:
            pass

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
                except Exception as e:
                    self._debug(f"SDK list_tools failed for {conn.name}", e)
            if self.http_fallback and conn.transport == 'http' and not conn.tools and conn.url:
                try:
                    tools = self._http_list_tools(conn)
                    conn.tools = tools
                except Exception as e:
                    self._debug(f"HTTP list_tools failed for {conn.name}", e)
            return {server: list(conn.tools)}
        out: Dict[str, List[MCPToolSpec]] = {}
        for name, conn in self._servers.items():
            if self._sdk_enabled() and not conn.tools:
                try:
                    conn.tools = self._sdk_list_tools(conn)
                except Exception as e:
                    self._debug(f"SDK list_tools failed for {conn.name}", e)
            if self.http_fallback and conn.transport == 'http' and not conn.tools and conn.url:
                try:
                    conn.tools = self._http_list_tools(conn)
                except Exception as e:
                    self._debug(f"HTTP list_tools failed for {conn.name}", e)
            out[name] = list(conn.tools)
        return out

    def list_resources(self, server: Optional[str] = None) -> Dict[str, List[MCPResource]]:
        if server:
            conn = self._servers.get(server)
            if not conn:
                return {server: []}
            if self._sdk_enabled() and not conn.resources:
                try:
                    conn.resources = self._sdk_list_resources(conn)
                except Exception as e:
                    self._debug(f"SDK list_resources failed for {conn.name}", e)
            if self.http_fallback and conn.transport == 'http' and not conn.resources and conn.url:
                try:
                    conn.resources = self._http_list_resources(conn)
                except Exception as e:
                    self._debug(f"HTTP list_resources failed for {conn.name}", e)
            return {server: list(conn.resources)}
        out: Dict[str, List[MCPResource]] = {}
        for name, conn in self._servers.items():
            if self._sdk_enabled() and not conn.resources:
                try:
                    conn.resources = self._sdk_list_resources(conn)
                except Exception as e:
                    self._debug(f"SDK list_resources failed for {conn.name}", e)
            if self.http_fallback and conn.transport == 'http' and not conn.resources and conn.url:
                try:
                    conn.resources = self._http_list_resources(conn)
                except Exception as e:
                    self._debug(f"HTTP list_resources failed for {conn.name}", e)
            out[name] = list(conn.resources)
        return out

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
            except Exception as e:
                self._debug(f"SDK call_tool failed for {conn.name}:{tool_name}", e)

        if self.http_fallback and conn.transport == 'http' and conn.url:
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
            except Exception as e:
                self._debug(f"SDK fetch_resource failed for {conn.name}:{uri}", e)
        if self.http_fallback and conn.transport == 'http' and conn.url:
            return self._http_fetch_resource(conn, uri)
        raise NotImplementedError("MCP client has no transport for this server.")

    # -- HTTP helpers --------------------------------------------------------
    def _http_list_tools(self, conn: MCPServerConnection) -> List[MCPToolSpec]:
        url = urljoin(conn.url.rstrip('/') + '/', 'tools')
        req = urllib.request.Request(url, headers=self._headers(conn))
        with self._urlopen_with_retries(req, timeout=self.call_timeout) as resp:
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
        with self._urlopen_with_retries(req, timeout=self.call_timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _http_fetch_resource(self, conn: MCPServerConnection, uri: str) -> Dict[str, Any]:
        base = conn.url.rstrip('/') + '/'
        url = urljoin(base, 'resource') + f"?uri={urllib.parse.quote(uri)}"
        req = urllib.request.Request(url, headers=self._headers(conn))
        with self._urlopen_with_retries(req, timeout=self.call_timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _headers(self, conn: MCPServerConnection, json_body: bool = False) -> Dict[str, str]:
        headers = dict(conn.headers or {})
        # Always prefer JSON responses
        headers.setdefault('Accept', 'application/json')
        if json_body:
            headers.setdefault('Content-Type', 'application/json')
        return headers

    # --- Retry helper -------------------------------------------------------
    def _urlopen_with_retries(self, req: urllib.request.Request, timeout: float):
        attempt = 0
        last_err = None
        while True:
            attempt += 1
            try:
                return urllib.request.urlopen(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                # Retry on 429 or 5xx
                if e.code == 429 or 500 <= e.code < 600:
                    last_err = e
                else:
                    raise
            except urllib.error.URLError as e:
                last_err = e
            except Exception as e:
                last_err = e
            if attempt > (self.retries + 1):
                # Exhausted attempts
                assert last_err is not None
                raise last_err
            # Exponential backoff with jitter
            sleep_ms = (150 * (2 ** (attempt - 1))) * (1.0 + random.random() * 0.25)
            time.sleep(sleep_ms / 1000.0)

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
        """Attempt to import SDK modules; returns a dict of constructors or raises ImportError.

        Tries multiple symbol layouts across SDK versions and logs failures in debug mode.
        """
        import importlib
        # Helper to try multiple module paths and attr names
        def _find_attr(mod_path: str, names: List[str]):
            try:
                mod = importlib.import_module(mod_path)
            except Exception as e:
                self._debug(f"import failed: {mod_path}", e)
                return None
            for n in names:
                try:
                    obj = getattr(mod, n)
                    if obj is not None:
                        return obj
                except Exception:
                    continue
            return None

        # Prefer 'mcp' package
        try:
            importlib.import_module('mcp')
            # High-level session + transport helpers
            ClientSession = (
                _find_attr('mcp', ['ClientSession'])
                or _find_attr('mcp.client', ['ClientSession'])
            )
            streamablehttp_client = _find_attr('mcp.client.streamable_http', ['streamablehttp_client'])
            stdio_client = _find_attr('mcp.client.stdio', ['stdio_client'])
            StdioServerParameters = _find_attr('mcp', ['StdioServerParameters']) or _find_attr('mcp.client.stdio', ['StdioServerParameters'])

            # If modern helpers are present, return immediately without probing legacy transports
            if ClientSession or streamablehttp_client or stdio_client:
                return {
                    'Client': None,
                    'ClientSession': ClientSession,
                    'HTTPTransport': None,
                    'StdioTransport': None,
                    'streamablehttp_client': streamablehttp_client,
                    'stdio_client': stdio_client,
                    'StdioServerParameters': StdioServerParameters,
                    'ns': 'mcp',
                }

            # Legacy Client/Transport candidates (only if modern helpers absent)
            Client = (
                _find_attr('mcp.client', ['Client', 'ClientSession'])
                or _find_attr('mcp', ['Client', 'ClientSession'])
            )
            HTTPTransport = None
            StdioTransport = None
            if Client is not None:
                HTTPTransport = _find_attr('mcp.transport.http', ['HTTPTransport', 'HTTPClientTransport']) or _find_attr('mcp.http', ['HTTPTransport', 'HTTPClientTransport']) or _find_attr('mcp.transport.sse', ['HTTPTransport', 'HTTPClientTransport'])
                StdioTransport = _find_attr('mcp.transport.stdio', ['StdioTransport', 'StdioClientTransport']) or _find_attr('mcp.stdio', ['StdioTransport', 'StdioClientTransport'])
            if (ClientSession is not None) or (Client is not None):
                return {
                    'Client': Client,
                    'ClientSession': ClientSession,
                    'HTTPTransport': HTTPTransport,
                    'StdioTransport': StdioTransport,
                    'streamablehttp_client': streamablehttp_client,
                    'stdio_client': stdio_client,
                    'StdioServerParameters': StdioServerParameters,
                    'ns': 'mcp',
                }
        except Exception as e:
            self._debug("mcp package detection failed", e)

        # Alternative package name: modelcontextprotocol
        try:
            importlib.import_module('modelcontextprotocol')
            ClientSession = (
                _find_attr('modelcontextprotocol', ['ClientSession'])
                or _find_attr('modelcontextprotocol.client', ['ClientSession'])
            )
            streamablehttp_client = _find_attr('modelcontextprotocol.client.streamable_http', ['streamablehttp_client'])
            stdio_client = _find_attr('modelcontextprotocol.client.stdio', ['stdio_client'])
            StdioServerParameters = _find_attr('modelcontextprotocol', ['StdioServerParameters']) or _find_attr('modelcontextprotocol.client.stdio', ['StdioServerParameters'])

            # If modern helpers are present, return immediately
            if ClientSession or streamablehttp_client or stdio_client:
                return {
                    'Client': None,
                    'ClientSession': ClientSession,
                    'HTTPTransport': None,
                    'StdioTransport': None,
                    'streamablehttp_client': streamablehttp_client,
                    'stdio_client': stdio_client,
                    'StdioServerParameters': StdioServerParameters,
                    'ns': 'modelcontextprotocol',
                }

            Client = (
                _find_attr('modelcontextprotocol.client', ['Client', 'ClientSession'])
                or _find_attr('modelcontextprotocol', ['Client', 'ClientSession'])
            )
            HTTPTransport = None
            StdioTransport = None
            if Client is not None:
                HTTPTransport = _find_attr('modelcontextprotocol.transport.http', ['HTTPTransport', 'HTTPClientTransport']) or _find_attr('modelcontextprotocol.http', ['HTTPTransport', 'HTTPClientTransport'])
                StdioTransport = _find_attr('modelcontextprotocol.transport.stdio', ['StdioTransport', 'StdioClientTransport'])
            if (ClientSession is None) and (Client is None):
                raise ImportError('MCP SDK Client not found')
            return {
                'Client': Client,
                'ClientSession': ClientSession,
                'HTTPTransport': HTTPTransport,
                'StdioTransport': StdioTransport,
                'streamablehttp_client': streamablehttp_client,
                'stdio_client': stdio_client,
                'StdioServerParameters': StdioServerParameters,
                'ns': 'modelcontextprotocol',
            }
        except Exception as e:
            self._debug("modelcontextprotocol package detection failed", e)
            raise

    def _sdk_ensure_session(self, conn: MCPServerConnection):
        if conn.sdk is not None:
            return conn.sdk
        handles = self._sdk_get_handles()
        Client = handles.get('Client')
        HTTPTransport = handles.get('HTTPTransport')
        StdioTransport = handles.get('StdioTransport')
        # Build a client based on transport
        if Client is None:
            raise RuntimeError('SDK persistent client unavailable for this SDK')
        if conn.transport == 'http' and conn.url and HTTPTransport is not None:
            client = Client(transport=HTTPTransport(conn.url, headers=conn.headers or {}))
        elif conn.transport == 'http' and conn.url:
            # Fallback constructors for SDKs that don't expose HTTPTransport explicitly
            made = False
            # Factory classmethod patterns
            for factory_name in ('from_http', 'from_url', 'connect_http'):
                fn = getattr(Client, factory_name, None)
                if callable(fn):
                    try:
                        client = fn(conn.url, headers=conn.headers or {})
                        made = True
                        break
                    except Exception as e:
                        self._debug(f"SDK Client.{factory_name} failed for {conn.name}", e)
            if not made:
                # Try direct kwargs on constructor
                ctor_kwargs_candidates = (
                    {'url': conn.url, 'headers': conn.headers or {}},
                    {'base_url': conn.url, 'headers': conn.headers or {}},
                    {'endpoint': conn.url, 'headers': conn.headers or {}},
                    {'transport': 'http', 'url': conn.url, 'headers': conn.headers or {}},
                )
                for kw in ctor_kwargs_candidates:
                    try:
                        client = Client(**kw)
                        made = True
                        break
                    except Exception as e:
                        self._debug(f"SDK Client(**{list(kw.keys())}) failed for {conn.name}", e)
            if not made:
                raise RuntimeError('SDK transport unavailable for this connection')
        elif conn.transport == 'stdio' and conn.cmd and StdioTransport is not None:
            client = Client(transport=StdioTransport(command=conn.cmd))
        else:
            # Transport not supported by SDK
            raise RuntimeError('SDK transport unavailable for this connection')
        # Some clients may need an explicit connect/init
        try:
            if hasattr(client, 'connect'):
                res = client.connect()
                # Handle async connect()
                if inspect.iscoroutine(res):
                    asyncio.run(res)
        except Exception as e:
            self._debug(f"SDK connect failed for {conn.name}", e)
        conn.sdk = client
        return client

    def _sdk_list_tools(self, conn: MCPServerConnection) -> List[MCPToolSpec]:
        # Prefer one-shot streamable HTTP session for http transport
        if conn.transport == 'http' and conn.url:
            try:
                handles = self._sdk_get_handles()
                sh = handles.get('streamablehttp_client')
                CS = handles.get('ClientSession')
                if sh and CS:
                    async def run_once():
                        async with sh(conn.url) as (read, write, _):
                            async with CS(read, write) as session:
                                if hasattr(session, 'initialize'):
                                    await session.initialize()
                                return await session.list_tools()
                    resp = asyncio.run(run_once())
                    return self._parse_tools_response(resp)
            except Exception as e:
                self._debug(f"SDK (streamable-http) list_tools error for {conn.name}", e)

        # Persistent client path
        client = self._sdk_ensure_session(conn)
        resp = None
        try:
            method = None
            for name in ('list_tools', 'get_tools'):
                if hasattr(client, name):
                    method = getattr(client, name)
                    break
            if callable(method):
                res = method()
                resp = asyncio.run(res) if inspect.iscoroutine(res) else res
            else:
                # Property or attribute
                resp = getattr(client, 'tools', None)
        except Exception as e:
            self._debug(f"SDK list_tools call error for {conn.name}", e)
        if resp is None:
            return []
        return self._parse_tools_response(resp)

    def _parse_tools_response(self, resp: Any) -> List[MCPToolSpec]:
        tools: List[MCPToolSpec] = []
        items = resp if isinstance(resp, list) else (resp.get('tools') if isinstance(resp, dict) else getattr(resp, 'tools', None))
        items = items or []
        for t in items:
            try:
                name = t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)
                desc = t.get('description') if isinstance(t, dict) else getattr(t, 'description', '')
                schema = (
                    (t.get('inputSchema') if isinstance(t, dict) else getattr(t, 'inputSchema', None))
                    or (t.get('input_schema') if isinstance(t, dict) else getattr(t, 'input_schema', None))
                    or {}
                )
                if name:
                    tools.append(MCPToolSpec(name=name, description=desc or '', input_schema=schema or {}))
            except Exception:
                continue
        return tools

    def _sdk_call_tool(self, conn: MCPServerConnection, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Prefer streamable HTTP one-shot for http transport
        if conn.transport == 'http' and conn.url:
            try:
                handles = self._sdk_get_handles()
                sh = handles.get('streamablehttp_client')
                CS = handles.get('ClientSession')
                if sh and CS:
                    async def run_once():
                        async with sh(conn.url) as (read, write, _):
                            async with CS(read, write) as session:
                                if hasattr(session, 'initialize'):
                                    await session.initialize()
                                return await session.call_tool(tool_name, arguments)
                    return asyncio.run(run_once())
            except Exception as e:
                self._debug(f"SDK (streamable-http) call_tool error for {conn.name}", e)
        # Persistent client path
        client = self._sdk_ensure_session(conn)
        if hasattr(client, 'call_tool'):
            res = client.call_tool(tool_name, arguments)  # type: ignore
            return asyncio.run(res) if inspect.iscoroutine(res) else res
        fn = getattr(client, 'call', None)
        if callable(fn):
            res = fn(tool_name, arguments)
            return asyncio.run(res) if inspect.iscoroutine(res) else res
        raise RuntimeError('SDK client does not support tool calls')

    def _sdk_fetch_resource(self, conn: MCPServerConnection, uri: str) -> Dict[str, Any]:
        # Prefer streamable HTTP one-shot for http transport
        if conn.transport == 'http' and conn.url:
            try:
                handles = self._sdk_get_handles()
                sh = handles.get('streamablehttp_client')
                CS = handles.get('ClientSession')
                if sh and CS:
                    async def run_once():
                        async with sh(conn.url) as (read, write, _):
                            async with CS(read, write) as session:
                                if hasattr(session, 'initialize'):
                                    await session.initialize()
                                if hasattr(session, 'read_resource'):
                                    return await session.read_resource(uri)
                                if hasattr(session, 'get_resource'):
                                    return await session.get_resource(uri)
                                raise RuntimeError('SDK client session does not support resource fetch')
                    return asyncio.run(run_once())
            except Exception as e:
                self._debug(f"SDK (streamable-http) fetch_resource error for {conn.name}", e)
        # Persistent client path
        client = self._sdk_ensure_session(conn)
        if hasattr(client, 'get_resource'):
            res = client.get_resource(uri)  # type: ignore
            return asyncio.run(res) if inspect.iscoroutine(res) else res
        fn = getattr(client, 'fetch_resource', None)
        if callable(fn):
            res = fn(uri)
            return asyncio.run(res) if inspect.iscoroutine(res) else res
        raise RuntimeError('SDK client does not support resource fetch')

    def _sdk_list_resources(self, conn: MCPServerConnection) -> List[MCPResource]:
        # Prefer one-shot streamable HTTP session for http transport
        if conn.transport == 'http' and conn.url:
            try:
                handles = self._sdk_get_handles()
                sh = handles.get('streamablehttp_client')
                CS = handles.get('ClientSession')
                if sh and CS:
                    async def run_once():
                        async with sh(conn.url) as (read, write, _):
                            async with CS(read, write) as session:
                                if hasattr(session, 'initialize'):
                                    await session.initialize()
                                return await session.list_resources()
                    resp = asyncio.run(run_once())
                    return self._parse_resources_response(resp)
            except Exception as e:
                self._debug(f"SDK (streamable-http) list_resources error for {conn.name}", e)

        client = self._sdk_ensure_session(conn)
        resources: List[MCPResource] = []
        resp = None
        # Try common shapes: list_resources(), get_resources(), resources attribute
        try:
            if hasattr(client, 'list_resources'):
                tmp = client.list_resources()
                resp = asyncio.run(tmp) if inspect.iscoroutine(tmp) else tmp
        except Exception as e:
            self._debug(f"SDK list_resources call error for {conn.name}", e)
            resp = None
        if resp is None:
            try:
                if hasattr(client, 'get_resources'):
                    tmp = client.get_resources()
                    resp = asyncio.run(tmp) if inspect.iscoroutine(tmp) else tmp
            except Exception as e:
                self._debug(f"SDK get_resources call error for {conn.name}", e)
                resp = None
        if resp is None:
            # attribute
            try:
                resp = getattr(client, 'resources', None)
            except Exception:
                resp = None
        items = resp if isinstance(resp, list) else (resp.get('resources') if isinstance(resp, dict) else getattr(resp, 'resources', None))
        for r in items:
            try:
                if isinstance(r, dict):
                    uri = r.get('uri') or ''
                    title = r.get('title') or r.get('name')
                    mime = r.get('mimeType') or r.get('mime_type')
                else:
                    uri = getattr(r, 'uri', '')
                    title = getattr(r, 'title', getattr(r, 'name', None))
                    mime = getattr(r, 'mimeType', getattr(r, 'mime_type', None))
                if uri:
                    resources.append(MCPResource(uri=uri, title=title, mime_type=mime))
            except Exception:
                continue
        return resources

    def _parse_resources_response(self, resp: Any) -> List[MCPResource]:
        items = resp if isinstance(resp, list) else (resp.get('resources') if isinstance(resp, dict) else getattr(resp, 'resources', None))
        items = items or []
        out: List[MCPResource] = []
        for r in items:
            try:
                if isinstance(r, dict):
                    uri = r.get('uri') or ''
                    title = r.get('title') or r.get('name')
                    mime = r.get('mimeType') or r.get('mime_type')
                else:
                    uri = getattr(r, 'uri', '')
                    title = getattr(r, 'title', getattr(r, 'name', None))
                    mime = getattr(r, 'mimeType', getattr(r, 'mime_type', None))
                if uri:
                    out.append(MCPResource(uri=uri, title=title, mime_type=mime))
            except Exception:
                continue
        return out

    def _http_list_resources(self, conn: MCPServerConnection) -> List[MCPResource]:
        url = urljoin(conn.url.rstrip('/') + '/', 'resources')
        req = urllib.request.Request(url, headers=self._headers(conn))
        with self._urlopen_with_retries(req, timeout=self.call_timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        items = data if isinstance(data, list) else (data.get('resources') or [])
        out: List[MCPResource] = []
        for r in items:
            uri = r.get('uri') if isinstance(r, dict) else getattr(r, 'uri', None)
            if not uri:
                continue
            title = r.get('title') if isinstance(r, dict) else getattr(r, 'title', None)
            if title is None:
                title = r.get('name') if isinstance(r, dict) else getattr(r, 'name', None)
            mime = r.get('mimeType') if isinstance(r, dict) else getattr(r, 'mimeType', getattr(r, 'mime_type', None))
            out.append(MCPResource(uri=str(uri), title=title, mime_type=mime))
        return out


# -- Session helpers ----------------------------------------------------------

def get_or_create_client(session) -> MCPClient:
    """Return a session-scoped MCPClient, creating it on first use.

    Respects [MCP].use_sdk flag when instantiating the client.
    """
    key = '__mcp_client__'
    cli = session.get_user_data(key)
    if not isinstance(cli, MCPClient):
        use_sdk = False
        connect_timeout = None
        call_timeout = None
        retries = 0
        try:
            use_sdk = bool(session.get_option('MCP', 'use_sdk', fallback=False))
        except Exception:
            use_sdk = False
        # Optional flags
        debug = False
        http_fallback = False
        try:
            val = session.get_option('MCP', 'debug', fallback=False)
            debug = (str(val).strip().lower() in ('1', 'true', 'yes', 'on')) if not isinstance(val, bool) else bool(val)
        except Exception:
            debug = False
        try:
            val = session.get_option('MCP', 'http_fallback', fallback=False)
            http_fallback = (str(val).strip().lower() in ('1', 'true', 'yes', 'on')) if not isinstance(val, bool) else bool(val)
        except Exception:
            http_fallback = False
        # Parse durations like '10s' or plain numbers
        def _parse_seconds(val) -> Optional[float]:
            if val is None:
                return None
            try:
                s = str(val).strip().lower()
                if s.endswith('ms'):
                    return float(s[:-2]) / 1000.0
                if s.endswith('s'):
                    return float(s[:-1])
                if s.endswith('m'):
                    return float(s[:-1]) * 60.0
                return float(s)
            except Exception:
                return None
        try:
            connect_timeout = _parse_seconds(session.get_option('MCP', 'connect_timeout', fallback=None))
        except Exception:
            connect_timeout = None
        try:
            call_timeout = _parse_seconds(session.get_option('MCP', 'call_timeout', fallback=None))
        except Exception:
            call_timeout = None
        try:
            retries = int(session.get_option('MCP', 'retries', fallback=0) or 0)
        except Exception:
            retries = 0

        cli = MCPClient(use_sdk=use_sdk, connect_timeout=connect_timeout, call_timeout=call_timeout, retries=retries, debug=debug, http_fallback=http_fallback)
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
