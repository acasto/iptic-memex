import os
import re
from configparser import ConfigParser, NoOptionError, NoSectionError
from typing import Dict, Any, Optional, List

from core.model_catalog import CONNECTION_OPTIONS, ModelCatalog


class ConfigManager:
    """
    Immutable configuration manager - reads config files once and provides 
    session-specific configuration objects
    """
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = None
        self.base_config = self._load_configs(config_file)
        self.models = self._load_models()
        self.model_catalog = ModelCatalog(self.base_config)
        self.discovered_models = self.model_catalog.read()
        
    def _load_configs(self, config_file: Optional[str] = None) -> ConfigParser:
        """
        Load and merge configuration files
        :param config_file: optional path to a custom config file
        :return: ConfigParser object
        """
        # Get the default config file path and make sure it exists
        default_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        if not os.path.exists(default_config_file):
            raise FileNotFoundError(f'Could not find the default config file at {default_config_file}')
            
        # Instantiate the config parser and read the config files
        config = ConfigParser()
        config.read(default_config_file)

        # Get the user config location from the default config file and check and read it
        if 'user_config' in config['DEFAULT']:
            user_config = self.resolve_file_path(config['DEFAULT']['user_config'])
            if user_config is not None:
                config.read(user_config)

        # If a custom config file was specified, check and read it
        if config_file is not None:
            file = self.resolve_file_path(config_file)
            if file is None:
                raise FileNotFoundError(f'Could not find the custom config file at {config_file}')
            self.config_file = file
            config.read(file)
            
        return config

    def _load_models(self) -> ConfigParser:
        """
        Load model configuration files
        :return: ConfigParser object
        """
        # Read the 'models.ini' file
        models_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models.ini')
        if not os.path.exists(models_file):
            raise FileNotFoundError(f'Could not find the models file at {models_file}')
            
        models = ConfigParser()
        models.read(models_file)

        # Get the user model definition overrides from the configuration file
        if 'user_models' in self.base_config['DEFAULT']:
            user_models = self.resolve_file_path(self.base_config['DEFAULT']['user_models'])
            if user_models is not None:
                models.read(user_models)

        return models
    
    def create_session_config(self, overrides: Optional[Dict[str, Any]] = None) -> 'SessionConfig':
        """Create a mutable session-specific config"""
        if overrides is None:
            overrides = {}
        
        config = SessionConfig(self.base_config, self.models, overrides,
                               getattr(self, 'discovered_models', None))
        if 'model' in overrides:
            model = overrides['model']
            normalized_model = config.normalize_model_name(model)
            if normalized_model:
                overrides['model'] = normalized_model
        
        return config
    
    def list_models(self, active_only: bool = True) -> Dict[str, Dict[str, Any]]:
        """List available models"""
        return self.create_session_config().list_models(showall=not active_only,
                                                        include_unverified=True)
    
    def list_providers(self, active_only: bool = True) -> Dict[str, Dict[str, Any]]:
        """List available providers"""
        filtered_providers = {}
        
        if not active_only:
            for provider in self.base_config.sections():
                filtered_providers[provider] = {
                    option: self.fix_values(self.base_config.get(provider, option))
                    for option in self.base_config.options(provider)
                }
        else:
            for provider in self.base_config.sections():
                if self.base_config.getboolean(provider, 'active', fallback=False):
                    filtered_providers[provider] = {
                        option: self.fix_values(self.base_config.get(provider, option))
                        for option in self.base_config.options(provider)
                    }

        return filtered_providers
    
    def list_prompts(self) -> List[str]:
        """List available prompts from prompt directories"""
        prompts = set()
        
        # Get from default prompt directory
        prompt_dir_str = self._get_base_option('DEFAULT', 'prompt_directory', fallback='prompts')
        if prompt_dir_str:
            prompt_dir = self.resolve_directory_path(prompt_dir_str)
            if prompt_dir and os.path.isdir(prompt_dir):
                for f in os.listdir(prompt_dir):
                    if os.path.isfile(os.path.join(prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        # Get from user prompt directory, overriding defaults
        user_prompt_dir_str = self._get_base_option('DEFAULT', 'user_prompts', fallback=None)
        if user_prompt_dir_str:
            user_prompt_dir = self.resolve_directory_path(user_prompt_dir_str)
            if user_prompt_dir and os.path.isdir(user_prompt_dir):
                for f in os.listdir(user_prompt_dir):
                    if os.path.isfile(os.path.join(user_prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        return sorted(list(prompts)) if prompts else []
    
    def _get_base_option(self, section: str, option: str, fallback: Any = None) -> Any:
        """Get an option from the base configuration"""
        try:
            return self.fix_values(self.base_config.get(section, option))
        except (NoSectionError, NoOptionError):
            return fallback
    
    @staticmethod
    def fix_values(value: Any) -> Any:
        """Fix some values due to how they are stored and retrieved with ConfigParser"""
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
                return {k.strip(): ConfigManager.fix_values(v.strip()) for k, v in pairs}

            # Handle list-like strings
            if value.startswith('[') and value.endswith(']'):
                return [ConfigManager.fix_values(item.strip()) for item in re.findall(r'<[^>]+>|[^,\s]+', value[1:-1])]

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
    def resolve_file_path(file_name: str, base_dir: Optional[str] = None, extension: Optional[str] = None) -> Optional[str]:
        """
        Works out the path to a file based on the filename and optional base directory
        :param file_name: name of the file to resolve the path to
        :param base_dir: optional base directory to resolve the path from
        :param extension: optional extension to append to the file name
        :return: absolute path to the file or None
        """
        # Return if file_name is None
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
    def resolve_directory_path(dir_name: str) -> Optional[str]:
        """
        Works out the path to a directory
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


class SessionConfig:
    """
    Mutable configuration for a specific session.
    Handles runtime overrides and model/provider specific settings.
    Provides backward compatibility with ConfigHandler interface.
    """
    
    def __init__(self, base_config: ConfigParser, models: ConfigParser,
                 overrides: Optional[Dict[str, Any]] = None, discovered_models=None):
        self.base_config = base_config
        self.models = models
        self.discovered_models = discovered_models or {}
        self.overrides = overrides or {}
        self._cached_params = None
        self._current_model = None
        
        # Backward compatibility - expose the base config as 'conf'
        self.conf = base_config
    
    def get_params(self, model: Optional[str] = None) -> Dict[str, Any]:
        """Get merged parameters for current model/provider"""
        if model is None:
            # Get model from overrides first, then from default_model setting
            model = self.overrides.get('model')
            if model is None:
                model = self.base_config.get('DEFAULT', 'default_model', fallback=None)
        
        # Cache based on current model
        if self._current_model == model and self._cached_params is not None:
            return self._cached_params
        
        # Merge base -> provider -> model -> overrides
        params = {}
        
        # Start with base config DEFAULT section
        params.update({k: ConfigManager.fix_values(v) for k, v in self.base_config['DEFAULT'].items()})
        
        # Set the model parameter - use the section name for internal reference
        if model:
            # Normalize to get the section name (display name)
            normalized_model = self.normalize_model_name(model)
            if normalized_model is None and isinstance(model, str):
                provider, separator, _ = self._expand_model_prefix(model).partition(':')
                if separator and self.base_config.has_section(provider):
                    raise RuntimeError(
                        f"Unknown model '{model}'. Enable discovery and run 'list-models --refresh', "
                        "or add it to models.ini."
                    )
            section_name = normalized_model if normalized_model else model
            params['model'] = section_name  # Keep section name for internal use
            
            # Add provider-specific config if model is specified
            model_config = self._model_options(section_name)
            provider = model_config.get('provider')
            if provider:
                params['provider'] = provider
                provider_config = self._get_provider_config(provider)
                params.update(provider_config)
                
                # Add model-specific config - this will include the actual model_name for the provider
                params.update(model_config)
                if section_name in self.discovered_models and not self.models.has_section(section_name):
                    params.setdefault('price_in', None)
                    params.setdefault('price_out', None)
        
        # Apply overrides (this will override the model if explicitly set)
        params.update(self.overrides)
        
        # Normalize selected path-like options to be app-relative when not absolute
        try:
            params = self._normalize_paths(params)
        except Exception:
            # Best-effort; keep params even if normalization fails
            pass

        # Cache the result
        self._cached_params = params
        self._current_model = model
        
        return params

    # --- internal helpers ---------------------------------------------------
    def _app_root(self) -> str:
        """Directory considered the application root (where main.py lives)."""
        # This module (config_manager.py) sits in the project root next to main.py
        return os.path.dirname(os.path.abspath(__file__))

    def _resolve_app_relative(self, path: Any) -> Any:
        """Expand `~` and make relative paths resolve against the app root."""
        if not isinstance(path, str) or not path:
            return path
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded):
            return expanded
        return os.path.join(self._app_root(), expanded)

    def _normalize_paths(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Apply app-relative resolution to known directory/file options."""
        out = dict(params)
        # chats_directory should not follow CWD; make it app-relative if not absolute
        if 'chats_directory' in out:
            out['chats_directory'] = self._resolve_app_relative(out.get('chats_directory'))
        return out
    
    def set_option(self, key: str, value: Any) -> None:
        """Set a runtime override"""
        self.overrides[key] = value
        self._cached_params = None  # Invalidate cache
        self._current_model = None
    
    def get_option(self, section: str, option: str, fallback: Any = None) -> Any:
        """
        Get a setting from the configuration - backward compatibility method
        
        :param section: the section to get the setting from
        :param option: the option to get
        :param fallback: the value to return if the option is not found
        :return: the setting value
        """
        # First check overrides for the option name directly (ignoring section for overrides)
        if option in self.overrides:
            return self.overrides[option]
        
        # Then check the specific section in base config
        try:
            return ConfigManager.fix_values(self.base_config.get(section, option))
        except (NoSectionError, NoOptionError):
            # If not found and this is DEFAULT section, check merged params
            if section == 'DEFAULT':
                params = self.get_params()
                return params.get(option, fallback)
            return fallback
    
    def _get_provider_config(self, provider: str) -> Dict[str, Any]:
        """Get configuration for a specific provider"""
        if self.base_config.has_section(provider):
            return {
                option: ConfigManager.fix_values(self.base_config.get(provider, option))
                for option in self.base_config.options(provider)
            }
        return {}
    
    def _catalog_alias(self, section):
        provider = self.models.get(section, 'provider', fallback=None)
        model = self.models.get(section, 'model_name', fallback=section)
        qualified = f'{provider}:{model}'
        if qualified not in self.discovered_models:
            return None
        # A model may target a different endpoint or account from its provider section.
        for option in CONNECTION_OPTIONS:
            value = self.models.get(section, option, raw=True, fallback=None)
            if value is not None and value != self.base_config.get(provider, option, raw=True,
                                                                   fallback=None):
                return None
        return qualified

    def _model_options(self, section) -> Dict[str, Any]:
        if self.models.has_section(section):
            discovered = self.discovered_models.get(self._catalog_alias(section), {})
            configured = {key: ConfigManager.fix_values(self.models.get(section, key))
                          for key in self.models.options(section)}
            return {**discovered, **configured}
        if section in self.discovered_models:
            defaults = {key: ConfigManager.fix_values(value)
                        for key, value in self.models.defaults().items()}
            return {**defaults, **self.discovered_models[section]}
        return {}

    def _get_model_config(self, model: str) -> Dict[str, Any]:
        return self._model_options(self.normalize_model_name(model))

    def valid_model(self, model: str) -> bool:
        """Check if a configured or discovered model name is valid."""
        return self.normalize_model_name(model) is not None

    def _expand_model_prefix(self, model: str) -> str:
        prefix, separator, model_id = model.partition(':')
        if not separator or not prefix or self.base_config.has_section(prefix):
            return model
        providers = [section for section in self.base_config.sections()
                     if self.base_config.get(section, 'model_prefix', fallback=None) == prefix]
        if len(providers) > 1:
            raise RuntimeError(f"Model prefix '{prefix}' is configured for multiple providers: "
                               f"{', '.join(providers)}.")
        return f'{providers[0]}:{model_id}' if providers else model

    def normalize_model_name(self, model: str) -> Optional[str]:
        """Resolve an INI alias, a configured model ID, or a qualified catalog ID."""
        if self.models.has_section(model):
            return model
        qualified = self._expand_model_prefix(model) if isinstance(model, str) else model
        if qualified != model and self.models.has_section(qualified):
            return qualified
        if qualified in self.discovered_models:
            return next((section for section in self.models.sections()
                         if self._catalog_alias(section) == qualified), qualified)
        for section in self.models.sections():
            model_name = self.models.get(section, 'model_name', fallback=section)
            if model == model_name:
                return section
        return None

    def display_model_name(self, model: str) -> str:
        """Prefer a discovered model's shortcut when it selects the same model."""
        record = self.discovered_models.get(model)
        if not record or self.models.has_section(model):
            return model
        prefix = self.base_config.get(record['provider'], 'model_prefix', fallback=None)
        if not prefix:
            return model
        shortcut = f'{prefix}:{record["model_name"]}'
        try:
            return shortcut if self.normalize_model_name(shortcut) == model else model
        except RuntimeError:
            # An ambiguous prefix should not make the model list unusable.
            return model

    def get_default_prompt_source(self) -> str:
        """
        Determines the source of the default prompt from config files.
        Returns the name of the prompt/chain, or a filename.
        """
        # 1. Check for 'default' in [PROMPTS] section (the new desired behavior)
        if self.base_config.has_section('PROMPTS'):
            prompt_source = self.base_config.get('PROMPTS', 'default', fallback=None)
            if prompt_source:
                return prompt_source

        # 2. Check for 'default_prompt' in [DEFAULT] section (legacy)
        prompt_source = self.base_config.get('DEFAULT', 'default_prompt', fallback=None)
        if prompt_source:
            return prompt_source

        # 3. If nothing is defined, fall back to the literal filename "default.txt"
        return "default.txt"
    
    # Backward compatibility methods that delegate to ConfigHandler-style interface
    def list_models(self, showall: bool = False, *,
                    include_unverified: bool = False) -> Dict[str, Dict[str, Any]]:
        """List configured models and eligible discoveries with the same filters."""
        filtered_models = {}
        default_model = self.normalize_model_name(self.base_config['DEFAULT'].get('default_model'))
        configured = self.models.sections()
        aliases = {self._catalog_alias(section) for section in configured}
        for model in dict.fromkeys([*configured, *self.discovered_models]):
            discovered = not self.models.has_section(model)
            if discovered and model in aliases:
                continue
            data = self._model_options(model)
            explicitly_listed = False
            if not showall:
                provider = data.get('provider')
                if not provider or not self.base_config.getboolean(provider, 'active', fallback=False):
                    continue
                preferred = self.base_config.get(provider, 'models', fallback=None)
                if preferred is not None:
                    names = {name.strip() for name in preferred.split(',')}
                    explicitly_listed = model in names or (discovered and data['model_name'] in names)
                    if not explicitly_listed:
                        continue
            if discovered and data.get('chat_supported') is not True:
                if not (showall or include_unverified or explicitly_listed or model == default_model):
                    continue
                data['chat_compatibility'] = 'unverified'
            if model == default_model:
                data['default'] = True
            filtered_models[model] = data
        return filtered_models

    def list_providers(self, showall: bool = False) -> Dict[str, Dict[str, Any]]:
        """List providers - backward compatibility"""
        filtered_providers = {}
        
        if showall:
            for provider in self.base_config.sections():
                filtered_providers[provider] = {
                    option: ConfigManager.fix_values(self.base_config.get(provider, option))
                    for option in self.base_config.options(provider)
                }
        else:
            for provider in self.base_config.sections():
                if self.base_config.getboolean(provider, 'active', fallback=False):
                    filtered_providers[provider] = {
                        option: ConfigManager.fix_values(self.base_config.get(provider, option))
                        for option in self.base_config.options(provider)
                    }

        return filtered_providers
    
    def list_prompts(self) -> List[str]:
        """List available prompts from prompt directories"""
        prompts = set()
        
        # Get from default prompt directory
        prompt_dir_str = self.get_option('DEFAULT', 'prompt_directory', fallback='prompts')
        if prompt_dir_str:
            prompt_dir = ConfigManager.resolve_directory_path(prompt_dir_str)
            if prompt_dir and os.path.isdir(prompt_dir):
                for f in os.listdir(prompt_dir):
                    if os.path.isfile(os.path.join(prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        # Get from user prompt directory, overriding defaults
        user_prompt_dir_str = self.get_option('DEFAULT', 'user_prompts', fallback=None)
        if user_prompt_dir_str:
            user_prompt_dir = ConfigManager.resolve_directory_path(user_prompt_dir_str)
            if user_prompt_dir and os.path.isdir(user_prompt_dir):
                for f in os.listdir(user_prompt_dir):
                    if os.path.isfile(os.path.join(user_prompt_dir, f)):
                        prompts.add(os.path.splitext(f)[0])

        return sorted(list(prompts)) if prompts else []
    
    # Additional backward compatibility methods that may be needed
    def get_option_from_model(self, option: str, model: str) -> Optional[Any]:
        """Get an option from the model - backward compatibility"""
        model_config = self._get_model_config(model)
        return model_config.get(option)
    
    def get_option_from_provider(self, option: str, provider: str) -> Optional[Any]:
        """Get an option from the provider - backward compatibility"""
        provider_config = self._get_provider_config(provider)
        return provider_config.get(option)
    
    def get_all_options_from_provider(self, provider: str) -> Dict[str, Any]:
        """Get all options from provider - backward compatibility"""
        return self._get_provider_config(provider)
    
    def get_all_options_from_model(self, model: str) -> Dict[str, Any]:
        """Get all options from model - backward compatibility"""
        return self._get_model_config(model)
    
    def get_all_options_from_section(self, section: str) -> Dict[str, Any]:
        """Get all options from section - backward compatibility"""
        if self.base_config.has_section(section):
            return {
                option: ConfigManager.fix_values(self.base_config.get(section, option))
                for option in self.base_config.options(section)
            }
        return {}
    
    @staticmethod
    def resolve_file_path(file_name: str, base_dir: Optional[str] = None, extension: Optional[str] = None) -> Optional[str]:
        """Static method for backward compatibility"""
        return ConfigManager.resolve_file_path(file_name, base_dir, extension)
    
    @staticmethod
    def resolve_directory_path(dir_name: str) -> Optional[str]:
        """Static method for backward compatibility"""
        return ConfigManager.resolve_directory_path(dir_name)
