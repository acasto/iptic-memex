import importlib
from abc import ABC, abstractmethod
from typing import Any, Generator
from config_handler import ConfigHandler


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

    @abstractmethod
    def start(self):
        pass


############################################################################################################
# The main session handler
############################################################################################################

class SessionHandler:
    """
    Class for handling the current session
    """

    def __init__(self):
        self.conf = ConfigHandler()
        self.options = {}
        self.session = {}

    def set_option(self, key, value):
        """
        Set a user option which can be processed later into the session configuration
        :param key: the key to set
        :param value: the value to set
        """
        if 'options' not in self.session:
            self.session['options'] = {}
        self.session['options'][key] = value

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

    def get_session_settings(self):
        """
        Build the settings for the session by reconciling user supplied options and those from ConfigHandler
        """
        if 'parms' not in self.session:
            self.session['parms'] = {}

        # if model is not set, use the default model
        if 'model' not in self.session['parms']:
            self.session['model'] = self.conf.get_default_model()
        else:  # else move it up out of options since we want the full model name there
            self.session['model'] = self.session['parms']['model']
            del self.session['parms']['model']

        # make sure model is valid since it is central
        if not self.conf.valid_model(self.session['model']):
            raise ValueError(f"Invalid model: {self.session['model']}")

        # get the full model name
        self.session['parms']['model_name'] = self.conf.get_option_from_model('model_name', self.session['model'])

        # get the provider from the model (mostly for the dump-session command)
        self.session['provider'] = self.conf.get_option_from_model('provider', self.session['model'])

        # get the api_key from the provider
        self.session['api_key'] = self.conf.get_option_from_provider('api_key', self.session['provider'])

        # get the endpoint from the provider
        self.session['endpoint'] = self.conf.get_option_from_provider('endpoint', self.session['provider'])

        # get the max_tokens from the provider if not in session already
        if 'max_tokens' not in self.session['parms']:
            self.session['parms']['max_tokens'] = self.conf.get_option_from_provider('max_tokens', self.session['provider'])

        # get the temperature from the provider if not in session already
        if 'temperature' not in self.session['parms']:
            self.session['parms']['temperature'] = self.conf.get_option_from_provider('temperature', self.session['provider'])

        # get the stream setting from the config if not in session already
        if 'stream' not in self.session['parms']:
            self.session['parms']['stream'] = self.conf.get_option_from_provider('stream', self.session['provider'])

        return self.session

    def intialize_api_provider(self) -> APIProvider:
        """
        Initialize the API provider
        """
        session = self.get_session_settings()
        provider = session['provider']

        # Construct the module and class names based on the provider
        module_name = f'providers.{provider.lower()}_provider'
        class_name = f'{provider}Provider'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            provider_class = getattr(module, class_name)
            return provider_class(self)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported provider: {provider}")

    def start_mode(self, mode: str):
        """
        Start an interaction with the user
        :param mode: the interaction to start
        """

        # get a provider
        provider = self.intialize_api_provider()

        # Construct the module and class names based on the context type
        module_name = f'interactions.{mode}_mode'
        class_name = f'{mode.capitalize()}Mode'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            mode_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported interaction: {mode}")

        # Instantiate the class and start the interaction
        interaction = mode_class(self, provider)
        interaction.start()

    ############################################################################################################
    # Passthrough methods for CLI interaction
    ############################################################################################################

    def list_prompts(self):
        """
        List the available prompts from ConfigHandler
        """
        return self.conf.list_prompts()

    def list_models(self, showall=False):
        """
        List the available models from ConfigHandler
        """
        return self.conf.list_models(showall)

    def list_providers(self, showall=False):
        """
        List the available providers from ConfigHandler
        """
        return self.conf.list_providers(showall)
