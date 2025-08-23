from __future__ import annotations

from typing import List

from base_classes import InteractionAction
from rag.fs_utils import load_rag_config
from rag.search import search
from rag.provider_utils import get_embedding_provider, get_embedding_candidates


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

        # Choose embedding provider for query (dedicated instance; honors [TOOLS].embedding_provider)
        prov = get_embedding_provider(self.session)
        if not prov:
            try:
                self.session.ui.emit('error', {'message': 'No embedding-capable provider available for query embedding. Configure [TOOLS].embedding_provider and [TOOLS].embedding_model (e.g., embedding_provider = LlamaCpp + embedding_model = /path/model.gguf, or embedding_provider = OpenAI + embedding_model = text-embedding-3-small). Set embedding_provider_strict = false to allow fallbacks.'})
            except Exception:
                pass
            return False

        # Interactive query loop
        while True:
            # Ask for query
            try:
                query = self.session.ui.ask_text("Enter RAG query (or 'q' to exit): ")
            except Exception:
                query = self.session.utils.input.get_input(prompt="Enter RAG query (or 'q' to exit): ")
            if not query or query.strip().lower() == 'q':
                return True

            # Perform search with graceful fallback across candidate providers
            last_err = None
            candidates = get_embedding_candidates(self.session)
            # If strict mode produced no candidates, try the explicit provider only
            if not candidates and prov:
                candidates = [prov]
            for candidate in candidates:
                try:
                    res = search(
                        indexes=indexes,
                        names=names,
                        vector_db=vector_db,
                        embed_query_fn=lambda batch, _c=candidate: _c.embed(batch, model=embedding_model or 'text-embedding-3-small'),
                        query=query,
                        k=8,
                        preview_lines=max(0, int(preview_lines)),
                        per_index_cap=None,
                    )
                    break
                except Exception as e:
                    last_err = e
                    res = None
                    continue
            if res is None:
                try:
                    self.session.ui.emit('error', {'message': f'Embedding error during query: {last_err}'})
                except Exception:
                    pass
                return False

            results = res.get('results', [])
            if not results:
                try:
                    self.session.ui.emit('status', {'message': 'No RAG matches. Try another query or enter q to exit.'})
                except Exception:
                    pass
                continue

            # Build a readable summary block and print it
            lines: List[str] = []
            header = f"RAG results (query: {query})"
            lines.append(header)
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
                        lines.append(f"      {pl}")
                lines.append("")
            content = "\n".join(lines)

            # Print summary before prompting
            try:
                out = self.session.utils.output
                out.write()
                out.write(content.rstrip())
                out.write()
            except Exception:
                # Fallback: single status emission
                try:
                    self.session.ui.emit('status', {'message': content})
                except Exception:
                    pass

            # Confirm use of results before adding to context
            try:
                raw_use = self.session.ui.ask_text("Use these results? [Y/n]: ")
            except Exception:
                raw_use = self.session.utils.input.get_input(prompt="Use these results? [Y/n]: ")
            use_ans = (raw_use or '').strip().lower()
            use_results = False if use_ans in ('n', 'no') else True
            if not use_results:
                # Loop back to a new query
                continue

            # Add to context (consolidated block)
            self.session.add_context('rag', {'name': f"RAG: {query}", 'content': content})

            # Offer to load full files in blocking UIs
            try:
                blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
            except Exception:
                blocking = False
            if blocking:
                # Ask via text to ensure blank Enter respects default 'n'
                try:
                    raw = self.session.ui.ask_text("Load any full files from results? [y/N]: ")
                except Exception:
                    raw = self.session.utils.input.get_input(prompt="Load any full files from results? [y/N]: ")
                ans = (raw or '').strip().lower()
                want_load = True if ans in ('y', 'yes') else False
                if want_load:
                    # Echo compact list to assist selection
                    try:
                        compact = [f"{i+1:>2}. {r['path']}" for i, r in enumerate(results)]
                        self.session.utils.output.write("\nSelect files to load:")
                        for line in compact:
                            self.session.utils.output.write(line)
                        self.session.utils.output.write()
                    except Exception:
                        pass
                    try:
                        choice = self.session.ui.ask_text('Enter indices (e.g., 1,3) or "all":')
                    except Exception:
                        choice = self.session.utils.input.get_input(prompt='Enter indices (e.g., 1,3) or "all": ')
                    to_load: List[str] = []
                    if (choice or '').strip().lower() == 'all':
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

            return True
