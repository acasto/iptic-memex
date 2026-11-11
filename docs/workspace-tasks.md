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
explicit terminal state. CLI chat attachment, attempt-scoped questions, and one-off
scheduled repeats are available below. Calendar schedules and checkpoint
continuation remain later increments. See the
[proposed scheduling and continuation plan](#proposed-next-work-scheduling-and-continuation)
for the next design increments.

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


### Watch activity from a second terminal

For a directed/session task, run this from the same project (or select it with
`--project` before `workspace`):

```bash
python main.py workspace watch TASK_ID
python main.py workspace watch TASK_ID --once --verbose --tail 50
python main.py workspace task cancel TASK_ID
```

Use `--directory PATH` when chat attached a nondefault records directory. The
watcher shows the original brief, current agent assignments, the latest 30 activity
lines, then newly recorded activity. It reports commands and arguments, file
operations and paths, search queries, delegation, tool results/errors, and emitted
assistant text. Status changes show call usage and agent phases; a heartbeat every
15 seconds updates elapsed time even when the model is silent. Event timestamps
are recorded in UTC. `--tail 0` skips earlier events; `--once` prints and exits.

This is a read-only view of recorded evidence, with no model calls. Ctrl-C stops
only the watcher. To stop the task itself, use `workspace task cancel TASK_ID`
from another terminal, or `/workspace cancel TASK_ID` in chat. Cancellation is a
request to the supervisor, not a rollback of completed actions.

The watcher exits when the task completes, stops for input, or otherwise ends.
Running it again after `/workspace continue` follows the new attempt; running it
on a finished task reviews its latest attempt. Legacy fixed/adaptive workflows
continue to use `workspace status` and `workspace report`.

Output is a bounded excerpt, not a raw shell stream or private reasoning trace.
`--verbose` expands displayed excerpts to 4000 characters; tool-start fields are
recorded up to 1024 characters each. It cannot recover details absent from older
logs. Tool output may appear only on completion, and some tools supply content
through saved conversation contexts instead of their result payload. The printed
records directory contains the full available event/history artifacts. Commands
and search queries can themselves contain sensitive text; this local view does
not claim to redact secrets embedded in those values.

### Stop for input and continue after restarting chat

For an input-enabled chat task, the root agent can call
`agents(operation="yield", message="Which option should I use?", progress="Completed work, evidence, remaining work, and uncertain side effects")`.
This saves the question and handoff, then cancels the attempt, including its other
agents. Later calls in the same tool batch are skipped. Already-running external
operations cannot be undone. Workers must send their issues to the root; they
cannot yield the entire task themselves. Existing `ask` still waits within the
live attempt's timeout; use `yield` when the answer may arrive much later.

Chat shows **stopped, input needed** at its next input boundary. No process or
model polling remains once the attempt has stopped. You can quit and later use:

```text
/workspace attach
/workspace list
/workspace inspect TASK_ID
/workspace continue TASK_ID
```

`continue` displays the saved progress and question, then prompts for your answer.
It starts a new attached attempt under the same task identity, with the original
brief, saved execution profile, question, handoff, answer, and a bounded recent
history excerpt. Full earlier histories remain explicitly retrievable with the
agents history operations. Old tool calls and Python sessions are never replayed.
The answer is saved in the new attempt's `experiment.json`, and the attempt records
`continued_from`. A stale answer cannot restart a different attempt.

Each explicit continuation gets a fresh allowance from the saved profile. There
is no automatic continuation or cumulative chain budget in this experiment.
Scheduled repeats cannot advance a task while it is stopped for input. Explicit
`task repeat` still means a fresh execution of the original brief, without the
handoff. Closing chat while a continuation is actively running still cancels it;
wait for **stopped, input needed** before quitting if you want to continue later.

A small single-agent profile is available at
`examples/workspace/profiles/clarification.toml`. From chat:

```text
/workspace attach
/workspace start --profile examples/workspace/profiles/clarification.toml Plan a small experiment. If an important choice needs my input, yield with your progress and question so I can continue later.
```

This tests the model's judgment about when to yield. For a mechanical round-trip
test, explicitly instruct it to yield once with a known fact in its handoff, then
use your answer and that fact to finish on continuation. Set the profile's model
to a configured alias; the example selects `glm-5.3`.

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

## Workspace call metadata and conversation history

Directed/session task agents now receive a factual status context before every
provider call, including tool follow-ups. The fields are `run_id`, `agent_id`,
`generation`, `provider_call`, `calls_limit`, `calls_charged`,
`calls_remaining_after_current`, and `seconds_remaining`. The current call has
already been charged. The remaining count is a shared-pool snapshot, not a promise
of future calls for this agent. Helpers consume that same allowance; they do not
inherit task tools or the task's configurable lifecycle prompts.

These fields are also available to standard prompt templates under
`{{turn:workspace_FIELD}}`, for example
`{{turn:workspace_calls_remaining_after_current}}`. They do not replace the user
message's ID/index or turn metadata. The application callback adds context through
the existing runtime boundary; the shared runtime has no workspace policy.

A session strategy may optionally define:

```toml
call_prompt = "Remaining shared calls after this one: {{turn:workspace_calls_remaining_after_current}}"
final_call_threshold = 1
final_call_prompt = "If work remains, record progress, evidence, unresolved work, and a next action."
```

Both prompts default to empty. Sources use the standard prompt resolver and the
configured template-handler chain; `prompt_template` must be enabled for the
`turn:` substitutions. The final prompt is added when unallocated calls after the
current charge are less than `final_call_threshold` (default 1). It may therefore
appear for more than one agent/call with a larger threshold. No separate evaluator
or extra model call is introduced. See
[the handoff experiment](../examples/workspace/strategies/handoff.toml), selectable
with `workspace task start --strategy examples/workspace/strategies/handoff.toml`.

This is best-effort prompt policy, not guaranteed finalization. A timeout, abrupt
exit, cancellation, or another agent consuming the shared budget can remove a
later opportunity to write a report. There is no reserved final call, automatic
continuation, or budget extension. Existing configured sidecar hooks remain
disabled for task workers; these prompt callbacks do not turn them on.

### Durable history and explicit retrieval

Each task agent saves a versioned `runs/RUN_ID/AGENT_ID/history.json` snapshot at
context preparation/update, assistant-message completion, and run completion.
It preserves application user/assistant/tool/context messages, native tool-call
IDs and arguments, textual attached context, and session/generation provenance.
Retained-session follow-ups update the same archive with their accumulated history;
new task attempts have separate archives. Existing history is not automatically
injected into the next attempt.

These are portable conversation records, not restorable Python sessions. Opaque
provider continuation objects, separate reasoning metadata, binary image content,
and runtime objects are omitted. System prompts are already saved separately per
agent generation. A hard crash can leave the last complete snapshot behind the
latest side effect; history is not proof that an uncertain operation did not run.
Archive write failures cancel the task rather than silently claiming durable state.

The task-scoped `agents` tool adds:

- `history_list`: list available archived agents across this task's attempts,
  with session IDs, generation, phase, and message counts. `start` defaults to 0;
  `limit` defaults to 20, maximum 50.
- `history_read`: read `target` (default self), optionally selecting `run_id` from
  that list. `start=-10`, `limit=10` selects the last ten message records. A negative
  start counts backward; nonnegative starts are zero-based. Records include tool
  exchanges and context updates, so they are not synonymous with user turns.
- Long selected ranges are returned as JSON text chunks: `max_chars` defaults to
  12,000 (allowed 256–16,000). Follow `next_offset` using the returned resolved
  `start` to read the rest of the same range, or `next_start` for the next range.
  Pass the returned `snapshot_id` on subsequent pages; a concurrently changed
  snapshot is rejected so callers can restart pagination instead of mixing versions.

Retrieval is restricted to the same durable task ID, including its earlier
attempts and agents. A run without a durable task ID can access only its own run.
Histories of other tasks are not exposed by these operations. Explicit retrieval
can read another agent's history within the task; private histories are never
copied automatically. Normal tool blocklists and host-owned read-only record
protections still apply. Older runs without archives remain inspectable through
their existing artifacts; this feature does not invent missing transcripts.

History retention currently preserves all message records in the latest snapshot
per agent; there is no automatic pruning. Retrieval bounds, initial context size,
and retention policy are deliberately separate concerns. This allows experiments
with recent history, a handoff report, or agent-selected retrieval without imposing
a summary as the only state available between attempts.

### Interfaces reserved for subsequent experiments

The next building blocks remain separate from the implemented metadata/history
features:

- A local durable inbox with explicit publish/read/acknowledge operations. A
  deterministic callback at the chat input boundary can announce unread counts
  and optionally display a single message. Display, acknowledgement, and model
  context injection must remain separate choices; no sidecar is required to count
  messages. Reuse the existing chat safe-input boundary rather than allowing a
  worker to print into an active terminal prompt.
- A host-owned continuation request with an agent-supplied disposition/reason and
  an independently validated effective state. The recurring schedule's enabled
  flag remains distinct from a request for another attempt within an episode.
  Only an explicit user-enabled policy can approve wakes, subject to persistent
  chain budgets, delay limits, and pause state. A prompt report alone cannot start
  another attempt or reset allowances.
- Optional evaluation as a configurable policy consumer of history/state. A
  separate evaluator is not mandatory; the same agent's end-of-allowance report
  can be evaluated experimentally first. Deterministic checks own authority and
  bounds regardless of which prompt or agent proposes a decision.

## Scheduled task repeats

Scheduling is available for directed/session tasks. It runs saved tasks once
or on an elapsed-time interval, when an external timer or a person invokes
`run-due`. No resident process watches the clock. Local calendar schedules,
evaluation, notifications, and checkpoint continuation remain future work.

```bash
memex --project /path/to/project workspace schedule add TASK_ID --at 2026-11-05T06:00:00-05:00
memex --project /path/to/project workspace schedule list
memex --project /path/to/project workspace schedule inspect SCHEDULE_ID
memex --project /path/to/project workspace schedule pause SCHEDULE_ID
memex --project /path/to/project workspace schedule resume SCHEDULE_ID
memex --project /path/to/project workspace schedule run-due --dry-run
memex --project /path/to/project workspace schedule run-due --max-runs 1 --max-seconds 600
```

All commands emit JSON. `--directory` selects a different workspace record store.
The saved task must belong to the selected project. Without `--every`, add
authorizes one unattended attempt using the saved brief, strategy, and profile; it does
not execute immediately. A timestamp must include an explicit offset or `Z` and
is stored in UTC. Use an absolute project path when invoking from cron or a timer.
The command does not install or change external timers.

`run-due` defaults to one attempt and a 600-second tick allowance. It starts only
an attempt whose complete profile timeout plus ten seconds of cleanup allowance
fits in the time remaining. Otherwise that schedule remains pending. Due work is
ordered by timestamp and schedule ID, and the due-time cutoff is frozen at tick
start. Work becoming due during execution waits for a later invocation. Overdue
one-off work runs once when next eligible; it is never replayed for each missed poll.

A workspace dispatch lock prevents overlapping ticks. A task lease prevents a
scheduled attempt from overlapping a manual repeat. Busy tasks are deferred and
other due tasks may run. Pause prevents a future claim but does not cancel an
active attempt. Cancelling an attempt stops further dispatch in that invocation.
The foreground scheduler forwards SIGTERM through bounded task cancellation.

Each schedule has a stable occurrence ID and stores the digest of its authorized
task inputs. Changed saved inputs require a newly authorized schedule. Claims are
persisted before launch, and run records contain occurrence provenance. A later
poll can reconcile a conclusive terminal result after a scheduler crash. If a
claimed occurrence still has a live owner, the tick returns busy. If its outcome
is uncertain, it becomes `needs_review` without automatic replay. Inspection and
dry-run do not perform this reconciliation or otherwise mutate records.

One-off occurrences are consumed whether successful or not. Completed attempts
set the schedule to `completed`; incomplete, failed, cancelled, or input-requiring
attempts set it to `needs_attention`. Neither executes again automatically.
`resume` only re-enables an unconsumed schedule. Inspect uncertain work before
explicitly creating another schedule; doing so authorizes another attempt and
could repeat side effects.

Scheduled execution disables live question waiting even when the task originally
came from chat. A question produces `needs_input`; there is no automatic answer,
terminal prompt, or replay of a dead action. Saved input settings are not changed.
Schedule records live under the protected `schedules/` directory and remain
read-only to worker file/command tools, including with project writes allowed.

Exit status is 0 for idle, dry-run, completed, or deferred ticks; 2 for a busy
scheduler; 1 for an error or attention-requiring outcome encountered in this tick;
and 130 for cancellation. JSON distinguishes those cases. Previously consumed
failures are visible in list/inspect but do not make every later idle poll fail.
There is no claim of exactly-once external side effects after a crash.

### Recurring intervals and failure limits

```bash
memex --project /path/to/project workspace schedule add TASK_ID \
  --at 2026-11-05T06:00:00-05:00 --every 24h --max-failures 3
```

`--every` accepts positive integer durations with `s`, `m`, `h`, or `d` units,
from one minute to 366 days. These are elapsed-time intervals anchored at `--at`.
A 24-hour cadence is not a promise of the same local hour across daylight-saving
changes. Polling every fifteen minutes still limits when due work is noticed.

Each recurring schedule executes at most once per invocation. Missed slots produce
one catch-up attempt; the next due time advances to the first slot strictly after
completion on the original cadence. The completed occurrence records its nominal
due time and skipped interval count/range. Backward clock changes cannot replay a
consumed occurrence. A busy task or insufficient tick allowance leaves the current
occurrence pending without advancing it.

A successful attempt resets the consecutive-failure count. Other terminal outcomes
increment it; reaching `--max-failures` (default 3) pauses the schedule for attention.
`needs_input`, cancellation, and interruption pause immediately. Failed occurrences
are consumed; a later regular slot is a new occurrence, not a retry of the old one.
Unknown crash outcomes remain `needs_review` and never resume automatically.

A user pause made during execution remains effective after that attempt finishes.
`resume` can re-enable a recurring schedule paused for a known terminal outcome;
it resets the failure streak and retains the next pending occurrence. It does not
replay the failed slot or bypass `needs_review`. An explicit resume after a long
pause uses the same one-catch-up policy.

Version-1 one-off records remain supported unchanged. Recurring schedules use
version 2 with a current occurrence and latest completed occurrence in
`schedule.json`. Each finished occurrence and its next-slot transition is archived
under `occurrences/OCCURRENCE_ID.json` before advancing the schedule pointer.
Recovery reuses that transition, preserving occurrence identity and failure counts.
A tick that reconciles an old occurrence does not also dispatch the new one;
newly due work waits for the next invocation. Archives currently have no automatic
retention pruning. No worker/evaluator can reset these host-owned limits.

## Proposed next work: scheduling and continuation

Design draft, 2026-11-04. This section describes the broader target design;
the one-off and elapsed-interval scheduling subset above is now implemented.
The Phase E baseline is commit `bbddb9f`. These are incremental work
packages, not a commitment to build a resident service or restore live processes.

The initial use cases are daily briefs, periodic site-change checks, update
availability checks, and later a task asking to continue at a future time. An
external cron job, timer, or manual invocation supplies the wakeup. Memex stores
what is due and performs bounded work when invoked. With a fifteen-minute trigger,
work due at 10:07 normally becomes eligible at the 10:15 invocation; a stopped
machine or failed trigger delays it further. There is no timing guarantee between
invocations and no implicit polling from ordinary chat or completion startup.

### Boundaries to preserve

- Put schedule records, due-time calculation, occurrence claims, observation state,
  and continuation decisions in `workspace/`. CLI adapters belong in `modes/`.
- Reuse `TaskRunner`, task leases, execution profiles, and the existing runtime.
  Do not introduce another provider/tool loop or import workspace from shared core.
- Keep `SessionHost` an owner of live sessions. Schedule policy, persistence, and
  restart recovery do not belong in it.
- Treat prompts and evaluator output as policy suggestions. They cannot grant
  tools, models, roots, writes, budget, notification destinations, or wake frequency.
- Inspection and dry runs are passive: no model initialization, claim acquisition
  that reserves work, schedule advancement, or task execution.
- Keep new records host-owned. Extend `workspace/scope.py` and its filesystem and
  Docker tests before exposing any new control directory to worker tools.
- Preserve explicit `repeat` semantics. Continuing from a checkpoint must be an
  explicit operation; existing repeat must not silently become continuation.

### Plan 1: an externally triggered due-work command

The first usable increment automates a saved task in the same way a person would
invoke `workspace task repeat`. It requires no evaluator, observation comparison,
notification service, or restart restoration.

Proposed interface (names/options remain subject to implementation review):

```text
memex --project /project workspace schedule add TASK_ID --at ISO_TIMESTAMP
memex --project /project workspace schedule add TASK_ID --every 24h --at ISO_TIMESTAMP
memex --project /project workspace schedule list
memex --project /project workspace schedule inspect SCHEDULE_ID
memex --project /project workspace schedule pause SCHEDULE_ID
memex --project /project workspace schedule resume SCHEDULE_ID
memex --project /project workspace schedule run-due --max-runs 1 --max-seconds 600
memex --project /project workspace schedule run-due --dry-run
```

Scheduling is an explicit grant to execute the saved task/profile unattended.
Registering a schedule does not immediately run it. The first version supports
one-off timestamps with explicit offsets and elapsed-time intervals anchored to
a timestamp. Persist timestamps in UTC. A 24-hour interval is not a promise of
6am local time across daylight-saving changes. Local calendar schedules, IANA
time zones, and their ambiguous/missing-time rules are a later small increment.
No arbitrary cron-expression parser is needed for the first slice.

`run-due` checks a single explicitly selected project/store, runs eligible work
in the foreground, waits for bounded cleanup, writes outcomes, and exits. With
nothing due, it exits promptly without assembling a model session. First-version
execution is sequential. The external scheduler only needs this one command;
Memex does not install or edit system cron/timer configuration automatically.

#### Records and ownership

Use a versioned `schedules/` control directory in the bound workspace. A schedule
record contains its ID, task ID, enabled/paused state, recurrence definition,
next due time, revision, selected execution limits, and creation/update timestamps.
The existing task specification remains the source of its brief, strategy, and
profile. Record the exact schedule revision and effective task-spec digest for
each occurrence. Editing execution authority later must be explicit and auditable;
resuming a schedule does not silently adopt a different task/profile. As with
manual repeat, source files and installation/provider configuration remain live;
a saved specification is not a frozen execution environment. Record effective
configuration provenance without copying credentials into schedule records.

An occurrence is one scheduled opportunity to execute, separate from a task and
its attempts. Persist an ID, schedule/revision, nominal due time, claim state,
linked task/run IDs, execution outcome, and any control error. The identity is
stable for that schedule revision and due slot. A later poll must find the same
occurrence rather than invent a second one for the same slot.

Use a workspace scheduler lock for overlapping `run-due` invocations and short
record locks for schedule edits. Do not hold an edit lock during model execution.
The existing task lease remains authoritative for execution, including manual
repeat attempts. A busy task is deferred without consuming its due occurrence or
blocking other eligible schedules. Recheck pause/revision immediately before a
claim; pausing prevents future claims but does not imply cancelling active work.
Cancelling an active attempt continues to use the existing task control path.

Persist the occurrence claim before dispatch. Add optional caller provenance to
`TaskRunner.run` so the occurrence ID is written into the run record at creation,
not only afterward in a callback. This is workspace bookkeeping, not a new runtime
policy. It lets a later invocation reconcile a crash between creating the run and
linking it back to the occurrence. Do not infer ownership from a PID alone.

#### One invocation

1. Resolve the project/store and validate schedule records without loading models.
2. Obtain the scheduler lock; if another invocation owns it, return a distinct
   structured `busy` result without launching work.
3. Reconcile previously claimed occurrences using saved run outcomes and task
   leases. If an earlier scheduled occurrence still has a live owner, return busy
   rather than dispatching more work after a supervisor loss. A claim with no
   conclusive terminal outcome
   becomes `needs_review`; do not replay it automatically.
4. Select due, enabled schedules in a stable order, respecting task ownership,
   tick limits, and schedule limits. Persist each claim before execution.
5. Start a new ordinary attempt with occurrence provenance. Supervise it with the
   existing timeout/cancellation behavior. No model call is needed just to decide
   whether a timestamp is due.
6. Persist the occurrence outcome and advance its schedule according to the
   recurrence policy. Release ownership and exit after cleanup.

Freeze the selection cutoff at invocation start. Work newly due during this tick
waits for a later invocation; an evaluator cannot create a same-tick execution
loop. Do not start an attempt whose allowed runtime cannot fit in the remaining
tick allowance, including cleanup grace. Use monotonic time for live deadlines
and UTC wall time for persisted due times. Define exit codes and JSON output for
`idle`, `busy`, completed work, and failures before implementing the CLI.

#### Missed runs, failure, and unattended input

The proposed default is one catch-up occurrence for missed intervals, followed by
the next future slot on the original cadence. Record the skipped range/count;
do not replay every missed interval or shift the cadence to completion time.
Occurrence identity prevents a backward wall-clock adjustment from rerunning an
already consumed slot. A large forward jump follows the catch-up policy.

A failed occurrence is still consumed; the next regular occurrence is distinct
from retrying it. Permit future regular checks under a configured consecutive-
failure ceiling, then pause for attention. Unknown side-effect outcomes and
ambiguous crash windows pause immediately. Default to no automatic same-occurrence
retry. Cancellation pauses further automatic work until explicitly resumed.

Use fresh unattended interaction policy even when the saved task was created in
chat with `user_input=True`. A scheduled attempt must not spend its whole allowance
waiting for a terminal that does not exist. Record `needs_input` and pause for the
user; do not fabricate a reply or carry forward a stale interaction ID. Later
continuation support can accept an answer as new input to a new attempt.

First-slice limits include per-attempt profile ceilings, maximum attempts per tick,
a tick deadline, a minimum allowed interval, and a consecutive-failure ceiling.
Exact defaults are implementation choices to document before release. Recurrent
monitoring is intentionally long-lived, but each invocation remains bounded.

#### First-slice acceptance checks

- With a fake clock, idle/dry-run/list/inspect make zero model calls and no changes.
- Two concurrent invocations cannot dispatch the same occurrence; manual repeat
  and a scheduled attempt cannot both acquire the same task lease.
- Simulated crashes before claim, after claim, around run creation, and after a
  result write either reconcile conclusively or stop for review without replay.
- Busy tasks defer fairly; pause/revision races cannot launch an obsolete claim.
- Missed intervals, forward/backward clock changes, one-off completion, failures,
  cancellation, and an insufficient remaining tick allowance have explicit tests.
- Chat-created tasks run unattended when scheduled and stop cleanly on input needs.
- Schedule/occurrence records are read-only to file and command tools, including
  nested helpers; ordinary chat/completion/agent suites remain unchanged.

### State between runs: execution and task disposition

Reserve a separate representation for what the work means. A successful provider
run is not proof that a user goal is done, that a site changed, or that another
attempt is justified. These concepts should not be packed into `RunOutcome.status`.

| Record | Answers |
| --- | --- |
| Execution outcome | Did this attempt complete, fail, time out, or need input? |
| Observation/checkpoint | What was learned, what changed, and what remains? |
| Task disposition | Should we wait, continue, finish, or ask for attention? |
| Notification event | Is there something the user requested to hear about? |

Proposed dispositions are `wait_for_schedule`, `continue_after`, `complete`, and
`needs_attention`, each with a reason and source attempt. Store observations and
decisions as bounded, versioned artifacts with references to supporting evidence.
Keep a pointer to the latest accepted state; do not grow an unbounded prompt by
appending the entire attempt history. A failed fetch is an unknown observation,
not evidence that a monitored site is unchanged.

For example:

| Use case | Saved observation | Intended decision |
| --- | --- | --- |
| Daily brief | Covered period, sources, resulting brief | Publish the brief when due; wait for the next scheduled period |
| Site monitor | Previous successful content digest and relevant extracted facts | If unchanged, wait quietly; if meaningfully changed, notify |
| Update check | Last observed release and last notified release | Notify for a newly relevant release once; otherwise wait |
| Continuing investigation | Progress, unresolved questions, evidence, next action | Continue only when justified and within remaining allowances |

The first scheduling slice records execution outcomes only. It must not claim to
understand these dispositions yet. A subsequent state/policy slice adds them
without redesigning occurrence identity or the execution loop.

### Optional evaluation and notification policy

An optional separate evaluation call can consume the previous accepted state,
current attempt outcome, bounded evidence, and the user's saved policy prompt.
For example: “If nothing meaningful changed, defer until the next scheduled check
and stay quiet.” Another task may explicitly request a no-change report. Preserve
that preference instead of imposing a universal notification behavior.

Prefer deterministic comparisons where sufficient; do not require a judge call
for every timer event. When evaluation is enabled, give it a separate scratch
session with no side-effect tools and a strict structured-output schema. Save its
prompt/version, inputs, result, and validation outcome. Invalid, failed, or
ambiguous decisions become `needs_attention`, not an automatic rerun.

Reserve evaluation calls inside the occurrence's total call/time allowance before
starting work. A worker that exhausts its portion cannot cause an unbudgeted judge
call. Aggregate accounting must include helpers and evaluators; do not add a
hidden mandatory model pass to ordinary completion or task execution.

The host validates proposed state updates and wake times. A model cannot increase
its limits, unpause a schedule, or choose a notification destination. Commit the
accepted observation, disposition, and any notification intent under one state
revision so a later poll cannot confuse old observations with a new decision.

Notification delivery is a separate adapter. Begin with a local durable outbox
and inspectable events; no email, push, or other external transport is presumed.
Deduplicate event creation using the occurrence/decision ID and persist delivery
receipts separately. Delivery failure must not rerun the investigation. External
exactly-once delivery requires transport support; otherwise disclose possible
duplicates at uncertain delivery boundaries. No-change monitoring stays quiet
unless the user explicitly chose periodic reports; failures and required action
remain distinguishable from “no change.”

### Plan 2: checkpoint-based continuation and recovery

Build this after scheduled repeat is useful. The same due-work command can dispatch
both recurring occurrences and explicit one-off wakes. It must not interpret every
`incomplete` or `failed` outcome as a reason to continue.

A checkpoint should contain a bounded progress summary, evidence/artifact
references, unresolved work, a concrete next action, known completed side effects,
uncertain operations, and provenance linking it to its source attempt. Record the
requested wake time/reason separately from the host-approved wake time and limits.
The host, not the model, allocates checkpoint IDs and commits accepted revisions.

A continuation starts a new supervised attempt under the existing task identity,
linked to the parent attempt and checkpoint. Assemble a new session using the
original brief, explicitly selected context, and the accepted checkpoint as
background. Revalidate the project, profile, tool restrictions, and available
artifacts before dispatch. Do not deserialize a Python stack, replay old tool
calls, reuse old question tokens, or assume native provider continuation state
can be restored. Missing or incompatible inputs require attention.

Manual continuation should precede automatic continuation in implementation. A
future `workspace task continue TASK_ID --checkpoint CHECKPOINT_ID` and a scheduled
wake must use the same application entry point. Keep `repeat` available for a
fresh execution of the saved brief without automatically using a checkpoint.

Automatic continuation requires an explicit user-enabled policy and host-enforced
limits across the chain: maximum continuations, cumulative model calls, cumulative
active runtime, an absolute expiry, and a minimum wake delay. These limits do not
reset when a new attempt starts. A recurring monitor gets a new bounded allowance
per regular occurrence; continuation within that occurrence consumes its existing
chain allowance. Add rolling schedule-level limits if repeated episodes need an
additional cost/frequency ceiling. Models may request less work, never expand it.

Persist consumed/reserved allowances before dispatch. Where a crash makes usage
uncertain, retain the reservation rather than refunding it optimistically. Reject
identical checkpoints that request the same next step repeatedly; also count
consecutive no-progress decisions, since superficial rewording can defeat exact
hash comparisons. Model claims of progress are evidence to assess, not a substitute
for hard chain ceilings. Reaching a limit produces `needs_attention` or a final
partial result, not a new self-scheduled chain.

Waiting releases the process: the accepted checkpoint and approved wake are durable,
then execution exits. A later external poll creates the next attempt. A human reply
after process exit is new input for that attempt, with provenance; it cannot resume
the old stepwise action. Unknown side effects, interrupted tool execution, or a
checkpoint produced before the last known action require reconciliation before
continuation. No generic automatic side-effect retry is part of this design.

#### Continuation acceptance checks

- Manual and scheduled continuation consume the same immutable checkpoint snapshot.
- Limits survive attempts, crashes, repeated polls, and model requests to reset them.
- Duplicate wakes, unchanged checkpoints, and repeated no-progress decisions stop
  without an unbounded chain; normal recurring no-change checks still wait quietly.
- Stale human replies and provider-specific metadata cannot resurrect dead actions.
- Interrupted side effects and missing artifacts stop for attention rather than
  silently replaying operations or inventing successful completion.
- Existing `repeat`, ordinary task execution, and shared runtime outcomes retain
  their current meaning.

### Suggested implementation sequence and open choices

1. Implement schedule records and one-shot `run-due` for explicit repeat, including
   ownership/crash tests, unattended input handling, and execution provenance.
2. Add recurring intervals, catch-up behavior, failure ceilings, and passive
   inspection. Review these together as the first usable scheduling release.
3. Add accepted observation state, configurable disposition policy, and local
   notification events. Introduce the optional evaluator only where it helps.
4. Add manual checkpoint continuation, then opt-in scheduled wakes with persistent
   chain budgets. Review ambiguous side-effect handling before enabling automation.

Before each increment, settle only its relevant choices: CLI names and exit codes;
minimum interval and tick defaults; failure threshold; record version/migration
rules; calendar/time-zone support; checkpoint size and retention; evaluator schema
and budget split; notification destinations and delivery guarantees. Full live-
session restoration and an always-on runner remain outside this plan. The one-off and interval scheduling increments are available;
state evaluation and continuation remain planned.
