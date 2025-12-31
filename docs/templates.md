# Templates

## Overview

Prompt sources (system, per-turn, hook prompts) are resolved through the prompt resolver and then passed through a chain
of template handlers. The handlers are configured via `DEFAULT.template_handler` (comma-separated), and run in order.

Default config:

```ini
template_handler = prompt_template, prompt_template_chat, prompt_template_memory
```

Order matters. Each handler receives the output of the previous handler.

## Built-in handlers

- `prompt_template` (basic variables):
  - `{{date}}`, `{{date:%Y-%m-%d}}`
  - `{{message_id}}`
  - `{{session:<key>}}` (session params)
  - `{{config:SECTION:key}}`
  - `{{env:VAR}}`
  - `{{turn:<key>}}` (per-turn metadata)
- `prompt_template_chat` (chat transcript placeholders) - see below
- `prompt_template_memory` (memory recall via `{{memory}}` / `{{memory:project}}`)

## Chat prompt placeholders

- `{{chat}}` / `{{chat:window}}`: provider-visible window (respects `context_sent`).
- `{{chat:last}}`, `{{chat:last=3}}`, `{{chat:last=5}}`, etc. (legacy `last_3` remains supported)

Modifiers (semicolon-separated) for finer control, e.g. `{{chat:last_5;only=user;max_tokens=256}}`:
- `only=<roles>` / `exclude=<roles>` (CSV, case-insensitive) to filter roles
- `max_tokens=<n>` token-caps the rendered transcript (uses `tiktoken` when available, falls back to words)
- `max_chars=<n>` overrides the default `chat_template_max_chars` cap (default ~2000 chars)

## Adding your own template handler

Template handlers are actions. Create a new action with a `run(content)` method that returns the transformed text and
register it by name in `DEFAULT.template_handler`.

Example (custom handler):

```python
from base_classes import InteractionAction

class PromptTemplateMyvarsAction(InteractionAction):
    def __init__(self, session):
        self.session = session

    def run(self, content=None):
        if not content:
            return ""
        return content.replace("{{myvar}}", "hello")
```

Then set:

```ini
template_handler = prompt_template, prompt_template_chat, prompt_template_myvars
```

To add custom actions without editing core files, place them in the user actions directory (see
`DEFAULT.user_actions` in `config.ini`).

## Related docs

- prompts.md (prompt resolver, system addenda, per-turn prompts)
