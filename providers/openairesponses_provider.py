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
            # Keep behavior consistent with existing provider: require key when provider explicitly selected
            if params.get('provider', '').lower() == 'openairesponses':
                print("\nOpenAI API Key is required\n")
                quit()
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

    def _assemble_input(self) -> List[Dict[str, Any]]:
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
        for turn in turns:
            role = turn.get('role')
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
            input_items = self._assemble_input()

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
                    self.running_usage['total_in'] += getattr(usage, 'prompt_tokens', 0)
                    self.running_usage['total_out'] += getattr(usage, 'completion_tokens', 0)
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
                # The Responses SDK emits typed events. We try a few common shapes
                # but stay defensive to avoid breaking when shapes differ.
                try:
                    etype = getattr(event, 'type', None)
                    # Text deltas
                    if etype and 'output_text' in str(etype) and hasattr(event, 'delta'):
                        delta = getattr(event, 'delta', None)
                        if isinstance(delta, str) and delta:
                            yield delta
                    # Final usage
                    usage = getattr(event, 'usage', None)
                    if usage is not None:
                        self.turn_usage = usage
                        self.running_usage['total_in'] += getattr(usage, 'prompt_tokens', 0)
                        self.running_usage['total_out'] += getattr(usage, 'completion_tokens', 0)
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
        return list(self._last_tool_calls or [])

    # Provider-native tool spec construction (MVP: return empty)
    def get_tools_for_request(self) -> list:
        try:
            cmd = self.session.get_action('assistant_commands')
            if not cmd or not hasattr(cmd, 'get_tool_specs'):
                return []
            # TODO: Map canonical tool specs to Responses function schema
            # in a subsequent iteration.
            return []
        except Exception:
            return []

    # --- Usage and cost -------------------------------------------------
    def get_usage(self) -> Dict[str, Any]:
        stats = {
            'total_in': self.running_usage.get('total_in', 0),
            'total_out': self.running_usage.get('total_out', 0),
            'total_tokens': self.running_usage.get('total_in', 0) + self.running_usage.get('total_out', 0),
            'total_time': self.running_usage.get('total_time', 0.0),
        }
        if self.turn_usage is not None:
            stats.update({
                'turn_in': getattr(self.turn_usage, 'prompt_tokens', 0),
                'turn_out': getattr(self.turn_usage, 'completion_tokens', 0),
                'turn_total': getattr(self.turn_usage, 'total_tokens', 0),
            })
        return stats

    def reset_usage(self) -> None:
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0,
        }

    def get_cost(self) -> dict:
        # Reuse existing cost logic assumptions: defer to caller that knows pricing tables.
        usage = self.get_usage()
        if not usage:
            return None
        return {
            'currency': 'USD',
            'input_tokens': usage.get('total_in', 0),
            'output_tokens': usage.get('total_out', 0),
            'total_tokens': usage.get('total_tokens', 0),
            # Actual cost calculation depends on model-specific pricing; left to higher-level cost module.
            'estimated_cost': None,
        }
