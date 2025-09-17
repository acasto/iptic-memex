"""Input completion helpers for the TUI."""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from tui.models import CommandItem

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from textual.widgets import Input
    from tui.widgets.command_hint import CommandHint


class InputCompletionManager:
    """Manages command and file-path completions for the input widget."""

    def __init__(self) -> None:
        self._input: Optional["Input"] = None
        self._command_hint: Optional["CommandHint"] = None
        self._top_level_commands: List[CommandItem] = []
        self._subcommand_map: Dict[str, List[CommandItem]] = {}
        self._all_suggestion_strings: List[str] = []
        self._tab_cycle_state: Dict[str, object] = {"mode": "", "prefix": "", "index": 0, "suggestions": []}
        self._suppress_reset_once = False
        self._last_file_completion_hint: Optional[str] = None
        self._last_file_completion_choices: List[str] = []
        self._last_file_completion_abs_dir: Optional[str] = None

    # --- widget wiring -------------------------------------------------
    def set_input(self, input_widget: Optional["Input"]) -> None:
        self._input = input_widget

    def set_command_hint(self, command_hint: Optional["CommandHint"]) -> None:
        self._command_hint = command_hint

    # --- catalog management -------------------------------------------
    def update_catalog(
        self,
        top_level_commands: List[CommandItem],
        subcommand_map: Dict[str, List[CommandItem]],
    ) -> None:
        self._top_level_commands = list(top_level_commands)
        self._subcommand_map = {key: list(values) for key, values in subcommand_map.items()}
        self._refresh_input_suggester()

    # --- state helpers -------------------------------------------------
    def mark_programmatic_update(self) -> None:
        self._suppress_reset_once = True

    def clear_command_hint(self) -> None:
        if self._command_hint:
            try:
                self._command_hint.update_suggestions([], prefix="")
            except Exception:
                pass
        self._reset_tab_cycle()

    # --- input events --------------------------------------------------
    def handle_input_changed(
        self,
        value: str,
        *,
        find_command_suggestions: Callable[[str], Tuple[List[CommandItem], str]],
    ) -> None:
        if self._suppress_reset_once:
            self._suppress_reset_once = False
        else:
            self._reset_tab_cycle()

        if not value.startswith("/"):
            self.clear_command_hint()
            self._update_input_suggestions(value, [])
            return

        file_active, *_ = self._detect_file_completion_context(value)
        if file_active:
            self._update_input_suggestions(value, [])
            return

        suggestions, highlight = find_command_suggestions(value)
        if self._command_hint:
            try:
                self._command_hint.update_suggestions(suggestions, prefix=highlight)
            except Exception:
                pass
        self._update_input_suggestions(value, suggestions)

    def handle_tab_completion(
        self,
        *,
        find_command_suggestions: Callable[[str], Tuple[List[CommandItem], str]],
        focus_input: Callable[[], None],
    ) -> None:
        if not self._input:
            return
        current = self._input.value or ""
        cycle = dict(self._tab_cycle_state)

        file_active, _, _, _ = self._detect_file_completion_context(current)
        if file_active:
            file_suggestions = self._file_path_completion_values(current)
            abs_dir = self._last_file_completion_abs_dir
            need_seed = (
                cycle.get("mode") != "file"
                or cycle.get("prefix") != current
                or cycle.get("abs_dir") != abs_dir
                or not cycle.get("suggestions")
            )
            if need_seed:
                self._tab_cycle_state = {
                    "mode": "file",
                    "prefix": current,
                    "applied": current,
                    "index": -1,
                    "suggestions": file_suggestions,
                    "abs_dir": abs_dir,
                }
                self._show_file_completion_hint()
                return

            suggestions_list = list(cycle.get("suggestions") or [])
            if not suggestions_list:
                return
            index = (int(cycle.get("index", -1)) + 1) % len(suggestions_list)
            chosen = suggestions_list[index]
            cycle.update({"index": index})
            self._tab_cycle_state = {**cycle, "applied": chosen}
            self._apply_programmatic_value(chosen, focus_input)
            return

        suggestions_items, _ = find_command_suggestions(current)
        if suggestions_items:
            suggestions_list = [self._format_suggestion(item.title) for item in suggestions_items]
        else:
            suggestions_list = list(self._all_suggestion_strings)
        if not suggestions_list:
            return
        chosen = suggestions_list[0]
        self._tab_cycle_state = {
            "mode": "command",
            "prefix": current,
            "applied": chosen,
            "index": 0,
            "suggestions": suggestions_list,
        }
        self._apply_programmatic_value(chosen, focus_input)

    # --- utilities -----------------------------------------------------
    def _apply_programmatic_value(self, value: str, focus_input: Callable[[], None]) -> None:
        if not self._input:
            return
        try:
            self.mark_programmatic_update()
            self._input.value = value
            if hasattr(self._input, "cursor_position"):
                self._input.cursor_position = len(value)
        except Exception:
            pass
        finally:
            try:
                focus_input()
            except Exception:
                pass

    def _refresh_input_suggester(self) -> None:
        if not self._input:
            return
        entries: List[str] = []
        for item in self._top_level_commands:
            entries.append(self._format_suggestion(item.title))
        for items in self._subcommand_map.values():
            for entry in items:
                entries.append(self._format_suggestion(entry.title))
        deduped: List[str] = []
        seen = set()
        for value in entries:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        self._all_suggestion_strings = deduped
        try:
            from textual.suggester import SuggestFromList  # type: ignore

            if deduped:
                self._input.suggester = SuggestFromList(deduped, case_sensitive=False)
            else:
                self._input.suggester = None
        except Exception:
            pass

    def _update_input_suggestions(self, line: str, items: List[CommandItem]) -> None:
        if not self._input:
            return
        try:
            if not hasattr(self._input, "suggestions"):
                return
            file_active = self._detect_file_completion_context(line)[0]
            if file_active:
                suggestions = self._file_path_completion_values(line)
                if suggestions:
                    self._input.suggestions = suggestions  # type: ignore[attr-defined]
                else:
                    self._input.suggestions = []  # type: ignore[attr-defined]
                if hasattr(self._input, "suggester"):
                    self._input.suggester = None  # type: ignore[attr-defined]
                self._show_file_completion_hint()
                return
            self._last_file_completion_hint = None
            if not line.startswith("/"):
                self._input.suggestions = []  # type: ignore[attr-defined]
                return
            suggs: List[str] = []
            for it in items:
                suggs.append(self._format_suggestion(it.title))
            uniq: List[str] = []
            seen = set()
            for s in suggs:
                if s not in seen:
                    seen.add(s)
                    uniq.append(s)
            self._input.suggestions = uniq  # type: ignore[attr-defined]
            if hasattr(self._input, "suggest_on"):
                self._input.suggest_on = "typing"  # type: ignore[attr-defined]
        except Exception:
            pass

    def _format_suggestion(self, title: str) -> str:
        value = title.strip()
        if not value.startswith("/"):
            value = "/" + value
        if not value.endswith(" "):
            value += " "
        return value

    def _reset_tab_cycle(self) -> None:
        self._tab_cycle_state = {"mode": "", "prefix": "", "index": 0, "suggestions": []}
        self._last_file_completion_hint = None
        self._last_file_completion_choices = []
        self._last_file_completion_abs_dir = None

    def _detect_file_completion_context(self, line: str) -> Tuple[bool, str, str, str]:
        if not self._input:
            return False, "", "", ""
        cursor = getattr(self._input, "cursor_position", len(line))
        before = line[:cursor]
        after = line[cursor:]
        if after and after.strip():
            return False, "", "", ""
        prefixes = ["/load file ", "/file "]
        for prefix in prefixes:
            if before.startswith(prefix):
                fragment = before[len(prefix) :]
                return True, prefix, fragment, after
        if before in {"/load file", "/file"} and line.endswith(" "):
            prefix = before + " "
            return True, prefix, "", after
        return False, "", "", ""

    def _file_path_completion_values(self, line: str) -> List[str]:
        active, prefix, fragment, suffix = self._detect_file_completion_context(line)
        if not active:
            return []
        fragment = fragment or ""
        cwd = os.getcwd()
        separators = ["/", "\\"]
        display_prefix = ""
        search = fragment
        if fragment.endswith(tuple(separators)):
            display_prefix = fragment
            search = ""
        else:
            idx = max(fragment.rfind(sep) for sep in separators)
            if idx >= 0:
                display_prefix = fragment[: idx + 1]
                search = fragment[idx + 1 :]
        base_dir = cwd
        expanded_prefix = os.path.expanduser(display_prefix) if display_prefix else ""
        if display_prefix:
            if os.path.isabs(expanded_prefix):
                base_dir = expanded_prefix
            else:
                base_dir = os.path.abspath(os.path.join(cwd, expanded_prefix))
        expanded_fragment = os.path.expanduser(fragment)
        if fragment and os.path.isdir(expanded_fragment):
            base_dir = expanded_fragment
            if not fragment.endswith(tuple(separators)):
                display_prefix = fragment + os.path.sep
            search = ""
        elif fragment and fragment.endswith(tuple(separators)):
            base_dir = os.path.expanduser(fragment)

        self._last_file_completion_choices = []
        try:
            entries = sorted(os.listdir(base_dir))
        except Exception:
            self._last_file_completion_hint = None
            self._last_file_completion_choices = []
            self._last_file_completion_abs_dir = None
            return []

        suggestions: List[str] = []
        dir_sep = "/" if display_prefix.endswith("/") else "\\" if display_prefix.endswith("\\") else os.path.sep
        display_hint = display_prefix if display_prefix else "."
        try:
            home = os.path.expanduser("~")
            if display_hint.startswith(home):
                display_hint = display_hint.replace(home, "~", 1)
        except Exception:
            pass
        self._last_file_completion_hint = display_hint
        self._last_file_completion_abs_dir = base_dir

        limit = 60
        count = 0
        for name in entries:
            if name in {".", ".."}:
                continue
            if search and not name.startswith(search):
                continue
            candidate_display = display_prefix + name
            absolute_path = os.path.join(base_dir, name)
            if os.path.isdir(absolute_path):
                append_sep = dir_sep if not candidate_display.endswith((dir_sep, "/", "\\")) else ""
                candidate_display = f"{candidate_display}{append_sep}"
            formatted = candidate_display
            if any(ch.isspace() for ch in formatted):
                formatted = f'"{formatted}"'
            value = prefix + formatted + suffix
            suggestions.append(value)
            self._last_file_completion_choices.append(candidate_display)
            count += 1
            if count >= limit:
                break
        return suggestions

    def _show_file_completion_hint(self) -> None:
        if not self._command_hint:
            return
        hint = self._last_file_completion_hint
        choices = list(self._last_file_completion_choices or [])
        try:
            if not hint:
                if choices:
                    self._command_hint.show_strings(choices[:8], title="Files")
                else:
                    self._command_hint.show_message(
                        "Tab to complete file paths (supports ~). Enter with no path opens picker."
                    )
                return
            label = "current directory" if hint == "." else hint
            title = f"Files in {label} · Tab to cycle · Enter loads"
            display_choices = choices[:8]
            if len(choices) > 8:
                display_choices.append("...")
            if display_choices:
                self._command_hint.show_strings(display_choices, title=title)
            else:
                message = (
                    f"Tab to complete file paths (base: {hint}; ~ expands to home). "
                    "Enter with a path loads immediately; Enter alone opens picker."
                )
                self._command_hint.show_message(message)
        except Exception:
            pass
