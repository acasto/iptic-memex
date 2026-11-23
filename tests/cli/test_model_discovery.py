"""Discover and select models through real SDKs backed by offline HTTP responses."""

import json
from configparser import ConfigParser
from types import SimpleNamespace

import httpx
import pytest
from anthropic import Anthropic
from click.testing import CliRunner
from google import genai
from google.genai.types import HttpOptions
from openai import OpenAI

from app.session_builder import SessionBuilder
from component_registry import ComponentRegistry
from config_manager import ConfigManager
from core.provider_factory import ProviderFactory
from main import cli
from providers.anthropic_provider import AnthropicProvider
from providers.google_provider import GoogleProvider
from providers.openai_provider import OpenAIProvider
from providers.openairesponses_provider import OpenAIResponsesProvider


def test_catalog_refresh_and_offline_selection(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    base = ConfigParser()
    base.read_dict({
        'DEFAULT': {'default_model': 'claude-local', 'template_handler': 'none'},
        'OpenAIResponses': {'active': 'true', 'discover_models': 'true'},
        'Anthropic': {'active': 'true', 'discover_models': 'true'},
        'Google': {'active': 'true', 'discover_models': 'true', 'models': 'gemini-existing, gemini-new'},
    })
    models = ConfigParser()
    models.read_dict({
        'claude-local': {'provider': 'Anthropic', 'model_name': 'claude-first', 'price_in': '4',
                         'price_out': '8', 'context_size': '1234'},
        'gemini-existing': {'provider': 'Google', 'model_name': 'gemini-existing',
                            'base_url': 'https://other.test', 'price_in': '3'},
    })
    monkeypatch.setattr(ConfigManager, '_load_configs', lambda *args: base)
    monkeypatch.setattr(ConfigManager, '_load_models', lambda *args: models)

    requests = []
    failing = set()

    def respond(request):
        host = request.url.host
        requests.append((host, request.url.path, request.url.params))
        assert request.method == 'GET'
        if host in failing:
            return httpx.Response(503, json={'error': {'message': 'private details'}})
        if host == 'openai.test':
            return httpx.Response(200, json={'object': 'list', 'data': [
                {'id': 'gpt-new', 'created': 1, 'object': 'model', 'owned_by': 'openai'}]})
        if host == 'anthropic.test':
            after = request.url.params.get('after_id')
            name = 'claude-second' if after else 'claude-first'
            return httpx.Response(200, json={'data': [
                {'id': name, 'created_at': '2026-01-01T00:00:00Z', 'type': 'model',
                 'display_name': 'Claude', 'max_input_tokens': 10000, 'max_tokens': 4000}],
                'has_more': not after, 'first_id': name, 'last_id': name})
        return httpx.Response(200, json={'models': [
            {'name': 'models/gemini-new', 'inputTokenLimit': 10000, 'outputTokenLimit': 8000,
             'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/gemini-existing', 'inputTokenLimit': 10000,
             'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/gemini-embedding', 'supportedGenerationMethods': ['embedContent']},
            {'name': 'models/gemini-filtered', 'supportedGenerationMethods': ['generateContent']},
        ]})

    def instantiate(name, *, registry, params_override, required_method):
        assert registry.config.base_config is base
        assert params_override['max_retries'] == 0
        assert required_method == 'list_models'
        http = httpx.Client(transport=httpx.MockTransport(respond))
        session = SimpleNamespace(get_params=lambda: {})
        if name == 'OpenAIResponses':
            client = OpenAI(api_key='test', max_retries=0, base_url='https://openai.test/v1',
                            http_client=http)
            monkeypatch.setattr(OpenAIResponsesProvider, '_initialize_client', lambda _: client)
            return OpenAIResponsesProvider(session)
        if name == 'Anthropic':
            client = Anthropic(api_key='test', max_retries=0, base_url='https://anthropic.test',
                               http_client=http)
            monkeypatch.setattr(AnthropicProvider, '_initialize_client', lambda _: client)
            return AnthropicProvider(session)
        provider = GoogleProvider(session)
        provider.client = genai.Client(api_key='test', vertexai=False,
                                      http_options=HttpOptions(base_url='https://google.test',
                                                               httpx_client=http))
        return provider

    monkeypatch.setattr(ProviderFactory, 'instantiate_by_name', instantiate)
    runner = CliRunner()
    refreshed = runner.invoke(cli, ['list-models', '--refresh'])
    assert refreshed.exit_code == 0, refreshed.output
    assert 'OpenAIResponses:gpt-new (chat compatibility unverified)' in refreshed.stdout
    assert 'Anthropic: refreshed 2 models' in refreshed.stderr
    assert 'gemini-embedding' not in refreshed.stdout
    assert 'gemini-filtered' not in refreshed.stdout
    assert any(path == '/v1/models' and params.get('after_id') == 'claude-first'
               for _, path, params in requests)

    failing.add('openai.test')
    partial = runner.invoke(cli, ['list-models', '--refresh'])
    assert partial.exit_code == 0, partial.output
    assert 'HTTP 503' in partial.stderr and 'private details' not in partial.output
    assert 'OpenAIResponses:gpt-new' in partial.stdout
    requests.clear()

    selected = runner.invoke(cli, ['-m', 'OpenAIResponses:gpt-new', 'list-models'])
    assert selected.exit_code == 0, selected.output
    assert requests == []
    config = ConfigManager().create_session_config()
    assert config.normalize_model_name('Anthropic:claude-first') == 'claude-local'
    assert config.get_params('claude-local')['context_size'] == 1234
    assert config.get_params('claude-local')['output_token_limit'] == 4000
    assert 'context_size' not in config.get_params('gemini-existing')
    assert config.get_params('Google:gemini-existing')['context_size'] == 10000
    assert config.get_params('Google:gemini-new')['provider'] == 'Google'
    assert config.get_params('OpenAIResponses:gpt-new')['price_in'] is None
    assert 'Anthropic:claude-second' in config.list_models()
    assert 'Google:gemini-new' in config.list_models()
    assert 'OpenAIResponses:gpt-new' not in config.list_models()
    assert 'OpenAIResponses:gpt-new' in config.list_models(showall=True)
    assert 'gpt-new' not in models.sections()

    def complete(request):
        assert request.url.path == '/v1/responses'
        payload = json.loads(request.content)
        assert payload['model'] == 'gpt-new'
        assert payload['input'][0]['role'] == 'developer'
        return httpx.Response(200, json={
            'id': 'resp_1', 'object': 'response', 'status': 'completed', 'model': 'gpt-new',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Done'}]}],
        })

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class',
                        lambda self, name: {'OpenAIResponses': OpenAIResponsesProvider}[name])
    monkeypatch.setattr(OpenAIResponsesProvider, '_initialize_client', lambda self: OpenAI(
        api_key='test', base_url='https://api.openai.com/v1', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(complete))))
    completed = runner.invoke(cli, ['-m', 'OpenAIResponses:gpt-new', '--message', 'Hello',
                                    '-p', 'Give a short answer.'])
    assert completed.exit_code == 0, completed.output
    assert completed.stdout.strip() == 'Done'

    monkeypatch.setenv('OPENAI_BASE_URL', 'https://a-different-endpoint.test/v1')
    different_endpoint = ConfigManager().create_session_config()
    assert not different_endpoint.valid_model('OpenAIResponses:gpt-new')
    assert different_endpoint.valid_model('Google:gemini-new')


