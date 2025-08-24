from __future__ import annotations

import os
from typing import Optional, Any, Dict

from config_manager import ConfigManager


class ProviderFactory:
    """
    Centralized provider construction helpers.

    - instantiate_by_name: construct a provider class by name with an optional
      isolated param view (DEFAULT + [Provider] + overrides).
    - get_for_capability: choose a provider for a capability (e.g., 'embedding')
      based on config and fallbacks.
    """

    @staticmethod
    def instantiate_by_name(
        provider_name: str,
        *,
        registry,
        session=None,
        params_override: Optional[dict] = None,
        isolated: bool = True,
    ):
        cls = registry.load_provider_class(provider_name)
        if not cls:
            raise RuntimeError(f"Provider '{provider_name}' not found")

        # Build a lightweight session view for the provider instance
        real_session = session
        if real_session is None:
            # Fallback stub carrying config + registry
            class _Stub:
                pass

            real_session = _Stub()
            setattr(real_session, 'config', registry.config)
            setattr(real_session, '_registry', registry)

        if not isolated:
            return cls(real_session)

        params_override = params_override or {}
        base_cfg = registry.config.base_config

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
                params: Dict[str, Any] = {}
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
        return cls(view)

    @staticmethod
    def get_for_capability(
        capability: str,
        *,
        session,
        candidate_names: Optional[list[str]] = None,
        strict: bool = True,
        overrides: Optional[dict] = None,
    ):
        """
        Choose and instantiate a provider for a capability.

        Example (embedding): fills candidate_names from [TOOLS] and fallbacks,
        honoring strict vs permissive preference.
        """
        registry = getattr(session, '_registry', None)
        if registry is None:
            raise RuntimeError('Session is missing registry')

        if capability.lower() == 'embedding':
            tools = session.get_tools()
            embedding_model = tools.get('embedding_model') or ''
            explicit = (tools.get('embedding_provider') or '').strip()

            names: list[str] = []
            if candidate_names:
                names.extend([n for n in candidate_names if n])
            elif explicit:
                names.append(explicit)
            else:
                # Hint: local embedding model paths (.gguf) prefer LlamaCpp
                try:
                    p = os.path.expanduser(str(embedding_model))
                    if p and (p.lower().endswith('.gguf') or os.path.exists(p)):
                        names.append('LlamaCpp')
                except Exception:
                    pass

            if not strict:
                # Consider current chat provider, then any active provider section
                try:
                    current = session.get_params().get('provider')
                    if current:
                        names.append(str(current))
                except Exception:
                    pass
                try:
                    for sec in registry.config.base_config.sections():
                        if sec not in names:
                            names.append(sec)
                except Exception:
                    pass

            # Try to instantiate in order; require embed() capability
            for name in names:
                try:
                    prov = ProviderFactory.instantiate_by_name(
                        name,
                        registry=registry,
                        session=session,
                        params_override=(overrides or {}),
                        isolated=True,
                    )
                    if prov and hasattr(prov, 'embed'):
                        return prov
                except Exception:
                    continue
            return None

        raise NotImplementedError(f"Capability not supported: {capability}")

