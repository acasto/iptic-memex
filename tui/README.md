# TUI Architecture Overview

The Textual user interface is organized around a thin `MemexTUIApp` wrapper in
`tui/app.py` that delegates most behaviours to focused helpers under `tui/`.
This file gives a quick roadmap for the moving parts so new features land in the
right module.

## Execution Flow (high level)

1. **User input** arrives through the Textual `Input` widget. `InputCompletionManager`
   keeps suggestions, file-path completion, and Tab cycling in sync with the
   command catalogue.
2. `TurnExecutor` submits one chat task to the session host. The worker
   runs shared application policy and sends ordered events to the Textual loop.
3. The transcript renders visible deltas, completed messages, and tool activity.
   A modal sends the user's response to the pending request on its run handle.
   Between runs, `TuiOutput` and `OutputBridge` render command output.
4. Command execution uses the dynamic registry exposed by
   `CommandController`, which pulls specs from `user_commands_registry` once at
   startup and feeds both the palette UI and in-line slash suggestions.

## Key Modules

- `tui/app.py`: creates the top-level Textual `App`, wires widgets together, and
  delegates most work to helpers.
- `tui/turn_executor.py`: runs chat turns (`run()`), manages streaming
  lifecycle and live input, and exposes the active assistant message id.
- `tui/output_bridge.py`: collects `OutputEvent`s, maintains status history, and
  renders status lines into the transcript.
- `tui/commands/controller.py`: loads command metadata for TUI use (palette +
  inline suggestions).
- `tui/input_completion.py`: owns command/file completion state for the main
  input field.
- `tui/screens/*`: modal screens (command palette, compose dialog, interaction
  prompts, status history).
- `tui/widgets/*`: reusable widget building blocks (chat transcript, status
  panel, hints, etc.).

## Adding Features

- **New slash commands / palette entries**: extend the underlying command
  registry (via `user_commands_registry`). The `CommandController` already pulls
  fresh specs on mount; reuse its data rather than duplicating parsing logic.
- **Chat activity**: render shared runtime events in `TurnExecutor`.
- **Command output**: route command diagnostics and scope information through `OutputBridge`.
- **Input behaviour**: `InputCompletionManager` consolidates everything related
  to in-field suggestions. Extend it if you need richer completions (e.g., per
  command arguments) so the Textual-specific behaviour remains in one file.

## Notes & Roadmap Ideas

- Status history is stored in `OutputBridge.status_history` and surfaced via the
  F8 modal. If a persistent sidebar returns, call `OutputBridge.set_status_log`
  with a `StatusPanel` instance.
- The helpers are intentionally framework-agnostic where possible. They accept
  callables (`schedule_task`, `emit_status`, etc.) so they can be unit-tested
  outside Textual with simple stubs.
