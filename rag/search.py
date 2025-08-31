from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Tuple, Any


def _load_index(index_dir: str) -> Tuple[List[Dict[str, Any]], List[List[float]]]:
    chunks_path = os.path.join(index_dir, 'chunks.jsonl')
    emb_path = os.path.join(index_dir, 'embeddings.json')
    chunks: List[Dict[str, Any]] = []
    try:
        with open(chunks_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    chunks.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        return [], []
    try:
        with open(emb_path, 'r', encoding='utf-8') as f:
            embeddings: List[List[float]] = json.load(f)
    except FileNotFoundError:
        return [], []
    return chunks, embeddings


def _normalize(vec: List[float]) -> List[float]:
    s = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x / s for x in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    # assumes both normalized
    return sum(x*y for x, y in zip(a, b))


def _char_to_line_range(path: str, start: int, end: int, preview_lines: int) -> Tuple[int, int, List[str]]:
    """Map char offsets to line numbers and extract a preview window.

    Returns (line_start, line_end, lines_preview). Lines are 1-based inclusive.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    except Exception:
        return 1, 1, []
    # Compute line breaks
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == '\n':
            line_starts.append(i + 1)
    line_starts.append(len(text))
    # Find line numbers covering [start, end)
    def find_line(pos: int) -> int:
        # binary search
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if line_starts[mid] <= pos < line_starts[mid + 1]:
                return mid + 1  # 1-based
            if pos < line_starts[mid]:
                hi = mid
            else:
                lo = mid + 1
        return max(1, min(len(line_starts) - 1, lo + 1))
    ls = find_line(start)
    le = find_line(end)
    # Build preview window
    if preview_lines and preview_lines > 0:
        pstart = max(1, ls - preview_lines)
        pend = min(len(line_starts) - 1, le + preview_lines)
        # slice lines
        lines = text.splitlines()
        snippet = lines[pstart - 1:pend]
    else:
        snippet = []
    return ls, le, snippet


def search(
    *,
    indexes: Dict[str, str],
    names: List[str],
    vector_db: str,
    embed_query_fn,
    query: str,
    k: int = 8,
    preview_lines: int = 0,
    per_index_cap: int | None = None,
) -> Dict[str, Any]:
    """Search across provided index names; return ranked results with previews.

    Returns dict with 'query', 'results' list where each result has:
      { 'score': float, 'path': str, 'line_start': int, 'line_end': int, 'index': str, 'preview': [lines] }
    """
    # Load all vectors
    all_items: List[Tuple[str, Dict[str, Any], List[float]]] = []
    index_status: List[Dict[str, Any]] = []
    for name in names:
        index_dir = os.path.join(os.path.expanduser(vector_db), name)
        chunks, embs = _load_index(index_dir)
        if not chunks or not embs:
            index_status.append({'index': name, 'dir': index_dir, 'loaded': 0, 'reason': 'missing'})
            continue
        if len(chunks) != len(embs):
            # Skip malformed index
            index_status.append({'index': name, 'dir': index_dir, 'loaded': 0, 'reason': 'mismatch'})
            continue
        for ch, vec in zip(chunks, embs):
            all_items.append((name, ch, vec))
        index_status.append({'index': name, 'dir': index_dir, 'loaded': len(chunks), 'reason': None})

    if not all_items:
        return {"query": query, "results": [], "stats": {"total_items": 0, "indices": index_status, "vector_db": vector_db}}

    # Normalize vectors
    norm_items = [(name, ch, _normalize(vec)) for (name, ch, vec) in all_items]
    # Embed query
    q = embed_query_fn([query])[0]
    qn = _normalize(q)

    # Score
    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    for name, ch, vec in norm_items:
        s = _cosine(qn, vec)
        scored.append((s, name, ch))

    # Sort desc by score
    scored.sort(key=lambda x: x[0], reverse=True)

    # Optional per-index cap
    if per_index_cap:
        capped: List[Tuple[float, str, Dict[str, Any]]] = []
        counts: Dict[str, int] = {}
        for s, name, ch in scored:
            if counts.get(name, 0) < per_index_cap:
                capped.append((s, name, ch))
                counts[name] = counts.get(name, 0) + 1
        scored = capped

    top = scored[:k]
    out: List[Dict[str, Any]] = []
    for s, name, ch in top:
        preview_path = ch.get('path')
        display_path = ch.get('source_path', preview_path)
        ls, le, snippet = _char_to_line_range(preview_path, int(ch.get('start', 0)), int(ch.get('end', 0)), preview_lines)
        out.append({
            'score': round(float(s), 4),
            'path': display_path,
            'line_start': ls,
            'line_end': le,
            'index': name,
            'preview': snippet,
        })

    return {"query": query, "results": out, "stats": {"total_items": len(all_items), "indices": index_status, "vector_db": vector_db}}
