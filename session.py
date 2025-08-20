from __future__ import annotations
import os
import importlib.util
from typing import Dict, List, Any, Optional, Literal, TypedDict, cast
from config_manager import SessionConfig
from component_registry import ComponentRegistry


class Session:
    """
    Central session object that holds state and provides access to all services.
    This is what gets passed to actions and other components.
    """

    def __init__(self, config: SessionConfig, registry: ComponentRegistry):
        self.config = config
        self.context = {}  # Dict of context_type -> [context_objects]
        self.provider = None
        self.flags = {}
        self.usage_stats = {}
        self.user_data = {}  # For arbitrary session data
        self._registry = registry
        self.current_model = None  # Track the current model
        self.ui = None  # UI adapter (CLI/Web/TUI)

    # Convenience methods that delegate to registry
    def get_action(self, name: str):
        """Return an action instance for the given name."""
        action_class = self._registry.get_action_class(name)
        if action_class:
            try:
                return action_class(self)
            except Exception as e:
                try:
                    self.utils.output.warning(f"Could not instantiate action '{name}': {e}")
                except Exception:
                    print(f"Warning: Could not instantiate action '{name}': {e}")
                return None
        return None

    def create_context(self, context_type: str, data=None):
        """Create and return a context instance for the given type."""
        try:
            context_class = self._registry.get_context_class(context_type)
            if context_class:
                # Handle special context types that need additional parameters
                if context_type == 'prompt':
                    prompt_resolver = self._registry.get_prompt_resolver()
                    return context_class(self, data, prompt_resolver)
                else:
                    return context_class(self, data)
            return None
        except Exception as e:
            try:
                self.utils.output.warning(f"Could not create context '{context_type}': {e}")
            except Exception:
                print(f"Warning: Could not create context '{context_type}': {e}")
            return None

    def add_context(self, context_type: str, data=None):
        """Add a context to the session"""
        context = self.create_context(context_type, data)
        if context:
            if context_type not in self.context:
                self.context[context_type] = []
            self.context[context_type].append(context)
            return context
        return None

    def get_context(self, context_type: str):
        """Get contexts of a specific type (backward compatibility)"""
        contexts = self.context.get(context_type, [])
        if contexts:
            return contexts[0]  # Return first context for backward compatibility
        return None

    def get_contexts(self, context_type: str = None):
        """Get contexts - backward compatibility for process_contexts action"""
        if context_type:
            return self.context.get(context_type, [])
        else:
            # Return all contexts in the format expected by process_contexts
            all_contexts = []
            for ctx_type, ctx_list in self.context.items():
                for ctx in ctx_list:
                    all_contexts.append({
                        'type': ctx_type,
                        'context': ctx
                    })
            return all_contexts

    def clear_context(self, context_type: str):
        """Clear all contexts of a specific type"""
        if context_type in self.context:
            del self.context[context_type]

    def clear_contexts(self, context_types: List[str]):
        """Clear multiple context types"""
        for context_type in context_types:
            self.clear_context(context_type)

    # --- Context transactions -----------------------------------------
    class ContextTransaction:
        def __init__(self, session: 'Session') -> None:
            self._session = session
            self._staged_adds: List[Dict[str, Any]] = []
            self._committed = False

        def add_context(self, kind: str, value: Any):
            # Stage by recording the intent; actual creation deferred to commit
            self._staged_adds.append({'type': kind, 'data': value})
            return self

        def commit(self):
            if self._committed:
                return
            for item in self._staged_adds:
                try:
                    self._session.add_context(item['type'], item.get('data'))
                except Exception:
                    # Best-effort; continue adding others
                    pass
            self._committed = True

        def rollback(self):
            # Nothing to do: we never applied staged changes
            self._staged_adds.clear()

    def context_transaction(self) -> 'Session.ContextTransaction':
        return Session.ContextTransaction(self)

    @property
    def utils(self):
        """Access to utility functions"""
        return self._registry.utils

    @property
    def params(self):
        """Current merged parameters for the session"""
        # Always derive from current overrides; do not rely on a cached model
        return self.config.get_params()

    def get_params(self):
        """Backward compatibility method"""
        return self.params

    def get_session_state(self):
        """Get current session state information for display purposes"""
        return {
            'params': self.get_params(),
            'model': self.get_params().get('model'),
            'provider': self.provider.__class__.__name__ if self.provider else None,
            'contexts': list(self.context.keys()),
            'flags': self.flags.copy(),
            'user_data': self.user_data.copy()
        }

    def switch_model(self, model: str) -> bool:
        """
        Switch to a different model, potentially changing providers.
        Preserves conversation state.
        Returns True if provider needs to be recreated.
        """
        # Validate and normalize model to a section/display name
        normalized = self.config.normalize_model_name(model)
        if not normalized:
            try:
                self.utils.output.error(f"Unknown model '{model}'. Use 'show models' or CLI 'list-models'.")
            except Exception:
                print(f"Unknown model '{model}'.")
            return False

        # Compute old/new provider based on params before/after override
        old_provider_name = self.params.get('provider')
        self.config.set_option('model', normalized)
        self.current_model = normalized
        new_provider_name = self.params.get('provider')

        if old_provider_name != new_provider_name:
            # Rebuild provider using registry
            old_usage = None
            if self.provider and hasattr(self.provider, 'get_usage'):
                try:
                    old_usage = self.provider.get_usage()
                except Exception:
                    old_usage = None

            provider_class = self._registry.load_provider_class(new_provider_name)
            if not provider_class:
                try:
                    self.utils.output.error(f"Could not load provider '{new_provider_name}' for model '{normalized}'.")
                except Exception:
                    print(f"Could not load provider '{new_provider_name}'.")
                return False

            self.provider = provider_class(self)

            if old_usage and hasattr(self.provider, 'set_usage'):
                try:
                    self.provider.set_usage(old_usage)
                except Exception:
                    pass

            try:
                self.utils.output.info(f"Switched to {normalized} (provider {new_provider_name}).")
            except Exception:
                pass
            return True

        # Same provider; update its params if supported
        if self.provider and hasattr(self.provider, 'update_params'):
            try:
                self.provider.update_params(self.params)
            except Exception:
                pass
        try:
            self.utils.output.info(f"Switched to {normalized}.")
        except Exception:
            pass
        return False

    def set_flag(self, flag_name: str, value: bool):
        """Set a session flag"""
        self.flags[flag_name] = value

    def get_flag(self, flag_name: str, default: bool = False) -> bool:
        """Get a session flag"""
        return self.flags.get(flag_name, default)

    def set_user_data(self, key: str, value: Any):
        """Set arbitrary user data"""
        self.user_data[key] = value

    def get_user_data(self, key: str, default: Any = None) -> Any:
        """Get arbitrary user data"""
        return self.user_data.get(key, default)

    # Agent mode helpers
    WritePolicy = Literal['deny', 'dry-run', 'allow']

    class _AgentState(TypedDict, total=False):
        enabled: bool
        write_policy: WritePolicy

    _ALLOWED_POLICIES: set[str] = {"deny", "dry-run", "allow"}

    def _normalize_write_policy(self, policy: Optional[str]) -> WritePolicy:
        p = (policy or 'deny').lower()
        return cast(Session.WritePolicy, p if p in self._ALLOWED_POLICIES else 'deny')

    def in_agent_mode(self) -> bool:
        """Return True if this session is running in Agent Mode."""
        agent = cast(Optional[Session._AgentState], self.user_data.get('agent'))
        if isinstance(agent, dict):
            return bool(agent.get('enabled'))
        # Backward compatibility with older flags
        return bool(self.user_data.get('agent_mode'))

    def get_agent_write_policy(self) -> Optional[WritePolicy]:
        """Return current agent write policy when in Agent Mode, else None."""
        if not self.in_agent_mode():
            return None
        agent = cast(Optional[Session._AgentState], self.user_data.get('agent'))
        if isinstance(agent, dict) and 'write_policy' in agent:
            return self._normalize_write_policy(cast(str, agent.get('write_policy')))
        # Backward compatibility
        return self._normalize_write_policy(self.user_data.get('agent_write_policy'))

    def enter_agent_mode(self, writes_policy: WritePolicy | str = "deny") -> None:
        """Enable Agent Mode and set the write policy with validation/clamp."""
        normalized = self._normalize_write_policy(str(writes_policy) if writes_policy is not None else 'deny')
        agent_state: Session._AgentState = {'enabled': True, 'write_policy': normalized}
        self.user_data['agent'] = agent_state
        # Backward compatibility flags are no longer required, but keep them cleared to avoid drift
        self.user_data.pop('agent_mode', None)
        self.user_data.pop('agent_write_policy', None)

    def exit_agent_mode(self) -> None:
        """Disable Agent Mode and clear related settings (both new and legacy)."""
        # Namespaced state
        if 'agent' in self.user_data:
            self.user_data.pop('agent', None)
        # Legacy flags
        self.user_data.pop('agent_mode', None)
        self.user_data.pop('agent_write_policy', None)

    # Backward compatibility methods for existing code
    def list_models(self, showall: bool = False):
        """List models - backward compatibility"""
        return self.config.list_models(showall)

    def list_providers(self, showall: bool = False):
        """List providers - backward compatibility"""
        return self.config.list_providers(showall)

    def get_option(self, section: str, option: str, fallback: Any = None):
        """Get config option - backward compatibility"""
        return self.config.get_option(section, option, fallback)

    def valid_model(self, model: str) -> bool:
        """Check if model is valid - backward compatibility"""
        return self.config.valid_model(model)

    def normalize_model_name(self, model: str) -> Optional[str]:
        """Normalize model name - backward compatibility"""
        return self.config.normalize_model_name(model)

    def get_option_from_model(self, option: str, model: str = None) -> Optional[Any]:
        """Get an option from the model - backward compatibility"""
        if model is None:
            model = self.params.get('model')
        return self.config.get_option_from_model(option, model)

    def get_option_from_provider(self, option: str, provider: str = None) -> Optional[Any]:
        """Get an option from the provider - backward compatibility"""
        if provider is None:
            provider = self.params.get('provider')
        return self.config.get_option_from_provider(option, provider)

    def get_all_options_from_model(self, model: str = None) -> Dict[str, Any]:
        """Get all options from model - backward compatibility"""
        if model is None:
            model = self.params.get('model')
        return self.config.get_all_options_from_model(model)

    def get_all_options_from_provider(self, provider: str = None) -> Dict[str, Any]:
        """Get all options from provider - backward compatibility"""
        if provider is None:
            provider = self.params.get('provider')
        return self.config.get_all_options_from_provider(provider)

    def get_all_options_from_section(self, section: str) -> Dict[str, Any]:
        """Get all options from section - backward compatibility"""
        return self.config.get_all_options_from_section(section)

    def get_provider(self):
        """Get the current provider - backward compatibility"""
        return self.provider

    def get_tools(self):
        """Get tool settings from configuration"""
        # Get tool settings from the TOOLS section of config
        tools = {}
        try:
            # Use get_all_options_from_section to get merged config (base + user)
            tools = self.config.get_all_options_from_section('TOOLS')

            # Convert string booleans to actual booleans
            for key, value in tools.items():
                if isinstance(value, str) and value.lower() in ['true', 'false']:
                    tools[key] = value.lower() == 'true'
        except Exception:
            pass
        return tools

    # ---- Tools mode helpers -----------------------------------------
    def get_effective_tool_mode(self) -> str:
        """Return effective tool mode: 'official' | 'pseudo' | 'none'.

        Precedence when DEFAULT.enable_tools is true:
        1) model.tool_mode
        2) provider.tool_mode
        3) [TOOLS].tool_mode (default 'official')
        When DEFAULT.enable_tools is false → 'none'.
        """
        # Global gate
        enabled_raw = self.get_option('DEFAULT', 'enable_tools', fallback=True)
        enabled = enabled_raw if isinstance(enabled_raw, bool) else str(enabled_raw).lower() not in ('false', '0', 'no')
        if not enabled:
            return 'none'

        # Model override
        try:
            model = self.params.get('model')
            if model:
                model_mode = self.get_option_from_model('tool_mode', model)
                if model_mode:
                    m = str(model_mode).strip().lower()
                    if m in ('official', 'pseudo', 'none'):
                        return m
        except Exception:
            pass

        # Provider override
        try:
            provider_name = self.params.get('provider')
            if provider_name:
                prov_mode = self.get_option_from_provider('tool_mode', provider_name)
                if prov_mode:
                    p = str(prov_mode).strip().lower()
                    if p in ('official', 'pseudo', 'none'):
                        return p
        except Exception:
            pass

        # Global tools mode default
        try:
            base_mode = self.get_option('TOOLS', 'tool_mode', fallback='official')
            b = base_mode if isinstance(base_mode, str) else str(base_mode)
            b = b.strip().lower()
            if b in ('official', 'pseudo'):
                return b
        except Exception:
            pass
        return 'official'

    def remove_context_type(self, context_type: str):
        """Remove all contexts of a specific type - backward compatibility"""
        self.clear_context(context_type)

    def remove_context_item(self, context_type: str, index: int):
        """Remove a specific context item by index - backward compatibility"""
        if context_type in self.context:
            if 0 <= index < len(self.context[context_type]):
                self.context[context_type].pop(index)
                # If list is now empty, remove the context type entirely
                if not self.context[context_type]:
                    del self.context[context_type]

    def handle_exit(self):
        """Handle exit functionality - backward compatibility"""
        # This would typically handle cleanup and user confirmation
        # For now, return True to indicate exit should proceed
        try:
            response = self.utils.input.get_input(
                self.utils.output.style_text("Hit Ctrl-C or enter 'y' to quit: ", "red")
            )
            if response.lower() != 'y':
                return False
        except (KeyboardInterrupt, EOFError):
            self.utils.output.write()
            return True

        # Run cleanup tasks
        if self.provider and hasattr(self.provider, 'cleanup'):
            try:
                self.provider.cleanup()
            except Exception as e:
                self.utils.output.error(f"Error during provider cleanup: {e}")

        # Persist stats if available
        try:
            persist_action = self.get_action('persist_stats')
            if persist_action:
                persist_action.run()
        except Exception:
            pass

        return True

    @property
    def user_options(self):
        """User options property for backward compatibility"""
        return self.config.overrides

    def set_option(self, key: str, value: str, mode: str = 'params'):
        """Set a session option - backward compatibility"""
        if mode == 'params':
            if key == 'model':
                # Route model changes through switch_model for validation and rebuild
                self.switch_model(value)
            else:
                self.config.set_option(key, value)
        elif mode == 'tools':
            # For tools, we'd need to handle this differently
            # For now, just store in session data
            if 'tools' not in self.user_data:
                self.user_data['tools'] = {}
            self.user_data['tools'][key] = value


