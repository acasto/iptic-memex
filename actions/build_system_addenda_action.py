from __future__ import annotations

import html
import os
from typing import List, Optional

from base_classes import InteractionAction
from component_registry import PromptResolver


class BuildSystemAddendaAction(InteractionAction):
    """Compose conditional system-prompt addenda post-templating.

    Sources (resolved via PromptResolver; support chains or literal text):
    - Pseudo-tools guidance when effective tool mode is 'pseudo' and
      `[TOOLS].pseudo_tool_prompt` is set.
    - `supplemental_prompt` layered at DEFAULT, Provider, and Model scopes.

    Concatenation order (earlier items come first):
    1) Pseudo-tools
    2) DEFAULT.supplemental_prompt
    3) <Provider>.supplemental_prompt
    4) <Model>.supplemental_prompt
    """

    def __init__(self, session):
        self.session = session
        # Build a resolver using the session config (merged base + user)
        self._resolver = PromptResolver(self.session.config)

    def _warn(self, message: str) -> None:
        try:
            self.session.utils.output.warning(message)
        except Exception:
            return

    def _resolve_prompt(self, source: Optional[str]) -> str:
        if not source:
            return ""
        try:
            resolved = self._resolver.resolve(str(source))
            return resolved.strip() if isinstance(resolved, str) else ""
        except Exception:
            return str(source)

    @staticmethod
    def _to_bool(value: object, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if not s:
            return default
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off"):
            return False
        return default

    @staticmethod
    def _split_csv(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            items = []
            for v in value:
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    items.append(s)
            return items
        s = str(value).strip()
        if not s:
            return []
        return [p.strip() for p in s.split(",") if p.strip()]

    @staticmethod
    def _app_root() -> str:
        try:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            return os.path.abspath(os.getcwd())

    def _base_dir(self) -> str:
        try:
            base_dir = self.session.get_option("TOOLS", "base_directory", fallback="working")
        except Exception:
            base_dir = "working"
        if base_dir in ("working", "."):
            return os.path.realpath(os.path.abspath(os.getcwd()))
        return os.path.realpath(os.path.abspath(os.path.expanduser(str(base_dir))))

    def _resolve_skill_root(self, root: str) -> Optional[str]:
        token = (root or "").strip()
        if not token:
            return None
        if token in ("skills", "./skills"):
            return os.path.realpath(os.path.abspath(os.path.join(self._app_root(), "skills")))
        if token in (".skills", "./.skills"):
            return os.path.realpath(os.path.abspath(os.path.join(self._base_dir(), ".skills")))
        expanded = os.path.expanduser(token)
        if os.path.isabs(expanded):
            return os.path.realpath(os.path.abspath(expanded))
        return os.path.realpath(os.path.abspath(os.path.join(self._base_dir(), expanded)))

    @staticmethod
    def _extract_frontmatter(text: str) -> str:
        """Return YAML frontmatter (without --- lines) or empty string."""
        try:
            lines = text.splitlines()
            if not lines:
                return ""
            if lines[0].strip() != "---":
                return ""
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    return "\n".join(lines[1:i])
        except Exception:
            return ""
        return ""

    @staticmethod
    def _strip_quotes(value: str) -> str:
        v = (value or "").strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            return v[1:-1]
        return v

    def _parse_frontmatter_minimal(self, frontmatter: str) -> dict:
        """Parse name/description from YAML frontmatter without PyYAML.

        Supports simple scalars and block scalars (| or >).
        """
        out: dict = {}
        lines = (frontmatter or "").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                i += 1
                continue
            if ":" not in line:
                i += 1
                continue
            key, rest = line.split(":", 1)
            key = key.strip()
            rest = rest.lstrip()
            if key not in ("name", "description", "compatibility", "allowed-tools"):
                i += 1
                continue

            # Block scalars: key: | or key: >
            if rest in ("|", ">"):
                i += 1
                block_lines: list[str] = []
                block_indent: Optional[int] = None
                while i < len(lines):
                    l2 = lines[i]
                    if not l2.strip():
                        block_lines.append("")
                        i += 1
                        continue
                    cur_indent = len(l2) - len(l2.lstrip(" "))
                    if block_indent is None:
                        block_indent = cur_indent
                    if cur_indent < block_indent:
                        break
                    block_lines.append(l2[block_indent:])
                    i += 1
                value = "\n".join(block_lines).strip()
                out[key] = value
                continue

            out[key] = self._strip_quotes(rest)
            i += 1
        return out

    def _discover_skills(self) -> list[dict]:
        """Discover skills in configured directories.

        Skill roots are configured under [SKILLS].directories (CSV).
        Each direct child directory containing SKILL.md is treated as a skill.
        """
        try:
            enabled_raw = self.session.get_option("SKILLS", "active", fallback=False)
        except Exception:
            enabled_raw = False
        if not self._to_bool(enabled_raw, False):
            return []

        try:
            dirs_raw = self.session.get_option("SKILLS", "directories", fallback=None)
        except Exception:
            dirs_raw = None
        roots = self._split_csv(dirs_raw)
        if not roots:
            return []

        skills: list[dict] = []
        for root in roots:
            root_abs = self._resolve_skill_root(root)
            if not root_abs:
                continue
            if not os.path.isdir(root_abs):
                continue

            candidates: list[str] = []
            # Allow a root to be a skill dir itself
            if os.path.isfile(os.path.join(root_abs, "SKILL.md")):
                candidates.append(root_abs)
            else:
                try:
                    for name in sorted(os.listdir(root_abs)):
                        p = os.path.join(root_abs, name)
                        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "SKILL.md")):
                            candidates.append(p)
                except Exception:
                    continue

            for skill_dir in candidates:
                skill_md = os.path.join(skill_dir, "SKILL.md")
                try:
                    with open(skill_md, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except Exception:
                    self._warn(f"[SKILLS] failed to read: {skill_md}")
                    continue

                fm = self._extract_frontmatter(text)
                meta = self._parse_frontmatter_minimal(fm)
                name = str(meta.get("name") or "").strip()
                desc = str(meta.get("description") or "").strip()
                if not name or not desc:
                    self._warn(f"[SKILLS] missing name/description: {skill_md}")
                    continue

                dir_name = os.path.basename(os.path.abspath(skill_dir))
                if name != dir_name:
                    self._warn(f"[SKILLS] name mismatch (dir '{dir_name}' != '{name}'): {skill_md}")
                    continue

                skills.append(
                    {
                        "name": name,
                        "description": desc,
                        "location": os.path.abspath(skill_md),
                    }
                )

        # De-dupe by name while preserving order
        seen_names: set[str] = set()
        unique: list[dict] = []
        for s in skills:
            n = str(s.get("name") or "")
            if not n or n in seen_names:
                continue
            seen_names.add(n)
            unique.append(s)
        return unique

    def _format_skills_addendum(self, skills: list[dict]) -> str:
        if not skills:
            return ""

        # XML-ish format matches the Agent Skills integration docs, but we keep it plain text.
        lines: list[str] = ["<available_skills>"]
        for s in skills:
            name = html.escape(str(s.get("name") or ""))
            desc = html.escape(str(s.get("description") or ""))
            loc = html.escape(str(s.get("location") or ""))
            lines.extend(
                [
                    "  <skill>",
                    f"    <name>{name}</name>",
                    f"    <description>{desc}</description>",
                    f"    <location>{loc}</location>",
                    "  </skill>",
                ]
            )
        lines.append("</available_skills>")
        return "\n".join(lines)

    def run(self, content=None) -> str:
        items: List[str] = []

        # 1) Pseudo-tools guidance (only when effective mode is 'pseudo')
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
        except Exception:
            mode = 'none'
        if mode == 'pseudo':
            try:
                pseudo_src = self.session.get_option('TOOLS', 'pseudo_tool_prompt', fallback=None)
            except Exception:
                pseudo_src = None
            pseudo_text = self._resolve_prompt(pseudo_src)
            if pseudo_text:
                items.append(pseudo_text)

        # 1.5) Agent Skills metadata (opt-in via [SKILLS].active)
        try:
            skills = self._discover_skills()
        except Exception:
            skills = []
        skills_text = self._format_skills_addendum(skills)
        if skills_text:
            items.append(skills_text)

        # 2) DEFAULT-level supplemental
        try:
            default_sup = self.session.get_option('DEFAULT', 'supplemental_prompt', fallback=None)
        except Exception:
            default_sup = None
        default_text = self._resolve_prompt(default_sup)
        if default_text:
            items.append(default_text)

        # 3) Provider-level supplemental (current provider from params)
        try:
            provider_name = self.session.params.get('provider')
        except Exception:
            provider_name = None
        prov_text = ""
        if provider_name:
            try:
                prov_sup = self.session.get_option_from_provider('supplemental_prompt', provider_name)
            except Exception:
                prov_sup = None
            prov_text = self._resolve_prompt(prov_sup)
            if prov_text:
                items.append(prov_text)

        # 4) Model-level supplemental (current model from params)
        try:
            model_name = self.session.params.get('model')
        except Exception:
            model_name = None
        if model_name:
            try:
                model_sup = self.session.get_option_from_model('supplemental_prompt', model_name)
            except Exception:
                model_sup = None
            model_text = self._resolve_prompt(model_sup)
            if model_text:
                items.append(model_text)

        # De-duplicate while preserving order (avoids repeated supplementals when
        # the same chain/text is configured at multiple scopes)
        seen = set()
        unique: List[str] = []
        for s in items:
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            unique.append(s)
        return "\n\n".join(unique)
