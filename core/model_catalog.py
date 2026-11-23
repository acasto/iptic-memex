"""Local snapshots of the models reported by configured API endpoints."""

import hashlib
import json
import os
from collections.abc import Iterable
from configparser import ConfigParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote


_OPENAI_ENV = ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID')
_PROVIDER_ENV = {
    'openai': _OPENAI_ENV,
    'openairesponses': _OPENAI_ENV,
    'anthropic': ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'),
    'google': (
        'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GEMINI_BASE_URL', 'GOOGLE_VERTEX_BASE_URL',
        'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_GENAI_USE_ENTERPRISE', 'GOOGLE_CLOUD_PROJECT',
        'GOOGLE_CLOUD_LOCATION', 'GOOGLE_APPLICATION_CREDENTIALS',
    ),
}
CONNECTION_OPTIONS = (
    'alias', 'api_key', 'base_url', 'endpoint', 'organization', 'project', 'location', 'api_version',
    'vertexai', 'enterprise', 'default_headers', 'http_options',
)
_METADATA = {'display_name': str, 'context_size': int, 'output_token_limit': int,
             'vision': bool, 'chat_supported': bool}


def _model_record(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    model = record.get('model_name')
    if (not isinstance(model, str) or not model or not model.isprintable()
            or any(char.isspace() for char in model)):
        return None
    result = {'model_name': model}
    for key, expected in _METADATA.items():
        value = record.get(key)
        if type(value) is not expected:
            continue
        if (expected is int and value <= 0) or (expected is str and not value.isprintable()):
            continue
        result[key] = value
    return result


class ModelCatalog:
    """Cache each supported provider connection independently of the INI files."""

    def __init__(self, config: ConfigParser):
        cache_dir = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache').expanduser()
        self.root = cache_dir / 'iptic-memex' / 'model-catalog-v1'
        self.paths: dict[str, Path] = {}
        for provider in config.sections():
            if not (config.getboolean(provider, 'discover_models', fallback=False)
                    and config.getboolean(provider, 'active', fallback=False)):
                continue
            adapter = config.get(provider, 'alias', fallback=provider).casefold()
            options = [config.get(provider, key, raw=True, fallback=None)
                       for key in CONNECTION_OPTIONS]
            environment = [os.environ.get(key) for key in _PROVIDER_ENV.get(adapter, ())]
            # Partition snapshots across API accounts and endpoints. Persist only the digest.
            digest = hashlib.sha256(json.dumps([options, environment]).encode()).hexdigest()
            self.paths[provider] = self.root / f"{quote(provider, safe='')}-{digest}.json"

    def read(self) -> dict[str, dict]:
        """Load qualified IDs and validated provider metadata from disk."""
        models = {}
        for provider, path in self.paths.items():
            try:
                records = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(records, list):
                continue
            for record in records:
                model = _model_record(record)
                if model is not None:
                    models[f'{provider}:{model["model_name"]}'] = {**model, 'provider': provider}
        return models

    def replace(self, provider: str, records: Iterable[dict]) -> int:
        """Publish a complete provider response atomically and return its model count."""
        path = self.paths[provider]
        models = {}
        for record in records:
            model = _model_record(record)
            if model is not None:
                models[model['model_name']] = model
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as tmp:
            pending = Path(tmp.name)
            try:
                json.dump(list(models.values()), tmp, ensure_ascii=False)
                tmp.close()
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
        return len(models)
