"""Clickable function keys for the main terminal and their keyboard reference."""

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static


# Key, action, short label, keyboard help
KEYS = (
    ('F1', 'key_help', 'Keys', 'Open this keyboard reference'),
    ('F2', 'open_commands', 'Commands', 'Browse slash commands (also Ctrl+K)'),
    ('F5', 'inspect_run', 'Inspect', 'Inspect model calls, tool activity and input estimates'),
    ('F6', 'watch_tasks', 'Tasks', 'Watch tasks recorded in this project'),
    ('F7', 'open_reader', 'Reader', 'Read and select text from the highlighted message'),
    ('F8', 'show_status', 'Status', 'Review status messages'),
    ('^F', 'find_message', 'Find', 'Find and highlight an earlier message'),
    ('^X', 'cancel_turn', 'Stop', 'Cancel the active chat turn'),
)


def key_strip(width, private):
    line = Text(no_wrap=True, overflow='crop')
    for key, action, label, _ in KEYS:
        if private and action == 'watch_tasks':
            continue
        item = Text(f' {key} ', style='bold #17292c on #b0d9c5')
        item.append(f' {label} ', style='#e0ede5 on #25403c')
        if len(line) + len(item) > width:
            break
        line.append_text(item.on(click='app.' + action))
        line.append(' ')
    return line


class KeyReference(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Back', priority=True)]

    def compose(self):
        with Vertical(classes='monitor-window'):
            yield Static('KEYBOARD REFERENCE', classes='window-title')
            with VerticalScroll():
                body = Text()
                for key, action, _, description in KEYS:
                    if self.app.session.private and action == 'watch_tasks':
                        continue
                    body.append(f'{key:10}', style='bold #ffc875')
                    body.append(description + '\n')
                for key, description in (
                    ('Enter', 'Send the draft'),
                    ('Ctrl+J', 'Insert a line break (also Shift+Enter)'),
                    ('Tab', 'Complete a command or file path'),
                    ('Ctrl+S', 'Switch streaming between turns'),
                    ('Ctrl+Q', 'Quit Memex'),
                ):
                    body.append(f'{key:10}', style='bold #ffc875')
                    body.append(description + '\n')
                yield Static(body)
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.query_one(VerticalScroll).focus()
