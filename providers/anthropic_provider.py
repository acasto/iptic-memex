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
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_writes += other.cache_writes
        self.cache_hits += other.cache_hits
        self.time_elapsed += other.time_elapsed


class AnthropicProvider(APIProvider):
    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        self.client = self._initialize_client()
        self.current_usage = Usage()
        self.total_usage = Usage()

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
            block = {
                'type': 'text',
                'text': message
            }
            # Add cache_control to the last message if caching is enabled
            if i == len(chat_turns) - 1 and self._is_caching_enabled():
                block['cache_control'] = {"type": "ephemeral"}
            content_blocks.append(block)

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
            for event in response:
                if event.type == "message_start" and event.message and event.message.usage:
                    self._update_usage_from_event(event.message.usage)
                elif event.type == "content_block_delta":
                    yield event.delta.text

        except Exception as e:
            yield self._format_error(e)

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
        self.current_usage.input_tokens = usage.input_tokens
        self.current_usage.output_tokens = usage.output_tokens

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
