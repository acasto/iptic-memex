"""Local backend errors must reach the shared runner, without model loading."""
from types import SimpleNamespace
import importlib.util
from pathlib import Path
import sys

import pytest


def test_llamacpp_chat_propagates_backend_error():
    pytest.importorskip('llama_cpp')
    from providers.llamacpp_provider import LlamaCppProvider
    session = SimpleNamespace(get_params=lambda: {})
    provider = LlamaCppProvider(session)
    provider.assemble_message = lambda: []

    def fail():
        raise RuntimeError('backend unavailable')

    provider._get_chat_llm = fail
    with pytest.raises(RuntimeError, match='backend unavailable'):
        provider.chat()


@pytest.mark.parametrize('stream', [False, True])
def test_mlx_propagates_generation_errors(monkeypatch, stream):
    # Load the provider with the optional library unavailable, avoiding GPU init.
    monkeypatch.setitem(sys.modules, 'mlx_lm', None)
    source = Path(__file__).resolve().parents[2] / 'providers' / 'mlx_provider.py'
    spec = importlib.util.spec_from_file_location('mlx_error_fixture', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Bypass constructor so the test never loads a model.
    provider = module.MlxProvider.__new__(module.MlxProvider)
    provider.session = SimpleNamespace(get_params=lambda: {'stream': stream})
    provider.model = None
    provider.model_name = 'fake'
    provider.tokenizer = None
    provider.parameters = []
    provider.last_api_param = None
    provider.running_usage = {'total_time': 0.0}
    provider.assemble_message = lambda: []
    provider._messages_to_prompt = lambda messages: 'Task'

    def fail(*args, **kwargs):
        raise RuntimeError('generation failed')

    monkeypatch.setattr(module, 'generate', fail, raising=False)
    monkeypatch.setattr(module, 'stream_generate', fail, raising=False)
    with pytest.raises(RuntimeError, match='generation failed'):
        if stream:
            list(provider.stream_chat())
        else:
            provider.chat()
