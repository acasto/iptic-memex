"""A bounded, in-memory record of the chat runs observed by this terminal."""

from collections import deque
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static


MINT = '#81e1c1'
AMBER = '#ffc875'
MUTED = '#92aaa7'
INK = '#e0ede5'


def single_line(value, limit=240):
    """Keep arbitrary model and tool text literal, bounded, and on one terminal line."""
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in str(value)[:limit]).split())


def tool_detail(data):
    args = data.get('args') or {}
    for keys in (('mode', 'file'), ('command', 'arguments'), ('query',),
                 ('operation', 'target', 'message'), ('desc',)):
        values = [single_line(args[key]) for key in keys if args.get(key)]
        if values:
            return ' '.join(values)
    return single_line(data.get('content') or '')


@dataclass
class RunTrace:
    title: str
    window: int
    phase: str = 'Starting'
    calls: int = 0
    tools: int = 0
    estimate: int | None = None
    entries: deque = field(default_factory=lambda: deque(maxlen=160))

    def observe(self, event):
        kind, data = event.type, event.data
        if kind == 'provider_started':
            self.calls += 1
            self.estimate = data.get('estimated_input_tokens')
            self.phase = 'Model working'
            detail = f'Model call {self.calls}'
            if self.estimate is not None:
                detail += f' · ~{self.estimate:,} input tokens'
            label = 'CALL'
        elif kind == 'tool_started':
            self.tools += 1
            self.phase = 'Running tool'
            label, detail = 'TOOL', f"{data['name']} {tool_detail(data)}".rstrip()
        elif kind == 'tool_finished':
            label, detail = 'RETURN', f"{data['name']} · {data['status']}"
            self.phase = 'Continuing'
        elif kind == 'interaction_requested':
            self.phase = 'Input needed'
            label, detail = 'INPUT', data['spec'].get('prompt') or data['kind']
        elif kind == 'interaction_resolved':
            self.phase = 'Continuing'
            label, detail = 'REPLY', 'Input received'
        elif kind in ('warning', 'error'):
            label, detail = kind.upper(), data.get('message', '')
        elif kind == 'usage':
            usage = data.get('usage') or {}
            if not isinstance(usage, dict) or type(usage.get('turn_in')) is not int:
                return False
            label, detail = 'USAGE', f"Last model call · {usage['turn_in']:,} input tokens"
            if type(usage.get('turn_cached')) is int:
                detail += f" · {usage['turn_cached']:,} cached"
        elif kind == 'run_finished':
            self.phase = data['status'].replace('_', ' ').capitalize()
            label, detail = 'END', self.phase
            if data.get('stop_reason'):
                detail += ' · ' + data['stop_reason']
        else:
            return False
        self.entries.append((event.timestamp[11:19], label, single_line(detail)))
        return True


def summary(trace):
    result = Text()
    phase = trace.phase if trace else 'Ready'
    result.append('● ' + phase, style='bold ' + (AMBER if phase == 'Input needed' else MINT))
    if trace:
        result.append(f'\nModel calls  {trace.calls}   Tools  {trace.tools}', style=INK)
    result.append('\n\nINPUT ESTIMATE\n', style=MUTED)
    if trace is None or trace.estimate is None:
        result.append('Awaiting a model call', style=MUTED)
    else:
        count = trace.estimate
        result.append(f'{count:,}', style='bold ' + INK)
        if trace.window > 0:
            result.append(f' / {trace.window:,} tokens\n', style=MUTED)
            bars = min(18, round(18 * count / trace.window))
            result.append('━' * bars, style=AMBER if count >= .85 * trace.window else MINT)
            result.append('─' * (18 - bars), style=MUTED)
            percent = '<1%' if 0 < count / trace.window < .01 else f'{count / trace.window:.0%}'
            result.append('  ' + percent, style=INK)
        else:
            result.append(' tokens', style=MUTED)
    return result


def ledger(trace, *, compact=False):
    result = Text()
    if not trace or not trace.entries:
        return Text('Provider calls, tools and requests for input appear here.', style=MUTED)
    rows = list(trace.entries)
    for stamp, label, detail in (reversed(rows[-5:]) if compact else rows):
        if result:
            result.append('\n')
        if not compact:
            result.append(stamp + '  ', style=MUTED)
        result.append(label, style=AMBER if label in ('INPUT', 'WARNING', 'ERROR') else MINT)
        result.append(('\n' if compact else '  ') + detail, style=INK)
        if compact:
            result.append('\n')
    return result


class RunInspector(ModalScreen):
    """Browse recent chat turns while the live run continues."""

    BINDINGS = [
        Binding('left', 'step(-1)', 'Previous turn', priority=True),
        Binding('right', 'step(1)', 'Next turn', priority=True),
        Binding('escape', 'dismiss', 'Back', priority=True),
    ]

    def __init__(self, traces):
        super().__init__()
        self.traces = traces
        self.selected = None  # Follow the newest trace until the user browses back.

    def compose(self) -> ComposeResult:
        with Vertical(classes='monitor-window'):
            yield Static('RUN INSPECTOR', classes='window-title')
            with VerticalScroll(id='inspect_scroll'):
                yield Static(id='inspect_turn', classes='section-title')
                yield Static(id='inspect_summary')
                yield Static('EVENT LOG  /  UTC', classes='section-title')
                yield Static(id='inspect_events')
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.redraw()
        self.query_one(VerticalScroll).focus()

    def redraw(self):
        rows = list(self.traces)
        index = next((i for i, row in enumerate(rows) if row is self.selected), len(rows) - 1)
        trace = rows[index] if rows else None
        title = f'{index + 1}/{len(rows)}   {trace.title}' if trace else 'No runs in this session'
        self.query_one('#inspect_turn', Static).update(Text(title))
        self.query_one('#inspect_summary', Static).update(summary(trace))
        self.query_one('#inspect_events', Static).update(ledger(trace))

    def action_step(self, offset):
        rows = list(self.traces)
        if not rows:
            return
        index = next((i for i, row in enumerate(rows) if row is self.selected), len(rows) - 1)
        index = max(0, min(len(rows) - 1, index + offset))
        self.selected = None if index == len(rows) - 1 else rows[index]
        self.redraw()
        self.query_one(VerticalScroll).scroll_home(animate=False)
