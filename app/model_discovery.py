"""Refresh model catalogs through the configured provider SDKs."""

from component_registry import ComponentRegistry
from config_manager import ConfigManager
from core.provider_factory import ProviderFactory


def refresh_model_catalogs(config_manager: ConfigManager) -> dict[str, str | int]:
    """Return a model count or a refresh error for each enabled catalog source."""
    registry = ComponentRegistry(config_manager.create_session_config())
    results = {}
    for name in config_manager.model_catalog.paths:
        try:
            provider = ProviderFactory.instantiate_by_name(
                name, registry=registry, required_method='list_models',
                params_override={'timeout': 10, 'max_retries': 0})
            try:
                records = list(provider.list_models())
            finally:
                cleanup = getattr(provider, 'cleanup', None)
                if callable(cleanup):
                    cleanup()
            results[name] = config_manager.model_catalog.replace(name, records)
        except NotImplementedError:
            results[name] = 'provider adapter has no model discovery'
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            # SDK error bodies can contain account details or credentials in request URLs.
            results[name] = f'HTTP {status}' if isinstance(status, int) else type(exc).__name__
    config_manager.discovered_models = config_manager.model_catalog.read()
    return results
