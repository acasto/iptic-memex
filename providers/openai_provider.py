import os
from time import time
import openai
from openai import OpenAI
from session_handler import APIProvider, SessionHandler


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
            # Assemble the message from the context
            messages = self.assemble_message()

            # Loop through the parameters and add them to the list if they are available
            api_parms = {}
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # If streaming set stream_options - we could set this in the config, but since it's dependent
            # on stream and enables internal feature, we'll set it here
            if 'stream' in api_parms and api_parms['stream'] is True:
                if 'stream_options' not in self.params or self.params['stream_options'] is not False:
                    api_parms['stream_options'] = {
                        'include_usage': True,
                    }

            # Add the messages to the API parameters
            api_parms['messages'] = messages

            # Save the params for debugging
            self.last_api_param = api_parms

            # Call the OpenAI API
            response = self.client.chat.completions.create(**api_parms)

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response.usage is not None:
                    self.turn_usage = response.usage
                if self.turn_usage:
                    self.running_usage['total_in'] += self.turn_usage.prompt_tokens
                    self.running_usage['total_out'] += self.turn_usage.completion_tokens
                return response.choices[0].message.content

        except (openai.APIConnectionError, openai.RateLimitError, openai.APIStatusError, Exception) as e:
            print("An exception occurred:")
            if isinstance(e, openai.APIConnectionError):
                print("The server could not be reached")
                print(e.__cause__)  # an underlying Exception, likely raised within httpx.
            elif isinstance(e, openai.RateLimitError):
                print("A 429 status code was received; we should back off a bit.")
            elif isinstance(e, openai.APIStatusError):
                print("Another non-200-range status code was received")
                print("Status code: " + str(e.status_code))
                print("Response: " + str(e.response))
            else:
                print(f"An unexpected error occurred: {e}")
            # the last_api_param is set then print them nicely
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
        finally:
            # calculate the total time for the API call
            self.running_usage['total_time'] += time() - start_time

    def stream_chat(self):
        """
        Use generator chaining to keep the response provider-agnostic
        :return:
        """
        response = self.chat()
        start_time = time()  # Start the timer for the streaming
        if response is None:
            return

        for i, chunk in enumerate(response):
            if chunk.choices and len(chunk.choices) > 0:
                if chunk.choices[0].finish_reason != 'stop' and chunk.choices[0].delta.content is not None:
                    content = chunk.choices[0].delta.content
                    if i < 2:  # Only process the first two chunks
                        content = content.lstrip('\n')  # Remove leading newlines only
                    yield content
            if chunk.usage is not None:
                self.turn_usage = chunk.usage

        # Update running totals once after streaming is complete
        self.running_usage['total_time'] += time() - start_time
        if self.turn_usage:
            self.running_usage['total_in'] += self.turn_usage.prompt_tokens
            self.running_usage['total_out'] += self.turn_usage.completion_tokens

    def assemble_message(self) -> list:
        """
        Assemble the message from the context
        :return: message (str)
        """
        message = []
        if self.session.get_context('prompt'):
            message.append({'role': 'system', 'content': self.session.get_context('prompt').get()['content']})

        chat = self.session.get_context('chat')
        if chat is not None:
            for idx, turn in enumerate(chat.get()):  # go through each turn in the conversation
                turn_context = ''
                # if context is in turn and not an empty list
                if 'context' in turn and turn['context']:
                    # get the processed context
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])

                message.append({'role': turn['role'], 'content': turn_context + "\n" + turn['message']})
        return message

    def get_messages(self):
        return self.assemble_message()

    def get_usage(self):
        stats = {
            'total_in': self.running_usage['total_in'],
            'total_out': self.running_usage['total_out'],
            'total_tokens': self.running_usage['total_in'] + self.running_usage['total_out'],
            'total_time': self.running_usage['total_time']
        }

        if self.turn_usage:  # Add current turn stats if available
            stats.update({
                'turn_in': self.turn_usage.prompt_tokens,
                'turn_out': self.turn_usage.completion_tokens,
                'turn_total': self.turn_usage.total_tokens
            })

        return stats

    def reset_usage(self):
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }
