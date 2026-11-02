# Experimental workspace mode

Workspace mode runs independent Memex agents against a persistent collection of
Markdown contributions. Workers choose their own investigations, use the existing
action/tool registry, and can discover, build on, or correct other contributions.
The engine manages process lifecycle and limits; it does not assign personas or
divide the question into predefined subtasks.

Two workers are started concurrently by default. A fresh model context then
answers the original question using published findings and worker outcomes.
Private worker chat histories are not passed to synthesis. This is synthesis,
not an independent verification pass.

The `workspace/` package owns supervision (`engine.py`), persistence (`state.py`),
reports (`report.py`), the subprocess entry point (`worker.py`), and shared-note
tools (`tools.py`). `runtime.py` builds sessions using explicit application
services and submits through the common agent/completion adapters. Core does not
import workspace. CLI and action-discovery modules remain thin entry points.

Workers use the shared runtime through `app/agent.py`; synthesis uses the
tool-free completion profile, including when worker tools were explicitly
selected. Peer-note and remaining-budget updates enter through an explicit
pre-call service, separate from passive event reporting. Process limits, saved
artifact formats, and the bounded research strategy are preserved.

## Start a run

From the repository root:

```bash
python main.py workspace run "Where is default model selection implemented?"
```

Select an explicit model, additional tools, and a repository:

```bash
python main.py -m glm-5.3 --tools file,cmd workspace run \
  --repository /path/to/project \
  --directory /path/to/shared-workspace \
  --agents 2 --steps 12 \
  "Locate the request authentication flow and cite the relevant code."
```

Global options such as `-m`, `--tools`, `--base-dir`, `--agent-writes`, and
`--conf` go before `workspace`. Workspace options go after `workspace run`.
The question may instead be supplied with `--message` or `--stdin-message`.
`--json` returns the run record and answer. File attachments are not supported
in this initial mode; use `--repository` for source access.

Workers inherit `[AGENT].default_model`. Synthesis uses
`[WORKSPACE].synthesis_model` when configured, otherwise the worker model.
Both aliases must exist in your model configuration. Model choices belong in
configuration or CLI options; workspace does not hardcode a local model.

Model precedence for synthesis, highest first:

1. `workspace run --synthesis-model MODEL`
2. Global `-m MODEL` (overrides both workers and synthesis)
3. `[WORKSPACE].synthesis_model`
4. The worker model, when the synthesis setting is blank

For example, `python main.py -m glm-5.3 workspace run "Question"` keeps the
entire run local. To change only the worker model while retaining a separate
answer model, specify both `-m WORKER_MODEL` and `--synthesis-model ANSWER_MODEL`.

## Configuration

```ini
[WORKSPACE]
directory = .memex-workspace
agents = 2
max_steps_per_agent = 12
max_tokens =
writes_policy = deny
run_timeout_seconds = 300
synthesis_timeout_seconds = 90
synthesis_model = gpt-6.1
worker_prompt = workspace/contribute
synthesis_prompt = workspace/synthesize
synthesis_max_chars = 48000
```

Relative workspace paths resolve against the investigated repository (the
configured tool base directory by default). The workspace persists across runs;
different repositories should generally have different workspace directories.
The default local directory is excluded from Git.

A blank `max_tokens` inherits the selected model's output limit. Set a workspace
override or use global `-l/--max-tokens` for a deliberate shared limit. Workers and
synthesis otherwise retain their respective model settings. Existing model/provider
`supplemental_prompt` addenda apply through the normal prompt pipeline; use those
for demonstrated model-specific needs rather than restricting the shared prompts.
Addenda apply to every session using that model; phrase workspace-only guidance
conditionally if it should not affect ordinary chat or completion runs.

The synthesis allowance is reserved *within* the total timeout. The default
worker phase therefore has up to 210 seconds, with the remaining 90 seconds
available for synthesis. Workers that finish early leave more time available.
When overriding `--timeout`, keep `--synthesis-timeout` smaller than it.
Shutdown may take an additional short cleanup grace period.

