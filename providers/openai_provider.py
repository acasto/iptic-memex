import ast
import os
from time import time
import openai
from openai import OpenAI
from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction
from typing import List, Optional


class OpenAIProvider(APIProvider):
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

        # Initialize client with fresh params
        self.client = self._initialize_client()

        # List of parameters that can be passed to the OpenAI API that we want to handle automatically
        # todo: add list of items for include/exclude to the providers config
        self.parameters = [
            'model',
            'messages',
            'max_tokens',
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
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

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

        if 'timeout' in params and params['timeout'] is not None:
            options['timeout'] = params['timeout']

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
        try:
            # Get fresh parameters instead of using cached self.params
            current_params = self.session.get_params()
            
            messages = self.assemble_message()
            api_parms = {}

            # Check if this is a reasoning model
            is_reasoning = current_params.get('reasoning', False)

            # Get excluded parameters if any
            excluded_params = []
            if is_reasoning:
                excluded_params = current_params.get('excluded_parameters', [])

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

            # Handle reasoning model specific logic
            if is_reasoning:
                # Initialize or get extra_body
                extra_body = api_parms.get('extra_body', {})
                if isinstance(extra_body, str):
                    # If extra_body is a string, parse it as a literal dict (safe)
                    try:
                        extra_body = ast.literal_eval(extra_body)
                    except (SyntaxError, ValueError) as e:
                        print(f"Warning: Could not parse extra_body string: {e}")
                        extra_body = {}

                # Handle max_tokens vs max_completion_tokens
                max_completion_tokens = current_params.get('max_completion_tokens')
                max_tokens = current_params.get('max_tokens')

                if max_completion_tokens is not None:
                    extra_body['max_completion_tokens'] = max_completion_tokens
                    # Remove max_tokens if it exists in api_parms
                    api_parms.pop('max_tokens', None)
                elif max_tokens is not None:
                    extra_body['max_completion_tokens'] = max_tokens
                    # Remove max_tokens from api_parms since we're using it as max_completion_tokens
                    api_parms.pop('max_tokens', None)

                # Handle reasoning_effort
                reasoning_effort = current_params.get('reasoning_effort')
                if reasoning_effort is not None:
                    # Normalize to lowercase
                    extra_body['reasoning_effort'] = reasoning_effort.lower()

                # Handle verbosity (low|medium|high) for reasoning-capable models
                verbosity = current_params.get('verbosity')
                if verbosity is not None:
                    # Normalize to lowercase and pass through mechanically
                    try:
                        extra_body['verbosity'] = str(verbosity).lower()
                    except Exception:
                        # Be lenient: if it can't be lowercased cleanly, just pass as-is
                        extra_body['verbosity'] = verbosity

                # Update api_parms with modified extra_body
                if extra_body:
                    api_parms['extra_body'] = extra_body

            if 'stream' in api_parms and api_parms['stream'] is True:
                # Only include stream_options when the backend supports it
                if self.session.get_params().get('stream_options', True):
                    api_parms['stream_options'] = {
                        'include_usage': True,
                    }

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
            self.last_api_param = api_parms

            # Make the API call and store the full response
            response = self.client.chat.completions.create(**api_parms)
            self._last_response = response
            try:
                choices = getattr(response, 'choices', None)
                if choices:
                    self._last_finish_reason = getattr(choices[0], 'finish_reason', None)
                else:
                    self._last_finish_reason = None
            except Exception:
                self._last_finish_reason = None
            # Capture reasoning_content from the message (thinking models)
            try:
                msg = getattr(response.choices[0], 'message', None) if getattr(response, 'choices', None) else None
                r = getattr(msg, 'reasoning_content', None) if msg else None
                if r:
                    self._last_reasoning = r
            except Exception:
                pass

            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                self._update_usage_stats(response)
                return response.choices[0].message.content

        except Exception as e:
            self._last_response = None
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

            print(error_msg)
            return error_msg

        finally:
            self.running_usage['total_time'] += time() - start_time

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()
        start_time = time()

        if isinstance(response, str):
            yield response
            return

        if response is None:
            return

        # Track tool_call deltas (Chat Completions streaming)
        tool_calls_map = {}  # index -> {id, name, arguments(str)}

        try:
            for chunk in response:
                # Handle content/tool_call chunks
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    fr = getattr(choice, 'finish_reason', None)
                    if fr is not None:
                        self._last_finish_reason = fr
                    delta = getattr(choice, 'delta', None)
                    if delta is not None:
                        # Text content delta
                        if getattr(delta, 'content', None):
                            yield delta.content
                        # Reasoning deltas (thinking models)
                        rdelta = getattr(delta, 'reasoning_content', None)
                        if rdelta:
                            if self._last_reasoning is None:
                                self._last_reasoning = ''
                            self._last_reasoning += rdelta
                        # Tool call deltas
                        tool_calls = getattr(delta, 'tool_calls', None)
                        if tool_calls:
                            try:
                                for tc in tool_calls:
                                    idx = getattr(tc, 'index', None)
                                    fn = getattr(tc, 'function', None)
                                    name = getattr(fn, 'name', None) if fn else None
                                    args_chunk = getattr(fn, 'arguments', None) if fn else None
                                    # Initialize record
                                    rec = tool_calls_map.get(idx) or {'id': getattr(tc, 'id', None), 'name': None, 'arguments': ''}
                                    if name:
                                        rec['name'] = name
                                    if args_chunk:
                                        try:
                                            rec['arguments'] = (rec.get('arguments') or '') + str(args_chunk)
                                        except Exception:
                                            pass
                                    tool_calls_map[idx] = rec
                            except Exception:
                                pass

                # Handle final usage stats in last chunk
                if chunk.usage:
                    self.turn_usage = chunk.usage
                    self.running_usage['total_in'] += chunk.usage.prompt_tokens
                    self.running_usage['total_out'] += chunk.usage.completion_tokens

                    # Handle cached tokens
                    if hasattr(chunk.usage, 'prompt_tokens_details'):
                        prompt_details = chunk.usage.prompt_tokens_details
                        # Support dict or object attributes
                        cached = None
                        if isinstance(prompt_details, dict):
                            cached = prompt_details.get('cached_tokens')
                        elif hasattr(prompt_details, 'cached_tokens'):
                            cached = prompt_details.cached_tokens
                        if cached is not None:
                            if 'cached_tokens' not in self.running_usage:
                                self.running_usage['cached_tokens'] = 0
                            self.running_usage['cached_tokens'] += cached

                    # Handle reasoning-specific metrics from completion_tokens_details
                    if hasattr(chunk.usage, 'completion_tokens_details'):
                        details = getattr(chunk.usage, 'completion_tokens_details')
                        # Accept dict or object with attributes
                        if isinstance(details, dict):
                            rt = details.get('reasoning_tokens', 0)
                            ap = details.get('accepted_prediction_tokens', 0)
                            rp = details.get('rejected_prediction_tokens', 0)
                        else:
                            rt = getattr(details, 'reasoning_tokens', 0)
                            ap = getattr(details, 'accepted_prediction_tokens', 0)
                            rp = getattr(details, 'rejected_prediction_tokens', 0)

                        if rt:
                            self.running_usage['reasoning_tokens'] = self.running_usage.get('reasoning_tokens', 0) + rt
                        if ap:
                            self.running_usage['accepted_prediction_tokens'] = self.running_usage.get('accepted_prediction_tokens', 0) + ap
                        if rp:
                            self.running_usage['rejected_prediction_tokens'] = self.running_usage.get('rejected_prediction_tokens', 0) + rp

        except Exception as e:
            error_msg = "Stream interrupted:\n"
            if hasattr(e, 'status_code'):
                error_msg += f"Status code: {e.status_code}\n"
            if hasattr(e, 'response'):
                error_msg += f"Response: {e.response}\n"
            error_msg += f"Error details: {str(e)}"
            yield error_msg

        finally:
            self.running_usage['total_time'] += time() - start_time
            # Finalize tool calls collected during streaming
            try:
                out = []
                import json
                for _, rec in sorted(tool_calls_map.items(), key=lambda kv: (kv[0] if kv[0] is not None else 0)):
                    args_obj = {}
                    args_str = rec.get('arguments') or ''
                    parsed = False
                    if args_str:
                        try:
                            args_obj = json.loads(args_str)
                            # Valid JSON but not an object (list/scalar/null) is
                            # invalid for tool arguments: reject it too.
                            parsed = isinstance(args_obj, dict)
                            if not parsed:
                                args_obj = {}
                        except Exception:
                            # Invalid arguments (malformed or cut off): do not
                            # silently convert to {} and execute with no arguments.
                            args_obj = {}
                    # An arguments string that fails to parse to an object (or a call
                    # that produced no arguments string at all after deltas were seen)
                    # is invalid. A parsed empty object ('{}') is legitimate.
                    truncated_call = (bool(args_str) and not parsed) or (not args_str and rec.get('name'))
                    out.append({
                        'id': rec.get('id'),
                        'name': rec.get('name'),
                        'arguments': args_obj,
                        # Arguments did not parse: likely cut off mid-write
                        'truncated': truncated_call,
                    })
                self._last_stream_tool_calls = out
            except Exception:
                self._last_stream_tool_calls = None

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
                    import json
                    tool_calls_out = []
                    for tc in (turn.get('tool_calls') or []):
                        fn_name = tc.get('name')
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
                    msg_out = {'role': 'assistant', 'content': None, 'tool_calls': tool_calls_out}
                    # Retransmit stored reasoning so the model keeps its chain of thought
                    if turn.get('reasoning_content'):
                        msg_out['reasoning_content'] = turn.get('reasoning_content')
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
                    if turn.get('role') == 'assistant' and turn.get('reasoning_content'):
                        msg_out['reasoning_content'] = turn.get('reasoning_content')
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
                    if turn.get('role') == 'assistant' and turn.get('reasoning_content'):
                        msg_out['reasoning_content'] = turn.get('reasoning_content')
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
        if self._last_stream_tool_calls:
            return list(self._last_stream_tool_calls)
        resp = self._last_response
        out = []
        try:
            if not resp or not getattr(resp, 'choices', None):
                return out
            choice0 = resp.choices[0]
            msg = getattr(choice0, 'message', None)
            tool_calls = getattr(msg, 'tool_calls', None) if msg else None
            if not tool_calls:
                return out
            import json
            for tc in tool_calls:
                fn = getattr(tc, 'function', None)
                name = getattr(fn, 'name', None) if fn else None
                args = getattr(fn, 'arguments', None) if fn else None
                # Validate: arguments must parse to a JSON object. Anything else
                # (unparseable string, list, scalar, null) is a malformed or
                # truncated call and must be rejected, not executed with {}.
                truncated_call = False
                if isinstance(args, str) and args:
                    try:
                        args_obj = json.loads(args)
                        truncated_call = not isinstance(args_obj, dict)
                        if truncated_call:
                            args_obj = {}
                    except Exception:
                        args_obj = {}
                        truncated_call = True
                elif isinstance(args, dict):
                    args_obj = args
                else:
                    # Missing/None arguments: reject, matching the streaming path.
                    # Legitimate empty arguments arrive as '{}' or an explicit {}.
                    args_obj = {}
                    truncated_call = True
                # Map API-safe tool names back to canonical names when available
                try:
                    mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
                    if isinstance(mapping, dict) and isinstance(name, str) and name in mapping:
                        name = mapping.get(name, name)
                except Exception:
                    pass
                out.append({
                    'id': getattr(tc, 'id', None),
                    'name': name,
                    'arguments': args_obj,
                    'truncated': truncated_call,
                })
        except Exception:
            return []
        return out

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
        """Update usage tracking with both standard and reasoning-specific metrics"""
        if response.usage:
            self.turn_usage = response.usage
            self.running_usage['total_in'] += response.usage.prompt_tokens
            self.running_usage['total_out'] += response.usage.completion_tokens

            # Handle cached tokens from prompt_tokens_details (support dict or object)
            if hasattr(response.usage, 'prompt_tokens_details'):
                prompt_details = getattr(response.usage, 'prompt_tokens_details')
                cached = None
                if isinstance(prompt_details, dict):
                    cached = prompt_details.get('cached_tokens')
                elif hasattr(prompt_details, 'cached_tokens'):
                    cached = prompt_details.cached_tokens
                if cached is not None:
                    if 'cached_tokens' not in self.running_usage:
                        self.running_usage['cached_tokens'] = 0
                    self.running_usage['cached_tokens'] += cached

            # Handle reasoning-specific metrics (support dict or object)
            if hasattr(response.usage, 'completion_tokens_details'):
                details = getattr(response.usage, 'completion_tokens_details')

                if isinstance(details, dict):
                    rt = details.get('reasoning_tokens', 0)
                    ap = details.get('accepted_prediction_tokens', 0)
                    rp = details.get('rejected_prediction_tokens', 0)
                else:
                    rt = getattr(details, 'reasoning_tokens', 0)
                    ap = getattr(details, 'accepted_prediction_tokens', 0)
                    rp = getattr(details, 'rejected_prediction_tokens', 0)

                # Initialize if missing, then accumulate
                if 'reasoning_tokens' not in self.running_usage:
                    self.running_usage['reasoning_tokens'] = 0
                if 'accepted_prediction_tokens' not in self.running_usage:
                    self.running_usage['accepted_prediction_tokens'] = 0
                if 'rejected_prediction_tokens' not in self.running_usage:
                    self.running_usage['rejected_prediction_tokens'] = 0

                self.running_usage['reasoning_tokens'] += rt
                self.running_usage['accepted_prediction_tokens'] += ap
                self.running_usage['rejected_prediction_tokens'] += rp

    def get_usage(self):
        """Get usage statistics including both standard and reasoning metrics"""
        stats = {
            'total_in': self.running_usage['total_in'],
            'total_out': self.running_usage['total_out'],
            'total_tokens': self.running_usage['total_in'] + self.running_usage['total_out'],
            'total_time': self.running_usage['total_time']
        }

        # Include cached tokens if available
        if 'cached_tokens' in self.running_usage:
            stats['total_cached'] = self.running_usage['cached_tokens']

        # Include reasoning metrics if they exist in running_usage
        reasoning_metrics = [
            ('total_reasoning', 'reasoning_tokens'),
            ('total_accepted_predictions', 'accepted_prediction_tokens'),
            ('total_rejected_predictions', 'rejected_prediction_tokens')
        ]

        for stat_name, metric_name in reasoning_metrics:
            if metric_name in self.running_usage:
                stats[stat_name] = self.running_usage[metric_name]

        if self.turn_usage:
            stats.update({
                'turn_in': self.turn_usage.prompt_tokens,
                'turn_out': self.turn_usage.completion_tokens,
                'turn_total': self.turn_usage.total_tokens
            })

            # Handle per-turn cached tokens (support dict or object)
            if hasattr(self.turn_usage, 'prompt_tokens_details'):
                prompt_details = getattr(self.turn_usage, 'prompt_tokens_details')
                cached = None
                if isinstance(prompt_details, dict):
                    cached = prompt_details.get('cached_tokens')
                elif hasattr(prompt_details, 'cached_tokens'):
                    cached = prompt_details.cached_tokens
                if cached is not None:
                    stats['turn_cached'] = cached

            # Include per-turn reasoning metrics if available (support dict or object)
            if hasattr(self.turn_usage, 'completion_tokens_details'):
                details = getattr(self.turn_usage, 'completion_tokens_details')

                if isinstance(details, dict):
                    turn_metrics = [
                        ('turn_reasoning', 'reasoning_tokens'),
                        ('turn_accepted_predictions', 'accepted_prediction_tokens'),
                        ('turn_rejected_predictions', 'rejected_prediction_tokens')
                    ]
                    for stat_name, metric_name in turn_metrics:
                        if metric_name in details:
                            stats[stat_name] = details[metric_name]
                else:
                    rt = getattr(details, 'reasoning_tokens', None)
                    ap = getattr(details, 'accepted_prediction_tokens', None)
                    rp = getattr(details, 'rejected_prediction_tokens', None)
                    if rt is not None:
                        stats['turn_reasoning'] = rt
                    if ap is not None:
                        stats['turn_accepted_predictions'] = ap
                    if rp is not None:
                        stats['turn_rejected_predictions'] = rp

        return stats

    def reset_usage(self):
        """Reset all usage metrics including reasoning-specific ones"""
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0,
            'cached_tokens': 0,
            'reasoning_tokens': 0,
            'accepted_prediction_tokens': 0,
            'rejected_prediction_tokens': 0
        }

    def get_cost(self) -> dict:
        """Calculate cost for OpenAI API usage"""
        usage = self.get_usage()
        if not usage:
            return None

        try:
            price_unit = float(self.session.get_params().get('price_unit', 1000000))
            price_in = float(self.session.get_params().get('price_in', 0))
            price_out = float(self.session.get_params().get('price_out', 0))

            input_cost = (usage['total_in'] / price_unit) * price_in
            # Optionally bill reasoning tokens as output tokens (default True)
            bill_reasoning = bool(self.session.get_params().get('bill_reasoning_as_output', True))
            total_reasoning = usage.get('total_reasoning', 0)
            billable_out = usage['total_out'] + (total_reasoning if bill_reasoning else 0)
            output_cost = (billable_out / price_unit) * price_out

            return {
                'input_cost': round(input_cost, 6),
                'output_cost': round(output_cost, 6),
                'total_cost': round(input_cost + output_cost, 6)
            }
        except (ValueError, TypeError):
            return None
