# Repository Guidelines

## Project Structure & Modules
- Root Python package: entrypoint `main.py`; core modules in the repo root (`session.py`, `component_registry.py`, `config_manager.py`, `base_classes.py`).
- Core runtime: `core/` (shared building blocks)
  - `turns.py` (TurnRunner, TurnOptions, TurnResult)
  - `mode_runner.py` (headless internal runs for completion/agent)
  - `provider_factory.py` (capability-aware provider instantiation)
  - `prompt_resolver.py` (prompt chains/files/literals)
  - `session_builder.py` (session construction; `session.SessionBuilder` re-exports)
  - `utils.py` (UtilsHandler used by registry)
  - `null_ui.py` (non-interactive UI for internal runs)
  - `context_transfer.py` (copy contexts/chat across sessions)
- Feature folders: `providers/` (LLM backends), `contexts/` (context sources/loaders), `prompts/` (templates), `modes/` (CLI modes), `tui/` (terminal UI), `utils/` (helpers), `scripts/` (small CLI wrappers), `examples/`, `sessions/`.
- Configuration: project `config.ini`, `models.ini`; user-level config in `~/.config/iptic-memex/` (symlink `user-config` points there).

## Build, Run, and Dev
- Python 3.11+. Use a virtual env.
  - Create env: `python -m venv .venv && source .venv/bin/activate`
  - Install deps: `pip install -r requirements.txt`
- Run CLI:
  - Help: `python main.py --help` and `python main.py <subcommand> --help`
  - Chat mode: `python main.py chat`
  - One-shot (completion) example: `echo "What is PI?" | python main.py -f -`
- Convenience: `scripts/chat` (interactive) and `scripts/ask "your question"` wrappers.

### Default Model Selection (Unified Rule)
- Interactive modes (`chat`, `tui`, `web`) use `[DEFAULT].default_model` unless a model is explicitly provided (CLI `-m/--model` or `/set model`).
- Non‑interactive modes (`completion`, internal runs via `core.mode_runner`, and Agent Mode) use `[AGENT].default_model` when no explicit model is provided.
- Explicit model always wins. This logic is centralized in `core/session_builder.SessionBuilder.build(mode=...)` to avoid per‑caller or per‑action duplication.

## Coding Style & Naming
- Follow PEP 8; 4-space indentation; keep lines ~100 chars.
- Naming: modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_CASE`.
- Docstrings for public functions; add type hints where clear and helpful.
- Prefer small, composable modules mirroring existing layout (e.g., new provider in `providers/`, new mode in `modes/`).

## Testing Guidelines
- Unit and integration tests use `pytest` under `tests/` (mirrors module layout).
- Quick runs: `pytest -q` (or `make test`) and Web-only: `pytest -q tests/web/` (or `make test-web`).
- Headless smoke: `python web/selftest.py` runs a fake-session e2e for `/api/status`, `/api/chat`, SSE `/api/stream`, stepwise start→resume, and token reuse/expiry.
- Suites cover Web (SSE done + interaction handoff, auto-submit, multi-step), TUI adapters (InteractionNeeded), TurnRunner (auto-submit/sentinel), and modes (Chat/Agent/Completion).

## Commit & PR Guidelines
- Commits: concise, imperative mood (e.g., "Add MLX provider"; "Fix usage tracking"). Group related changes.
- PRs: include summary, rationale, before/after notes or screenshots (for TUI), and manual test steps. Link related issues.
- Keep changes focused; update README or docs when behavior/config changes.

## Security & Configuration
- Do not commit secrets. Use env vars (e.g., `OPENAI_API_KEY`) or user config in `~/.config/iptic-memex/`.
- Keep `config.ini`/`models.ini` minimal in repo; override in user config for local tweaks.
- When adding providers, document required keys and safe defaults; avoid enabling risky shell/file operations by default.

### Filesystem Sandbox
- The assistant’s file- and cmd-related actions are restricted to a sandbox root controlled by `[TOOLS].base_directory`.
  - `working` or `.` uses the current shell directory at process start.
  - Absolute paths are allowed only if they reside inside the sandbox root.
  - Relative paths resolve against the sandbox root (not the process CWD).
- CLI override: `--base-dir PATH` sets the sandbox root for a single run and is respected by all tools.
  - Local CMD runs with `cwd = base_dir` so `pwd`/`ls` operate within the sandbox.
  - Docker CMD mounts the sandbox root at `/workspace` and sets `-w /workspace`.
  - File tool reads/writes are validated and confirmed per `[TOOLS]` policy and agent write policy.
- Attachments passed via `-f/--file` are explicit user inputs and not subject to the sandbox.

### Docker CMD in Agent Mode
- To avoid cross-run contention, agents default to ephemeral Docker runs (each command in a fresh `docker run --rm ...`).
- Controlled by `[AGENT].docker_always_ephemeral` (default `True`). If you set it to `False`, a persistent environment defined by `[TOOLS].docker_env` may be reused, which can couple the agent to the first container’s mount/options.

## Actions & Commands
- Actions live in `actions/<snake>_action.py`.
- New actions should subclass `StepwiseAction` and implement `start(args, content)` and `resume(state_token, response)`.
  - Use `session.ui.ask_text/ask_bool/ask_choice/ask_files` for prompts (CLI blocks; Web/TUI raises `InteractionNeeded`).
  - Use `session.ui.emit('status'|'warning'|'error'|'progress', {...})` for updates instead of `print`.
  - Return `Completed({...})` when done; optionally return `Updates([...])` to stream intermediate events.
- Callers must invoke `action.run(...)` in all modes; `StepwiseAction.run(...)` drives `start/resume` until `Completed`.
- Helper/internal actions that never prompt (e.g., filesystem helpers, simple runners) may still subclass `InteractionAction` and expose utility methods.
- Access helpers via `self.session.utils` (I/O, spinners) and configs via `self.session.get_option(...)`.

### System Prompt Addenda (Post-Templating)
- Core now appends conditional addenda to the system prompt without template placeholders.
- Sources (resolved via `PromptResolver`, support chains or literal text):
  - Pseudo-tools guidance when effective tool mode is `pseudo` from `[TOOLS].pseudo_tool_prompt`.
  - Supplemental prompts layered at `[DEFAULT].supplemental_prompt`, per-provider (`[Provider].supplemental_prompt`), and per-model (`[Model].supplemental_prompt` in `models.ini`).
- Injection order and de-duplication:
  - Order: Pseudo-tools → DEFAULT → Provider → Model.
  - Exact-text de-dup removes repeats while preserving order (works well with rolled-up configs).
- Implementation:
  - `actions/build_system_addenda_action.py` composes addenda.
  - `contexts/prompt_context.py` appends addenda after template handlers run and before providers assemble requests.
- Migration:
  - The `{{pseudo_tool_prompt}}` template handler and placeholder have been removed. No template handlers are required for core addenda.

### Internal Runs (Helpers)
- Two helpers on `session.Session` simplify running internal completions/agent loops from actions without subprocesses:
  - `session.run_internal_completion(message: str, overrides: dict | None = None, contexts: Iterable[Tuple[str, Any]] | None = None, capture: 'text'|'raw' = 'text') -> ModeResult`
  - `session.run_internal_agent(steps: int, overrides: dict | None = None, contexts: Iterable[Tuple[str, Any]] | None = None, output: 'final'|'full'|'none' | None = None, verbose_dump: bool = False) -> ModeResult`

Example usage in an action:

```python
from base_classes import StepwiseAction, Completed

