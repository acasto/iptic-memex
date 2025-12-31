# Runners

Runners provide a way to execute completion/agent loops without tying them to the current interactive UI.
Memex supports two runner types: internal (in-process) and external (subprocess).

## Internal runner

Internal runs are used by actions (hooks, tools, etc.) to run a completion/agent loop without spawning a subprocess.

Entry points:
- `Session.run_internal_completion(...)`
- `Session.run_internal_agent(...)`

Behavior:
- Builds a subsession from the embedded `SessionBuilder`.
- Attaches contexts (non-chat).
- Uses a NullUI to avoid stdout.
- Returns a `ModeResult` (`last_text`, `turns`, `usage`, `cost`, `events`).
- Applies a non-interactive input gate (large inputs can abort with an error event).
- For agents, chat history is not copied into the subsession; instead a chat seed is used for templates (see below).

## External runner

External runs are supported when you want a separate process. A runner snapshot is passed over stdin and the external
runner returns JSON with the final message.

Default entrypoint:

```bash
python main.py agent --from-stdin --json
```

External runner behavior:
- The caller builds a snapshot via `build_runner_snapshot(...)`.
- The snapshot includes a limited set of params, contexts, and a chat seed.
- The external process is isolated and returns `{ "last_text": ..., "error": ... }`.

Hooks can opt into external runs by setting `runner = external` and `external_cmd = ...` in the hook config.

## Snapshot schema (high level)

```json
{
  "version": 1,
  "params": { "base_directory": "..." },
  "chat_seed": [ { "timestamp": ..., "role": "user", "message": "..." }, ... ],
  "contexts": { "file": [ { "id": 0, "data": { ... } } ], ... }
}
```

Notes:
- Params are conservative and whitelisted (base_directory only; use overrides for everything else).
- Contexts exclude chat and prompt contexts; they are reconstructed in the subsession.
- The chat seed is read-only and used for template rendering only.

## Chat seed behavior

Internal/external runs can receive a read-only chat seed for template rendering (for example, `{{chat:last=3}}`).
The seed is not merged into the inner session chat history; it is only used by the template handler.

This avoids duplicating the full conversation in the subsession while still allowing prompt templates to reference
recent history.

## When to use which

- Use **internal** runs when you want tight integration and access to the same process state.
- Use **external** runs when you need isolation or a separate environment.

If you need full session state in the sub-run, prefer internal runs or pass the needed context explicitly.
