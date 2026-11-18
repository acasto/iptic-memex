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
```

Commands:
- `/show sessions` - list saved sessions, newest first, with date, user turn count,
  model, and opening/latest user messages
- `/load session` - choose a saved session from the same descriptive list
- `/load session <id>` - resume a saved session directly (checkpoints fork by default)
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
