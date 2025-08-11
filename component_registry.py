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
            print(f"Warning: Unknown context type: {context_type}")
            return None

        context_class = self._context_classes[context_type]
        return context_class

    def get_prompt_resolver(self) -> PromptResolver:
        """Get the prompt resolver"""
        if self._prompt_resolver is None:
            self._prompt_resolver = PromptResolver(self.config)
        return self._prompt_resolver

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

            # Look for a class that ends with 'Action'
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        attr_name.endswith('Action') and
                        attr_name != 'InteractionAction'):
                    # Return the action class without instantiating it yet
                    return attr

            return None

        except Exception as e:
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