class MyVisionSummarizer(StepwiseAction):
    def __init__(self, session):
        self.session = session

    def start(self, args=None, content=None):
        img = self.session.ui.ask_files("Pick an image:")
        res = self.session.run_internal_completion(
            message="Summarize this image",
            overrides={"model": "my-vision-model", "vision": True},
            contexts=[("image", img[0])],
        )
        return Completed({"summary": res.last_text})
```

- Rationale: less boilerplate than importing `core.mode_runner` in every action, consistent NullUI behavior, and easy to mock in tests.

Minimal template (Stepwise)

```python
from base_classes import StepwiseAction, Completed

class MyAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    def start(self, args=None, content=None):
        # Prompt for input (blocks in CLI, raises InteractionNeeded in Web/TUI)
        name = self.session.ui.ask_text("Enter a name:")
        # Emit an update
        self.session.ui.emit('status', {'message': f'Creating {name}...'})
        # Do work, then finish
        return Completed({'ok': True, 'name': name})

    def resume(self, state_token: str, response):
        # Handle a follow‑up response for Web/TUI resumes if needed
        return Completed({'ok': True, 'resumed': True})
```

### Prompt & Turn Prompt Notes
- System prompts: resolved via `PromptResolver`, templated through `DEFAULT.template_handler`, with supplemental addenda (pseudo-tools + provider/model `supplemental_prompt`).
- Per-turn prompts: set `turn_prompt` (DEFAULT/provider/model). Resolved like system prompts; templated with `prompt_template` variables including `{{message_id}}` and `{{turn:*}}`. Injected as transient context on user turns (not saved in transcripts); formatted as plain text before other contexts.

### Argument Normalization Helpers
- Location: `utils/tool_args.py`
- Purpose: Safely parse optional tool/action arguments from models or users where values may arrive as empty strings, different scalar types, lists, or comma‑separated strings.
- Functions:
  - `get_str(args, key, default=None, strip=True, empty_as_none=True) -> str|None`: returns a clean string; blank becomes `None` by default.
  - `get_int(args, key, default=None) -> int|None`: parses integers; blank/invalid -> default.
  - `get_float(args, key, default=None) -> float|None`: parses floats; blank/invalid -> default.
  - `get_bool(args, key, default=None) -> bool|None`: parses booleans from bools or strings (`true/false/1/0/yes/no/on/off`).
  - `get_list(args, key, default=None, sep=',', strip_items=True) -> list[str]|None`: accepts a list/tuple or a comma‑separated string; trims items and drops empties.
- Usage patterns:
  - Treat empty or whitespace‑only strings as “not provided” for optional fields.
  - Accept both arrays and comma‑separated strings for multi‑value inputs.
  - Prefer `get_bool` for flags; avoid `bool('false')` pitfalls.
- Examples:
```python
from utils.tool_args import get_str, get_int, get_bool, get_list

def start(self, args=None, content=""):
    # Strings: blank becomes None
    name = get_str(args or {}, 'name') or 'Untitled'

    # Numbers: parse or use defaults
    top_k = get_int(args or {}, 'k') or 8

    # Booleans: parse permissively
    recursive = bool(get_bool(args or {}, 'recursive', False))

    # Lists: accept comma‑separated string or list
    indexes = get_list(args or {}, 'indexes') or []