Step and time limits are independent. Increasing `--steps` does not extend the
worker phase. For example, `--steps 25 --timeout 600` allows up to 25 turns per
worker within a 510-second worker phase, retaining the default 90-second reserve.
Startup and `workspace status` display both limits. A worker can time out before
reaching its step limit.
Workers receive their remaining time at each turn boundary through an ordinary
context update, alongside the existing turn limit. This reports the execution
budget without assigning work or requiring a particular investigation strategy.

Tool profiles inherit the normal non-interactive settings and blocklist. The
`workspace` tool is added to the selected profile and is only exposed inside
workspace sessions. Blocking it prevents a contributor from running. `--tools
None` disables additional tools, retaining shared-state access. MCP is off
unless explicitly enabled with the existing non-interactive `--mcp` flag.
Global file memory and lifecycle hooks are disabled in these scratch sessions.
File-memory restrictions apply before prompt/provider setup and propagate to
nested helpers without changing the shared configuration. Workspace access and
required workspace tools belong only to contributor sessions, not synthesis or
ordinary helper sessions.

The default write policy denies ordinary filesystem changes, including shell
writes through Docker's read-only mounts. Publishing a contribution is a separate,
narrow operation: the workspace tool can create immutable notes but cannot modify
repository files or arbitrary paths. Non-allow write policies require the Docker
command backend if `cmd` is exposed. `--agent-writes allow` explicitly enables
ordinary writes; concurrent editing is not coordinated by this MVP.

Command containers remain ephemeral, with per-session temporary storage. The
engine does not host persistent application services or provision per-agent
container environments.

## Inspect from another terminal

```bash
python main.py workspace status --watch
python main.py workspace status --directory /path/to/shared-workspace --watch
python main.py workspace status RUN_ID --json
python main.py workspace report RUN_ID
python main.py workspace report RUN_ID --json
```

Status reads saved snapshots without invoking a model or controlling the run.
The newest run is selected when no ID is provided. Closing the monitor has no
effect on workers. Stop the foreground run with Ctrl-C; the engine stops workers
and preserves completed notes. A saved running record whose engine process no
longer exists is displayed as interrupted, without rewriting that record.

`report` also makes no model calls. It reports wall time, cumulative token usage
across requests (including cache hits), tool counts, note delivery, workspace-tool
reads/search results, time to first publication, and explicit peer citation links.
Workspace validation errors, including rejected publication sizes, are retained in
worker event logs and shown by `report`. A returned `ok=false` tool result is
logged as an error even when its action completed without raising an exception.
Delivery means a preview entered context; a citation means an ID was recorded in
references or prose. Neither establishes verification or useful collaboration.
Unavailable historical measurements are `null`, not assumed zero.

## Compare synthesis models or prompts

```bash
python main.py -m glm-5.3 workspace synthesize RUN_ID
python main.py -m gpt-6.1 workspace synthesize REPLAY_RUN_ID
python main.py -m glm-5.3 -p /path/to/alternate-synthesis.md workspace synthesize REPLAY_RUN_ID
```

Each command starts only a fresh synthesis session and saves a **new run** with
its own answer. The source run, notes and answer remain unchanged. The model
defaults to `[WORKSPACE].synthesis_model` or the normal non-interactive default;
explicit `-m` wins. For this command, global `-p` overrides the synthesis prompt.
`--timeout` limits this synthesis alone (90 seconds by default).

New investigations save the exact evidence message in `synthesis-input.json`,
with its SHA-256 in `run.json`. Replays reuse that frozen input, even if more
notes appear later. Old runs without a snapshot reconstruct one in the **new**
run; replay that new run for subsequent comparisons. Reconstruction is not an
exact reproduction of the historical model request. A modified snapshot whose
checksum no longer matches is rejected.

For older failed investigations without a snapshot, reconstruction can recover
historical notes explicitly read by their workers, even if they published no new
notes. An answer based only on historical notes is labeled partial. The failed
source run remains unchanged.

