from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("llama_cpp")

from core.cancellation import CancellationToken
from providers.llamacpp_provider import LlamaCppProvider


class FakeLLM:
    def tokenize(self, data: bytes, add_bos: bool = False):
        # Return a deterministic token list proportional to the byte length
        if isinstance(data, bytes):
            length = len(data)
        else:
            length = len(bytes(str(data), 'utf-8'))
        if add_bos:
            # Add a single BOS token at the front to mimic llama tokenization
            return [0] + list(range(length))
        return list(range(length))


def _default_messages():
    return [{'content': 'Hello from prompt'}]


def _chunk(text: str) -> dict:
    return {
        'choices': [
            {
                'delta': {'content': text},
            }
        ]
    }


class FakeSession:
    def __init__(self) -> None:
        self._params = {
            'context_size': 128,
            'n_gpu_layers': -1,
            'verbose': False,
        }
        self._token = CancellationToken()
        self.flags: dict[str, bool] = {}

    def get_params(self):
        return self._params

    def get_cancellation_token(self):
        return self._token

    def set_cancellation_token(self, token: CancellationToken) -> None:
        self._token = token

    def set_flag(self, name: str, value: bool) -> None:
        self.flags[name] = value

    def get_flag(self, name: str, default: bool = False) -> bool:
        return self.flags.get(name, default)


def _build_provider(session: FakeSession, *, closed_flag: dict[str, bool]):
    provider = LlamaCppProvider(session)
    provider.llm = FakeLLM()

    def _fake_chat():
        provider.last_api_param = {'messages': _default_messages()}

        def _gen():
            try:
                yield _chunk('foo')
                yield _chunk('bar')
            finally:
                closed_flag['value'] = True

        return _gen()

    provider.chat = _fake_chat  # type: ignore[assignment]
    return provider


def test_stream_chat_cancel_closes_inner_generator():
    session = FakeSession()
    session.set_cancellation_token(CancellationToken())
    closed = {'value': False}

    provider = _build_provider(session, closed_flag=closed)

    stream = provider.stream_chat()

    first = next(stream)
    assert first == 'foo'

    token = session.get_cancellation_token()
    token.cancel('user')
    session.set_flag('turn_cancelled', True)

    with pytest.raises(StopIteration):
        next(stream)

    assert closed['value'] is True

    usage = provider.turn_usage
    assert usage is not None
    assert usage['prompt_tokens'] > 0
    # Only the first chunk should have been counted before cancellation
    assert usage['completion_tokens'] == len('foo')


def test_stream_chat_close_updates_usage_and_closes_generator():
    session = FakeSession()
    session.set_cancellation_token(CancellationToken())
    closed = {'value': False}

    provider = _build_provider(session, closed_flag=closed)

    stream = provider.stream_chat()

    first = next(stream)
    assert first == 'foo'

    stream.close()

    assert closed['value'] is True

    usage = provider.turn_usage
    assert usage is not None
    # Only one chunk emitted before close
    assert usage['completion_tokens'] == len('foo')
