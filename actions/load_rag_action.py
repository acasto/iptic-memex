from __future__ import annotations

from typing import List

from base_classes import InteractionAction
from rag.fs_utils import load_rag_config
from core.provider_factory import ProviderFactory
from rag.search import search
import os
from rag.fs_utils import read_text


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
        index_names: List[str]
        if index_name:
            if index_name not in indexes:
                try:
                    self.session.ui.emit('error', {'message': f"Unknown RAG index '{index_name}'. Known: {', '.join(indexes.keys())}"})
                except Exception:
                    pass
                return False
            index_names = [index_name]
        else:
            index_names = active if active else list(indexes.keys())

        # Require explicit embedding provider and model; no fallbacks
        tools = self.session.get_tools()
        embedding_model = (tools.get('embedding_model') or '').strip()
        embedding_provider = (tools.get('embedding_provider') or '').strip()
        if not embedding_model or not embedding_provider:
            try:
                self.session.ui.emit('error', {'message': 'RAG requires [TOOLS].embedding_provider and [TOOLS].embedding_model to be set.'})
            except Exception:
                pass
            return False
        try:
            provider = ProviderFactory.instantiate_by_name(
                embedding_provider,
                registry=self.session._registry,
                session=self.session,
                isolated=True,
            )
        except Exception:
            provider = None
        if not provider or not hasattr(provider, 'embed'):
            try:
                self.session.ui.emit('error', {'message': f"Embedding provider '{embedding_provider}' is not available or lacks 'embed()'."})
            except Exception:
                pass
            return False

        # Read RAG tuning knobs from [TOOLS]
        def _get_tool_opt(name: str, default):
            try:
                return tools.get(name, default)
            except Exception:
                return default

        try:
            top_k = int(_get_tool_opt('rag_top_k', 8) or 8)
        except Exception:
            top_k = 8
        raw_cap = _get_tool_opt('rag_per_index_cap', None)
        try:
            per_index_cap = int(raw_cap) if raw_cap is not None else None
        except Exception:
            per_index_cap = None
        try:
            preview_default = int(_get_tool_opt('rag_preview_lines', 3) or 3)
        except Exception:
            preview_default = 3
        try:
            threshold = float(_get_tool_opt('rag_similarity_threshold', 0.0) or 0.0)
        except Exception:
            threshold = 0.0
        attach_mode = str(_get_tool_opt('rag_attach_mode', 'summary') or 'summary').strip().lower()
        try:
            budget = int(_get_tool_opt('rag_total_chars_budget', 20000) or 20000)
        except Exception:
            budget = 20000
        group_by_file = bool(_get_tool_opt('rag_group_by_file', True))
        merge_adjacent = bool(_get_tool_opt('rag_merge_adjacent', True))
        try:
            merge_gap = int(_get_tool_opt('rag_merge_gap', 5) or 5)
        except Exception:
            merge_gap = 5

        # Interactive query loop
        while True:
            # Ask for query
            try:
                query = self.session.ui.ask_text("Enter RAG query (or 'q' to exit): ")
            except Exception:
                query = self.session.utils.input.get_input(prompt="Enter RAG query (or 'q' to exit): ")
            if not query or query.strip().lower() == 'q':
                return True

            # Perform search
            try:
                res = search(
                    indexes=indexes,
                    names=index_names,
                    vector_db=vector_db,
                    embed_query_fn=lambda batch: provider.embed(batch, model=embedding_model),
                    query=query,
                    k=top_k,
                    preview_lines=max(0, int(preview_lines or preview_default)),
                    per_index_cap=per_index_cap,
                )
            except Exception:
                res = None
            if res is None:
                try:
                    self.session.ui.emit('error', {'message': 'Embedding error during query. Check [TOOLS].embedding_* settings.'})
                except Exception:
                    pass
                return False

            results = res.get('results', [])
            # Apply similarity threshold
            if threshold and threshold > 0.0:
                try:
                    results = [r for r in results if float(r.get('score') or 0.0) >= threshold]
                except Exception:
                    results = list(results)
            if not results:
                # Provide additional hints if indexes failed to load
                stats = res.get('stats') or {}
                total = int(stats.get('total_items') or 0)
                if total == 0:
                    indices = stats.get('indices') or []
                    lines = ["No RAG data loaded from indexes:"]
                    for st in indices:
                        reason = st.get('reason') or 'none'
                        lines.append(f"- {st.get('index')}: {reason} ({st.get('dir')})")
                    lines.append("Check [RAG] paths, file extensions (.md|.mdx|.txt|.rst), and that 'rag update' produced chunks.")
                    try:
                        self.session.ui.emit('warning', {'message': "\n".join(lines)})
                    except Exception:
                        pass
                else:
                    try:
                        self.session.ui.emit('status', {'message': 'No RAG matches. Try another query or enter q to exit.'})
                    except Exception:
                        pass
                continue

            # Optional grouping by file and merge adjacent ranges
            grouped = []
            if group_by_file:
                try:
                    from collections import defaultdict
                    by_path = defaultdict(list)
                    for r in results:
                        by_path[r['path']].append(r)
                    for path, items in by_path.items():
                        items.sort(key=lambda r: (int(r.get('line_start') or 0), -float(r.get('score') or 0.0)))
                        acc = []
                        for r in items:
                            if not acc:
                                acc.append(dict(r))
                                continue
                            prev = acc[-1]
                            gap = int(r.get('line_start') or 0) - int(prev.get('line_end') or 0)
                            if merge_adjacent and r['path'] == prev['path'] and gap >= 0 and gap <= merge_gap:
                                prev['line_end'] = max(int(prev['line_end']), int(r['line_end']))
                                prev['score'] = max(float(prev['score']), float(r['score']))
                                prev_prev = prev.get('preview') or []
                                now_prev = r.get('preview') or []
                                prev['preview'] = list(dict.fromkeys(prev_prev + now_prev))
                            else:
                                acc.append(dict(r))
                        grouped.extend(acc)
                except Exception:
                    grouped = list(results)
            else:
                grouped = list(results)
            # Re-apply top_k after grouping
            grouped.sort(key=lambda r: float(r.get('score') or 0.0), reverse=True)
            grouped = grouped[:top_k]

            # Build a readable summary block and print it
            lines: List[str] = []
            header = f"RAG results (query: {query})"
            lines.append(header)
            for i, item in enumerate(grouped, start=1):
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
                # Quick hint for non-default settings
                hint_parts = []
                if attach_mode != 'summary':
                    hint_parts.append(f"attach={attach_mode}")
                if top_k != 8:
                    hint_parts.append(f"k={top_k}")
                if per_index_cap is not None:
                    hint_parts.append(f"per_index_cap={per_index_cap}")
                if threshold and threshold > 0.0:
                    hint_parts.append(f"threshold={threshold:.2f}")
                if hint_parts:
                    out.write("(" + ", ".join(hint_parts) + ")")
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

            # Attach according to mode
            if attach_mode == 'snippets':
                # Build snippet content within budget from grouped ranges
                remaining = max(0, int(budget))
                blocks: List[str] = []
                for it in grouped:
                    try:
                        text = read_text(it['path']) or ''
                        lines_src = text.splitlines()
                        ls = max(1, int(it['line_start']))
                        le = max(ls, int(it['line_end']))
                        seg = "\n".join(lines_src[ls - 1:le])
                        if not seg.strip():
                            # Fallback to preview lines if file slice is empty
                            prev_lines = it.get('preview') or []
                            seg = "\n".join(prev_lines)
                        try:
                            base = os.path.basename(str(it['path']))
                        except Exception:
                            base = str(it['path'])
                        header = f"## {base}#L{ls}-L{le}\n"
                        blk = header + seg + "\n\n"
                        if len(blk) <= remaining:
                            blocks.append(blk)
                            remaining -= len(blk)
                        else:
                            if remaining > len(header):
                                take = remaining - len(header)
                                blocks.append(header + seg[:max(0, take)])
                                remaining = 0
                            break
                    except Exception:
                        continue
                    if remaining <= 0:
                        break
                snippet_content = "".join(blocks).strip()
                if snippet_content:
                    self.session.add_context('rag', {'name': f"RAG snippets: {query}", 'content': snippet_content})
                else:
                    # Fallback to summary if no snippet content could be assembled
                    self.session.add_context('rag', {'name': f"RAG: {query}", 'content': content})
            else:
                # Default summary block
                self.session.add_context('rag', {'name': f"RAG: {query}", 'content': content})

            # Offer to load full files (summary mode only) in blocking UIs
            if attach_mode == 'summary':
                try:
                    blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
                except Exception:
                    blocking = False
                if blocking:
                    try:
                        raw = self.session.ui.ask_text("Load any full files from results? [y/N]: ")
                    except Exception:
                        raw = self.session.utils.input.get_input(prompt="Load any full files from results? [y/N]: ")
                    ans = (raw or '').strip().lower()
                    want_load = True if ans in ('y', 'yes') else False
                    if want_load:
                        try:
                            compact = [f"{i+1:>2}. {r['path']}#L{r['line_start']}-L{r['line_end']}" for i, r in enumerate(grouped)]
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
                            to_load = list({item['path'] for item in grouped})
                        else:
                            try:
                                idxs = {int(x.strip()) for x in (choice or '').split(',') if x.strip().isdigit()}
                                for j in idxs:
                                    if 1 <= j <= len(grouped):
                                        to_load.append(grouped[j - 1]['path'])
                            except Exception:
                                to_load = []
                        if to_load:
                            try:
                                self.session.get_action('load_file').run(to_load)
                            except Exception:
                                pass

            return True
