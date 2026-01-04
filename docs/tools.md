# Tools

## Built-in tools

- File system (FILE): read/write/append/rename/delete/summarize
- Shell commands (CMD): local or Docker
- Web search (WEBSEARCH)
- Math calculator (MATH)
- Memory (MEMORY)
- RAG (ragsearch)
- Persona review (PERSONA_REVIEW)

## CMD tool: local vs Docker

Memex has two implementations of the `cmd` tool:

- **Local CMD** (`assistant_cmd_tool`): runs on the host. The working directory is `[TOOLS].base_directory`.
- **Docker CMD** (`assistant_docker_tool`): runs inside a container with the base directory mounted at its absolute path.
  For compatibility, the base directory is also mounted at `/workspace` as an alias.

Select the implementation in `config.ini`:

```ini
[TOOLS]
cmd_tool = assistant_cmd_tool        # local (default)
;cmd_tool = assistant_docker_tool    # Docker
docker_env = ephemeral
```

If you need a true sandbox, use the Docker CMD tool. The local CMD tool is not sandboxed.

## File tool base_directory guard

The file tool is restricted to `[TOOLS].base_directory` plus optional allowlisted extra roots:

- `[TOOLS].extra_ro_roots` (read-only)
- `[TOOLS].extra_rw_roots` (read-write; supersedes read-only for exact matches)

Relative paths resolve against the base directory; absolute paths must live inside an allowed root. This is a path
guard, not a container sandbox.

## Dynamic registry

Tools are discovered dynamically from action files and exposed to providers for official tool calling.

Discovery: any action ending with `_tool_action.py` that defines:
- `tool_name()` -> canonical lowercase name
- `tool_spec(session)` -> `{args, description, required, schema:{properties}, auto_submit}`
- Optional `tool_aliases()`

Gating via config:
- `[TOOLS].active_tools = cmd,file,websearch,ragsearch`
- `[TOOLS].inactive_tools = math`
- Non-interactive defaults: `[AGENT].active_tools` and `[AGENT].blocked_tools`
- CLI `--tools` overrides defaults

Overrides:
- Per tool: `[TOOLS].<tool>_tool = action_name`
- Shell: `[TOOLS].cmd_tool = assistant_cmd_tool | assistant_docker_tool`
- Web search: `[TOOLS].websearch_tool = assistant_websearch_tool`

Pseudo-tools:
- Blocks like `%%CMD%% ... %%END%%` are case-insensitive

## Minimal user tool example

```python
from base_classes import InteractionAction

class AssistantMytoolToolAction(InteractionAction):
    @classmethod
    def tool_name(cls):
        return 'mytool'
    @classmethod
    def tool_spec(cls, session):
        return {
            'args': ['foo'],
            'description': 'Do something with foo',
            'required': ['foo'],
            'schema': {'properties': {'foo': {'type': 'string'}, 'content': {'type': 'string'}}},
            'auto_submit': True,
        }
    def __init__(self, session):
        self.session = session
    def run(self, args, content=''):
        self.session.add_context('assistant', {'name': 'mytool', 'content': 'ok'})
```

Place it under your user actions dir (see `DEFAULT.user_actions` in `config.ini`). Optionally set
`[TOOLS].mytool_tool = assistant_mytool_tool` and list `mytool` in `[TOOLS].active_tools`.

## Persona review quickstart

Enable by listing `persona_review` in `[TOOLS].active_tools` in `config.ini`.

Example (pseudo-tools):
```
%%PERSONA_REVIEW%% personas="personas.md" goal="Reduce activation friction" panel=true files="Brand_Guide.md,Audience.md,Product.md"
Evaluate feature: invite teammates by email (see Product.md -> Onboarding)
%%END%%
```
