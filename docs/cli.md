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
- `/load multiline` - paste multiline text into context; finish with `.done` on its own line
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

## Completion and agent input

Task text and system prompts are separate:

```bash
python main.py --message "Explain this code" -f example.py
python main.py --steps 3 agent --message "Review this code" -f example.py
printf 'Summarize this document' | python main.py agent --stdin-message -f notes.md
python main.py --system-prompt "Answer briefly" --message "What is PI?"
```

`--message` supplies the user task. `--stdin-message` reads task text from stdin;
it cannot be combined with `--message` or `-f -`. Both task options work on the
root command and on `agent`. Global options such as `--steps` and `--model`
go before the subcommand.

`-p`, `--prompt`, and `--system-prompt` are aliases for the system prompt. They
accept a prompt name, file, chain, or literal text. They do not set the user task.

Files can be supplied before or after `agent`; all attachments are forwarded to
one run. The existing `-f -` convention remains supported: stdin becomes the task
when no explicit task is supplied, or an attachment when `--message` is present.
Piped stdin otherwise requires `--stdin-message` or `-f -`.

The root command runs a completion for one step and routes to the shared agent
pipeline when the effective step count is greater than one. The explicit `agent`
command always uses the agent pipeline, including for one step.

## Agent output and failures

```bash
python main.py --steps 3 --agent-output full agent --message "Review this project"
python main.py --steps 3 --agent-writes deny agent --message "Suggest changes" -f project.md
python main.py --steps 3 --base-dir ~/Projects/that-repo agent --message "Inspect this project"
python main.py --steps 3 agent --json --message "Summarize this file" -f notes.md
```

`--agent-output final` (default) prints the final answer, `full` streams each turn,
and `none` suppresses answer output. Diagnostics and verbose request dumps go to
stderr. A failed stream may already have written partial answer text to stdout.

`agent --json` always prints one JSON result for a run, including when the answer
is empty or execution fails. Its fields are `last_text`, `error`, `status`,
`stop_reason`, `truncated`, `turns`, `usage`, and `cost`. Existing consumers can
continue reading `last_text` and `error`.

Completed runs exit 0. Provider failures, failed context loading, cancellation,
and incomplete responses such as token-limit stops exit 1. JSON statuses are
`completed`, `failed`, `cancelled`, and `incomplete`; a successful empty response
has no error. Status reflects the runner's execution outcome, not whether the
model's answer is correct. Agent stop reasons such as `sentinel`, `no_tools`,
and `steps` identify why the loop ended. Invalid command-line options use Click's
normal usage errors.

`agent --from-stdin` is specifically for runner snapshot JSON, containing params,
contexts, and an optional chat seed. It is separate from stdin task text and
cannot be combined with task options or file attachments. CLI options override
snapshot params. `--no-hooks` disables hooks for an agent run.

## Resume sessions from CLI

```bash
python main.py chat --resume                 # Pick a saved session
python main.py chat --resume --latest        # Resume the most recent session
python main.py chat --resume --last          # Alias for --latest
python main.py chat --resume <id-or-path>     # Resume directly
```

In chat, `/load session` opens the same picker. Entries show the last save date,
user turn count, model, and opening/latest message excerpts, truncated to 80
characters. `/show sessions` and `list-sessions` show these details too.
