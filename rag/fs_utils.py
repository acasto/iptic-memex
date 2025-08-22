from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Tuple


DEFAULT_EXTS = {".md", ".mdx", ".txt", ".rst"}
DEFAULT_EXCLUDES = {".git", ".hg", ".svn", "node_modules", ".venv", "__pycache__"}


def _normalize_path(p: str) -> str:
    return str(Path(os.path.expandvars(os.path.expanduser(p))).resolve())


def load_rag_config(session) -> Tuple[Dict[str, str], list[str], str, str]:
    """Extract RAG-related config from session.

    Returns (indexes, active, vector_db, embedding_model)
    - indexes: mapping of index_name -> absolute path (existing only)
    - active: list of active index names (subset of indexes) or all if not set
    - vector_db: base directory for vector storage
    - embedding_model: embedding model name (may be empty string if not configured)
    """
    tools = session.get_tools()
    params = session.get_params()

    # Indexes from [RAG] as flat key=path (ONLY keys explicitly in [RAG])
    indexes: Dict[str, str] = {}
    cfg = session.config.base_config
    sec = getattr(cfg, '_sections', {}).get('RAG', {}) or {}
    try:
        for key, value in sec.items():
            if key in ('active', '__name__'):
                continue
            abspath = _normalize_path(value)
            if os.path.isdir(abspath):
                indexes[key] = abspath
    except Exception:
        pass

    # Active list
    active_raw = None
    try:
        active_raw = sec.get('active')
    except Exception:
        active_raw = None
    active = [x.strip() for x in str(active_raw).split(',')] if active_raw else list(indexes.keys())
    active = [n for n in active if n in indexes]

    # Vector DB path, default under home
    vector_db = params.get('vector_db') or os.path.expanduser('~/.codex/vector_store')

    # Embedding model from tools
    embedding_model = tools.get('embedding_model', '') or ''

    return indexes, active, vector_db, embedding_model


def iter_index_files(root: str, *, exts: set[str] | None = None, excludes: set[str] | None = None,
                     max_bytes: int = 10 * 1024 * 1024) -> Iterator[str]:
    """Yield absolute file paths under root that match filters.

    - Only returns files whose real path remains within root (symlink escape protection).
    - Skips excluded directory names and oversized files.
    """
    base = Path(root).resolve()
    exts = exts or DEFAULT_EXTS
    excludes = excludes or DEFAULT_EXCLUDES

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                rp = p.resolve()
                # Ensure within base
                if os.name == 'nt':
                    if not str(rp).lower().startswith(str(base).lower()):
                        continue
                else:
                    if not str(rp).startswith(str(base)):
                        continue
                # Filter by extension
                if rp.suffix.lower() not in exts:
                    continue
                # Size cap
                try:
                    if rp.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                yield str(rp)
            except Exception:
                continue


def read_text(path: str, encoding: str = 'utf-8') -> str | None:
    try:
        with open(path, 'r', encoding=encoding, errors='ignore') as f:
            return f.read()
    except Exception:
        return None


def chunk_text(text: str, *, size: int = 3000, overlap: int = 300) -> Iterable[tuple[int, int, str]]:
    """Yield (start, end, chunk_text) in character offsets with overlap."""
    if not text:
        return []
    n = len(text)
    i = 0
    while i < n:
        j = min(i + size, n)
        yield (i, j, text[i:j])
        if j >= n:
            break
        i = max(0, j - overlap)
