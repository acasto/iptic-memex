# RAG

Memex includes a lightweight, local RAG pipeline for indexing folders and loading relevant snippets into chat context.
You can define multiple indexes and query them separately or as a whole. 

## Quickstart

Configure indexes in `[RAG]`:

```ini
[RAG]
indexes = notes, research
active = true

[RAG.notes]
path = ~/Notes

[RAG.research]
path = ~/Research
```

Set embeddings:

```ini
[TOOLS]
embedding_provider = OpenAI
embedding_model = text-embedding-3-small
```

Then run:
- Build indexes: `/rag update` (or `/rag update notes`)
- Query all indexes: `/load rag` (interactive prompt)
- Query a specific index: `/load rag <index>`
- Inspect: `/rag status`

## Key config knobs

Top-level `[RAG]`:
- `indexes` - comma-separated list of index names
- `active` - global on/off switch
- `vector_db` - where the index artifacts live
- `included_exts`, `default_include`, `default_exclude`, `max_file_mb`
- Tuning: `top_k`, `per_index_cap`, `preview_lines`, `similarity_threshold`, `attach_mode`, `total_chars_budget`,
  `group_by_file`, `merge_adjacent`, `merge_gap`

Per-index `[RAG.<name>]`:
- `path` - folder to index
- Optional `include` / `exclude` globs

## Embedding providers

RAG uses the embedding provider configured under `[TOOLS]` (no auto-fallback):
- `embedding_provider` selects the provider
- `embedding_model` selects the model

Local embeddings example (llama.cpp):

```ini
[TOOLS]
embedding_provider = LlamaCpp
embedding_model = /abs/path/to/model.gguf
```

## More detail

For internals (chunking, storage layout, search flow, roadmap), see `rag/README.md`.
