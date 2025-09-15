from __future__ import annotations

from base_classes import InteractionAction
from typing import List

from rag.fs_utils import load_rag_config, load_rag_filters, load_rag_exts, load_rag_max_bytes
from core.provider_factory import ProviderFactory
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
        # Gate RAG commands behind [RAG].active similar to MCP
        try:
            return bool(session.get_option('RAG', 'active', fallback=False))
        except Exception:
            return False

    def _resolve_embedder(self) -> Optional[object]:
        tools = self.session.get_tools()
        embedding_provider = (tools.get('embedding_provider') or '').strip()
        embedding_model = (tools.get('embedding_model') or '').strip()
        if not embedding_provider or not embedding_model:
            return None
        try:
            prov = ProviderFactory.instantiate_by_name(
                embedding_provider,
                registry=self.session._registry,
                session=self.session,
                isolated=True,
            )
        except Exception:
            return None
        return prov if hasattr(prov, 'embed') else None

    def run(self, args: List[str] | None = None):
        args = args or []
        target = args[0] if args else None
        indexes, active, vector_db, embedding_model = load_rag_config(self.session)
        if not vector_db:
            try:
                self.session.ui.emit('error', {'message': "RAG requires [RAG].vector_db to be set."})
            except Exception:
                pass
            return False
        filters = load_rag_filters(self.session)
        exts = load_rag_exts(self.session)
        max_bytes = load_rag_max_bytes(self.session)

        if not indexes:
            try:
                self.session.ui.emit('error', {'message': "No [RAG] indexes configured. Define [RAG].indexes and per-index sections like [RAG.notes] with path=... in config.ini."})
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
        if prov is None:
            try:
                self.session.ui.emit('error', {'message': 'RAG requires [TOOLS].embedding_provider and [TOOLS].embedding_model to be set.'})
            except Exception:
                pass
            return False

        # Ensure vector_db exists
        try:
            self.session.utils.fs.ensure_directory(vector_db)
        except Exception:
            pass

        # Log update start
        try:
            self.session.utils.logger.rag_event('update_begin', {
                'indexes': list(indexes.keys()),
                'active': list(active or []),
                'vector_db': vector_db,
                'target': target,
            }, component='rag.update')
        except Exception:
            pass

        # Process each index
        for name in names:
            root = indexes[name]
            try:
                inc = filters.get(name, {}).get('include') if filters else None
                exc = filters.get(name, {}).get('exclude') if filters else None
                info_bits = []
                if inc:
                    info_bits.append(f"include={len(inc)}")
                if exc:
                    info_bits.append(f"exclude={len(exc)}")
                suffix = f" (" + ", ".join(info_bits) + ")" if info_bits else ""
                ext_suffix = f", exts={len(exts)}" if exts else ""
                size_mb = max_bytes // (1024 * 1024)
                size_suffix = f", max_file_mb={size_mb}"
                self.session.ui.emit('status', {'message': f"Indexing {name}: {root}{suffix}{ext_suffix}{size_suffix}"})
            except Exception:
                pass
            # Build an embedding signature to avoid mixing vector backends/dims
            sig = {
                'provider': prov.__class__.__name__,
                'embedding_id': embedding_model,
            }
            try:
                self.session.utils.logger.rag_event('index_begin', {'name': name, 'root': root}, component='rag.update')
            except Exception:
                pass
            stats = update_index(
                index_name=name,
                root_path=root,
                vector_db=vector_db,
                embed_fn=lambda batch: prov.embed(batch, model=embedding_model),
                embedding_model=embedding_model,
                embedding_signature=sig,
                include_globs=(filters.get(name, {}).get('include') if filters else None),
                exclude_globs=(filters.get(name, {}).get('exclude') if filters else None),
                exts=exts,
                max_bytes=max_bytes,
            )
            try:
                skipped = stats.get('skipped')
                if skipped:
                    self.session.ui.emit('status', {'message': f"Up to date {name}: files={stats['files']} chunks={stats['chunks']} -> {stats['index_dir']}"})
                else:
                    self.session.ui.emit('status', {'message': f"Indexed {name}: files={stats['files']} chunks={stats['chunks']} embedded={stats['embedded']} -> {stats['index_dir']}"})
                try:
                    self.session.utils.logger.rag_event('index_done', {'name': name, **stats}, component='rag.update')
                except Exception:
                    pass
            except Exception:
                pass

        return True
