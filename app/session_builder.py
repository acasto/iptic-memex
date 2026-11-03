from __future__ import annotations

import os
import importlib.util
from typing import Optional

from component_registry import ComponentRegistry
from config_manager import SessionConfig


class SessionBuilder:
    """
    Resolve application profiles and assemble sessions.

    Headless profiles share event-based assembly. Interactive profiles retain
    their frontend startup services outside the execution runtime.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def build(self, mode: Optional[str] = None, *, services=None, **options):
        if mode == 'completion':
            from app.completion import build_completion_session
            return build_completion_session(self.config_manager, options, builder=self,
                                            services=services)
        if mode in ('agent', 'internal'):
            from app.agent import build_agent_session
            return build_agent_session(self.config_manager, options, builder=self,
                                       default_prompt=mode == 'internal', services=services)
        if services is not None:
            raise ValueError('Headless services require completion, agent, or internal mode')
        # Late import to avoid circular dependency at module load time
        from session import Session

        from app.project import project_options, bind_project
        project, eff_options, sources = project_options(options, interactive=True)
        options = eff_options
        try:
            ui_mode = (mode or 'chat').lower()
        except Exception:
            ui_mode = 'chat'
        interactive = ui_mode in ('chat', 'web', 'tui')

        session_config: SessionConfig = self.config_manager.create_session_config(eff_options)
        registry = ComponentRegistry(session_config)
        session = Session(session_config, registry)
        bind_project(session, project, sources)
        try:
            setattr(session, '_builder', self)
        except Exception:
            pass

        session.current_model = eff_options.get('model')
        if ui_mode == 'chat':
            from app.runtime_services import install_tool_selection
            install_tool_selection(session, 'TOOLS')

        # Initialize UI first so we can present status/spinners during provider startup
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
            try:
                from ui.cli import CLIUI  # type: ignore
                session.ui = CLIUI(session)
            except Exception:
                session.ui = None

        # Bind session-level logging context early (even when LOG.active is false).
        try:
            session.utils.logger.update_base_context(
                {
                    "session_uid": getattr(session, "session_uid", None),
                    "ui_mode": ui_mode,
                }
            )
        except Exception:
            pass

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
        params_for_model = session_config.get_params(model)
        provider_name = params_for_model.get('provider')
        if provider_name:
            try:
                active = bool(session_config.base_config.getboolean(provider_name, 'active', fallback=False))
            except Exception:
                active = False
            if not active:
                effective_model = params_for_model.get('model') or model or provider_name
                raise RuntimeError(
                    f"Provider '{provider_name}' is not active for model '{effective_model}'. "
                    "Enable it in config.ini."
                )
            provider_class = registry.load_provider_class(provider_name)
            if provider_class:
                # Let providers signal slow startup by exposing a class attribute
                # 'startup_wait_message' (str). If present, show an indicator while
                # instantiating the provider in chat-like UIs.
                show_loading = interactive and bool(getattr(provider_class, 'startup_wait_message', None))
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

        from core.memory_files import prepare_memory
        prepare_memory(session, interactive=interactive)

        try:
            if ui_mode != 'agent' or 'prompt' in options:
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
            if interactive:
                from memex_mcp.bootstrap import autoload_mcp
                autoload_mcp(session)
        except Exception:
            pass

        # Optional session autosave for interactive modes
        try:
            if interactive:
                autosave = session.get_option('SESSIONS', 'session_autosave', fallback=False)
                if isinstance(autosave, str):
                    autosave = autosave.strip().lower() in ('true', '1', 'yes', 'on')
                if autosave:
                    from core.session_persistence import save_session, prune_sessions

                    def _has_user_turn() -> bool:
                        try:
                            chat = session.get_context('chat')
                            turns = chat.get('all') if chat else []
                        except Exception:
                            turns = []
                        for turn in turns or []:
                            if not isinstance(turn, dict):
                                continue
                            if turn.get('role') != 'user':
                                continue
                            msg = turn.get('message')
                            if isinstance(msg, str) and msg.strip():
                                return True
                            ctx = turn.get('context')
                            if ctx:
                                return True
                        return False

                    def _autosave():
                        try:
                            if not _has_user_turn():
                                return
                            save_session(session, kind='session')
                            prune_sessions(session, kind='session')
                        except Exception:
                            pass

                    session.register_cleanup_callback(_autosave)
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
