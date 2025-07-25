import os
import re
from configparser import ConfigParser, NoOptionError, NoSectionError


class ConfigHandler:
    """
    This class is used to read and process the configration files and command line arguments
    """

    def __init__(self, config_file=None):
        self.conf = self.read_config_files(config_file)
        self.models = self.read_models_files()

    @staticmethod
    def read_config_files(config_file=None) -> ConfigParser:
        """
        go through the config files and return the ConfigParser object
        :param config_file: optional path to a custom config file
        :return: ConfigParser object
        """
        # get the default config file path and make sure it exists
        default_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        if not os.path.exists(default_config_file):
            raise FileNotFoundError(f'Could not find the default config file at {default_config_file}')
        # instantiate the config parser and read the config files
        config = ConfigParser()
        config.read(default_config_file)

        # get the user config location from the default config file and check and read it
        if 'user_config' in config['DEFAULT']:
            user_config = ConfigHandler.resolve_file_path(config['DEFAULT']['user_config'])
            if user_config is not None:
                config.read(user_config)

        # if a custom config file was specified, check and read it
        if config_file is not None:
            file = ConfigHandler.resolve_file_path(config_file)
            if file is None:
                raise FileNotFoundError(f'Could not find the custom config file at {config_file}')
            config.read(config_file)  # read the custom config file
        return config

    def read_models_files(self) -> ConfigParser:
        """
        go through the models files and return the ConfigParser object

        :return: ConfigParser object
        """
        # Read the 'models.ini' file
        models_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models.ini')
        if not os.path.exists(models_file):
            raise FileNotFoundError(f'Could not find the models file at {models_file}')
        models = ConfigParser()
        models.read(models_file)

        # Get the user model definition overrides from the configuration file
        if 'user_models' in self.conf['DEFAULT']:
            user_models = ConfigHandler.resolve_file_path(self.conf['DEFAULT']['user_models'])
            if user_models is not None:
                models.read(user_models)

        return models

    def list_providers(self, showall=False) -> dict:
        """
        List the providers available in the configuration file, used mostly for output to the user
        :param showall: optional flag to show all providers
        """
        filtered_providers = {}
        if showall:
            for provider in self.conf.sections():
                filtered_providers[provider] = {option: self.conf.get(provider, option) for option in
                                                self.conf.options(provider)}
        else:
            for provider in self.conf.sections():
                if self.conf.getboolean(provider, 'active', fallback=False):
                    filtered_providers[provider] = {option: self.conf.get(provider, option) for option in
                                                    self.conf.options(provider)}

        return filtered_providers

    def list_prompts(self):
        """
        List the prompts available in the prompt directory, used mostly for output to the user
        """
        prompts = set()
        
        # Get from default prompt directory
        prompt_dir_str = self.get_option('DEFAULT', 'prompt_directory', fallback='prompts')
        if prompt_dir_str:
            prompt_dir = self.resolve_directory_path(prompt_dir_str)
            if prompt_dir and os.path.isdir(prompt_dir):
                for f in os.listdir(prompt_dir):
                    if os.path.isfile(os.path.join(prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        # Get from user prompt directory, overriding defaults
        user_prompt_dir_str = self.get_option('DEFAULT', 'user_prompt_directory', fallback=None)
        if user_prompt_dir_str:
            user_prompt_dir = self.resolve_directory_path(user_prompt_dir_str)
            if user_prompt_dir and os.path.isdir(user_prompt_dir):
                for f in os.listdir(user_prompt_dir):
                    if os.path.isfile(os.path.join(user_prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        return sorted(list(prompts)) if prompts else []

    def list_models(self, showall=False) -> dict:
        """
        List the models available in the models file, used mostly for output to the user
        :param showall: optional flag to show all models
        """
        filtered_models = {}

        # first lets add "default = True" to the default model
        for model in self.models.sections():
            if model == self.conf['DEFAULT'].get('default_model', None):
                self.models.set(model, 'default', 'True')

        if showall:
            for model in self.models.sections():
                filtered_models[model] = {option: self.models.get(model, option) for option in
                                          self.models.options(model)}
        else:
            for provider in self.conf.sections():
                # Check if the provider is active
                if self.conf.getboolean(provider, 'active', fallback=False):
                    # See if user has a preferred list of models to use for this provider, else use all
                    desired_models = self.conf.get(provider, 'models', fallback=None)
                    if desired_models is not None:
                        desired_models = [model.strip() for model in desired_models.split(',')]
                    # Filter the models based on the provider configuration
                    for model in self.models.sections():
                        if self.models.get(model,
                                           'provider') == provider:  # Check if the provider of the current model matches the current provider
                            if desired_models is None or model in desired_models:
                                filtered_models[model] = {option: self.models.get(model, option) for option in
                                                          self.models.options(model)}

        return filtered_models

    def valid_model(self, model) -> bool:
        """
        Check if a model name is valid based on output of list_models
        :param model: the model name to check
        :return: True if the model is valid, False otherwise
        """
        # check against both the keys and value of 'model_name' in the output of list_models
        return model in self.list_models().keys() or model in [model['model_name'] for model in
                                                               self.list_models().values()]

    def normalize_model_name(self, model) -> str:
        """
        Config section names and use a shorter simplified name for a model. The full name is stored in the 'model_name'
        option. The user can specify either, but we want to  normalize to the full name.
        """
        for key, value in self.list_models().items():
            if model == key or model == value['model_name']:
                return value['model_name']
        return None

    def get_option_from_model(self, option, model):
        """
        Get an option from the model based on the output of list_models
        :param option: the option to get
        :param model: the model name
        :return: the provider name
        """
        # check against both the keys and value of 'model_name' in the output of list_models
        for key, value in self.list_models().items():
            if model == key or model == value['model_name']:
                if option in value:
                    return self.fix_values(value[option])
        return None

    def get_option_from_provider(self, option, provider):
        """
        Get an option from the respective config section based on the provider
        :param option: the option to get
        :param provider: the provider name
        :return: the option value
        """
        # should make sure it exists before returning
        if self.conf.has_option(provider, option):
            return self.fix_values(self.conf.get(provider, option))
        return None

    def get_all_options_from_provider(self, provider):
        """
        Get all the options from the respective config section of provider
        :param provider: the provider name
        :return: the options for the provider
        """
        # should make sure it exists before returning
        return {option: self.fix_values(self.conf.get(provider, option)) for option in self.conf.options(provider)}

    def get_all_options_from_model(self, model):
        """
        Get all the options from the respective config section of model based on short name or full name
        :param model: the model name
        :return: the model name
        """
        # should make sure it exists before returning
        for key, value in self.list_models().items():
            if model == key or model == value['model_name']:
                return {option: self.fix_values(value[option]) for option in self.models.options(key)}
        return None

    def get_all_options_from_section(self, section):
        """
        Get all the options from the specified config section
        :param section: the section name
        :return: dict of options and their values for the section
        """
        if self.conf.has_section(section):
            return {option: self.fix_values(self.conf.get(section, option))
                    for option in self.conf.options(section)}
        return {}

    def get_default_prompt(self) -> str:
        """
        gets the prompt as a string either from user specified, <prompt_dir>/default.txt, or the fallback prompt
        :return: the prompt string
        """
        try:
            user_prompt_dir = self.get_option('DEFAULT', 'user_prompt_directory')
            prompt_dir = self.get_option('DEFAULT', 'prompt_directory')

            # if there is a default_prompt in the config file, check and make sure it exists and return it
            if self.conf.has_option('DEFAULT', 'default_prompt'):
                prompt_name = self.conf['DEFAULT']['default_prompt']
                # Check user dir first
                if user_prompt_dir:
                    prompt_file = ConfigHandler.resolve_file_path(prompt_name, user_prompt_dir, '.txt')
                    if prompt_file:
                        with open(prompt_file, 'r') as f:
                            return f.read()
                # Then check default dir
                if prompt_dir:
                    prompt_file = ConfigHandler.resolve_file_path(prompt_name, prompt_dir, '.txt')
                    if prompt_file:
                        with open(prompt_file, 'r') as f:
                            return f.read()

            # if there is a prompt_directory in the config file, check and make sure it exists and return the default.txt file
            # Check user dir first
            if user_prompt_dir:
                prompt_file = ConfigHandler.resolve_file_path("default.txt", user_prompt_dir)
                if prompt_file:
                    with open(prompt_file, 'r') as f:
                        return f.read()
            # Then check default dir
            if prompt_dir:
                prompt_file = ConfigHandler.resolve_file_path("default.txt", prompt_dir)
                if prompt_file:
                    with open(prompt_file, 'r') as f:
                        return f.read()

            # if there is a fallback_prompt in the config file, check and make sure it exists and return it
            return self.get_option('DEFAULT', 'fallback_prompt', fallback='')

        except FileNotFoundError:
            print(f'Warning: Could not find the prompt file. Using fallback prompt.')
            return self.get_option('DEFAULT', 'fallback_prompt', fallback='')

    def get_option(self, section, option, fallback=None):
        """
        Get a setting from the configuration file

        :param section: the section to get the setting from
        :param option: the option to get
        :param fallback: the value to return if the option is not found. Defaults to None.
        :return: the setting
        """
        try:
            return self.fix_values(self.conf.get(section, option))
        except NoSectionError:
            return fallback
        except NoOptionError:
            return fallback

    @staticmethod
    def fix_values(value):
        """
        Fix some values due to how they are stored and retrieved with ConfigParser
        """
        if isinstance(value, str):
            value = value.strip()

            # Handle path expansion only for strings that clearly look like paths
            if (value.startswith(('~', './', '/', '\\')) and
                    not value.startswith(('{', '[', '"', "'"))):
                expanded = os.path.expanduser(value)
                if expanded != value:
                    value = expanded

            # Handle dict-like strings
            if value.startswith('{') and value.endswith('}'):
                pairs = re.findall(r'(\w+)\s*:\s*(\[.*?]|[^,]+)(?=\s*(?:,|$))', value[1:-1])
                return {k.strip(): ConfigHandler.fix_values(v.strip()) for k, v in pairs}

            # Handle list-like strings
            if value.startswith('[') and value.endswith(']'):
                return [ConfigHandler.fix_values(item.strip()) for item in re.findall(r'<[^>]+>|[^,\s]+', value[1:-1])]

            # Check for integer values
            if value.isdigit():
                return int(value)

            # Handle boolean values
            lower_value = value.lower()
            if lower_value in ('true', 'yes', '1'):
                return True
            if lower_value in ('false', 'no', '0'):
                return False

            # Remove quotes if present
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                return value[1:-1]

        # Return as is for other cases
        return value

    @staticmethod
    def resolve_file_path(file_name: str, base_dir=None, extension=None):
        """
        works out the path to a file based on the filename and optional base directory and can take an optional extension
        :param file_name: name of the file to resolve the path to
        :param base_dir: optional base directory to resolve the path from
        :param extension: optional extension to append to the file name
        :return: absolute path to the file or None
        """
        # return if file_name is None
        if file_name is None:
            return None

        # If base_dir is not specified, use the current working directory
        if base_dir is None:
            base_dir = os.getcwd()
        # If base_dir is a relative path, convert it to an absolute path based on the main.py directory
        elif not os.path.isabs(base_dir):
            main_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = os.path.abspath(os.path.join(main_dir, base_dir))
        # Expand user's home directory if base_dir starts with a tilde
        base_dir = os.path.expanduser(base_dir)

        # Check if base_dir exists and is a directory
        if not os.path.isdir(base_dir):
            return None

        # If the file_name is an absolute path, check if it exists
        file_name = os.path.expanduser(file_name)
        if os.path.isabs(file_name):
            if os.path.isfile(file_name):
                return file_name
            elif extension is not None and os.path.isfile(file_name + extension):
                return file_name + extension
        else:
            # If the file_name is a relative path, check if it exists
            full_path = os.path.join(base_dir, file_name)
            if os.path.isfile(full_path):
                return full_path
            elif extension is not None and os.path.isfile(full_path + extension):
                return full_path + extension

            # If the file_name is just a file name, check if it exists in the base directory
            full_path = os.path.join(base_dir, file_name)
            if os.path.isfile(full_path):
                return full_path
            elif extension is not None and os.path.isfile(full_path + extension):
                return full_path + extension

        # If none of the conditions are met, return None
        return None

    @staticmethod
    def resolve_directory_path(dir_name: str):
        """
        works out the path to a directory
        :param dir_name: name of the directory to resolve the path to
        :return: absolute path to the directory
        """
        dir_name = os.path.expanduser(dir_name)
        if not os.path.isabs(dir_name):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), dir_name)
            if os.path.isdir(path):
                return path
        else:
            if os.path.isdir(dir_name):
                return dir_name
        return None
