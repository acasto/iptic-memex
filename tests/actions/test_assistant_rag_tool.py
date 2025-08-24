from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
import pytest


class FakeOut:
    def write(self, *a, **k):
        pass


class FakeUtils:
    def __init__(self):
        self.output = FakeOut()
        self.input = type('I', (), {'get_input': lambda *a, **k: ''})()


class FakeSession:
    def __init__(self, tools: dict):
        self._tools = dict(tools)
        self.utils = FakeUtils()
        self._contexts = []
        # Minimal registry stub
        self._registry = types.SimpleNamespace(config=types.SimpleNamespace())
    def get_tools(self):
        return dict(self._tools)
    def add_context(self, kind: str, data):
        self._contexts.append((kind, data))
    def get_action(self, name):
        return None


def test_rag_tool_attaches_summary_and_threshold(monkeypatch, tmp_path):
    # Monkeypatch config loader used inside the tool
    import actions.assistant_rag_tool_action as rtool
    idxdir = tmp_path / 'db'
    monkeypatch.setattr(rtool, 'load_rag_config', lambda session: ({'notes': str(tmp_path)}, ['notes'], str(idxdir), 'M'))

    # Provider factory returns a simple provider with embed()
    class Prov:
        def __init__(self, s):
            pass
        def embed(self, texts, model=None):
            return [[1, 0, 0] for _ in texts]

    import core.provider_factory as pf
    monkeypatch.setattr(pf.ProviderFactory, 'instantiate_by_name', lambda *a, **k: Prov(None))

    # Search returns two hits, one below threshold
    def fake_search(**kwargs):
        return {
            'results': [
                {'score': 0.9, 'path': str(tmp_path / 'a.md'), 'line_start': 1, 'line_end': 2, 'index': 'notes', 'preview': ['aaa']},
                {'score': 0.1, 'path': str(tmp_path / 'b.md'), 'line_start': 3, 'line_end': 4, 'index': 'notes', 'preview': ['bbb']},
            ],
            'stats': {'total_items': 2}
        }

    monkeypatch.setattr(rtool, 'search', lambda **kw: fake_search(**kw))

    (tmp_path / 'a.md').write_text('aaa')
    (tmp_path / 'b.md').write_text('bbb')

    from actions.assistant_rag_tool_action import AssistantRagToolAction

    sess = FakeSession({'embedding_provider': 'X', 'embedding_model': 'M'})
    AssistantRagToolAction(sess).run({'query': 'test', 'threshold': 0.5}, '')

    # Should add a rag context and assistant feedback; filtered out low-score entry
    kinds = [k for k, _ in sess._contexts]
    assert 'rag' in kinds
    content = next(v for k, v in sess._contexts if k == 'rag')['content']
    assert 'a.md' in content and 'b.md' not in content
    # Feedback present
    assert any(k == 'assistant' for k, _ in sess._contexts)


def test_rag_tool_handles_missing_embedding_config(monkeypatch, tmp_path):
    import actions.assistant_rag_tool_action as rtool
    idxdir = tmp_path / 'db'
    monkeypatch.setattr(rtool, 'load_rag_config', lambda session: ({'notes': str(tmp_path)}, ['notes'], str(idxdir), 'M'))

    from actions.assistant_rag_tool_action import AssistantRagToolAction
    # Missing embedding provider/model triggers rag_error context
    sess = FakeSession({})
    AssistantRagToolAction(sess).run({'query': 'q'}, '')
    kinds = [k for k, _ in sess._contexts]
    assert 'assistant' in kinds
    msg = ' '.join((v.get('content') or '') for k, v in sess._contexts if k == 'assistant')
    assert 'RAGSEARCH' in msg and ('embedding' in msg or 'set' in msg)


def test_rag_tool_defaults_to_all_when_indexes_arg_empty(monkeypatch, tmp_path):
    # When neither index nor indexes are set (or are empty strings),
    # the tool should search active/all indexes rather than error out.
    import actions.assistant_rag_tool_action as rtool

    # Provide two known indexes and no active list (so it should use all)
    idxdir = tmp_path / 'db'
    paths = {'notes': str(tmp_path / 'notes'), 'docs': str(tmp_path / 'docs')}
    for p in paths.values():
        (tmp_path / 'db').mkdir(exist_ok=True)
    monkeypatch.setattr(rtool, 'load_rag_config', lambda session: (paths, [], str(idxdir), 'M'))

    # Provider factory stub
    class Prov:
        def __init__(self, s):
            pass
        def embed(self, texts, model=None):
            return [[1, 0] for _ in texts]

    import core.provider_factory as pf
    monkeypatch.setattr(pf.ProviderFactory, 'instantiate_by_name', lambda *a, **k: Prov(None))

    # Capture names passed to search and return an empty result set
    captured = {}
    def fake_search(**kwargs):
        captured['names'] = list(kwargs.get('names') or [])
        return {'results': [], 'stats': {'total_items': 0}}

    monkeypatch.setattr(rtool, 'search', lambda **kw: fake_search(**kw))

    from actions.assistant_rag_tool_action import AssistantRagToolAction
    sess = FakeSession({'embedding_provider': 'X', 'embedding_model': 'M'})

    # Provide an explicit empty 'indexes' arg to simulate strict provider behavior
    AssistantRagToolAction(sess).run({'query': 'q', 'indexes': ''}, '')

    # Should have searched all configured indexes (both keys in paths)
    assert set(captured.get('names') or []) == set(paths.keys())
    # And should have attached a rag context (even with no results it creates a header)
    assert any(k == 'rag' for k, _ in sess._contexts)
