# Core refactor: first completion slice

Tool-free completion now uses requests, ordered events, and explicit outcomes.
CLI rendering and application/session assembly sit outside execution. Chat and
agents still use TurnRunner pending migration; workspace retains its current
worker algorithm and subprocess supervision.

## Supported behavior

| Path | Behavior |
| --- | --- |
| CLI completion | One provider call, no client tools, text/raw output, optional streaming |
| Inputs | Explicit message, stdin task, text/image attachments, explicit system prompt |
| Models | Explicit selection wins; otherwise `[AGENT].default_model`, then normal config fallback |
| Prompts | Explicit prompt and normal supplemental addenda; no implicit interactive prompt |
| Headless completion | Same runtime/outcome, optional event observer, no UI substitution or stdout capture |
| Failures | Input gate, provider finish reasons, partial output, cooperative cancellation |
| Chat and bounded agents | Existing TurnRunner path; migrate in the next slices |
| Workspace | Existing worker/notes workflow; synthesis completion uses the new runtime |

```bash
python main.py --message "Explain this concept"
echo "Explain this concept" | python main.py -f -
python main.py --message "Summarize the attachment" -f notes.txt
python main.py -s --message "Explain this concept"
python main.py -r --message "Explain this concept"
```

Completion remains tool-free. A submitted task supporting bounded tool exchanges
belongs to the next slice. Use the existing agent command when tools are needed.

## Ownership

- `app/session_builder.py` resolves application profiles. Completion assembles a
  normal Session with no CLI/Web/TUI adapter.
- `app/completion.py` resolves tool policy, context inputs, and the input limit
  into a RunRequest. Optional services are not inferred inside execution.
- `core/runtime/` owns provider execution, transcript/context transfer, filtering,
  cancellation checks, event ordering, and RunOutcome.
- `ui/completion_renderer.py` renders events without calling providers or changing
  execution. Final-only consumers can ignore deltas.
- `app/session_output.py` translates existing provider/loader diagnostic helpers
  into events. It neither captures stdout nor acts as a UI.

The replaced completion execution body in `core/mode_runner.py` is deleted; its
entry point delegates to application completion for existing internal callers.
`core/session_builder.py` is a temporary import bridge to application assembly,
with no assembly implementation of its own. The remaining workflows will migrate
incrementally, rather than retaining two completion implementations.

## Events and outcomes

RunEvent contains run/session IDs, sequence number, UTC timestamp, type, and
payload. Events cover run/provider start and finish, visible text deltas,
completed messages, diagnostics, usage, and unresolved input requests. Ordering
is local to a run. A completed message is authoritative; deltas are presentation
updates, not extra transcript entries.

RunOutcome carries text, optional raw payload, call count (`turns` at the existing
adapter boundary), usage/cost, retained events, status, stop reason, error, and
truncation. Deltas reach observers but are not duplicated in the retained event
list. Native replay metadata stays in the transcript; hidden reasoning and raw
response objects do not enter ordinary events.

Outcomes distinguish completed, incomplete, failed, cancelled, and needs_input.
Provider exceptions/metadata determine failure, rather than error-like prose.
Partial text survives stream failure or cancellation. An optional observer
failure is recorded and detaches that observer. Missing usage/cost stays unknown.

## Headless use

```python
from app.session_builder import SessionBuilder
from app.completion import run_completion
from config_manager import ConfigManager
from core.cancellation import CancellationToken

session = SessionBuilder(ConfigManager()).build(mode="completion")
token = CancellationToken()
events = []
result = run_completion(
    session=session,
    message="Explain the attachment",
    contexts=[("file", "notes.txt")],
    stream=True,
    on_event=events.append,
    cancellation=token,
)
```

This is a blocking API. A caller may use its own worker thread and cancel the
token from another thread. Overlapping runs on one session are rejected before
request contexts are attached. Independent sessions can execute concurrently.
No background queue or durable continuation is implied.

Cancellation is cooperative around invocation and between stream items. It
cannot forcibly interrupt a backend blocked in native/network code. Workspace
retains process supervision for hard deadlines. Cleanup runs outside token locks
and executes once. Input limits count text contexts, task, system prompt, and
existing transcript content with the session's token counter; this is an estimate,
not an exact accounting of every provider's wire encoding or image input.

## Deferred services

Completion does not implicitly start hooks, MCP autoload, file-memory preparation
or backups, or session autosave. Explicit prompt templates/addenda still resolve
normally, including templates that read memory. Service lifecycle integration
will return as explicit application/workflow services.

Interactive response/resume, tools, continued agent work, an asynchronous run
handle, hard deadlines, and research scheduling are outside this slice. An
unexpected interaction ends with needs_input; execution does not replay earlier
side effects to resume it. Web/TUI have not been migrated or rebuilt.

## Validation

Offline tests use real config/session/context assembly with fake providers. They
cover terminal and headless execution, stdin/files, model precedence/addenda,
stream filtering, outcomes, metadata, cancellation, observer failure, and session
isolation. Existing chat/agent/workspace and provider suites provide regression
coverage. The correctness suite requires no model call.
