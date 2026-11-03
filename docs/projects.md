# Project preferences

Chat, completion, and internal sessions resolve an application project at startup.
The default root is CWD; `memex --project /path/to/project chat` selects another
root. Put global options before the mode. Project metadata is optional.

Create `.memex/project.toml` inside that root:

```toml
name = "Business website"
model = "your-chat-model"
agent_model = "your-agent-model"
supplemental_prompt = """
Use the business brief as the source of project requirements.
Distinguish confirmed decisions from proposals.
"""
```

All fields are optional strings. `model` supplies the interactive preference;
`agent_model` supplies the completion/internal/agent preference. Explicit `-m`
wins; absent project preferences retain the configured DEFAULT/AGENT defaults.
Unknown keys or sections are rejected. The file cannot configure tools, hooks,
MCP, credentials, permissions, or budgets.

The project supplemental is literal text appended after existing model addenda,
with the same exact-text deduplication. It is not a prompt filename or chain.
Existing prompt selection and provider/model addenda continue to work.

Project settings are session-local. `session.project` records resolved inputs;
`session.project_sources` identifies root/model selection sources. Internal
helpers inherit the resolved project even if process CWD changes. No central
registry, parent-directory discovery, durable project ID, or scheduler is added.

The existing `working`/`.` tool-root convention resolves to the selected project.
An explicit `--base-dir` or configured tool root remains independent. Project
selection is not a new security boundary: enforcing non-expanding delegation
permissions is the next application phase. Existing workspace process wiring
will be integrated separately; this slice does not expose agent delegation in chat.
