# Repository Guidelines

## Project Structure & Modules
- Root Python package: entrypoint `main.py`; core modules in the repo root (`session.py`, `component_registry.py`, `config_manager.py`, `base_classes.py`).
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

## Coding Style & Naming
- Follow PEP 8; 4-space indentation; keep lines ~100 chars.
- Naming: modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_CASE`.
- Docstrings for public functions; add type hints where clear and helpful.
- Prefer small, composable modules mirroring existing layout (e.g., new provider in `providers/`, new mode in `modes/`).

## Testing Guidelines
- No formal test suite yet. Add `pytest` tests under `tests/`, mirroring module paths.
- Name tests `test_*.py`; aim for fast, unit-level coverage around providers, parsers, and CLI mode handlers.
- Quick smoke tests: run `python main.py --help`, `python main.py chat`, and exercise a simple prompt.

## Commit & PR Guidelines
- Commits: concise, imperative mood (e.g., "Add MLX provider"; "Fix usage tracking"). Group related changes.
- PRs: include summary, rationale, before/after notes or screenshots (for TUI), and manual test steps. Link related issues.
- Keep changes focused; update README or docs when behavior/config changes.

## Security & Configuration
- Do not commit secrets. Use env vars (e.g., `OPENAI_API_KEY`) or user config in `~/.config/iptic-memex/`.
- Keep `config.ini`/`models.ini` minimal in repo; override in user config for local tweaks.
- When adding providers, document required keys and safe defaults; avoid enabling risky shell/file operations by default.

## Actions & Commands
- Actions live in `actions/<snake>_action.py` with class `<PascalCase>Action(InteractionAction)` and a `run(...)` method. Access helpers via `self.session.utils` (I/O, spinners) and configs via `self.session.get_option(...)`.
- Assistant commands are parsed from assistant output by `assistant_commands_action.py` using blocks:
  `%%CMD%% command="echo" arguments="hello"\n...optional content...\n%%END%%`. Reference content blocks with `%%BLOCK:note%% ... %%END%%` and `block="note"`.
- Command mapping: `{ "CMD": { "args": ["command","arguments"], "auto_submit": true, "function": {"type":"action","name":"assistant_cmd_tool"}}}` resolves to `actions/assistant_cmd_tool_action.py` → `AssistantCmdToolAction.run(args, content)`.
- Register new assistant commands by returning a dict from `actions/register_assistant_commands_action.py` (or user override in `examples/user_actions/`): `{ "MY_TOOL": { "args": ["foo"], "function": {"type":"action","name":"my_tool"}}}`.
- User commands are matched in `user_commands_action.py` and invoke actions with positional args or call a specific class method via `{"method": "set_search_model"}`. Provide concise descriptions and keep names short (e.g., `load file`, `save chat`).
- Gating: expose `@classmethod can_run(cls, session)` on an action to hide commands when prerequisites (APIs, tools) are missing.
- Auto-submit: if a command sets `auto_submit: true` and `TOOLS.allow_auto_submit` is enabled, the next prompt is skipped after execution.

## Stepwise Actions & UI Adapters (Core)
- UI adapters: `session.ui` provides `ask_text/ask_bool/ask_choice/ask_files` and `emit(event_type, data)`.
  - CLI blocks (`CLIUI`, `capabilities.blocking=True`); Web/TUI raise `InteractionNeeded` (`blocking=False`).
- Stepwise protocol (mode-agnostic):
  - Actions may implement `start(args, content)` and `resume(state_token, response)` and subclass `StepwiseAction`.
  - `StepwiseAction.run(...)` remains for back-compat and can drive start/resume in CLI.
- Confirmations & updates:
  - Use `session.ui.ask_bool(...)` for confirmations; use `session.ui.emit('status'|'progress'|'warning'|'error', {...})` for updates.
  - Reprint gating: only reprint chat in blocking UIs (CLI) to avoid stdout in Web/TUI.
- Auto-submit mechanics (centralized):
  - Tools set `session.auto_submit` (with `TOOLS.allow_auto_submit=True`).
  - The `TurnRunner` orchestrates the follow-up across all modes: reprocess contexts (silent when requested) → add a synthetic empty user turn → run the next assistant turn.
  - Chat, Agent, and Web (stream and non‑stream) all use the same logic; no mode‑specific drift.

## Web/TUI Runner Notes (MVP)
- Web endpoints: `/api/action/start|resume` for stepwise actions; `/api/chat` and `/api/stream` for chat.
- Command handling: `/api/chat` matches `user_commands_action` to run action/method commands before calling the model.
- Interactions: `InteractionNeeded` produces `{needs_interaction, state_token}`; client renders a prompt and calls `/api/action/resume`.
- Streaming: `TurnRunner` powers streaming turns. When a tool needs interaction mid‑stream, the server sends a terminal SSE `done` event with the prompt + token; the client resumes over JSON.
- Isolation: web-specific plumbing (output sink, token store) is confined to `web/` and does not change action code.

## Action Conversion Checklist
- Replace stdin/stdout with `session.ui.ask_*` and `session.ui.emit(...)`.
- For multi-step actions, implement `start/resume`; on resume, use `force=True` where needed to avoid re-confirm loops.
- Use `session.context_transaction()` to stage side effects pending confirmation.
- Keep a thin `run(...)` for back-compat by subclassing `StepwiseAction`.
- Gating: if an action is inherently interactive/looping and not yet fully stateful for Web/TUI, gate it to CLI by checking `session.ui.capabilities.blocking` and emit a warning otherwise (e.g., `manage_chats`, `save_code`, `debug_storage`).

## Status: Actions Converted
- Core actions now use the Stepwise model and UI adapters (ask_*/emit) where applicable: file loaders (`load_file`, `load_doc`, `load_pdf`, `load_image`, `load_sheet`, `load_raw`, `load_multiline`), tooling (`assistant_file_tool`, `assistant_fs_handler`, `set_option`, `set_model`, `run_command`, `run_code`, `fetch_code_snippet`, `fetch_from_web`, `clear_context`, `clear_chat`, `count_tokens`, `reprint_chat`, `show`).
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
  - `turns/runner.py` provides a mode‑agnostic engine that coordinates user turns, assistant turns, tool execution, auto‑submit follow‑ups, and sentinel handling.
  - Used by: `ChatMode`, `AgentMode`, and Web endpoints (`/api/chat`, `/api/stream`). TUI benefits indirectly via shared session/actions.

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
