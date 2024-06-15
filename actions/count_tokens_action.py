import tiktoken
from session_handler import InteractionAction


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
        if params['tokenizer'] == 'tiktoken':
            messages = self.session.get_provider().get_messages()
            num_tokens = self.count_tiktoken(messages)
            print(f"Number of tokens: {num_tokens}")

    @staticmethod
    def count_tiktoken(messages, model="gpt-4o"):
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
