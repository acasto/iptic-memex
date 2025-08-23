from __future__ import annotations

from base_classes import InteractionAction
from typing import List

from rag.fs_utils import load_rag_config
from rag.indexer import update_index
from typing import Optional
import os


class RagUpdateAction(InteractionAction):
    """User command to build or refresh RAG indexes.

    Usage:
      - rag update           -> update all active (or all defined) indexes
      - rag update <name>    -> update only the named index
    """

    def __init__(self, session):
        self.session = session
        self._env = __import__('os').environ

    @staticmethod
    def can_run(session) -> bool:
        # Always available for user command; embedding may error later if no provider
        return True

    def _looks_like_gguf(self, path: str) -> bool:
        try:
            p = os.path.expanduser(str(path or ''))
            return bool(p) and (p.lower().endswith('.gguf') or os.path.exists(p))
        except Exception:
            return False

    def _resolve_embedder(self) -> Optional[object]:
        tools = self.session.get_tools()
        embedding_model = tools.get('embedding_model') or ''
        explicit = (tools.get('embedding_provider') or '').strip()
        strict_raw = tools.get('embedding_provider_strict')
        strict = True if strict_raw is None else bool(strict_raw)

        names: list[str] = []
        if explicit:
            names.append(explicit)
        elif self._looks_like_gguf(embedding_model):
            names.append('LlamaCpp')

        if not strict:
            # Current chat provider, if any
            try:
                current = self.session.get_params().get('provider')
                if current:
                    names.append(str(current))
            except Exception:
                pass
            # Add known providers or any active provider sections
            try:
                for sec in self.session.config.base_config.sections():
                    if sec not in names:
                        names.append(sec)
            except Exception:
                pass

        # Try to instantiate in order
        for name in names:
            prov = self.session._registry.create_provider(name)
            if prov and hasattr(prov, 'embed'):
                return prov
        return None

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

        prov = self._resolve_embedder()
        if prov is None or not hasattr(prov, 'embed'):
            err = getattr(self.session._registry, '_provider_factory_last_error', None)
            if isinstance(err, dict) and err.get('name') and err.get('error'):
                msg = f"Embedding provider '{err['name']}' failed to initialize: {err['error']}. Check credentials/config for that provider."
            else:
                msg = 'No embedding-capable provider available. Configure [TOOLS].embedding_provider and [TOOLS].embedding_model, or set embedding_provider_strict = false to allow fallbacks.'
            try:
                self.session.ui.emit('error', {'message': msg})
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