```

Gating and CLI‑only flows
- If an action is intentionally CLI‑only (interactive loops, REPLs), guard with `if not session.ui.capabilities.blocking:` and `ui.emit('warning', ...)` instead of `print`.
- Prefer converting multi‑step/confirm flows to Stepwise so they work uniformly across Chat, Agent, Web, and TUI.
- Assistant commands are parsed from assistant output by `assistant_commands_action.py` using blocks:
  `%%CMD%% command="echo" arguments="hello"\n...optional content...\n%%END%%`. Reference content blocks with `%%BLOCK:note%% ... %%END%%` and `block="note"`.
- Dynamic tool registry: tools are discovered from action files ending with `_tool_action.py` that implement:
  - `tool_name()` → canonical lowercase name (e.g., `cmd`, `file`, `websearch`, `ragsearch`).
  - `tool_spec(session)` → `{args, description, required, schema:{properties}, auto_submit}`.
  - Optional `tool_aliases()` and `can_run(session)` for gating.
  The canonical registry is built at runtime and used by providers for official tool calling.
  - API-safe names: The registry selects an API-safe tool name (^[A-Za-z0-9_-]+$) per function, preferring a valid alias or sanitizing if needed. It also stores a mapping `__tool_api_to_cmd__` (api name → canonical command key) so providers can map tool calls back to the right action.
  - Dynamic tools are always included: session-registered tools (e.g., MCP) bypass `[TOOLS].active_tools` filtering so models see them immediately.
- Config:
  - `[TOOLS].active_tools` / `[TOOLS].inactive_tools` control exposure (agent variants supported).
  - Handler override per tool: `[TOOLS].<tool>_tool = action_name`.
  - `cmd` selection stays `[TOOLS].cmd_tool = assistant_cmd_tool | assistant_docker_tool`.
  - Web search override uses `[TOOLS].websearch_tool` (legacy `search_tool` removed).
- Pseudo-tool blocks remain supported and are now case-insensitive; names are normalized to lowercase.
- Register new assistant commands by returning a dict from `actions/register_assistant_commands_action.py` (or user override in `examples/user_actions/`): `{ "MY_TOOL": { "args": ["foo"], "function": {"type":"action","name":"my_tool"}}}`.
- User commands use explicit slash syntax and are matched by `chat_commands_action.py` (CLI) using a shared registry in `user_commands_registry_action.py`.
  - Shape: `/<command> [<subcommand>] [args]` (e.g., `/load file`, `/save chat`, `/set model gpt-4o`).
  - Registry-driven parsing/dispatch; subcommand-level gating honors `Action.can_run(session)` and supports fine-grained gates via `gate: {type: 'action_can_run', name: '<action>', args: [...]}`.
  - Completions are hierarchical: `/` → commands, `/load ` → subs, `/load file ` → file paths, `/set model ` → model names.
  - Provide concise descriptions and keep names short (e.g., `/load file`, `/save chat`).
- Gating: expose `@classmethod can_run(cls, session)` on an action to hide commands when prerequisites (APIs, tools) are missing.
- Auto-submit: if a command sets `auto_submit: true` and `TOOLS.allow_auto_submit` is enabled, the next prompt is skipped after execution.

#### Built-in Tool: persona_review
- Action: `assistant_persona_review_tool` (tool name: `persona_review`).
- Purpose: review content, ideas, or features from one or more persona perspectives and optionally synthesize a panel summary.
- Required: `content`, `personas` (CSV/list or a path to a Markdown personas file).
- Optional: `goal`, `notes`, `artifact_type`, `panel`, `files`, `model`. Supplemental files are read as plain text and attached under a `[Guidelines]` section for each internal run.
- Personas file formats supported: `## Personas` with `### <Name>` subsections; top-level headings (##/###); or a bulleted list `- Name: one-liner`.
- Prompts: `prompts/personas/review.md` and `prompts/personas/panel.md` are resolved via the standard prompt resolver (supports `.txt` and `.md`).
- Enable by listing `persona_review` in `[TOOLS].active_tools`. For non-interactive runs (Agent/Completion), the default tool set comes from `[AGENT].active_tools` and a hard blocklist from `[AGENT].blocked_tools`. CLI `--tools` overrides the default set and remains subject to the blocklist.

### Official Tool Calling (OpenAI-compatible)
- Settings:
  - `[DEFAULT].enable_tools = true|false` to globally enable/disable tools.
  - `[TOOLS].tool_mode = official|pseudo` sets the baseline mode (default `official`).
  - Overrides: per provider (`[Provider].tool_mode`) or per model (`[Model].tool_mode`) can be `official|pseudo|none`.
- Source of truth: `actions/assistant_commands_action.py` registry defines available tools, metadata, and canonical specs. Each provider maps these canonical specs to its API shape.
- Provider (OpenAI):
  - Sends `tools`/`tool_choice` when effective tool mode is `official`.
  
- Provider (Google/Gemini):
  - Sends function declarations as Gemini tools when effective tool mode is `official`.
  - Mapping: assistant commands → Google functionDeclarations via provider-local builder from the canonical registry specs.
  - Streaming: only textual parts are surfaced; `function_call` parts are not coerced to text. Tool calls are parsed from candidates’ content parts and normalized for the TurnRunner.
  - Note: No `tool_choice` control is wired at this time; the model decides when to call.
  - Parses `tool_calls` in non-streaming and streaming. Streaming aggregates argument deltas without printing JSON.
- Turn orchestration:
  - On a tool call, the runner replaces the last assistant message (provider view) with an assistant message containing `tool_calls`, merges any `function.fixed_args` (e.g., MCP server/tool pinning), executes the mapped actions, and appends `tool` role messages with `tool_call_id` and real outputs. Then it auto-submits the follow-up assistant turn.
  - Pseudo-tools still work (when enabled) and run after official tools if no official calls are present.
- UX:
  - Chat Mode: shows a single spinner line `Tool calling: <name>` per tool; tools may print their own status lines (e.g., file tool). A separator newline is written after all tools finish to disambiguate from model output.
  - Agent Mode: suppresses interim status/noise and newlines; focuses on completing steps and returning results.
- Introspection: `show messages` surfaces assistant entries with Tool Calls metadata (name/id) alongside normal message text.

### Mid‑Turn Interactions (Confirmations)
- Unified behavior (official + pseudo): When a tool/action requires user input mid‑turn, it raises `InteractionNeeded`. The TurnRunner pauses, prompts via a session‑scoped broker, resumes the same action instance (looping if needed), then continues the tool flow.
- TUI broker: the TUI registers an interaction broker on the session; `prompt(need)` displays a modal and blocks the worker thread safely until the user responds.
- Pseudo‑tools: assistant commands annotate `InteractionNeeded.spec` with `__action__`, `__args__`, `__content__`. The runner rehydrates `_current_args/_current_content` before calling `resume()` so state is preserved.
- Output scoping: confirmations and resume output render in a tool bubble. The runner sets `__last_tool_scope__` for consistent grouping of auto‑submit summaries.

### File Tool Confirmations + Diffs
- Write/Append: the action computes a unified diff and shows it in the confirmation prompt.
  - TUI/Web (non‑blocking): diff appears inside the modal; on confirm, the write proceeds with `force=True`.
  - CLI (blocking): prints the diff to the console, then asks inline via `ui.ask_bool`.
- Edit: generate the candidate text first, then show a diff and confirm before writing.
- Patch: find exact match of search block, replace with replacement block, then show diff and confirm.
- Helpers do not print diffs when the action has already shown them (internal flag `__suppress_fs_diff__`).
- Post‑write verification: helpers re‑read the file and report a clear error if the final content does not match.

### Auto‑Submit (Follow‑up Assistant Turn)
- Official tools require a `tool` role message with `tool_call_id` (tool result) before the assistant’s follow‑up. The runner emits this and sets `auto_submit`.
- TUI fallback: if `allow_auto_submit=true`, a tool ran, and the last assistant text was empty, TUI schedules one follow‑up turn and reuses the prior empty assistant bubble for the reply (avoids extra bubbles).

