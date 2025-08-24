import os
import importlib.util
from typing import Optional, List
from config_manager import SessionConfig, ConfigManager
from core.prompt_resolver import PromptResolver
from core.provider_factory import ProviderFactory


## PromptResolver moved to core.prompt_resolver


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
            from core.utils import UtilsHandler
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
        """Delegate to core.ProviderFactory to instantiate a provider.

        Preserves the previous semantics (isolated param view by default) and
        records the last error for callers that want to surface details.
        """
        try:
            setattr(self, '_provider_factory_last_error', None)
        except Exception:
            pass
        real_session = getattr(self, 'session', None)
        try:
            prov = ProviderFactory.instantiate_by_name(
                provider_name,
                registry=self,
                session=real_session,
                params_override=params_override or {},
                isolated=isolated,
            )
            return prov
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
