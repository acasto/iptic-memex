# Memex terminal workbench

Start with `python main.py tui`. The composer keeps three lines for a draft.
Enter sends it, and Ctrl+J inserts a newline. Click a function key along the
bottom or press F1 for the keyboard reference.

| Key | Action |
| --- | --- |
| F2 or Ctrl+K | Open the command palette |
| F5 | Inspect the current chat run. Left and Right browse recent turns |
| F6 | Watch the current project’s workspace tasks |
| F7 | Open the highlighted transcript message for reading or copying |
| F8 | Review status messages |
| Ctrl+F | Search visible messages and tool output. Enter locates a match |
| Ctrl+S | Toggle provider streaming between turns |
| Ctrl+X | Cancel the active chat turn |
| Ctrl+Q | Quit |

At 100 columns or wider, a side panel follows the latest run. The inspector
shows tool paths or commands, model calls, requests for input, the outcome,
and the runtime’s text-token estimate for each call. The gauge uses the model’s
configured context window when available. Provider-reported input and cached
tokens appear separately when available for the last call. The inspector keeps
the latest 24 turns and up to 160 events per turn in memory. Opening a saved
conversation restores its visible messages; new runs begin a new inspector history.

## Following an agent task

Press F6 to open the workspace selected by the project root and
`[WORKSPACE].directory` (default `.memex-workspace`). Work which needs your
input appears first. Select a task with the arrow keys to follow its assignments,
file reads, commands, budget and outcome. On a narrow terminal, Enter opens the
activity and Esc returns to the task list. V reads the full latest handoff or
answer. C copies the task ID. Watching an external task leaves its owner in control.

When a session task has stopped with a saved question, A opens its handoff.
Review the progress and execution profile, type an answer, and choose **Continue
task** (Alt+Enter). This starts a fresh attempt under the existing task ID and
saved allowance. Closing the watch returns to chat while the attempt continues.
X stops the selected attempt if this TUI owns it. Quitting cancels all attempts this TUI owns. An open form belongs to the
specific attempt you reviewed; a stale answer cannot resume a newer attempt.
Workspace Watch is unavailable in private chat.

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

## Offline checks

Run `python -m pytest -q tests/tui` for keyboard navigation, approvals, responsive
layouts, live workspace records, and a stop/answer/continue round trip with a
deterministic worker subprocess.
