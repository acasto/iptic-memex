# RAG System

A small, self-contained retrieval system that indexes user-configured folders and lets you run ad-hoc semantic searches to load results into chat context. Actions remain thin; this module encapsulates discovery, chunking, embeddings, on-disk storage, and search.

## Overview
- User commands (core):
  - `rag update [index?]`: build/refresh one or more indexes.
  - `load rag [index?] [preview_lines?]`: search indexes, add a readable results block to context, optionally load full files.
  - `rag status [index?]`: show per-index status (paths, counts, vector dim, last updated) and consistency checks.
- Read-only against indexed folders; all artifacts live under `vector_db/<index>/`.
- Default backend is a naive cosine similarity without new deps (implemented in pure Python here); FAISS/sqlite-vec planned as optional backends.

## Why a separate module?
- Cohesion: discovery, chunking, embeddings, storage, and search evolve together.
- Extensibility: clean place for alternative backends (FAISS, sqlite-vec), incremental updates, file-type extractors.
- Stability: actions are slim adapters; RAG internals can improve without changing command surfaces.
- Testability: easier unit coverage for chunking, FS guards, and similarity scoring.

## Files & Responsibilities
- `fs_utils.py`
  - `load_rag_config(session) -> (indexes, active, vector_db, embedding_model)`
  - Safe traversal (`iter_index_files`), UTF-8 read, overlap-aware `chunk_text`.
  - Enforces read-only allowlist and symlink-escape protection.
- `extractors.py`
- Basic text extraction for PDFs (pypdf), DOCX (python-docx), and XLSX (openpyxl).
  - Reports minimal metadata and a version signature used for visibility in the manifest.
- `indexer.py`
  - `update_index(index_name, root_path, vector_db, embed_fn, embedding_model, batch_size)`
  - Full rebuild MVP: discovers files, chunks text, batches embeddings, writes artifacts.
- `search.py`
  - `search(indexes, names, vector_db, embed_query_fn, query, k, preview_lines, per_index_cap)`
  - Loads per-index artifacts, embeds query, computes cosine similarity, maps char offsets to line ranges, returns top-k with previews.
- `vector_store.py`
  - `NaiveStore`: minimal on-disk layout per index: `manifest.json`, `chunks.jsonl`, `embeddings.json`.
  - Backend interface is intentionally small to allow FAISS/sqlite-vec drop-ins later.

## Actions (adapters)
- `actions/rag_update_action.py`
  - Uses any provider that implements `embed()` to build indexes (selected via `[TOOLS].embedding_provider`).
  - Validates optional index arg against `[RAG]`.
- `actions/load_rag_action.py`
  - Prompts for query; optional `index` and `preview_lines` args.
  - Adds a `rag` context block with ranked results and optional previews.
  - In blocking UIs, offers to load selected full files via existing `load file` command.
 - `actions/rag_status_action.py`
  - Prints status for configured indexes (or a specific index) with manifest summary and chunk/embedding consistency.

## Contexts
- `contexts/rag_context.py`: Minimal `{ name, content }` wrapper used by `load rag` output.

## Configuration
- `[RAG]` (top-level)
  - `indexes = notes, docs` (defines the active index list)
  - `active = true|false` (global on/off gate; when false, RAG commands/tools are hidden)
  - Optional global discovery controls:
    - `included_exts = .md,.mdx,.txt,.rst,.pdf,.docx,.xlsx` (extends base allowlist)
    - `default_include = **/*.md, **/*.mdx, **/*.txt, **/*.rst`
    - `default_exclude = .git, node_modules, __pycache__, .venv, **/*.png, **/*.jpg`
    - `max_file_mb = 10` (skip files larger than this size)
  - RAG tuning knobs (global defaults):
    - `top_k` (default 8)
    - `per_index_cap` (default None)
    - `preview_lines` (default 3)
    - `similarity_threshold` (default 0.0)
    - `attach_mode` ('summary'|'snippets', default 'summary')
    - `total_chars_budget` (default 20000)
    - `group_by_file` (default True)
    - `merge_adjacent` (default True)
    - `merge_gap` (default 5)
- Per-index sections `[RAG.<name>]`
  - `path = /abs/path/to/folder`
  - Optional `include` / `exclude` glob lists (matched relative to the index root)
  - Glob nuance: a leading `**/` is treated as optional for includes, so `**/*.md` also matches files at the index root.
- `[RAG]`
  - `vector_db=~/.config/iptic-memex/vector_store` (default in repo config)
- `[TOOLS]` (embeddings)
  - `embedding_model=text-embedding-3-small` (or your choice)
  - `embedding_provider=openai|openairesponses|…` selects the embedding-capable provider

### RAG tuning (in `[RAG]`)
Knobs to balance relevance vs context size. Defaults shown above.

- `top_k`: number of top results to consider.
- `per_index_cap`: cap results per index.
- `preview_lines`: preview lines per hit in summary mode.
- `similarity_threshold`: drop hits below this cosine score.
- `attach_mode` ('summary'|'snippets'):
  - `summary`: a single consolidated, readable block with paths + previews.
  - `snippets`: attach sliced text ranges directly as context under a character budget.
- `total_chars_budget`: character budget for snippets.
- `group_by_file`: group hits by file before attaching.
- `merge_adjacent`: merge near-contiguous ranges in the same file.
- `merge_gap`: maximum line gap to merge adjacent ranges.
- `max_file_mb`: maximum file size considered during discovery.

