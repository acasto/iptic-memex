"""Observe existing workspace tasks from the terminal without acquiring task ownership."""

import asyncio
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

from tui.run_inspector import AMBER, INK, MINT, MUTED, single_line
from workspace.state import WorkspaceStore, read_json
from workspace.tasks import TaskStore
from workspace.watch import TaskWatch


def task_record(store, task_id):
    """Read the current task and its latest user-facing handoff or result."""
    record = TaskStore(store).inspect(task_id)
    parts = [record['brief'], task_id, 'Status  ' + record['status'].replace('_', ' ')]
    if record['attempts']:
        run_id = record['attempts'][-1]['run_id']
        directory = store.run_dir(run_id)
        parts.append('Attempt  ' + run_id)
        if record['status'] == 'needs_input':
            saved = read_json(directory / 'input-needed.json')
            fields = (('question', 'QUESTION'), ('progress', 'SAVED PROGRESS'))
        else:
            saved = read_json(directory / 'task-result.json')
            fields = (('answer', 'ANSWER'), ('error', 'ERROR'))
        for key, title in fields:
            if saved.get(key):
                parts.extend((title, str(saved[key])))
    if record.get('error'):
        parts.append(str(record['error']))
    return '\n\n'.join(parts)


class RecordReader(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Back', priority=True)]

    def __init__(self, body):
        super().__init__()
        self.body = body

    def compose(self):
        with Vertical(classes='monitor-window'):
            yield Static('TASK RECORD', classes='window-title')
            yield TextArea(self.body, read_only=True, soft_wrap=True, id='record_text')
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.query_one(TextArea).focus()


class ContinueTask(ModalScreen):
    """Collect an explicit answer for the particular handoff the user reviewed."""

    BINDINGS = [Binding('escape', 'dismiss', 'Back', priority=True),
                Binding('alt+enter', 'submit', 'Continue task', priority=True)]

    def __init__(self, record, handoff):
        super().__init__()
        self.record, self.handoff = record, handoff

    def compose(self):
        profile = self.record['spec']['profile']
        with Vertical(classes='monitor-window'):
            yield Static('CONTINUE TASK', classes='window-title')
            notes = (self.record['brief'] + '\n\nSAVED PROGRESS\n' + self.handoff['progress']
                     + '\n\nQUESTION\n' + self.handoff['question'])
            yield TextArea(notes, read_only=True, soft_wrap=True, id='handoff_notes')
            yield Static(Text(f"Fresh allowance: {profile['calls']} calls · {profile['model']}"
                              f" · writes {profile['writes']} · profile tools {', '.join(profile['tools']) or 'none'}"
                              "\nQuitting this TUI stops the continued attempt."), id='handoff_policy')
            self.answer = TextArea(placeholder='Your answer', soft_wrap=True, id='handoff_answer')
            yield self.answer
            yield Button('Continue task', variant='primary', id='continue_task', disabled=True)
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.answer.focus()

    def on_text_area_changed(self, event):
        if event.control is self.answer:
            self.query_one(Button).disabled = not 1 <= len(self.answer.text.strip()) <= 16000

    def on_button_pressed(self):
        self.action_submit()

    def action_submit(self):
        value = self.answer.text.strip()
        if 1 <= len(value) <= 16000:
            self.dismiss(value)


class WorkspaceMonitor(ModalScreen):
    """Follow the selected task; only the visible screen polls the records."""

    BINDINGS = [
        Binding('a', 'answer', 'Answer'),
        Binding('x', 'stop_attempt', 'Stop attempt'),
        Binding('v', 'read_record', 'Read record'),
        Binding('r', 'refresh_records', 'Refresh'),
        Binding('c', 'copy_task_id', 'Copy ID'),
        Binding('escape', 'back', 'Back', priority=True),
    ]

    def __init__(self, directory: Path):
        super().__init__()
        self.store = WorkspaceStore(directory)
        self.tasks = TaskStore(self.store)
        self._choices = None
        self._watch = None
        self._lock = asyncio.Lock()
        self._error = None
        self.drill_down = False
        self._handoff = None

    def compose(self) -> ComposeResult:
        with Vertical(classes='monitor-window'):
            yield Static('WORKSPACE WATCH', classes='window-title')
            yield Static(Text(str(self.store.root)), id='watch_path')
            with Horizontal(id='watch_panes'):
                self.selector = OptionList(id='task_selector', markup=False)
                self.selector.border_title = 'Tasks'
                self.selector.border_subtitle = 'Enter inspect'
                yield self.selector
                with Vertical(id='task_detail'):
                    yield Static('Looking for task records…', id='task_brief', markup=False)
                    self.activity = RichLog(id='task_events', markup=False, wrap=True,
                                            min_width=1, max_lines=500)
                    self.activity.border_title = 'Activity · UTC'
                    yield self.activity
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.selector.focus()
        self.set_interval(1.5, self.action_refresh_records)
        self.call_after_refresh(self.action_refresh_records)

    def on_resize(self, event):
        self.set_class(event.size.width < 86, 'narrow-watch')

    def on_option_list_option_selected(self):
        if not self._selected_id():
            return
        self.drill_down = True
        self.add_class('task-open')
        self.activity.focus()

    def action_back(self):
        if self.drill_down:
            self.drill_down = False
            self.remove_class('task-open')
            self.selector.focus()
        else:
            self.dismiss()

    def check_action(self, action, parameters):
        if action == 'stop_attempt':
            return bool(self.app.continuation_token(self.store, self._selected_id()))
        if action == 'answer':
            return bool(self._handoff and self._handoff[0]['id'] == self._selected_id()
                        and not self.app.continuation_token(self.store, self._selected_id()))
        return super().check_action(action, parameters)

    def action_stop_attempt(self):
        token = self.app.continuation_token(self.store, self._selected_id())
        if token:
            token.cancel('Stopped in workspace watch')
            self.notify('Stopping the continued attempt.')

    def action_answer(self):
        if not self.check_action('answer', ()):
            return
        record, handoff = self._handoff
        task_id, source_id = record['id'], handoff['run_id']

        def submit(answer):
            if answer:
                self.app.continue_task(self.store, task_id, source_id, answer)

        self.app.push_screen(ContinueTask(record, handoff), submit)

    async def action_read_record(self):
        task_id = self._selected_id()
        if task_id:
            try:
                body = await asyncio.to_thread(task_record, self.store, task_id)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.notify(single_line(exc), severity='warning', markup=False)
                return
            if self.app.screen is self:
                self.app.push_screen(RecordReader(body))

    def action_copy_task_id(self):
        task_id = self._selected_id()
        if task_id:
            try:
                self.app.copy_to_clipboard(task_id)
                self.notify('Task ID copied')
            except RuntimeError as exc:
                self.notify(str(exc), severity='warning', markup=False)

    async def on_option_list_option_highlighted(self):
        await self.action_refresh_records()

    def _selected_id(self):
        if not hasattr(self, 'selector'):
            return None
        index = self.selector.highlighted
        if index is not None and index < self.selector.option_count:
            return self.selector.get_option_at_index(index).id
        return None

    def _update_choices(self, records):
        choices = [(row['id'], row['status'], single_line(row['brief'], 95)) for row in records]
        if choices == self._choices:
            return
        selected = self._selected_id()
        self._choices = choices
        options = []
        for task_id, status, brief in choices:
            label = Text(status.replace('_', ' ').upper() + '  ',
                         style=AMBER if status == 'needs_input' else MINT)
            label.append(brief, style=INK)
            options.append(Option(label, id=task_id))
        self.selector.set_options(options or [Option('No tasks recorded here', disabled=True)])
        if choices:
            self.selector.highlighted = next(
                (i for i, row in enumerate(choices) if row[0] == selected), 0)

    def _poll_watch(self, watch, record, new_selection):
        lines, _ = watch.poll()
        if new_selection:
            # TaskWatch introduces its CLI controls in the first four lines.
            # The screen already presents this metadata and has its own controls.
            lines = [line for line in lines if not line.startswith(
                ('Task: ', 'Brief: ', 'Records: ', 'Read-only watch; Ctrl-C'))]
        handoff = {}
        if record['status'] == 'needs_input' and watch.run_id:
            handoff = read_json(self.store.run_dir(watch.run_id) / 'input-needed.json')
        return lines, handoff

    async def action_refresh_records(self):
        if self.app.screen is not self or self._lock.locked():
            return
        async with self._lock:
            try:
                records = await asyncio.to_thread(self.tasks.list)
                if not self.is_mounted or self.app.screen is not self:
                    return
                records.sort(key=lambda r: r.get('updated_at', ''), reverse=True)
                priority = {'needs_input': 0, 'running': 1, 'pending': 2}
                records.sort(key=lambda r: priority.get(r['status'], 3))
                self._update_choices(records)
                selected = self._selected_id()
                record = next((r for r in records if r['id'] == selected), None)
                brief = self.query_one('#task_brief', Static)
                if record is None:
                    self._watch = None
                    self._handoff = None
                    self.refresh_bindings()
                    self.activity.clear()
                    brief.update('No tasks recorded here. Tasks started in this workspace will appear here.')
                    return
                fresh = self._watch is None or self._watch.task_id != selected
                if fresh:
                    self._watch = TaskWatch(self.store, selected, tail=40)
                    self.activity.clear()
                lines, handoff = await asyncio.to_thread(self._poll_watch, self._watch, record, fresh)
                if not self.is_mounted or self._selected_id() != selected:
                    self._watch = None
                    return
                header = Text(single_line(record['brief'], 500), style='bold ' + INK)
                header.append('\n' + selected, style=MUTED)
                if handoff:
                    header.append('\nInput needed  ', style=AMBER)
                    header.append(single_line(handoff.get('question', ''), 500))
                    if handoff.get('progress'):
                        header.append('\n' + single_line(handoff['progress'], 500), style=INK)
                project = getattr(self.app.session, 'project', None)
                spec = record.get('spec') or {}
                profile = spec.get('profile') or {}
                eligible = (project and {'calls', 'model', 'writes', 'tools'} <= profile.keys()
                            and spec.get('project', {}).get('root') == str(project.root)
                            and spec.get('strategy', {}).get('workflow') == 'session'
                            and handoff.get('run_id') and handoff['run_id'] == self._watch.run_id
                            and handoff.get('progress') and handoff.get('question'))
                self._handoff = (record, handoff) if eligible else None
                self.refresh_bindings()
                brief.update(header)
                for line in lines:
                    stamp, sep, rest = line.partition('  ')
                    if sep and len(stamp) >= 19 and stamp[10:11] == 'T':
                        agent, _, description = rest.partition('  ')
                        entry = Text(stamp[11:19] + '  ', style=MUTED)
                        entry.append(single_line(agent) + '  ', style=MINT)
                        entry.append(single_line(description, 4500))
                    else:
                        entry = Text(single_line(line, 4500),
                                     style=AMBER if line.startswith('Input needed:') else INK)
                    self.activity.write(entry)
                self._error = None
            except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
                if self.is_mounted and str(exc) != self._error:
                    self._handoff = None
                    self.refresh_bindings()
                    self._error = str(exc)
                    self.query_one('#task_brief', Static).update(
                        Text('Could not read workspace records: ' + single_line(exc), style=AMBER))
