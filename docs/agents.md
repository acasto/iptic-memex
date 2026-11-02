# Agents

Agent mode submits bounded tool work through the same runtime as chat and completion,
with a configurable write policy. It does not maintain a separate provider loop.

## Ways to run agents

Recommended: explicit agent subcommand

```bash
python main.py --steps 3 agent -f ./path/to/file
```

Legacy (still supported): top-level file mode with `--steps`

```bash
echo "Summarize this file" | python main.py --steps 2 -f -
```

Notes:
- `--steps` omitted defaults to 1.
- With top-level `-f/--file`, Memex routes to completion mode unless `--steps > 1`.
- The explicit `agent` subcommand always runs agent mode (single-step is allowed).
- Current CLI convention: shared options such as `--steps`, `--agent-output`, `--tools`, and `--base-dir` are top-level Click options, so place them before `agent`. Agent-only options such as `--json`, `--from-stdin`, `--no-hooks`, and agent `-f/--file` go after `agent`.

## Options

Shared agent flags (top-level options; place before `agent` when using the subcommand):
- `--steps N` - maximum provider calls, including tool follow-ups (defaults to `[AGENT].default_steps`, fallback 1)
- `--agent-writes deny|dry-run|allow` - file tool write policy
- `--agent-output final|full|none` - output mode (default: final)
- `--no-agent-status-tags` - disable per-turn `<status>` tag injection
- `--tools` - CSV allowlist for agent tools (use `None` to disable all tools)
- `--mcp` / `--no-mcp` - enable or disable MCP for non-interactive runs
- `--mcp-servers` - CSV of MCP server labels to enable
- `--base-dir` - override filesystem sandbox root

Agent subcommand-only flags:
- `--from-stdin` - read a runner snapshot JSON from stdin (external runner)
- `--json` - return JSON output (for external runner use)
- `--no-hooks` - disable hooks for this run

`final|full|none` changes presentation; provider streaming follows the model's
`stream` setting or `-s`. Exhausting the call budget while tools need a follow-up
returns `incomplete` with stop reason `steps` and a nonzero CLI exit. Completed
tool results remain saved in the transcript. A reply without tools ends the run;
the application also recognizes `%%DONE%%`, `%%COMPLETED%%`, and `%%COMPLETE%%`.
Final text trims these markers; full output leaves them visible. Raw responses
remain opaque provider payloads.

## Examples

Multiple turns, full output:
```bash
echo "Implement X and show a diff" | python main.py --steps 3 --agent-output full agent -f -
```

Final-only output (default):
```bash
echo "Summarize this file" | python main.py --steps 2 agent -f notes.md
```

Deny writes, set workspace root:
```bash
python main.py --steps 3 --agent-writes deny --base-dir ~/Projects/that-repo agent -f -
```

Use the legacy file mode with steps:
```bash
echo "Check facts in this doc" | python main.py --steps 3 -f -
```

External runner snapshot:
```bash
python main.py agent --from-stdin --json
```

## Docker and agents

By default, agents force the Docker CMD tool to use ephemeral containers to avoid contention across parallel runs.

- Control via `[AGENT].docker_always_ephemeral` (defaults to `True`).
- Set to `False` only if you intentionally share a persistent container and understand the implications.
- With Docker CMD, `--agent-writes deny` and `--agent-writes dry-run` force read-only workspace mounts. They also force
  an ephemeral container even if `docker_always_ephemeral = False`, since Docker cannot retrofit read-only bind mounts
  onto an existing container.
- Under deny/dry-run policy, the runtime rejects local CMD calls. Use Docker CMD
  for read-only commands, or explicitly allow writes to use host commands.