### Action Authoring (Prompts vs Exceptions)
- Blocking UIs (CLI): do not raise `InteractionNeeded`; ask inline via `ui.ask_*`, then proceed (use `force=True` on the second pass to avoid re‑prompting).
- Non‑blocking UIs (TUI/Web): raise `InteractionNeeded(kind, spec, state_token)` and let the runner/broker prompt and resume.
- Prefer confirms in the action, not the helper. For edits, confirm after generating the candidate so you can show a real diff.
- Keep prompts concise; trim large diffs (~200 lines/8KB) when embedding in modals.

### Session Keys (internal)
- `__interaction_broker__`: object with `prompt(need)` for mid‑turn UI prompts.
- `__reuse_assistant_msg_id__`: TUI hint to reuse an empty assistant bubble for the auto‑submit follow‑up.
- `__last_tool_scope__`: tool scoping metadata for grouping outputs and summaries.
- `__suppress_fs_diff__`: suppress helper‑side diff printing when the action already surfaced a diff.

#### Anthropic Messages API (Claude)
- Tools: Defined via `tools=[{name, description, input_schema}]` with native JSON Schema.
- Tool calls: Assistant emits `tool_use` content blocks (id, name, input). Client must reply with `user` `tool_result` blocks referencing `tool_use_id` and including result text.
- Provider integration: The Anthropic provider maps assistant `tool_calls` to `tool_use` blocks and tool messages to `tool_result` blocks. Streaming detects `tool_use` starts and input deltas without printing raw JSON.
- Limitations: With extended thinking, only `tool_choice: auto|none` is supported.

- MCP connector (beta): Configure via top‑level `mcp_servers` (not `tools`). The provider attaches the required beta flag automatically and sends entries like `{type:"url", url:"https://.../sse", name:"label", authorization_token?, tool_configuration?}`. Claude returns MCP blocks (`mcp_tool_use`, `mcp_tool_result`).

#### Local Backends (llama.cpp)
- Current status:
  - Tool/function calling support in `llama-cpp-python` varies by model/build and is not reliable across the board.
  - Many local models emit tool calls mid‑reply or after long reasoning traces; some builds ignore provided tools entirely.
- Our approach (for now):
  - The llama.cpp provider uses pseudo‑tools only (`tool_mode = pseudo`). Official tool calling is not used with llama.cpp until upstream support stabilizes.
- Rationale:
  - Avoid added complexity/coupling and brittle textual detection in the provider.
- Notes:
  - Settings like `use_old_system_role` can still be set per model/provider.
  - Ensure `[LlamaCpp].tool_mode = pseudo` when working with llama.cpp.
  - Tuning llama-cpp-python (LlamaCpp provider): set native kwargs via `llamacpp_init` at the provider and/or model level. Model settings override provider defaults.
    - Example provider (config.ini): `[LlamaCpp] llamacpp_init = { flash_attn:true, n_threads:8 }`
    - Example model (models.ini): `llamacpp_init = { n_threads:6, use_mlock:true }`
    - All keys use the llama_cpp.Llama __init__ signature (e.g., `flash_attn`, `use_mmap`, `use_mlock`, `n_threads`, etc.).
    - Unknown keys are ignored with a warning to avoid runtime errors (easy to update the allowlist in code).

##### LlamaCppServer (managed server)
- Purpose:
  - Auto-manage `llama-server` (OpenAI-compatible API) and reuse the existing `OpenAIProvider` client. Keeps users away from ports/URLs.
- How it works:
  - Spawns `llama-server` with `-m <model.gguf>`, binds to a free port in `[LlamaCppServer].port_range` on `127.0.0.1`, and waits for `/health` (fallback `/v1/models`).
  - Disables the Web UI by default via `--no-webui` to keep the managed server headless.
  - Generates an API key by default and configures the client to use `Authorization: Bearer ...`.
  - Streaming is supported (normal `stream=True`). We default `stream_options=False` unless explicitly set.
  - Session-local prompt caching is enabled by forcing `extra_body.cache_prompt = true` on requests. No slot persistence.
- Tools mode:
  - Defaults to pseudo-tools (`tool_mode = pseudo`). If upstream adds official function tools, switch via config to `official`.
- Config (config.ini):
  - `[LlamaCppServer]` requires `binary = /abs/path/to/llama-server`.
  - Optional: `host`, `port_range`, `startup_timeout`, `use_api_key`, logging (`log_path` or `log_dir`), and pass-through `extra_flags`.
- Model (models.ini):
  - `provider = LlamaCppServer`, `model_path = /abs/path/model.gguf`, `stream = true`, `tools = false`.
  - Advanced: `extra_flags_append = --jinja` for templates or other switches.
  - Optional: `draft_model_path = /abs/path/draft-model.gguf` to enable speculative decoding via `-md`.

#### Provider UX Hooks
- Providers can advertise slow initialization by defining a class attribute `startup_wait_message`.
  - Core shows a spinner in CLI chat and status updates in Web/TUI while the provider initializes.
  - A matching `startup_ready_message` can be defined for a completion notice in non-blocking UIs.
  - Agent and Completion modes do not display these indicators.

- MCP capability: providers may implement a static method `supports_mcp_passthrough() -> bool`. When `True`, and `[MCP].active=true`, provider pass‑through MCP will be surfaced (e.g., OpenAI Responses, Anthropic Messages). The `mcp provider` and `mcp status` commands report support explicitly to avoid confusion when app‑side MCP is active but the current provider cannot pass it through.

##### RAG embeddings with llama.cpp
- The `LlamaCpp` provider implements `embed(texts, model?)` for local embeddings with a lightweight, lazy embedding handle.
- Privacy-safe default (strict): RAG does not fallback to network embeddings unless you opt in.
- Configure via `[TOOLS]`:
  - Local (recommended for private docs):
    - `embedding_provider = LlamaCpp`
    - `embedding_model = /abs/path/to/model.gguf`
  - Remote:
    - `embedding_provider = OpenAI`
    - `embedding_model = text-embedding-3-small`
  - To allow fallback candidates, set `embedding_provider_strict = false`.
- RAG indexing is incremental: unchanged chunks (by content hash) are reused when the embedding signature (provider/model) is unchanged. Manifest records `embedding_signature` and `vector_dim`.

### Retrieval-Augmented Generation (RAG)
- Overview:
  - Lightweight local RAG pipeline under `rag/`: discovery → chunking → embeddings → on-disk vector store → semantic search.
  - Artifacts per index live under `vector_db/<index>/` (configurable via `[RAG].vector_db`).
