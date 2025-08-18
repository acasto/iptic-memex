import tiktoken
from base_classes import InteractionAction
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
                try:
                    self.session.ui.emit('status', {'message': f'Number of tokens: {num_tokens}'})
                except Exception:
                    pass
        else:
            try:
                self.session.ui.emit('warning', {'message': 'No tokenizer specified.'})
            except Exception:
                pass

    @staticmethod
    def count_tiktoken(messages, model="gpt-4"):
        """Returns the number of tokens used"""
        if messages is not None:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                try:
                    self.session.ui.emit('warning', {'message': 'Warning: model not found. Using cl100k_base encoding.'})
                except Exception:
                    pass
                encoding = tiktoken.get_encoding("cl100k_base")

            tokens_per_message = 3
            tokens_per_name = 1

            num_tokens = 0

            if isinstance(messages, str):
                # If messages is a string, simply encode and count
                num_tokens = len(encoding.encode(messages, disallowed_special=()))
            elif isinstance(messages, dict):
                try:
                    # If messages is a dictionary, convert it to a string and count
                    messages_str = json.dumps(messages)
                    num_tokens = len(encoding.encode(messages_str, disallowed_special=()))
                except TypeError:
                    # If JSON serialization fails, convert to string first
                    messages_str = str(messages)
                    num_tokens = len(encoding.encode(messages_str, disallowed_special=()))
            elif isinstance(messages, list):
                # If messages is a list, process each message
                for message in messages:
                    num_tokens += tokens_per_message
                    for key, value in message.items():
                        if isinstance(value, dict):
                            try:
                                # If the value is a dictionary, convert it to a string
                                value = json.dumps(value)
                            except TypeError:
                                # If JSON serialization fails, convert to string first
                                value = str(value)
                        num_tokens += len(encoding.encode(str(value), disallowed_special=()))
                        if key == "role":
                            num_tokens += tokens_per_name
                num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
            else:
                # For any other type, convert to string
                num_tokens = len(encoding.encode(str(messages), disallowed_special=()))
        else:
            num_tokens = 0

        return num_tokens
