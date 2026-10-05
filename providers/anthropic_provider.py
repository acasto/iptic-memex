"""Anthropic Messages, with native transcript replay and configurable endpoints."""

import hashlib
import inspect
import json
import os
from copy import deepcopy
from time import time

from anthropic import Anthropic

from base_classes import APIProvider
from providers.anthropic_usage import AnthropicUsage, Usage
from providers.api_utils import (
    as_dict, excluded_parameters, extra_body, field, parse_tool_arguments, sdk_params,
)
from utils.tool_args import get_bool, get_int, get_list


class AnthropicProvider(AnthropicUsage, APIProvider):
    parameters = {
        'model', 'max_tokens', 'stop_sequences', 'metadata', 'temperature', 'top_k',
        'top_p', 'tool_choice', 'thinking', 'output_config', 'service_tier', 'container',
        'context_management', 'inference_geo', 'user_profile_id', 'cache_control',
        'extra_headers', 'extra_query', 'timeout',
    }

    def __init__(self, session):
        self.session = session
        self.client = self._initialize_client()
        self._active_stream = None
        self._init_usage()
        self._clear_response()
        self._tool_api_to_cmd = {}

    @staticmethod
    def supports_mcp_passthrough() -> bool:
        """Support Anthropic's remote MCP connector."""
        return True

    def _initialize_client(self):
        params = self.session.get_params()
        options = {'api_key': params.get('api_key') or os.getenv('ANTHROPIC_API_KEY') or 'none'}
        for name in ('base_url', 'timeout', 'max_retries', 'default_headers'):
            if params.get(name) is not None:
                options[name] = (extra_body(params[name]) if name == 'default_headers'
                                 else params[name])
        return Anthropic(**options)

    def _clear_response(self):
        if getattr(self, '_active_stream', None) is not None:
            self._active_stream.close()
            self._active_stream = None
        self._last_response = None
        self._last_tool_calls = []
        self._last_finish_reason = None
        self._native_content = []
        self._current_reasoning = ''
        self._visible_text = ''
        self._request_prefix = None
        self._request_scope = {}
        self._replay_container = None
        self._native_replay_safe = True

    def _begin_request(self):
        self._clear_response()
        self.current_usage = Usage()
        self._usage_known = False
        self._usage_params = deepcopy(self.session.get_params())
        self._started_at = time()

    def _cache_config(self):
        p = self.session.get_params()
        ttl = p.get('prompt_cache_ttl', p.get('cache_ttl', '5m'))
        ttl = {5: '5m', 60: '1h', '5': '5m', '60': '1h'}.get(ttl, ttl)
        if ttl not in ('5m', '1h'):
            raise ValueError('prompt_cache_ttl must be 5m or 1h')
        return {'type': 'ephemeral', **({'ttl': ttl} if ttl == '1h' else {})}

    def _is_caching_enabled(self):
        return bool(get_bool(self.session.get_params(), 'prompt_caching', False))

    def _explicit_caching(self):
        p = self.session.get_params()
        return (self._is_caching_enabled() and p.get('cache_strategy', 'explicit') == 'explicit'
                and not p.get('cache_control')
                and not extra_body(p.get('extra_body')).get('cache_control'))

    def _build_system_content(self):
        context = self.session.get_context('prompt')
        text = field(context.get(), 'content') if context else None
        if not text:
            return []
        block = {'type': 'text', 'text': text}
        if self._explicit_caching():
            block['cache_control'] = self._cache_config()
        return [block]

    @staticmethod
    def _fingerprint(messages, scope):
        # Cache breakpoints move between requests; they don't change prompt content.
        def clean(value):
            if isinstance(value, dict):
                cacheable = value.get('type') in (
                    'text', 'image', 'tool_use', 'tool_result', 'document', 'mcp_toolset',
                ) or ('name' in value and 'input_schema' in value)
                return {key: (deepcopy(child) if key in ('input', 'input_schema') else clean(child))
                        for key, child in value.items()
                        if key != 'cache_control' or not cacheable}
            if isinstance(value, list):
                return [clean(child) for child in value]
            return value
        payload = json.dumps(clean([scope, messages]), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _context_blocks(self, context):
        blocks = []
        for item in context or []:
            if item.get('type') == 'image':
                if not get_bool(self.session.get_params(), 'vision', False):
                    continue
                data = item['context'].get()
                blocks.append({'type': 'image', 'source': {
                    'type': 'base64', 'media_type': data['mime_type'], 'data': data['content'],
                }})
            else:
                text = self._process_context([item])
                if text:
                    blocks.append({'type': 'text', 'text': text})
        return blocks

    @staticmethod
    def _process_context(context):
        """Keep the provider's existing context delimiters and raw-text behavior."""
        if isinstance(context, str):
            return context
        text = ''
        project = False
        for item in context or []:
            if item.get('type') == 'image':
                continue
            data = item['context'].get()
            content = data.get('content', '')
            name = data.get('name', 'unnamed')
            if item.get('type') == 'raw' or name == 'turn_status':
                text += content
            elif item.get('type') == 'project':
                project = True
                text += (f"<|project_notes|>\nProject Name: {name}\n"
                         f"Project Notes: {content}\n<|end_project_notes|>\n")
            else:
                text += f"<|file:{name}|>\n{content}\n<|end_file:{name}|>\n"
        return f'<|project_context>{text}<|end_project_context|>' if project else text

    def _native_blocks(self, turn, messages, scope):
        content = turn.get('anthropic_content')
        if (not isinstance(content, list) or turn.get('context')
                or turn.get('message', '') != turn.get('anthropic_text')
                or turn.get('anthropic_prefix') != self._fingerprint(messages, scope)):
            return None
        if 'tool_calls' in turn:
            original = [(b.get('id'), b.get('name'), b.get('input')) for b in content
                        if b.get('type') == 'tool_use']
            current = [(c.get('id'), self._api_name(c), c.get('arguments'))
                       for c in turn.get('tool_calls') or []]
            if original != current:
                return None
        self._replay_container = turn.get('anthropic_container')
        return deepcopy(content)

    def _api_name(self, call):
        if call.get('api_name'):
            return call['api_name']
        mapping = self._tool_api_to_cmd or getattr(
            self.session, 'get_user_data', lambda name: {})('__tool_api_to_cmd__') or {}
        return next((api for api, canonical in mapping.items() if canonical == call.get('name')),
                    call.get('name'))

    def _build_messages(self, scope=None):
        if scope is None:
            scope = self._request_scope
        chat = self.session.get_context('chat')
        messages = []
        self._replay_container = None
        for turn in chat.get() if chat else []:
            role = turn.get('role')
            if role == 'tool':
                block = {'type': 'tool_result',
                         'tool_use_id': turn.get('tool_call_id') or turn.get('id'),
                         'content': turn.get('message') or ''}
                if turn.get('is_error'):
                    block['is_error'] = True
                # All parallel results belong in the immediately following user message.
                if messages and messages[-1]['role'] == 'user' and all(
                        b.get('type') == 'tool_result' for b in messages[-1]['content']):
                    messages[-1]['content'].append(block)
                else:
                    messages.append({'role': 'user', 'content': [block]})
                continue
            if role not in ('user', 'assistant'):
                continue
            blocks = self._native_blocks(turn, messages, scope) if role == 'assistant' else None
            if blocks is None:
                blocks = self._context_blocks(turn.get('context'))
                if turn.get('message'):
                    blocks.append({'type': 'text', 'text': turn['message']})
                if role == 'assistant':
                    self._replay_container = None
                    for call in turn.get('tool_calls') or []:
                        blocks.append({'type': 'tool_use', 'id': call.get('id'),
                                       'name': self._api_name(call),
                                       'input': call.get('arguments') or {}})
            if blocks:
                messages.append({'role': role, 'content': blocks})
        if self._explicit_caching() and messages:
            for block in reversed(messages[-1]['content']):
                if block.get('type') in ('text', 'image', 'document', 'tool_use', 'tool_result'):
                    block['cache_control'] = self._cache_config()
                    break
        return messages

    def get_tools_for_request(self):
        """Build native client tools from the canonical registry without mutating it."""
        commands = self.session.get_action('assistant_commands')
        if not commands or not hasattr(commands, 'get_tool_specs'):
            return []
        specs = commands.get_tool_specs() or []
        self._tool_api_to_cmd = self.session.get_user_data('__tool_api_to_cmd__') or {}
        return [{'name': spec['name'], 'description': spec.get('description') or '',
                 'input_schema': deepcopy(spec.get('parameters') or {
                     'type': 'object', 'properties': {},
                 })} for spec in specs]

    def _mcp_active(self):
        return bool(get_bool({'active': self.session.get_option('MCP', 'active', fallback=False)},
                             'active', False))

    def _mcp_beta(self):
        value = self.session.get_params().get('mcp_beta', 'mcp-client-2025-11-20')
        return 'mcp-client-2025-04-04' if value == 'legacy' else value

    def _build_mcp_servers(self):
        """Map shared MCP configuration; leave native list entries available too."""
        if not self._mcp_active():
            return []
        p = self.session.get_params()
        value = p.get('mcp_servers')
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            import ast
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = ast.literal_eval(value)
        if isinstance(value, list):
            return deepcopy(value)
        mapping = value if isinstance(value, dict) else {}
        if isinstance(value, str):
            mapping = dict(item.strip().split('=', 1) for item in value.split(',') if '=' in item)
        servers = []
        for label, url in mapping.items():
            label, url = str(label).strip(), str(url).strip()
            if not label or not url:
                continue
            server = {'type': 'url', 'name': label, 'url': url}
            headers = extra_body(p.get(f'mcp_headers_{label}'))
            auth = next((str(v) for k, v in headers.items() if k.lower() == 'authorization'), '')
            token = p.get(f'mcp_token_{label}')
            if not token and auth.lower().startswith('bearer '):
                token = auth[7:].strip()
            if token:
                server['authorization_token'] = token
            allowed = get_list(p, f'mcp_allowed_{label}')
            if allowed:
                server['tool_configuration'] = {'enabled': True, 'allowed_tools': allowed}
            servers.append(server)
        return servers

    @staticmethod
    def _merge_tools(configured, generated):
        def key(tool):
            return tool.get('name') or ('mcp', tool.get('mcp_server_name'))
        result = deepcopy(configured)
        seen = {key(tool) for tool in result}
        result.extend(tool for tool in generated if key(tool) not in seen)
        return result

    def _prepare_api_parameters(self, stream=None):
        p = self.session.get_params()
        excluded = excluded_parameters(p)
        source = {key: value for key, value in p.items() if key not in excluded
                  and value is not None
                  and not (isinstance(value, str) and not value.strip())}
        params = {key: deepcopy(value) for key, value in source.items()
                  if key in self.parameters and value is not None}
        for name in ('metadata', 'tool_choice', 'thinking', 'output_config', 'container',
                     'context_management', 'cache_control', 'extra_headers', 'extra_query'):
            if (name in params and isinstance(params[name], str)
                    and params[name].lstrip().startswith('{')):
                params[name] = extra_body(params[name])
        if source.get('reasoning_effort') is not None:
            params.setdefault('output_config', {}).setdefault('effort', source['reasoning_effort'])
        if source.get('thinking_budget') is not None:
            params.setdefault('thinking', {'type': 'enabled',
                                           'budget_tokens': get_int(source, 'thinking_budget')})
        params['model'] = source.get('model_name') or source.get('model')
        params['stream'] = bool(get_bool(p, 'stream', False)) if stream is None else stream
        configured = source.get('tools')
        if isinstance(configured, str):
            # Literal arrays are supported alongside the dictionary escape hatch.
            import ast
            try:
                configured = json.loads(configured)
            except json.JSONDecodeError:
                configured = ast.literal_eval(configured)
        configured = deepcopy(configured) if isinstance(configured, list) else []
        official = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')() == 'official'
        tools = self._merge_tools(configured, self.get_tools_for_request() if official else [])
        servers = self._build_mcp_servers() if 'mcp_servers' not in excluded else []
        if servers and self._mcp_beta() != 'mcp-client-2025-04-04':
            toolsets = []
            for server in servers:
                config = server.pop('tool_configuration', {})
                toolset = {'type': 'mcp_toolset', 'mcp_server_name': server['name']}
                allowed = config.get('allowed_tools')
                if config.get('enabled') is False:
                    toolset['default_config'] = {'enabled': False}
                elif allowed is not None:
                    toolset.update(default_config={'enabled': False},
                                   configs={name: {'enabled': True} for name in allowed})
                elif 'enabled' in config:
                    toolset['default_config'] = {'enabled': config['enabled']}
                toolsets.append(toolset)
            tools = self._merge_tools(tools, toolsets)
        if not self._mcp_active():
            tools = [tool for tool in tools if tool.get('type') != 'mcp_toolset']
        if tools:
            params['tools'] = tools
        if servers:
            params['mcp_servers'] = servers
        if self._is_caching_enabled() and source.get('cache_strategy') == 'automatic':
            params.setdefault('cache_control', self._cache_config())
        params['system'] = self._build_system_content()
        body = extra_body(source.get('extra_body'))
        # Exclusions apply to aliases and escape-hatch fields alike.
        for name in excluded:
            params.pop(name, None)
            body.pop(name, None)
        if not self._mcp_active():
            body.pop('mcp_servers', None)
            if isinstance(body.get('tools'), list):
                body['tools'] = [t for t in body['tools'] if t.get('type') != 'mcp_toolset']
        effective = {**params, **body}
        self._request_scope = {name: deepcopy(effective.get(name)) for name in
                               ('model', 'system', 'tools', 'thinking')}
        messages = self._build_messages(self._request_scope)
        params['messages'] = messages
        if self._replay_container and 'container' not in effective and 'container' not in excluded:
            params['container'] = field(self._replay_container, 'id', self._replay_container)
        if 'cache_control' in excluded:
            def strip_cache(value):
                if isinstance(value, dict):
                    if value.get('type') in (
                            'text', 'image', 'document', 'tool_use', 'tool_result', 'mcp_toolset',
                    ) or ('name' in value and 'input_schema' in value):
                        value.pop('cache_control', None)
                    for key, child in value.items():
                        if key not in ('input', 'input_schema'):
                            strip_cache(child)
                elif isinstance(value, list):
                    for child in value:
                        strip_cache(child)
            strip_cache(params)
            strip_cache(body)
        if body:
            params['extra_body'] = body
        for name in excluded:
            params.pop(name, None)
        self._request_prefix = self._fingerprint(body.get('messages', messages), self._request_scope)
        return params

    def _create(self, params):
        if self._cancelled():
            raise RuntimeError('Request cancelled')
        p = self.session.get_params()
        body = params.get('extra_body') or {}
        betas = ([] if 'betas' in excluded_parameters(p) else get_list(p, 'betas', [])) or []
        if params.get('mcp_servers') or body.get('mcp_servers'):
            betas.append(self._mcp_beta())
        headers = deepcopy(params.get('extra_headers') or {})
        existing = next((headers.pop(k) for k in list(headers) if k.lower() == 'anthropic-beta'), '')
        betas += [b.strip() for b in existing.split(',') if b.strip()]
        betas = list(dict.fromkeys(betas))
        create = self.client.messages.create
        if betas:
            beta = getattr(self.client, 'beta', None)
            if beta and getattr(beta, 'messages', None):
                create = beta.messages.create
                try:
                    signature = inspect.signature(create)
                    supported = 'betas' in signature.parameters or any(
                        value.kind == inspect.Parameter.VAR_KEYWORD
                        for value in signature.parameters.values())
                except (ValueError, TypeError):
                    supported = True
                if supported:
                    params = {**params, 'betas': betas}
                else:
                    headers['anthropic-beta'] = ','.join(betas)
            else:
                headers['anthropic-beta'] = ','.join(betas)
        if headers or 'extra_headers' in params:
            params = {**params, 'extra_headers': headers}
        return create(**sdk_params(create, params))

    def _cancelled(self):
        getter = getattr(self.session, 'get_cancellation_token', lambda: None)
        token = getter()
        return bool((token and token.is_cancelled())
                    or getattr(self.session, 'get_flag', lambda name: False)('turn_cancelled'))

    def _register_stream(self, stream):
        self._active_stream = stream
        token = getattr(self.session, 'get_cancellation_token', lambda: None)()
        if token:
            token.register_cleanup(stream.close)

    def _capture(self, response, completed=True, closed_blocks=None):
        self._last_response = response
        content = as_dict(field(response, 'content', []) or [])
        self._native_content.extend(content)
        self._current_reasoning += ''.join(b.get('thinking', '') for b in content
                                           if b.get('type') == 'thinking')
        text = ''.join(b.get('text', '') for b in content if b.get('type', 'text') == 'text')
        self._visible_text += text
        reason = field(response, 'stop_reason')
        self._last_finish_reason = {
            'max_tokens': 'length', 'model_context_window_exceeded': 'length',
            'refusal': 'content_filter',
        }.get(reason, reason)
        if not completed:
            self._last_finish_reason = 'cancelled' if self._cancelled() else 'length'
        mapping = self._tool_api_to_cmd or getattr(
            self.session, 'get_user_data', lambda name: {})('__tool_api_to_cmd__') or {}
        for index, block in enumerate(content):
            if block.get('type') != 'tool_use':
                continue
            args, invalid = parse_tool_arguments(block.get('input'))
            name = block.get('name')
            bad = (invalid or not block.get('id') or not name or not completed
                   or self._last_finish_reason not in ('tool_use', 'end_turn')
                   or (closed_blocks is not None and index not in closed_blocks))
            if bad:
                self._native_replay_safe = False
            self._last_tool_calls.append({'id': block.get('id'), 'name': mapping.get(name, name),
                                          'api_name': name, 'arguments': args,
                                          **({'truncated': True} if bad else {})})
        return text

    def _limit(self):
        return max(0, get_int(self.session.get_params(), 'max_server_continuations', 3))

    def _continue_params(self, params, response):
        result = deepcopy(params)
        # Use the actual input even when an escape hatch supplied messages.
        body = result.get('extra_body') or {}
        messages = body.pop('messages', result.get('messages', []))
        result['messages'] = [*messages, {'role': 'assistant',
                                         'content': as_dict(field(response, 'content', []))}]
        container = field(response, 'container')
        if container and 'container' not in excluded_parameters(self.session.get_params()):
            result['container'] = field(container, 'id', container)
        return result

    def chat(self):
        """Return visible text; retain all native blocks and terminal details."""
        self._begin_request()
        usage = Usage()
        try:
            params = self._prepare_api_parameters()
            if params.get('stream'):
                stream = self._create(params)
                self._register_stream(stream)
                return stream
            for attempt in range(self._limit() + 1):
                response = self._create(params)
                native_usage = field(response, 'usage')
                if native_usage is not None:
                    current = Usage()
                    current.merge(native_usage)
                    usage.update(current)
                    self._usage_known = True
                self._capture(response)
                if field(response, 'stop_reason') != 'pause_turn':
                    break
                if attempt == self._limit():
                    self._visible_text += '\n[Server tool continuation limit reached.]'
                    break
                params = self._continue_params(params, response)
            if self._last_finish_reason == 'content_filter' and not self._visible_text:
                self._visible_text = '[Response refused.]'
            return self._visible_text
        except Exception as error:
            self._last_tool_calls = []
            self._last_finish_reason = 'cancelled' if self._cancelled() else 'error'
            return self._format_error(error)
        finally:
            if self._active_stream is None:
                usage.time_elapsed = time() - self._started_at
                self._record_usage(usage)

    def stream_chat(self):
        """Reconstruct native content, including signed thinking, from stream events."""
        self._begin_request()
        params = None
        total = Usage()
        try:
            params = self._prepare_api_parameters(stream=True)
            for attempt in range(self._limit() + 1):
                response = self._create(params)
                self._register_stream(response)
                message = {'type': 'message', 'role': 'assistant', 'content': []}
                blocks, argument_deltas, closed = {}, {}, set()
                usage = Usage()
                native_usage = {}
                usage_seen = False
                completed = False
                try:
                    for event in response:
                        if self._cancelled():
                            break
                        kind = field(event, 'type')
                        if kind == 'message_start':
                            message.update(as_dict(field(event, 'message')))
                            if field(message, 'usage') is not None:
                                usage_seen = True
                                native_usage.update(message['usage'])
                                usage.merge(message['usage'])
                                self._usage_known = True
                        elif kind == 'content_block_start':
                            index = field(event, 'index')
                            blocks[index] = as_dict(field(event, 'content_block'))
                            if blocks[index].get('type') == 'text' and blocks[index].get('text'):
                                yield blocks[index]['text']
                        elif kind == 'content_block_delta':
                            index, delta = field(event, 'index'), as_dict(field(event, 'delta'))
                            block = blocks.get(index)
                            if block is None:
                                raise ValueError('Content delta without a block start')
                            if delta.get('type') == 'input_json_delta':
                                argument_deltas[index] = (argument_deltas.get(index, '')
                                                           + delta['partial_json'])
                            elif delta.get('type') == 'citations_delta':
                                block.setdefault('citations', []).append(delta['citation'])
                            elif delta.get('type') == 'compaction_delta':
                                block['content'] = block.get('content', '') + delta.get('content', '')
                            else:
                                for name in ('text', 'thinking', 'signature'):
                                    if name in delta:
                                        block[name] = block.get(name, '') + delta[name]
                                if block.get('type') == 'text' and delta.get('text'):
                                    yield delta['text']
                        elif kind == 'content_block_stop':
                            closed.add(field(event, 'index'))
                        elif kind == 'message_delta':
                            message.update(as_dict(field(event, 'delta')) or {})
                            if field(event, 'usage') is not None:
                                usage_seen = True
                                native_usage.update(as_dict(field(event, 'usage')))
                                usage.merge(field(event, 'usage'))
                                self._usage_known = True
                        elif kind == 'message_stop':
                            completed = True
                            break
                        elif kind == 'error':
                            raise RuntimeError(str(field(event, 'error')))
                finally:
                    response.close()
                    self._active_stream = None
                    total.update(usage)
                    for index, arguments in argument_deltas.items():
                        parsed, invalid = parse_tool_arguments(arguments)
                        blocks[index]['input'] = arguments if invalid else parsed
                    order = sorted(blocks)
                    message['content'] = [blocks[index] for index in order]
                    message['usage'] = {
                        **native_usage,
                        'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens,
                        'cache_creation_input_tokens': usage.cache_writes,
                        'cache_read_input_tokens': usage.cache_hits,
                        'cache_creation': {'ephemeral_1h_input_tokens': usage.cache_writes_1h,
                                           'ephemeral_5m_input_tokens': max(
                                               0, usage.cache_writes - usage.cache_writes_1h)},
                    } if usage_seen else None
                    self._capture(
                        message, completed=completed and bool(message.get('stop_reason')),
                        closed_blocks={pos for pos, index in enumerate(order) if index in closed},
                    )
                if not completed or field(message, 'stop_reason') != 'pause_turn':
                    break
                if attempt == self._limit():
                    warning = '\n[Server tool continuation limit reached.]'
                    self._visible_text += warning
                    yield warning
                    break
                params = self._continue_params(params, message)
            if self._last_finish_reason == 'content_filter' and not self._visible_text:
                self._visible_text = '[Response refused.]'
                yield self._visible_text
        except GeneratorExit:
            self._last_tool_calls = []
            self._last_finish_reason = 'cancelled'
            raise
        except Exception as error:
            self._last_tool_calls = []
            self._last_finish_reason = 'cancelled' if self._cancelled() else 'error'
            if not self._cancelled():
                yield self._format_error(error)
        finally:
            if self._active_stream is not None:
                self._active_stream.close()
                self._active_stream = None
            total.time_elapsed = time() - self._started_at
            self._record_usage(total)

    def get_full_response(self):
        """Return the final native Message, reconstructed for streaming calls."""
        return self._last_response

    def get_finish_reason(self):
        """Expose completion, truncation, refusal, failure, or cancellation to core."""
        return self._last_finish_reason

    def get_current_reasoning(self):
        """Return available thinking summaries for transcript inspection."""
        return self._current_reasoning

    def get_assistant_metadata(self):
        """Store replayable native blocks with the exact prompt prefix they follow."""
        if (not self._native_replay_safe or self._last_finish_reason not in
                ('end_turn', 'tool_use', 'stop_sequence', 'pause_turn')):
            return {}
        result = {'anthropic_content': deepcopy(self._native_content),
                  'anthropic_text': self._visible_text, 'anthropic_prefix': self._request_prefix}
        container = field(self._last_response, 'container')
        if container:
            result['anthropic_container'] = as_dict(container)
        return result

    def get_messages(self):
        """Inspect the same system/messages assembly used by actual requests."""
        saved = (self._request_scope, self._request_prefix, self._replay_container)
        try:
            params = self._prepare_api_parameters()
            body = params.get('extra_body') or {}
            return deepcopy([
                {'role': 'system', 'content': body.get('system', params.get('system', []))},
                *body.get('messages', params.get('messages', [])),
            ])
        finally:
            self._request_scope, self._request_prefix, self._replay_container = saved

    def get_tool_calls(self):
        """Consume this response's client tool calls exactly once."""
        calls, self._last_tool_calls = self._last_tool_calls, []
        return deepcopy(calls)

    @staticmethod
    def _format_error(error):
        details = ['An error occurred:']
        if getattr(error, 'status_code', None) is not None:
            details.append(f'Status code: {error.status_code}')
        details.append(f'Error details: {error}')
        return '\n'.join(details)

    def cleanup(self):
        """Close an active stream and the underlying SDK client."""
        if self._active_stream is not None:
            self._active_stream.close()
            self._active_stream = None
        close = getattr(self.client, 'close', None)
        if close:
            close()