Each session saves its resolved `system-prompt.md` and prompt checksum, selected
model name, and configured output limit. The prompt includes normal supplemental
addenda and, for workers, Agent Mode's finish/write-policy instructions. These
are evidence/prompt snapshots, not a full API-request capture. Provider defaults,
reasoning effort, sampling, budgets and endpoint load can also affect comparisons.
To compare model quality, hold evidence and prompt constant; to compare prompts,
hold model and evidence constant. Synthesis replay does not evaluate worker
investigation or collaboration quality.

Replaying with an external provider sends the included saved findings through
that provider's configured API, just as an ordinary Memex completion does.

## Saved records and contributions

```text
workspace/
  notes/
    note-<id>.md          # Human-readable, immutable contribution
    note-<id>.json        # Author, run, timestamp, and referenced note IDs
  runs/<run-id>/
    question.md
    run.json             # Settings, phase, limits, outcome
    answer.md
    synthesis-input.json # Frozen evidence message and checksum, when synthesis starts
    worker-1/
      status.json        # Atomic snapshot
      events.jsonl       # Observable step/tool/publication events
      process.log        # Diagnostic stdout/stderr
      response.md        # Final contribution, if any
      system-prompt.md   # Resolved prompt, including ordinary addenda
    worker-2/...
    synthesis/...
```

Each process owns its status and event log. Snapshot replacements and note
publication are atomic so readers do not observe half-written records. Notes
cannot overwrite earlier contributions; corrections publish a new note citing
the old one. Publication validates referenced IDs. Sources such as code paths
and URLs belong in the Markdown content.

Tool publications are `finding` records. Automatically published final responses
are `summary` records linked to their author's findings. Valid note IDs explicitly
mentioned in prose are also captured as references. Summaries preserve new
information without being presented as additional independent evidence. Historical
final summaries are identified from saved worker results during synthesis/reporting.

The workspace tool provides keyword search, reading a note, and publication.
Search returns a bounded selection, not the entire history. New peer notes are
also made visible between model steps through Memex's ordinary context pipeline.
Agents cannot receive an update during an already-running model request.
Each update includes up to eight unseen current-run peer notes, plus the existing
four search-selected previews at startup. Already-delivered and own current-run
notes do not consume the peer-note allowance, so finite publication backlogs drain
over subsequent calls. Failed context attachment ends the worker visibly rather
than claiming delivery. Preview bodies remain limited to 1,500 characters each.

An invalid publication reports validation and size diagnostics without putting
the rejected body in the event log; repairable drafts are retained separately. If a note was saved successfully but its activity-log
write fails, the tool returns the note ID with a warning and a successful result;
it does not report the committed publication as failed.

Synthesis receives bounded current findings, historical notes explicitly read
through the workspace tool by the run's workers, and referenced historical notes,
with inclusion counts recorded in `run.json`. Identical text is included once
with all originating IDs, authors and references retained. Other notes remain
separate; there is no semantic deduplication. Whole notes are selected within
`synthesis_max_chars`; omission counts are visible to synthesis. Auto-published
final responses exceeding the note-size limit are shortened, with full text kept
in `response.md`. Private reasoning traces are not passed along. Relevance
selection and evidence quality remain experimental.

Historical search hits and automatically delivered previews alone do not select
notes for synthesis. Reusing a previously published note does not establish that
its claims were revalidated. When no new contributions exist, selected historical
notes can still support a partial answer with an explicit notice. Run records and
reports distinguish current from historical evidence.

## Outcomes and initial limits

Completed runs print and save an answer. A worker failure, exhausted step budget,
or timeout yields a partial result when usable findings exist. If synthesis fails,
the answer file contains a labeled incomplete report and available contributions.
Partial results are successful CLI deliveries with an explicit warning; runs
without findings fail. Interrupted foreground runs exit with status 130.

This version has bounded invocations, not a background scheduler. There is no
live steering, automatic consensus, independent judge, or web/TUI dependency.
The shared environment is the durable part; workers can end between questions.

