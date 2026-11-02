# Shared core runtime

Completion, CLI chat, agents, and internal helpers share requests, ordered events, explicit outcomes, and
one provider/tool execution lifecycle. Application/session assembly, workflow
services, and terminal rendering sit outside execution. Web/TUI still use the
isolated `legacy/turns.py` executor pending migration. The separate `workspace/`
package owns the current research algorithm, artifacts, and subprocess supervision.

## Supported behavior

| Path | Behavior |
| --- | --- |
| CLI completion | One provider call by default; explicit `--tools` enables bounded tool exchanges |
| Inputs | Explicit message, stdin task, text/image attachments, explicit system prompt |
| Models | Explicit selection wins; otherwise `[AGENT].default_model`, then normal config fallback |
| Prompts | Explicit prompt and normal supplemental addenda; no implicit interactive prompt |
| Headless completion | Same runtime/outcome, optional event observer, no UI substitution or stdout capture |
| Failures | Input gate, provider finish reasons, partial output, cooperative cancellation |
| CLI chat | Same runtime, tool executor, and interaction service as completion |
| Bounded agents | Same runtime, with a provider-call budget and explicit agent policy |
| Internal helpers | Separate headless sessions; shared RunOutcome and inherited cancellation |
| Workspace | Workers use the agent adapter; fresh synthesis uses tool-free completion |

```bash
python main.py --message "Explain this concept"
echo "Explain this concept" | python main.py -f -
python main.py --message "Summarize the attachment" -f notes.txt
python main.py -s --message "Explain this concept"
python main.py -r --message "Explain this concept"
python main.py --tools file --message "Read README.md and explain the project"
python main.py --tools file chat
```

Completion stays tool-free unless a tools list is explicitly supplied. Tool-enabled
completion uses the noninteractive tool profile and `[AGENT].writes_policy`
(default deny). `--agent-writes allow` explicitly permits writes; otherwise local
command execution cannot bypass a deny/dry-run policy and must use Docker's
read-only backend. Chat retains interactive confirmations unless an explicit
write policy is supplied. Models with tools disabled remain tool-free.

One submitted task can contain several provider calls. The application currently
maps `[TOOLS].auto_submit_max_turns` (default 6) to the call ceiling. Hitting it
preserves completed tool results and returns incomplete/call_limit. The runtime
does not read auto_submit flags or invent empty user turns for follow-up.

## Ownership

- `app/session_builder.py` resolves application profiles. Completion assembles a
  normal Session with no CLI/Web/TUI adapter.
- `app/completion.py` resolves tool policy, context inputs, and the input limit
  into a RunRequest. Optional services are not inferred inside execution.
- `app/chat.py` resolves chat policy and keeps its hook lifecycle in explicit
  before-call/after-run services. Observer callbacks do not perform workflow work.
- `app/agent.py` resolves agent tool/write policy, finish markers, MCP opt-in, and
  pre-call context updates. `app/runs.py` owns isolated helper sessions and closes
  their resources on success or failure. Caller-supplied sessions remain open.
- `app/headless.py` assembles completion/agent sessions without frontend imports.
- `app/session_services.py` supplies explicit headless service choices: file-memory
  access, required tools subject to the blocklist, and workflow initialization
  before provider/prompt assembly. Workflow setup does not edit shared config.
- `workspace/runtime.py` adapts worker and synthesis sessions to these services.
  Core has no workspace imports. `workspace/state.py` and `workspace/report.py`
  can be used without loading the application, providers, or interfaces.
- `core/runtime/` owns provider execution, transcript/context transfer, filtering,
  cancellation checks, event ordering, and RunOutcome.
- `ui/completion_renderer.py` renders events without calling providers or changing
  execution. Final-only consumers can ignore deltas.
- `app/session_output.py` translates existing provider/loader diagnostic helpers
  into events during execution. Chat's terminal helpers remain usable between
  runs. This adapter neither captures stdout nor acts as a UI.

The replaced execution bodies in `core/mode_runner.py` are deleted; it is a small
import bridge to `app/runs.py` for existing callers. There is no separate agent loop
or ModeResult. The old Web/TUI user-turn executor lives under `legacy/`; its
duplicate agent loop is deleted.
`core/session_builder.py` is a temporary import bridge to application assembly,
with no assembly implementation of its own.

## Events and outcomes

RunEvent contains run/session IDs, sequence number, UTC timestamp, type, and
payload. Events cover run/provider start and finish, visible text deltas,
completed messages, diagnostics, usage, and unresolved input requests. Ordering
is local to a run. A completed message is authoritative; deltas are presentation
updates, not extra transcript entries.

