# Sessions

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

Bare `--resume` in TUI/Web mode continues to load the most recent session.