- Commands:
  - `rag update` (action: `actions/rag_update_action.py`): build or refresh indexes using the current embedding provider (prefers current provider; falls back to an embedding-capable provider).
  - `load rag` (action: `actions/load_rag_action.py`): interactive query and summary; loads top matches into chat context.
- Config:
  - `[RAG]`: declare indexes and global defaults.
    - `indexes = notes, docs` (the active index list)
    - `active = true|false` (global feature gate, like `[MCP].active`)
    - `included_exts = .md,.mdx,.txt,.rst,.pdf,.docx,.xlsx` (optional; extends base allowlist)
    - `default_include = **/*.md, **/*.mdx, **/*.txt, **/*.rst` (optional)
    - `default_exclude = .git, node_modules, __pycache__, .venv, **/*.png, **/*.jpg` (optional)
    - `max_file_mb = 10` (optional; discovery size cap per file)
    - RAG tuning knobs (global defaults):
      - `top_k`, `per_index_cap`, `preview_lines`, `similarity_threshold`
      - `attach_mode` ('summary'|'snippets'), `total_chars_budget`
      - `group_by_file`, `merge_adjacent`, `merge_gap`
  - Per-index sections: `[RAG.<name>]` with:
    - `path = /path/to/folder`
    - Optional `include`/`exclude` globs (matched relative to the index root)
  - Embeddings: still configured under `[TOOLS]`:
    - `embedding_provider`, `embedding_model`
- Internals:
  - `rag/vector_store.py`: `NaiveStore` layout – `manifest.json`, `chunks.jsonl`, `embeddings.json`.
  - `rag/indexer.py`: chunks text, batches embeddings, writes artifacts.
  - `rag/search.py`: loads artifacts, embeds query, cosine similarity, maps to line-range previews.
  - `rag/fs_utils.py`: pulls `[RAG]` config, active index, paths, and embedding model/provider.
  - See `rag/README.md` for details and roadmaps (incremental updates, locks, backends).
  - Size cap: `[RAG].max_file_mb` (default 10) controls the maximum file size considered during discovery.

## Stepwise Actions & UI Adapters (Core)
## OpenAI Responses API
- Overview:
  - Forward-looking replacement for Chat Completions with typed events, native tools, and optional state.
  - Recommended for agentic apps: cleaner tool loops and better streaming semantics.

- Key differences:
  - Input items: send a list of typed items instead of a single `messages[]` array.
  - Tools: function tools are first‑class; tool calls appear as `function_call` items and tool results as `function_call_output` items.
  - Token cap: use `max_output_tokens`.
  - Reasoning: use nested `reasoning: {effort: "low|medium|high"}` for OpenAIResponses (minimal not supported). For classic OpenAI Chat Completions, pass `reasoning_effort = minimal|low|medium|high` via `extra_body`.
  - Stateful chaining: opt‑in via `store` and `previous_response_id`.

- Streaming + state:
  - Provider captures `response.id` from streaming events (`response.created`/`response.completed`).
  - If `[OpenAIResponses].store = true` and `use_previous_response = true`, the next turn includes `previous_response_id` automatically and only the new turns after the last assistant reply are sent (tool calls + outputs or fresh user input).

- Tool schema (function tools):
  - Built from the runtime registry assembled from tool actions (see above).
  - Responses requires: `parameters.additionalProperties = false` and `parameters.required` includes every key in `parameters.properties`.
  - Provider enforces `strict: true` and normalizes schemas accordingly.

- Tool I/O mapping:
  - Assistant tool calls → `function_call` input items (include `call_id`, `name`, and JSON `arguments`).
  - Tool results → `function_call_output` input items (include `call_id`, JSON `output`).
  - The pair must share the same `call_id` (provider assembles both from chat context during the next turn).

- Config quickstart:
  - models.ini: `provider = OpenAIResponses`; set `stream = true`.
  - config.ini `[OpenAIResponses]`:
    - `store = true`, `use_previous_response = true` to enable stateful chaining with automatic minimization of repeated context.
    - MCP pass‑through uses `[MCP].active` as the gate (no provider‑local toggle). When active and the provider supports pass‑through, MCP servers are sent via built‑ins; configure servers with `mcp_servers` and optional per‑server keys (see below).
  - Reasoning: set `reasoning = true` and `reasoning_effort = minimal|low|medium|high` on the model.

### Provider: OpenAIResponses

- Name: `OpenAIResponses` (module: `providers/openairesponses_provider.py`).
- Purpose: Use OpenAI’s Responses API instead of Chat Completions while keeping the same TurnRunner/tooling UX.
- Selection: Choose per model via `provider = OpenAIResponses` in `models.ini`.

- Key config (config.ini → `[OpenAIResponses]`):
  - `active = True`
  - `store = False` (default in repo; set to `True` to enable server‑side chaining)
  - `use_previous_response = False` (set `True` to pass `previous_response_id` when available and reuse server-side state)
  - `tool_mode = official` (uses the dynamic canonical tool registry)
  - MCP pass‑through: controlled by `[MCP].active`; configure with `mcp_servers` and per‑server options.

- Example model (models.ini):
```
[gpt-5-mini-resp]
provider = OpenAIResponses
model_name = gpt-5-mini
stream = true
# pricing for cost calc (example only; use your current rates)
price_unit = 1000000
price_in = 2.0
price_out = 10.0
# reasoning controls
reasoning = true
reasoning_effort = low
# token cap (mapped to max_output_tokens)
max_completion_tokens = 4096
```

- Usage/cost tracking:
  - Usage: aggregates `input_tokens` and `output_tokens` (fallback to prompt/completion) and `output_tokens_details.reasoning_tokens`.
  - Cost: uses model `price_in`/`price_out` and `price_unit`. When `bill_reasoning_as_output = True`, reasoning tokens are billed as output.
  - Introspection: `show usage`, `show cost` will reflect both streaming and non‑streaming runs.

- Tool calling details:
  - Functions are built from `actions/assistant_commands_action.py` into Responses schema with:
    - `parameters.type = object`, `parameters.additionalProperties = false`, and `parameters.required` including every key in `parameters.properties`.
    - `strict = true`.
  - Tool calls in outputs are parsed as `function_call` items and normalized for TurnRunner.
  - MCP tool calls are executed on the provider side (OpenAI). They surface as `mcp_*` items in `output` rather than `function_call` entries and therefore are not routed through our tool runner. Control approval with `mcp_require_approval = never|always` (global or per server via `mcp_require_approval_<label>`). Gated by `[MCP].active`.
  - Provider clears `get_tool_calls()` after read to avoid double execution in follow‑ups.