class SessionBuilder:
    """
    Builds fully configured sessions.
    Handles initialization logic currently in Session.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def build(self, mode: Optional[str] = None, **options) -> Session:
        """
        Build a new session with the given options.
        """
        # Create session config
        session_config = self.config_manager.create_session_config(options)

        # Create registry
        registry = ComponentRegistry(session_config)

        # Create session
        session = Session(session_config, registry)

        # Set the current model
        session.current_model = options.get('model')

        # Initialize provider
        model = options.get('model')  # Get the model from options
        provider_name = session_config.get_params(model).get('provider')
        if provider_name:
            provider_class = registry.load_provider_class(provider_name)
            if provider_class:
                session.provider = provider_class(session)

        # Attach UI adapter according to mode (default to CLI)
        ui_mode = (mode or 'chat').lower()
        try:
            if ui_mode in ('web',):
                from ui.web import WebUI
                session.ui = WebUI(session)
            elif ui_mode in ('tui',):
                from ui.tui import TUIUI
                session.ui = TUIUI(session)
            else:
                from ui.cli import CLIUI
                session.ui = CLIUI(session)
        except Exception:
            # Fallback to a minimal CLI UI if adapter import fails
            try:
                from ui.cli import CLIUI  # type: ignore
                session.ui = CLIUI(session)
            except Exception:
                session.ui = None

        # Initialize default contexts based on mode
        try:
            if mode != 'completion' or 'prompt' in options:
                prompt_resolver = registry.get_prompt_resolver()
                if prompt_resolver:
                    # Get prompt from options or use default
                    prompt_name = options.get('prompt', None)
                    prompt_content = prompt_resolver.resolve(prompt_name)
                    if prompt_content:
                        session.add_context('prompt', prompt_content)
        except Exception as e:
            print(f"Warning: Could not load prompt context: {e}")

        return session

    def rebuild_provider(self, session: Session) -> None:
        """Rebuild provider after model switch"""
        provider_name = session.params.get('provider')
        if not provider_name:
            return

        provider_class = self._load_provider_class(provider_name)
        if not provider_class:
            try:
                session.utils.output.warning(f"Could not load provider '{provider_name}' during rebuild.")
            except Exception:
                pass
            return

        # Preserve any provider-specific state if needed
        old_usage = None
        if hasattr(session.provider, 'get_usage'):
            old_usage = session.provider.get_usage()

        # Create new provider
        session.provider = provider_class(session)

        # Restore state if applicable
        if old_usage and hasattr(session.provider, 'set_usage'):
            session.provider.set_usage(old_usage)

    def _load_provider_class(self, provider_name: str):
        """Load a provider class dynamically, handling aliases"""
        try:
            # Check if this provider has an alias
            provider_config = {}
            if self.config_manager.base_config.has_section(provider_name):
                provider_config = {
                    option: self.config_manager.base_config.get(provider_name, option)
                    for option in self.config_manager.base_config.options(provider_name)
                }

            # If there's an alias, use the aliased provider
            actual_provider = provider_config.get('alias', provider_name)

            # Convert provider name to module name (e.g., 'OpenAI' -> 'openai_provider')
            module_name = f"{actual_provider.lower()}_provider"

            # Try to import from providers package
            module_path = os.path.join(os.path.dirname(__file__), 'providers', f"{module_name}.py")

            if not os.path.isfile(module_path):
                print(f"Warning: Provider module {module_path} not found")
                return None

            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Look for a class that matches the actual provider name
            class_name = f"{actual_provider}Provider"
            if hasattr(module, class_name):
                return getattr(module, class_name)

            # Fallback: look for any class ending with 'Provider'
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        attr_name.endswith('Provider') and
                        attr_name != 'APIProvider'):
                    return attr

            print(f"Warning: No provider class found in {module_name}")
            return None

        except Exception as e:
            print(f"Warning: Could not load provider {provider_name}: {e}")
            return None
