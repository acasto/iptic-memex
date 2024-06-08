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
    def chat(self, context: dict) -> Any:
        pass

    @abstractmethod
    def stream_chat(self, context: Any) -> Generator[Any, None, None]:
        pass


# The InteractionMode class is an abstract class that defines the methods that interactions modes
# like chat, ask, and completion must implement to work.
class InteractionMode(ABC):
    """
    Abstract class for interaction handlers
    """

    @abstractmethod
    def start(self):
        pass


# The InteractionContext class is an abstract class that defines the methods that interaction contexts
# like prompt, file, and chat contexts must implement to work.
class InteractionContext(ABC):
    """
    Abstract class for interaction contexts
    """

    @abstractmethod
    def get(self):
        pass


# The InteractionAction class is an abstract class that defines the methods that interaction actions
# like count_tokens, list_chats, save_chat, etc. must implement to work.
class InteractionAction(ABC):
    """
    Abstract class for interaction actions
    """

    @abstractmethod
    def run(self):
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
        if 'parms' not in self.session:
            self.session['parms'] = {}
        self.session['parms'][key] = value

    def add_context(self, context_type: str, context_data=None):
        """
        Add a context object to the session
        :param context_type: the type of context to add
        :param context_data: the data for the context
        """
        # Construct the module and class names based on the context type
        module_name = f'contexts.{context_type}_context'
        class_name = f'{context_type.capitalize()}Context'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            context_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported context type: {context_type}")

        # Instantiate the class and add the context to the session
        context = context_class(self.conf, context_data)
        # Add the context to the session under self.session[context_type]
        if 'loadctx' not in self.session:
            self.session['loadctx'] = {}
        if context_type not in self.session['loadctx']:
            self.session['loadctx'][context_type] = []
        self.session['loadctx'][context_type].append(context)

    def get_action(self, action: str):
        """
        Instantiate and return an action class
        :param action: the action to instantiate
        """
        # construct the module and class names based on the action
        module_name = f'actions.{action}_action'
        class_name = f'{action.capitalize()}Action'

        # import the module and get the class
        try:
            module = importlib.import_module(module_name)
            action_class = getattr(module, class_name)
            return action_class(self)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported action: {action}")

    def get_session_settings(self):
        """
        Build the settings for the session by reconciling user supplied options and those from ConfigHandler
        """
        if 'parms' not in self.session:
            self.session['parms'] = {}
        if 'loadctx' not in self.session:
            self.session['loadctx'] = {}

        # get the default prompt if needed
        if 'prompt' not in self.session['loadctx']:
            self.add_context('prompt')

        # if model is not set, use the default model
        if 'model' not in self.session['parms']:
            self.session['model'] = self.conf.get_default_model()
        else:  # else move it up out of options since we want the full model name there
            self.session['model'] = self.session['parms']['model']
            del self.session['parms']['model']

        # make sure model is valid since it is central
        if not self.conf.valid_model(self.session['model']):
            raise ValueError(f"Invalid model: {self.session['model']}")

        # get the full model name and place it in parms for use with the API
        self.session['parms']['model'] = self.conf.get_option_from_model('model_name', self.session['model'])

        # get the provider from the model (mostly for the dump-session command)
        self.session['provider'] = self.conf.get_option_from_model('provider', self.session['model'])

        # go through all the options in the [<provider>] section and add them to the session if not already there
        for option in self.conf.get_all_options_from_provider(self.session['provider']):
            if option not in self.session['parms']:
                self.session['parms'][option] = self.conf.get_option_from_provider(option, self.session['provider'])

        return self.session

    def get_provider(self) -> APIProvider:
        """
        Initialize and return an API provider
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

        # Construct the module and class names based on the context type
        module_name = f'modes.{mode}_mode'
        class_name = f'{mode.capitalize()}Mode'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            mode_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported interaction: {mode}")

        # Instantiate the class and start the interaction
        interaction = mode_class(self)
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