RunOutcome carries text, optional raw payload, call count (`turns` at the existing
adapter boundary), usage/cost, retained events, status, stop reason, error, and
truncation, plus tool call/failure counts. Deltas reach observers but are not duplicated in the retained event
list. Native replay metadata stays in the transcript; hidden reasoning and raw
response objects do not enter ordinary events.

Outcomes distinguish completed, incomplete, failed, cancelled, and needs_input.
Provider exceptions/metadata determine failure, rather than error-like prose.
Partial text survives stream failure or cancellation. An optional observer
failure is recorded and detaches that observer. Missing usage/cost stays unknown.

Official and pseudo tools normalize into one action executor. Actions run once;
an exception is not retried automatically. Explicit stepwise requests resume the
same action instance. Completed payloads with `ok=false`, named error contexts,
and exceptions are recorded as tool failures. A model can recover and still
deliver an answer; inspect tool_failures/tool_finished to distinguish that from
an investigation with no failed operations.

Every native tool-call ID receives a result, including skipped/cancelled calls
and calls that need input. Native transcript metadata is retained. File/image
contexts belong to their tool results rather than leaking into repeated user
turns. Provider adapters encode tool-created images through their existing user
media encoders after the complete tool-result batch, without changing stored
history or inserting base64 into tool result text.

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

For tools, construct the session with `active_tools_agent="file,cmd"`. Completion
then selects the noninteractive profile and write policy. For chat tasks, use
`app.chat.configure_chat` and `run_chat_turn`; optional workflow services remain
in that application adapter.

This is a blocking API. A caller may use its own worker thread and cancel the
token from another thread. Overlapping runs on one session are rejected before
request contexts are attached. Independent sessions can execute concurrently.
No background queue or durable continuation is implied.

## Interaction

CLI chat shows the configured spinner without a text label before reply text,
then prints the assistant label when text arrives. Streamed chunks are flushed
immediately. `DEFAULT.spinner_style`, colors, and terminal policy determine whether
an animation appears; there is no hardcoded waiting message or fallback line.
On POSIX terminals, input echo/typeahead is suspended during a run
and restored for actual input requests and the next user prompt. Keystrokes
entered while busy are discarded; piped input is untouched. Ctrl-C still works.

Pass an `InteractionService(handler)` to a run for synchronous structured input.
The handler receives an InteractionRequest with ID, kind, and constraints; CLI
chat uses a terminal handler. Alternatively use `InteractionService(wait=True)`
and respond from another thread with `service.respond(id, value)`. Invalid,
duplicate, and stale responses are rejected. Cancellation wakes waiting input.

Without a handler or waiter, an input request ends with needs_input. Its action
is not replayed later. File confirmations include the proposed diff, and an edit
resumes its saved candidate rather than generating a different candidate after
approval. The service's resolution events include IDs rather than response values;
an action may still include relevant input in its explicit tool result.

Existing actions' ask_*/emit calls pass through one action-service bridge during
execution; the runtime never inspects UI capability/blocking flags. New actions
can address `session.interactions` directly. Legacy frontend adapters remain
outside this contract until migrated.

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

Agent helpers retain noninteractive file-memory preparation and explicitly opt
into MCP through `[AGENT].use_mcp` (and the global MCP gate). Completion disables
MCP startup and provider pass-through. Hooks stay application-owned in CLI chat;
their sidecars use the same internal-agent API without recursively starting hooks.
Helpers inherit the caller's sandbox root, use the noninteractive model default,
and copy non-chat context data independently. Parent chat is a template seed,
not a second copy of the child transcript. Child cancellation follows the parent.
Explicitly disabled file-memory access also remains disabled in nested helpers;
workflow-specific required tools and workspace access do not propagate to them.

Each agent step is one provider call; tool follow-ups consume the same budget.
Budget exhaustion returns incomplete/steps. A reply without tool calls completes
naturally; finish markers are recognized and trimmed for final/none output.
Provider streaming follows model configuration; final/full/none controls rendering.
Required pre-call updates run under the session lock separately from observers.
Updates after the initial input become explicit context records, rather than
fabricated empty user turns, and provider adapters encode them as context input.

Continued goal pursuit, an asynchronous run handle, durable resume, hard deadlines,
and research scheduling remain outside this slice. Chat keeps its existing
memory/MCP/autosave startup and application-owned hooks. Web/TUI have not been
migrated or rebuilt; their old continuation paths remain until migration.

## Validation

Offline tests use real config/session/context assembly with fake providers. They
cover terminal and headless execution, stdin/files, model precedence/addenda,
stream filtering, outcomes, metadata, cancellation, observer failure, and session
isolation. Existing chat/agent/workspace and provider suites provide regression
coverage. The correctness suite requires no model call.
