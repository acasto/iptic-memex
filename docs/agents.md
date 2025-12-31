# Agents

Agent mode runs a non-interactive multi-turn loop with tool calls and a configurable write policy.

## Ways to run agents

Recommended: explicit agent subcommand

```bash
python main.py agent --steps 3 -f ./path/to/file
```

Legacy (still supported): top-level file mode with `--steps`

```bash
echo "Summarize this file" | python main.py --steps 2 -f -
```

Notes:
- `--steps` omitted defaults to 1.
- With top-level `-f/--file`, Memex routes to completion mode unless `--steps > 1`.
- The explicit `agent` subcommand always runs agent mode (single-step is allowed).

## Options

Common agent flags (top-level or `agent` subcommand):
- `--steps N` - number of assistant turns (defaults to `[AGENT].default_steps`, fallback 1)
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

## Examples

Multiple turns, full output:
```bash
echo "Implement X and show a diff" | python main.py agent --steps 3 --agent-output full -f -
```

Final-only output (default):
```bash
echo "Summarize this file" | python main.py agent --steps 2 -f notes.md
```

Deny writes, set workspace root:
```bash
python main.py agent --steps 3 --agent-writes deny --base-dir ~/Projects/that-repo -f -
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
