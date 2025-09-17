"""Command metadata loading and suggestion helpers."""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Tuple

from tui.models import CommandItem


class CommandController:
    """Loads command specs and exposes suggestion helpers."""

    def __init__(self) -> None:
        self._command_items: List[CommandItem] = []
        self._command_specs: Dict[str, Dict[str, Any]] = {}
        self._top_level_commands: List[CommandItem] = []
        self._subcommand_map: Dict[str, List[CommandItem]] = {}

    # ------------------------------------------------------------------
    def load(self, registry: Any) -> None:
        specs: Dict[str, Any] = {}
        if registry:
            try:
                specs = registry.get_specs("tui")
            except Exception:
                specs = {}
        self._build_from_specs(specs)

    def _build_from_specs(self, specs: Dict[str, Any]) -> None:
        commands: List[CommandItem] = []
        top_level: List[CommandItem] = []
        sub_map: Dict[str, List[CommandItem]] = {}

        for item in specs.get("commands", []):
            cmd_name = item.get("command")
            if not cmd_name:
                continue
            help_text = item.get("help", "")
            has_default = False
            for sub in item.get("subs", []):
                sub_name = sub.get("sub") or ""
                title = f"/{cmd_name}" + (f" {sub_name}" if sub_name else "")
                cmd_help = help_text
                sub_help = sub.get("ui", {}).get("help") if isinstance(sub.get("ui"), dict) else ""
                entry = CommandItem(
                    title=title,
                    path=[cmd_name, sub_name],
                    help=sub_help or cmd_help,
                    handler=sub,
                )
                commands.append(entry)
                if sub_name:
                    sub_map.setdefault(cmd_name, []).append(entry)
                else:
                    has_default = True
                    top_level.append(entry)
            if not has_default:
                top_level.append(
                    CommandItem(
                        title=f"/{cmd_name}",
                        path=[cmd_name, ""],
                        help=help_text,
                        handler={},
                    )
                )

        self._command_items = commands
        self._command_specs = {item.get("command"): item for item in specs.get("commands", []) if item.get("command")}
        self._top_level_commands = sorted(top_level, key=lambda c: c.title.lower())
        for key, entries in sub_map.items():
            entries.sort(key=lambda c: c.title.lower())
        self._subcommand_map = sub_map

    # ------------------------------------------------------------------
    @property
    def command_items(self) -> List[CommandItem]:
        return list(self._command_items)

    @property
    def top_level_commands(self) -> List[CommandItem]:
        return list(self._top_level_commands)

    @property
    def subcommand_map(self) -> Dict[str, List[CommandItem]]:
        return {key: list(value) for key, value in self._subcommand_map.items()}

    @property
    def has_commands(self) -> bool:
        return bool(self._command_items or self._top_level_commands)

    def find_command_suggestions(self, line: str) -> Tuple[List[CommandItem], str]:
        if not line.startswith("/"):
            return [], ""
        if not self._command_specs:
            return [], ""
        raw = line[1:]
        if not raw:
            return self._top_level_commands, ""
        has_space = raw.endswith(" ")
        try:
            tokens = shlex.split(raw, posix=True)
        except Exception:
            tokens = raw.strip().split()
        if not tokens:
            return self._top_level_commands, ""
        first_token = tokens[0]
        first_lower = first_token.lower()
        top_matches = [item for item in self._top_level_commands if item.title.lower().startswith(f"/{first_lower}")]
        if len(tokens) == 1 and not has_space:
            if top_matches:
                return top_matches, first_token
            return self._top_level_commands, first_token

        command_name = first_token if first_token in self._command_specs else None
        if not command_name:
            candidates = [name for name in self._command_specs if name.startswith(first_token)]
            if len(candidates) == 1:
                command_name = candidates[0]
        if not command_name:
            highlight = first_token
            suggestions = top_matches or self._top_level_commands
            return suggestions, highlight

        if len(tokens) == 1 and not has_space:
            matches = [item for item in self._top_level_commands if item.path[0] == command_name]
            return matches, first_token

        sub_items = self._subcommand_map.get(command_name, [])
        if not sub_items:
            matches = [item for item in self._top_level_commands if item.path[0] == command_name]
            return matches, first_token

        sub_fragment = tokens[1] if len(tokens) >= 2 else ""
        highlight = sub_fragment
        if sub_fragment:
            frag_lower = sub_fragment.lower()
            sub_matches = [
                item
                for item in sub_items
                if item.title.lower().startswith(f"/{command_name.lower()} {frag_lower}")
            ]
            if not sub_matches:
                highlight = ""
                sub_matches = sub_items
        else:
            sub_matches = sub_items
        return sub_matches, highlight
