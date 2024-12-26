import importlib
from abc import ABC, abstractmethod
from typing import Any, Generator
from config_handler import ConfigHandler
from utils_handler import UtilsHandler


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
    def chat(self) -> Any:
        pass

    @abstractmethod
    def stream_chat(self) -> Generator[Any, None, None]:
        pass

    @abstractmethod
    def get_messages(self) -> Any:
        pass

    @abstractmethod
    def get_usage(self) -> Any:
        pass

    @abstractmethod
    def reset_usage(self) -> Any:
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

    def __init__(self, config_file=None):
        self.conf = ConfigHandler(config_file)
        self.utils = UtilsHandler(self.conf)
        self.session_state = {
            "context": {},
            "params": {},
            "provider": None,
        }
        self.user_options = {}

    def set_option(self, key, value):
        """
        Set a user option which can be processed later into the session configuration
        :param key: the key to set
        :param value: the value to set
        """
        # if we're dealing with the model, make sure it is valid and normalized to the full model name
        if key == 'model':
            if not self.conf.valid_model(value):
                print(f"Invalid model: {value}")
                quit()
            else:
                value = self.conf.normalize_model_name(value)
            # save the model to the user_options dict
            self.user_options[key] = value
            # if a provider is already set, we need to reconfigure the session
            if self.session_state['provider']:
                self.configure_session()
        else:
            # if value is a string and looks like it should be a bool then convert it
            if isinstance(value, str) and value.lower() in ['true', 'false']:
                value = value.lower() == 'true'
            # if we're not dealing with a model change then we can save it to user_options and go
            # ahead and update the session state.
            self.user_options[key] = value
            self.session_state['params'][key] = value

    def add_context(self, context_type: str, context_data=None):
        """
        Add a context object to the session
        :param context_type: the type of context to add
        :param context_data: the data for the context
        """
        # Construct the module and class names based on the context type
        module_name = f'contexts.{context_type}_context'
        class_name = ''.join(word.capitalize() for word in context_type.split('_'))
        class_name += 'Context'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            context_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported context type: {context_type}")

        # Add the context to the session under self.session[context_type]
        if context_type not in self.session_state['context']:
            self.session_state['context'][context_type] = []
        self.session_state['context'][context_type].append(context_class(self, context_data))

    def remove_context_type(self, context_type: str):
        """
        Remove a context object from the session
        :param context_type: the type of context to remove
        """
        if context_type in self.session_state['context']:
            self.session_state['context'].pop(context_type)

    def remove_context_item(self, context_type: str, index: int):
        """
        Remove a context object from the session
        :param context_type: the type of context to remove
        :param index: the index of the context to remove
        """
        if context_type in self.session_state['context']:
            if index < len(self.session_state['context'][context_type]):
                self.session_state['context'][context_type].pop(index)

    def get_action(self, action: str, action_folder: str = "actions"):
        """
        Instantiate and return an action class
        :param action: the action to instantiate
        :param action_folder: the folder where the action is located
        """
        # construct the module and class names based on the action
        module_name = f'{action_folder}.{action}_action'
        class_name = ''.join(word.capitalize() for word in action.split('_'))
        class_name += 'Action'

        # import the module and get the class
        try:
            module = importlib.import_module(module_name)
            action_class = getattr(module, class_name)
            return action_class(self)
        except (ImportError, AttributeError):
            # raise ValueError(f"Unsupported action: {action}")
            return None

    def configure_session(self):
        """
        (re)build the settings for the session by reconciling user supplied options and those from ConfigHandler
        """
        # clear the params and provider
        self.session_state['params'] = {}
        self.session_state['provider'] = None

        # let the user no if no providers are available
        if not self.conf.list_providers():
            print(f"\nNo active providers available. Check the config file.\n")
            quit()

        # if model is not set, use the default model
        if 'model' not in self.user_options:
            self.session_state['params']['model'] = self.conf.normalize_model_name(
                self.conf.get_option('DEFAULT', 'default_model'))
        else:
            self.session_state['params']['model'] = self.user_options['model']
        model = self.session_state['params']['model']  # for our convenience

        # get the default prompt if needed
        if 'prompt' not in self.session_state['context']:
            # does model have a prompt defined?
            if self.conf.get_option_from_model('prompt', model):
                self.add_context('prompt', self.conf.get_option_from_model('prompt', model))
            else:
                # use the default prompt
                self.add_context('prompt')

        # get the provider for the model for our convenience
        provider = self.conf.get_option_from_model('provider', model)

        # go through all the options in the [<model>] section and add them to the session
        for option in self.conf.get_all_options_from_model(model):
            if option not in self.session_state['params']:
                self.session_state['params'][option] = self.conf.get_option_from_model(option, model)

        # go through all the options in the [<provider>] section and add them to the session
        for option in self.conf.get_all_options_from_provider(provider):
            if option not in self.session_state['params']:
                self.session_state['params'][option] = self.conf.get_option_from_provider(option, provider)

        # set the user options to the session state
        self.session_state['params'].update(self.user_options)

        # set the provider
        self.set_provider(provider)

    def set_provider(self, provider):
        """
        Initialize and set an API provider
        """
        # See if the provider is an alias
        alias = self.conf.get_option_from_provider('alias', provider)
        if alias:
            provider = alias

        # Construct the module and class names based on the provider
        module_name = f'providers.{provider.lower()}_provider'
        class_name = f'{provider}Provider'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            provider_class = getattr(module, class_name)
            self.session_state['provider'] = provider_class(self)
        except (ImportError, AttributeError):
            print(f"\nUnsupported provider: {provider}\n")
            quit()

    def start_mode(self, mode: str):
        """
        Start an interaction with the user
        :param mode: the interaction to start
        """
        # Make sure the session is configured
        if not self.session_state['provider']:
            self.configure_session()

        # Construct the module and class names based on the context type
        module_name = f'modes.{mode}_mode'
        class_name = ''.join(word.capitalize() for word in mode.split('_'))
        class_name += 'Mode'

        # Import the module and get the class
        try:
            module = importlib.import_module(module_name)
            mode_class = getattr(module, class_name)
        except (ImportError, AttributeError):
            raise ValueError(f"Unsupported interaction: {mode}")

        # Instantiate the class and start the interaction
        interaction = mode_class(self)
        interaction.start()

    def get_session_state(self):
        """
        Get the current session state
        """
        return self.session_state

    def get_provider(self):
        """
        Get the provider from the session
        """
        return self.session_state['provider']

    def get_params(self):
        """
        Get the parameters from the session
        """
        return self.session_state['params']

    def get_context(self, context_type=None):
        """
        Get the context from the session
        :param context_type: the type of context to get
        """
        # if context_type is prompt or chat return the object
        # if context_type is something else returns a list of objects
        # if context type is None return the whole context dict
        if context_type and context_type in self.session_state['context']:
            if context_type == 'prompt':
                return self.session_state['context']['prompt'][0]
            if context_type == 'chat':
                return self.session_state['context']['chat'][0]
            else:
                return self.session_state['context'][context_type]

        if context_type is None:
            return self.session_state['context']

    def set_flag(self, flag_name: str, value: bool) -> None:
        """
        Set a session flag
        :param flag_name: name of the flag to set
        :param value: boolean value for the flag
        """
        if "flags" not in self.session_state:
            self.session_state["flags"] = {}
        self.session_state["flags"][flag_name] = value

    def get_flag(self, flag_name: str, default: bool = False) -> bool:
        """
        Get a session flag value
        :param flag_name: name of the flag to get
        :param default: default value if flag is not set
        :return: the flag's value
        """
        return self.session_state.get("flags", {}).get(flag_name, default)

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
