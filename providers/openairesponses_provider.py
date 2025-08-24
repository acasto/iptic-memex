import os
from time import time
from typing import Any, Generator, List, Dict

from openai import OpenAI

from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction


class OpenAIResponsesProvider(APIProvider):
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

        # usage tracking
        self.turn_usage = None
        self.running_usage: Dict[str, Any] = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

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

        if params.get('timeout') is not None:
            options['timeout'] = params['timeout']

        return OpenAI(**options)

    # --- Message/Input assembly ----------------------------------------
    def _assemble_instructions(self) -> str | None:
        prompt_ctx = self.session.get_context('prompt')
        if not prompt_ctx:
            return None
        content = prompt_ctx.get().get('content')
        if content is None:
            return None
        return content if content.strip() != '' else ' '

    def _stringify_content(self, content: Any) -> str:
        """Normalize a message 'content' field into plain text.

        - If already a string, return as-is.
        - If it's a list of parts (OpenAI-style), concatenate any text fields.
        - Otherwise, return empty string.
        """
        try:
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                out: List[str] = []
                for part in content:
                    if isinstance(part, dict):
                        # Common keys: 'type' with 'text' or 'content' variants
                        if 'text' in part and isinstance(part['text'], str):
                            out.append(part['text'])
                        elif 'content' in part and isinstance(part['content'], str):
                            out.append(part['content'])
                return "\n".join(out)
        except Exception:
            pass
        return ""

    def _assemble_input(self, chain_minimize: bool = False) -> List[Dict[str, Any]]:
        """Build Responses API `input` from chat history and contexts.

        We mirror the existing context assembly but target a simple, safe
        shape for Responses: a list of {role, content} where content is text.
        Non-text contexts are summarized into text via ProcessContextsAction
        for an initial MVP. (We can extend to typed parts later.)
        """
        input_items: List[Dict[str, Any]] = []
        chat = self.session.get_context('chat')
        if not chat:
            return input_items

        turns = chat.get()
        import json

        # When chaining with previous_response_id and minimization is enabled,
        # include only the latest interaction window (tool call + output), or
        # the last user message if no tools are involved.
        if chain_minimize and turns:
            idx_tool_assistant = None
            idx_last_tool = None
            for i in range(len(turns) - 1, -1, -1):
                t = turns[i]
                if idx_last_tool is None and t.get('role') == 'tool':
                    idx_last_tool = i
                if idx_tool_assistant is None and t.get('role') == 'assistant' and 'tool_calls' in t:
                    idx_tool_assistant = i
                if idx_tool_assistant is not None and idx_last_tool is not None:
                    break

            if idx_last_tool is not None and idx_tool_assistant is not None and idx_tool_assistant < idx_last_tool:
                # Include only the assistant function_call(s) and subsequent tool outputs
                window = turns[idx_tool_assistant:]
            else:
                # Include only the last user message
                # Find last user turn
                j = len(turns) - 1
                while j >= 0 and turns[j].get('role') != 'user':
                    j -= 1
                window = [turns[j]] if j >= 0 else [turns[-1]]
        else:
            window = turns

        for turn in window:
            role = turn.get('role')
            # Assistant tool calls: include as function_call items for pairing with outputs
            if role == 'assistant' and 'tool_calls' in turn:
                try:
                    for tc in (turn.get('tool_calls') or []):
                        name = tc.get('name')
                        args = tc.get('arguments')
                        call_id = tc.get('id') or tc.get('call_id')
                        if not name or not call_id:
                            continue
                        if not isinstance(args, str):
                            try:
                                args = json.dumps(args or {})
                            except Exception:
                                args = '{}'
                        input_items.append({
                            'type': 'function_call',
                            'call_id': call_id,
                            'name': name,
                            'arguments': args,
                        })
                except Exception:
                    pass
                # Do not include assistant text in input; continue to next turn
                continue
            # Convert tool outputs to function_call_output items
            if role == 'tool':
                call_id = turn.get('tool_call_id') or turn.get('id')
                output_text = turn.get('message') or ''
                try:
                    output_json = json.dumps(output_text)
                except Exception:
                    output_json = '""'
                if call_id:
                    input_items.append({'type': 'function_call_output', 'call_id': call_id, 'output': output_json})
                # Skip normal message handling for tool turns
                continue
            # Skip any other assistant messages
            if role == 'assistant':
                continue
            # Combine primary text + any non-image contexts into a single text block
            text_parts: List[str] = []
            # Primary message text (the canonical field used across the app)
            msg_text = turn.get('message')
            if isinstance(msg_text, str):
                text_parts.append(msg_text)

            # Collect non-image contexts into a text summary
            if 'context' in turn and turn['context']:
                other_contexts = []
                for ctx in turn['context']:
                    if ctx.get('type') == 'image':
                        # Defer rich image mapping to a future iteration
                        continue
                    other_contexts.append(ctx)
                if other_contexts:
                    summarized = ProcessContextsAction.process_contexts_for_assistant(other_contexts)
                    if summarized:
                        text_parts.append(str(summarized))

            if role:
                merged = "\n\n".join([p for p in text_parts if isinstance(p, str)])
                if merged.strip() == '':
                    merged = ' '
                input_items.append({'role': role, 'content': merged})

        return input_items

    # --- API calls ------------------------------------------------------
    def chat(self) -> Any:
        """Create a Responses request; return string or a streaming handle.

        For non-streaming requests, returns `response.output_text`.
        For streaming, returns the streaming iterator (consumed by stream_chat).
        """
        start = time()
        try:
            p = self.session.get_params()
            instructions = self._assemble_instructions()
            # Determine if we'll chain with previous_response_id and minimize input
            chain_minimize = False
            will_chain = bool(p.get('store') and p.get('use_previous_response') and self._last_response_id)
            if will_chain and bool(p.get('chain_minimize_input', True)):
                chain_minimize = True
            input_items = self._assemble_input(chain_minimize=chain_minimize)

            # Base params
            api_params: Dict[str, Any] = {
                'model': p.get('model_name', p.get('model')),
            }
            if instructions:
                api_params['instructions'] = instructions
            # Always include input; Responses requires it
            if not input_items:
                input_items = [{'role': 'user', 'content': ' '}]
            api_params['input'] = input_items

            # Privacy/storage controls
            if p.get('store') is not None:
                api_params['store'] = bool(p.get('store'))
            # Optional: use previous response id when storing is enabled
            if api_params.get('store') and p.get('use_previous_response') and self._last_response_id:
                api_params['previous_response_id'] = self._last_response_id

            # Token cap: Responses uses 'max_output_tokens'
            mct = p.get('max_completion_tokens') or p.get('max_tokens')
            if mct is not None:
                api_params['max_output_tokens'] = mct
            # Reasoning effort: Responses expects a nested 'reasoning' object
            if p.get('reasoning') and p.get('reasoning_effort') is not None:
                try:
                    api_params['reasoning'] = {'effort': str(p['reasoning_effort']).lower()}
                except Exception:
                    api_params['reasoning'] = {'effort': p['reasoning_effort']}

            # Tools (custom). For MVP, we keep this off unless explicitly provided later.
            try:
                mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
                if mode == 'official':
                    tools_spec = self.get_tools_for_request() or []
                    if tools_spec:
                        api_params['tools'] = tools_spec
                        if p.get('tool_choice') is not None:
                            api_params['tool_choice'] = p.get('tool_choice')
            except Exception:
                pass

            # Streaming
            stream = bool(p.get('stream'))
            if stream:
                api_params['stream'] = True
                # Responses API does not accept stream_options.include_usage; omit entirely
                resp = self._client.responses.create(**api_params)
                self._last_response = resp
                return resp

            # Non-streaming
            resp = self._client.responses.create(**api_params)
            self._last_response = resp
            try:
                # Track response id for optional chaining
                self._last_response_id = getattr(resp, 'id', None)
                # Usage (best-effort; Responses may differ in shape)
                usage = getattr(resp, 'usage', None)
                if usage is not None:
                    self.turn_usage = usage
                    # Prefer Responses fields: input_tokens/output_tokens
                    ti = getattr(usage, 'input_tokens', None)
                    to = getattr(usage, 'output_tokens', None)
                    if ti is None:
                        ti = getattr(usage, 'prompt_tokens', 0)
                    if to is None:
                        to = getattr(usage, 'completion_tokens', 0)
                    self.running_usage['total_in'] += ti or 0
                    self.running_usage['total_out'] += to or 0
                    # Reasoning tokens (Responses: output_tokens_details)
                    try:
                        otd = getattr(usage, 'output_tokens_details', None)
                        if isinstance(otd, dict):
                            rt = otd.get('reasoning_tokens', 0)
                        else:
                            rt = getattr(otd, 'reasoning_tokens', 0)
                        if rt:
                            self.running_usage['reasoning_tokens'] = self.running_usage.get('reasoning_tokens', 0) + rt
                    except Exception:
                        pass
                # Parse function tool calls from non-streaming outputs
                self._last_tool_calls = []
                outputs = getattr(resp, 'output', None)
                if isinstance(outputs, list):
                    import json
                    for item in outputs:
                        try:
                            if getattr(item, 'type', None) == 'function_call':
                                name = getattr(item, 'name', None)
                                args = getattr(item, 'arguments', None)
                                call_id = getattr(item, 'id', None) or getattr(item, 'call_id', None)
                                args_obj = {}
                                if isinstance(args, str):
                                    try:
                                        args_obj = json.loads(args)
                                    except Exception:
                                        args_obj = {}
                                elif isinstance(args, dict):
                                    args_obj = args
                                self._last_tool_calls.append({'id': call_id, 'name': name, 'arguments': args_obj})
                        except Exception:
                            continue
            except Exception:
                pass

            # Output text helper
            text = getattr(resp, 'output_text', None)
            return text if isinstance(text, str) else ''

        except Exception as e:
            self._last_response = None
            return f"An error occurred in OpenAIResponsesProvider: {e}"
        finally:
            self.running_usage['total_time'] += time() - start

    def stream_chat(self) -> Generator[Any, None, None]:
        """Stream Responses output as text. MVP: best-effort handling.

        If `chat()` returned a string, just yield it. If it returned a
        streaming iterator of events, we attempt to surface text deltas
        and capture usage on the terminal event.
        """
        resp = self.chat()
        start = time()

        if isinstance(resp, str):
            if resp:
                yield resp
            return

        if resp is None:
            return

        try:
            for event in resp:
                # Typed events per Responses streaming API
                try:
                    etype = getattr(event, 'type', None)
                    resp_obj = getattr(event, 'response', None)

                    # Text deltas only
                    if etype == 'response.output_text.delta':
                        delta = getattr(event, 'delta', None)
                        if isinstance(delta, str) and delta:
                            yield delta
                        # Continue to allow ID capture below as well

                    # Capture response id for chaining on any event that carries it
                    if resp_obj is not None and getattr(resp_obj, 'id', None):
                        try:
                            self._last_response_id = getattr(resp_obj, 'id', None)
                        except Exception:
                            pass

                    # On completed, record usage and parse tool calls
                    if etype == 'response.completed' and resp_obj is not None:
                        usage = getattr(resp_obj, 'usage', None)
                        if usage is not None:
                            self.turn_usage = usage
                            ti = getattr(usage, 'input_tokens', None)
                            to = getattr(usage, 'output_tokens', None)
                            if ti is None:
                                ti = getattr(usage, 'prompt_tokens', 0)
                            if to is None:
                                to = getattr(usage, 'completion_tokens', 0)
                            self.running_usage['total_in'] += ti or 0
                            self.running_usage['total_out'] += to or 0
                            # Reasoning tokens from streaming usage
                            try:
                                otd = getattr(usage, 'output_tokens_details', None)
                                if isinstance(otd, dict):
                                    rt = otd.get('reasoning_tokens', 0)
                                else:
                                    rt = getattr(otd, 'reasoning_tokens', 0)
                                if rt:
                                    self.running_usage['reasoning_tokens'] = self.running_usage.get('reasoning_tokens', 0) + rt
                            except Exception:
                                pass

                        # Parse and normalize function tool calls from final output
                        outputs = getattr(resp_obj, 'output', None)
                        if isinstance(outputs, list):
                            import json
                            calls = []
                            for item in outputs:
                                try:
                                    if getattr(item, 'type', None) == 'function_call':
                                        name = getattr(item, 'name', None)
                                        args = getattr(item, 'arguments', None)
                                        call_id = getattr(item, 'id', None) or getattr(item, 'call_id', None)
                                        args_obj = {}
                                        if isinstance(args, str):
                                            try:
                                                args_obj = json.loads(args)
                                            except Exception:
                                                args_obj = {}
                                        elif isinstance(args, dict):
                                            args_obj = args
                                        calls.append({'id': call_id, 'name': name, 'arguments': args_obj})
                                except Exception:
                                    continue
                            if calls:
                                self._last_tool_calls = calls
                except Exception:
                    # If we don't recognize the event, ignore quietly
                    pass
        except Exception as e:
            yield f"Stream interrupted (Responses): {e}"
        finally:
            self.running_usage['total_time'] += time() - start

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
                        try:
                            img_data = ctx['context'].get()
                            content.append({
                                'type': 'image_url',
                                'image_url': {
                                    'url': f"data:image/{img_data['mime_type'].split('/')[-1]};base64,{img_data['content']}"
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

            out.append({'role': role, 'content': content})

        return out

    def get_full_response(self) -> Any:
        return self._last_response

    # Tool calls normalization (MVP: none; return empty list)
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
            canonical = cmd.get_tool_specs() or []
            out = []
            # Config: allow optional properties to be explicitly null
            try:
                allow_nullable = bool(self.session.get_params().get('nullable_optionals', False))
            except Exception:
                allow_nullable = False
            for spec in canonical:
                try:
                    params = spec.get('parameters') or {'type': 'object', 'properties': {}}
                    # Force Responses-required schema shape: additionalProperties must be present and false
                    try:
                        params = dict(params)
                    except Exception:
                        params = {'type': 'object', 'properties': {}}
                    if 'type' not in params:
                        params['type'] = 'object'
                    if 'properties' not in params or not isinstance(params['properties'], dict):
                        params['properties'] = {}
                    # Remove non-argument convenience fields (e.g., 'content')
                    try:
                        if 'content' in params['properties']:
                            params['properties'].pop('content', None)
                    except Exception:
                        pass
                    # Optionally mark optional properties as nullable
                    if allow_nullable:
                        try:
                            orig_required = []
                            try:
                                sp = spec.get('parameters') or {}
                                rq = sp.get('required')
                                if isinstance(rq, list):
                                    orig_required = [str(x) for x in rq]
                            except Exception:
                                orig_required = []
                            for key, sch in list(params['properties'].items()):
                                if key in orig_required:
                                    continue
                                # Wrap existing schema in anyOf [..., {type: 'null'}]
                                try:
                                    if isinstance(sch, dict):
                                        # If already anyOf, ensure null is included
                                        if 'anyOf' in sch and isinstance(sch['anyOf'], list):
                                            has_null = any(isinstance(it, dict) and it.get('type') == 'null' for it in sch['anyOf'])
                                            if not has_null:
                                                sch['anyOf'].append({'type': 'null'})
                                        else:
                                            # Preserve description at top-level if present
                                            desc = sch.get('description')
                                            first = dict(sch)
                                            # Create new schema with anyOf
                                            new_s = {'anyOf': [first, {'type': 'null'}]}
                                            if desc is not None:
                                                new_s['description'] = desc
                                            params['properties'][key] = new_s
                                except Exception:
                                    # If anything goes wrong, leave schema as-is
                                    continue
                        except Exception:
                            pass
                    params['additionalProperties'] = False
                    # Ensure required includes every key in properties as per Responses validation
                    prop_keys = list(params['properties'].keys())
                    req = params.get('required')
                    if not isinstance(req, list) or set(req) != set(prop_keys):
                        params['required'] = prop_keys

                    out.append({
                        'type': 'function',
                        'name': spec.get('name'),
                        'description': spec.get('description'),
                        'parameters': params,
                        'strict': True,
                    })
                except Exception:
                    continue
            # Optionally add built-in tools if explicitly enabled via config
            try:
                enabled_builtin = self.session.get_params().get('enable_builtin_tools')
                if enabled_builtin:
                    names = [s.strip() for s in str(enabled_builtin).split(',') if s.strip()]
                    for name in names:
                        # Minimal inclusion without provider-managed resources
                        out.append({'type': name})
            except Exception:
                pass
            return out
        except Exception:
            return []

    # --- Embeddings ---------------------------------------------------
    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Create embeddings using OpenAI embeddings API via the same client."""
        chosen = model or self.session.get_tools().get('embedding_model') or 'text-embedding-3-small'
        resp = self._client.embeddings.create(model=chosen, input=texts)
        return [item.embedding for item in (resp.data or [])]

    # --- Usage and cost -------------------------------------------------
    def get_usage(self) -> Dict[str, Any]:
        stats = {
            'total_in': self.running_usage.get('total_in', 0),
            'total_out': self.running_usage.get('total_out', 0),
            'total_tokens': self.running_usage.get('total_in', 0) + self.running_usage.get('total_out', 0),
            'total_time': self.running_usage.get('total_time', 0.0),
        }
        # Include reasoning totals if tracked
        if 'reasoning_tokens' in self.running_usage:
            stats['total_reasoning'] = self.running_usage.get('reasoning_tokens', 0)
        if self.turn_usage is not None:
            ti = getattr(self.turn_usage, 'input_tokens', None)
            to = getattr(self.turn_usage, 'output_tokens', None)
            if ti is None:
                ti = getattr(self.turn_usage, 'prompt_tokens', 0)
            if to is None:
                to = getattr(self.turn_usage, 'completion_tokens', 0)
            stats.update({
                'turn_in': ti or 0,
                'turn_out': to or 0,
                'turn_total': getattr(self.turn_usage, 'total_tokens', (ti or 0) + (to or 0)),
            })
            # Per-turn reasoning
            try:
                otd = getattr(self.turn_usage, 'output_tokens_details', None)
                if isinstance(otd, dict):
                    rt = otd.get('reasoning_tokens', None)
                else:
                    rt = getattr(otd, 'reasoning_tokens', None)
                if rt is not None:
                    stats['turn_reasoning'] = rt
            except Exception:
                pass
        return stats

    def reset_usage(self) -> None:
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0,
        }

    def get_cost(self) -> dict:
        """Calculate cost using model pricing (same approach as OpenAI provider)."""
        usage = self.get_usage()
        if not usage:
            return None
        try:
            price_unit = float(self.session.get_params().get('price_unit', 1000000))
            price_in = float(self.session.get_params().get('price_in', 0))
            price_out = float(self.session.get_params().get('price_out', 0))

            input_cost = (usage['total_in'] / price_unit) * price_in
            bill_reasoning = bool(self.session.get_params().get('bill_reasoning_as_output', True))
            total_reasoning = usage.get('total_reasoning', 0)
            billable_out = usage['total_out'] + (total_reasoning if bill_reasoning else 0)
            output_cost = (billable_out / price_unit) * price_out

            return {
                'input_cost': round(input_cost, 6),
                'output_cost': round(output_cost, 6),
                'total_cost': round(input_cost + output_cost, 6),
            }
        except (ValueError, TypeError):
            return None
