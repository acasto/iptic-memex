# File memory

File memory uses a persistent host directory that the agent reads, searches,
writes, and patches with the existing file and command tools. No memory tool
is required. SQLite memory remains available for existing users and migration.

## Enable

In your user config, select the file template handler **instead of** the SQLite
handler (do not include both, since both consume the same placeholders):

```ini
[MEMORY]
active = true
directory = ~/.config/iptic-memex/memory
backup_directory = ~/.config/iptic-memex/memory-backups
backup_on_session_start = true
backup_count = 10
max_chars = 6000

[DEFAULT]
template_handler = prompt_template, prompt_template_chat, prompt_template_memory_file, prompt_template_file

[PROMPTS]
default = default.txt, memory_files

[TOOLS]
active_tools = cmd,file,openlink,ragsearch,websearch,youtrack,persona_review
```

Append `memory_files` to your own prompt chain if you already have one. The
starter prompt is `prompts/memory_files.txt`; copy it to your user prompts
directory to customize it. Remove `memory` from any explicitly configured
`AGENT.active_tools` list after migration too. For pseudo tools, remove
`tool_memory` from your `PROMPTS.pseudo_tools` chain. Review any scribe hook:
select file/cmd tools and a prompt that uses ordinary files (the shipped SQLite
scribe prompt is unchanged).

The application creates the memory directory when enabled. Entry files are
created by you or the agent as needed; existing files are never overwritten
by setup. Example structure:

```text
memory/
    MEMORY.md
    preferences.md
    projects/
        iptic-memex/
            MEMORY.md
            decisions.md
```

## Templates and access

- `{{memory_directory}}`: resolved absolute path shared by host and Docker tools.
- `{{memory}}`: contents of the root `MEMORY.md`.
- `{{memory:iptic-memex}}`: contents of `projects/iptic-memex/MEMORY.md`.

Project names contain letters, digits, hyphens, and underscores, beginning
with a letter or digit. Paths escaping the memory root, including symlinks,
are rejected. Missing entries are empty; unreadable entries emit a warning.
`max_chars` limits each injected entry independently. Detailed notes should
be linked from entry files and read on demand. Templates run when a prompt
is built, so an existing prompt contains a snapshot, not a live view.

Absolute and `~` paths are expanded on the host. Relative directories resolve
against `TOOLS.base_directory`, just like extra tool roots. Enabling memory
adds its root to the shared filesystem policy as writable. Docker consumes
that generic policy and mounts the directory at the same absolute path; no
memory-specific Docker configuration is necessary. In Docker, use the resolved
absolute path instead of `~`. Agent deny/dry-run and environment read-only
mount policies still apply. Existing persistent containers must be recreated
to pick up changed mounts. These bind mounts assume a local Docker daemon.

## Recovery copies

Before memory is used in a new chat, TUI, or Web session, the application copies
the existing directory to a timestamped backup. Resuming a chat creates a new
interactive session and therefore a new recovery copy. Agent, completion, and
internal helper runs never create or rotate backups. A missing or empty memory
directory is not backed up. `backup_on_session_start = false` or
`backup_count = 0` disables backups.

Only after a successful copy are older completed backups removed, keeping
`backup_count` copies. Copy failures produce a warning and leave existing
recovery copies intact. Backup directories must not overlap writable agent roots
and must not contain, or be contained by, the memory directory. Broad roots
such as your entire home directory, when writable, can violate this constraint.
Read-only access to the backup directory or its parent is allowed. Only
application-created snapshot directories are pruned. Symlinks are preserved
as links; their external targets are not backed up. Avoid concurrent writers
during startup if you need a consistent snapshot across multiple files.

To restore, stop sessions writing memory, preserve the current directory if
needed, and copy the chosen snapshot's contents back into the configured
memory directory. The agent does not manage backups. The deprecated local
command tool is not sandboxed and cannot protect recovery copies from shell
access; Docker is the appropriate choice for filesystem isolation.

## Migrating SQLite memories

Keep the SQLite `memory` tool enabled during migration. Ask the model to read
`action=read, project=all`, organize the records into global/project files,
and read them back to check completeness. Then switch template handlers and
remove the tool from enabled lists. Retain the original database for recovery;
there is no automatic migration or synchronization between the two stores.
