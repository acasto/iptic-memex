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

Workers inherit `[AGENT].default_model` (`glm-5.3` for this experiment).
Synthesis uses `[WORKSPACE].synthesis_model`, now configured as `gpt-6.1`.
Both aliases must exist in your model configuration. A normal run therefore
uses local GLM workers and sends their published findings to GPT-6.1 for the
final answer. Existing chat defaults are unaffected.

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

Tool profiles inherit the normal non-interactive settings and blocklist. The
`workspace` tool is added to the selected profile and is only exposed inside
workspace sessions. Blocking it prevents a contributor from running. `--tools
None` disables additional tools, retaining shared-state access. MCP is off
unless explicitly enabled with the existing non-interactive `--mcp` flag.
Global file memory and lifecycle hooks are disabled in these scratch sessions.

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

Synthesis receives bounded current findings and referenced historical notes,
with inclusion counts recorded in `run.json`. Identical text is included once
with all originating IDs, authors and references retained. Other notes remain
separate; there is no semantic deduplication. Whole notes are selected within
`synthesis_max_chars`; omission counts are visible to synthesis. Auto-published
final responses exceeding the note-size limit are shortened, with full text kept
in `response.md`. Private reasoning traces are not passed along. Relevance
selection and evidence quality remain experimental.

## Outcomes and initial limits

Completed runs print and save an answer. A worker failure, exhausted step budget,
or timeout yields a partial result when usable findings exist. If synthesis fails,
the answer file contains a labeled incomplete report and available contributions.
Partial results are successful CLI deliveries with an explicit warning; runs
without findings fail. Interrupted foreground runs exit with status 130.

This version has bounded invocations, not a background scheduler. There is no
live steering, automatic consensus, independent judge, or web/TUI dependency.
The shared environment is the durable part; workers can end between questions.

Focused tests use real concurrent fake-worker processes to establish cross-worker
information transfer, failure handling and timeout cleanup, plus the existing
runner and tool tests. A two-worker local-model smoke test is useful for checking
actual tool use and the quality of the final answer.
