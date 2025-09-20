from __future__ import annotations

import os
import importlib.util
from typing import Optional

from component_registry import ComponentRegistry
from config_manager import SessionConfig


class SessionBuilder:
    """
    Builds fully configured sessions.
    Extracted to core for reuse by internal runners.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def build(self, mode: Optional[str] = None, **options):
        # Late import to avoid circular dependency at module load time
        from session import Session

        # Determine effective default model based on interaction style (mode)
        # - Non-interactive runs ('completion', 'internal') prefer [AGENT].default_model when not explicitly set
        # - Interactive runs ('chat', 'tui', 'web') keep using [DEFAULT].default_model
        eff_options = dict(options or {})
        try:
            ui_mode = (mode or 'chat').lower()
        except Exception:
            ui_mode = 'chat'
        if not eff_options.get('model') and ui_mode in ('completion', 'internal'):
            try:
                base_cfg = getattr(self.config_manager, 'base_config', None)
                if base_cfg is not None:
                    agent_default = base_cfg.get('AGENT', 'default_model', fallback=None)
                    if agent_default:
                        eff_options['model'] = agent_default
            except Exception:
                pass

        session_config: SessionConfig = self.config_manager.create_session_config(eff_options)
        registry = ComponentRegistry(session_config)
        session = Session(session_config, registry)
        try:
            setattr(session, '_builder', self)
        except Exception:
            pass

        session.current_model = eff_options.get('model')

        # Initialize UI first so we can present status/spinners during provider startup
        ui_mode = (mode or 'chat').lower()
        try:
            if ui_mode in ('web',):
                from ui.web import WebUI
                session.ui = WebUI(session)
            elif ui_mode in ('tui',):
                from ui.tui import TUIUI
                session.ui = TUIUI(session)
            elif ui_mode in ('internal',):
                # caller will override with a core.NullUI for internal runs
                from ui.cli import CLIUI
                session.ui = CLIUI(session)
            else:
                from ui.cli import CLIUI
                session.ui = CLIUI(session)
        except Exception:
            try:
                from ui.cli import CLIUI  # type: ignore
                session.ui = CLIUI(session)
            except Exception:
                session.ui = None

        # Default visibility policy: in non-blocking UIs (Web/TUI) hide pre-prompt
        # context summaries; actions will emit per-item status/context updates.
        try:
            blocking = bool(getattr(session.ui, 'capabilities', None) and session.ui.capabilities.blocking)
        except Exception:
            blocking = True
        if not blocking:
            try:
                session.set_option('show_context_summary', False)
            except Exception:
                pass

        # Initialize logging early and warn if enabled but not writable
        try:
            log_enabled = bool(session.get_option('LOG', 'active', fallback=False))
        except Exception:
            log_enabled = False
        if log_enabled:
            try:
                # Force logger initialization
                _ = session.utils.logger
                if not session.utils.logger.active():
                    try:
                        session.utils.output.warning("Logging is enabled but the log file could not be opened; check [LOG].dir or permissions.")
                    except Exception:
                        print("Warning: Logging is enabled but the log file could not be opened; check [LOG].dir or permissions.")
            except Exception as e:
                try:
                    session.utils.output.warning(f"Logging is enabled but failed to initialize: {e}")
                except Exception:
                    print(f"Warning: Logging is enabled but failed to initialize: {e}")

        # Provider instantiation (with UX for long startups in chat-like modes)
        model = options.get('model')
        provider_name = session_config.get_params(model).get('provider')
        if provider_name:
            provider_class = registry.load_provider_class(provider_name)
            if provider_class:
                # Let providers signal slow startup by exposing a class attribute
                # 'startup_wait_message' (str). If present, show an indicator while
                # instantiating the provider in chat-like UIs.
                show_loading = ui_mode in ('chat', 'web', 'tui') and bool(getattr(provider_class, 'startup_wait_message', None))
                if show_loading:
                    msg = getattr(provider_class, 'startup_wait_message', 'Loading...')
                    ready_msg = getattr(provider_class, 'startup_ready_message', None)
                    try:
                        # CLI: spinner; Web/TUI: status events
                        blocking = bool(getattr(session.ui, 'capabilities', None) and session.ui.capabilities.blocking)
                    except Exception:
                        blocking = True
                    if blocking:
                        with session.utils.output.spinner(str(msg)):
                            session.provider = provider_class(session)
                    else:
                        try:
                            session.ui.emit('status', {'message': str(msg)})
                        except Exception:
                            pass
                        session.provider = provider_class(session)
                        try:
                            if ready_msg:
                                session.ui.emit('status', {'message': str(ready_msg)})
                        except Exception:
                            pass
                else:
                    session.provider = provider_class(session)

        try:
            if mode != 'completion' or 'prompt' in options:
                prompt_resolver = registry.get_prompt_resolver()
                if prompt_resolver:
                    prompt_name = options.get('prompt', None)
                    prompt_content = prompt_resolver.resolve(prompt_name)
                    if prompt_content:
                        session.add_context('prompt', prompt_content)
        except Exception as e:
            print(f"Warning: Could not load prompt context: {e}")

        # MCP autoload/bootstrap based on [MCP]
        # Only autoload for interactive modes here. Non-interactive (completion/internal)
        # will trigger autoload from their respective runners to apply correct gating.
        try:
            if ui_mode in ('chat', 'web', 'tui'):
                from memex_mcp.bootstrap import autoload_mcp
                autoload_mcp(session)
        except Exception:
            pass

        return session

    def rebuild_provider(self, session) -> None:
        provider_name = session.params.get('provider')
        if not provider_name:
            return

        provider_class = session._registry.load_provider_class(provider_name)
        if not provider_class:
            try:
                session.utils.output.warning(f"Could not load provider '{provider_name}' during rebuild.")
            except Exception:
                pass
            return

        old_usage = None
        if hasattr(session.provider, 'get_usage'):
            old_usage = session.provider.get_usage()

        session.provider = provider_class(session)

        if old_usage and hasattr(session.provider, 'set_usage'):
            session.provider.set_usage(old_usage)
