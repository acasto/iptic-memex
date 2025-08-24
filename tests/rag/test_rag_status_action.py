from __future__ import annotations

import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.rag_status_action import RagStatusAction
from rag.vector_store import NaiveStore


class CaptureOut:
    def __init__(self):
        self.lines = []
    def write(self, *args, **kwargs):
        if args:
            s = args[0]
            if s is None:
                return
            s = str(s)
            if s.strip():
                self.lines.append(s)


class FakeUtils:
    def __init__(self):
        self.output = CaptureOut()
        self.input = type('I', (), {'get_input': lambda *a, **k: 'y'})()


class FakeUI:
    def __init__(self):
        self.capabilities = type('C', (), {'blocking': False})()
        self.events = []
    def emit(self, t, d):
        self.events.append({'type': t, **(d or {})})


class FakeConfig:
    def __init__(self, rag_map: dict, vector_db: str):
        import configparser
        self.base_config = configparser.ConfigParser()
        self.base_config['RAG'] = {**rag_map, 'active': ','.join(rag_map.keys())}
        self.overrides = {}
    def get_params(self, *a, **k):
        return {}


class FakeRegistry:
    def __init__(self, cfg):
        self.config = cfg


class FakeSession:
    def __init__(self, cfg):
        self.config = cfg
        self.utils = FakeUtils()
        self.ui = FakeUI()
    def get_params(self):
        return {}
    def get_tools(self):
        return {}


def test_rag_status_reports_vector_dim(tmp_path, monkeypatch):
    # Prepare artifacts for one index
    index_name = 'notes'
    root = tmp_path / 'docs'
    root.mkdir()
    (root / 'a.md').write_text('hello\nworld')
    vector_db = tmp_path / 'db'
    store = NaiveStore(str(vector_db), index_name)
    store.ensure_dirs()
    manifest = {
        'name': index_name,
        'root_path': str(root),
        'created': 't0',
        'updated': 't1',
        'embedding_model': 'M',
        'embedding_signature': {'provider': 'P', 'embedding_model': 'M'},
        'vector_dim': 3,
        'backend': 'naive',
        'counts': {'files': 1, 'chunks': 1},
    }
    chunks = [{'path': str(root / 'a.md'), 'start': 0, 'end': 5, 'hash': 'h'}]
    embs = [[1.0, 0.0, 0.0]]
    store.write(manifest=manifest, chunks=chunks, embeddings=embs)

    cfg = FakeConfig({'notes': str(root)}, str(vector_db))
    sess = FakeSession(cfg)
    act = RagStatusAction(sess)
    # Ensure status reads from our vector_db path
    import actions.rag_status_action as rsa
    monkeypatch.setattr(rsa, 'load_rag_config', lambda session: ({'notes': str(root)}, ['notes'], str(vector_db), 'M'))
    ok = act.run([])
    assert ok is True
    text = '\n'.join(sess.utils.output.lines)
    assert 'RAG Index Status' in text
    assert 'dim:' in text and '3' in text
    assert 'ok' in text or 'needs update' in text
