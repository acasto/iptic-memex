# Workspace task experiments

A task is a durable brief plus selected strategy, execution profile, project
context, and attempt history. The new foreground CLI runs bounded experiments
against the existing providers, actions, and shared workspace notes:

```bash
memex -m glm-5.3 --project /path/to/project workspace task start \
  --brief /path/to/website-brief.md \
  --strategy examples/workspace/strategies/investigate.toml \
  --profile examples/workspace/profiles/limited.toml
```

The example model is a local alias: use a model configured in your installation.
Strategy/profile paths are relative to the invoking shell directory. Project
source access is rooted in `--project` (CWD by default). A task brief is explicit
input and may live outside that root. Supporting sources should be in the project
or referenced as URLs for an enabled web tool. This command does not yet accept
`-f` attachments; the existing `workspace research` attachment interface remains.

## Strategy and profile

A strategy TOML file accepts `name`, `workflow`, `prompt`, and `agents` (initial
fan-out for the fixed experiment only). It cannot select permissions, models,
budgets, or filesystem roots. A prompt may use Memex's existing prompt resolution;
actual assembled system prompts are saved per agent generation.

Built-in strategy names:

| Name | Behavior |
| --- | --- |
| `directed` (default) | Start one ordinary task session with optional agent tools; no mandatory synthesis |
| `fixed` | Existing fixed workers and fresh synthesis |
| `adaptive` | Existing lead/branch research experiment and fresh synthesis |
| `single` | Existing single research context and fresh synthesis |

The named legacy workflows retain their existing research policies. They are
adapters, not rules imposed on the new task service. Select a TOML strategy with
`workflow = "session"` to experiment primarily through instructions.

An execution profile TOML file accepts:

```toml
# model = "configured-model-name"       # defaults to project/AGENT selection
# synthesis_model = "another-model"     # used by legacy synthesis workflows

tools = ["file"]
calls = 12
concurrency = 2
max_sessions = 4
timeout = 180
max_tokens = 2048
writes = "deny"
```

All calls share the task ceiling, including delegated sessions, model-backed
helpers, and any legacy synthesis. `concurrency` limits simultaneous agent runs;
`max_sessions` limits retained task sessions in the directed workflow. Legacy
research helpers are short-lived and use their existing reservation accounting.
A fixed strategy exceeding profile capacity is rejected instead of silently
increasing resources. Its per-worker allowance is derived from the total call
ceiling, reserving one synthesis call; unused remainder may remain unspent.

Project agent model/default configuration supplies the default model. A selected
profile overrides it; explicit global `-m`, `--tools`, `--agent-writes`, and
`--max-tokens` override those profile fields. Task `--calls` and `--timeout` are
explicit user overrides. Global `--steps` and `--prompt` are rejected here: use
strategy selection and the shared call limit. Profile files are explicit user
inputs, not model tool arguments or automatically loaded project permissions.

Workspace/agent tools are task capabilities added to the directed workflow;
`tools` selects the ordinary action tools. Existing blocklists remain enforced.
Unscoped global memory and RAG retrieval are excluded pending scoped retrieval.
Commands use the existing Docker action with ephemeral containers. Task/run/note
records are read-only through filesystem/command tools even when project writes
are allowed; workspace publications go through the host-owned contribution tool.

## Agent capabilities

The task-scoped `agents` tool provides:

- `spawn`: start independent work in the inherited project and tool scope.
- `status`: inspect IDs, outcomes, pending inputs, and resources.
- `send`: queue selected text at a future provider-call boundary.
- `wait`: wait up to 30 seconds without consuming additional model calls.
- `followup`: continue a completed descendant's retained live session.
- `cancel`: cancel a descendant and its active descendants.

Every agent can use these operations subject to the same capacity and allowance.
Only an owning ancestor may follow up or cancel an agent. Agents cannot choose
another directory, model endpoint, permission set, or budget through this tool.
Private histories are not copied between agents. Result notifications carry
selected text; a full response and automatically published summary remain saved.
Shortened previews are identified and link to their full response artifact.

The foreground directed workflow returns the entry session's answer and waits
for task-owned work to stop. Unconsumed contributions or incomplete agent runs
produce a partial outcome. Completed execution is not independent verification
that the user's goal is satisfied. No judge or synthesis is mandatory in the
shared service. Strategies decide what evidence they need before answering.

## Inspection and repeat

```bash
memex --project /path/to/project workspace task list
memex --project /path/to/project workspace task inspect TASK_ID
memex --project /path/to/project workspace status --watch
memex --project /path/to/project workspace task repeat TASK_ID
```

Inspection is passive. Records are under `.memex-workspace/tasks/TASK_ID/` and
`.memex-workspace/runs/RUN_ID/`. Each attempt saves the brief, resolved project,
strategy/profile snapshots and origins, effective prompts, agent relationships,
ordered events, usage when reported, budget charges, responses, and outcomes.
Existing fixed/adaptive experiment artifacts remain intact and are linked from
the enclosing task attempt.

`repeat` starts a NEW attempt with the saved brief/settings and shared project
notes. It does not resume the previous provider or tool stack and may repeat
side effects. Source files and installation/user configuration remain live; this
is not a frozen repository or a promise of identical model output. Start a new
task to change settings. A local POSIX lease prevents overlapping attempts for
the same task and is inherited by the worker if its supervisor exits.