- Tool result mapping (input side):
  - Assistant tool calls from the prior turn are sent as `function_call` input items `{type, call_id, name, arguments}`.
  - Tool outputs are sent as `function_call_output` input items `{type, call_id, output}`.
  - Both must share the same `call_id`; the provider assembles the pair from chat context.

- Streaming + state:
  - Provider captures streaming `response.id` and applies it as `previous_response_id` on the next request when `store=true` and `use_previous_response=true`.
- When chaining is active, only the latest window is sent: either the last user message(s) or the `function_call` + `function_call_output` pair for tool loops.

- Notes and caveats:
  - `verbosity` is not a Responses request parameter; omit it.
  - Built‑in tools (e.g., `web_search_preview`, `file_search`, `code_interpreter`) may require additional resources/ids; enabling the flag only declares availability.
  - Privacy: `store=true` persists Response objects server‑side for chaining/retrieval. Disable to avoid persistence; costs remain token‑based.
- UI adapters: `session.ui` provides `ask_text/ask_bool/ask_choice/ask_files` and `emit(event_type, data)`.
  - CLI blocks (`CLIUI`, `capabilities.blocking=True`); Web/TUI raise `InteractionNeeded` (`blocking=False`).
- Stepwise protocol (mode-agnostic):
  - Actions may implement `start(args, content)` and `resume(state_token, response)` and subclass `StepwiseAction`.
- `run()` is the public entrypoint across modes; `StepwiseAction.run(...)` drives `start/resume` and may raise `InteractionNeeded` in non‑blocking UIs.
- Confirmations & updates:
  - Use `session.ui.ask_bool(...)` for confirmations; use `session.ui.emit('status'|'progress'|'warning'|'error', {...})` for updates.
  - Reprint gating: only reprint chat in blocking UIs (CLI) to avoid stdout in Web/TUI.

### Spinners vs Progress
- Spinners: Use when waiting an unknown amount of time (e.g., running tools, waiting for first streaming tokens). Call `session.utils.output.spinner(message)` and stop with `stop_spinner()` or let the context manager close. Spinner style is configurable via `DEFAULT.spinner_style`.
- Progress events: Optional UI signal via `session.ui.emit('progress', {"progress": 0.0–1.0, "message": str})`. Best for long‑running, multi‑phase work where incremental progress makes sense.
- Current usage: Core actions keep spinners for waits; file tools do not emit progress (operations are fast and progress lines clutter with the spinner). The capability remains available for future long‑running actions where a bar is useful.
- Auto-submit mechanics (centralized):
  - Tools set `session.auto_submit` (with `TOOLS.allow_auto_submit=True`).
  - The `TurnRunner` orchestrates the follow-up across all modes: reprocess contexts (silent when requested) → add a synthetic empty user turn → run the next assistant turn.
  - Chat, Agent, and Web (stream and non‑stream) all use the same logic; no mode‑specific drift.
  - Large-input gate: Interactive runs (Chat/TUI/Web) apply a centralized check during context processing. If combined context tokens exceed `TOOLS.large_input_limit` and `TOOLS.confirm_large_input=True`, auto-submit is cancelled so the user can review contexts before proceeding. Non-interactive runs (Completion/Agent/internal) use a separate `[AGENT].large_input_limit` and abort early with a structured error event when exceeded.

## Web/TUI Runner Notes
- Endpoints: `/api/action/start|resume` for stepwise actions; `/api/chat` (non-stream) and `/api/stream` (SSE).
- Command handling: `/api/chat` first matches `user_commands_action` to run actions/builtins (e.g., `/set model ...`) without contacting the LLM. Only slash-prefixed inputs are considered commands.
- Interactions: `InteractionNeeded` yields `{needs_interaction, state_token}`; the client renders a widget and resumes via `/api/action/resume`. A cancel endpoint marks the token used.
- Streaming: `TurnRunner` powers SSE. If a tool requests interaction mid‑stream, the server sends a terminal `done` event with `{needs_interaction, state_token}`; the client resumes via JSON.
  - Official tools: the provider detects function-call deltas during streaming and the runner executes tools after the stream completes; status updates appear in the updates panel.
- Secure streaming start: the browser POSTs `/api/stream/start` with `{message}` and receives a short‑lived HMAC token; it then opens `EventSource('/api/stream?token=...')` to avoid logging sensitive content in URLs.
- UI/UX: Web renders an updates/interaction panel (status/warning/error/progress) separate from the chat log, with Cancel and close controls. Attachments: 📎 button and drag‑and‑drop upload to `/api/upload`, then call `load_file` with server paths to skip prompts. Uploaded files are read into context and deleted immediately; file contexts record `{name, origin:'upload', server_path}`.

## Action Conversion Checklist
- Replace stdin/stdout with `session.ui.ask_*` and `session.ui.emit(...)`.
- For multi-step actions, implement `start/resume`; on resume, use `force=True` where needed to avoid re-confirm loops.
- Use `session.context_transaction()` to stage side effects pending confirmation.
- Implement or inherit `run(...)` (preferred). For stepwise flows, subclass `StepwiseAction` so `run()` drives `start/resume`.
- Gating: if an action is inherently interactive/looping and not yet fully stateful for Web/TUI, gate it to CLI by checking `session.ui.capabilities.blocking` and emit a warning otherwise (e.g., `manage_chats`, `save_code`, `debug_storage`).

## Status: Actions Converted
- Core actions now use the Stepwise model and UI adapters (ask_*/emit) where applicable: file loaders (`load_file`, `load_raw`, `load_multiline`), tooling (`assistant_file_tool`, `assistant_fs_handler`, `set_option`, `set_model`, `run_command`, `run_code`, `fetch_from_web`, `clear_context`, `clear_chat`, `count_tokens`, `reprint_chat`, `show`).
- Project helper `load_project` is Stepwise: CLI loops via `ask_choice`; Web/TUI offers one selection and dispatches to sub-actions.
- Example user actions under `examples/user_actions` updated: `brave_search`, `brave_summary`, `debug_reload` (emits), and `debug_storage` gated to CLI.
- CLI-only (for now): `manage_chats`, `save_code`, and `debug_storage` retain richer CLI interactions; Web/TUI will emit a warning if invoked.

