from __future__ import annotations
import os
import importlib.util
from typing import Dict, List, Any, Optional, Literal, TypedDict, cast, Iterable, Tuple
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
        try:
            # Link back so registry can construct providers with this session
            setattr(self._registry, 'session', self)
        except Exception:
            pass
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

            # Generic indicator: providers can expose 'startup_wait_message' to request a spinner/status during init
            msg = getattr(provider_class, 'startup_wait_message', None)
            if msg:
                try:
                    blocking = bool(getattr(self.ui, 'capabilities', None) and self.ui.capabilities.blocking)
                except Exception:
                    blocking = True
                if blocking and not self.in_agent_mode():
                    with self.utils.output.spinner(str(msg)):
                        self.provider = provider_class(self)
                else:
                    # For non-blocking UIs, emit status begin/end only when not in Agent mode
                    if not self.in_agent_mode():
                        try:
                            self.ui.emit('status', {'message': str(msg)})
                        except Exception:
                            pass
                    self.provider = provider_class(self)
                    if not self.in_agent_mode():
                        ready_msg = getattr(provider_class, 'startup_ready_message', None)
                        if ready_msg:
                            try:
                                self.ui.emit('status', {'message': str(ready_msg)})
                            except Exception:
                                pass
            else:
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
            # Log effective settings
            try:
                self.utils.logger.settings({
                    'model': normalized,
                    'provider': new_provider_name,
                    'tool_mode': getattr(self, 'get_effective_tool_mode', lambda: 'none')(),
                })
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
        try:
            self.utils.logger.settings({
                'model': normalized,
                'provider': self.params.get('provider'),
                'tool_mode': getattr(self, 'get_effective_tool_mode', lambda: 'none')(),
            })
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

    # ---- Cancellation helpers -----------------------------------------
    def get_cancellation_token(self):
        """Return the current turn's CancellationToken if present."""
        return self.user_data.get('__turn_cancel__')

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
    def get_effective_tool_mode(self) -> Literal['official', 'pseudo', 'none']:
        """Return effective tool mode: 'official' | 'pseudo' | 'none'.

        Precedence when DEFAULT.enable_tools is true:
        - Hard gate: if model/tools flag is False → 'none'
        1) explicit param override self.params['tool_mode'] when present
        2) model.tool_mode
        3) provider.tool_mode
        4) [TOOLS].tool_mode (default 'official')
        When DEFAULT.enable_tools is false → 'none'.
        """
        # Global gate
        enabled_raw = self.get_option('DEFAULT', 'enable_tools', fallback=True)
        enabled = enabled_raw if isinstance(enabled_raw, bool) else str(enabled_raw).lower() not in ('false', '0', 'no')
        if not enabled:
            return 'none'

        # Model-level boolean gate: if tools flag is explicitly false, disable
        try:
            # Prefer params value which includes model roll-up
            tools_enabled = self.params.get('tools')
            if tools_enabled is None:
                tools_enabled = True
            if isinstance(tools_enabled, str):
                tools_enabled = tools_enabled.strip().lower() not in ('false', '0', 'no')
            if tools_enabled is False:
                return 'none'
        except Exception:
            pass

        # Explicit override in params
        try:
            p_mode = (self.params.get('tool_mode') or '').strip().lower()
            if p_mode in ('official', 'pseudo', 'none'):
                return p_mode
        except Exception:
            pass

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

    def remove_context_type(self, context_type: str) -> None:
        """Remove all contexts of a specific type - backward compatibility"""
        self.clear_context(context_type)

    def remove_context_item(self, context_type: str, index: int) -> None:
        """Remove a specific context item by index - backward compatibility"""
        if context_type in self.context:
            if 0 <= index < len(self.context[context_type]):
                self.context[context_type].pop(index)
                # If list is now empty, remove the context type entirely
                if not self.context[context_type]:
                    del self.context[context_type]

    def handle_exit(self, confirm: bool = True) -> bool:
        """Handle exit functionality - backward compatibility.

        Args:
            confirm: When True, prompt the user before exiting.
        """
        # Optionally prompt the user before exiting
        if confirm:
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

    # ---- Internal run helpers (dev ergonomics) -----------------------
    def run_internal_completion(
        self,
        message: str = '',
        *,
        overrides: Optional[dict] = None,
        contexts: Optional[Iterable[Tuple[str, Any]]] = None,
        capture: Literal['text', 'raw'] = 'text',
    ):
        """Run a one-shot internal completion using the embedded builder.

        Returns core.mode_runner.ModeResult.
        """
        if not hasattr(self, '_builder') or getattr(self, '_builder', None) is None:
            raise RuntimeError('Session builder is not available')
        # Local import to avoid import-time cycles
        from core.mode_runner import run_completion as _run_completion  # type: ignore
        return _run_completion(
            builder=self._builder,
            overrides=overrides,
            contexts=contexts,
            message=message,
            capture=capture,
        )

        
    def run_internal_agent(
        self,
        steps: int,
        *,
        overrides: Optional[dict] = None,
        contexts: Optional[Iterable[Tuple[str, Any]]] = None,
        output: Optional[Literal['final', 'full', 'none']] = None,
        verbose_dump: bool = False,
    ):
        """Run an internal agent loop using the embedded builder.

        Returns core.mode_runner.ModeResult.
        """
        if not hasattr(self, '_builder') or getattr(self, '_builder', None) is None:
            raise RuntimeError('Session builder is not available')
        from core.mode_runner import run_agent as _run_agent  # type: ignore
        return _run_agent(
            builder=self._builder,
            steps=steps,
            overrides=overrides,
            contexts=contexts,
            output=output,
            verbose_dump=verbose_dump,
        )


class SessionBuilder:
    """Use core.session_builder.SessionBuilder; kept for import compatibility."""
    def __init__(self, config_manager):
        from core.session_builder import SessionBuilder as _SB
        self._impl = _SB(config_manager)

    def build(self, mode: Optional[str] = None, **options) -> Session:
        return self._impl.build(mode=mode, **options)

    def rebuild_provider(self, session: Session) -> None:
        return self._impl.rebuild_provider(session)
