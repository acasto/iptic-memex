# Skills (Agent Skills)

Memex supports “Agent Skills” as a lightweight, filesystem-based way to give the model reusable workflows, reference
material, and (optionally) helper scripts—without stuffing everything into the system prompt.

Memex’s implementation follows the Agent Skills open format:
- Spec: https://agentskills.io/specification
- Integration guide: https://agentskills.io/integrate-skills


## What a Skill is

A Skill is a directory containing at minimum a `SKILL.md` file:

  <skill-name>/
    SKILL.md
    references/   (optional)
    scripts/      (optional)
    assets/       (optional)

`SKILL.md` starts with YAML frontmatter:
- `name` (required): must match the parent directory name
- `description` (required): what it does + when to use it (include keywords users will say)


## How Skills work in Memex

Memex currently implements the “filesystem-based agent” approach:

1) Discovery (startup):
   - Memex scans skills directories (see config below).
   - It reads only YAML frontmatter from each `SKILL.md` to collect `name` and `description`.

2) Injection (system prompt addenda):
   - Memex appends an `<available_skills>` block to the system prompt addenda when skills are enabled.
   - Each entry includes:
     - name
     - description
     - location (absolute path to `SKILL.md`)

3) Activation (during conversation):
   - The model chooses when to activate a skill by reading its `SKILL.md` (and optionally referenced files) using tools
     like `file` or `cmd`.
   - Skills are structured for progressive disclosure: `SKILL.md` is the overview; deeper docs live in referenced files.

Implementation detail:
- System addenda are built by `actions/build_system_addenda_action.py`.


## File access + sandboxing (important)

Memex tools enforce filesystem access via allowlisted roots:
- Base root: `[TOOLS].base_directory` (read-write)
- Optional extra roots:
  - `[TOOLS].extra_ro_roots` (read-only)
  - `[TOOLS].extra_rw_roots` (read-write; supersedes read-only for exact matches)

Skills directories are implicitly allowlisted as read-only so the model can read `SKILL.md` and references without you
having to configure extra roots manually. If you want the model/tools to write into a skills directory, explicitly add
that directory to `extra_rw_roots`.


## Docker CMD tool compatibility

If you use the Docker `cmd` tool, Memex mounts:
- the base directory at its absolute host path (used as working directory)
- the base directory again at `/workspace` (compatibility alias)
- any extra allowlisted roots at their absolute host paths (read-only or read-write)

This allows a single `location` path (absolute host path) to work both:
- on the host (file tool / local cmd tool), and
- inside Docker (docker cmd tool)


## Skills directories (where to put them)

Enable and configure in `config.ini`:

  [SKILLS]
  active = true
  directories = skills, .skills, ~/.config/iptic-memex/skills

Semantics:
- `skills` is app-relative (the shipped skills directory in the Memex repo/install).
- `.skills` is base-directory-relative (project-local skills next to the user’s working tree).
- `~/.config/iptic-memex/skills` is user-global skills.
- Any additional entries may be absolute paths or base-directory-relative paths.

Notes:
- Missing directories are skipped silently.
- Each direct child directory containing `SKILL.md` is treated as a skill.
- A directory that itself contains `SKILL.md` is also treated as a skill root.


## Getting started (create a new skill)

1) Pick a name (lowercase letters/numbers/hyphens; must match the directory name).
2) Create a skill directory under one of the configured roots, e.g.:

  .skills/my-skill/SKILL.md

3) Add frontmatter + instructions:

  ---
  name: my-skill
  description: Does X. Use when the user asks for Y or mentions Z.
  ---

  # My Skill
  ...

4) Enable skills:

  [SKILLS]
  active = true

5) Start `memex chat` and confirm you see the `<available_skills>` block reflected in behavior.

Shipped example:
- `skills/creating-skills` includes guidance for writing new skills in-spec.


## Tips for writing good skills

- Keep `SKILL.md` concise; link out to `references/*.md` for details.
- Keep references one hop from `SKILL.md` (avoid deep chains of links).
- Prefer scripts for deterministic validation steps, and be explicit when to run them vs read them.
- Avoid assuming tools/packages exist; list dependencies and required commands.


## Related docs

- docs/prompts.md (system addenda behavior)
- docs/tools.md (file/cmd tools and sandboxing)
- docs/templates.md (optional `{{file:...}}` prompt includes)