## Web/TUI Caveats (MVP)
- Streaming: When a stepwise action needs input mid-stream, the server sends a terminal `done` event with `{needs_interaction, state_token}`. The client resumes via `/api/action/resume`.
- Token store: Minimal in-memory store; next steps include TTL/monotonic step and stronger signatures.

## User Actions & Overrides
- Users can override or extend core behavior without editing repo files:
  - Actions: set `DEFAULT.user_actions` in `config.ini`; actions in that folder override names in `actions/` (see `examples/user_actions/`).
  - Prompts: set `DEFAULT.user_prompts`; user prompts resolve before `prompts/`.
  - Config: `~/.config/iptic-memex/config.ini` and `models.ini` override repo defaults (symlink `user-config` points there).
- Hot reload during development: use assistant `RELOAD` or user command `debug reload` to invalidate caches and re-import modules on next use.

## Reasoning Models (OpenAI) – Params & Usage

- Config-first: set provider-agnostic options in config.ini/models.ini; providers map them to API params.
  - Capabilities (model-level): set `tools = true|false` and `vision = true|false` per model (defaults in `[DEFAULT]`: `tools = True`, `vision = False`).
    - When `tools = false`, the effective tool mode hard-gates to `none` and providers do not send tool specs.
    - When `vision = true`, providers that support multi-part messages will include image parts; otherwise images are omitted.
- OpenAI reasoning settings (when `reasoning = true`):
  - Token cap:
    - Use `max_completion_tokens`. If omitted, `max_tokens` is mapped to it.
  - Effort:
    - `reasoning_effort` accepts `minimal|low|medium|high` (lowercased and passed through).
  - Verbosity:
    - `verbosity` accepts `low|medium|high` to steer response length.
  - Streaming usage:
    - `stream_options = True` to include usage stats in streaming; disable for OpenAI-compatible backends that don’t support it.
  - Billing:
    - `bill_reasoning_as_output = True|False` (default True) controls whether reasoning tokens are included in output cost estimates.
- Usage tracking:
  - Tracks prompt/completion tokens, cached prompt tokens, and reasoning metrics (`reasoning_tokens`, `accepted_prediction_tokens`, `rejected_prediction_tokens`) for both streaming and non-streaming.
  - `get_usage()` exposes per-turn (`turn_*`) and running totals.
  - `get_cost()` uses `bill_reasoning_as_output` to include/exclude reasoning tokens in output cost.
- Other providers:
  - Don’t assume these params exist elsewhere; map analogous concepts in each provider as needed.

## Agent Mode

- Overview:
  - Trigger via `--steps > 1` or `[AGENT].default_steps > 1`.
  - Non‑interactive N‑turn loop with optional tool calls between turns.
  - Routes from both chat and completion entry points; existing modes unchanged.

- CLI flags:
  - `--steps <N>`: number of assistant turns (Agent Mode when > 1).
  - `--agent-writes {deny|dry-run|allow}`: file tool write policy.
  - `--agent-output {final|full|none}`: control assistant output visibility.
  - `--no-agent-status-tags`: disable per‑turn `<status>` tag injection.
  - `-v/--verbose`: dumps system prompt (once) and provider‑visible messages before each turn; skips last assistant content to avoid duplication (implemented by `TurnRunner`).
  - `-r/--raw`: in final mode, prints only the raw provider response (JSON if applicable); sentinel tokens are stripped.

- Output modes:
  - final: prints only the final assistant message; with `-r` prints only raw response; strips `%%DONE%%|%%COMPLETED%%|%%COMPLETE%%` from output.
  - full: streams assistant output per turn; leaves `%%DONE%%` visible for external watchers.
  - none: suppresses assistant output (useful for tool‑only runs).

- Finish sentinel:
  - Early stop on `%%DONE%%|%%COMPLETED%%|%%COMPLETE%%`.
  - Instruction moved to the system prompt; not included in status tags.
  - In final/raw: sentinel tokens are stripped from printed results; in full: kept.

- Prompt and contexts:
  - Agent Mode injects at start: finish signal + write policy note (for deny/dry-run) into the system prompt.
  - Stdin (`-f -`): stdin content becomes the user’s message (not a file context), remaining contexts still attach.
  - Status context is minimal: `Turn X of Y` (write policy removed).

- Write policy (file tool):
  - deny: block writes; model should output unified diffs.
  - dry-run: compute diffs without writing; non‑existent files treated as empty.
  - allow: perform writes without confirmation.
  - `[TOOLS].ensure_trailing_newline` appends a newline on text writes when enabled.

## Turn Orchestration (TurnRunner)

- Overview:
  - `core/turns.py` provides a mode‑agnostic engine that coordinates user turns, assistant turns, tool execution, auto‑submit follow‑ups, and sentinel handling.
  - Used by: `ChatMode`, `AgentMode`, Web endpoints (`/api/chat`, `/api/stream`), and internal runs via `core/mode_runner.py`.

- API:
  - `run_user_turn(input_text, options) -> TurnResult` for single‑turn flows with optional auto‑submit continuations (Chat/Web).
  - `run_agent_loop(steps, prepare_prompt, options) -> TurnResult` for bounded N‑turn agent runs, including status context injection and write‑policy prompt notes.

- Streaming and display:
  - Streaming uses `assistant_output_action` to render tokens. Non‑streaming applies `AssistantOutputAction.filter_full_text(...)` for display and `..._for_return(...)` for tools.
  - `suppress_context_print` option lets callers show pre‑prompt summaries once (e.g., Chat) and attach contexts silently during turns.

- Agent specifics supported:
  - Sentinel detection (`%%DONE%%|%%COMPLETED%%|%%COMPLETE%%`) and trimming in final mode.
  - `verbose_dump` emits the system prompt (once) and provider‑visible messages before each turn for troubleshooting.
  - `-r/--raw` is honored in Agent “final” mode by printing `provider.get_full_response()` (JSON) instead of the filtered text.

- Interaction handling:
  - When actions raise `InteractionNeeded`, TurnRunner lets it propagate; Web mode catches it and issues a state token for `/api/action/resume`.

