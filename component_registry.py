import os
import importlib.util
from typing import Optional, List
from config_manager import SessionConfig, ConfigManager


class PromptResolver:
    """
    Handles prompt resolution from files and directories.
    """

    def __init__(self, config: SessionConfig):
        self.config = config
        self._cache = {}

    def resolve(self, prompt_source: Optional[str] = None) -> Optional[str]:
        """
        Resolve a prompt from various sources:
        - Filename (with or without extension)
        - Chain name from config
        - Default prompt if none specified
        """
        if prompt_source is None:
            prompt_source = self.config.get_default_prompt_source()

        # Check cache first
        if prompt_source in self._cache:
            return self._cache[prompt_source]

        content = None

        # Try to resolve as a chain first (comma-separated list)
        if ',' in prompt_source:
            content = self._resolve_chain(prompt_source)
        else:
            # Try to resolve as a single prompt file
            content = self._resolve_single_prompt(prompt_source)

        # Cache the result
        if content:
            self._cache[prompt_source] = content

        return content

    def _resolve_chain(self, chain: str) -> Optional[str]:
        """Resolve a comma-separated chain of prompts"""
        prompts = []
        for prompt_name in chain.split(','):
            prompt_name = prompt_name.strip()
            if prompt_name:
                content = self._resolve_single_prompt(prompt_name)
                if content:
                    prompts.append(content)

        return '\n\n'.join(prompts) if prompts else None

    def _resolve_single_prompt(self, prompt_name: str) -> Optional[str]:
        """Resolve a single prompt file"""
        # First check if it's defined in [PROMPTS] section (user config first, then core)
        prompts_value = self.config.get_option('PROMPTS', prompt_name, fallback=None)
        if prompts_value:
            # If it's another chain, resolve recursively
            if ',' in prompts_value:
                return self._resolve_chain(prompts_value)
            else:
                return self._resolve_single_prompt(prompts_value)

        # Then try to find as a file with .txt extension
        # Try user prompts directory first
        user_prompt_dir = self.config.get_option('DEFAULT', 'user_prompts', fallback=None)
        if user_prompt_dir:
            user_dir = ConfigManager.resolve_directory_path(user_prompt_dir)
            if user_dir:
                content = self._load_prompt_from_directory(prompt_name, user_dir)
                if content:
                    return content

        # Try default prompts directory
        prompt_dir = self.config.get_option('DEFAULT', 'prompt_directory', fallback='prompts')
        if prompt_dir:
            default_dir = ConfigManager.resolve_directory_path(prompt_dir)
            if default_dir:
                content = self._load_prompt_from_directory(prompt_name, default_dir)
                if content:
                    return content

        # Finally, treat as literal string if no file found
        return prompt_name

    @staticmethod
    def _load_prompt_from_directory(prompt_name: str, directory: str) -> Optional[str]:
        """Load a prompt file from a specific directory"""
        # Try with common extensions
        extensions = ['', '.txt', '.md']

        for ext in extensions:
            file_path = os.path.join(directory, prompt_name + ext)
            if os.path.isfile(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return f.read().strip()
                except (IOError, UnicodeDecodeError) as e:
                    # PromptResolver does not have access to utils/output handler
                    print(f"Warning: Could not read prompt file {file_path}: {e}")
                    continue

        return None


class ComponentRegistry:
    """
    Central registry for dynamic components and utilities.
    - Returns action and context classes (not instances).
    - Session is responsible for instantiation and wiring.
    """

    def __init__(self, config: SessionConfig):
        self.config = config
        self._action_cache = {}
        self._context_classes = {}
        self._utils = None
        self._prompt_resolver = None
        self._load_context_classes()

    @property
    def utils(self):
        """Lazy load and cache utils"""
        if self._utils is None:
            from utils_handler import UtilsHandler
            self._utils = UtilsHandler(self.config)
        return self._utils

    def get_action_class(self, name: str) -> Optional[type]:
        """Return the action class for the given name."""
        if name not in self._action_cache:
            self._action_cache[name] = self._load_action(name)
        return self._action_cache[name]

    def get_context_class(self, context_type: str) -> Optional[type]:
        """Return the context class for the given type."""
        if context_type not in self._context_classes:
            # Try to load the context class
            self._load_context_class(context_type)

        if context_type not in self._context_classes:
            try:
                self.utils.output.warning(f"Unknown context type: {context_type}")
            except Exception:
                print(f"Warning: Unknown context type: {context_type}")
            return None

        context_class = self._context_classes[context_type]
        return context_class

    def get_prompt_resolver(self) -> PromptResolver:
        """Get the prompt resolver"""
        if self._prompt_resolver is None:
            self._prompt_resolver = PromptResolver(self.config)
        return self._prompt_resolver

    # --- Provider factory -----------------------------------------------
    def create_provider(self, provider_name: str, *, params_override: Optional[dict] = None,
                        isolated: bool = True):
        """Instantiate a provider by name with an optional isolated param view.

        - When isolated=True (default), the provider sees params composed from
          [DEFAULT] + [<Provider>] + params_override only, preventing bleed from
          the active chat provider.
        - Delegates all other attributes/methods to the real session at call time.
        """
        # Reset last factory error before attempting
        try:
            setattr(self, '_provider_factory_last_error', None)
        except Exception:
            pass

        cls = self.load_provider_class(provider_name)
        if not cls:
            try:
                self.utils.output.warning(f"Provider '{provider_name}' not found")
            except Exception:
                print(f"Warning: Provider '{provider_name}' not found")
            return None

        # Build a lightweight session view for the provider instance
        real_session = getattr(self, 'session', None)
        # Some call-sites may not set registry.session; try to infer from config
        if real_session is None:
            # Fallback: provider may not need the session beyond params; create a stub
            class _Stub:
                pass
            real_session = _Stub()
            setattr(real_session, 'config', self.config)
            setattr(real_session, '_registry', self)

        if not isolated:
            try:
                return cls(real_session)
            except Exception as e:
                try:
                    self._provider_factory_last_error = {'name': provider_name, 'error': str(e)}
                except Exception:
                    pass
                try:
                    self.utils.output.warning(f"Could not instantiate provider '{provider_name}': {e}")
                except Exception:
                    print(f"Warning: Could not instantiate provider '{provider_name}': {e}")
                return None

        params_override = params_override or {}
        base_cfg = self.config.base_config

        class ProviderParamView:
            def __init__(self, session_obj, provider: str, overrides: dict):
                self._s = session_obj
                self._provider = provider
                self._over = dict(overrides or {})

            def __getattr__(self, item):
                if item == 'get_params':
                    return object.__getattribute__(self, 'get_params')
                return getattr(self._s, item)

            def get_params(self):
                # Compose params from DEFAULT + provider section + overrides
                params = {}
                try:
                    # DEFAULTs
                    for k, v in base_cfg['DEFAULT'].items():
                        params[k] = ConfigManager.fix_values(v)
                    # Provider section
                    if base_cfg.has_section(self._provider):
                        for opt in base_cfg.options(self._provider):
                            try:
                                params[opt] = ConfigManager.fix_values(base_cfg.get(self._provider, opt))
                            except Exception:
                                continue
                    # Overrides
                    params.update(self._over)
                    # Identify as this provider
                    params['provider'] = self._provider
                except Exception:
                    pass
                return params

        view = ProviderParamView(real_session, provider_name, params_override)
        try:
            instance = cls(view)
        except Exception as e:
            try:
                self._provider_factory_last_error = {'name': provider_name, 'error': str(e)}
            except Exception:
                pass
            try:
                self.utils.output.warning(f"Could not instantiate provider '{provider_name}': {e}")
            except Exception:
                print(f"Warning: Could not instantiate provider '{provider_name}': {e}")
            return None
        return instance

    def load_provider_class(self, provider_name: str):
        """Load a provider class dynamically, handling aliases via config.

        Looks for providers/<lower>_provider.py and a class named
        '<ProviderName>Provider', falling back to any class ending with 'Provider'.
        """
        try:
            # Resolve alias from base config if present
            provider_config = {}
            if self.config.base_config.has_section(provider_name):
                provider_config = {
                    option: self.config.base_config.get(provider_name, option)
                    for option in self.config.base_config.options(provider_name)
                }

            actual_provider = provider_config.get('alias', provider_name)
            module_name = f"{actual_provider.lower()}_provider"

            module_path = os.path.join(os.path.dirname(__file__), 'providers', f"{module_name}.py")
            if not os.path.isfile(module_path):
                try:
                    self.utils.output.warning(f"Provider module {module_path} not found")
                except Exception:
                    print(f"Warning: Provider module {module_path} not found")
                return None

            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            class_name = f"{actual_provider}Provider"
            if hasattr(module, class_name):
                return getattr(module, class_name)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type)
                        and attr_name.endswith('Provider')
                        and attr_name != 'APIProvider'):
                    return attr

            try:
                self.utils.output.warning(f"No provider class found in {module_name}")
            except Exception:
                print(f"Warning: No provider class found in {module_name}")
            return None
        except Exception as e:
            try:
                self.utils.output.warning(f"Could not load provider {provider_name}: {e}")
            except Exception:
                print(f"Warning: Could not load provider {provider_name}: {e}")
            return None

    def _load_context_classes(self):
        """Load all available context classes"""
        contexts_dir = os.path.join(os.path.dirname(__file__), 'contexts')
        if not os.path.isdir(contexts_dir):
            return

        for filename in os.listdir(contexts_dir):
            if filename.endswith('_context.py') and filename != '__init__.py':
                context_type = filename[:-11]  # Remove '_context.py'
                self._load_context_class(context_type)

    def _load_context_class(self, context_type: str):
        """Load a specific context class"""
        try:
            module_name = f"{context_type}_context"
            module_path = os.path.join(os.path.dirname(__file__), 'contexts', f"{module_name}.py")

            if not os.path.isfile(module_path):
                return

            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Look for a class that ends with 'Context'
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        attr_name.endswith('Context') and
                        attr_name != 'InteractionContext'):
                    self._context_classes[context_type] = attr
                    break

        except Exception as e:
            try:
                self.utils.output.warning(f"Could not load context {context_type}: {e}")
            except Exception:
                print(f"Warning: Could not load context {context_type}: {e}")

    def _load_action(self, name: str):
        """Dynamic action loading logic (similar to current Session)"""
        # Check user actions directory first
        user_actions_dir = self.config.get_option('DEFAULT', 'user_actions', fallback=None)
        if user_actions_dir:
            user_dir = ConfigManager.resolve_directory_path(user_actions_dir)
            if user_dir:
                action = self._try_load_from_directory(name, user_dir)
                if action:
                    return action

        # Fall back to project actions
        actions_dir = os.path.join(os.path.dirname(__file__), 'actions')
        action = self._try_load_from_directory(name, actions_dir)
        if action:
            return action

        try:
            self.utils.output.warning(f"Action '{name}' not found")
        except Exception:
            print(f"Warning: Action '{name}' not found")
        return None

    @staticmethod
    def _try_load_from_directory(name: str, directory: str):
        """Try to load an action from a specific directory"""
        try:
            # Convert action name to filename (e.g., 'load_file' -> 'load_file_action.py')
            filename = f"{name}_action.py"
            file_path = os.path.join(directory, filename)

            if not os.path.isfile(file_path):
                return None

            # Load the module
            spec = importlib.util.spec_from_file_location(f"{name}_action", file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Prefer a class that matches the expected PascalCase name: '<SnakeToPascal>Action'
            def snake_to_pascal(s: str) -> str:
                return ''.join(part.capitalize() for part in s.split('_'))

            expected_class = f"{snake_to_pascal(name)}Action"
            if hasattr(module, expected_class):
                cls = getattr(module, expected_class)
                if isinstance(cls, type):
                    return cls

            # Fallback: pick the first 'Action' class defined in THIS module (avoid imported classes)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and attr_name.endswith('Action')
                    and attr_name != 'InteractionAction'
                    and getattr(attr, '__module__', None) == module.__name__
                ):
                    return attr

            return None

        except Exception as e:
            try:
                self.utils.output.warning(f"Could not load action {name} from {directory}: {e}")
            except Exception:
                print(f"Warning: Could not load action {name} from {directory}: {e}")
            return None

    def list_available_actions(self) -> List[str]:
        """List all available actions"""
        actions = set()

        # Check project actions directory
        actions_dir = os.path.join(os.path.dirname(__file__), 'actions')
        if os.path.isdir(actions_dir):
            for filename in os.listdir(actions_dir):
                if filename.endswith('_action.py') and filename != '__init__.py':
                    action_name = filename[:-10]  # Remove '_action.py'
                    actions.add(action_name)

        # Check user actions directory
        user_actions_dir = self.config.get_option('DEFAULT', 'user_actions', fallback=None)
        if user_actions_dir:
            user_dir = ConfigManager.resolve_directory_path(user_actions_dir)
            if user_dir and os.path.isdir(user_dir):
                for filename in os.listdir(user_dir):
                    if filename.endswith('_action.py'):
                        action_name = filename[:-10]  # Remove '_action.py'
                        actions.add(action_name)

        return sorted(list(actions))

    def list_available_contexts(self) -> List[str]:
        """List all available context types"""
        return sorted(list(self._context_classes.keys()))
