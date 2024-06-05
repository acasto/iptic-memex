import os
from configparser import ConfigParser
from helpers import resolve_file_path, resolve_directory_path


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
            user_config = resolve_file_path(config['DEFAULT']['user_config'])
            if user_config is not None:
                config.read(user_config)

        # if a custom config file was specified, check and read it
        if config_file is not None:
            file = resolve_file_path(config_file)
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
            user_models = resolve_file_path(self.conf['DEFAULT']['user_models'])
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
        prompt_dir = self.conf['DEFAULT'].get('prompt_directory', None)
        if prompt_dir is None:
            return None
        prompt_dir = resolve_directory_path(prompt_dir)
        if prompt_dir is None:
            return None
        return [f for f in os.listdir(prompt_dir) if os.path.isfile(os.path.join(prompt_dir, f))]

    # def list_chats(self):
    #     """
    #     List the chat logs available in the chat directory, used mostly for output to the user
    #     """
    #     chat_dir = self.conf['DEFAULT'].get('chats_directory', None)
    #     if chat_dir is None:
    #         return None
    #     chat_dir = resolve_directory_path(chat_dir)
    #     if chat_dir is None:
    #         return None
    #     return [f for f in os.listdir(chat_dir) if os.path.isfile(os.path.join(chat_dir, f))]

    def list_models(self, showall=False) -> dict:
        """
        List the models available in the models file, used mostly for output to the user
        :param showall: optional flag to show all models
        """
        filtered_models = {}
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

    def get_prompt(self) -> str:
        """
        gets the prompt as a string either from user specified, <prompt_dir>/default.txt, or the fallback prompt
        :return: the prompt string
        """
        try:
            if 'prompt' in self.user_options:
                input_prompt = self.user_options['prompt']

                # if prompt is a file in prompt_directory check and make sure it exists and return it
                prompt_file = resolve_file_path(input_prompt, self.conf['DEFAULT']['prompt_directory'], '.txt')
                if prompt_file is not None:
                    with open(prompt_file, 'r') as f:
                        return f.read()

                # if prompt is a file check and make sure it exists and return it
                prompt_file = resolve_file_path(input_prompt)
                if prompt_file is not None:
                    with open(prompt_file, 'r') as f:
                        return f.read()

                # if it seems like the user meant to specify a file, but it doesn't exist, raise an error
                if input_prompt.endswith(('.txt', '.md', '.prompt')) or ' ' not in input_prompt:
                    raise FileNotFoundError(f'Could not find the prompt file at {input_prompt}')

                # if prompt is not a file but a string check and make sure it's not empty and return it
                elif self.user_options['prompt'].strip() != '':
                    return self.user_options['prompt']

            # if there is a default_prompt in the config file, check and make sure it exists and return it
            if self.conf.has_option('DEFAULT', 'default_prompt'):
                prompt_file = resolve_file_path(self.conf['DEFAULT']['default_prompt'],
                                                self.conf['DEFAULT']['prompt_directory'], '.txt')
                if prompt_file is not None:
                    with open(prompt_file, 'r') as f:
                        return f.read()

            # if there is a prompt_directory in the config file, check and make sure it exists and return the default.txt file
            if self.conf.has_option('DEFAULT', 'prompt_directory'):
                prompt = resolve_file_path("default.txt", self.conf['DEFAULT']['prompt_directory'])
                if prompt is not None:
                    with open(prompt, 'r') as f:
                        return f.read()

            # if there is a fallback_prompt in the config file, check and make sure it exists and return it
            if self.conf.has_option('DEFAULT', 'fallback_prompt'):
                return self.conf['DEFAULT'].get('fallback_prompt', None)

        except FileNotFoundError:
            print(f'Warning: Could not find the prompt file. Using fallback prompt.')
            return self.conf['DEFAULT'].get('fallback_prompt', None)
