"""Keyboard search over the conversation already visible in the terminal."""

import re

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from tui.run_inspector import AMBER, MINT, MUTED


class TranscriptSearch(ModalScreen):
    BINDINGS = [
        Binding('up', 'move(-1)', 'Previous', priority=True),
        Binding('down', 'move(1)', 'Next', priority=True),
        Binding('enter', 'pick', 'Locate', priority=True),
        Binding('escape', 'dismiss', 'Back', priority=True),
    ]

    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def compose(self) -> ComposeResult:
        with Vertical(classes='monitor-window'):
            yield Static('FIND IN CONVERSATION', classes='window-title')
            self.search = Input(placeholder='Search messages and tool output')
            yield self.search
            self.matches = OptionList(markup=False, id='search_results')
            yield self.matches
            self.count = Static()
            yield self.count
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self._filter('')
        self.search.focus()

    def on_input_changed(self, event):
        self._filter(event.value.strip())

    def _filter(self, query):
        pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
        options = []
        for message in reversed(self.messages):
            match = pattern.search(message.text) if pattern else None
            if pattern and not match:
                continue
            pos = match.start() if match else 0
            start, end = max(0, pos - 28), pos + max(64, min(len(query), 100))
            excerpt = message.text[start:end].replace('\n', ' ')
            label = Text(message.role.upper() + '  ', style=MINT)
            if start:
                label.append('…', style=MUTED)
            offset = len(label)
            label.append(excerpt)
            if match:
                label.stylize('bold ' + AMBER, offset + pos - start,
                              offset + min(len(excerpt), match.end() - start))
            options.append(Option(label, id=message.msg_id))
            if len(options) == 100:
                break
        self.matches.set_options(options)
        self.matches.highlighted = 0 if options else None
        self.count.update('Newest 100 matches' if len(options) == 100 else
                          f'{len(options)} matching messages' if options else 'No matches')

    def action_move(self, amount):
        if self.matches.option_count:
            self.matches.highlighted = max(0, min(self.matches.option_count - 1,
                                                  (self.matches.highlighted or 0) + amount))

    def action_pick(self):
        if self.matches.highlighted is not None:
            self.dismiss(self.matches.get_option_at_index(self.matches.highlighted).id)

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)
