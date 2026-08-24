from __future__ import annotations

import os
import sys

import openai
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from providers.llamacppserver_provider import LlamaCppServerProvider


class FakeSession:
    def get_params(self):
        return {
            'binary': '/tmp/llama-server',
            'model_path': '/tmp/model.gguf',
            'host': '127.0.0.1',
            'port_range': '40100-40149',
            'use_api_key': False,
        }


class FakeProcess:
    returncode = None

    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups only')
def test_managed_server_uses_separate_process_group(monkeypatch):
    provider = LlamaCppServerProvider.__new__(LlamaCppServerProvider)
    provider.session = FakeSession()
    provider._proc = None
    provider._api_key = None
    provider._base_url = None
    provider._log_path = None

    popen_call = {}

    def fake_popen(cmd, **kwargs):
        popen_call['cmd'] = cmd
        popen_call['kwargs'] = kwargs
        return FakeProcess()

    monkeypatch.setattr('providers.llamacppserver_provider.os.path.exists', lambda _path: True)
    monkeypatch.setattr('providers.llamacppserver_provider.subprocess.Popen', fake_popen)
    monkeypatch.setattr(provider, '_pick_free_port', lambda *_args: 40100)
    monkeypatch.setattr(provider, '_wait_until_ready', lambda *_args: None)
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: kwargs)

    client = provider._initialize_client()

    assert popen_call['kwargs']['process_group'] == 0
    assert client['base_url'] == 'http://127.0.0.1:40100/v1'

    provider.cleanup()

    assert provider._proc.terminated is True
    assert provider._proc.killed is False
