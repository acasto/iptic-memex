# Sessions

## Private CLI chat

```bash
python main.py chat --private
python main.py tui --private
python main.py web --private
```

Private chat starts fresh and keeps the conversation in memory until exit. Memex
skips session autosave, diagnostic logs, usage statistics, persistent memory and
its backups, configured hooks, and MCP autoload. Session resume, saved chats,
checkpoints, `/save code`, and workspace attachment are unavailable in private
chat.

The configured model still receives your messages. Remote providers and tools
apply their own retention policies. Terminal scrollback and files you explicitly
write with tools can still contain chat content.

## Saved sessions

Interactive session persistence (autosave/resume/checkpoints) can be enabled via:

```ini
[SESSIONS]
session_directory = ~/.config/iptic-memex/sessions
session_autosave = false
session_autosave_limit = 20
session_checkpoint_limit = 50
programmatic_session_autosave_limit = 20
programmatic_session_checkpoint_limit = 50
```

Commands:
- `/show sessions` - list saved sessions, newest first, with date, user turn count,
  model, and opening/latest user messages
- `/load session` - choose a saved session from the same descriptive list
- `/load session <id>` - resume a saved session directly (checkpoints fork by default)
- `/save session [title]` - save the current conversation under its ID, including when autosave is off
- `/save checkpoint [title]` - save a checkpoint template

CLI:
- `python main.py list-sessions` (list saved sessions)
- `python main.py chat --resume` (open the session picker)
- `python main.py chat --resume --latest` (most recently saved, without prompting)
- `python main.py chat --resume --last` (alias for `--latest`)
- `python main.py chat --resume <id-or-path>` (explicit session)

The picker accepts a numbered choice and includes a Cancel option. Opening/latest
message excerpts and titles are collapsed to one line and truncated to 80 characters
(including the ellipsis). Turn counts include user messages and attachment-only
inputs, excluding assistant replies, tool results, and automatic follow-ups.
Existing session files work without migration. Creation time is preserved across
saves; the displayed date is the last save time, in the local timezone.

`tui` and `web` accept the same resume options. Bare `--resume` opens the picker
inside the interface, and `--resume --latest` selects the most recent session.

## Using a saved conversation from a script

Run `scripts/memex session --help` to discover the commands. Each invocation
exits after its operation. To consult a model across several shell commands:

```bash
memex=scripts/memex
sid=$("$memex" session new --model MODEL --title "Migration review")
"$memex" session send "$sid" "We can pause writes for two minutes. Suggest a migration plan."
"$memex" session send "$sid" "Which step needs to fit inside that window?"
"$memex" session read "$sid" --last 4
"$memex" session search "$sid" "writes" --json
```

Replace `MODEL` with a name from `list-models`, or omit `--model MODEL` to use
the interactive chat default. `new` prints the session ID. For multiline input,
pipe text into `session send ID --stdin-message`. A successful `send` prints
the reply after saving it. With `--json`, it prints the same run outcome fields
as `agent --json`, including `status`, `last_text`, and `error`.

To hand off a human conversation, use `/save session` and quit, or enable
autosave and quit. Find its ID with `session list --json`. A caller can read it,
search it, or send a turn directly by ID. Later, `chat --resume ID` opens the
updated conversation. Explicit interactive saves and autosave on exit continue
to follow your settings. Checkpoints can be read and searched. To continue one,
resume it interactively and use `/save session` to save the fork under its own ID.

`read` and `search` access saved message text without starting a provider.
Both accept `--last N` and `--json`. JSON rows contain the one-based transcript
`index`, `role`, and `text`. Search uses a case-insensitive literal substring
and returns matches in chronological order.

### Origins and history

Chat, TUI and Web create sessions with `origin=interactive`. Existing saves
without an origin are treated as interactive. `session new` creates a
`programmatic` session. Sending, saving or resuming preserves the origin.

- `session list`, `list-sessions`, `/show sessions`, the resume picker and
  `--resume --latest` default to interactive history.
- Use `session list --origin programmatic` to find consultations or
  `session list --all` to see both origins. `list-sessions --all` and
  `/show sessions --all` also show both. The picker offers **Show all sessions**
  when other entries exist. `/load session --all` opens that view directly.
- Each origin has its own retention limits for sessions and checkpoints.
  A programmatic save uses the two `programmatic_` limits shown above.
  A limit of zero disables pruning for that origin and kind.

### Turn and ownership boundaries

`send` uses the saved conversation and chat policy, runs one user turn through
any tool follow-ups, and saves the resulting transcript. A run that needs a
confirmation returns `needs_input` and exits with status 1. Failed, cancelled
and incomplete runs also exit nonzero. Inspect the saved transcript before
deciding on a follow-up. A later send starts a new user turn. Pending approvals
and in-flight provider requests exist only in the process that started them.

A scripted send holds a writer lock for the turn. An interactive client holds
the lock on a saved session from explicit save or resume until it closes or
loads another session. A competing writer receives a busy error immediately.
Readers can inspect the last committed transcript while a writer is active.
