import os
import json
from time import time
import openai
from openai import OpenAI
from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction
from typing import List, Optional
from copy import deepcopy
from providers.openai_common import (
    OpenAIUsage, excluded_parameters, extra_body, field, parse_tool_arguments, sdk_params,
)


class OpenAIProvider(OpenAIUsage, APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session):
        self.session = session
        self.last_api_param = None
        self._last_response = None
        self._last_stream_tool_calls = None  # capture tool calls seen during streaming
        self._last_finish_reason = None  # finish reason of the last response (stream or non-stream)
        self._last_reasoning = None  # reasoning_content accumulated from streamed deltas
        self._reasoning_field = None
        self._tool_calls_read = False

        # Initialize client with fresh params
        self.client = self._initialize_client()

        # Native parameters; backend extensions remain configurable via extra_body.
        self.parameters = [
            'model',
            'messages',
            'max_tokens',
            'max_completion_tokens',
            'reasoning_effort',
            'verbosity',
            'parallel_tool_calls',
            'frequency_penalty',
            'logit_bias',
            'logprobs',
            'top_logprobs',
            'n',
            'presence_penalty',
            'response_format',
            'seed',
            'stop',
            'stream',
            'temperature',
            'top_p',
            'tool_choice',
            'user',
            'extra_body'
        ]

        # place to store usage data
        self._init_usage()

    def _initialize_client(self) -> OpenAI:
        """Initialize OpenAI client with current connection parameters"""
        params = self.session.get_params()
        
        # set the options for the OpenAI API client
        options = {}
        if 'api_key' in params and params['api_key'] is not None:
            options['api_key'] = params['api_key']
        elif 'OPENAI_API_KEY' in os.environ:
            options['api_key'] = os.environ['OPENAI_API_KEY']
        else:
            options['api_key'] = 'none'  # in case we're using the library for something else but still need something set

        # Quick hack to provide a simple and clear message if someone clones the repo and forgets to set the API key
        # since OpenAI will probably be the most common provider. Will still error out on other providers that require
        # an API key though until we figure out a better way to handle  this (issue is above where we set it to none
        # so that it still works with local providers that don't require an API key)
        if params.get('provider', '').lower() == 'openai' and options.get('api_key') == 'none':
            # Raise instead of exiting so auxiliary usages (e.g., embeddings) can surface a clear error
            raise RuntimeError("OpenAI API Key is required")

        if 'base_url' in params and params['base_url'] is not None:
            base_url = params['base_url']
            
            # If there's also an endpoint parameter, combine them
            if 'endpoint' in params and params['endpoint'] is not None:
                endpoint = params['endpoint']
                # Make sure we don't double up on slashes
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

    # --- Embeddings ---------------------------------------------------
    def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """Create embeddings for a list of texts using OpenAI embeddings API."""
        # Resolve model: prefer explicit, else tools.embedding_model, else a sane default
        chosen = model or self.session.get_tools().get('embedding_model') or 'text-embedding-3-small'
        # OpenAI client exposes embeddings.create
        resp = self.client.embeddings.create(model=chosen, input=texts)
        # The SDK returns objects with .data[*].embedding
        return [item.embedding for item in (resp.data or [])]

    def chat(self):
        """
        Creates a chat completion request to the OpenAI API
        :return: response (str)
        """
        start_time = time()
        # Reset per-request response state
        self._last_finish_reason = None
        self._last_reasoning = None
        self._last_stream_tool_calls = None
        self._last_response = None
        self._reasoning_field = None
        self.turn_usage = None
        self.last_api_param = None
        self._tool_calls_read = False
        try:
            # Get fresh parameters instead of using cached self.params
            current_params = self.session.get_params()
            self._usage_params = deepcopy(current_params)
            
            messages = self.assemble_message()
            api_parms = {}

            # Check if this is a reasoning model
            is_reasoning = current_params.get('reasoning', False)

            # Get excluded parameters if any
            excluded_params = excluded_parameters(current_params)

            # Filter out excluded parameters from self.parameters
            valid_params = [p for p in self.parameters if p not in excluded_params]

            # Build parameter dictionary using fresh params
            for parameter in valid_params:
                if parameter in current_params and current_params[parameter] is not None:
                    # Handle stream parameter specially - only include if True
                    if parameter == 'stream':
                        if current_params[parameter] is True:
                            api_parms[parameter] = True
                    else:
                        api_parms[parameter] = current_params[parameter]

            # Use model_name for the API call, fallback to model if model_name doesn't exist
            api_model = current_params.get('model_name', current_params.get('model'))
            if api_model:
                api_parms['model'] = api_model

            # Native parameters work without the legacy reasoning flag. Keep its
            # max_tokens alias for existing model configs and third-party APIs.
            if 'max_completion_tokens' in api_parms:
                api_parms.pop('max_tokens', None)
            elif is_reasoning and 'max_tokens' in api_parms:
                if 'max_completion_tokens' not in excluded_params:
                    api_parms['max_completion_tokens'] = api_parms.pop('max_tokens')
            for key in ('reasoning_effort', 'verbosity'):
                if isinstance(api_parms.get(key), str):
                    api_parms[key] = api_parms[key].lower()
            if 'extra_body' in api_parms:
                api_parms['extra_body'] = extra_body(api_parms['extra_body'])
                for key in excluded_params:
                    api_parms['extra_body'].pop(key, None)

            if 'stream' in api_parms and api_parms['stream'] is True:
                # Only include stream_options when the backend supports it
                options = current_params.get('stream_options', True)
                if options and 'stream_options' not in excluded_params:
                    api_parms['stream_options'] = (
                        deepcopy(options) if isinstance(options, dict) else {'include_usage': True}
                    )

            # Attach official tool specs when enabled
            try:
                mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
                if mode == 'official':
                    tools_spec = self.get_tools_for_request() or []
                    if tools_spec:
                        api_parms['tools'] = tools_spec
                        if current_params.get('tool_choice') is not None:
                            api_parms['tool_choice'] = current_params.get('tool_choice')
            except Exception:
                pass

            api_parms['messages'] = messages
            for key in excluded_params:
                api_parms.pop(key, None)
            self.last_api_param = api_parms

            # Make the API call and store the full response
            create = self.client.chat.completions.create
            response = create(**sdk_params(create, api_parms))
            self._last_response = response
            try:
                choices = getattr(response, 'choices', None)
                if choices:
                    self._last_finish_reason = field(self._choice_zero(response), 'finish_reason')
                else:
                    self._last_finish_reason = None
            except Exception:
                self._last_finish_reason = None
            # TensorFold/older servers use reasoning_content; newer vLLM uses reasoning.
            try:
                msg = field(self._choice_zero(response), 'message')
                self._capture_reasoning(msg)
            except Exception:
                pass

            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                self._update_usage_stats(response)
                msg = field(self._choice_zero(response), 'message')
                return field(msg, 'content') or field(msg, 'refusal') or ''

        except Exception as e:
            self._last_response = None
            self._last_finish_reason = 'error'
            error_msg = "An error occurred:\n"
            if isinstance(e, openai.APIConnectionError):
                error_msg += "The server could not be reached\n"
                if e.__cause__:
                    error_msg += f"Cause: {str(e.__cause__)}\n"
            elif isinstance(e, openai.RateLimitError):
                error_msg += "Rate limit exceeded - please wait before retrying\n"
            elif isinstance(e, openai.APIStatusError):
                error_msg += f"Status code: {getattr(e, 'status_code', 'unknown')}\n"
                resp_obj = getattr(e, 'response', None)
                # Include response text/json for easier debugging
                try:
                    if resp_obj is not None:
                        body = None
                        if hasattr(resp_obj, 'text'):
                            body = resp_obj.text
                        elif hasattr(resp_obj, 'json'):
                            try:
                                body = resp_obj.json()
                            except Exception:
                                body = str(resp_obj)
                        else:
                            body = str(resp_obj)
                        error_msg += f"Response: {body}\n"
                    else:
                        error_msg += f"Response: {getattr(e, 'response', 'unknown')}\n"
                except Exception:
                    error_msg += f"Response: {getattr(e, 'response', 'unknown')}\n"
            else:
                error_msg += f"Unexpected error: {str(e)}\n"

            if self.last_api_param is not None:
                error_msg += "\nDebug info:\n"
                
                # Add URL information from the client
                if hasattr(self.client, 'base_url'):
                    error_msg += f"base_url: {self.client.base_url}\n"
                elif 'base_url' in current_params:
                    error_msg += f"base_url: {current_params['base_url']}\n"
                
                # Add endpoint if available
                if 'endpoint' in current_params:
                    error_msg += f"endpoint: {current_params['endpoint']}\n"
                
                # Add provider info for context
                if 'provider' in current_params:
                    error_msg += f"provider: {current_params['provider']}\n"
                
                for key, value in self.last_api_param.items():
                    # Don't print the full messages as they can be very long
                    if key == 'messages':
                        error_msg += f"{key}: <{len(value)} messages>\n"
                    else:
                        error_msg += f"{key}: {value}\n"

            try:
                self.session.ui.emit('error', {'message': error_msg})
            except Exception:
                pass
            return error_msg

        finally:
            self.running_usage['total_time'] += time() - start_time

    def _capture_reasoning(self, message):
        """Capture a server's reasoning field and remember it for replay."""
        preferred = self.session.get_params().get('reasoning_field')
        names = [preferred] if preferred else []
        names += [name for name in ('reasoning_content', 'reasoning') if name not in names]
        for name in names:
            value = field(message, name)
            if isinstance(value, str) and value:
                self._reasoning_field = name
                self._last_reasoning = (self._last_reasoning or '') + value
                break

    @staticmethod
    def _choice_zero(response):
        return next((choice for choice in field(response, 'choices', []) or []
                     if field(choice, 'index', 0) == 0), None)

    def get_assistant_metadata(self):
        """Keep the backend's reasoning field name with its transcript turn."""
        if self._reasoning_field:
            return {'reasoning_field': self._reasoning_field}
        return {}

    def _reasoning_for_turn(self, turn):
        content = turn.get('reasoning_content') or turn.get('reasoning')
        if not content:
            return {}
        name = (self.session.get_params().get('reasoning_field')
                or turn.get('reasoning_field') or 'reasoning_content')
        return {name: content}

    def stream_chat(self):
        """Stream choice zero and expose calls only after a terminal finish."""
        response = self.chat()
        start_time = time()
        if isinstance(response, str):
            if response:
                yield response
            return
        if response is None:
            return
        tool_calls_map = {}
        completed = False
        try:
            for chunk in response:
                choices = field(chunk, 'choices', []) or []
                choice = next((c for c in choices if field(c, 'index', 0) == 0), None)
                if choice is not None:
                    finish = field(choice, 'finish_reason')
                    if finish is not None:
                        self._last_finish_reason = finish
                        completed = finish in ('stop', 'tool_calls')
                    delta = field(choice, 'delta')
                    self._capture_reasoning(delta)
                    text = field(delta, 'content') or field(delta, 'refusal')
                    if text:
                        yield text
                    for call in field(delta, 'tool_calls', []) or []:
                        index = field(call, 'index', 0) or 0
                        fn = field(call, 'function')
                        record = tool_calls_map.setdefault(index, {'id': None, 'name': None, 'arguments': ''})
                        if field(call, 'id'):
                            record['id'] = field(call, 'id')
                        if field(fn, 'name'):
                            name = field(fn, 'name')
                            prior_name = record['name'] or ''
                            if not prior_name or name.startswith(prior_name):
                                record['name'] = name
                            elif name != prior_name:
                                record['name'] += name
                        if field(fn, 'arguments'):
                            record['arguments'] += field(fn, 'arguments')
                self._record_usage(field(chunk, 'usage'))
            if self._last_finish_reason is None:
                self._last_finish_reason = 'error'
                yield 'Stream interrupted: no terminal finish reason received'
        except Exception as exc:
            completed = False
            self._last_finish_reason = 'error'
            yield f'Stream interrupted: {exc}'
        finally:
            close = getattr(response, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            self.running_usage['total_time'] += time() - start_time
            out = []
            for index in sorted(tool_calls_map):
                record = tool_calls_map[index]
                arguments, invalid = parse_tool_arguments(record['arguments'])
                out.append({
                    'id': record['id'], 'name': record['name'],
                    'arguments': arguments,
                    'truncated': invalid or not completed or not record['id'] or not record['name'],
                })
            self._last_stream_tool_calls = out

    def assemble_message(self) -> list:
        """
        Assemble the message from the context, including image handling
        :return: message (list)
        """
        message = []
        if self.session.get_context('prompt'):
            # Use 'system' or 'developer' based on provider configuration
            role = 'system' if self.session.get_params().get('use_old_system_role', False) else 'developer'
            prompt_content = self.session.get_context('prompt').get()['content']
            if prompt_content.strip() == '':
                prompt_content = ' '  # Replace empty content with a space
            message.append({'role': role, 'content': prompt_content})

        chat = self.session.get_context('chat')
        if chat is not None:
            # Check if provider uses simple message format
            use_simple_format = self.session.get_params().get('use_simple_message_format', False)

            for idx, turn in enumerate(chat.get()):
                # Assistant tool calls: include 'tool_calls' array on assistant message
                if turn.get('role') == 'assistant' and 'tool_calls' in turn:
                    tool_calls_out = []
                    for tc in (turn.get('tool_calls') or []):
                        fn_name = tc.get('api_name') or tc.get('name')
                        args = tc.get('arguments')
                        # Chat Completions expects arguments as a JSON-encoded string
                        if not isinstance(args, str):
                            try:
                                args = json.dumps(args or {})
                            except Exception:
                                args = "{}"
                        tool_calls_out.append({
                            'id': tc.get('id'),
                            'type': 'function',
                            'function': {
                                'name': fn_name,
                                'arguments': args,
                            }
                        })
                    msg_out = {'role': 'assistant', 'content': turn.get('message') or None,
                               'tool_calls': tool_calls_out}
                    # Retransmit stored reasoning so the model keeps its chain of thought
                    msg_out.update(self._reasoning_for_turn(turn))
                    message.append(msg_out)
                    continue

                # Official tool outputs: include as tool role messages with tool_call_id
                if turn.get('role') == 'tool':
                    tool_call_id = turn.get('tool_call_id') or turn.get('id')
                    tool_msg = {'role': 'tool', 'content': turn.get('message') or ''}
                    if tool_call_id:
                        tool_msg['tool_call_id'] = tool_call_id
                    message.append(tool_msg)
                    continue
                # For simple format, we'll use a single string for content
                if use_simple_format:
                    turn_content = ""

                    # Process contexts if any exist
                    if 'context' in turn and turn['context']:
                        turn_contexts = []
                        for ctx in turn['context']:
                            # Image context is not supported in simple format
                            if ctx['type'] != 'image':
                                turn_contexts.append(ctx)

                        # Add text contexts
                        if turn_contexts:
                            text_context = ProcessContextsAction.process_contexts_for_assistant(turn_contexts)
                            if text_context:
                                turn_content += text_context + "\n\n"

                    # Add the message text
                    turn_content += turn['message']
                    if turn_content.strip() == '':
                        turn_content = ' '  # Replace empty content with a space
                    msg_out = {'role': turn['role'], 'content': turn_content}
                    if turn.get('role') == 'assistant':
                        msg_out.update(self._reasoning_for_turn(turn))
                    message.append(msg_out)
                else:
                    # Modern format with content array
                    content = []
                    turn_contexts = []

                    # Handle message text
                    if turn['message'].strip() == '':
                        content.append({'type': 'text', 'text': ' '})  # Replace empty content with a space
                    else:
                        content.append({'type': 'text', 'text': turn['message']})

                    # Process contexts
                    if 'context' in turn and turn['context']:
                        # Include images only when the current model supports vision
                        include_images = False
                        try:
                            include_images = bool(self.session.get_params().get('vision', False))
                        except Exception:
                            include_images = False
                        for ctx in turn['context']:
                            if ctx['type'] == 'image' and include_images:
                                img_data = ctx['context'].get()
                                # Format image data for OpenAI's API
                                content.append({
                                    'type': 'image_url',
                                    'image_url': {
                                        'url': f"data:image/{img_data['mime_type'].split('/')[-1]};base64,{img_data['content']}"
                                    }
                                })
                            else:
                                # Accumulate non-image contexts
                                turn_contexts.append(ctx)

                        # Add text contexts if any exist
                        if turn_contexts:
                            text_context = ProcessContextsAction.process_contexts_for_assistant(turn_contexts)
                            if text_context:
                                content.insert(0, {'type': 'text', 'text': text_context})

                    msg_out = {'role': turn['role'], 'content': content}
                    if turn.get('role') == 'assistant':
                        msg_out.update(self._reasoning_for_turn(turn))
                    message.append(msg_out)

        return message

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def get_finish_reason(self):
        """Finish reason of the last response ('stop', 'length', 'tool_calls', ...)."""
        return self._last_finish_reason

    def get_current_reasoning(self):
        """Reasoning content accumulated from the last response's deltas, if any."""
        return self._last_reasoning

    def get_tool_calls(self):
        """Return normalized tool calls from the last response, if present.

        Shape: [{"id": str, "name": str, "arguments": dict}]
        """
        # Prefer tool calls collected from streaming
        if getattr(self, '_tool_calls_read', False):
            return []
        self._tool_calls_read = True
        if self._last_stream_tool_calls:
            calls = self._last_stream_tool_calls
            self._last_stream_tool_calls = None
            return [self._normalize_call(call) for call in calls]
        resp = self._last_response
        out = []
        try:
            if not resp or not getattr(resp, 'choices', None):
                return out
            choice0 = self._choice_zero(resp)
            msg = getattr(choice0, 'message', None)
            tool_calls = getattr(msg, 'tool_calls', None) if msg else None
            if not tool_calls:
                return out
            for tc in tool_calls:
                fn = getattr(tc, 'function', None)
                name = getattr(fn, 'name', None) if fn else None
                args = getattr(fn, 'arguments', None) if fn else None
                args_obj, truncated_call = parse_tool_arguments(args)
                out.append(self._normalize_call({
                    'id': getattr(tc, 'id', None),
                    'name': name,
                    'arguments': args_obj,
                    'truncated': (truncated_call or not name or not getattr(tc, 'id', None)
                                  or self._last_finish_reason in ('length', 'content_filter')),
                }))
        except Exception:
            return []
        return out

    def _normalize_call(self, call):
        """Separate the dispatch name from the API-safe name needed for replay."""
        call = dict(call)
        call['api_name'] = call['name']
        try:
            mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
            call['name'] = mapping.get(call['name'], call['name'])
        except (AttributeError, TypeError):
            pass
        return call

    # Provider-native tool spec construction
    def get_tools_for_request(self) -> list:
        try:
            cmd = self.session.get_action('assistant_commands')
            if not cmd or not hasattr(cmd, 'get_tool_specs'):
                return []
            canonical = cmd.get_tool_specs() or []
            tools = []
            for spec in canonical:
                try:
                    tools.append({
                        'type': 'function',
                        'function': {
                            'name': spec.get('name'),
                            'description': spec.get('description'),
                            'parameters': spec.get('parameters') or {'type': 'object', 'properties': {}},
                        }
                    })
                except Exception:
                    continue
            return tools
        except Exception:
            return []

    def _update_usage_stats(self, response):
        """Record SDK usage, including nullable detail fields."""
        self._record_usage(field(response, 'usage'))

    def reset_usage(self):
        """Clear accounting and any unconsumed response state."""
        super().reset_usage()
        self._last_response = None
        self._last_finish_reason = None
        self._last_reasoning = None
        self._reasoning_field = None
        self._last_stream_tool_calls = None
        self._tool_calls_read = False

    def cleanup(self):
        """Close the SDK client when a session ends or rebuilds its provider."""
        self.client.close()
