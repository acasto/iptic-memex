# Modes

## Chat mode
Interactive multi-turn conversations with full context management and history.

```bash
python main.py chat
```

## Completion mode (one-shot)
Pipe input for a single response. Useful for scripting.

```bash
echo "What is PI?" | python main.py -f -
```

## Agent mode (non-interactive)
Run N assistant turns with tool calls and a configurable write policy.

```bash
python main.py agent --steps 2 -f ./path/to/file
```

Notes:
- `--steps` omitted defaults to 1.
- `--agent-output` controls output mode (final/full/none).

## TUI mode
Terminal UI with stepwise interactions backed by the same TurnRunner.

```bash
python main.py tui
```

## Web mode
Local Web UI with the same action pipeline.

```bash
python main.py web
```

## Default model selection
- Interactive modes (chat/tui/web): `[DEFAULT].default_model` unless you specify `-m/--model` or `/set model`.
- Non-interactive (completion, internal runs, agent): `[AGENT].default_model` when no model is provided.
- Explicit model always wins.
