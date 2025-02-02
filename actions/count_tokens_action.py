import tiktoken
from session_handler import InteractionAction
import json


class CountTokensAction(InteractionAction):
    """
    Class for counting tokens in a message
    """

    def __init__(self, session):
        self.session = session

    def run(self, message=None):
        """
        Count the tokens in the chat context
        """
        params = self.session.get_params()
        if 'tokenizer' in params and params['tokenizer'] is not None:
            if params['tokenizer'] == 'tiktoken':
                messages = self.session.get_provider().get_messages()
                num_tokens = self.count_tiktoken(messages)
                print(f"Number of tokens: {num_tokens}")
        else:
            print("No tokenizer specified.")

    @staticmethod
    def count_tiktoken(messages, model="gpt-4"):
        """Returns the number of tokens used"""
        if messages is not None:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                print("Warning: model not found. Using cl100k_base encoding.")
                encoding = tiktoken.get_encoding("cl100k_base")

            tokens_per_message = 3
            tokens_per_name = 1

            num_tokens = 0

            if isinstance(messages, str):
                # If messages is a string, simply encode and count
                # num_tokens = len(encoding.encode(messages, disallowed_special=()))
                num_tokens = len(encoding.encode(messages, allowed_special="all"))
            elif isinstance(messages, dict):
                # If messages is a dictionary, convert it to a string and count
                messages_str = json.dumps(messages)
                # num_tokens = len(encoding.encode(messages_str, disallowed_special=()))
                num_tokens = len(encoding.encode(messages, allowed_special="all"))
            elif isinstance(messages, list):
                # If messages is a list, process each message
                for message in messages:
                    num_tokens += tokens_per_message
                    for key, value in message.items():
                        if isinstance(value, dict):
                            # If the value is a dictionary, convert it to a string
                            value = json.dumps(value)
                        # num_tokens += len(encoding.encode(str(value), disallowed_special=()))
                        num_tokens = len(encoding.encode(messages, allowed_special="all"))
                        if key == "role":
                            num_tokens += tokens_per_name
                num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
            else:
                raise ValueError("Input must be a string, a dictionary, or a list of message dictionaries")
        else:
            num_tokens = 0

        return num_tokens
