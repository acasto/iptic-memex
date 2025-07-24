import os
from time import time
from dataclasses import dataclass
from typing import List, Dict, Any, Generator
from anthropic import Anthropic
from session_handler import APIProvider, SessionHandler


@dataclass
class Usage:
    """Tracks token usage and caching metrics for API calls"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_writes: int = 0
    cache_hits: int = 0
    time_elapsed: float = 0.0

    def update(self, other: 'Usage'):
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
    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        self.client = self._initialize_client()
        self.current_usage = Usage()
        self.total_usage = Usage()
        self._last_response = None

        self.parameters = {
            'model', 'max_tokens', 'system', 'messages', 'stop_sequences',
            'metadata', 'stream', 'temperature', 'top_k', 'top_p',
            'tools', 'tool_choice'
        }

    def _initialize_client(self) -> Anthropic:
        options = {}
        api_key = self.params.get('api_key') or os.getenv('ANTHROPIC_API_KEY')
        if api_key:
            options['api_key'] = api_key
        else:
            options['api_key'] = 'none'

        base_url = self.params.get('base_url')
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
            content_blocks = []
            context = turn.get('context')

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
                    'role': turn['role'],
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
        return self.params.get('prompt_caching', False)

    def _prepare_api_parameters(self) -> Dict[str, Any]:
        params = {
            key: value for key, value in self.params.items()
            if key in self.parameters and value is not None
        }
        params['system'] = self._build_system_content()
        params['messages'] = self._build_messages()
        return params

    def chat(self) -> Any:
        start_time = time()
        try:
            params = self._prepare_api_parameters()
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
        initial_usage = None  # to store usage from message_start which includes input_tokens
        final_usage = None  # will be updated later with message_delta usage info
        response = self.chat()

        if isinstance(response, str):
            yield response
            return

        try:
            for event in response:
                event_usage = None
                if event.type == "message_start" and event.message and event.message.usage:
                    event_usage = event.message.usage
                    initial_usage = event_usage
                    final_usage = event_usage
                elif event.type == "content_block_delta":
                    yield event.delta.text
                elif event.type == "message_delta":
                    if hasattr(event, 'usage') and event.usage:
                        event_usage = event.usage
                        final_usage = event_usage

                # Update cache metrics whenever they appear
                if event_usage:
                    if hasattr(event_usage, 'cache_creation_input_tokens'):
                        self.current_usage.cache_writes = event_usage.cache_creation_input_tokens
                    if hasattr(event_usage, 'cache_read_input_tokens'):
                        self.current_usage.cache_hits = event_usage.cache_read_input_tokens

        except Exception as e:
            yield self._format_error(e)

        if final_usage:
            # Merge initial input tokens if missing in final_usage.
            input_tokens = (
                initial_usage.input_tokens
                if initial_usage and hasattr(initial_usage, 'input_tokens')
                else 0
            )
            output_tokens = getattr(final_usage, 'output_tokens', 0)
            cache_writes = getattr(final_usage, 'cache_creation_input_tokens', 0)
            cache_hits = getattr(final_usage, 'cache_read_input_tokens', 0)
            # Create a merged usage object.
            merged_usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_writes=cache_writes,
                cache_hits=cache_hits,
            )
            self._update_usage_from_event(merged_usage)

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

            messages.append({
                'role': turn['role'],
                'context': context,
                'message': turn.get('message') or ""
            })
        return messages

    def _update_usage_from_response(self, response: Any) -> None:
        if hasattr(response, 'usage'):
            usage = response.usage
            self.current_usage.input_tokens = usage.input_tokens
            self.current_usage.output_tokens = usage.output_tokens

            if hasattr(usage, 'cache_creation_input_tokens'):
                self.current_usage.cache_writes = usage.cache_creation_input_tokens
            if hasattr(usage, 'cache_read_input_tokens'):
                self.current_usage.cache_hits = usage.cache_read_input_tokens

            self.total_usage.update(self.current_usage)

    def _update_usage_from_event(self, usage: Any) -> None:
        # Only update input_tokens if present; otherwise, keep the existing value.
        self.current_usage.input_tokens = getattr(usage, 'input_tokens', self.current_usage.input_tokens)
        self.current_usage.output_tokens = getattr(usage, 'output_tokens', self.current_usage.output_tokens)

        if hasattr(usage, 'cache_creation_input_tokens'):
            self.current_usage.cache_writes = usage.cache_creation_input_tokens
        if hasattr(usage, 'cache_read_input_tokens'):
            self.current_usage.cache_hits = usage.cache_read_input_tokens

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
        return {
            'total_in': self.total_usage.input_tokens,
            'total_out': self.total_usage.output_tokens,
            'total_tokens': self.total_usage.input_tokens + self.total_usage.output_tokens,
            'total_time': self.total_usage.time_elapsed,
            'total_cache_writes': self.total_usage.cache_writes,
            'total_cache_hits': self.total_usage.cache_hits,
            'turn_in': self.current_usage.input_tokens,
            'turn_out': self.current_usage.output_tokens,
            'turn_total': self.current_usage.input_tokens + self.current_usage.output_tokens,
            'turn_cache_writes': self.current_usage.cache_writes,
            'turn_cache_hits': self.current_usage.cache_hits
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
            price_unit = float(self.params.get('price_unit', 1000000))
            price_in = float(self.params.get('price_in', 0))
            price_out = float(self.params.get('price_out', 0))
            price_cache_in = float(self.params.get('price_cache_in', price_in))
            price_cache_out = float(self.params.get('price_cache_out', price_out))

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