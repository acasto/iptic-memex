from __future__ import annotations

from typing import Any, Dict, List, Optional

from base_classes import InteractionAction
from rag.fs_utils import load_rag_config, read_text
from core.provider_factory import ProviderFactory
from rag.search import search


class AssistantRagToolAction(InteractionAction):
    """
    Assistant tool: run a semantic search over configured RAG indexes and attach
    a readable summary block to the 'rag' context for the next assistant turn.

    Inputs (args):
      - query (str, required): search query text (falls back to content)
      - index (str, optional): single index name
      - indexes (str, optional): comma-separated index names
      - k (int, optional): top-K results (default from [TOOLS].rag_top_k)
      - preview_lines (int, optional): lines per hit (default from [TOOLS].rag_preview_lines)
      - per_index_cap (int, optional): cap per index
      - threshold (float, optional): similarity threshold to filter results

    Notes:
      - Requires [TOOLS].embedding_provider and [TOOLS].embedding_model.
      - Attaches a single consolidated summary under the 'rag' context.
    """

    def __init__(self, session):
        self.session = session

    @staticmethod
    def can_run(session) -> bool:
        return True

    def run(self, args: Dict[str, Any], content: str = ""):
        args = args or {}

        # Resolve query
        query = (args.get('query') or content or '').strip()
        if not query:
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': 'RAGSEARCH: missing query.'
            })
            return

        # Load config and resolve which indexes to search
        indexes, active, vector_db, _ = load_rag_config(self.session)
        if not indexes:
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': 'RAGSEARCH: no [RAG] indexes configured.'
            })
            return

        index_names: List[str]
        chosen = (args.get('index') or '').strip()
        chosen_multi = (args.get('indexes') or '').strip()
        if chosen_multi:
            cand = [x.strip() for x in chosen_multi.split(',') if x.strip()]
            index_names = [n for n in cand if n in indexes]
        elif chosen:
            index_names = [chosen] if chosen in indexes else []
        else:
            index_names = active if active else list(indexes.keys())

        if not index_names:
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': 'RAGSEARCH: no valid index names selected.'
            })
            return

        # Embedding provider + model (explicit only; no fallback)
        tools = self.session.get_tools()
        embedding_model = (tools.get('embedding_model') or '').strip()
        embedding_provider = (tools.get('embedding_provider') or '').strip()
        if not embedding_model or not embedding_provider:
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': 'RAGSEARCH: set [TOOLS].embedding_provider and [TOOLS].embedding_model first.'
            })
            return

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
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': f"RAGSEARCH: embedding provider '{embedding_provider}' unavailable or lacks embed()."
            })
            return

        # Tuning knobs (defaults from [TOOLS], allow overrides via args)
        def _tool_opt(name: str, default):
            try:
                return tools.get(name, default)
            except Exception:
                return default

        def _int(v, d):
            try:
                return int(v)
            except Exception:
                return d

        def _float(v, d):
            try:
                return float(v)
            except Exception:
                return d

        top_k = _int(args.get('k', None), _int(_tool_opt('rag_top_k', 8) or 8, 8))
        preview_default = _int(_tool_opt('rag_preview_lines', 3) or 3, 3)
        preview_lines = _int(args.get('preview_lines', None), preview_default)
        pic_raw = args.get('per_index_cap', None)
        per_index_cap = None if pic_raw is None else _int(pic_raw, None)
        threshold = _float(args.get('threshold', None), _float(_tool_opt('rag_similarity_threshold', 0.0) or 0.0, 0.0))

        # Execute search
        try:
            res = search(
                indexes=indexes,
                names=index_names,
                vector_db=vector_db,
                embed_query_fn=lambda batch: provider.embed(batch, model=embedding_model),
                query=query,
                k=max(1, top_k),
                preview_lines=max(0, preview_lines),
                per_index_cap=per_index_cap,
            )
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'rag_error',
                'content': f'RAGSEARCH failed: {e}'
            })
            return

        results = list(res.get('results', []) or [])
        # Apply threshold if set
        try:
            if threshold and threshold > 0.0:
                results = [r for r in results if float(r.get('score') or 0.0) >= threshold]
        except Exception:
            pass

        # Group by file and merge adjacent line ranges (mirror LoadRagAction defaults)
        group_by_file = bool(_tool_opt('rag_group_by_file', True))
        merge_adjacent = bool(_tool_opt('rag_merge_adjacent', True))
        merge_gap = _int(_tool_opt('rag_merge_gap', 5) or 5, 5)

        grouped: List[Dict[str, Any]] = []
        if group_by_file:
            try:
                from collections import defaultdict
                by_path: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for r in results:
                    by_path[r['path']].append(r)
                for path, items in by_path.items():
                    items.sort(key=lambda r: (int(r.get('line_start') or 0), -float(r.get('score') or 0.0)))
                    acc: List[Dict[str, Any]] = []
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
        grouped = grouped[: max(1, top_k)]

        # Build readable summary block
        lines: List[str] = []
        header = f"RAG results (query: {query})"
        lines.append(header)
        for i, item in enumerate(grouped, start=1):
            try:
                score = float(item.get('score') or 0.0)
            except Exception:
                score = 0.0
            path = item.get('path')
            ls = int(item.get('line_start') or 0)
            le = int(item.get('line_end') or 0)
            idx = item.get('index')
            lines.append(f"{i:>2}. [{score:.3f}] ({idx}) {path}#L{ls}-L{le}")
            prev_lines = item.get('preview') or []
            for pl in prev_lines:
                try:
                    lines.append(f"      {pl}")
                except Exception:
                    continue
            lines.append("")
        content_block = "\n".join(lines)

        # Attach to 'rag' context for grounding
        self.session.add_context('rag', {'name': f"RAG: {query}", 'content': content_block})

        # Optional: brief assistant feedback for visibility
        try:
            self.session.add_context('assistant', {
                'name': 'assistant_feedback',
                'content': f"Attached RAG results for '{query}' ({len(grouped)} items)."
            })
        except Exception:
            pass

