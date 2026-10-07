import os
from time import time
import json
from copy import deepcopy
from typing import Any, Generator, List, Dict

from openai import OpenAI

from base_classes import APIProvider, InteractionNeeded
from providers.openai_common import (
    OpenAIUsage, as_dict, excluded_parameters, extra_body, field,
    parse_tool_arguments, strict_schema,
)
from actions.process_contexts_action import ProcessContextsAction


class OpenAIResponsesProvider(OpenAIUsage, APIProvider):
    """OpenAI Responses API handler

    This provider is isolated from the legacy Chat Completions-based
    OpenAIProvider to avoid breaking third-party OpenAI-compatible backends.
    It targets OpenAI's Responses API and contains its own config section
    `[OpenAIResponses]` for overrides.
    """

    def __init__(self, session):
        self.session = session
        self._client = self._initialize_client()
        self._last_response = None
        self._last_response_id = None
        self._last_tool_calls = None
        self._tool_api_to_cmd: Dict[str, str] = {}
        self._pending_mcp_approvals: list[dict] = []

        self._last_output_items = []
        self._last_status = None
        self._last_finish_reason = None
        self._response_model = None
        self._last_stored = False
        self._chain_prefix = None
        self.last_api_param = None
        self._init_usage()

    @staticmethod
    def supports_mcp_passthrough() -> bool:
        """This provider supports pass-through MCP via Responses built-ins."""
        return True

    # --- Client init ----------------------------------------------------
    def _initialize_client(self) -> OpenAI:
        params = self.session.get_params()
        options: Dict[str, Any] = {}

        if params.get('api_key'):
            options['api_key'] = params['api_key']
        elif 'OPENAI_API_KEY' in os.environ:
            options['api_key'] = os.environ['OPENAI_API_KEY']
        else:
            # Require key when provider explicitly selected; raise to allow callers to handle gracefully
            if params.get('provider', '').lower() == 'openairesponses':
                raise RuntimeError("OpenAI API Key is required")
            options['api_key'] = 'none'

        # Support custom base_url + endpoint
        base_url = params.get('base_url')
        endpoint = params.get('endpoint')
        if base_url:
            if endpoint:
                if not base_url.endswith('/') and not endpoint.startswith('/'):
                    base_url += '/'
                elif base_url.endswith('/') and endpoint.startswith('/'):
                    endpoint = endpoint[1:]
                base_url += endpoint
            options['base_url'] = base_url

        for key in ('timeout', 'max_retries', 'organization', 'project'):
            if params.get(key) is not None:
                options[key] = params[key]

        return OpenAI(**options)

    # --- MCP helpers ----------------------------------------------------
    def _summarize_mcp_outputs(self, outputs: list) -> str:
        """Render a concise textual summary of MCP outputs so the UI isn't blank.

        - mcp_list_tools: show server label + tool names (limited)
        - mcp_call: show server label, tool name, and a brief output preview
        """
        try:
            if not isinstance(outputs, list):
                return ''
            imported: list[str] = []
            calls: list[str] = []
            for item in outputs:
                try:
                    itype = field(item, 'type')
                    if itype == 'mcp_list_tools':
                        label = field(item, 'server_label') or field(item, 'serverLabel')
                        tools = field(item, 'tools') or []
                        names = []
                        for t in tools or []:
                            name = getattr(t, 'name', None) if not isinstance(t, dict) else t.get('name')
                            if isinstance(name, str) and name:
                                names.append(name)
                        if label and names:
                            imported.append(f"{label}: {', '.join(names[:6])}{'…' if len(names) > 6 else ''}")
                    elif itype == 'mcp_call':
                        label = field(item, 'server_label') or field(item, 'serverLabel')
                        name = field(item, 'name')
                        output = field(item, 'output')
                        if output is None:
                            err = field(item, 'error')
                            if err:
                                calls.append(f"{label}.{name} error: {err}")
                            continue
                        text = ''
                        try:
                            s = str(output)
                            text = s if len(s) <= 240 else s[:240] + '…'
                        except Exception:
                            text = '[output]'
                        if label and name:
                            calls.append(f"{label}.{name} → {text}")
                    elif itype == 'mcp_approval_request':
                        label = field(item, 'server_label') or field(item, 'serverLabel')
                        name = field(item, 'name')
                        calls.append(f"Approval requested for {label}.{name}")
                except Exception:
                    continue
            parts = []
            if imported:
                parts.append("Imported MCP tools: " + "; ".join(imported))
            if calls:
                parts.append("MCP results: " + "; ".join(calls))
            return "\n\n".join(parts)
        except Exception:
            return ''

    def _capture_mcp_approvals(self, outputs: list) -> None:
        """Scan outputs for mcp_approval_request and remember IDs for next turn."""
        try:
            if not isinstance(outputs, list):
                return
            pending: list[dict] = []
            for item in outputs:
                try:
                    if field(item, 'type') == 'mcp_approval_request':
                        pending.append({
                            'id': field(item, 'id'),
                            'approval_request_id': field(item, 'approval_request_id') or field(item, 'id'),
                            'server_label': field(item, 'server_label') or field(item, 'serverLabel'),
                            'name': field(item, 'name'),
                            'arguments': field(item, 'arguments'),
                        })
                except Exception:
                    continue
            if pending:
                self._pending_mcp_approvals = pending
        except Exception:
            pass

    # --- Message/Input assembly ----------------------------------------
    def _assemble_instructions(self) -> str | None:
        prompt_ctx = self.session.get_context('prompt')
        if not prompt_ctx:
            return None
        content = prompt_ctx.get().get('content')
        if content is None:
            return None
        return content if content.strip() != '' else ' '

    def _assemble_input(self, turns=None) -> List[Dict[str, Any]]:
        """Assemble typed input, preserving native output items and images."""
        if turns is None:
            chat = self.session.get_context('chat')
            turns = chat.get() if chat else []
        input_items = []
        params = self.session.get_params()
        model = params.get('model_name', params.get('model'))
        for turn in turns:
            role = turn.get('role')
            if role == 'assistant' and 'responses_output' in turn:
                if (turn.get('responses_model') == model
                        and turn.get('message', '') == turn.get('responses_text', '')):
                    input_items.extend(deepcopy(turn.get('responses_input') or []))
                    input_items.extend(deepcopy(turn['responses_output']))
                    continue
            if role == 'tool':
                call_id = turn.get('tool_call_id') or turn.get('id')
                if call_id:
                    input_items.append({'type': 'function_call_output', 'call_id': call_id,
                                        'output': turn.get('message') or ''})
                continue
            if role not in ('user', 'assistant', 'system', 'developer'):
                continue
            if role == 'assistant' and turn.get('tool_calls'):
                for call in turn['tool_calls']:
                    arguments = call.get('arguments')
                    input_items.append({
                        'type': 'function_call', 'call_id': call.get('id') or call.get('call_id'),
                        'name': call.get('api_name') or call.get('name'),
                        'arguments': arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
                    })
            text_parts = []
            images = []
            for context in turn.get('context') or []:
                if context.get('type') == 'image':
                    if not params.get('vision', False):
                        continue
                    data = context['context'].get()
                    images.append({'type': 'input_image',
                                   'image_url': f"data:{data['mime_type']};base64,{data['content']}"})
                else:
                    text_parts.append(context)
            text = turn.get('message') or ''
            if text_parts:
                context_text = ProcessContextsAction.process_contexts_for_assistant(text_parts)
                if context_text:
                    text = str(context_text) + ('\n\n' + text if text else '')
            # A tool-call-only assistant must not acquire an extra blank message.
            if role == 'assistant' and turn.get('tool_calls') and not text:
                continue
            if images:
                content = [{'type': 'input_text', 'text': text or ' '}, *images]
            else:
                content = text or ' '
            input_items.append({'role': role, 'content': content})
        return input_items

    def _chain_input(self, full_input, params):
        """Continue only an unchanged, provider-visible prefix we actually sent."""
        if not (params.get('store') and params.get('use_previous_response')
                and self._last_stored and self._last_response_id
                and self._response_model == params.get('model_name', params.get('model'))):
            return full_input, False
        chat = self.session.get_context('chat')
        turns = chat.get() if chat else []
        for index in range(len(turns) - 1, -1, -1):
            if turns[index].get('responses_response_id') == self._last_response_id:
                prefix = self._assemble_input(turns[:index + 1])
                if prefix == self._chain_prefix:
                    return self._assemble_input(turns[index + 1:]), True
                break
        # An edited, trimmed, cleared or loaded transcript must replay its own history.
        return full_input, False

    def _approval_inputs(self, params):
        """Resolve pending MCP approvals through the existing interaction broker."""
        inputs = []
        for request in self._pending_mcp_approvals:
            request_id = request.get('approval_request_id') or request.get('id')
            if not request_id:
                continue
            approved = bool(params.get('mcp_auto_approve', False))
            if not approved:
                prompt = f"Allow MCP tool {request.get('server_label')}.{request.get('name')}?"
                if request.get('arguments'):
                    prompt += '\nArguments: ' + str(request['arguments'])[:4000]
                spec = {'prompt': prompt, 'default': False}
                ui = getattr(self.session, 'ui', None)
                if getattr(getattr(ui, 'capabilities', None), 'blocking', False):
                    approved = ui.ask_bool(prompt, default=False)
                else:
                    broker = self.session.get_user_data('__interaction_broker__')
                    prompt_fn = getattr(broker, 'prompt', None)
                    if not callable(prompt_fn):
                        prompt_fn = self.session.get_user_data('__interaction_prompt__')
                    if not callable(prompt_fn):
                        raise RuntimeError('MCP approval requires an interactive UI or mcp_auto_approve')
                    approved = prompt_fn(InteractionNeeded('bool', spec, 'mcp_approval'))
                if approved is None:
                    raise RuntimeError('MCP approval cancelled')
            inputs.append({'type': 'mcp_approval_response', 'approve': bool(approved),
                           'approval_request_id': request_id})
        return inputs

    def _request_params(self, params, input_items, will_chain):
        """Map Memex settings to native Responses parameters without guessing models."""
        excluded = excluded_parameters(params)
        api = {'model': params.get('model_name', params.get('model')),
               'input': input_items, 'store': bool(params.get('store', False))}
        instructions = self._assemble_instructions()
        if instructions:
            api['instructions'] = instructions
        if will_chain:
            api['previous_response_id'] = self._last_response_id
        for key in ('temperature', 'top_p', 'max_output_tokens', 'include', 'metadata',
                    'parallel_tool_calls', 'tool_choice', 'text', 'truncation',
                    'service_tier', 'user', 'safety_identifier', 'prompt_cache_key',
                    'prompt_cache_retention'):
            if params.get(key) is not None:
                api[key] = deepcopy(params[key])
        if 'max_output_tokens' not in api:
            for key in ('max_completion_tokens', 'max_tokens'):
                if params.get(key) is not None and key not in excluded:
                    api['max_output_tokens'] = params[key]
                    break
        reasoning = params.get('reasoning')
        if isinstance(reasoning, dict):
            api['reasoning'] = deepcopy(reasoning)
        if params.get('reasoning_effort') is not None and 'reasoning_effort' not in excluded:
            api.setdefault('reasoning', {})['effort'] = str(params['reasoning_effort']).lower()
        if params.get('reasoning_summary') is not None and 'reasoning_summary' not in excluded:
            api.setdefault('reasoning', {})['summary'] = params['reasoning_summary']
        if params.get('verbosity') is not None and 'verbosity' not in excluded:
            api.setdefault('text', {})['verbosity'] = str(params['verbosity']).lower()
        if params.get('extra_body') is not None:
            api['extra_body'] = extra_body(params['extra_body'])
            for key in excluded:
                api['extra_body'].pop(key, None)
        mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
        if mode == 'official':
            tools = self.get_tools_for_request()
            if tools:
                api['tools'] = tools
        if params.get('stream'):
            api['stream'] = True
        for key in excluded:
            api.pop(key, None)
        return api

    def _finish_response(self, response):
        """Record terminal output once, using call_id for function-result pairing."""
        self._last_response = response
        self._last_status = field(response, 'status') or 'completed'
        self._last_response_id = field(response, 'id')
        self._last_stored = bool(self.last_api_param.get('store')) and self._last_status != 'failed'
        self._last_finish_reason = 'stop'
        if self._last_status == 'incomplete':
            reason = field(field(response, 'incomplete_details'), 'reason')
            self._last_finish_reason = 'length' if reason == 'max_output_tokens' else reason or 'incomplete'
        elif self._last_status == 'failed':
            self._last_finish_reason = 'error'
        self._record_usage(field(response, 'usage'))
        outputs = field(response, 'output', []) or []
        self._last_output_items = [as_dict(item) for item in outputs]
        self._last_tool_calls = []
        for item in outputs:
            if field(item, 'type') != 'function_call':
                continue
            api_name = field(item, 'name')
            arguments, invalid = parse_tool_arguments(field(item, 'arguments'))
            self._last_tool_calls.append({
                'id': field(item, 'call_id') or field(item, 'id'),
                'name': self._tool_api_to_cmd.get(api_name, api_name), 'api_name': api_name,
                'arguments': arguments,
                'truncated': invalid or self._last_status != 'completed'
                             or field(item, 'status') == 'incomplete',
            })
        if self._last_tool_calls and self._last_status == 'completed':
            self._last_finish_reason = 'tool_calls'
        self._pending_mcp_approvals = []
        self._capture_mcp_approvals(outputs)
        self._response_model = self.last_api_param.get('model')
        self._chain_prefix = deepcopy(self._request_full_input) + self._last_output_items
        summary = self._summarize_mcp_outputs(outputs)
        if summary:
            self._log('mcp_event', 'summary', {'text': summary})

    def _log(self, event, *args):
        try:
            logger = self.session.utils.logger
            getattr(logger, event)(*args, component='providers.openairesponses')
        except Exception:
            pass

    def _response_text(self, response):
        """Surface refusals and terminal errors as well as normal output text."""
        text = field(response, 'output_text') or ''
        refusals = []
        if not text:
            parts = []
            for item in field(response, 'output', []) or []:
                if field(item, 'type') != 'message':
                    continue
                for part in field(item, 'content', []) or []:
                    if field(part, 'type') == 'output_text':
                        parts.append(field(part, 'text', ''))
                    elif field(part, 'type') == 'refusal':
                        refusals.append(field(part, 'refusal', ''))
            text = ''.join(parts) or ''.join(refusals)
        if not text:
            text = self._summarize_mcp_outputs(field(response, 'output', []) or [])
        if self._last_status == 'failed':
            error = field(field(response, 'error'), 'message', 'Response failed')
            text += ('\n' if text else '') + f'Response failed: {error}'
        elif self._last_status == 'incomplete':
            reason = field(field(response, 'incomplete_details'), 'reason', 'unknown')
            text += ('\n' if text else '') + f'[Response incomplete: {reason}]'
        return text

    def chat(self) -> Any:
        """Create a native Responses request and retain its terminal output."""
        start = time()
        self.turn_usage = None
        self._cache_write_headers = {}
        self._last_tool_calls = None
        self._last_output_items = []
        self._last_status = None
        self._last_finish_reason = None
        self._delivered_text = ''
        self._last_response = None
        self.last_api_param = None
        try:
            params = self.session.get_params()
            self._usage_params = deepcopy(params)
            full_input = self._assemble_input()
            input_items, will_chain = self._chain_input(full_input, params)
            approval_ids = {field(item, 'id') for item in full_input
                            if field(item, 'type') == 'mcp_approval_request'}
            self._pending_mcp_approvals = [
                request for request in self._pending_mcp_approvals
                if (request.get('approval_request_id') or request.get('id')) in approval_ids
            ]
            approvals = self._approval_inputs(params)
            self._request_approvals = deepcopy(approvals)
            input_items = input_items + approvals
            if not input_items and not will_chain:
                input_items = [{'role': 'user', 'content': ' '}]
            self._request_full_input = full_input + approvals
            api = self._request_params(params, input_items, will_chain)
            self.last_api_param = api
            self._log('provider_start', {
                'provider': 'OpenAIResponses', 'model': api.get('model'),
                'stream': bool(api.get('stream')), 'store': api.get('store'),
                'chain_minimize': will_chain,
            })
            response = self._create_response(self._client.responses, api)
            if approvals:
                self._pending_mcp_approvals = []
            if api.get('stream'):
                return response
            self._finish_response(response)
            self._delivered_text = self._response_text(response)
            return self._delivered_text
        except Exception as exc:
            self._last_response = None
            self._last_response_id = None
            self._last_stored = False
            self._last_finish_reason = 'error'
            return f'An error occurred in OpenAIResponsesProvider: {exc}'
        finally:
            self.running_usage['total_time'] += time() - start
            if not self.last_api_param or not self.last_api_param.get('stream'):
                self._log('provider_done', {
                    'response_id': self._last_response_id,
                    'elapsed_ms': int((time() - start) * 1000),
                })

    def stream_chat(self) -> Generator[Any, None, None]:
        """Consume typed events; incomplete streams never expose executable calls."""
        response = self.chat()
        if isinstance(response, str):
            if response:
                yield response
            return
        if response is None:
            return
        start = time()
        terminal = False
        try:
            for event in response:
                event_type = field(event, 'type')
                if event_type in ('response.output_text.delta', 'response.refusal.delta'):
                    delta = field(event, 'delta')
                    if isinstance(delta, str) and delta:
                        self._delivered_text += delta
                        yield delta
                elif event_type in ('response.completed', 'response.incomplete', 'response.failed'):
                    final = field(event, 'response')
                    if final is None:
                        raise ValueError(f'{event_type} omitted its response')
                    self._finish_response(final)
                    terminal = True
                    text = self._response_text(final)
                    if not self._delivered_text:
                        self._delivered_text = text
                        if text:
                            yield text
                    elif self._last_status in ('incomplete', 'failed'):
                        # The final text contains the visible terminal diagnostic.
                        diagnostic = text.rsplit('\n', 1)[-1]
                        self._delivered_text += '\n' + diagnostic
                        yield '\n' + diagnostic
                    break
                elif event_type == 'error':
                    raise RuntimeError(field(event, 'message', 'Responses stream error'))
            if not terminal:
                raise RuntimeError('Stream ended before a terminal response event')
        except Exception as exc:
            terminal = False
            yield f'Stream interrupted (Responses): {exc}'
        finally:
            if not terminal:
                self._last_response = None
                self._last_response_id = None
                self._last_stored = False
                self._last_tool_calls = None
                self._last_output_items = []
                self._last_finish_reason = 'error'
            close = getattr(response, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            self.running_usage['total_time'] += time() - start
            self._log('provider_done', {
                'response_id': self._last_response_id,
                'elapsed_ms': int((time() - start) * 1000),
            })

    def get_assistant_metadata(self):
        """Return serializable native output for subsequent tool and user turns."""
        if self._last_response is None or self._last_status in (None, 'failed'):
            return {}
        return {
            'responses_output': deepcopy(self._last_output_items),
            'responses_input': deepcopy(self._request_approvals),
            'responses_response_id': self._last_response_id,
            'responses_model': self._response_model,
            'responses_text': self._delivered_text,
        }

    def get_finish_reason(self):
        """Expose incomplete token limits to the shared turn runner."""
        return self._last_finish_reason

    def reset_usage(self):
        """Clear accounting and server conversation state when the chat is cleared."""
        super().reset_usage()
        self._last_response = None
        self._last_response_id = None
        self._last_stored = False
        self._last_output_items = []
        self._last_tool_calls = None
        self._pending_mcp_approvals = []
        self._chain_prefix = None
        self._last_status = None
        self._last_finish_reason = None

    def cleanup(self):
        """Close the SDK client when a session ends or rebuilds its provider."""
        self._client.close()

    # --- Introspection --------------------------------------------------
    def get_messages(self) -> Any:
        """Return a Chat Completions-style view for introspection.

        This mirrors the OpenAIProvider output shape used by `show messages`:
        - Optional system/developer entry with a plain content string
        - Chat turns with modern content array: [{'type':'text','text':...}, ...]
        - Contexts summarized as a leading text block; image contexts noted as image_url entries
        """
        out: List[Dict[str, Any]] = []
        # Prompt as system/developer
        if self.session.get_context('prompt'):
            role = 'system' if self.session.get_params().get('use_old_system_role', False) else 'developer'
            prompt_content = self.session.get_context('prompt').get().get('content', '')
            if isinstance(prompt_content, str) and prompt_content.strip() == '':
                prompt_content = ' '
            out.append({'role': role, 'content': prompt_content})

        chat = self.session.get_context('chat')
        if not chat:
            return out

        for turn in chat.get():
            role = turn.get('role')
            if not role:
                continue

            # Modern content array
            content: List[Dict[str, Any]] = []
            turn_contexts: List[Dict[str, Any]] = []

            # Message text
            message_text = turn.get('message') or ''
            if isinstance(message_text, str) and message_text.strip() == '':
                content.append({'type': 'text', 'text': ' '})
            elif isinstance(message_text, str):
                content.append({'type': 'text', 'text': message_text})

            # Process contexts
            if 'context' in turn and turn['context']:
                for ctx in turn['context']:
                    if ctx.get('type') == 'image':
                        if not self.session.get_params().get('vision', False):
                            continue
                        try:
                            img_data = ctx['context'].get()
                            content.append({
                                'type': 'image_url',
                                'image_url': {
                                    'url': f"data:{img_data['mime_type']};base64,{img_data['content']}"
                                }
                            })
                        except Exception:
                            # Fall back to a placeholder
                            content.append({'type': 'text', 'text': '[IMAGE]'} )
                    else:
                        turn_contexts.append(ctx)
                if turn_contexts:
                    text_ctx = ProcessContextsAction.process_contexts_for_assistant(turn_contexts)
                    if text_ctx:
                        content.insert(0, {'type': 'text', 'text': text_ctx})

            view = {'role': role, 'content': content}
            if role == 'assistant' and turn.get('tool_calls'):
                view['tool_calls'] = [{
                    'id': call.get('id') or call.get('call_id'), 'type': 'function',
                    'function': {
                        'name': call.get('api_name') or call.get('name'),
                        'arguments': (call['arguments'] if isinstance(call.get('arguments'), str)
                                      else json.dumps(call.get('arguments') or {})),
                    },
                } for call in turn['tool_calls']]
            if role == 'tool' and turn.get('tool_call_id'):
                view['tool_call_id'] = turn['tool_call_id']
            out.append(view)

        return out

    def get_full_response(self) -> Any:
        return self._last_response

    # Normalized calls are consumed once by the turn runner.
    def get_tool_calls(self) -> List[Dict[str, Any]]:
        calls = list(self._last_tool_calls or [])
        # Clear after read so we don't re-run tools on the follow-up turn
        self._last_tool_calls = None
        return calls

    # Provider-native tool spec construction for Responses API
    def get_tools_for_request(self) -> list:
        try:
            cmd = self.session.get_action('assistant_commands')
            if not cmd or not hasattr(cmd, 'get_tool_specs'):
                return []
            # Build specs first (this call records the fresh API→canonical mapping in session)
            canonical = cmd.get_tool_specs() or []
            # Refresh mapping after building specs so it reflects this turn's tool list
            try:
                self._tool_api_to_cmd = self.session.get_user_data('__tool_api_to_cmd__') or {}
            except Exception:
                self._tool_api_to_cmd = {}
            out = []
            # Config: allow optional properties to be explicitly null
            try:
                allow_nullable = bool(self.session.get_params().get('nullable_optionals', False))
            except Exception:
                allow_nullable = False
            for spec in canonical:
                try:
                    params = strict_schema(
                        spec.get('parameters') or {'type': 'object', 'properties': {}},
                        nullable_optionals=allow_nullable,
                    )
                    out.append({
                        'type': 'function',
                        'name': spec.get('name'),
                        'description': spec.get('description'),
                        'parameters': params,
                        'strict': True,
                    })
                except Exception:
                    continue
            # Add MCP built-in tools when app-side MCP is active
            try:
                mcp_active = bool(self.session.get_option('MCP', 'active', fallback=False))
            except Exception:
                mcp_active = False
            if mcp_active:
                out.extend(self._build_mcp_tools())
            return out
        except Exception:
            return []

    # --- Embeddings ---------------------------------------------------
    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Create embeddings using OpenAI embeddings API via the same client."""
        chosen = model or self.session.get_tools().get('embedding_model') or 'text-embedding-3-small'
        resp = self._client.embeddings.create(model=chosen, input=texts)
        return [item.embedding for item in (resp.data or [])]

    # --- Built-in MCP tool wiring -------------------------------------
    def _build_mcp_tools(self) -> list:
        """Construct OpenAI Responses MCP tool entries from config/session params.

        Responses MCP tool entry shape:
          {
            "type": "mcp",
            "server_label": "dmcp",
            "server_description": "...",
            "server_url": "https://.../sse",
            "require_approval": "never",
            "allowed_tools": ["roll"],            # optional
            "authorization": "<token>",          # optional (connectors & some servers)
            "connector_id": "connector_dropbox"  # optional (connectors)
          }

        We map session params synthesized by mcp/bootstrap.py and optional extras:
          - mcp_servers: CSV or dict of label=url
          - mcp_headers_<label>: JSON dict; we extract Authorization Bearer token → authorization
          - mcp_allowed_<label>: CSV list of tool names
          - mcp_require_approval[ _<label> ]: always|never
          - mcp_description_<label>: optional description
          - mcp_connector_id_<label>: optional connector id (when using connectors)
          - mcp_token_<label> or mcp_authorization_<label>: explicit token override
        """
        params = self.session.get_params() or {}
        servers_val = params.get('mcp_servers')
        servers: dict[str, str] = {}

        # Parse servers
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
            # else: unsupported type → ignore
        except Exception:
            servers = {}

        tools = []
        for label, url in servers.items():
            try:
                tool: dict = {'type': 'mcp', 'server_label': label}
                if url:
                    tool['server_url'] = url

                # Optional description
                desc_key = f'mcp_description_{label}'
                if isinstance(params.get(desc_key), str) and params.get(desc_key).strip():
                    tool['server_description'] = params.get(desc_key).strip()

                # Optional authorization from headers or explicit token
                token = None
                hdr_key = f'mcp_headers_{label}'
                headers_val = params.get(hdr_key)
                headers_obj = None
                if isinstance(headers_val, dict):
                    headers_obj = headers_val
                elif isinstance(headers_val, str) and headers_val.strip().startswith('{'):
                    try:
                        parsed = json.loads(headers_val)
                        if isinstance(parsed, dict):
                            headers_obj = parsed
                    except Exception:
                        headers_obj = None
                if isinstance(headers_obj, dict):
                    for k, v in headers_obj.items():
                        if str(k).lower() == 'authorization':
                            token = str(v)
                            break
                if not token:
                    for tkey in (f'mcp_token_{label}', f'mcp_authorization_{label}'):
                        if isinstance(params.get(tkey), str) and params.get(tkey).strip():
                            token = params.get(tkey).strip()
                            break
                if token:
                    # Pass as 'authorization' per docs; strip leading 'Bearer ' if present
                    t = token
                    if isinstance(t, str) and t.lower().startswith('bearer '):
                        t = t[7:].strip()
                    tool['authorization'] = t

                # Optional connector_id
                cid_key = f'mcp_connector_id_{label}'
                if isinstance(params.get(cid_key), str) and params.get(cid_key).strip():
                    tool['connector_id'] = params.get(cid_key).strip()

                # Optional per-server allowed tools: mcp_allowed_<label>
                allowed_key = f'mcp_allowed_{label}'
                allowed_val = params.get(allowed_key)
                if isinstance(allowed_val, str) and allowed_val.strip():
                    allow_list = [s.strip() for s in allowed_val.split(',') if s.strip()]
                    if allow_list:
                        tool['allowed_tools'] = allow_list

                # Optional approval policy: global or per-label
                ra = params.get(f'mcp_require_approval_{label}') or params.get('mcp_require_approval')
                if isinstance(ra, str) and ra.strip().lower() in ('always', 'never'):
                    tool['require_approval'] = ra.strip().lower()

                tools.append(tool)
            except Exception:
                continue

        return tools
