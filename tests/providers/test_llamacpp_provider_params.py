from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("llama_cpp")

from providers.llamacpp_provider import LlamaCppProvider


class FakeLLM:
    def __init__(self):
        self.last_kwargs = None

    def create_chat_completion(self, **kwargs):
        self.last_kwargs = kwargs
        return {
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "choices": [{"message": {"content": "ok"}}],
        }


class FakeSession:
    def __init__(self):
        self._params = {
            "context_size": 128,
            "n_gpu_layers": -1,
            "verbose": False,
            "temperature": "0.7",
            "max_tokens": "32",
        }

    def get_params(self):
        return dict(self._params)


def test_llamacpp_coerces_numeric_request_params_from_strings():
    session = FakeSession()
    provider = LlamaCppProvider(session)
    fake_llm = FakeLLM()
    provider.llm = fake_llm
    provider._get_chat_llm = lambda: fake_llm  # type: ignore[method-assign]
    provider.assemble_message = lambda: [{"role": "user", "content": "hi"}]  # type: ignore[method-assign]

    assert provider.chat() == "ok"

    assert fake_llm.last_kwargs is not None
    assert fake_llm.last_kwargs["temperature"] == 0.7
    assert isinstance(fake_llm.last_kwargs["temperature"], float)
    assert fake_llm.last_kwargs["max_tokens"] == 32
    assert isinstance(fake_llm.last_kwargs["max_tokens"], int)
