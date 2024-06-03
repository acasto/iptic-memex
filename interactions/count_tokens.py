import tiktoken
from abc import ABC, abstractmethod


class TokenCounter(ABC):
    @abstractmethod
    def count_tokens(self, messages, model):
        pass


class TikTokenCounter(TokenCounter):
    # A method to count the number of tokens in a message
    # using the tiktoken library
    # See: https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb
    def count_tokens(self, messages, model="gpt-4o"):
        """Returns the number of tokens used"""
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            print("Warning: model not found. Using cl100k_base encoding.")
            encoding = tiktoken.get_encoding("cl100k_base")

        tokens_per_message = 3
        tokens_per_name = 1

        num_tokens = 0
        # if we're given a str for a completion prompt, convert it to a list
        if not isinstance(messages, list):
            messages = [messages]
        # process list of messages
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "role":
                    num_tokens += tokens_per_name
        num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
        return num_tokens
