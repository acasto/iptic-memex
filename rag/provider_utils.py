from __future__ import annotations

from typing import Optional, List


def _boolish(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or '').strip().lower()
    return s in ('1', 'true', 'yes', 'on')


def _is_strict(tools: dict) -> bool:
    """Strict defaults to True unless explicitly set false-ish."""
    if 'embedding_provider_strict' not in tools:
        return True
    return _boolish(tools.get('embedding_provider_strict'))


def get_embedding_provider(session) -> Optional[object]:
    """Select and instantiate an embedding-capable provider.

    Order of precedence:
    1) Explicit `[TOOLS].embedding_provider` if set. If `embedding_provider_strict` is true,
       only this provider is used (no fallbacks).
    2) If `[TOOLS].embedding_model` looks like a local GGUF path, prefer `llamacpp`.
    3) Current chat provider instance if it exposes `embed`.
    4) Fallbacks in order: `llamacpp`, `openairesponses`, `openai`.
    Returns a provider instance or None.
    """
    tools = session.get_tools()
    embedding_model = tools.get('embedding_model', '') or ''
    explicit = (tools.get('embedding_provider') or '').strip().lower() or None
    strict = _is_strict(tools)

    registry = session._registry

    def instantiate(name: str):
        try:
            if name.lower() == 'llamacpp':
                # Use a lightweight shim to avoid heavy chat-model init
                return _LlamaCppEmbedderShim(session, embedding_model)
            cls = registry.load_provider_class(name)
            if not cls:
                return None
            prov = cls(session)
            return prov if hasattr(prov, 'embed') else None
        except Exception:
            return None

    # 1) Explicit provider (strict by definition)
    if explicit:
        return instantiate(explicit)

    # 2) If embedding_model looks like GGUF, prefer llama.cpp
    try:
        import os
        path = os.path.expanduser(str(embedding_model))
        if path and (path.lower().endswith('.gguf') or os.path.exists(path)):
            prov = instantiate('llamacpp')
            if prov:
                return prov
    except Exception:
        pass

    # 3) Strict mode: do not guess or fallback
    if strict:
        return None

    # 4) Non-strict: try current provider then fallbacks
    current = getattr(session, 'provider', None)
    if current and hasattr(current, 'embed'):
        return current
    for name in ('llamacpp', 'openairesponses', 'openai'):
        prov = instantiate(name)
        if prov:
            return prov
    return None


def get_embedding_candidates(session) -> list[object]:
    """Return an ordered list of embedding-capable provider instances to try.

    Uses the same precedence as `get_embedding_provider`, but returns all viable
    candidates (deduped by class name) to allow graceful fallback on errors.
    """
    tools = session.get_tools()
    embedding_model = tools.get('embedding_model', '') or ''
    explicit = (tools.get('embedding_provider') or '').strip().lower() or None
    strict = _is_strict(tools)

    registry = session._registry

    def instantiate(name: str):
        try:
            if name.lower() == 'llamacpp':
                return _LlamaCppEmbedderShim(session, embedding_model)
            cls = registry.load_provider_class(name)
            if not cls:
                return None
            prov = cls(session)
            return prov if hasattr(prov, 'embed') else None
        except Exception:
            return None

    out: list[object] = []

    def add(p):
        if not p:
            return
        cls = p.__class__.__name__
        if not any(getattr(x, '__class__', type('X',(),{})) .__name__ == cls for x in out):
            out.append(p)

    # 1) Explicit (strict)
    if explicit:
        p = instantiate(explicit)
        if p:
            add(p)
        return out

    # 2) GGUF hint
    try:
        import os
        path = os.path.expanduser(str(embedding_model))
        if path and (path.lower().endswith('.gguf') or os.path.exists(path)):
            add(instantiate('llamacpp'))
    except Exception:
        pass

    # 3) Strict: do not add any further candidates
    if strict:
        return [p for p in out if p]

    # 4) Non-strict: current then fallbacks
    current = getattr(session, 'provider', None)
    if current and hasattr(current, 'embed'):
        add(current)
    for name in ('llamacpp', 'openairesponses', 'openai'):
        add(instantiate(name))
    return [p for p in out if p]


class _LlamaCppEmbedderShim:
    """Minimal llama.cpp embedder that avoids initializing the chat model.

    Uses `llama_cpp.Llama(embedding=True)` lazily with a provided GGUF path
    (from `[TOOLS].embedding_model` when it looks like a path), or falls back
    to the session's `model_path` if provided. Implements only `embed()`.
    """

    def __init__(self, session, model_hint: str | None):
        self._session = session
        self._model_hint = model_hint
        self._llm = None
        self._path = None

    def _resolve_path(self, override: Optional[str]) -> Optional[str]:
        import os
        # Prefer explicit override if it looks like a GGUF path
        if override:
            p = os.path.expanduser(str(override))
            if p.lower().endswith('.gguf') or os.path.exists(p):
                return p
        # Then the tools hint
        if self._model_hint:
            p = os.path.expanduser(str(self._model_hint))
            if p.lower().endswith('.gguf') or os.path.exists(p):
                return p
        # Finally session params
        try:
            p = self._session.get_params().get('model_path')
        except Exception:
            p = None
        return p

    def _get_llm(self, path: Optional[str]):
        if self._llm is not None and self._path == path:
            return self._llm
        from llama_cpp import Llama
        import io
        from contextlib import redirect_stderr
        params = self._session.get_params()
        n_ctx = params.get('context_size', 2048)
        n_gpu_layers = int(params.get('n_gpu_layers', -1))
        verbose = params.get('verbose', False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            self._llm = Llama(
                model_path=path,
                n_ctx=n_ctx,
                embedding=True,
                n_gpu_layers=n_gpu_layers,
                use_mlock=False,
                flash_attn=True,
                verbose=verbose,
            )
        self._path = path
        return self._llm

    def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        # Normalize inputs
        if isinstance(texts, str):
            texts = [texts]
        path = self._resolve_path(model)
        llm = self._get_llm(path)

        def _pool(v):
            if isinstance(v, list) and v and isinstance(v[0], (int, float)):
                return [float(x) for x in v]
            if isinstance(v, list) and v and isinstance(v[0], list):
                dim = len(v[0]) if v[0] else 0
                agg = [0.0] * dim
                n = 0
                for tv in v:
                    if not tv:
                        continue
                    if len(tv) != dim:
                        dim = min(dim, len(tv))
                        tv = tv[:dim]
                        agg = agg[:dim]
                    for i, val in enumerate(tv):
                        agg[i] += float(val)
                    n += 1
                return [a / n for a in agg] if n else []
            return []

        out: List[List[float]] = []
        import io
        from contextlib import redirect_stderr
        for t in texts:
            vec = None
            try:
                if hasattr(llm, 'embed'):
                    buf = io.StringIO()
                    with redirect_stderr(buf):
                        res = llm.embed(t, truncate=True)
                    vec = _pool(res)
            except Exception:
                vec = None
            if not vec:
                try:
                    buf = io.StringIO()
                    with redirect_stderr(buf):
                        resp = llm.create_embedding(t)
                    if isinstance(resp, dict) and 'data' in resp:
                        data = resp.get('data') or []
                        if data:
                            vec = [float(x) for x in (data[0].get('embedding') or [])]
                    else:
                        vec = _pool(resp)
                except Exception:
                    vec = None
            if not vec:
                raise RuntimeError('Failed to compute embedding with llama.cpp')
            out.append(vec)
        return out
