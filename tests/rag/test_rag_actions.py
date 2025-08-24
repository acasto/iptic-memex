from __future__ import annotations

import os
import sys
import types
import configparser

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeOut:
    def write(self, *a, **k):
        pass
    def info(self, *a, **k):
        pass
    def warning(self, *a, **k):
        pass
    def error(self, *a, **k):
        pass


class FakeFS:
    def ensure_directory(self, path: str):
        os.makedirs(path, exist_ok=True)


class FakeUtils:
    def __init__(self):
        self.output = FakeOut()
        self.fs = FakeFS()
        self.input = type('I', (), {'get_input': lambda *a, **k: 'q'})()


class FakeUI:
    def __init__(self):
        self.events = []
        self.capabilities = type('C', (), {'blocking': False})()
    def emit(self, t, d):
        self.events.append({'type': t, **(d or {})})
    def ask_text(self, prompt: str):
        return 'q'


class FakeConfig:
    def __init__(self):
        self.base_config = configparser.ConfigParser()
        self.overrides = {}
    def create_session_config(self, options):
        return self
    def get_params(self, *a, **k):
        return {}


class FakeRegistry:
    def __init__(self, provider_cls=None):
        self.config = FakeConfig()
        self._provider_cls = provider_cls
    def load_provider_class(self, name):
        if self._provider_cls and name == 'FakeEmbed':
            return self._provider_cls
        return None


class FakeSession:
    def __init__(self, tools: dict, registry):
        self._tools = dict(tools)
        self._registry = registry
        self.utils = FakeUtils()
        self.ui = FakeUI()
    def get_tools(self):
        return dict(self._tools)
    def get_params(self):
        return {}


def test_rag_update_requires_explicit_config(tmp_path, monkeypatch):
    from actions.rag_update_action import RagUpdateAction
    # Prepare a dummy index so we get to provider resolution path
    root = tmp_path / 'idx'
    root.mkdir()
    (root / 'a.md').write_text('hello world')

    # Monkeypatch loader to return one index
    import actions.rag_update_action as rua
    monkeypatch.setattr(rua, 'load_rag_config', lambda session: ({'notes': str(root)}, ['notes'], str(tmp_path / 'db'), ''))

    sess = FakeSession(tools={}, registry=FakeRegistry())
    act = RagUpdateAction(sess)
    ok = act.run([])
    assert ok is False


def test_rag_update_uses_explicit_provider_only(tmp_path, monkeypatch):
    from actions.rag_update_action import RagUpdateAction

    # Create an index with one small file
    root = tmp_path / 'idx'
    root.mkdir()
    (root / 'a.md').write_text('hello world')

    # Monkeypatch loader
    import actions.rag_update_action as rua
    monkeypatch.setattr(rua, 'load_rag_config', lambda session: ({'notes': str(root)}, ['notes'], str(tmp_path / 'db'), 'dummy-model'))

    # Fake embedding provider that returns 3-dim vectors
    class FakeEmbedProvider:
        def __init__(self, session):
            self.session = session
        def embed(self, texts, model=None):
            return [[1.0, 0.0, 0.0] for _ in texts]

    sess = FakeSession(tools={'embedding_provider': 'FakeEmbed', 'embedding_model': 'dummy-model'}, registry=FakeRegistry(provider_cls=FakeEmbedProvider))
    act = RagUpdateAction(sess)
    ok = act.run([])
    assert ok is True


def test_load_rag_requires_explicit_config(monkeypatch):
    from actions.load_rag_action import LoadRagAction
    # Return at least one index so the code checks provider
    import actions.load_rag_action as lra
    monkeypatch.setattr(lra, 'load_rag_config', lambda session: ({'notes': '/abs/path'}, ['notes'], '/tmp/db', ''))
    sess = FakeSession(tools={}, registry=FakeRegistry())
    act = LoadRagAction(sess)
    ok = act.run([])
    assert ok is False