Focused tests use real concurrent processes with offline providers to exercise
the actual worker entry point, tools, synthesis isolation, replay, and timeout
cleanup. They also cover note backlogs, publication diagnostics, and passive
readers with application/provider imports disabled. Model quality still requires
a separate live-model evaluation.

## Adaptive research experiment

`workspace research` keeps one continuing investigation and lets it request one
independent focused branch at a time. The lead chooses questions and priorities;
the scheduler enforces mechanics and limits. Fixed `workspace run` is retained
as a comparison and does not acquire this policy.

```bash
python main.py -m MODEL --tools file,cmd workspace research \
  --brief examples/workspace/tui-review.md --repository /path/to/source
python main.py -m MODEL --tools file,cmd workspace research \
  --brief task.md -f supporting-notes.pdf --calls 31 --timeout 600 \
  --synthesis-timeout 120 --synthesis-model ANSWER_MODEL
python main.py -m MODEL --tools file,cmd workspace research \
  --brief task.md --strategy single
```

The brief is task input, separate from `-p` (system prompt) and `-f` (supporting
attachments). Text, `--message` and `--stdin-message` remain available. Attachments
are copied into the run before loading with existing Memex context loaders;
`inputs.json` records sources, snapshots and hashes. URLs are sources for tools
to fetch, not evidence already inspected. `--steps` is intentionally rejected:
`--calls` bounds the entire run, including lead coordination, all branches, and
one reserved synthesis call. Its default is `[WORKSPACE].research_total_calls`
(fallback 32). Existing run/synthesis timeout settings apply. Explicit `-m`
selects research and synthesis unless `--synthesis-model` overrides the latter.

The lead has a `research` tool to record its working brief, advisory activity,
open/resolved/deferred questions, focused requests and a sufficient/partial
coverage assessment. Branch requests cite relevant evidence and reserve an
allowance from the shared ledger. Their unused calls are returned after exit.
Model-backed internal helpers consume the same allowance, including helpers
inside a focused branch. Their usage is not silently counted as zero when
separate helper metrics are unavailable.
An unavailable interrupted-worker count is charged at its reserved ceiling and
labeled conservative; missing token/cost metrics remain unknown. Failed zero-call
branches still count toward a finite dispatch ceiling. Branches can propose
questions in their findings but cannot recursively delegate.

A branch runs alongside the lead, in a separate context using the same tools and
source access. `research wait` collects its result without repeated model calls.
Branch results are evidence; only the lead owns scheduling/question state.
`research.json` saves the brief versions, questions, decisions, delivery backlog,
branch requests/results, assessment and budget. The pre-call view includes only
recent decisions and the latest brief interpretation. Activity expires after
two minutes and is advisory, not a work reservation. Status/report remain passive.

Fresh synthesis receives selected findings plus frozen research state and gaps,
not private worker histories. A lead's 'sufficient' assessment is not verification.
Missing/partial assessments or failed/incomplete work produce a partial answer.
Synthesis replay preserves that assessment. A branch saying done does not close
its question automatically; the lead must assess its evidence. Timeouts/cancel
reach the lead's whole process group, including a branch after a lead crash.

Publication accepts optional sources, method, uncertainty and relation metadata
(`extends`, `contradicts`, `supersedes`, `checks`). These are contributor claims,
not automatic verification. A stable `publication_id` makes identical retries
return the same note; changed content needs a new ID. Rejected publications retain
scoped drafts readable with `read_draft`, with actionable repair errors. Oversized
display titles are shortened while the submitted title is retained in provenance;
substantive evidence is never silently shortened by this tool. Drafts are not
published findings or synthesis evidence.

The experiment is foreground and bounded. It has no live steering, arbitrary
Python-stack resume, recursive delegation, permanent manager persona, or automatic
artifact-building phase. The `single` strategy retains the same goal/state/note
tools while disabling independent branches. Compare it and fixed workers under
the same model, tools, sources and aggregate allowance before increasing complexity.
