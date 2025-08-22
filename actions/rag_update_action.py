from __future__ import annotations

from base_classes import InteractionAction
from typing import List

from rag.fs_utils import load_rag_config
from rag.indexer import update_index


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
        # Prefer current session provider if it supports embeddings
        provider = getattr(self.session, 'provider', None)
        if provider and hasattr(provider, 'embed'):
            try:
                # Probe minimal to ensure method exists; do not call network
                getattr(provider, 'embed')
                return provider
            except Exception:
                pass
        # Try overriding provider via tools.embedding_provider
        name = str(self.session.get_tools().get('embedding_provider') or '').strip().lower()
        if name:
            provider_class = self.session._registry.load_provider_class(name)
            if provider_class:
                try:
                    prov = provider_class(self.session)
                    if hasattr(prov, 'embed'):
                        return prov
                except Exception:
                    pass
        # Fallback: if current model's provider class is OpenAIResponses or OpenAI, use it
        for fallback in ('openairesponses', 'openai'):
            try:
                provider_class = self.session._registry.load_provider_class(fallback)
                if provider_class:
                    prov = provider_class(self.session)
                    if hasattr(prov, 'embed'):
                        return prov
            except Exception:
                continue
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

        prov = self._get_embedding_provider()
        if prov is None or not hasattr(prov, 'embed'):
            try:
                self.session.ui.emit('error', {'message': 'No embedding-capable provider available. Configure [TOOLS].embedding_provider or use an OpenAI model.'})
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
            stats = update_index(
                index_name=name,
                root_path=root,
                vector_db=vector_db,
                embed_fn=lambda batch: prov.embed(batch, model=embedding_model),
                embedding_model=embedding_model or 'text-embedding-3-small',
            )
            try:
                self.session.ui.emit('status', {'message': f"Indexed {name}: files={stats['files']} chunks={stats['chunks']} embedded={stats['embedded']} -> {stats['index_dir']}"})
            except Exception:
                pass

        return True

