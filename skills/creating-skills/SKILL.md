---
name: creating-skills
description: Creates new Agent Skills (SKILL.md + optional scripts/references) that follow the Agent Skills spec. Use when the user asks to create, structure, validate, or improve an agent skill.
---

# Creating Skills

Create a new Agent Skills-compatible skill directory with a high-quality `SKILL.md`.

## Quick start (new skill)

1. Pick a skill name:
   - 1–64 chars
   - lowercase letters, numbers, hyphens only
   - no leading/trailing hyphen, no consecutive `--`
2. Create a directory named exactly the skill name.
3. Add `SKILL.md` with YAML frontmatter and a short, practical body.

Minimal template:

```markdown
---
name: <skill-name>
description: <what it does>. Use when <triggers / keywords / situations>.
---

# <Title Case Skill Name>

## When to use this skill
- ...

## Workflow
1. ...

## Examples
### Example 1
Input: ...
Output: ...
```

## Authoring guidance

### 1) Write for discovery first (frontmatter)

The `description` is how agents decide to activate the skill. It should:
- Say what the skill does
- Say when to use it (include keywords users will say)
- Be third-person and specific

Good:

```yaml
description: Generates descriptive git commit messages from diffs. Use when the user asks for help writing commit messages or summarizing changes for a commit.
```

Weak:

```yaml
description: Helps with git.
```

### 2) Keep `SKILL.md` short; link out for details

Assume the agent is competent; include only what it needs to be correct in your environment.

When `SKILL.md` gets long, split content into files and link them directly from `SKILL.md` (avoid deep chains).

Suggested structure:

```
my-skill/
├── SKILL.md
├── references/
│   ├── REFERENCE.md
│   └── EXAMPLES.md
└── scripts/
    ├── validate.py
    └── run.py
```

### 3) Prefer deterministic scripts for fragile steps

If correctness matters, bundle scripts that:
- Validate inputs
- Produce machine-checkable intermediate outputs (e.g., `plan.json`)
- Fail with actionable errors

Be explicit in instructions whether to:
- run a script (most common), or
- read a script as reference (less common)

### 4) Make workflows checkable

For multi-step tasks, include a checklist and validation loop:

```
Progress:
- [ ] Gather inputs
- [ ] Generate plan artifact
- [ ] Validate artifact
- [ ] Apply changes
- [ ] Verify outputs
```

### 5) Avoid assumptions

Don’t assume tools/packages exist. If something is required:
- name it
- show the command to install/run it
- include a fallback if possible

## Review checklist

- [ ] Directory name equals `name:` in frontmatter
- [ ] `description` includes “what” + “when”
- [ ] `SKILL.md` is concise; heavy details are in referenced files
- [ ] File references are relative paths from the skill root
- [ ] Scripts (if any) handle errors and edge cases explicitly

