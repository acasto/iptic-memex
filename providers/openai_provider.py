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
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            if 'stream' in api_parms and api_parms['stream'] is True:
                api_parms['stream_options'] = {
                    'include_usage': True,
                }

            api_parms['messages'] = messages
            self.last_api_param = api_parms

            response = self.client.chat.completions.create(**api_parms)

            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                self._update_usage_stats(response)
                return response.choices[0].message.content

        except Exception as e:
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
            message.append({'role': 'developer', 'content': self.session.get_context('prompt').get()['content']})

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

    def _update_usage_stats(self, response):
        """Update usage tracking with cached token information"""
        if response.usage:
            self.turn_usage = response.usage
            self.running_usage['total_in'] += response.usage.prompt_tokens
            self.running_usage['total_out'] += response.usage.completion_tokens

            # Handle cached tokens from prompt_tokens_details
            if hasattr(response.usage, 'prompt_tokens_details'):
                cached = response.usage.prompt_tokens_details.get('cached_tokens', 0)
                if 'cached_tokens' not in self.running_usage:
                    self.running_usage['cached_tokens'] = 0
                self.running_usage['cached_tokens'] += cached

    def get_usage(self):
        """Get usage statistics including cache metrics"""
        stats = {
            'total_in': self.running_usage['total_in'],
            'total_out': self.running_usage['total_out'],
            'total_tokens': self.running_usage['total_in'] + self.running_usage['total_out'],
            'total_time': self.running_usage['total_time']
        }

        if 'cached_tokens' in self.running_usage:
            stats['total_cached'] = self.running_usage['cached_tokens']

        if self.turn_usage:
            stats.update({
                'turn_in': self.turn_usage.prompt_tokens,
                'turn_out': self.turn_usage.completion_tokens,
                'turn_total': self.turn_usage.total_tokens
            })

            if hasattr(self.turn_usage, 'prompt_tokens_details'):
                stats['turn_cached'] = self.turn_usage.prompt_tokens_details.get('cached_tokens', 0)

        return stats

    def reset_usage(self):
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }
