from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Tuple, List, Optional, Set
import fnmatch
import configparser


DEFAULT_EXTS = {".md", ".mdx", ".txt", ".rst"}
DEFAULT_EXCLUDES = {".git", ".hg", ".svn", "node_modules", ".venv", "__pycache__"}

# Config helpers
try:
    from utils.tool_args import get_list, get_int
except Exception:  # very early import paths or tests
    def get_list(args, key, default=None, sep=",", strip_items=True):  # type: ignore
        try:
            val = args.get(key)
        except Exception:
            val = None
        if val is None:
            return default
        if isinstance(val, (list, tuple)):
            out = []
            for it in val:
                try:
                    s = str(it)
                    if strip_items:
                        s = s.strip()
                    if s:
                        out.append(s)
                except Exception:
                    continue
            return out
        try:
            s = str(val).strip()
        except Exception:
            return default
        if s == "":
            return default
        parts = [p.strip() if strip_items else p for p in s.split(sep)]
        return [p for p in parts if p]
    def get_int(args, key, default=None):  # type: ignore
        try:
            val = args.get(key)
        except Exception:
            return default
        if val is None:
            return default
        try:
            return int(val)
        except Exception:
            return default


def _normalize_path(p: str) -> str:
    return str(Path(os.path.expandvars(os.path.expanduser(p))).resolve())


def load_rag_config(session) -> Tuple[Dict[str, str], list[str], str, str]:
    """Extract RAG-related config from session using the new schema.

    Returns (indexes, active_names, vector_db, embedding_model)
    - indexes: mapping of index_name -> absolute path (existing only)
    - active_names: list of index names to search/update by default (now identical to [RAG].indexes)
    - vector_db: base directory for vector storage (from [RAG].vector_db)
    - embedding_model: embedding model name (may be empty string if not configured)

    New schema overview:
      [RAG]
      indexes = notes,docs
      active = true

      [RAG.notes]
      path = ~/notes
      include = **/*.md
      exclude = .git, node_modules

      [RAG.docs]
      path = ~/docs
    """
    tools = session.get_tools()
    params = session.get_params()

    # Be robust in tests or minimal sessions without a config
    cfg = getattr(getattr(session, 'config', None), 'base_config', None) or configparser.ConfigParser()

    # Parse [RAG].indexes list
    indexes: Dict[str, str] = {}
    rag_top: Dict[str, str] = {}
    try:
        rag_top = {k: v for k, v in getattr(cfg, '_sections', {}).get('RAG', {}) .items()}
    except Exception:
        rag_top = {}

    raw_indexes = None
    try:
        raw_indexes = rag_top.get('indexes')
    except Exception:
        raw_indexes = None
    names = [x.strip() for x in str(raw_indexes).split(',')] if raw_indexes else []

    # Build indexes from [RAG.<name>].path
    if names:
        for name in names:
            sec_name = f'RAG.{name}'
            try:
                if cfg.has_section(sec_name):
                    path_val = cfg.get(sec_name, 'path', fallback='')
                else:
                    path_val = ''
            except Exception:
                path_val = ''
            if path_val:
                abspath = _normalize_path(path_val)
                if os.path.isdir(abspath):
                    indexes[name] = abspath
    # If none found, leave empty (no legacy auto-detection required)

    # Active indexes list is the same as declared [RAG].indexes
    active = list(indexes.keys())

    # Vector DB path: must be provided in [RAG].vector_db (no fallback)
    try:
        vector_db = (cfg.get('RAG', 'vector_db', fallback='') or '').strip()
    except Exception:
        vector_db = ''

    # Embedding model from tools (still configured under [TOOLS])
    embedding_model = tools.get('embedding_model', '') or ''

    return indexes, active, vector_db, embedding_model


def _matches_any(posix_path: str, patterns: Optional[List[str]]) -> bool:
    if not patterns:
        return False
    for pat in patterns:
        try:
            # Treat leading '**/' as optional so patterns like '**/*.pdf'
            # also match files at the root level (e.g., 'a.pdf').
            if fnmatch.fnmatchcase(posix_path, pat):
                return True
            if pat.startswith('**/') and fnmatch.fnmatchcase(posix_path, pat[3:]):
                return True
        except Exception:
            continue
    return False


def _rel_posix(base: Path, p: Path) -> Optional[str]:
    try:
        rel = p.resolve().relative_to(base)
        s = rel.as_posix()
        return s if s != "." else ""
    except Exception:
        return None


def iter_index_files(
    root: str,
    *,
    exts: set[str] | None = None,
    excludes: set[str] | None = None,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    max_bytes: int = 10 * 1024 * 1024,
) -> Iterator[str]:
    """Yield absolute file paths under root that match filters.

    - Only returns files whose real path remains within root (symlink escape protection).
    - Skips excluded directory names and oversized files.
    """
    base = Path(root).resolve()
    exts = exts or DEFAULT_EXTS
    excludes = excludes or DEFAULT_EXCLUDES

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place (by name and glob)
        cur_rel = _rel_posix(base, Path(dirpath)) or ""
        pruned: List[str] = []
        for d in list(dirnames):
            if d in excludes:
                pruned.append(d)
                continue
            child_rel = f"{cur_rel}/{d}" if cur_rel else d
            if _matches_any(child_rel, exclude_globs) or _matches_any(child_rel + "/", exclude_globs):
                pruned.append(d)
                continue
        if pruned:
            dirnames[:] = [d for d in dirnames if d not in pruned]
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
                # Glob includes (relative to root)
                rel = _rel_posix(base, rp)
                if rel is None:
                    continue
                if include_globs and not _matches_any(rel, include_globs):
                    continue
                # Glob excludes
                if _matches_any(rel, exclude_globs):
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


def load_rag_filters(session) -> Dict[str, Dict[str, List[str] | None]]:
    """Load per-index include/exclude glob patterns.

    Returns mapping: { index: { 'include': [patterns]|None, 'exclude': [patterns]|None } }
    - Per-index keys read from [RAG.<index>]: include, exclude
    - Defaults from [RAG]: default_include, default_exclude
    """
    # Be robust in tests or minimal sessions without a config
    cfg = getattr(getattr(session, 'config', None), 'base_config', None) or configparser.ConfigParser()

    # Top-level defaults under [RAG]
    top = getattr(cfg, '_sections', {}).get('RAG', {}) or {}
    default_include = get_list(top, 'default_include') or []  # type: ignore[arg-type]
    default_exclude = get_list(top, 'default_exclude') or []  # type: ignore[arg-type]

    # Which indexes are defined
    raw_names = (top.get('indexes') if isinstance(top, dict) else None) or ''
    names = [x.strip() for x in str(raw_names).split(',') if str(x).strip()]

    out: Dict[str, Dict[str, List[str] | None]] = {}
    for name in names:
        sec_name = f'RAG.{name}'
        inc = []
        exc = []
        try:
            if cfg.has_section(sec_name):
                inc = get_list(cfg._sections.get(sec_name, {}), 'include') or []  # type: ignore[arg-type]
                exc = get_list(cfg._sections.get(sec_name, {}), 'exclude') or []  # type: ignore[arg-type]
        except Exception:
            inc = []
            exc = []
        eff_inc = inc if (inc and len(inc) > 0) else (default_include if default_include else None)
        # Merge default + per-index for excludes
        merged_exc: List[str] = []
        if default_exclude:
            merged_exc.extend(default_exclude)
        if exc:
            for p in exc:
                if p not in merged_exc:
                    merged_exc.append(p)
        eff_exc = merged_exc if merged_exc else None
        out[name] = {'include': eff_inc, 'exclude': eff_exc}

    return out


def load_rag_exts(session) -> Set[str]:
    """Return effective extension allowlist for discovery.

    Defaults to DEFAULT_EXTS. If `[RAG].included_exts` is set, it extends
    the defaults (merge) for safety.
    """
    # Read from [RAG] section
    cfg = getattr(getattr(session, 'config', None), 'base_config', None) or configparser.ConfigParser()
    top = getattr(cfg, '_sections', {}).get('RAG', {}) or {}
    raw = get_list(top, 'included_exts')  # type: ignore[arg-type]
    exts: Set[str] = set(DEFAULT_EXTS)
    if raw:
        for itm in raw:
            s = str(itm).strip().lower()
            if not s:
                continue
            if not s.startswith('.'):
                if s.startswith('*'):
                    # allow forms like *.md
                    s = s[1:]
                s = '.' + s
            exts.add(s)
    return exts


def load_rag_max_bytes(session) -> int:
    """Return max file size in bytes for discovery.

    Reads `[RAG].max_file_mb`; defaults to 10 MB when unset/invalid.
    """
    cfg = getattr(getattr(session, 'config', None), 'base_config', None) or configparser.ConfigParser()
    top = getattr(cfg, '_sections', {}).get('RAG', {}) or {}
    mb = get_int(top, 'max_file_mb')  # type: ignore[arg-type]
    if not isinstance(mb, int) or mb <= 0:
        return 10 * 1024 * 1024
    return mb * 1024 * 1024
