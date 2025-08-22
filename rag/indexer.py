from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import List, Dict, Any, Callable

from .fs_utils import iter_index_files, read_text, chunk_text
from .vector_store import NaiveStore


def _hash_text(s: str) -> str:
    return hashlib.sha1(s.encode('utf-8', errors='ignore')).hexdigest()


def update_index(
    *,
    index_name: str,
    root_path: str,
    vector_db: str,
    embed_fn: Callable[[List[str]], List[List[float]]],
    embedding_model: str,
    batch_size: int = 128,
) -> Dict[str, Any]:
    """Build/rebuild the index for a single named root.

    MVP: full rebuild. Returns stats dict.
    """
    files = list(iter_index_files(root_path))
    chunks: List[Dict[str, Any]] = []
    texts: List[str] = []
    for path in files:
        text = read_text(path) or ''
        if not text:
            continue
        for start, end, ch in chunk_text(text):
            chunks.append({
                'path': path,
                'start': start,
                'end': end,
                'hash': _hash_text(ch),
            })
            texts.append(ch)

    # Embed in batches
    embeddings: List[List[float]] = []
    if texts:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vecs = embed_fn(batch)
            embeddings.extend(vecs)

    # Persist
    store = NaiveStore(vector_db, index_name)
    manifest = {
        'name': index_name,
        'root_path': os.path.abspath(root_path),
        'created': datetime.utcnow().isoformat() + 'Z',
        'updated': datetime.utcnow().isoformat() + 'Z',
        'embedding_model': embedding_model,
        'backend': 'naive',
        'counts': {
            'files': len(files),
            'chunks': len(chunks),
        },
    }
    store.write(manifest=manifest, chunks=chunks, embeddings=embeddings)

    return {
        'files': len(files),
        'chunks': len(chunks),
        'embedded': len(embeddings),
        'index_dir': store.index_dir,
    }

