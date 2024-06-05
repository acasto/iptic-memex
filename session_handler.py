import importlib
from abc import ABC, abstractmethod
from typing import Any, Generator
from config_handler import ConfigHandler


class SessionHandler:
    """
    Class for handling the current session
    """

    def __init__(self):
        self.conf = ConfigHandler()
        self.options = {}
        self.session = {}

    def add_context(self, context_type: str, context_data: Any):
        """
        Add a context object to the session
        :param context_type: the type of context to add
        :param context_data: the data for the context
        """
        # Construct the module and class names based on the context type
        module_name = f'interactions.{context_type}_context'
        class_name = f'{context_type.capitalize()}Context'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            context_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported context type: {context_type}")

        # Instantiate the class and add the context to the session
        context = context_class(context_data, self.conf)
        # Add the context to the session under self.session[context_type]
        if context_type not in self.session:
            self.session[context_type] = []
        self.session[context_type].append(context)

    def set_option(self, key, value):
        """
        Set a user option which can be processed later into the session configuration
        :param key: the key to set
        :param value: the value to set
        """
        self.options[key] = value


    def dump_session(self):
        """
        Dump the session data
        """
        return self.session

    def start_interaction(self, interaction: str):
        """
        Start an interaction with the user
        :param interaction: the interaction to start
        """
        if interaction == 'completion':
            print("we will do a completion")
        elif interaction == 'chat':
            print("we will do a chat")
        elif interaction == 'ask':
            print("ask your questions fool")


############################################################################################################
# Abstract base classes for providers and interactions
############################################################################################################

# The APIProvider class is an abstract class that defines the methods that
# an API Provider must implement to work with the APIHandler.
class APIProvider(ABC):
    """
    Abstract class for API handlers
    """

    @abstractmethod
    def chat(self, message: Any) -> Any:
        pass

    @abstractmethod
    def stream_chat(self, message: Any) -> Generator[Any, None, None]:
        pass


# The InteractionHandler class is an abstract class that defines the methods that
# an Interaction Handler must implement to work with the SessionHandler.
class InteractionHandler(ABC):
    """
    Abstract class for interaction handlers
    """

    def start(self, data: Any):
        pass
