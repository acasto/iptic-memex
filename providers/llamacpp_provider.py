import traceback
from time import time
from session_handler import APIProvider, SessionHandler
from llama_cpp import Llama
from llama_cpp.llama_speculative import LlamaPromptLookupDecoding
import io
from contextlib import redirect_stderr
from providers.llamacpp_draft_model import LlamaSmallModelDraft
from providers.llamacpp_draft_model import LlamaSmallModelDraftWithMetrics


class LlamaCppProvider(APIProvider):
    """
    llama.cpp Python bindings provider
    """

    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = self.session.get_params()
        self.last_api_param = None

        # Extract or set defaults for llama-cpp
        model_path = self.params.get('model_path', './models/7B/llama-model.gguf')
        # Example: you can allow specifying n_ctx or other llama-specific params:
        n_ctx = self.params.get('context_size', 2048)
        embedding = self.params.get('embedding', False)
        n_gpu_layers = int(self.params.get('n_gpu_layers', -1))
        verbose = self.params.get('verbose', False)

        # If you want to enable speculative decoding when requested:
        self.draft_model = None
        logits_all = False
        if self.params.get('speculative', False) == "draft":
            print("Using draft model for speculative decoding")
            self.draft_model = LlamaSmallModelDraftWithMetrics(
                # model_path="/Users/adam/LLM/models/draft/Llama-3.2-3B-Instruct-Q5_K_M.gguf",
                model_path="/Users/adam/LLM/models/draft/Llama-3.2-1B-Instruct-Q5_K_M.gguf",
                num_draft_tokens=self.params.get('draft', 10),
                temperature=0.2
            )
            logits_all = True
        if self.params.get('speculative', False) == "prompt":
            print("Using prompt lookup decoding")
            self.draft_model = LlamaPromptLookupDecoding(
                max_ngram_size=5,
                num_pred_tokens=self.params.get('draft', 10),
            )
            logits_all = True

        # Initialize the Llama model
        f = io.StringIO()
        with redirect_stderr(f):
            self.llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                embedding=embedding,
                n_gpu_layers=n_gpu_layers,
                draft_model=self.draft_model,
                logits_all=logits_all,
                use_mlock=False,
                flash_attn=True,
                # type_k=6,
                # type_v=6,  # not supported yet in upstream
                verbose=verbose
            )

        # Parameters we might want to map from self.params to llama_cpp.create_chat_completion()
        # Unlike OpenAI, llama-cpp-python tries to support a similar interface, so most should just pass through.
        self.parameters = [
            'model',          # Typically not needed since we set model_path at init
            'messages',
            'max_tokens',
            'frequency_penalty',
            'logit_bias',
            'logprobs',
            'n',
            'presence_penalty',
            'response_format',
            'seed',
            'stop',
            'stream',
            'temperature',
            'top_p',
            'tools',          # If using function calling / tools
            'tool_choice',    # If using function calling / tools
            'user',
            'extra_body'
        ]

        # Track usage similar to OpenAI
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def chat(self):
        """
        Creates a chat completion using llama-cpp-python
        """
        start_time = time()
        try:
            # Assemble messages from session
            messages = self.assemble_message()
            api_parms = {}

            # Map parameters from self.params to the call
            for parameter in self.parameters:
                if parameter in self.params and self.params[parameter] is not None:
                    api_parms[parameter] = self.params[parameter]

            # If streaming set stream_options - we could set this in the config, but since it's dependent
            # on stream and enables internal feature, we'll set it here
            # if 'stream' in api_parms and api_parms['stream'] is True:
            #    if 'stream_options' not in self.params or self.params['stream_options'] is not False:
            #        api_parms['stream_options'] = {
            #            'include_usage': True,
            #        }

            # llama-cpp-python expects messages and supports similar arguments
            api_parms['messages'] = messages

            # Save for debugging
            self.last_api_param = api_parms

            # Call llama-cpp create_chat_completion
            # Note: create_chat_completion returns a dict like OpenAI API
            response = self.llm.create_chat_completion(**api_parms)

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response['usage'] is not None:
                    self.turn_usage = response['usage']
                if self.turn_usage:
                    self.running_usage['total_in'] += self.turn_usage['prompt_tokens']
                    self.running_usage['total_out'] += self.turn_usage['completion_tokens']
                return response['choices'][0]['message']['content']

        except Exception as e:
            print("An exception occurred in llama-cpp provider:")
            traceback.print_exc()
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
        finally:
            self.running_usage['total_time'] += time() - start_time

    def stream_chat(self):
        """
        Stream chat responses while manually computing usage statistics.
        Since streaming usage stats aren't currently returned by llama-cpp-python,
        we will:
        - Extract prompt text from api_params['messages']
        - Tokenize the prompt to count prompt tokens
        - As we stream completion tokens, accumulate them in a buffer
        - After streaming completes, tokenize the generated text to count completion tokens
        - Populate self.turn_usage with the computed stats
        """

        response = self.chat()
        if response is None:
            return

        # The prompt messages should be in self.last_api_param['messages']
        messages = self.last_api_param.get('messages', [])
        prompt_str = ""
        for msg in messages:
            prompt_str += msg['content']

        # Tokenize the prompt (must encode to bytes)
        prompt_tokens = self.llm.tokenize(prompt_str.encode('utf-8'), add_bos=True)
        prompt_token_count = len(prompt_tokens)

        completion_str = ""
        start_time = time()

        # Stream the response tokens
        for chunk in response:
            if 'choices' in chunk and len(chunk['choices']) > 0:
                choice = chunk['choices'][0]
                content = choice.get('delta', {}).get('content')
                if content:
                    completion_str += content
                    yield content

        # Tokenize the completed response (encode to bytes)
        completion_tokens = self.llm.tokenize(completion_str.encode('utf-8'), add_bos=False)
        completion_token_count = len(completion_tokens)

        total_token_count = prompt_token_count + completion_token_count
        if self.params.get('speculative', False) == "draft":
            self.draft_model.print_overall_metrics()
        # Create a usage dict compatible with what we'd normally expect
        self.turn_usage = {
            'prompt_tokens': prompt_token_count,
            'completion_tokens': completion_token_count,
            'total_tokens': total_token_count
        }

        # Update running usage totals
        self.running_usage['total_in'] += prompt_token_count
        self.running_usage['total_out'] += completion_token_count
        self.running_usage['total_time'] += time() - start_time
        # Print the tokens per second
        # print()
        # print(f"TPS: {total_token_count / self.running_usage['total_time']}")

    def assemble_message(self) -> list:
        """
        Assemble messages for llama-cpp API from the session context
        """
        message = []
        if self.session.get_context('prompt'):
            # Use 'system' or 'developer' based on provider configuration
            # role = 'system' if self.params.get('use_old_system_role', False) else 'developer'
            role = 'system'  # Remove when 'developer' is supported and uncomment above
            message.append({'role': role, 'content': self.session.get_context('prompt').get()['content']})

        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():
                turn_context = ''
                if 'context' in turn and turn['context']:
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

        if self.turn_usage:  # Current turn stats
            stats.update({
                'turn_in': self.turn_usage['prompt_tokens'],
                'turn_out': self.turn_usage['completion_tokens'],
                'turn_total': self.turn_usage['total_tokens']
            })

        return stats

    def reset_usage(self):
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def get_cost(self):
        return {
            'total': 0.0,
            'turn': 0.0
        }
