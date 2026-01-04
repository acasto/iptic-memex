# Prompts

## System prompt addenda

Memex can append conditional addenda to the system prompt after templating, without requiring placeholders:

- Pseudo-tools note: when effective tool mode is `pseudo`, content from `[TOOLS].pseudo_tool_prompt` is appended. This value can be a prompt chain key or literal text and is resolved via the prompt resolver.
- Agent Skills: when `[SKILLS].active = true`, Memex scans `[SKILLS].directories` for skill folders containing `SKILL.md` and injects an `<available_skills>` metadata block (name, description, location). Recommended convention: `skills/` (shipped with the project), `.skills/` (project-owned), and `~/.config/iptic-memex/skills/` (user-global).
- Supplemental prompts: add per-environment corrections or tips using `supplemental_prompt` keys:
  - `[DEFAULT].supplemental_prompt`
  - `[Provider].supplemental_prompt`
  - `[Model].supplemental_prompt` (in `models.ini`)

Order and de-duplication:
- Final system prompt adds: Pseudo-tools -> Skills -> DEFAULT -> Provider -> Model
- Exact-text de-duplication removes repeated segments while preserving order

Note:
- This replaces the old `{{pseudo_tool_prompt}}` template handler. No template handler is required for core addenda.

## Per-turn prompts

Set `turn_prompt` in `config.ini` (DEFAULT/provider/model). It resolves via the prompt resolver and is templated with
turn variables (e.g., `{{turn:index}}`, `{{message_id}}`). Each user turn gets the prompt as transient context.

See `templates.md` for the template handler chain and adding your own template action.
