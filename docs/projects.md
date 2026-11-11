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

## Nested execution scope

Internal helpers created with an outer session now receive an immutable execution
scope before provider/prompt setup. A child may narrow its base directory inside
the parent's canonical sandbox; it cannot switch projects, increase write policy,
or replace application services/tool handlers through overrides. Further children
inherit the narrower boundary. Parent call-accounting callbacks and cancellation
continue to apply.

The child's filesystem policies exclude ambient extra roots and retain nested
read-only restrictions. Tool exposure is capped at the parent's available names,
including dynamic registrations. Global database memory and ragsearch are excluded
until they have explicit retrieval scopes; file memory is disabled. Automatic
parent context copying/chat seeding is disabled when the parent is scoped.
Helpers launched from ordinary chat retain copied input contexts and a chat
template seed without expanding their filesystem or tool permissions. Trusted
application callers can pass selected `contexts` or an explicit `chat_seed`—for
example a small identity summary—without granting access to the source store.
Hooks in ordinary chat retain implicit parent history; scoped callers must
select their context explicitly.

Scoped command execution requires the parent's Docker command tool. Local shell
execution is rejected. Docker uses ephemeral containers, scoped mounts (including
the /workspace alias), and a private temporary filesystem. These are boundaries
for built-in tools, not an OS sandbox around arbitrary Python actions or plugins.
Application-owned sessions and explicitly supplied context remain trusted inputs.

There is no new user-facing memory profile selector or project retrieval index
in this slice. Workspace subprocess delegation still needs explicit integration;
these services currently enforce nested application helper execution. The
existing workspace supervisor continues to own its branch restrictions.
