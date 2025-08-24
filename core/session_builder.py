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

        session_config: SessionConfig = self.config_manager.create_session_config(options)
        registry = ComponentRegistry(session_config)
        session = Session(session_config, registry)
        try:
            setattr(session, '_builder', self)
        except Exception:
            pass

        session.current_model = options.get('model')

        model = options.get('model')
        provider_name = session_config.get_params(model).get('provider')
        if provider_name:
            provider_class = registry.load_provider_class(provider_name)
            if provider_class:
                session.provider = provider_class(session)

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

