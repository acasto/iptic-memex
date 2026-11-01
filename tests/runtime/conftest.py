"""Real session assembly with an offline provider shared by runtime tests."""

from configparser import ConfigParser
import pytest
from app.session_builder import SessionBuilder
from component_registry import ComponentRegistry
from config_manager import ConfigManager


class Provider:
    def __init__(self, session):
        self.session = session
        self.text = 'A useful answer'
        self.chunks = ['A useful ', 'answer']
        self.calls = 0
        self.reason = 'stop'
        self.failure = None
        self.closed = False

    def chat(self):
        self.calls += 1
        if self.failure:
            raise self.failure
        self.session.utils.output.warning('Provider diagnostic')
        return self.text

    def stream_chat(self):
        self.calls += 1
        try:
            yield from self.chunks
            if self.failure:
                raise self.failure
        finally:
            self.closed = True

    def get_finish_reason(self):
        return self.reason

    def get_full_response(self):
        return {'native': 'opaque provider payload'}

    def get_assistant_metadata(self):
        return {'native_continuation': {'opaque': 'provider-owned'}}

    def get_usage(self):
        return {'input_tokens': 5, 'output_tokens': 3}

    def get_cost(self):
        return None


@pytest.fixture
def builder(monkeypatch):
    config = ConfigManager.__new__(ConfigManager)
    config.base_config = ConfigParser()
    config.base_config.read_dict({
        'DEFAULT': {'default_model': 'chat-model', 'template_handler': 'none'},
        'AGENT': {'default_model': 'worker-model', 'large_input_limit': '10000'},
        'Fake': {'active': 'true'},
    })
    config.models = ConfigParser()
    config.models.read_dict({name: {'provider': 'Fake', 'model_name': name, 'stream': 'true'}
                             for name in ('chat-model', 'worker-model', 'explicit-model')})
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: Provider)
    return SessionBuilder(config)
