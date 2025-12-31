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
- `/show sessions` - list saved sessions
- `/load session <id>` - resume a saved session (checkpoints fork by default)
- `/save checkpoint [title]` - save a checkpoint template

CLI:
- `python main.py chat --resume` (most recent)
- `python main.py chat --resume <id-or-path>` (explicit session)
