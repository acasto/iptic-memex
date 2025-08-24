import os
from typing import Optional

from config_manager import SessionConfig, ConfigManager


class PromptResolver:
    """
    Handles prompt resolution from files, chains, and user overrides.

    This is moved from component_registry to a standalone module so both the
    registry and any internal runners can use it directly.
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
        # Handle booleans explicitly: False disables prompt; True uses default
        if isinstance(prompt_source, bool):
            if not prompt_source:
                return None
            prompt_source = self.config.get_default_prompt_source()

        # Normalize non-strings best-effort
        if not isinstance(prompt_source, str):
            try:
                prompt_source = str(prompt_source)
            except Exception:
                prompt_source = ''

        # Check cache first
        if prompt_source in self._cache:
            return self._cache[prompt_source]

        content = None

        # Try to resolve as a chain first (comma-separated list)
        if isinstance(prompt_source, str) and ',' in prompt_source:
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
        if not isinstance(chain, str):
            return None
        prompts = []
        for prompt_name in chain.split(','):
            prompt_name = prompt_name.strip()
            if prompt_name:
                content = self._resolve_single_prompt(prompt_name)
                if content:
                    prompts.append(content)

        return '\n\n'.join(prompts) if prompts else None

    def _resolve_single_prompt(self, prompt_name: str) -> Optional[str]:
        """Resolve a single prompt file or mapping from [PROMPTS]."""
        if not isinstance(prompt_name, str):
            return None
        # First check if it's defined in [PROMPTS] section (user config first, then core)
        prompts_value = None
        try:
            bc = self.config.base_config  # raw base ConfigParser (merged with user)
            if bc.has_option('PROMPTS', prompt_name):
                prompts_value = bc.get('PROMPTS', prompt_name)
        except Exception:
            prompts_value = None
        if prompts_value is not None:
            # If it's a string, it could be a chain or a single prompt mapping
            if isinstance(prompts_value, str):
                if ',' in prompts_value:
                    return self._resolve_chain(prompts_value)
                else:
                    return self._resolve_single_prompt(prompts_value)
            # Handle booleans explicitly: False disables; True -> resolve default
            if isinstance(prompts_value, bool):
                if not prompts_value:
                    return None
                default_src = self.config.get_default_prompt_source()
                return self._resolve_single_prompt(default_src) if isinstance(default_src, str) else None
            # Fallback: treat as literal content
            try:
                return str(prompts_value)
            except Exception:
                return None

        # Then try to find as a file with .txt/.md extension
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