## Filesystem Model (Security)
- Read-only traversal of configured roots; no writes into source trees.
- Realpath validation blocks symlink escapes.
- Filters: include `.md|.mdx|.txt|.rst` by default; optional per-index `include`/`exclude` glob patterns, plus `[RAG] default_include/default_exclude`. Extend the allowlist via `[RAG].included_exts`.
- Size caps to avoid runaway memory/latency.

## Data Layout
- `vector_db/<index>/manifest.json`: `{ name, root_path, embedding_model, backend, created, updated, counts }`
- `vector_db/<index>/chunks.jsonl`: one JSON object per chunk `{ path, start, end, hash }`
- `vector_db/<index>/embeddings.json`: parallel array `[[float, ...], ...]`

## Provider Integration
- Providers can implement `embed(texts: list[str], model?: str) -> list[list[float]]`.
- Implemented: OpenAIProvider, OpenAIResponsesProvider, LlamaCpp.
- Embeddings require explicit configuration: RAG never falls back to other providers.
  - Configure both `[TOOLS].embedding_provider` and `[TOOLS].embedding_model`.
 - Mixed providers are supported: when `[TOOLS].embedding_provider` differs from the active chat provider,
   RAG instantiates the embedding provider with its own config section (API key, base_url, etc.).
   This avoids coupling embeddings to the chat provider’s settings.

### Local embeddings (llama.cpp)
- The `LlamaCpp` provider implements `embed()` using the local GGUF model via `llama_cpp`.
- Configure:
  - `[TOOLS].embedding_provider = LlamaCpp`
  - `[TOOLS].embedding_model = /path/to/model.gguf`
  - No fallbacks: if the provider/model is unavailable, actions will emit a clear error.
- Notes:
  - The provider creates a dedicated embedding instance (`embedding=True`) and pools token-level outputs when needed.
  - For robustness, embeddings are computed per-item to avoid batch decode edge cases.

### Model hints (optional)
- Some embedding models expect task prefixes for best quality.
  - NOmic-Embed-Text-V2: prefix documents with `search_document: ` and queries with `search_query: `.
- Potential step: add a config flag to enable these prefixes during indexing/search, e.g.:
  - `[RAG] prefix_style = nomic` (documents use `search_document:`; queries use `search_query:`)
  - This is off by default to avoid model coupling.

## Usage
- Configure indexes in `[RAG]`, choose `embedding_model`, set `[RAG].vector_db`.
- Build: `rag update` or `rag update notes`.
- Search: `load rag`, `load rag notes`, `load rag 3` (preview lines), `load rag notes 5`.
- Results are added to context as a single consolidated, readable block.

### Incremental updates
- Indexing reuses embeddings for unchanged chunks based on content hashes when the embedding signature matches.
- The manifest stores `embedding_signature` (provider/model info) and `vector_dim`.
- Changing embedding provider/model rebuilds the index to avoid mixing vector spaces.

### Minimal configs
- Local embeddings (privacy-first):
  - `[TOOLS]`
    - `embedding_provider = LlamaCpp`
    - `embedding_model = /abs/path/to/your-model.gguf`
- Remote embeddings:
  - `[TOOLS]`
    - `embedding_provider = OpenAI`
    - `embedding_model = text-embedding-3-small`

## Extension Points
- Backends: add a new store implementing the same write/read contract; switch via a future `[RAG] backend=naive|faiss|sqlite-vec`.
- Extractors: plug file-type parsers prior to chunking (PDF, DOCX, XLSX, code-aware chunkers). RAG includes basic PDF/DOCX/XLSX text extraction and caches extracted text under `vector_db/<index>/extracted/`.
- Scoring: swap similarity function or add MMR/diversity selection.
- Filters: extend include/exclude patterns and `.gitignore`-style support.

## Roadmap
Near-term improvements
- Incremental updates: track file mtimes/hashes; only re-embed changed chunks; delete removed docs.
- Per-index file locks: simple `.lock` in `vector_db/<index>/` to avoid concurrent writers.
- `rag status` JSON output option for scripting and integrations.
- More discovery knobs as needed (`included_exts`, `default_*` supported).
- UX: dedupe near-identical results; group hits by file with consolidated previews; ANSI highlighting for preview matches (optional).
- Assistant tool (later): `RAGSEARCH` official tool returning structured citations; assistant requests user to load files as needed.

Backends
- FAISS (CPU) backend: persist per-index FAISS index files; auto-detect and fallback to naive.
- sqlite-vec backend: optional SQLite extension; if extension load fails, fall back to naive.
- Document how to select backend once the `[RAG] backend` setting is introduced.

Extractors & Chunking
- Expand PDF/DOCX/XLSX handling (OCR for scanned PDFs as opt-in; tables/headers in DOCX); optional `chardet`/`cchardet` fallback.
- Smarter chunking: header-aware splits for Markdown; overlap tuning; code-aware strategies.

Performance & Safety
- Memory-mapped embeddings arrays (`.npy`/`.memmap`) for large indexes.
- Progressive indexing with resumable checkpoints.
- Telemetry (optional): simple timing/stats per stage.

Documentation & Tests
- Unit tests: chunk boundaries & overlap invariants, FS guards for symlink escapes, naive scoring sanity.
- Examples: minimal config + quickstart for `rag update`/`load rag` flows.

---
This module is intentionally minimal for MVP, with clear seams for evolution. Keep actions as adapters; implement features here so UX remains stable while internals improve.