def test_discovery_opt_in_and_qualified_defaults_for_a_compatible_alias(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    base = ConfigParser()
    model = 'MyGateway:vendor/new-model'
    base.read_dict({
        'DEFAULT': {'default_model': model, 'template_handler': 'none'},
        'AGENT': {'default_model': 'MyGateway:vendor/agent-model'},
        'OpenAIResponses': {'active': 'true'},
        'DisabledGateway': {'alias': 'OpenAI', 'active': 'false', 'discover_models': 'true'},
        'MyGateway': {'alias': 'OpenAI', 'active': 'true', 'discover_models': 'true',
                      'base_url': 'https://gateway.test/v1'},
        'Unsupported': {'active': 'true', 'discover_models': 'true'},
    })
    models = ConfigParser()
    monkeypatch.setattr(ConfigManager, '_load_configs', lambda *args: base)
    monkeypatch.setattr(ConfigManager, '_load_models', lambda *args: models)
    calls = []

    def respond(request):
        assert request.url.host == 'gateway.test'
        calls.append(request.url.path)
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'object': 'list', 'data': [
                {'id': name, 'created': 1, 'object': 'model', 'owned_by': 'vendor'}
                for name in ('vendor/new-model', 'vendor/agent-model')
            ]})
        assert request.url.path == '/v1/chat/completions'
        assert json.loads(request.content)['model'] == 'vendor/agent-model'
        return httpx.Response(200, json={
            'id': 'chat_1', 'object': 'chat.completion', 'created': 1, 'model': 'vendor/agent-model',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': 'Done'}}],
        })

    class Unsupported:
        def __init__(self, session):
            pytest.fail('A provider without listing support must not be initialized for discovery')

    def load_provider_class(registry, name):
        return {'MyGateway': OpenAIProvider, 'Unsupported': Unsupported}[name]

    def initialize_client(provider):
        params = provider.session.get_params()
        return OpenAI(api_key='test', base_url=params['base_url'], max_retries=0,
                      http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', load_provider_class)
    monkeypatch.setattr(OpenAIProvider, '_initialize_client', initialize_client)
    with pytest.raises(RuntimeError, match='list-models --refresh'):
        ConfigManager().create_session_config().get_params()

    runner = CliRunner()
    refreshed = runner.invoke(cli, ['list-models', '--refresh'])
    assert refreshed.exit_code == 0, refreshed.output
    assert f'{model} (default, chat compatibility unverified)' in refreshed.stdout
    assert 'Unsupported: refresh failed (provider adapter has no model discovery)' in refreshed.stderr
    assert calls == ['/v1/models']

    chat = SessionBuilder(ConfigManager()).build(mode='chat', private=True)
    try:
        assert isinstance(chat.provider, OpenAIProvider)
        assert chat.get_params()['model_name'] == 'vendor/new-model'
        assert chat.get_params()['provider'] == 'MyGateway'
    finally:
        chat.close()
    completed = runner.invoke(cli, ['--message', 'Hello'])
    assert completed.exit_code == 0, completed.output
    assert completed.stdout.strip() == 'Done'
    assert calls == ['/v1/models', '/v1/chat/completions']
    assert models.sections() == []

    base.set('MyGateway', 'discover_models', 'false')
    assert not ConfigManager().create_session_config().valid_model(model)
