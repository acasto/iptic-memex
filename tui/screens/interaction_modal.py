"""Modal shown when the runtime requires additional user input."""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.text import Text

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static


class InteractionModal(ModalScreen[Optional[Any]]):
    """Prompt users for additional input when actions require it."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, kind: str, spec: Dict[str, Any]) -> None:
        super().__init__()
        self.kind = kind
        self.spec = spec or {}
        self._choice_entries: list[tuple[str, Any]] = []
        self._default_choice = self.spec.get('default')
        self._default_bool: Optional[bool] = None
        if self.kind == 'bool' and self._default_choice is not None:
            self._default_bool = bool(self._default_choice)

    def compose(self) -> ComposeResult:
        prompt = str(self.spec.get("prompt") or self.spec.get("message") or "Input required")
        with Vertical(id="interaction_modal"):
            yield Static(prompt, id="interaction_prompt")
            if self.kind in ("text", "files"):
                default = self.spec.get("default")
                placeholder = self.spec.get("placeholder") or ""
                self.input = Input(
                    value=str(default or ""),
                    placeholder=str(placeholder),
                    id="interaction_input",
                )
                yield self.input
                hint = "Press Enter to submit · Esc to cancel"
                if self.spec.get("multiline"):
                    hint = "Enter submits · Esc cancels (multi-line supported)"
                yield Static(Text(hint, style="dim"), id="interaction_hint")
            elif self.kind == "bool":
                self.yes_button = Button("Yes", id="yes")
                self.no_button = Button("No", id="no")
                with Horizontal(id="interaction_bool_buttons"):
                    yield self.yes_button
                    yield self.no_button
            elif self.kind == "choice":
                options = list(self.spec.get("options") or self.spec.get("choices") or [])
                self._choice_entries = []
                self.list_view = ListView(id="interaction_choices")
                yield self.list_view
                yield Static(Text("Enter to select · Esc to cancel", style="dim"))
                for opt in options:
                    label = str(opt if not isinstance(opt, dict) else opt.get("label", opt.get("value")))
                    data = opt.get("value") if isinstance(opt, dict) else opt
                    self._choice_entries.append((label, data))
            else:
                self.input = Input(placeholder="Enter value…", id="interaction_input_generic")
                yield self.input
                yield Static(Text("Press Enter to submit · Esc to cancel", style="dim"))

    async def on_mount(self) -> None:
        if hasattr(self, "input"):
            self.set_focus(self.input)
        elif hasattr(self, "list_view"):
            try:
                if self._choice_entries:
                    for label, data in self._choice_entries:
                        item = ListItem(Static(label))
                        setattr(item, "choice_value", data)
                        setattr(item, "choice_label", label)
                        await self.list_view.append(item)
                    self._choice_entries = []
                default_value = self._default_choice
                target_index = 0
                if default_value is not None:
                    for idx, item in enumerate(self.list_view.children):
                        val = getattr(item, "choice_value", None)
                        label = getattr(item, "choice_label", None)
                        if val == default_value or label == default_value:
                            target_index = idx
                            break
                self.list_view.index = target_index
                self.set_focus(self.list_view)
            except Exception:
                pass
        if hasattr(self, "yes_button") and hasattr(self, "no_button"):
            target = self.no_button if self._default_bool is False else self.yes_button
            try:
                self.set_focus(target)
            except Exception:
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        value = event.value
        if self.kind == "files" and isinstance(value, str):
            value = value.split()
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        if event.button.id == "yes":
            self.dismiss(True)
        elif event.button.id == "no":
            self.dismiss(False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # type: ignore[override]
        choice = getattr(event.item, "choice_value", None)
        if choice is None:
            choice = getattr(event.item, "command", None)
        self.dismiss(choice)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        if self.kind == "choice" and event.key in ("tab", "shift+tab"):
            event.prevent_default()
            event.stop()
            list_view = getattr(self, "list_view", None)
            if list_view is not None:
                try:
                    self.set_focus(list_view)
                except Exception:
                    pass
            return
        if self.kind == "bool" and event.key in ("left", "right", "tab", "shift+tab"):
            event.prevent_default()
            event.stop()
            focus = self.focused
            target = None
            if focus is self.yes_button or focus is None:
                target = self.no_button if event.key in ("right", "tab") else self.yes_button
            elif focus is self.no_button:
                target = self.yes_button if event.key in ("left", "shift+tab") else self.no_button
            if target is not None:
                try:
                    self.set_focus(target)
                except Exception:
                    pass
            return
        if self.kind == "bool" and event.key == "enter":
            event.prevent_default()
            event.stop()
            focus = self.focused
            if focus is self.yes_button:
                self.dismiss(True)
            elif focus is self.no_button:
                self.dismiss(False)
            else:
                self.dismiss(self._default_bool if self._default_bool is not None else True)
            return
        parent_on_key = getattr(super(), "on_key", None)
        if callable(parent_on_key):
            parent_on_key(event)
