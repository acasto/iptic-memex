from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from typing import Optional, Any, Generator
from config_handler import ConfigHandler
from utils_handler import UtilsHandler
import os
from importlib import util


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
    def get_full_response(self) -> Any:
        pass

    @abstractmethod
    def get_usage(self) -> Any:
        pass

    @abstractmethod
    def reset_usage(self) -> Any:
        pass

    @abstractmethod
    def get_cost(self) -> dict:
        """Calculate cost based on token usage and model pricing"""
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
    def run(self, *args, **kwargs):
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
            "tools": {}
        }
        self.user_options = {}

    def set_option(self, key: str, value: str, mode: str = 'params'):
        """
        Set a session option in either params or tools mode
        :param key: the key to set
        :param value: the value to set
        :param mode: which settings to modify ('params' or 'tools')
        """
        if mode == 'params':
            # Existing params logic
            if key == 'model':
                if not self.conf.valid_model(value):
                    print(f"Invalid model: {value}")
                    quit()
                else:
                    value = self.conf.normalize_model_name(value)
                self.user_options[key] = value
                if self.session_state['provider']:
                    self.configure_session()
            else:
                # Convert string booleans
                if isinstance(value, str) and value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                self.user_options[key] = value
                self.session_state['params'][key] = value
        elif mode == 'tools':
            # Convert string booleans for tools too
            if isinstance(value, str) and value.lower() in ['true', 'false']:
                value = value.lower() == 'true'
            self.session_state['tools'][key] = value

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

        # Special handling for prompt context
        if context_type == 'prompt':
            resolved_content = self.resolve_prompt(context_data)
            context_data = resolved_content

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

    def get_action(self, action: str, action_folder: str = "actions") -> InteractionAction | None:
        """
        Instantiate and return an action class with improved error handling.
        Checks user actions directory first, then project actions directory.

        Args:
            action: The action to instantiate (e.g., 'load_file')
            action_folder: The folder where the action is located (default: 'actions')

        Returns:
            An instance of the requested action class

        Raises:
            ValueError: If the action cannot be loaded or instantiated
        """
        # Construct class name using consistent naming convention
        class_name = ''.join(word.capitalize() for word in action.split('_')) + 'Action'
        action_module_name = f"{action}_action"

        # Try user actions directory first if configured
        user_actions_dir = self.conf.get_option('DEFAULT', 'user_actions', fallback=None)
        if user_actions_dir:
            try:
                resolved_dir = self.utils.fs.resolve_directory_path(user_actions_dir)
                if resolved_dir:
                    user_action_path = os.path.join(resolved_dir, f"{action_module_name}.py")

                    if os.path.isfile(user_action_path):
                        try:
                            # Attempt to load user action
                            spec = importlib.util.spec_from_file_location(
                                action_module_name,
                                user_action_path
                            )
                            if spec is None:
                                raise ImportError(f"Could not load spec from {user_action_path}")

                            user_module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(user_module)

                            # Verify the class exists and is valid
                            if not hasattr(user_module, class_name):
                                raise AttributeError(f"Class {class_name} not found in {user_action_path}")

                            user_action_class = getattr(user_module, class_name)
                            return self._instantiate_action(user_action_class)

                        except Exception as e:
                            print(f"\nWarning: Failed to load user action {action}: {str(e)}\n")
                            # Fall through to try project actions

            except Exception as e:
                print(f"\nWarning: Error processing user actions directory: {str(e)}\n")
                # Fall through to try project actions

        # Try project actions directory
        try:
            module_name = f'{action_folder}.{action_module_name}'
            module = importlib.import_module(module_name)

            if not hasattr(module, class_name):
                raise AttributeError(f"Class {class_name} not found in {module_name}")

            action_class = getattr(module, class_name)
            return self._instantiate_action(action_class)

        except Exception as e:
            error_msg = f"\nFailed to load action '{action}': {str(e)}\n"
            error_msg += f"Expected to find class '{class_name}' in module '{action_module_name}'\n"
            if user_actions_dir:
                error_msg += f"Checked user actions directory: {user_actions_dir}\n"
            error_msg += f"Checked project actions directory: {action_folder}\n"
            print(error_msg)
            return None

    def _instantiate_action(self, action_class) -> InteractionAction:
        """
        Helper method to safely instantiate an action class

        Args:
            action_class: The class to instantiate

        Returns:
            An instance of the action class

        Raises:
            ValueError: If instantiation fails
        """
        try:
            instance = action_class(self)
            if not isinstance(instance, InteractionAction):
                raise ValueError(f"Class {action_class.__name__} does not inherit from InteractionAction")
            return instance
        except Exception as e:
            raise ValueError(f"Failed to instantiate action: {str(e)}")

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

        # Load tool settings from config
        tool_options = {k: v for k, v in self.conf.get_all_options_from_section('TOOLS').items()}
        self.session_state['tools'] = tool_options

    def set_provider(self, provider):
        """
        Initialize and set an API provider with detailed error reporting
        """
        # Check if provider is an alias
        alias = self.conf.get_option_from_provider('alias', provider)
        if alias:
            provider = alias

        module_name = f'providers.{provider.lower()}_provider'
        class_name = f'{provider}Provider'

        try:
            # Try importing the module
            try:
                module = importlib.import_module(module_name)
            except ImportError as e:
                error_msg = f"\nFailed to import provider module '{module_name}':\n{str(e)}"
                if util.find_spec(module_name) is None:
                    error_msg += f"\nModule '{module_name}.py' not found in providers directory"
                print(error_msg)
                quit()

            # Try getting the provider class
            try:
                provider_class = getattr(module, class_name)
            except AttributeError:
                print(f"\nProvider class '{class_name}' not found in {module_name}.py")
                print(f"Expected to find class definition: class {class_name}(APIProvider)")
                quit()

            # Try instantiating the provider
            try:
                self.session_state['provider'] = provider_class(self)
            except Exception as e:
                print(f"\nFailed to instantiate provider '{provider}':")
                print(f"Error: {str(e)}")
                if hasattr(e, '__traceback__'):
                    import traceback
                    traceback.print_tb(e.__traceback__)
                quit()

        except Exception as e:
            print(f"\nUnexpected error setting up provider '{provider}':")
            print(f"Error: {str(e)}")
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

    def resolve_prompt(self, prompt_source=None):
        """
        Central method for resolving prompts in a consistent way.

        Args:
            prompt_source: Optional source prompt (from user/CLI/etc)

        Returns:
            Resolved prompt content as string
        """
        def resolve_single_prompt(source):
            """Helper to resolve a single prompt source"""
            if not source or not isinstance(source, str):
                return ''

            # Handle none/false case
            if source.lower() in ['none', 'false']:
                return ''

            # Check if it's a chain in the PROMPTS section
            chain = self.conf.get_option('PROMPTS', source, fallback=None)
            if chain:
                resolved_contents = []
                # Split and recursively resolve each part
                for part in (p.strip() for p in chain.split(',')):
                    resolved = resolve_single_prompt(part)
                    if resolved:
                        resolved_contents.append(resolved)
                return '\n\n'.join(resolved_contents)

            # Check if source contains commas (comma-separated prompts from command line)
            if ',' in source:
                resolved_contents = []
                # Split and recursively resolve each part
                for part in (p.strip() for p in source.split(',')):
                    resolved = resolve_single_prompt(part)
                    if resolved:
                        resolved_contents.append(resolved)
                return '\n\n'.join(resolved_contents)

            # Check user prompt directory first
            user_prompt_dir_str = self.conf.get_option('DEFAULT', 'user_prompt_directory', fallback=None)
            if user_prompt_dir_str:
                user_prompt_dir = self.conf.resolve_directory_path(user_prompt_dir_str)
                if user_prompt_dir:
                    prompt_file = self.conf.resolve_file_path(source, user_prompt_dir, '.txt')
                    if prompt_file and os.path.exists(prompt_file):
                        with open(prompt_file, 'r') as f:
                            return f.read()

            # Then check default prompt directory for file
            prompt_dir_str = self.conf.get_option('DEFAULT', 'prompt_directory', fallback=None)
            if prompt_dir_str:
                prompt_dir = self.conf.resolve_directory_path(prompt_dir_str)
                if prompt_dir:
                    prompt_file = self.conf.resolve_file_path(source, prompt_dir, '.txt')
                    if prompt_file and os.path.exists(prompt_file):
                        with open(prompt_file, 'r') as f:
                            return f.read()

            # Check if direct file path
            prompt_file = self.conf.resolve_file_path(source)
            if prompt_file and os.path.exists(prompt_file):
                with open(prompt_file, 'r') as f:
                    return f.read()

            # Treat as direct prompt text if not a file
            if source.strip():
                return source

            return ''

        # Start resolution process
        if prompt_source:
            return resolve_single_prompt(prompt_source)

        # Check for model-specific prompt
        model = self.session_state['params'].get('model')
        if model:
            model_prompt = self.conf.get_option_from_model('prompt', model)
            if model_prompt:
                return resolve_single_prompt(model_prompt)

        # Finally fall back to default prompt
        return self.conf.get_default_prompt()

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

    def get_tools(self):
        """
        Get the tool settings from the session state
        :return: dict of tool settings
        """
        return self.session_state.get('tools', {})

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
        return None

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

    def handle_exit(self, prompt: bool = True) -> bool:
        """
        Clean up resources and handle program exit
        Args:
            prompt: Whether to prompt for confirmation
        Returns:
            True if exit should proceed, False if canceled
        """
        if prompt:
            try:
                user_input = self.utils.input.get_input(
                    self.utils.output.style_text("Hit Ctrl-C or enter 'y' to quit: ", "red")
                )
                if user_input.lower() != 'y':
                    return False
            except (KeyboardInterrupt, EOFError):
                self.utils.output.write()

        # Run cleanup tasks
        provider: Optional[APIProvider] = self.session_state['provider']
        if provider and hasattr(provider, 'cleanup'):
            try:
                provider.cleanup()
            except Exception as e:
                self.utils.output.error(f"Error during provider cleanup: {e}")

        # Persist stats
        self.get_action('persist_stats').run()
        return True

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