from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import List, Dict, Any, Callable, Optional

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
    embedding_signature: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build or refresh the index for a single named root.

    Incremental: reuse embeddings for unchanged chunks (by content hash) when
    the embedding signature matches the existing manifest. Returns stats dict.
    """
    files = list(iter_index_files(root_path))
    new_chunks: List[Dict[str, Any]] = []
    new_texts: List[str] = []
    for path in files:
        text = read_text(path) or ''
        if not text:
            continue
        for start, end, ch in chunk_text(text):
            h = _hash_text(ch)
            new_chunks.append({
                'path': path,
                'start': start,
                'end': end,
                'hash': h,
            })
            new_texts.append(ch)

    store = NaiveStore(vector_db, index_name)

    # Try to load previous artifacts
    prev_manifest, prev_chunks, prev_embeddings = store.read_all()

    # Determine if we can reuse by signature
    sig = embedding_signature or {
        'embedding_model': embedding_model,
    }
    can_reuse = False
    if prev_manifest and prev_chunks and prev_embeddings:
        prev_sig = prev_manifest.get('embedding_signature') or {
            'embedding_model': prev_manifest.get('embedding_model')
        }
        can_reuse = (prev_sig == sig)

    # Fast path: if signatures match and chunk hashes are identical, skip
    if can_reuse and len(prev_chunks) == len(new_chunks):
        prev_hashes = [c.get('hash') for c in prev_chunks]
        new_hashes = [c.get('hash') for c in new_chunks]
        if prev_hashes == new_hashes:
            return {
                'files': len(files),
                'chunks': len(new_chunks),
                'embedded': 0,
                'index_dir': store.index_dir,
                'skipped': True,
            }

    # Build a map from hash -> embedding (first occurrence) for reuse
    reuse_map: Dict[str, List[float]] = {}
    if can_reuse and prev_chunks and prev_embeddings and len(prev_chunks) == len(prev_embeddings):
        for ch, vec in zip(prev_chunks, prev_embeddings):
            h = ch.get('hash')
            if h and h not in reuse_map:
                reuse_map[h] = vec

    # Prepare new embeddings, reusing when possible
    embeddings: List[List[float]] = []
    to_embed_texts: List[str] = []
    to_embed_idx: List[int] = []
    for idx, ch in enumerate(new_chunks):
        h = ch.get('hash')
        if h in reuse_map:
            embeddings.append(reuse_map[h])
        else:
            embeddings.append([])  # placeholder to fill later
            to_embed_idx.append(idx)
            to_embed_texts.append(new_texts[idx])

    # Embed missing chunks in batches
    embedded_new = 0
    for i in range(0, len(to_embed_texts), batch_size):
        batch = to_embed_texts[i:i + batch_size]
        vecs = embed_fn(batch)
        for j, vec in enumerate(vecs):
            global_idx = to_embed_idx[i + j]
            embeddings[global_idx] = vec
        embedded_new += len(vecs)

    # Persist
    now = datetime.utcnow().isoformat() + 'Z'
    manifest = {
        'name': index_name,
        'root_path': os.path.abspath(root_path),
        'created': prev_manifest.get('created') if prev_manifest else now,
        'updated': now,
        'embedding_model': embedding_model,  # for backward compat
        'embedding_signature': sig,
        'vector_dim': (len(embeddings[0]) if embeddings else (prev_manifest.get('vector_dim') if prev_manifest else None)),
        'backend': 'naive',
        'counts': {
            'files': len(files),
            'chunks': len(new_chunks),
        },
    }
    store.write(manifest=manifest, chunks=new_chunks, embeddings=embeddings)

    return {
        'files': len(files),
        'chunks': len(new_chunks),
        'embedded': embedded_new,
        'index_dir': store.index_dir,
        'skipped': embedded_new == 0 and not to_embed_texts,
    }
