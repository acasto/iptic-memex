# CLI and chat commands

## CLI help

```bash
python main.py --help
python main.py <subcommand> --help
```

## Chat mode quick reference

- Type `/help` to see commands.
- Tab completion shows `/...` suggestions at the prompt.

### Context loading
- `/load file` or `/file` - load file content (auto-detects pdf/docx/xlsx/pptx/msg/audio/images)
- `/load multiline` - paste multiline text into context
- `/load web` - fetch a web page and extract content
- `/load raw` - load unformatted text (useful for raw chat transcripts)
- `/load rag` - query configured RAG indexes and add a summary
- `/clear context` - clear current turn context

### Chat management
- `/save chat`, `/save last`, `/save full` - save chat
- `/load chat` - manage saved chat sessions
- `/show chats` - list saved chats
- `/export chat` - export chat to Markdown/TXT/PDF
- `/clear chat` - reset chat
- `/clear last [n]`, `/clear first [n]` - trim history
- `/reprint`, `/reprint all`, `/reprint raw` - reprint history

### Sessions
- `/show sessions` - list saved sessions
- `/load session <id>` - resume a saved session (checkpoints fork by default)
- `/save checkpoint [title]` - save a checkpoint template
- `python main.py list-sessions` - list saved sessions

### Settings and shortcuts
- `/show settings`, `/show tool-settings`, `/show models`, `/show messages`, `/show usage`, `/show cost`
- `/set model <name>`
- `/set option <key> <value>`, `/set option-tools <key> <value>`
- Shortcuts: `/set stream <on|off>`, `/set reasoning <minimal|low|medium|high>`, `/set temperature <0..1>`, `/set top_p <0..1>`

### Integrated tools
- `/run code` - extract and execute code blocks (requires confirmation)
- `/save code` - save code blocks to a file
- `/run command` - run a shell command and capture output
- `/load rag` - query RAG indexes
- `/rag update` - build or refresh indexes
- `/rag status` - show index status

## Agent mode examples

- Multiple turns with full output:
  ```bash
  echo "Implement X and show a diff" | python main.py --steps 3 --agent-output full -f -
  ```
- Final-only output (default):
  ```bash
  echo "Summarize this file" | python main.py --steps 2 -f notes.md
  ```
- Deny writes:
  ```bash
  python main.py --steps 3 --agent-writes deny -f project.md
  ```
- Point at another workspace root:
  ```bash
  python main.py --steps 3 --base-dir ~/Projects/that-repo
  ```

## Resume sessions from CLI

```bash
python main.py chat --resume
python main.py chat --resume <id-or-path>
```