- Status updates between prompts:
  - For interactive Chat, use `show_pre_prompt_updates()` to print context summaries and any assistant/agent details accumulated between turns; TurnRunner then attaches contexts silently for the actual turn when requested.

- Newline handling:
  - In Agent Mode final/none, stdout is wrapped to drop leading blanks and collapse newline bursts.
  - Context printing avoids unconditional spacers during auto‑submit agent turns.

- Config keys:
  - `[AGENT]`: `default_steps`, `writes_policy`, `output`, `show_context_details`, `context_detail_max_chars`.
  - `[DEFAULT]`: `show_context_summary` (Agent Mode enables summaries/details only in full).
  - `[TOOLS]`: `ensure_trailing_newline`.

- Examples:
  - Steps + full: `python main.py --steps 3 --agent-output full -f notes.md`
  - Stdin user msg: `echo "Do X" | python main.py -f - --steps 2`
  - Verbose: `python main.py --steps 3 -v -f -`
  - Raw‑only final: `echo "Do X" | python main.py --steps 2 -r --agent-output final -f -`
  - Deny writes: `python main.py --steps 3 --agent-writes deny -f plan.md`

### Status System (Events + Scopes)

Actions and core emit lightweight status signals that UIs render consistently across CLI/Web/TUI.

- Event types:
  - `status`, `warning`, `error`, `progress` (existing)
  - `context` (new): “something entered current turn context”

- Scope metadata (grouping):
  - `origin`: `tool` | `command` | `system`
  - `tool_call_id` (official tools), `title` (e.g., `/load file`)
  - CLI prints lines; TUI/Web group events into bubbles by origin/scope.

- Baseline system visibility (framework-owned):
  - TurnRunner and Assistant Commands emit a spinner line `Tool calling: <name> — <desc?>` and wrap tool output in `tool_scope` so UIs can place the line near the active assistant bubble.

- Context summaries (pre‑prompt):
  - CLI shows “In context: …” via `ProcessContextsAction` by default (`[DEFAULT].show_context_summary = True`).
  - Web/TUI default to hidden summaries; per‑action context events are used instead. Core sets this in `core/session_builder` for non‑blocking UIs.

- Path + tokens conventions:
  - File names in context are stored as cwd‑relative paths (not absolute/basename) for project clarity.
  - MarkItDown runs as a helper only; it does not emit UI lines. It sets file context metadata:
    - `{converter:'markitdown', original_file:<abs>, token_count:<int>}` and an index `__markitdown_index__` in `session.user_data` keyed by original abs path.
  - Emitters (e.g., `load_file`, `assistant_file_tool`) should prefer `metadata.token_count` and only fall back to counting from content if missing.

- Action authoring guidelines:
  - For context adds/removes, emit a single `context` event:
    - `session.ui.emit('context', { 'message': f"Added file: {name} ({tokens} tokens)", 'origin': 'command'|'tool', 'kind': 'file'|'image'|..., 'name': name, 'tokens': tokens, 'action': 'add', 'title': '/load file' })`
  - For multi‑line blocks (status tables, etc.), print once using a consolidated write:
    - `out.write("\n".join(lines))` (avoid one write per line, which becomes multiple bubbles in TUI).
  - Do not rely on pre‑prompt summaries in non‑blocking UIs; use `context` events for visibility.
  - Keep helpers (like MarkItDown) silent; delegate user‑visible emits to the caller that knows the scope (tool vs command).

- Mode behavior (defaults):
  - CLI: shows pre‑prompt summaries; suppresses `context` events when summaries are enabled to avoid duplicates.
  - Web/TUI: hide pre‑prompt summaries; show per‑action `context`/`status` lines grouped in bubbles.
  - TUI suppresses generic “/<command> completed” for context‑modifying commands.

- Where to hook in code:
  - Tool spinner/scope: `core/turns.py` and `actions/assistant_commands_action.py`.
  - Context emits: `actions/load_file_action.py`, `actions/assistant_file_tool_action.py`, helpers like `actions/markitdown_action.py` (metadata/index only).
  - CLI/Web/TUI sinks and scoping: `utils/output_utils.py` (tool_scope), `tui/output_sink.py`, `web/output_sink.py`, and `web/routes/stream.py` (attach scope on emits).

## MCP Integration (Provider Pass‑Through + App‑Side)

- Global Gate
  - `[MCP].active = true|false` acts as a single on/off switch for all MCP features (provider pass‑through and app‑side http/stdio). When false, MCP actions and autoload are disabled.

- Provider Pass‑Through
  - Providers can advertise support with `supports_mcp_passthrough()` (True in this repo for OpenAI Responses and Anthropic Messages).
  - When supported and `[MCP].active=true`, MCP servers are forwarded using provider‑specific parameters (e.g., Responses built‑ins or Anthropic `mcp_servers`). Keys include: `mcp_servers`, `mcp_headers_<label>`, `mcp_allowed_<label>`, `mcp_require_approval(_<label>)`, `mcp_connector_id_<label>`.
  - CLI: `mcp provider`, `mcp status` show whether the current provider supports pass‑through, whether MCP is active, and list configured servers with hints.

- App‑Side MCP (http & stdio)
- `[MCP]` defaults: `mcp_servers` (optional), `autoload`, `auto_alias`.
  - Per‑server: `[MCP.<name>]` with `transport = provider|http|stdio`, target `url|command`, `headers` (supports `${env:VAR}`), and provider hints (`allowed_tools`, `require_approval`).
  - Per‑server overrides: `autoload`, `auto_alias`. Omitted keys inherit the global defaults.
  - `allowed_tools` also filters app‑side registration (only the listed tool names are registered for that server).
  - Autoload connects and registers app‑side tools for the session. Tools are exposed as `mcp:<server>/<tool>` with fixed args; optional aliases create short names when no conflicts exist.

- CLI Helpers
  - `mcp status`, `mcp provider`, `mcp tools`, `mcp resources` for visibility.
  - `mcp load <server>`; `mcp unload <server>`; `discover mcp tools <server>`; `register mcp tools <server> [--tools t1,t2] [--alias]`; `unregister mcp tools [pattern]`.
  - `show tools` lists assistant‑visible tools (deduped) and annotates MCP entries with `(server)` for clarity.

- Noise Control
- For MCP diagnostics, prefer centralized logging: set `[LOG].log_mcp = detail` (and optionally `mirror_to_console = true`).
