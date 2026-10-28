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

Workers and synthesis inherit `[AGENT].default_model` (`glm-5.3` for this
experiment). The local alias must exist in your model configuration. Explicit
`-m` takes precedence. `--synthesis-model` can select a different answer model.
Existing chat defaults are unaffected.

## Configuration

```ini
[WORKSPACE]
directory = .memex-workspace
agents = 2
max_steps_per_agent = 12
max_tokens = 4096
writes_policy = deny
run_timeout_seconds = 300
synthesis_timeout_seconds = 90
synthesis_model =
worker_prompt = workspace/contribute
synthesis_prompt = workspace/synthesize
synthesis_max_chars = 48000
```

Relative workspace paths resolve against the investigated repository (the
configured tool base directory by default). The workspace persists across runs;
different repositories should generally have different workspace directories.
The default local directory is excluded from Git.

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
```

Status reads saved snapshots without invoking a model or controlling the run.
The newest run is selected when no ID is provided. Closing the monitor has no
effect on workers. Stop the foreground run with Ctrl-C; the engine stops workers
and preserves completed notes. A saved running record whose engine process no
longer exists is displayed as interrupted, without rewriting that record.

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
    worker-1/
      status.json        # Atomic snapshot
      events.jsonl       # Observable step/tool/publication events
      process.log        # Diagnostic stdout/stderr
      response.md        # Final contribution, if any
    worker-2/...
    synthesis/...
```

Each process owns its status and event log. Snapshot replacements and note
publication are atomic so readers do not observe half-written records. Notes
cannot overwrite earlier contributions; corrections publish a new note citing
the old one. Publication validates referenced IDs. Sources such as code paths
and URLs belong in the Markdown content.

The workspace tool provides keyword search, reading a note, and publication.
Search returns a bounded selection, not the entire history. New peer notes are
also made visible between model steps through Memex's ordinary context pipeline.
Agents cannot receive an update during an already-running model request.

Synthesis receives bounded current findings and referenced historical notes,
with inclusion counts recorded in `run.json`. It does not receive private
reasoning traces. Large notes and final contributions may be shortened in this
input; full saved responses remain available separately. Relevance selection and
evidence quality remain experimental.

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
