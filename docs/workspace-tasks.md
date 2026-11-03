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
explicit terminal state. Chat attachment, durable user questions, scheduling,
and restart restoration are later phases.

Task stores bind to one canonical project root. A workspace already bound to a
different root, or containing legacy runs for another repository, is rejected;
choose a separate workspace directory instead of mixing historical notes.
Moving a bound project/store requires deliberate migration (not implemented).

Task-internal model-backed helpers inherit the profile's selected model as well
as its shared allowance. They cannot switch to another configured model through
an action argument. A separately selected legacy synthesis model applies only
to that synthesis execution. This prevents an experiment selecting a local model
from silently using the global agent-model default for helper calls.
