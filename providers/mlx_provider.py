import time
import os
import traceback
from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction

try:
    # Suppress Hugging Face tokenizers fork warning by explicitly setting parallelism behavior
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from mlx_lm import load, generate, stream_generate
    from mlx_lm.sample_utils import make_sampler
    from mlx_lm.tokenizer_utils import TokenizerWrapper
    import mlx.core as mx

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False


class MlxProvider(APIProvider):
    """
    MLX-LM Python library provider for Apple Silicon with true streaming support
    """

    def __init__(self, session):
        self.session = session
        self.last_api_param = None
        self._last_response = None
        self._model_cache = {}  # Cache for loaded models

        if not MLX_AVAILABLE:
            raise ImportError("mlx-lm is not installed. Install it with: pip install mlx-lm")

        # Get fresh params for initialization
        params = self.session.get_params()

        # Extract model path - use model_path as primary config setting
        self.model_name = params.get('model_path')
        if not self.model_name:
            raise ValueError("model_path must be specified in config")

        # Load model and tokenizer with caching
        # print(f"Loading MLX model: {self.model_name}")
        try:
            if self.model_name not in self._model_cache:
                # Build tokenizer config only if we have actual config to pass
                tokenizer_config = None
                if params.get('trust_remote_code', False):
                    tokenizer_config = {'trust_remote_code': True}

                # Only pass tokenizer_config if we have actual config
                if tokenizer_config:
                    self.model, self.tokenizer = load(
                        self.model_name,
                        tokenizer_config=tokenizer_config
                    )
                else:
                    self.model, self.tokenizer = load(self.model_name)

                self._model_cache[self.model_name] = (self.model, self.tokenizer)
                # print("MLX model loaded successfully")
            else:
                self.model, self.tokenizer = self._model_cache[self.model_name]
                # print("MLX model loaded from cache")
        except Exception as e:
            print(f"Error loading MLX model: {e}")
            raise

        # Wrap tokenizer if needed
        if not isinstance(self.tokenizer, TokenizerWrapper):
            self.tokenizer = TokenizerWrapper(self.tokenizer)

        # Parameters we support - these match MLX-LM's sampler and generation parameters
        self.parameters = [
            'max_tokens',
            'temperature',
            'top_p',
            'min_p',
            'top_k',
            'seed',
            'verbose',
            'min_tokens_to_keep',
            'xtc_probability',
            'xtc_threshold',
            'max_kv_size',
            'kv_bits',
            'kv_group_size',
            'quantized_kv_start',
            'num_draft_tokens'
        ]

        # Track usage
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def chat(self):
        """
        Creates a chat completion using MLX-LM with real streaming support
        """
        start_time = time.time()
        try:
            # Get fresh params each time
            current_params = self.session.get_params()

            # Assemble messages and convert to prompt
            messages = self.assemble_message()
            prompt = self._messages_to_prompt(messages)

            # Build generation parameters
            gen_params = {
                'model': self.model,
                'tokenizer': self.tokenizer,
                'prompt': prompt,
            }

            # Map supported parameters
            for param in self.parameters:
                if param in current_params and current_params[param] is not None:
                    gen_params[param] = current_params[param]

            # Create sampler with proper parameters
            sampler_params = {}
            for param in ['temperature', 'top_p', 'min_p', 'top_k', 'min_tokens_to_keep',
                          'xtc_probability', 'xtc_threshold']:
                if param in current_params and current_params[param] is not None:
                    sampler_params[param] = current_params[param]

            if sampler_params:
                # Add special tokens for XTC sampling
                xtc_special_tokens = (
                        self.tokenizer.encode("\n") + list(self.tokenizer.eos_token_ids)
                )
                sampler_params['xtc_special_tokens'] = xtc_special_tokens
                gen_params['sampler'] = make_sampler(**sampler_params)

            # Store for debugging
            self.last_api_param = gen_params.copy()
            self.last_api_param['model'] = f"<MLX model {self.model_name}>"
            self.last_api_param['tokenizer'] = f"<tokenizer for {self.model_name}>"
            self.last_api_param['prompt'] = f"<prompt with {len(prompt)} characters>"

            # Check if streaming was requested
            if current_params.get('stream', False):
                # Return the stream generator for streaming
                return self._create_stream_generator(gen_params)
            else:
                # Use the regular generate function for non-streaming
                response_text = generate(**gen_params)
                self._last_response = response_text

                # Calculate usage stats by tokenizing
                prompt_tokens = len(self.tokenizer.encode(prompt))
                completion_tokens = len(self.tokenizer.encode(response_text))

                self.turn_usage = {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                    'total_tokens': prompt_tokens + completion_tokens
                }

                self.running_usage['total_in'] += prompt_tokens
                self.running_usage['total_out'] += completion_tokens

                return response_text

        except Exception as e:
            print(f"An exception occurred in MLX provider: {e}")
            traceback.print_exc()
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
            return f"Error: {str(e)}"
        finally:
            self.running_usage['total_time'] += time.time() - start_time

    def stream_chat(self):
        """
        Stream chat responses using MLX-LM's native streaming
        """
        response = self.chat()

        if hasattr(response, '__iter__') and not isinstance(response, str):
            # It's our stream generator
            yield from response
        else:
            # It's a plain string response
            yield response

    def _create_stream_generator(self, gen_params):
        """
        Create a real streaming generator using MLX-LM's stream_generate
        """
        start_time = time.time()

        try:
            # Remove model and tokenizer from params for stream_generate
            stream_params = gen_params.copy()
            model = stream_params.pop('model')
            tokenizer = stream_params.pop('tokenizer')
            prompt = stream_params.pop('prompt')

            total_response = ""
            prompt_tokens = 0
            completion_tokens = 0

            for response in stream_generate(model, tokenizer, prompt, **stream_params):
                # Extract text and accumulate
                text_chunk = response.text
                total_response += text_chunk

                # Update token counts from the response object
                if hasattr(response, 'prompt_tokens'):
                    prompt_tokens = response.prompt_tokens
                if hasattr(response, 'generation_tokens'):
                    completion_tokens = response.generation_tokens

                yield text_chunk

            # Update usage after streaming completes
            self.turn_usage = {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': prompt_tokens + completion_tokens
            }

            self.running_usage['total_in'] += prompt_tokens
            self.running_usage['total_out'] += completion_tokens

        except Exception as e:
            yield f"Streaming error: {str(e)}"
        finally:
            self.running_usage['total_time'] += time.time() - start_time

    def assemble_message(self) -> list:
        """
        Assemble messages from session context
        """
        messages = []

        # Add system/developer prompt
        if self.session.get_context('prompt'):
            params = self.session.get_params()
            # MLX-LM works with standard chat roles
            role = 'system' if params.get('use_old_system_role', True) else 'developer'
            prompt_content = self.session.get_context('prompt').get()['content']
            if prompt_content.strip() == '':
                prompt_content = ' '  # Handle empty content
            messages.append({'role': role, 'content': prompt_content})

        # Add chat history
        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():
                turn_context = ''
                if 'context' in turn and turn['context']:
                    # Process contexts (text only - MLX doesn't handle images directly)
                    text_contexts = [ctx for ctx in turn['context'] if ctx['type'] != 'image']
                    if text_contexts:
                        turn_context = ProcessContextsAction.process_contexts_for_assistant(text_contexts)
                        turn_context += "\n\n" if turn_context else ""

                content = turn_context + turn['message']
                if content.strip() == '':
                    content = ' '
                messages.append({'role': turn['role'], 'content': content})

        return messages

    def _messages_to_prompt(self, messages):
        """
        Convert messages to a prompt string using MLX-LM's chat template support
        """
        current_params = self.session.get_params()

        # Check if we should ignore chat template
        if current_params.get('ignore_chat_template', False):
            # Simple concatenation
            prompt = ""
            for message in messages:
                role = message['role']
                content = message['content']
                prompt += f"{role}: {content}\n"
            return prompt.strip()

        try:
            # Use the tokenizer's chat template if available
            if hasattr(self.tokenizer, 'chat_template') and self.tokenizer.chat_template is not None:
                return self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )
        except Exception as e:
            print(f"Warning: Could not use chat template, falling back to simple format: {e}")

        # Fallback to simple format
        prompt = ""
        for message in messages:
            role = message['role']
            content = message['content']
            prompt += f"<|{role}|>\n{content}\n\n"

        prompt += "<|assistant|>\n"
        return prompt

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response from the last API call"""
        return self._last_response

    def get_usage(self):
        """Get usage statistics"""
        stats = {
            'total_in': self.running_usage['total_in'],
            'total_out': self.running_usage['total_out'],
            'total_tokens': self.running_usage['total_in'] + self.running_usage['total_out'],
            'total_time': self.running_usage['total_time']
        }

        if self.turn_usage:
            stats.update({
                'turn_in': self.turn_usage['prompt_tokens'],
                'turn_out': self.turn_usage['completion_tokens'],
                'turn_total': self.turn_usage['total_tokens']
            })

        return stats

    def reset_usage(self):
        """Reset usage statistics"""
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def get_cost(self):
        """MLX models are free to run locally"""
        return {
            'input_cost': 0.0,
            'output_cost': 0.0,
            'total_cost': 0.0
        }
