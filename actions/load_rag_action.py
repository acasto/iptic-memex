from __future__ import annotations

from typing import List

from base_classes import InteractionAction
from rag.fs_utils import load_rag_config
from rag.search import search


class LoadRagAction(InteractionAction):
    """User command to query RAG indexes and load a result summary into context.

    Usage:
      - load rag              -> query across active/all indexes
      - load rag <name>       -> query a specific index

    Extras:
      - Preview lines: controlled by optional numeric arg, e.g., "load rag 3".
        If both an index and a number are provided, use the first arg as index and
        second as preview lines.
      - After displaying results, in blocking UIs offer to load one or more full files
        via the existing 'load file' command.
    """

    def __init__(self, session):
        self.session = session

    @staticmethod
    def can_run(session) -> bool:
        return True

    def run(self, args: List[str] | None = None):
        args = args or []
        # Parse args: optional index name and/or preview lines number
        index_name = None
        preview_lines = 0
        if len(args) == 1:
            # Could be index or number
            a0 = args[0]
            if a0.isdigit():
                preview_lines = int(a0)
            else:
                index_name = a0
        elif len(args) >= 2:
            # First index, second number if numeric
            index_name = args[0]
            try:
                preview_lines = int(args[1])
            except Exception:
                preview_lines = 0

        # Ask for query
        try:
            query = self.session.ui.ask_text("Enter RAG query (or 'q' to exit): ")
        except Exception:
            query = self.session.utils.input.get_input(prompt="Enter RAG query (or 'q' to exit): ")
        if not query or query.strip().lower() == 'q':
            return False

        indexes, active, vector_db, embedding_model = load_rag_config(self.session)
        if not indexes:
            try:
                self.session.ui.emit('error', {'message': 'No [RAG] indexes configured.'})
            except Exception:
                pass
            return False
        names: List[str]
        if index_name:
            if index_name not in indexes:
                try:
                    self.session.ui.emit('error', {'message': f"Unknown RAG index '{index_name}'. Known: {', '.join(indexes.keys())}"})
                except Exception:
                    pass
                return False
            names = [index_name]
        else:
            names = active if active else list(indexes.keys())

        # Choose embedding provider for query
        prov = getattr(self.session, 'provider', None)
        if not prov or not hasattr(prov, 'embed'):
            # Try fallback providers if needed
            for fallback in ('openairesponses', 'openai'):
                try:
                    pclass = self.session._registry.load_provider_class(fallback)
                    if pclass:
                        prov = pclass(self.session)
                        if hasattr(prov, 'embed'):
                            break
                except Exception:
                    continue
        if not prov or not hasattr(prov, 'embed'):
            try:
                self.session.ui.emit('error', {'message': 'No embedding-capable provider available for query embedding.'})
            except Exception:
                pass
            return False

        # Perform search
        res = search(
            indexes=indexes,
            names=names,
            vector_db=vector_db,
            embed_query_fn=lambda batch: prov.embed(batch, model=embedding_model or 'text-embedding-3-small'),
            query=query,
            k=8,
            preview_lines=max(0, int(preview_lines)),
            per_index_cap=None,
        )

        results = res.get('results', [])
        if not results:
            try:
                self.session.ui.emit('status', {'message': 'No RAG matches.'})
            except Exception:
                pass
            return True

        # Build a readable summary block
        lines: List[str] = []
        lines.append(f"RAG results (query: {query})\n")
        for i, item in enumerate(results, start=1):
            score = item['score']
            path = item['path']
            ls = item['line_start']
            le = item['line_end']
            idx = item['index']
            lines.append(f"{i:>2}. [{score:.3f}] ({idx}) {path}#L{ls}-L{le}")
            prev = item.get('preview') or []
            if prev:
                for pl in prev:
                    # indent preview lines
                    lines.append(f"      {pl}")
            lines.append("")
        content = "\n".join(lines)

        # Add to context
        self.session.add_context('rag', {'name': f"RAG: {query}", 'content': content})
        try:
            self.session.ui.emit('status', {'message': f"Loaded RAG results into context ({len(results)} hits)."})
        except Exception:
            pass

        # Offer to load full files in blocking UIs
        try:
            blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
        except Exception:
            blocking = False
        if blocking:
            try:
                if self.session.ui.ask_bool('Load any full files from results?', default=False):
                    choice = self.session.ui.ask_text('Enter indices (e.g., 1,3) or "all":')
                    to_load: List[str] = []
                    if choice and choice.strip().lower() == 'all':
                        to_load = list({item['path'] for item in results})
                    else:
                        try:
                            idxs = {int(x.strip()) for x in (choice or '').split(',') if x.strip().isdigit()}
                            for j in idxs:
                                if 1 <= j <= len(results):
                                    to_load.append(results[j - 1]['path'])
                        except Exception:
                            to_load = []
                    if to_load:
                        try:
                            self.session.get_action('load_file').run(to_load)
                        except Exception:
                            pass
            except Exception:
                pass

        return True