The CLI owns a process group and enforces the profile deadline with cancellation
and bounded cleanup grace. Worker threads use SessionHost for live continuity;
they do not survive process exit. Interrupted work retains artifacts and gets an
explicit terminal state. CLI chat attachment and attempt-scoped questions are available below. Scheduling
and restart restoration remain later phases.

Task stores bind to one canonical project root. A workspace already bound to a
different root, or containing legacy runs for another repository, is rejected;
choose a separate workspace directory instead of mixing historical notes.
Moving a bound project/store requires deliberate migration (not implemented).

Task-internal model-backed helpers inherit the profile's selected model as well
as its shared allowance. They cannot switch to another configured model through
an action argument. A separately selected legacy synthesis model applies only
to that synthesis execution. This prevents an experiment selecting a local model
from silently using the global agent-model default for helper calls.


## CLI chat attachment and questions

Workspace access is opt-in for each CLI chat:

```text
/workspace attach
/workspace start Investigate the retry behavior and cite the relevant code
/workspace start --profile /path/to/local.toml Investigate the retry behavior
/workspace list
/workspace inspect TASK_ID
/workspace import TASK_ID
/workspace reply TASK_ID QUESTION_ID
/workspace cancel TASK_ID
```

`attach` defaults to the project's `.memex-workspace`; an optional directory selects
another store, still bound to this project. `start` uses the directed strategy and
an independent agent profile/model, rather than inheriting the interactive chat's
model or tool authority. An optional profile selects its model and limits. The
chat's custom configuration file is carried into the worker. Task input is the
explicit brief; chat history is not copied automatically. Use `--chat-last N`
to include selected recent messages as described below.

Attached work runs in supervised subprocesses. Chat displays completion notices
and pending questions only before its normal input prompt. Background work does
not read from or write to the terminal. `inspect` shows the saved result and pending
questions; results are not automatically inserted into the conversation. Agents
can use `agents(operation="ask", message="...")` to request user input. `reply`
prompts using the existing typed input handler; replies are validated again in the
worker. Waiting consumes wall-clock allowance but makes no model calls. Question
IDs belong to one attempt, accept one answer, and are closed when the wait ends.
Ordinary foreground task runs retain their existing noninteractive input policy.

Closing chat cancels and joins the work it started with `start`, including on
`/quit`, EOF, or an exception. To deliberately start independently owned work, use:

```text
/workspace detach --profile /path/to/local.toml Investigate the retry behavior
```

This starts a **new** task in a separate supervisor process; it does not transfer
an existing live task. It survives chat exit and remains inspectable from another
chat attached to the same store or `workspace task inspect TASK_ID`. Another
attached chat may answer questions or cancel it. It remains bounded by its saved
profile timeout, including when nobody answers a question. Replies do not restore
a terminated process or resume a Python stack after restart.


### Bringing a result into the conversation

`/workspace import TASK_ID [ATTEMPT_ID]` loads the latest finished attempt's answer
as context for your next message. If a newer attempt is still running, supply the
ID of an earlier finished attempt explicitly. Import does not submit a chat turn
or call a model. Follow it with a question such as “Assess these findings against
the code.”

Imported content identifies the task, attempt, saved result path, terminal status,
and recorded error. It is labeled as unverified model output whose claims and
sources need evaluation. Partial or failed attempts with an answer can be imported;
their status is retained. Empty, missing, mismatched, or unfinished results are
rejected. Answers over 32,000 characters require selecting relevant excerpts from
the saved result rather than silently truncating them. Each attempt is imported
once per chat; a repeated command reports that it was already imported. The normal
chat context pipeline attaches the snapshot to the next user turn.


### Including selected conversation context

```text
/workspace start --chat-last 4 Investigate the retry issue we discussed
/workspace detach --profile /path/to/local.toml --chat-last 2 Check the proposed fix
```

`--chat-last N` selects the last N nonempty user/assistant text messages from the
full chat history (1–100 messages), excluding tool-role messages, tool-call
metadata, attachments, loaded contexts, and system prompts. With fewer than N
eligible messages, it includes all available messages. No history is copied when
the option is omitted. Empty selections and selections over 16,000 text characters
are rejected before creating a task; choose fewer messages rather than silently
truncating them.

The task specification saves a separate `chat_snapshot` containing the source
session ID, requested count, and copied role/text pairs. The current brief remains
separate. The initial worker receives the snapshot as labeled historical background
context; it is not replayed as live conversation or treated as new instructions.
Repeating a saved task uses its saved excerpt, unaffected by later chat edits.
Options precede the brief, in either order; `--` ends option parsing.


### Ownership and failure handling

Cancellation received before worker startup records a cancelled attempt without
launching a worker. Once launched, the supervisor enforces the saved deadline and
cleans up the worker process group on cancellation. Detached supervisors also
handle SIGTERM through that cleanup path. Task input is supplied through a file so
a worker that never reads stdin cannot block the supervisor's deadline checks.

Inspection and listing check the ownership lease. A saved running attempt with no
remaining owner is displayed as interrupted, with its recorded status preserved
in the inspection output; inspection does not rewrite artifacts or restart work.
An inherited lease still held by a surviving worker counts as live ownership.
Abrupt termination (SIGKILL, machine failure) cannot guarantee immediate cleanup;
there is no automatic recovery or replay of uncertain side effects. A process
holding a lease is not proof that it is making progress.

Question replies and closure share a lock; a reply arriving after closure is
rejected. Dead attempts do not offer pending questions through chat. Ordinary
filesystem and command tools cannot write question/reply records, which remain
inside the host-owned run directory even when project writes are enabled.
