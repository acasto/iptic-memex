# Hooks

Hooks let you run lightweight agents around each turn without changing your normal workflow. By default they run
internally, but you can also run a hook in an external process.

## Configuration

Example config:

```ini
[HOOKS]
session_start = daily_tasks
pre_turn = context_scout
post_turn = memory_scribe
session_end = session_debrief

[HOOK.daily_tasks]
model = gpt-4o-mini
prompt = prompts/hooks/daily_tasks.md
tools = youtrack,memory
steps = 1
mode = inject
enable = true

[HOOK.context_scout]
model = gpt-4o-mini
prompt = prompts/hooks/scout.md
tools = ragsearch,memory
steps = 1
mode = inject
enable = true

[HOOK.memory_scribe]
model = gpt-4o-mini
prompt = prompts/hooks/scribe.md
tools = memory
steps = 1
mode = silent
enable = true

[HOOK.session_debrief]
model = gpt-4o-mini
prompt = prompts/hooks/debrief.md
tools = memory
steps = 1
mode = inject
enable = true
```

External runner example:

```ini
[HOOKS]
post_turn = memory_scribe

[HOOK.memory_scribe]
model = gpt-4o-mini
prompt = prompts/hooks/scribe.md
tools = memory
steps = 1
mode = silent
runner = external
external_cmd = python main.py agent --steps 1 --json --from-stdin --no-hooks
```

Optional gating:
- `when_every_n_turns = 3` (run on every 3rd user turn)
- `when_min_turn = 2` (skip until at least turn 2)
- `when_message_contains = profile,location` (case-insensitive substring match on current user message)
- `when_role = user` (future-proof; current turns are user-initiated)

Optional output labeling:
- `label = "My Hook"` sets the assistant context name
- `prefix = "### Hook output"` is prepended to the hook output (newline is auto-added if missing)

## Behavior

`mode=inject`:
- Runs once per user turn after the user message is recorded but before the provider call.
- The hook's `last_text` is attached as assistant context on the latest chat turn and included in the provider request.

`mode=silent`:
- Skipped in `pre_turn` (no added latency before the answer).
- Runs in `post_turn` so it can review the latest exchange and call tools without affecting the current response.

### Lifecycle phases

`session_start`:
- Runs once per process/session run on the first real user-initiated turn (auto-submit turns are skipped).
- Runs after the first user message is recorded but before the provider call, so injected output affects the first response.

`session_end`:
- Runs during `Session.handle_exit()` (Chat `/quit`, Ctrl-C/EOF confirm, TUI/Web shutdown).
- For `mode=inject`, the hook output is persisted as a normal `assistant` context so it is captured by autosave/checkpoints.

## Runner selection

- `runner = internal` (default): run inside the current process using `Session.run_internal_agent`.
- `runner = external`: run a subprocess with a runner snapshot passed on stdin. Use `external_cmd` to control the
  command line. The external runner returns JSON with `last_text` and `error`.
