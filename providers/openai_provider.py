import os
from time import time
import openai
from openai import OpenAI
from session_handler import APIProvider, SessionHandler
from actions.process_contexts_action import ProcessContextsAction


class OpenAIProvider(APIProvider):
    """
    OpenAI API handler
    """

    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        self.last_api_param = None
        self._last_response = None

        # set the options for the OpenAI API client
        options = {}
        if 'api_key' in self.params and self.params['api_key'] is not None:
            options['api_key'] = self.params['api_key']
        elif 'OPENAI_API_KEY' in os.environ:
            options['api_key'] = os.environ['OPENAI_API_KEY']
        else:
            options['api_key'] = 'none'  # in case we're using the library for something else but still need something set

        # Quick hack to provide a simple and clear message if someone clones the repo and forgets to set the API key
        # since OpenAI will probably be the most common provider. Will still error out on other providers that require
        # an API key though until we figure out a better way to handle  this (issue is above where we set it to none
        # so that it still works with local providers that don't require an API key)
        if self.params['provider'].lower() == 'openai' and options['api_key'] == 'none':
            print(f"\nOpenAI API Key is required\n")
            quit()

        if 'base_url' in self.params and self.params['base_url'] is not None:
            options['base_url'] = self.params['base_url']

        if 'timeout' in self.params and self.params['timeout'] is not None:
            options['timeout'] = self.params['timeout']

        # Initialize the OpenAI client
        self.client = OpenAI(**options)

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
            'tools',
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

    def chat(self):
        """
        Creates a chat completion request to the OpenAI API
        :return: response (str)
        """
        start_time = time()
        try:
            messages = self.assemble_message()
            api_parms = {}

            # Check if this is a reasoning model
            is_reasoning = self.params.get('reasoning', False)

            # Get excluded parameters if any
            excluded_params = []
            if is_reasoning:
                excluded_params = self.params.get('excluded_parameters', [])

            # Filter out excluded parameters from self.parameters
            valid_params = [p for p in self.parameters if p not in excluded_params]

            # Build parameter dictionary
            for parameter in valid_params:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # Handle reasoning model specific logic
            if is_reasoning:
                # Initialize or get extra_body
                extra_body = api_parms.get('extra_body', {})
                if isinstance(extra_body, str):
                    # If extra_body is a string, attempt to evaluate it as a dict
                    try:
                        extra_body = eval(extra_body)
                    except (SyntaxError, ValueError, NameError) as e:
                        print(f"Warning: Could not evaluate extra_body string: {e}")
                        extra_body = {}

                # Handle max_tokens vs max_completion_tokens
                max_completion_tokens = self.params.get('max_completion_tokens')
                max_tokens = self.params.get('max_tokens')

                if max_completion_tokens is not None:
                    extra_body['max_completion_tokens'] = max_completion_tokens
                    # Remove max_tokens if it exists in api_parms
                    api_parms.pop('max_tokens', None)
                elif max_tokens is not None:
                    extra_body['max_completion_tokens'] = max_tokens
                    # Remove max_tokens from api_parms since we're using it as max_completion_tokens
                    api_parms.pop('max_tokens', None)

                # Handle reasoning_effort
                reasoning_effort = self.params.get('reasoning_effort')
                if reasoning_effort is not None:
                    # Normalize to lowercase
                    extra_body['reasoning_effort'] = reasoning_effort.lower()

                # Update api_parms with modified extra_body
                if extra_body:
                    api_parms['extra_body'] = extra_body

            if 'stream' in api_parms and api_parms['stream'] is True:
                api_parms['stream_options'] = {
                    'include_usage': True,
                }

            api_parms['messages'] = messages
            self.last_api_param = api_parms

            # Make the API call and store the full response
            response = self.client.chat.completions.create(**api_parms)
            self._last_response = response

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
                error_msg += f"Response: {getattr(e, 'response', 'unknown')}\n"
            else:
                error_msg += f"Unexpected error: {str(e)}\n"

            if self.last_api_param is not None:
                error_msg += "\nDebug info:\n"
                for key, value in self.last_api_param.items():
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

        try:
            for chunk in response:
                # Handle content chunks
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    if choice.delta and choice.delta.content:
                        yield choice.delta.content

                # Handle final usage stats in last chunk
                if chunk.usage:
                    self.turn_usage = chunk.usage
                    self.running_usage['total_in'] += chunk.usage.prompt_tokens
                    self.running_usage['total_out'] += chunk.usage.completion_tokens

                    # Handle cached tokens
                    if hasattr(chunk.usage, 'prompt_tokens_details'):
                        cached = chunk.usage.prompt_tokens_details.get('cached_tokens', 0)
                        if 'cached_tokens' not in self.running_usage:
                            self.running_usage['cached_tokens'] = 0
                        self.running_usage['cached_tokens'] += cached

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

    def assemble_message(self) -> list:
        """
        Assemble the message from the context, including image handling
        :return: message (list)
        """
        message = []
        if self.session.get_context('prompt'):
            # Use 'system' or 'developer' based on provider configuration
            role = 'system' if self.params.get('use_old_system_role', False) else 'developer'
            message.append({'role': role, 'content': self.session.get_context('prompt').get()['content']})

        chat = self.session.get_context('chat')
        if chat is not None:
            for idx, turn in enumerate(chat.get()):
                content = []
                turn_contexts = []

                # Handle message text
                if turn['message']:
                    content.append({'type': 'text', 'text': turn['message']})

                # Process contexts
                if 'context' in turn and turn['context']:
                    for ctx in turn['context']:
                        if ctx['type'] == 'image':
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

                message.append({'role': turn['role'], 'content': content})

        return message

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def _update_usage_stats(self, response):
        """Update usage tracking with both standard and reasoning-specific metrics"""
        if response.usage:
            self.turn_usage = response.usage
            self.running_usage['total_in'] += response.usage.prompt_tokens
            self.running_usage['total_out'] += response.usage.completion_tokens

            # Handle cached tokens from prompt_tokens_details
            if hasattr(response.usage, 'prompt_tokens_details'):
                prompt_details = getattr(response.usage, 'prompt_tokens_details')
                if isinstance(prompt_details, dict):
                    cached = prompt_details.get('cached_tokens', 0)
                    if 'cached_tokens' not in self.running_usage:
                        self.running_usage['cached_tokens'] = 0
                    self.running_usage['cached_tokens'] += cached

            # Handle reasoning-specific metrics
            if hasattr(response.usage, 'completion_tokens_details'):
                details = getattr(response.usage, 'completion_tokens_details')
                if isinstance(details, dict):
                    # Initialize reasoning metrics in running usage if not present
                    metrics = [
                        'reasoning_tokens',
                        'accepted_prediction_tokens',
                        'rejected_prediction_tokens'
                    ]

                    # Initialize any missing metrics
                    for metric in metrics:
                        if metric not in self.running_usage:
                            self.running_usage[metric] = 0

                        # Update running totals safely
                        if metric in details:
                            self.running_usage[metric] += details[metric]

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

            # Handle per-turn cached tokens
            if hasattr(self.turn_usage, 'prompt_tokens_details'):
                prompt_details = getattr(self.turn_usage, 'prompt_tokens_details')
                if isinstance(prompt_details, dict):
                    stats['turn_cached'] = prompt_details.get('cached_tokens', 0)

            # Include per-turn reasoning metrics if available
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
