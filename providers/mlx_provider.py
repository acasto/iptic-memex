import mlx.core as mx
import mlx.nn as nn
import mlx.utils
from transformers import AutoConfig, AutoTokenizer  # For easy model loading
import time
import io
from contextlib import redirect_stderr

class MlxProvider:
    """
    MLX Python bindings provider
    """

    def __init__(self, session):
        """
        Initializes the MLX provider.

        Args:
            session: A Session instance (assuming your existing structure)
        """
        self.session = session
        self.last_api_param = None
        self._last_response = None

        # Get fresh params for initialization
        params = self.session.get_params()

        # Extract/set defaults for MLX
        model_name = params.get('model_name', "mlx-community/MyAwesomeModel")  # Default model
        self.device = params.get('device', "gpu")  # or "cpu"

        # Load Model and Tokenizer
        f = io.StringIO()
        with redirect_stderr(f): # Suppress any loading messages
            try:
                self.config = AutoConfig.from_pretrained(model_name)
                self.tokenizer = AutoTokenizer.from_pretrained(model_name)

                # This part is highly dependent on the model architecture
                # You'll likely need a model-specific implementation here
                # Example:
                class MyMLP(nn.Module): # Replace with the actual model architecture
                    def __init__(self, config):
                        super().__init__()
                        self.linear1 = nn.Linear(config.hidden_size, config.intermediate_size)
                        self.linear2 = nn.Linear(config.intermediate_size, config.hidden_size)

                    def __call__(self, x):
                        x = self.linear1(x)
                        x = mx.relu(x)
                        x = self.linear2(x)
                        return x

                self.model = MyMLP(self.config)
                # Attempt to load weights (you might need to adjust this)
                try:
                    mx.load_weights(model_name, self.model)
                except Exception as e:
                    print(f"Warning: Could not load pre-trained weights.  Model initialized with random weights.\n{e}")

                # Move the model to the desired device
                if self.device == "gpu":
                    mx.set_default_device(mx.gpu)
                else:
                    mx.set_default_device(mx.cpu)
                self.model.to(mx.default_device())

            except Exception as e:
                print(f"Error initializing MLX model: {e}")
                self.model = None
                self.tokenizer = None
                raise  # Re-raise to signal failure to caller

        # Track usage (similar to your LlamaCppProvider)
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def chat(self):
        """
        Creates a chat completion using MLX.
        """
        start_time = time.time()
        try:
            if self.model is None or self.tokenizer is None:
                raise Exception("MLX model not initialized properly")

            # 1. Assemble Messages (same as your LlamaCppProvider)
            messages = self.assemble_message()
            prompt_text = self._messages_to_prompt(messages) # Convert messages to plain text

            # 2. Tokenize the Input
            input_ids = self.tokenizer.encode(prompt_text, return_tensors="np") # Needs to be numpy first
            mx_input = mx.array(input_ids)
            mx_input = mx_input[None] # Add a batch dimension

            # 3. Run Inference
            output = self.model(mx_input) # Assumes model takes token IDs as input
            # Output shape is (batch_size, sequence_length, vocab_size)


            # 4. Decode the output (This part requires careful adaptation)
            # It depends on the model and what you want to extract

            # Simple example (assuming you want to get the most likely next token):
            next_token_id = mx.argmax(output[0, -1, :]).item() # Get the most likely token
            predicted_text = self.tokenizer.decode([next_token_id]) # Decode the token ID

            self._last_response = predicted_text  # Save for debugging

            # 5. Update Usage Statistics
            prompt_tokens = len(input_ids[0])
            completion_tokens = 1 # Only predicting one token for this example
            self.turn_usage = {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': prompt_tokens + completion_tokens
            }
            self.running_usage['total_in'] += prompt_tokens
            self.running_usage['total_out'] += completion_tokens


            return predicted_text

        except Exception as e:
            print(f"An exception occurred in MLX provider:\n{e}")
            raise # Re-raise for handling at a higher level
        finally:
            self.running_usage['total_time'] += time.time() - start_time


    def stream_chat(self):
        """
        Streaming is more complex in MLX and may not be directly analogous
        to the llama.cpp implementation. This is left as an exercise
        or may require a different approach.

        For true streaming, you'd need to iteratively generate tokens
        and yield them.
        """
        raise NotImplementedError("Streaming not yet implemented for MLX provider.")


    def assemble_message(self) -> list:
        """
        Assemble messages for MLX from the session context.
        This should be identical to your LlamaCppProvider.
        """
        message = []
        if self.session.get_context('prompt'):
            # Use 'system' or 'developer' based on provider configuration
            # Get fresh params to check use_old_system_role
            params = self.session.get_params()
            role = 'system' if params.get('use_old_system_role', False) else 'developer'
            message.append({'role': role, 'content': self.session.get_context('prompt').get()['content']})

        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():
                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])
                message.append({'role': turn['role'], 'content': turn_context + "\n" + turn['message']})

        return message

    def _messages_to_prompt(self, messages):
        """
        Convert a list of messages to a single prompt string.  This
        might need adjusting based on the specific model expectations.
        """
        prompt = ""
        for message in messages:
            prompt += f"{message['role']}: {message['content']}\n"
        return prompt

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

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