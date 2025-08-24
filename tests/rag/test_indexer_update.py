from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rag.indexer import update_index
from rag.vector_store import NaiveStore


def test_update_index_reuse_and_reembed(tmp_path):
    # Create a small indexable file
    root = tmp_path / 'docs'
    root.mkdir()
    (root / 'a.md').write_text('hello world\nthis is a test')

    vector_db = tmp_path / 'db'

    # First build with signature S1 and 3-dim vectors
    def embed3(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    sig1 = {'provider': 'P', 'embedding_model': 'M1'}
    stats1 = update_index(
        index_name='notes',
        root_path=str(root),
        vector_db=str(vector_db),
        embed_fn=embed3,
        embedding_model='M1',
        embedding_signature=sig1,
    )
    assert stats1['embedded'] > 0 and not stats1.get('skipped')

    store = NaiveStore(str(vector_db), 'notes')
    man1 = store.read_manifest()
    assert man1 and man1.get('embedding_signature') == sig1
    assert man1.get('vector_dim') == 3

    # Second build with same signature: should skip (no embeddings done)
    stats2 = update_index(
        index_name='notes',
        root_path=str(root),
        vector_db=str(vector_db),
        embed_fn=embed3,
        embedding_model='M1',
        embedding_signature=sig1,
    )
    assert stats2.get('skipped') is True and stats2['embedded'] == 0

    # Third build with changed signature: should re-embed
    sig2 = {'provider': 'P2', 'embedding_model': 'M2'}
    stats3 = update_index(
        index_name='notes',
        root_path=str(root),
        vector_db=str(vector_db),
        embed_fn=embed3,
        embedding_model='M2',
        embedding_signature=sig2,
    )
    assert stats3['embedded'] > 0 and not stats3.get('skipped')
    man2 = store.read_manifest()
    assert man2 and man2.get('embedding_signature') == sig2
    assert man2.get('vector_dim') == 3  # dim persists with same embedder output shape

