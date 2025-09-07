from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.load_rag_action import LoadRagAction


class FakeOut:
    def write(self, *a, **k):
        pass


class FakeUtils:
    def __init__(self):
        self.output = FakeOut()
        self.input = type('I', (), {'get_input': lambda *a, **k: 'q'})()


class FakeUI:
    def __init__(self):
        self.capabilities = type('C', (), {'blocking': False})()
        self._asks = []
    def emit(self, *a, **k):
        pass
    def ask_text(self, prompt: str):
        # First return a query, then yes to use results, then exit
        if 'Enter RAG query' in prompt:
            return 'query'
        if 'Use these results' in prompt:
            return 'y'
        return 'q'


class FakeSession:
    def __init__(self, tools: dict, rag: dict | None = None):
        self._tools = dict(tools)
        self._rag = dict(rag or {})
        self.utils = FakeUtils()
        self.ui = FakeUI()
        self._contexts = []
        self._registry = type('R', (), {'config': type('Cfg', (), {})()})()
    def get_tools(self):
        return dict(self._tools)
    def get_all_options_from_section(self, section: str):
        if section == 'RAG':
            return dict(self._rag)
        return {}
    def add_context(self, kind: str, data):
        self._contexts.append((kind, data))
    def get_action(self, name):
        return None


def test_threshold_filters_and_summary(monkeypatch, tmp_path):
    # Monkeypatch config loader
    import actions.load_rag_action as lra
    idxdir = tmp_path / 'db'
    monkeypatch.setattr(lra, 'load_rag_config', lambda session: ({'notes': str(tmp_path)}, ['notes'], str(idxdir), 'M'))

    # Monkeypatch provider factory and search
    class Prov:
        def __init__(self, s): pass
        def embed(self, texts, model=None): return [[1,0,0]]
    import core.provider_factory as pf
    monkeypatch.setattr(pf.ProviderFactory, 'instantiate_by_name', lambda *a, **k: Prov(None))

    def fake_search(**kwargs):
        return {'results': [
            {'score': 0.9, 'path': str(tmp_path / 'a.md'), 'line_start': 1, 'line_end': 2, 'index': 'notes', 'preview': ['aaa']},
            {'score': 0.1, 'path': str(tmp_path / 'b.md'), 'line_start': 3, 'line_end': 4, 'index': 'notes', 'preview': ['bbb']},
        ], 'stats': {'total_items': 2}}
    monkeypatch.setattr(lra, 'search', lambda **kw: fake_search(**kw))

    (tmp_path / 'a.md').write_text('aaa')
    (tmp_path / 'b.md').write_text('bbb')

    sess = FakeSession(
        {'embedding_provider': 'X', 'embedding_model': 'M'},
        {'similarity_threshold': 0.5, 'attach_mode': 'summary'}
    )
    ok = LoadRagAction(sess).run([])
    assert ok is True
    # Only one context added with summary; threshold filters out low-score
    kinds = [k for k, _ in sess._contexts]
    assert kinds == ['rag']
    content = sess._contexts[0][1]['content']
    assert 'a.md' in content and 'b.md' not in content


def test_snippets_mode_groups_and_budget(monkeypatch, tmp_path):
    import actions.load_rag_action as lra
    idxdir = tmp_path / 'db'
    monkeypatch.setattr(lra, 'load_rag_config', lambda session: ({'notes': str(tmp_path)}, ['notes'], str(idxdir), 'M'))

    class Prov:
        def __init__(self, s): pass
        def embed(self, texts, model=None): return [[1,0,0]]
    import core.provider_factory as pf
    monkeypatch.setattr(pf.ProviderFactory, 'instantiate_by_name', lambda *a, **k: Prov(None))

    # Two hits from same file with adjacent ranges; should merge
    def fake_search(**kwargs):
        return {'results': [
            {'score': 0.6, 'path': str(tmp_path / 'c.md'), 'line_start': 1, 'line_end': 2, 'index': 'notes', 'preview': ['l1','l2']},
            {'score': 0.5, 'path': str(tmp_path / 'c.md'), 'line_start': 3, 'line_end': 3, 'index': 'notes', 'preview': ['l3']},
        ], 'stats': {'total_items': 2}}
    monkeypatch.setattr(lra, 'search', lambda **kw: fake_search(**kw))

    (tmp_path / 'c.md').write_text('line1\nline2\nline3\nline4\n')

    sess = FakeSession(
        {'embedding_provider': 'X', 'embedding_model': 'M'},
        {'attach_mode': 'snippets', 'total_chars_budget': 100}
    )
    ok = LoadRagAction(sess).run([])
    assert ok is True
    kinds = [k for k, _ in sess._contexts]
    assert kinds == ['rag']
    content = sess._contexts[0][1]['content']
    # Merged lines 1-3 present once
    assert 'c.md#L1-L3' in content
    assert 'line1' in content and 'line3' in content


def test_merge_gap_controls_merge(monkeypatch, tmp_path):
    import actions.load_rag_action as lra
    idxdir = tmp_path / 'db'
    monkeypatch.setattr(lra, 'load_rag_config', lambda session: ({'notes': str(tmp_path)}, ['notes'], str(idxdir), 'M'))

    class Prov:
        def __init__(self, s): pass
        def embed(self, texts, model=None): return [[1,0,0]]
    import core.provider_factory as pf
    monkeypatch.setattr(pf.ProviderFactory, 'instantiate_by_name', lambda *a, **k: Prov(None))

    def fake_search(**kwargs):
        return {'results': [
            {'score': 0.6, 'path': str(tmp_path / 'd.md'), 'line_start': 1, 'line_end': 2, 'index': 'notes', 'preview': ['p1']},
            {'score': 0.5, 'path': str(tmp_path / 'd.md'), 'line_start': 8, 'line_end': 9, 'index': 'notes', 'preview': ['p2']},
        ], 'stats': {'total_items': 2}}
    monkeypatch.setattr(lra, 'search', lambda **kw: fake_search(**kw))

    # Create file with at least 9 lines
    (tmp_path / 'd.md').write_text('\n'.join([f'l{i}' for i in range(1, 11)]))

    # Small merge gap => no merge
    sess1 = FakeSession(
        {'embedding_provider': 'X', 'embedding_model': 'M'},
        {'attach_mode': 'snippets', 'group_by_file': True, 'merge_adjacent': True, 'merge_gap': 5}
    )
    ok1 = LoadRagAction(sess1).run([])
    assert ok1 is True
    content1 = sess1._contexts[0][1]['content']
    # Should contain two separate headers
    assert 'd.md#L1-L2' in content1 and 'd.md#L8-L9' in content1

    # Larger merge gap => merged into one range
    sess2 = FakeSession(
        {'embedding_provider': 'X', 'embedding_model': 'M'},
        {'attach_mode': 'snippets', 'group_by_file': True, 'merge_adjacent': True, 'merge_gap': 10}
    )
    ok2 = LoadRagAction(sess2).run([])
    assert ok2 is True
    content2 = sess2._contexts[0][1]['content']
    assert 'd.md#L1-L9' in content2
