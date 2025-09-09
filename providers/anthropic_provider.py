import os
from time import time
from dataclasses import dataclass
from typing import List, Dict, Any, Generator
from anthropic import Anthropic
from base_classes import APIProvider


@dataclass
class Usage:
    """Tracks token usage and caching metrics for API calls"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_writes: int = 0
    cache_hits: int = 0
    time_elapsed: float = 0.0

    def update(self, other: 'Usage'):
        """Update this usage with values from another Usage object"""
        if other.input_tokens is not None:
            self.input_tokens += other.input_tokens
        if other.output_tokens is not None:
            self.output_tokens += other.output_tokens
        if other.cache_writes is not None:
            self.cache_writes += other.cache_writes
        if other.cache_hits is not None:
            self.cache_hits += other.cache_hits
        if other.time_elapsed is not None:
            self.time_elapsed += other.time_elapsed


class AnthropicProvider(APIProvider):
    def __init__(self, session):
        self.session = session
        self.client = self._initialize_client()
        self.current_usage = Usage()
        self.total_usage = Usage()
        self._last_response = None
        self._last_stream_tool_calls = None
        self._tool_api_to_cmd: Dict[str, str] = {}

        self.parameters = {
            'model', 'max_tokens', 'system', 'messages', 'stop_sequences',
            'metadata', 'stream', 'temperature', 'top_k', 'top_p',
            'tools', 'tool_choice', 'mcp_servers'
        }

    @staticmethod
    def supports_mcp_passthrough() -> bool:
        """Anthropic Messages API supports pass-through MCP (beta)."""
        return True

    def _initialize_client(self) -> Anthropic:
        """Initialize Anthropic client with current connection parameters"""
        params = self.session.get_params()
        
        options = {}
        api_key = params.get('api_key') or os.getenv('ANTHROPIC_API_KEY')
        if api_key:
            options['api_key'] = api_key
        else:
            options['api_key'] = 'none'

        base_url = params.get('base_url')
        if base_url:
            options['base_url'] = base_url

        return Anthropic(**options)

    def _build_system_content(self) -> List[Dict[str, Any]]:
        system_blocks = []
        prompt_ctx = self.session.get_context('prompt')
        if prompt_ctx:
            content = prompt_ctx.get().get('content')
            block = None
            if content:
                block = {
                    'type': 'text',
                    'text': content
                }
            if block and self._is_caching_enabled():
                block['cache_control'] = {"type": "ephemeral"}
            if block:
                system_blocks.append(block)

        return system_blocks

    def _build_messages(self) -> List[Dict[str, Any]]:
        messages = []
        chat = self.session.get_context('chat')

        if not chat:
            return messages

        chat_turns = chat.get()
        for i, turn in enumerate(chat_turns):
            role = turn.get('role')
            content_blocks = []
            context = turn.get('context')

            # Special handling for tool plumbing
            # 1) Assistant tool calls: encode as tool_use blocks
            if role == 'assistant' and 'tool_calls' in turn:
                try:
                    # Include any processed context first (as text)
                    if context:
                        processed_context = self._process_context(context)
                        if processed_context:
                            content_blocks.append({'type': 'text', 'text': processed_context})
                except Exception:
                    pass
                try:
                    for tc in (turn.get('tool_calls') or []):
                        content_blocks.append({
                            'type': 'tool_use',
                            'id': tc.get('id'),
                            'name': tc.get('name'),
                            'input': tc.get('arguments') or {}
                        })
                except Exception:
                    pass
                if content_blocks:
                    messages.append({'role': 'assistant', 'content': content_blocks})
                continue

            # 2) Tool results: role must be 'user' with tool_result block
            if role == 'tool':
                try:
                    tool_id = turn.get('tool_call_id') or turn.get('id')
                    messages.append({
                        'role': 'user',
                        'content': [{
                            'type': 'tool_result',
                            'tool_use_id': tool_id,
                            'content': turn.get('message') or ''
                        }]
                    })
                except Exception:
                    pass
                continue

            # Normal role: user/assistant text + contexts/images
            if context:
                # Process contexts and handle images
                for ctx in context:
                    if ctx['type'] == 'image':
                        img_data = ctx['context'].get()
                        content_blocks.append({
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': img_data['mime_type'],
                                'data': img_data['content']
                            }
                        })
                    else:
                        # Accumulate non-image contexts for text processing
                        processed_context = self._process_context([ctx])
                        if processed_context:
                            content_blocks.append({
                                'type': 'text',
                                'text': processed_context
                            })

            message = turn.get('message')
            is_last_turn = (i == len(chat_turns) - 1)

            # Handle the message block if present
            if message:
                block = {
                    'type': 'text',
                    'text': message
                }
                if is_last_turn and self._is_caching_enabled():
                    block['cache_control'] = {"type": "ephemeral"}
                content_blocks.append(block)
            # If no message but last turn, add cache control to last context block
            elif is_last_turn and self._is_caching_enabled() and content_blocks:
                content_blocks[-1]['cache_control'] = {"type": "ephemeral"}

            # Add the turn to messages if we have any content blocks
            if content_blocks:
                messages.append({
                    'role': role,
                    'content': content_blocks
                })

        return messages

    @staticmethod
    def _process_context(context: Any) -> str:
        try:
            if isinstance(context, str):
                import ast
                context_list = ast.literal_eval(context)
            else:
                context_list = context

            if not isinstance(context_list, list):
                return str(context)

            turn_context = ""
            is_project = False
            for f in context_list:
                if f['type'] == 'raw':
                    turn_context += f['context'].get()['content']
                elif f['type'] != 'project':
                    file = f['context'].get()
                    turn_context += f"<|file:{file['name']}|>\n{file['content']}\n<|end_file:{file['name']}|>\n"
                else:
                    is_project = True
                    project = f['context'].get()
                    turn_context += f"<|project_notes|>\nProject Name: {project['name']}\nProject Notes: {project['content']}\n<|end_project_notes|>\n"

            if is_project:
                turn_context = "<|project_context>" + turn_context + "<|end_project_context|>"
            return turn_context

        except Exception:
            return str(context)

    def _is_caching_enabled(self) -> bool:
        """Check if caching should be enabled based on current config"""
        params = self.session.get_params()
        return params.get('prompt_caching', False)

    def _prepare_api_parameters(self) -> Dict[str, Any]:
        # Get fresh parameters instead of using cached self.params
        current_params = self.session.get_params()
        
        params = {
            key: value for key, value in current_params.items()
            if key in self.parameters and value is not None
        }
        
        # Handle stream parameter specially - only include if True
        if current_params.get('stream') is True:
            params['stream'] = True
        else:
            # Remove stream parameter entirely when False to get non-streaming response
            params.pop('stream', None)
        
        # Use model_name for the API call, fallback to model if model_name doesn't exist
        api_model = current_params.get('model_name', current_params.get('model'))
        if api_model:
            params['model'] = api_model
            
        # Attach tools if official mode is active
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
            if mode == 'official':
                tools_spec = self.get_tools_for_request() or []
                if tools_spec:
                    params['tools'] = tools_spec
                    if current_params.get('tool_choice') is not None:
                        params['tool_choice'] = current_params.get('tool_choice')
        except Exception:
            pass

        # Anthropic MCP (beta): attach mcp_servers when configured
        try:
            mcp_servers = self._build_mcp_servers()
            if mcp_servers:
                params['mcp_servers'] = mcp_servers
        except Exception:
            pass

        params['system'] = self._build_system_content()
        params['messages'] = self._build_messages()
        return params

    def chat(self) -> Any:
        start_time = time()
        try:
            params = self._prepare_api_parameters()

            # If MCP servers are present, invoke the beta Messages API with
            # the required beta flag for MCP client access.
            use_mcp_beta = bool(params.get('mcp_servers'))
            if use_mcp_beta:
                try:
                    # Prefer official beta surface when available
                    beta = getattr(self.client, 'beta', None)
                    if beta and hasattr(beta, 'messages'):
                        response = beta.messages.create(**params, betas=["mcp-client-2025-04-04"])
                    else:
                        # Fallback path: many SDKs accept per-call extra_headers
                        try:
                            response = self.client.messages.create(
                                **params,
                                extra_headers={"anthropic-beta": "mcp-client-2025-04-04"}
                            )
                        except TypeError:
                            # If extra_headers is not supported, last resort without header
                            response = self.client.messages.create(**params)
                except Exception as e:
                    return self._format_error(e)
            else:
                response = self.client.messages.create(**params)
            self._last_response = response

            if params.get('stream'):
                return response
            else:
                self._update_usage_from_response(response)
                return response.content[0].text if response.content else ""

        except Exception as e:
            return self._format_error(e)

        finally:
            self.current_usage.time_elapsed = time() - start_time
            self.total_usage.time_elapsed += self.current_usage.time_elapsed

    def stream_chat(self) -> Generator[str, None, None]:
        self.current_usage = Usage()
        response = self.chat()

        if isinstance(response, str):
            yield response
            return

        try:
            accumulated_text = ""
            message_start_usage = None
            message_delta_usage = None
            tool_calls_map = {}
            
            for event in response:
                # Handle message_start - contains input_tokens and initial output count
                if event.type == "message_start" and hasattr(event, 'message') and event.message.usage:
                    message_start_usage = event.message.usage
                
                # Handle content deltas - yield the text
                elif event.type == "content_block_delta" and hasattr(event, 'delta'):
                    # Text stream
                    if hasattr(event.delta, 'text') and event.delta.text:
                        text = event.delta.text
                        accumulated_text += text
                        yield text
                    # Accumulate tool input JSON deltas when present (best-effort)
                    if hasattr(event, 'index') and hasattr(event.delta, 'partial_json'):
                        try:
                            idx = event.index
                            rec = tool_calls_map.get(idx) or {'id': None, 'name': None, 'arguments': ''}
                            rec['arguments'] = (rec['arguments'] or '') + str(event.delta.partial_json)
                            tool_calls_map[idx] = rec
                        except Exception:
                            pass

                # Detect tool_use block start
                elif event.type == "content_block_start" and hasattr(event, 'content_block'):
                    block = event.content_block
                    try:
                        if getattr(block, 'type', None) == 'tool_use':
                            idx = getattr(event, 'index', None)
                            rec = tool_calls_map.get(idx) or {'id': None, 'name': None, 'arguments': ''}
                            rec['id'] = getattr(block, 'id', None)
                            rec['name'] = getattr(block, 'name', None)
                            tool_calls_map[idx] = rec
                    except Exception:
                        pass
                
                # Handle message_delta - contains final output token count
                elif event.type == "message_delta" and hasattr(event, 'usage') and event.usage:
                    message_delta_usage = event.usage

        except Exception as e:
            yield f"Stream error: {str(e)}"
            return

        # Reconstruct final usage from the streaming events
        if message_start_usage:
            # Input tokens come from message_start
            self.current_usage.input_tokens = message_start_usage.input_tokens
            
            # Output tokens: use message_delta if available, otherwise message_start
            if message_delta_usage and hasattr(message_delta_usage, 'output_tokens'):
                self.current_usage.output_tokens = message_delta_usage.output_tokens
            else:
                self.current_usage.output_tokens = message_start_usage.output_tokens
            
            # Handle cache metrics from message_start
            if hasattr(message_start_usage, 'cache_creation_input_tokens'):
                self.current_usage.cache_writes = message_start_usage.cache_creation_input_tokens or 0
            if hasattr(message_start_usage, 'cache_read_input_tokens'):
                self.current_usage.cache_hits = message_start_usage.cache_read_input_tokens or 0
            
            # Update totals
            self.total_usage.update(self.current_usage)
            # Finalize tool calls collected during streaming
            try:
                out = []
                import json
                for _, rec in sorted(tool_calls_map.items(), key=lambda kv: (kv[0] if kv[0] is not None else 0)):
                    args_obj = {}
                    args_str = rec.get('arguments') or ''
                    if args_str:
                        try:
                            args_obj = json.loads(args_str)
                        except Exception:
                            args_obj = {}
                    out.append({'id': rec.get('id'), 'name': rec.get('name'), 'arguments': args_obj})
                self._last_stream_tool_calls = out
            except Exception:
                self._last_stream_tool_calls = None

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def get_messages(self) -> Any:
        chat = self.session.get_context('chat')
        if not chat:
            return []

        messages = []
        for turn in chat.get():
            context = ''
            if turn.get('context'):
                context = self._process_context(turn['context'])
            role = turn.get('role')
            # Assistant with tool_calls → Anthropic tool_use blocks
            if role == 'assistant' and 'tool_calls' in turn:
                blocks = []
                try:
                    for tc in (turn.get('tool_calls') or []):
                        blocks.append({
                            'type': 'tool_use',
                            'id': tc.get('id'),
                            'name': tc.get('name'),
                            'input': tc.get('arguments') or {}
                        })
                except Exception:
                    blocks = []
                if context:
                    blocks.insert(0, {'type': 'text', 'text': context})
                messages.append({'role': 'assistant', 'content': blocks})
                continue

            # Tool result turns → Anthropic expects user tool_result blocks
            if role == 'tool':
                tool_id = turn.get('tool_call_id') or turn.get('id')
                messages.append({
                    'role': 'user',
                    'content': [{
                        'type': 'tool_result',
                        'tool_use_id': tool_id,
                        'content': turn.get('message') or ''
                    }]
                })
                continue

            messages.append({
                'role': role,
                'context': context,
                'message': turn.get('message') or ""
            })
        return messages

    def get_tool_calls(self):
        """Return normalized tool calls from last response or stream.

        Shape: [{"id": str, "name": str, "arguments": dict}]
        """
        if self._last_stream_tool_calls:
            # Map back to canonical names when available
            mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
            if isinstance(mapping, dict) and mapping:
                mapped = []
                for c in self._last_stream_tool_calls:
                    n = c.get('name')
                    if isinstance(n, str) and n in mapping:
                        c = dict(c)
                        c['name'] = mapping.get(n, n)
                    mapped.append(c)
                return mapped
            return list(self._last_stream_tool_calls)
        resp = self._last_response
        out = []
        try:
            if not resp:
                return out
            for block in getattr(resp, 'content', []) or []:
                try:
                    btype = getattr(block, 'type', None)
                    if btype == 'tool_use':
                        name = getattr(block, 'name', None)
                        mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
                        if isinstance(name, str) and isinstance(mapping, dict) and name in mapping:
                            name = mapping.get(name, name)
                        out.append({
                            'id': getattr(block, 'id', None),
                            'name': name,
                            'arguments': getattr(block, 'input', {}) or {}
                        })
                except Exception:
                    continue
        except Exception:
            return []
        return out

    # Provider-native tool spec construction
    def get_tools_for_request(self) -> list:
        try:
            cmd = self.session.get_action('assistant_commands')
            if not cmd or not hasattr(cmd, 'get_tool_specs'):
                return []
            # Build specs first (this call records the fresh API→canonical mapping in session)
            canonical = cmd.get_tool_specs() or []
            # Refresh mapping after building specs so it reflects this turn's tool list
            self._tool_api_to_cmd = self.session.get_user_data('__tool_api_to_cmd__') or {}
            tools = []
            for spec in canonical:
                try:
                    tools.append({
                        'name': spec.get('name'),
                        'description': spec.get('description'),
                        'input_schema': spec.get('parameters') or {'type': 'object', 'properties': {}},
                    })
                except Exception:
                    continue
            return tools
        except Exception:
            return []

    # --- Anthropic MCP (beta) configuration ---------------------------
    def _build_mcp_servers(self) -> list:
        """Build Anthropic `mcp_servers` param from session config.

        Source params synthesized by mcp/bootstrap.py:
          - `mcp_servers`: CSV "label=url" pairs or dict mapping
          - `mcp_headers_<label>`: JSON dict; we extract an Authorization Bearer token if present
          - `mcp_allowed_<label>`: CSV of tool names

        Returns a list per Anthropic MCP connector docs:
          [{
              "type": "url",
              "url": "https://...",
              "name": "label",
              "authorization_token": "...",        # optional
              "tool_configuration": {               # optional
                  "enabled": true,
                  "allowed_tools": ["foo", "bar"],
              }
          }]
        """
        # Gate by [MCP].active to align with app-side feature flag
        try:
            if not bool(self.session.get_option('MCP', 'active', fallback=False)):
                return []
        except Exception:
            return []

        params = self.session.get_params() or {}

        # Parse server mapping
        servers_val = params.get('mcp_servers')
        servers: dict[str, str] = {}
        try:
            if isinstance(servers_val, dict):
                servers = {str(k): str(v) for k, v in servers_val.items() if k and v}
            elif isinstance(servers_val, str):
                for item in servers_val.split(','):
                    item = item.strip()
                    if not item:
                        continue
                    if '=' in item:
                        label, url = item.split('=', 1)
                        label = label.strip()
                        url = url.strip()
                        if label and url:
                            servers[label] = url
        except Exception:
            servers = {}

        out: list = []
        for label, url in servers.items():
            try:
                entry: dict = {'type': 'url', 'url': url, 'name': label}

                # Extract Authorization Bearer token if configured
                token: str | None = None
                hdr_key = f'mcp_headers_{label}'
                headers_val = params.get(hdr_key)
                headers_obj = None
                if isinstance(headers_val, dict):
                    headers_obj = headers_val
                elif isinstance(headers_val, str) and headers_val.strip().startswith('{'):
                    import json
                    try:
                        parsed = json.loads(headers_val)
                        if isinstance(parsed, dict):
                            headers_obj = parsed
                    except Exception:
                        headers_obj = None
                if isinstance(headers_obj, dict):
                    auth = None
                    for k, v in headers_obj.items():
                        if str(k).lower() == 'authorization':
                            auth = str(v)
                            break
                    if auth and auth.lower().startswith('bearer '):
                        token = auth[7:].strip()
                # Optional explicit token param (future-proof)
                if not token:
                    tkey = f'mcp_token_{label}'
                    if isinstance(params.get(tkey), str) and params.get(tkey).strip():
                        token = params.get(tkey).strip()
                if token:
                    entry['authorization_token'] = token

                # Optional tool allowlist
                allowed_key = f'mcp_allowed_{label}'
                allowed_val = params.get(allowed_key)
                allow_list = None
                if isinstance(allowed_val, str) and allowed_val.strip():
                    allow_list = [s.strip() for s in allowed_val.split(',') if s.strip()]
                if allow_list:
                    entry['tool_configuration'] = {'enabled': True, 'allowed_tools': allow_list}

                out.append(entry)
            except Exception:
                continue

        return out

    def _update_usage_from_response(self, response: Any) -> None:
        """Update usage tracking from API response"""
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage

            # Handle basic token counts - these should always be present
            self.current_usage.input_tokens = getattr(usage, 'input_tokens', 0)
            self.current_usage.output_tokens = getattr(usage, 'output_tokens', 0)

            # Handle cache-related tokens - set to 0 if None/null
            cache_creation = getattr(usage, 'cache_creation_input_tokens', None)
            self.current_usage.cache_writes = cache_creation if cache_creation is not None else 0

            cache_read = getattr(usage, 'cache_read_input_tokens', None)
            self.current_usage.cache_hits = cache_read if cache_read is not None else 0

            # Update total usage
            self.total_usage.update(self.current_usage)

    def _update_usage_from_event(self, usage: Any) -> None:
        """Update usage from streaming event"""
        if not usage:
            return

        # Update input_tokens if present
        input_tokens = getattr(usage, 'input_tokens', None)
        if input_tokens is not None:
            self.current_usage.input_tokens = input_tokens

        # Update output_tokens if present
        output_tokens = getattr(usage, 'output_tokens', None)
        if output_tokens is not None:
            self.current_usage.output_tokens = output_tokens

        # Handle cache metrics - set to 0 if None/null
        cache_creation = getattr(usage, 'cache_creation_input_tokens', None)
        self.current_usage.cache_writes = cache_creation if cache_creation is not None else 0

        cache_read = getattr(usage, 'cache_read_input_tokens', None)
        self.current_usage.cache_hits = cache_read if cache_read is not None else 0

        # Update total usage
        self.total_usage.update(self.current_usage)

    @staticmethod
    def _format_error(error: Exception) -> str:
        parts = ["An error occurred:"]
        if hasattr(error, 'status_code'):
            parts.append(f"Status code: {error.status_code}")
        if hasattr(error, 'response'):
            parts.append(f"Response: {error.response}")
        parts.append(f"Error details: {str(error)}")
        return "\n".join(parts)

    def get_usage(self) -> Any:
        """Get usage statistics with cache-aware display"""
        return {
            'total_in': self.total_usage.input_tokens,
            'total_out': self.total_usage.output_tokens,
            'total_tokens': self.total_usage.input_tokens + self.total_usage.output_tokens,
            'total_time': self.total_usage.time_elapsed,
            'total_cache_writes': self.total_usage.cache_writes,
            'total_cache_hits': self.total_usage.cache_hits,
            'total_content_tokens': self.total_usage.input_tokens + self.total_usage.cache_hits,  # True content processed
            'turn_in': self.current_usage.input_tokens,
            'turn_out': self.current_usage.output_tokens,
            'turn_total': self.current_usage.input_tokens + self.current_usage.output_tokens,
            'turn_cache_writes': self.current_usage.cache_writes,
            'turn_cache_hits': self.current_usage.cache_hits,
            'turn_content_tokens': self.current_usage.input_tokens + self.current_usage.cache_hits  # True content this turn
        }

    def reset_usage(self) -> Any:
        self.current_usage = Usage()
        self.total_usage = Usage()

    def get_cost(self) -> Dict[str, float]:
        """
        Calculate costs for regular token usage and cache operations.
        Returns costs broken down by type and total cost.
        """
        usage = self.get_usage()
        if not usage:
            return {'total_cost': 0.0}

        try:
            # Use fresh parameters instead of cached self.params
            current_params = self.session.get_params()
            
            price_unit = float(current_params.get('price_unit', 1000000))
            price_in = float(current_params.get('price_in', 0))
            price_out = float(current_params.get('price_out', 0))
            price_cache_in = float(current_params.get('price_cache_in', price_in))
            price_cache_out = float(current_params.get('price_cache_out', price_out))

            # Calculate base token costs
            total_cost = 0.0
            result = {}

            # Regular token costs
            input_cost = (usage['total_in'] / price_unit) * price_in
            output_cost = (usage['total_out'] / price_unit) * price_out
            result['input_cost'] = round(input_cost, 6)
            result['output_cost'] = round(output_cost, 6)
            total_cost += input_cost + output_cost

            # Cache write costs (if present)
            if 'total_cache_writes' in usage and usage['total_cache_writes'] > 0:
                cache_write_cost = (usage['total_cache_writes'] / price_unit) * price_cache_in
                result['cache_write_cost'] = round(cache_write_cost, 6)
                total_cost += cache_write_cost

            # Cache read costs (if present)
            if 'total_cache_hits' in usage and usage['total_cache_hits'] > 0:
                cache_read_cost = (usage['total_cache_hits'] / price_unit) * price_cache_out
                result['cache_read_cost'] = round(cache_read_cost, 6)
                total_cost += cache_read_cost

                # Calculate theoretical cost without caching for comparison
                theoretical_cost = (usage['total_cache_hits'] / price_unit) * price_in
                cache_savings = theoretical_cost - cache_read_cost
                if cache_savings > 0:
                    result['cache_savings'] = round(cache_savings, 6)

            result['total_cost'] = round(total_cost, 6)
            return result

        except (ValueError, TypeError):
            return {'total_cost': 0.0}
