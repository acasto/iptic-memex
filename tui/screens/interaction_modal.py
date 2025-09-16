"""Modal shown when the runtime requires additional user input."""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.text import Text

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
                with Horizontal(id="interaction_bool_buttons"):
                    yield Button("Yes", id="yes")
                    yield Button("No", id="no")
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
                        await self.list_view.append(item)
                    self._choice_entries = []
                self.list_view.index = 0
                self.set_focus(self.list_view)
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
