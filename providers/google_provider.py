"""Google Gen AI generateContent, with signed native history and safe tool turns."""

import ast
import hashlib
import json
import os
import re
from copy import deepcopy
from collections.abc import Iterator
from time import monotonic
from uuid import uuid4

from google import genai
from google.genai import types as gx_types

from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction
from providers.api_utils import as_dict, excluded_parameters, extra_body, field
from providers.google_usage import GoogleUsage
from utils.tool_args import get_bool, get_int


def _literal(value):
    """Parse structured configuration without evaluating executable expressions."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    return as_dict(value) if hasattr(value, 'model_dump') else deepcopy(value)


def _camel(name):
    return re.sub(r'_([a-z])', lambda match: match[1].upper(), name)


def _merge_dict(left, right):
    """Merge transport escape hatches without discarding unrelated nested fields."""
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            _merge_dict(left[key], value)
        else:
            left[key] = deepcopy(value)
    return left


class GoogleProvider(GoogleUsage, APIProvider):
    """Use the native Gemini API while leaving client tool execution to core."""

    _blocked_reasons = {
        'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY',
        'IMAGE_PROHIBITED_CONTENT', 'IMAGE_RECITATION', 'ESCALATION', 'PUP_LIMITED_DISABLED',
    }
    _structured_config = {
        'thinking_config', 'response_schema', 'response_json_schema', 'tool_config',
        'automatic_function_calling', 'safety_settings', 'image_config', 'speech_config',
        'labels', 'routing_config', 'model_selection_config', 'model_armor_config',
        'response_modalities',
    }

    def __init__(self, session):
        self.session = session
        self.client = None
        self._active_stream = None
        self._http_responses = []
        self._tool_api_to_cmd = {}
        self._tool_schemas = {}
        self._init_usage()
        self._clear_response()

    def _ensure_client(self) -> None:
        """Initialize lazily with configurable SDK transport and authentication."""
        if self.client is not None:
            return
        p = self.session.get_params()
        options = {}
        api_key = p.get('api_key') or os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        if api_key:
            options['api_key'] = api_key
        for name in ('vertexai', 'enterprise', 'project', 'location'):
            if p.get(name) is not None:
                options[name] = (get_bool(p, name) if name in ('vertexai', 'enterprise')
                                 else p[name])
        http = extra_body(p.get('http_options'))
        for name in ('base_url', 'api_version'):
            if p.get(name) is not None:
                http.setdefault(name, p[name])
        if p.get('timeout') is not None:
            # The shared provider timeout is seconds; native HttpOptions uses milliseconds.
            http.setdefault('timeout', int(float(p['timeout']) * 1000))
        if p.get('max_retries') is not None:
            http.setdefault('retry_options', {'attempts': max(0, get_int(p, 'max_retries', 0)) + 1})
        if p.get('default_headers') is not None:
            http.setdefault('headers', extra_body(p['default_headers']))
        client_args = http.setdefault('client_args', {})
        hooks = client_args.setdefault('event_hooks', {})
        hooks.setdefault('response', []).append(self._track_response)
        options['http_options'] = gx_types.HttpOptions(**http)
        self.client = genai.Client(**options)

    def _track_response(self, response):
        # SDK stream generators do not always close their httpx.Response on early exit.
        self._http_responses.append(response)
        if self._cancelled():
            response.close()

    def _close_responses(self):
        for response in self._http_responses:
            try:
                response.close()
            except Exception:
                pass
        self._http_responses = []

    def _cancelled(self):
        token = getattr(self.session, 'get_cancellation_token', lambda: None)()
        return bool(token and token.is_cancelled()) or bool(
            getattr(self.session, 'get_flag', lambda name: False)('turn_cancelled'))

    def _clear_response(self):
        self._last_response = None
        self._last_tool_calls = []
        self._last_finish_reason = None
        self._native_content = None
        self._visible_text = ''
        self._current_reasoning = ''
        self._request_prefix = None
        self._candidate_index = None
        self._native_replay_safe = True

    def _begin_request(self):
        self._close_responses()
        self._clear_response()
        self.turn_usage = None
        self._turn_time = 0
        self._usage_params = deepcopy(self.session.get_params())
        self._started_at = monotonic()
        token = getattr(self.session, 'get_cancellation_token', lambda: None)()
        if token is not None:
            # Capture this request's list so a token retained from an old turn cannot
            # close a later request's connection after the provider has been reused.
            responses = self._http_responses
            token.register_cleanup(lambda: [response.close() for response in responses])

    def _get_system_prompt(self):
        context = self.session.get_context('prompt')
        return field(context.get(), 'content', '') if context else ''

    def _get_safety_settings(self):
        p = self.session.get_params()
        default = p.get('safety_default', 'BLOCK_MEDIUM_AND_ABOVE')
        return [{'category': category, 'threshold': p.get(f'safety_{name}', default)}
                for name, category in (
                    ('harassment', 'HARM_CATEGORY_HARASSMENT'),
                    ('hate_speech', 'HARM_CATEGORY_HATE_SPEECH'),
                    ('sexually_explicit', 'HARM_CATEGORY_SEXUALLY_EXPLICIT'),
                    ('dangerous_content', 'HARM_CATEGORY_DANGEROUS_CONTENT'),
                )]

    def _official_tools(self):
        return getattr(self.session, 'get_effective_tool_mode', lambda: 'none')() == 'official'

    def get_tools_for_request(self) -> list:
        """Preserve canonical JSON schemas and use the SDK's JSON Schema field."""
        if not self._official_tools():
            return []
        command = self.session.get_action('assistant_commands')
        if not command or not hasattr(command, 'get_tool_specs'):
            return []
        self._tool_api_to_cmd = getattr(self.session, 'get_user_data', lambda name: None)(
            '__tool_api_to_cmd__') or {}
        decls = []
        schema_field = ('parameters_json_schema'
                        if 'parameters_json_schema' in gx_types.FunctionDeclaration.model_fields
                        else 'parameters')
        for spec in command.get_tool_specs() or []:
            if not spec.get('name'):
                continue
            decls.append({'name': spec['name'], 'description': spec.get('description') or '',
                          schema_field: deepcopy(spec.get('parameters') or {
                              'type': 'object', 'properties': {},
                          })})
        return [{'function_declarations': decls}] if decls else []

    def _build_tools_config(self):
        """Retain the historical helper for integrations that inspect tool definitions."""
        return [gx_types.Tool(**tool) for tool in self.get_tools_for_request()] or None

    def _tools(self, p):
        if not self._official_tools():
            return []
        value = p.get('tools')
        if isinstance(value, str) and value.lstrip().startswith(('[', '{')):
            value = _literal(value)
        configured = ([value] if isinstance(value, dict) else value
                      if isinstance(value, list) else [])
        tools = deepcopy(configured)
        # Configured function declarations win over duplicate registry definitions.
        names = {field(decl, 'name') for tool in tools
                 for decl in (field(tool, 'function_declarations',
                                    field(tool, 'functionDeclarations', [])) or [])}
        for tool in self.get_tools_for_request():
            decls = [decl for decl in tool['function_declarations'] if decl['name'] not in names]
            if decls:
                tools.append({'function_declarations': decls})
        return tools

    def _generation_config(self):
        p = self.session.get_params()
        excluded = excluded_parameters(p)
        cfg = {}
        # All fields supported by the installed SDK are configurable, rather than
        # assuming a model-name-specific subset. New wire fields have an escape hatch.
        for name in gx_types.GenerateContentConfig.model_fields:
            if name in ('tools', 'system_instruction', 'http_options') or name in excluded:
                continue
            if p.get(name) is not None:
                cfg[name] = (_literal(p[name]) if name in self._structured_config else p[name])
        if 'max_output_tokens' not in cfg and 'max_output_tokens' not in excluded:
            for alias in ('max_completion_tokens', 'max_tokens'):
                if alias not in excluded and p.get(alias) is not None:
                    cfg['max_output_tokens'] = int(p[alias])
                    break
        thinking = cfg.setdefault('thinking_config', {})
        for alias, native in (('thinking_budget', 'thinking_budget'),
                              ('thinking_level', 'thinking_level'),
                              ('reasoning_effort', 'thinking_level'),
                              ('include_thoughts', 'include_thoughts')):
            if alias not in excluded and p.get(alias) is not None:
                thinking.setdefault(native, p[alias])
        if not thinking or 'thinking_config' in excluded:
            cfg.pop('thinking_config', None)
        if 'safety_settings' not in cfg and 'safety_settings' not in excluded:
            cfg['safety_settings'] = self._get_safety_settings()
        if 'system_instruction' not in excluded:
            system = p.get('system_instruction', self._get_system_prompt())
            if system:
                cfg['system_instruction'] = system
        tools = self._tools(p) if 'tools' not in excluded else []
        if tools:
            cfg['tools'] = tools
        if 'tool_config' not in cfg and 'tool_config' not in excluded:
            choice = p.get('tool_choice') if 'tool_choice' not in excluded else None
            if choice is not None:
                if isinstance(choice, str) and choice.lower() in ('auto', 'none', 'any', 'required'):
                    choice = {'mode': {'required': 'ANY'}.get(choice.lower(), choice.upper())}
                else:
                    choice = _literal(choice)
                    if choice.get('type') == 'function':
                        choice = {'mode': 'ANY', 'allowed_function_names': [
                            field(choice.get('function'), 'name')]}
                cfg['tool_config'] = {'function_calling_config': choice}
        # Never let the SDK independently execute client tools outside TurnRunner.
        cfg['automatic_function_calling'] = {'disable': True}
        http = extra_body(p.get('http_options'))
        body = extra_body(p.get('extra_body'))
        _merge_dict(body, extra_body(http.pop('extra_body', None)))
        for name in excluded:
            cfg.pop(name, None)
            body.pop(name, None)
            body.pop(_camel(name), None)
            generation = body.get('generationConfig', body.get('generation_config', {}))
            if isinstance(generation, dict):
                generation.pop(name, None)
                generation.pop(_camel(name), None)
        if not self._official_tools() or 'tools' in excluded:
            cfg.pop('tools', None)
            cfg.pop('tool_config', None)
            body.pop('tools', None)
            body.pop('toolConfig', None)
            body.pop('tool_config', None)
        if cfg.get('cached_content') or body.get('cachedContent') or body.get('cached_content'):
            # A cache owns its system instruction and tool declarations.
            cfg.pop('system_instruction', None)
            cfg.pop('tools', None)
        if body:
            http['extra_body'] = body
        if http:
            cfg['http_options'] = http
        self._tool_schemas = {}
        for tool in body.get('tools', cfg.get('tools', tools)):
            declarations = field(tool, 'function_declarations',
                                 field(tool, 'functionDeclarations', [])) or []
            for decl in declarations:
                self._tool_schemas[field(decl, 'name')] = (
                    field(decl, 'parameters_json_schema') or field(decl, 'parametersJsonSchema')
                    or field(decl, 'parameters') or {})
        return cfg

    @staticmethod
    def _fingerprint(contents, scope):
        payload = json.dumps([scope, contents], sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _api_name(self, call):
        if call.get('api_name'):
            return call['api_name']
        mapping = self._tool_api_to_cmd or getattr(self.session, 'get_user_data', lambda name: None)(
            '__tool_api_to_cmd__') or {}
        return next((api for api, canonical in mapping.items() if canonical == call.get('name')),
                    call.get('name'))

    @staticmethod
    def _native_id(call):
        if 'google_call_id' in call:
            return call['google_call_id']
        # Older checkpoints stored API IDs directly and generated google-func-* locally.
        ident = call.get('id') or call.get('call_id')
        return ident if ident and not ident.startswith('google-func-') else None

    def _native_parts(self, msg, contents, scope):
        native = msg.get('google_content')
        if (not isinstance(native, dict) or msg.get('context')
                or msg.get('raw_message', '') != msg.get('google_text')
                or msg.get('google_prefix') != self._fingerprint(contents, scope)):
            return None
        if 'tool_calls' in msg:
            original = [(field(p.get('function_call'), 'id'),
                         field(p.get('function_call'), 'name'),
                         field(p.get('function_call'), 'args') or {})
                        for p in native.get('parts', []) if p.get('function_call')]
            current = [(self._native_id(call), self._api_name(call), call.get('arguments'))
                       for call in msg.get('tool_calls') or []]
            if original != current:
                return None
        return deepcopy(native.get('parts') or [])

    def _build_contents(self, messages, scope=None):
        """Rebuild native history with exact signed parts and grouped tool results."""
        contents = []
        pending = []
        last_was_tool = False
        scope = scope or {}
        for msg in messages:
            role = msg.get('role')
            if role == 'model' and self._is_system_message(msg, self._get_system_prompt()):
                continue
            if role == 'tool':
                call_id = msg.get('tool_call_id')
                entry = next((call for call in pending if call['id'] == call_id), None)
                if entry is None and not call_id and pending:
                    entry = pending[0]
                if entry is None:
                    entry = {'id': call_id, 'name': 'unknown', 'text_fallback': True}
                else:
                    pending.remove(entry)
                payload = self._build_tool_response_payload(msg)
                if entry.get('text_fallback'):
                    part = {'text': 'Historical tool result (data from an earlier execution): '
                            + json.dumps({'name': entry['name'], 'id': entry['id'],
                                          'result': payload}, ensure_ascii=False)}
                else:
                    result = {'name': entry['name'], 'response': payload}
                    if entry.get('native_id'):
                        result['id'] = entry['native_id']
                    part = {'function_response': result}
                if contents and contents[-1]['role'] == 'user' and last_was_tool:
                    contents[-1]['parts'].append(part)
                else:
                    contents.append({'role': 'user', 'parts': [part]})
                last_was_tool = True
                continue
            last_was_tool = False
            if role not in ('user', 'assistant', 'model'):
                continue
            parts = self._native_parts(msg, contents, scope) if role == 'assistant' else None
            text_fallback = parts is None
            if parts is None:
                parts = [as_dict(p) for p in self._convert_basic_parts(msg.get('parts') or [])]
                for call in msg.get('tool_calls') or []:
                    # Imported/edited calls have no valid signed native state. Represent
                    # the executed trace as text instead of manufacturing signatures.
                    trace = {'name': self._api_name(call), 'id': call.get('id'),
                             'arguments': call.get('arguments') or {}}
                    parts.append({'text': 'Historical tool call (already executed): '
                                  + json.dumps(trace, ensure_ascii=False)})
            for call in msg.get('tool_calls') or []:
                pending.append({'id': call.get('id') or call.get('call_id'),
                                'name': self._api_name(call),
                                'native_id': self._native_id(call),
                                'text_fallback': text_fallback})
            if parts:
                contents.append({'role': 'model' if role in ('assistant', 'model') else 'user',
                                 'parts': parts})
        return [gx_types.Content(**content) for content in contents]

    def _convert_basic_parts(self, parts):
        converted = []
        for part in parts:
            if isinstance(part, dict) and (part == {'text': ''} or part == {'text': None}):
                continue
            converted.append(part if isinstance(part, gx_types.Part)
                             else gx_types.Part(**part) if isinstance(part, dict)
                             else gx_types.Part(text=str(part)))
        return converted

    @staticmethod
    def _is_system_message(msg, system_prompt):
        return bool(system_prompt) and msg.get('parts') == [{'text': system_prompt}]

    def _build_tool_response_payload(self, msg):
        text = self._extract_text_from_parts(msg.get('parts') or []) or msg.get('raw_message') or ''
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
        return {'output': text}

    @staticmethod
    def _extract_text_from_parts(parts):
        return '\n'.join(str(field(part, 'text')) for part in parts if field(part, 'text'))

    def assemble_message(self) -> list:
        """Assemble contexts without stripping native transcript metadata."""
        messages = []
        if self._get_system_prompt():
            messages.append({'role': 'model', 'parts': [{'text': self._get_system_prompt()}]})
        chat = self.session.get_context('chat')
        for turn in chat.get() if chat else []:
            parts, contexts = [], []
            for context in turn.get('context') or []:
                if context['type'] == 'image':
                    if get_bool(self.session.get_params(), 'vision', False):
                        data = context['context'].get()
                        parts.append({'inline_data': {'mime_type': data['mime_type'],
                                                      'data': data['content']}})
                else:
                    contexts.append(context)
            if contexts:
                text = ProcessContextsAction.process_contexts_for_assistant(contexts)
                if text:
                    parts.insert(0, {'text': text})
            if turn.get('message'):
                parts.append({'text': turn['message']})
            entry = {**turn, 'parts': parts, 'raw_message': turn.get('message', '')}
            messages.append(entry)
        return messages

    def _prepare_request(self, *, remember=True):
        cfg = self._generation_config()
        p = self.session.get_params()
        model = p.get('model_name') or p.get('model')
        scope = {'model': model, **{key: as_dict(cfg[key]) for key in (
            'system_instruction', 'tools', 'thinking_config', 'cached_content',
        ) if key in cfg}}
        scope['backend'] = {name: p[name] for name in (
            'base_url', 'api_version', 'vertexai', 'enterprise', 'project', 'location',
        ) if name in p}
        scope['http_backend'] = {name: field(cfg.get('http_options'), name) for name in (
            'base_url', 'api_version',
        ) if field(cfg.get('http_options'), name) is not None}
        body = field(cfg.get('http_options'), 'extra_body', {}) or {}
        scope['extra_body'] = body
        contents = self._build_contents(self.assemble_message(), scope)
        if remember:
            self._request_prefix = self._fingerprint([as_dict(c) for c in contents], scope)
            if 'contents' in body:
                self._native_replay_safe = False
        return contents, gx_types.GenerateContentConfig(**cfg), model

    def _candidate(self, response):
        candidates = field(response, 'candidates') or []
        if self._candidate_index is None and candidates:
            first = next((candidate for candidate in candidates
                          if field(candidate, 'index', 0) == 0), candidates[0])
            self._candidate_index = field(first, 'index') or 0
        return next((candidate for candidate in candidates
                     if (field(candidate, 'index') or 0) == self._candidate_index), None)

    def _capture(self, response, *, completed=True):
        self._last_response = response
        candidate = self._candidate(response)
        content = field(candidate, 'content')
        parts = field(content, 'parts') or []
        self._native_content = as_dict(content) if content else None
        self._visible_text = ''.join(field(part, 'text') or '' for part in parts
                                     if not field(part, 'thought'))
        self._current_reasoning = ''.join(field(part, 'text') or '' for part in parts
                                          if field(part, 'thought'))
        reason = field(candidate, 'finish_reason')
        reason = field(reason, 'value', reason)
        blocked = field(field(response, 'prompt_feedback'), 'block_reason')
        blocked = field(blocked, 'value', blocked)
        blocked = blocked not in (None, '', 'BLOCK_REASON_UNSPECIFIED', 'BLOCKED_REASON_UNSPECIFIED')
        if blocked or reason in self._blocked_reasons:
            self._last_finish_reason = 'content_filter'
        elif not completed or not reason:
            self._last_finish_reason = 'error'
        elif reason == 'MAX_TOKENS':
            self._last_finish_reason = 'length'
        elif reason != 'STOP':
            self._last_finish_reason = 'error'
        else:
            self._last_finish_reason = 'stop'
        calls, seen = [], set()
        for part in parts:
            fn = field(part, 'function_call')
            if not fn:
                continue
            if not field(fn, 'name'):
                self._native_replay_safe = False
                self._last_finish_reason = 'error'
                continue
            api_name = field(fn, 'name')
            args = field(fn, 'args')
            invalid = (args is not None and not isinstance(args, dict)) or bool(
                field(fn, 'partial_args') or field(fn, 'will_continue'))
            required = field(self._tool_schemas.get(api_name), 'required') or []
            if any(name not in (args or {}) for name in required):
                invalid = True
            native_id = field(fn, 'id')
            if native_id and native_id in seen:
                invalid = True
                for call in calls:
                    if call.get('google_call_id') == native_id:
                        call['truncated'] = True
            seen.add(native_id)
            calls.append({
                'id': native_id or f'google-func-{uuid4().hex}', 'google_call_id': native_id,
                'api_name': api_name,
                'name': self._tool_api_to_cmd.get(api_name, self._tool_api_to_cmd.get(
                    api_name.lower(), api_name.lower())),
                'arguments': deepcopy(args) if isinstance(args, dict) else {},
                **({'truncated': True} if invalid or self._last_finish_reason != 'stop' else {}),
            })
        if any(call.get('truncated') for call in calls):
            self._native_replay_safe = False
        self._last_tool_calls = (calls if self._last_finish_reason in ('stop', 'length') else [])

    def _notice(self):
        if self._last_finish_reason == 'content_filter':
            return '[Response blocked by Gemini.]'
        if self._last_finish_reason == 'error':
            candidate = self._candidate(self._last_response)
            reason = field(candidate, 'finish_reason') or 'missing terminal finish reason'
            return f'[Gemini response incomplete or failed: {field(reason, "value", reason)}]'
        return ''

    def chat(self) -> str:
        """Generate a complete response, resetting transient state even on failure."""
        self._begin_request()
        native_usage = None
        try:
            if self._cancelled():
                self._last_finish_reason = 'cancelled'
                return ''
            contents, config, model = self._prepare_request()
            if not contents:
                return ''
            self._ensure_client()
            response = self.client.models.generate_content(model=model, contents=contents, config=config)
            native_usage = field(response, 'usage_metadata')
            self._capture(response)
            if self._cancelled():
                self._last_finish_reason = 'cancelled'
                self._last_tool_calls = []
                return ''
            notice = self._notice()
            if notice:
                self._visible_text += ('\n' if self._visible_text else '') + notice
            return self._visible_text
        except Exception as error:
            self._last_tool_calls = []
            self._last_finish_reason = 'cancelled' if self._cancelled() else 'error'
            return '' if self._cancelled() else f'Error in chat completion: {error}'
        finally:
            self._close_responses()
            self._record_usage(native_usage, monotonic() - self._started_at)

    def stream_chat(self) -> Iterator[str]:
        """Aggregate native parts, terminal status, and cumulative usage across chunks."""
        self._begin_request()
        aggregate, native_usage, parts = {}, None, []
        candidate_data = {}
        completed = False
        try:
            if self._cancelled():
                self._last_finish_reason = 'cancelled'
                return
            contents, config, model = self._prepare_request()
            if not contents:
                return
            self._ensure_client()
            stream = self.client.models.generate_content_stream(model=model, contents=contents,
                                                                 config=config)
            self._active_stream = stream
            for chunk in stream:
                if self._cancelled():
                    self._last_finish_reason = 'cancelled'
                    break
                data = as_dict(chunk)
                aggregate.update({key: value for key, value in data.items()
                                  if key not in ('candidates', 'usage_metadata')})
                usage = field(chunk, 'usage_metadata')
                if usage is not None:
                    if native_usage is None:
                        native_usage = {}
                    native_usage.update(as_dict(usage))
                candidate = self._candidate(chunk)
                if candidate is None:
                    continue
                candidate_dict = as_dict(candidate)
                candidate_data.update({key: value for key, value in candidate_dict.items()
                                       if key != 'content'})
                chunk_parts = field(field(candidate, 'content'), 'parts') or []
                parts.extend(as_dict(part) for part in chunk_parts)
                if field(candidate, 'finish_reason'):
                    completed = True
                for part in chunk_parts:
                    if field(part, 'text') and not field(part, 'thought'):
                        yield field(part, 'text')
            candidate_data['content'] = {'role': 'model', 'parts': parts}
            aggregate['candidates'] = [candidate_data] if candidate_data else []
            aggregate['usage_metadata'] = native_usage
            self._capture(gx_types.GenerateContentResponse(**aggregate), completed=completed)
            if self._cancelled():
                self._last_finish_reason = 'cancelled'
                self._last_tool_calls = []
            else:
                notice = self._notice()
                if notice:
                    self._visible_text += ('\n' if self._visible_text else '') + notice
                    yield ('\n' if parts else '') + notice
        except GeneratorExit:
            self._last_finish_reason = 'cancelled'
            self._last_tool_calls = []
            raise
        except Exception as error:
            self._last_tool_calls = []
            self._last_finish_reason = 'cancelled' if self._cancelled() else 'error'
            if not self._cancelled():
                yield f'Stream error: {error}'
        finally:
            self._close_responses()
            if self._active_stream is not None:
                self._active_stream.close()
                self._active_stream = None
            if self._last_response is None and (aggregate or parts or native_usage is not None):
                reason = self._last_finish_reason
                candidate_data['content'] = {'role': 'model', 'parts': parts}
                aggregate['candidates'] = [candidate_data]
                aggregate['usage_metadata'] = native_usage
                self._capture(gx_types.GenerateContentResponse(**aggregate), completed=False)
                self._last_finish_reason = reason or 'error'
                self._last_tool_calls = []
            self._record_usage(native_usage, monotonic() - self._started_at)

    def get_full_response(self) -> gx_types.GenerateContentResponse | None:
        """Return the SDK response, reconstructed from all chunks after streaming."""
        return self._last_response

    def get_finish_reason(self) -> str | None:
        """Expose terminal failure, blocking, cancellation, or length to TurnRunner."""
        return self._last_finish_reason

    def get_current_reasoning(self) -> str:
        """Return thought summaries separately from the visible assistant answer."""
        return self._current_reasoning

    def get_assistant_metadata(self) -> dict:
        """Save JSON-safe native content with a fingerprint of the request prefix."""
        if (self._last_finish_reason != 'stop' or not self._native_replay_safe
                or not self._native_content):
            return {}
        return {'google_content': deepcopy(self._native_content),
                'google_text': self._visible_text, 'google_prefix': self._request_prefix}

    def get_tool_calls(self) -> list:
        """Consume normalized client tool calls exactly once."""
        calls, self._last_tool_calls = self._last_tool_calls, []
        return deepcopy(calls)

    def get_messages(self) -> list:
        """Inspect the same signed history and vision gating used by real requests."""
        contents, config, _ = self._prepare_request(remember=False)
        messages = [as_dict(content) for content in contents]
        system = field(config, 'system_instruction')
        body = field(field(config, 'http_options'), 'extra_body', {}) or {}
        messages = deepcopy(body.get('contents', messages))
        system = body.get('systemInstruction', body.get('system_instruction', system))
        if system:
            if isinstance(system, str):
                parts = [{'text': system}]
            elif isinstance(system, list):
                parts = [as_dict(part) for part in self._convert_basic_parts(system)]
            else:
                parts = as_dict(field(system, 'parts'))
            messages.insert(0, {'role': 'system', 'parts': parts})
        return messages

    def cleanup(self) -> None:
        """Release pending responses and SDK client resources."""
        self._close_responses()
        if self.client is not None:
            self.client.close()
            self.client = None

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
