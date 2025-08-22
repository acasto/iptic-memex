from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


class NaiveStore:
    """Minimal on-disk layout for per-index vectors.

    MVP writes whole index artifacts on update (rebuild). Incremental
    updates can be added later without changing the on-disk contract.
    """

    def __init__(self, base_dir: str, index_name: str) -> None:
        self.base_dir = os.path.expanduser(base_dir)
        self.index_name = index_name
        self.index_dir = os.path.join(self.base_dir, index_name)
        self.manifest_path = os.path.join(self.index_dir, 'manifest.json')
        self.chunks_path = os.path.join(self.index_dir, 'chunks.jsonl')
        self.embeddings_path = os.path.join(self.index_dir, 'embeddings.json')

    def ensure_dirs(self) -> None:
        Path(self.index_dir).mkdir(parents=True, exist_ok=True)

    def write(self, *, manifest: Dict[str, Any], chunks: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        self.ensure_dirs()
        # Manifest
        with open(self.manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        # Chunks JSONL
        with open(self.chunks_path, 'w', encoding='utf-8') as f:
            for ch in chunks:
                f.write(json.dumps(ch, ensure_ascii=False) + "\n")
        # Embeddings (JSON for MVP; consider .npy/memmap later)
        with open(self.embeddings_path, 'w', encoding='utf-8') as f:
            json.dump(embeddings, f)

