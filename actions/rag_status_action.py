from __future__ import annotations

from typing import List, Dict, Any
from base_classes import InteractionAction
from rag.fs_utils import load_rag_config
from rag.vector_store import NaiveStore
import os


class RagStatusAction(InteractionAction):
    """Show status for configured RAG indexes and their artifacts.

    Usage:
      - rag status          -> show all active (or all defined) indexes
      - rag status <name>   -> show only the named index
    """

    def __init__(self, session):
        self.session = session

    @staticmethod
    def can_run(session) -> bool:
        return True

    def _format_status_lines(self, rows: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        lines.append("RAG Index Status")
        for r in rows:
            ok = r.get('ok')
            idx = r.get('index')
            lines.append(f"- {idx} [{ 'ok' if ok else 'needs update' }]")
            lines.append(f"  root: {r.get('root')}")
            lines.append(f"  dir:  {r.get('dir')}")
            if not r.get('exists'):
                lines.append("  note: artifacts missing (run 'rag update')")
                continue
            if r.get('error'):
                lines.append(f"  error: {r['error']}")
            if r.get('manifest'):
                m = r['manifest']
                lines.append(f"  updated: {m.get('updated')}  created: {m.get('created')}")
                lines.append(f"  model:   {m.get('embedding_signature') or m.get('embedding_model')}")
                counts = m.get('counts') or {}
                lines.append(f"  counts:  files={counts.get('files', 0)} chunks={counts.get('chunks', 0)}")
                lines.append(f"  dim:     {m.get('vector_dim')}")
            # Consistency info
            lines.append(f"  loaded:  chunks={r.get('chunks_loaded', 0)} embeddings={r.get('embeddings_loaded', 0)}")
            if r.get('chunks_loaded') != r.get('embeddings_loaded'):
                lines.append("  note: chunk/embedding count mismatch (re-run 'rag update')")
        return lines

    def run(self, args: List[str] | None = None):
        args = args or []
        target = args[0] if args else None
        indexes, active, vector_db, _ = load_rag_config(self.session)

        if not indexes:
            try:
                self.session.ui.emit('error', {'message': 'No [RAG] indexes configured. Add entries like notes=/path in config.ini.'})
            except Exception:
                pass
            return False

        names: List[str]
        if target:
            if target not in indexes:
                try:
                    self.session.ui.emit('error', {'message': f"Unknown RAG index '{target}'. Known: {', '.join(indexes.keys())}"})
                except Exception:
                    pass
                return False
            names = [target]
        else:
            names = active if active else list(indexes.keys())

        rows: List[Dict[str, Any]] = []
        for name in names:
            root = indexes[name]
            index_dir = os.path.join(os.path.expanduser(vector_db), name)
            store = NaiveStore(vector_db, name)
            exists = os.path.isdir(index_dir)
            row: Dict[str, Any] = {
                'index': name,
                'root': root,
                'dir': index_dir,
                'exists': exists,
                'ok': False,
                'chunks_loaded': 0,
                'embeddings_loaded': 0,
            }
            if exists:
                try:
                    m = store.read_manifest()
                    row['manifest'] = m or {}
                except Exception as e:
                    row['error'] = f"manifest: {e}"
                try:
                    chunks = store.read_chunks()
                except Exception as e:
                    chunks = []
                    row['error'] = f"chunks: {e}"
                try:
                    embs = store.read_embeddings()
                except Exception as e:
                    embs = []
                    row['error'] = f"embeddings: {e}"
                row['chunks_loaded'] = len(chunks or [])
                row['embeddings_loaded'] = len(embs or [])
                row['ok'] = bool(chunks and embs and len(chunks) == len(embs))

            rows.append(row)

        # Emit nicely formatted status block
        lines = self._format_status_lines(rows)
        try:
            out = self.session.utils.output
            out.write()
            for line in lines:
                out.write(line)
            out.write()
        except Exception:
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass
        return True

