# User Actions and Tools

This folder shows how to extend Memex with your own actions and assistant tools — without editing core code.

There are two complementary ways to supply tools:

- Action-based (preferred): create a `*_tool_action.py` file whose class exposes a small metadata API. Tools are then auto-discovered and included in official tool calling.
- Registry-injected: return a dict from `register_assistant_commands_action.py` to define or override tools in one place.

Both approaches can live side-by-side. Gating and handler overrides apply uniformly.

## 1) Enable user actions

Point Memex to your user actions directory in `config.ini` (or user config):

```ini
[DEFAULT]
user_actions = ~/.config/iptic-memex/actions
```

Copy the examples from this folder into your user actions dir to experiment.

## 2) Action-based tools (auto-discovery)

Any action ending with `_tool_action.py` that implements these class methods is auto-registered as a tool:

- `@classmethod tool_name() -> str`: canonical lowercase tool name (e.g., `file`, `cmd`, `websearch`, `ragsearch`).
- `@classmethod tool_aliases() -> list[str]`: optional aliases (e.g., `['rag']`).
- `@classmethod tool_spec(session) -> dict`: returns `{args, description, required, schema:{properties}, auto_submit}`.

Minimal skeleton:

```python
from base_classes import InteractionAction

class AssistantMytoolToolAction(InteractionAction):
    @classmethod
    def tool_name(cls):
        return 'mytool'

    @classmethod
    def tool_aliases(cls):
        return []

    @classmethod
    def tool_spec(cls, session):
        return {
            'args': ['foo'],
            'description': 'Do something with foo',
            'required': ['foo'],
            'schema': {'properties': {
                'foo': {'type': 'string', 'description': 'Primary input'},
                'content': {'type': 'string', 'description': 'Optional freeform input'},
            }},
            'auto_submit': True,
        }

    def __init__(self, session):
        self.session = session

    def run(self, args, content=''):
        # implement the tool
        self.session.add_context('assistant', {'name': 'mytool', 'content': 'ok'})
```

Notes:
- File/class name can be anything; discovery only requires the `_tool_action.py` suffix.
- Use `InteractionAction` for non‑prompting tools; `StepwiseAction` if you need `start/resume` prompts via UI adapters.

## 3) Registry-injected tools

Alternatively (or additionally), define tools by returning a dict in `register_assistant_commands_action.py`.
Its keys are normalized to lowercase and shallow-merge into the canonical registry:

```python
class RegisterAssistantCommandsAction(InteractionAction):
    def run(self, args=None):
        return {
            'ask_ai': {
                'args': ['model', 'question'],
                'function': {'type': 'action', 'name': 'ask_ai_tool'},
                'description': "Ask a secondary model a question.",
                'schema': {'properties': {
                    'model': {'type': 'string'},
                    'question': {'type': 'string'},
                    'content': {'type': 'string'},
                }},
                'auto_submit': True,
            }
        }
```

## 4) Configuration knobs

- Show/hide tools:
  - Allowlist wins: `[TOOLS].active_tools = cmd,file,websearch,ragsearch,youtrack,ask_ai`
  - Denylist only applies when no allowlist: `[TOOLS].inactive_tools = math`
- Non-interactive (Agent/Completion): `[AGENT].active_tools` sets the default tool set; `[AGENT].blocked_tools` is a hard blocklist. CLI `--tools` overrides the default set.

- Choose implementation (handler) per tool:
  - `[TOOLS].<tool>_tool = action_name`
  - Special case: `cmd` keeps `[TOOLS].cmd_tool = assistant_cmd_tool | assistant_docker_tool`
  - Web search uses `[TOOLS].websearch_tool` (legacy `search_tool` removed)

## 5) Pseudo-tool blocks (text commands)

The block parser is case-insensitive and matches registered tool names:

```
%%MYTOOL%%
foo="value"

optional freeform content
%%END%%
```

Tips:
- Tool keys are stored in lowercase; blocks may be upper/lower/mixed case.
- For blocks that reference content by label, you can use:
  `%%BLOCK:note%% ... %%END%%` and pass `block="note"` as an arg — the parser will append the block content.

## 6) Reloading during development

- Use the example `assistant_reload_tool_action.py` (`%%RELOAD%% ... %%END%%`) to clear specific actions from the cache.
- Or call the built-in user command `debug reload` (prints status in the updates panel).

## 7) Collision rules and overrides

- When both a built-in and a user action claim the same `tool_name`, the built‑in spec wins by default.
- To run your code while keeping the built‑in schema, set `[TOOLS].<tool>_tool = your_action_name`.
- To override schema/description, return a matching key (lowercase) from `register_assistant_commands_action.py` to shallow‑merge fields.

## 8) Examples in this folder

- `ask_ai_tool_action.py` — a simple external model query tool (auto-discovered).
- `assistant_reload_tool_action.py` — reload selected actions by name (auto-discovered).
- `register_assistant_commands_action.py` — shows how to inject/override tool specs programmatically.

That’s it! With these patterns you can add and iterate on your own tools safely without touching core.
