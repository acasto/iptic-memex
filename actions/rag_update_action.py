from __future__ import annotations

from base_classes import InteractionAction
from typing import List

from rag.fs_utils import load_rag_config
from rag.indexer import update_index
from rag.provider_utils import get_embedding_provider
import os


class RagUpdateAction(InteractionAction):
    """User command to build or refresh RAG indexes.

    Usage:
      - rag update           -> update all active (or all defined) indexes
      - rag update <name>    -> update only the named index
    """

    def __init__(self, session):
        self.session = session

    @staticmethod
    def can_run(session) -> bool:
        # Always available for user command; embedding may error later if no provider
        return True

    def _get_embedding_provider(self):
        return get_embedding_provider(self.session)

    def run(self, args: List[str] | None = None):
        args = args or []
        target = args[0] if args else None
        indexes, active, vector_db, embedding_model = load_rag_config(self.session)

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

        prov = self._get_embedding_provider()
        if prov is None or not hasattr(prov, 'embed'):
            try:
                self.session.ui.emit('error', {'message': 'No embedding-capable provider available. Configure [TOOLS].embedding_provider and [TOOLS].embedding_model (e.g., embedding_provider = LlamaCpp + embedding_model = /path/model.gguf, or embedding_provider = OpenAI + embedding_model = text-embedding-3-small). Set embedding_provider_strict = false to allow fallbacks.'})
            except Exception:
                pass
            return False

        if not embedding_model:
            try:
                self.session.ui.emit('warning', {'message': 'No [TOOLS].embedding_model configured; defaulting to text-embedding-3-small.'})
            except Exception:
                pass

        # Ensure vector_db exists
        try:
            self.session.utils.fs.ensure_directory(vector_db)
        except Exception:
            pass

        # Process each index
        for name in names:
            root = indexes[name]
            try:
                self.session.ui.emit('status', {'message': f"Indexing {name}: {root}"})
            except Exception:
                pass
            # Build an embedding signature to avoid mixing vector backends/dims
            sig = {
                'provider': prov.__class__.__name__,
                'embedding_id': embedding_model,
            }
            # If embedding_model looks like a local model path, record metadata
            if embedding_model:
                path = os.path.expanduser(str(embedding_model))
                if os.path.exists(path) and path.lower().endswith('.gguf'):
                    try:
                        st = os.stat(path)
                        sig.update({
                            'model_path': path,
                            'model_mtime': int(st.st_mtime),
                            'model_size': int(st.st_size),
                        })
                    except Exception:
                        pass
            stats = update_index(
                index_name=name,
                root_path=root,
                vector_db=vector_db,
                embed_fn=lambda batch: prov.embed(batch, model=embedding_model),
                embedding_model=embedding_model or 'text-embedding-3-small',
                embedding_signature=sig,
            )
            try:
                skipped = stats.get('skipped')
                if skipped:
                    self.session.ui.emit('status', {'message': f"Up to date {name}: files={stats['files']} chunks={stats['chunks']} -> {stats['index_dir']}"})
                else:
                    self.session.ui.emit('status', {'message': f"Indexed {name}: files={stats['files']} chunks={stats['chunks']} embedded={stats['embedded']} -> {stats['index_dir']}"})
            except Exception:
                pass

        return True
