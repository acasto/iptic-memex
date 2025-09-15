from __future__ import annotations

import json
import time
import uuid
from base_classes import InteractionAction
from utils.tool_args import get_str
from memex_mcp.client import get_or_create_client


class McpProxyToolAction(InteractionAction):
    """Generic MCP proxy tool.

    This presents a single callable tool (`mcp`) that forwards a call to a named
    MCP server + tool with JSON arguments. It is a bridging option until we add
    per-tool dynamic registration. Hidden unless `[MCP].active=true`.
    """

    def __init__(self, session):
        self.session = session

    # ---- Dynamic tool registry metadata ------------------------------------
    @classmethod
    def tool_name(cls) -> str:
        return 'mcp'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def can_run(cls, session) -> bool:
        return bool(session.get_option('MCP', 'active', fallback=False))

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['server', 'tool', 'args', 'transport', 'url', 'cmd'],
            'description': (
                "Proxy a call to an MCP server tool. Provide 'server' and 'tool'. "
                "Pass JSON in 'args' or freeform content (JSON) as the body."
            ),
            'required': ['server', 'tool'],
            'schema': {
                'properties': {
                    'server': {"type": "string", "description": "Connected MCP server name."},
                    'tool': {"type": "string", "description": "Tool name on the server."},
                    'args': {"type": "string", "description": "JSON-encoded arguments for the tool."},
                    'transport': {"type": "string", "description": "When connecting inline: 'http' or 'stdio'."},
                    'url': {"type": "string", "description": "HTTP URL to connect (when inline)."},
                    'cmd': {"type": "string", "description": "Command to run (when inline stdio)."},
                    'content': {"type": "string", "description": "Optional JSON body (alternative to 'args')."},
                }
            },
            'auto_submit': True,
        }

    def run(self, args: dict, content: str = ""):
        # Inline connect (optional) for convenience
        transport = (get_str(args, 'transport') or '').lower()
        server = get_str(args, 'server')
        tool = get_str(args, 'tool')
        url = get_str(args, 'url')
        cmd = get_str(args, 'cmd')

        if not server or not tool:
            self._emit("error", "MCP: 'server' and 'tool' are required.")
            return

        client = get_or_create_client(self.session)

        if transport in ('http', 'stdio') and ((transport == 'http' and url) or (transport == 'stdio' and cmd)):
            if transport == 'http':
                client.connect_http(server, url)
            else:
                client.connect_stdio(server, cmd)

        # Parse arguments
        base_args = dict(args or {})
        # Reserved keys for the proxy plumbing
        reserved = {'server', 'tool', 'transport', 'url', 'cmd'}
        # JSON string 'args' takes precedence when provided
        payload = get_str(base_args, 'args')
        call_args = None
        if payload:
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    call_args = parsed
                else:
                    raise ValueError('args must be a JSON object')
            except Exception as e:
                self._emit("error", f"MCP: invalid JSON in args/content: {e}")
                return
        # If no JSON payload, build from provided named arguments
        if call_args is None:
            call_args = {k: v for k, v in base_args.items() if k not in reserved and k != 'args'}

        # Optional: validate against input_schema when available
        schema = None
        try:
            tool_map = client.list_tools(server) or {}
            tools = tool_map.get(server) or []
            for t in tools:
                if getattr(t, 'name', None) == tool:
                    schema = getattr(t, 'input_schema', None)
                    break
        except Exception:
            schema = None
        if schema and isinstance(schema, dict):
            try:
                # jsonschema is optional; validate only if available
                from jsonschema import Draft202012Validator  # type: ignore
                # Convenience mapping: if a single required string property exists and not provided,
                # accept 'content' fallback
                try:
                    props = schema.get('properties') or {}
                    req = list(schema.get('required') or [])
                    required_single = req[0] if len(req) == 1 else None
                    if required_single and required_single not in call_args:
                        p = props.get(required_single) if isinstance(props, dict) else None
                        if isinstance(p, dict) and p.get('type') == 'string':
                            cval = content
                            if isinstance(cval, str) and cval.strip():
                                call_args[required_single] = cval.strip()
                except Exception:
                    pass
                Draft202012Validator(schema).validate(call_args)
            except Exception as ve:
                self._emit("error", f"MCP: BadRequest - {ve}")
                return

        # Attempt call (stub may raise until real transport is wired). Measure time.
        started = time.time()
        corr_id = str(uuid.uuid4())
        try:
            result = client.call_tool(server, tool, call_args)
        except NotImplementedError:
            # Return a structured placeholder so downstream still gets a tool message
            result = {
                'content': [
                    {"type": "text", "text": f"MCP stub: would call {server}:{tool} with {call_args}"}
                ]
            }
        except Exception as e:
            elapsed_ms = int((time.time() - started) * 1000)
            self._emit("error", f"MCP call failed ({server}:{tool}) in {elapsed_ms} ms: {e}")
            return
        elapsed_ms = int((time.time() - started) * 1000)

        # Centralized logging at detail level
        try:
            self.session.utils.logger.mcp_detail('call_ok', {
                'server': server,
                'tool': tool,
                'elapsed_ms': elapsed_ms,
            }, component='mcp.proxy')
        except Exception:
            pass

        # Format result into a readable string for the chat tool message
        out_text = self._stringify_tool_result(result)

        # Add assistant context with provenance (TurnRunner will convert this to a 'tool' message)
        self.session.add_context('assistant', {
            'name': f"mcp:{server}/{tool}",
            'content': out_text,
            'metadata': {
                'source': f'mcp:{server}/{tool}',
                'server': server,
                'tool': tool,
                'elapsed_ms': elapsed_ms,
                'correlation_id': corr_id,
            }
        })

    # --- helpers ------------------------------------------------------------
    def _emit(self, level: str, msg: str):
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

    def _stringify_tool_result(self, result) -> str:
        """Best-effort conversion of MCP tool result to a compact text string.

        Handles dict-like results, SDK CallToolResult objects, and generic objects.
        """
        try:
            if result is None:
                return 'OK'
            if isinstance(result, str):
                return result
            # Dict path (HTTP fallback or SDK dict result)
            if isinstance(result, dict):
                # Content-first rendering
                content = result.get('content')
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        try:
                            if isinstance(item, dict):
                                itype = item.get('type')
                                if itype == 'text':
                                    parts.append(str(item.get('text') or ''))
                                elif itype in ('json', 'object'):
                                    val = item.get('value')
                                    parts.append(json.dumps(val, ensure_ascii=False))
                                elif itype in ('image', 'image_base64', 'image_url'):
                                    parts.append('[image]')
                                elif itype in ('resource', 'embedded_resource'):
                                    res = item.get('resource') or {}
                                    uri = res.get('uri') if isinstance(res, dict) else None
                                    parts.append(f"[resource {uri or ''}]")
                                else:
                                    parts.append(json.dumps(item, ensure_ascii=False))
                            else:
                                parts.append(str(item))
                        except Exception:
                            continue
                    if parts:
                        return "\n".join(p for p in parts if p)
                # Fallback to JSON dump of the dict
                return json.dumps(result, ensure_ascii=False)

            # SDK object path: look for a 'content' attribute
            content = getattr(result, 'content', None)
            if isinstance(content, list):
                parts = []
                for item in content:
                    try:
                        txt = getattr(item, 'text', None)
                        if isinstance(txt, str):
                            parts.append(txt)
                            continue
                        val = getattr(item, 'value', None)
                        if val is not None:
                            parts.append(json.dumps(val, ensure_ascii=False))
                            continue
                        uri = getattr(getattr(item, 'resource', None), 'uri', None)
                        if isinstance(uri, str) and uri:
                            parts.append(f"[resource {uri}]")
                            continue
                        # Fallback to str
                        parts.append(str(item))
                    except Exception:
                        continue
                if parts:
                    return "\n".join(p for p in parts if p)

            # Generic fallback
            try:
                return json.dumps(result, default=lambda o: getattr(o, '__dict__', str(o)), ensure_ascii=False)
            except Exception:
                return str(result)
        except Exception:
            return 'OK'
