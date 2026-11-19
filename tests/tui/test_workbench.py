"""Exercise the terminal workbench with real input, resizing and workspace records."""

import asyncio

from app.project import resolve_project
from tui.app import MemexTUIApp
from tui.run_inspector import RunInspector
from tui.screens.reader_overlay import ReaderOverlay
from tui.screens.task_watch import RecordReader, WorkspaceMonitor
from tui.screens.transcript_search import TranscriptSearch
from tui.widgets.function_keys import KeyReference
from ui.tui import TUIUI
from web.selftest import FakeSession
from workspace.state import WorkspaceStore, write_json
from workspace.tasks import TaskStore


def screen_text(app):
    return '\n'.join(line.text for line in app.screen._compositor.render_strips())


def test_inspect_find_and_resize_preserve_draft():
    async def exercise():
        session = FakeSession()
        session.ui = TUIUI(session)
        app = MemexTUIApp(session)
        async with app.run_test(size=(108, 30)) as pilot:
            assert app.rail_summary.parent.display
            for message in ('First decision [bold]', 'Second decision'):
                app._submit_input_text(message)
                await pilot.pause()
            assert len(app.traces) == 2
            assert app.traces[-1].calls == 1 and app.traces[-1].phase == 'Completed'
            app.input.value = 'Unsent draft'
            await pilot.press('f5', 'left')
            assert isinstance(app.screen, RunInspector)
            assert 'First decision [bold]' in screen_text(app)
            await pilot.press('right')
            assert 'Second decision' in screen_text(app)
            await pilot.press('escape', 'ctrl+f')
            assert isinstance(app.screen, TranscriptSearch)
            await pilot.press(*'missing')
            assert app.screen.matches.option_count == 0
            app.screen.search.value = '[bold]'
            await pilot.pause()
            assert app.screen.matches.option_count == 1
            await pilot.press('enter', 'f7')
            assert isinstance(app.screen, ReaderOverlay)
            assert 'First decision [bold]' in screen_text(app)
            await pilot.press('escape')
            await pilot.resize_terminal(56, 18)
            await pilot.pause()
            assert not app.rail_summary.parent.display
            assert 'Completed' in screen_text(app)
            assert app.input.value == 'Unsent draft'
            await pilot.press('f6')
            assert app.screen is app._base_screen  # Private chats never open workspace records.
            await pilot.click('#function_keys', offset=(2, 0))
            assert isinstance(app.screen, KeyReference)
            await pilot.press('escape')
            assert app.input.value == 'Unsent draft'
        session.close()

    asyncio.run(exercise())


def test_workspace_watch_follows_records_and_handles_small_terminal(tmp_path):
    async def exercise():
        session = FakeSession()
        session.private = False
        session.project = resolve_project(tmp_path)
        session.ui = TUIUI(session)
        app = MemexTUIApp(session)
        store = WorkspaceStore(tmp_path / '.memex-workspace')
        tasks = TaskStore(store)
        async with app.run_test(size=(112, 32)) as pilot:
            app.input.value = 'Draft'
            await pilot.press('f6')
            monitor = app.screen
            assert isinstance(monitor, WorkspaceMonitor)
            assert 'No tasks recorded' in screen_text(app)
            assert not store.root.exists()

            other = tasks.create('Review the release', {})
            task = tasks.create('Compare [literal] file reads', {})
            run = store.create_run(task['brief'], {})
            task.update(status='needs_input', attempts=[{'run_id': run['id']}])
            tasks.save(task)
            write_json(store.run_dir(run['id']) / 'input-needed.json',
                       {'question': 'Which baseline?', 'progress': 'Measured the full history.'})
            store.event(run['id'], 'agent-1', 'tool_started',
                        {'name': 'file', 'args': {'file': 'notes.md'}})
            await pilot.press('r')
            assert monitor._selected_id() == task['id']
            assert 'Which baseline?' in screen_text(app)
            assert 'notes.md' in screen_text(app)
            store.event(run['id'], 'agent-2', 'published', {'title': 'New evidence'})
            await pilot.press('r')
            assert 'New evidence' in screen_text(app)
            await pilot.press('down')
            assert monitor._selected_id() == other['id']
            assert 'New evidence' not in screen_text(app)
            await pilot.press('up')
            await pilot.resize_terminal(55, 18)
            await pilot.pause()
            assert monitor.selector.region.height > 0
            await pilot.press('enter', 'v')
            assert isinstance(app.screen, RecordReader)
            assert 'Measured the full history.' in app.screen.body
            await pilot.press('escape', 'escape')
            assert app.screen is monitor and not monitor.drill_down
            await pilot.press('escape')
            assert app.input.value == 'Draft'
            assert not app.traces
            assert tasks.read(task['id'])['status'] == 'needs_input'
        session.close()

    asyncio.run(exercise())


def test_answer_saved_handoff_runs_one_new_attempt(tmp_path, monkeypatch):
    import sys

    from tui.screens.task_watch import ContinueTask
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    from workspace.state import read_json
    from workspace.task_runner import TaskRunner

    # A deterministic subprocess exercises the normal task lease and continuation path.
    worker = """
import json, sys
from pathlib import Path
from workspace.state import write_json
spec = json.load(sys.stdin)
handoff = spec['continuation']
answer = handoff['handoff']['progress'] + ' ' + handoff['answer']
write_json(Path(spec['directory']) / 'runs' / spec['run_id'] / 'task-result.json',
           {'status': 'completed', 'answer': answer})
"""
    initialize = TaskRunner.__init__
    monkeypatch.setattr(TaskRunner, '__init__', lambda self, store:
                        initialize(self, store, worker_command=[sys.executable, '-c', worker]))

    async def exercise():
        session = FakeSession()
        session.private = False
        session.project = resolve_project(tmp_path)
        store = WorkspaceStore(tmp_path / '.memex-workspace')
        runner = TaskRunner(store)
        task = runner.create('Choose a baseline', session.project, snapshot(
            Strategy(), ExecutionProfile(model='offline', calls=6)))
        run = store.create_run(task['brief'], {'task_id': task['id']})
        source = run['id']
        task.update(status='needs_input', attempts=[{'run_id': source, 'status': 'needs_input'}])
        runner.tasks.save(task)
        write_json(store.run_dir(source) / 'input-needed.json', dict(
            run_id=source, agent_id='agent-1', question='Which baseline?', progress='Width 42.'))
        write_json(store.run_dir(source) / 'agent-1' / 'history.json',
                   dict(run_id=source, agent_id='agent-1', messages=[],
                        session_id='original', phase='run_finished', updated_at=run['created_at'], snapshot_id='saved'))
        app = MemexTUIApp(session)
        async with app.run_test(size=(94, 30)) as pilot:
            await pilot.press('f6', 'a')
            assert isinstance(app.screen, ContinueTask)
            assert '6 calls' in screen_text(app)
            await pilot.press('escape')
            assert len(runner.tasks.read(task['id'])['attempts']) == 1
            await pilot.press('a')
            app.screen.answer.text = 'Use full history.'
            await pilot.press('alt+enter')
            for _ in range(60):
                if not app._continued_tasks:
                    break
                await pilot.pause(.05)
            final = runner.tasks.read(task['id'])
            assert final['status'] == 'completed' and len(final['attempts']) == 2, (
                app._output_bridge.status_history, screen_text(app))
            attempt = final['attempts'][-1]
            assert attempt['continued_from'] == source
            result = read_json(store.run_dir(attempt['run_id']) / 'task-result.json')
            assert result['answer'] == 'Width 42. Use full history.'
            await pilot.press('r', 'v')
            assert isinstance(app.screen, RecordReader) and result['answer'] in app.screen.body
            assert not app.traces
        session.close()

    asyncio.run(exercise())


def test_terminal_stops_only_its_own_continuations(tmp_path, monkeypatch):
    from threading import Event
    from time import sleep
    from workspace.task_runner import TaskRunner

    started = Event()
    stopped = []

    def active_worker(self, task_id, *, cancellation, before_start, **options):
        before_start()
        started.set()
        while not cancellation.is_cancelled():
            sleep(.01)
        stopped.append(task_id)
        return {'status': 'cancelled'}

    monkeypatch.setattr(TaskRunner, 'run', active_worker)

    async def exercise():
        session = FakeSession()
        session.private = False
        session.project = resolve_project(tmp_path)
        store = WorkspaceStore(tmp_path / '.memex-workspace')
        tasks = TaskStore(store)
        spec = {'project': {'root': str(session.project.root)}}
        own = tasks.create('Continued here', spec)
        other = tasks.create('Owned elsewhere', spec)
        app = MemexTUIApp(session)
        async with app.run_test() as pilot:
            for exit_app in (False, True):
                started.clear()
                app.continue_task(store, own['id'], 'source', 'answer')
                assert await asyncio.to_thread(started.wait, 3)
                if exit_app:
                    break
                await pilot.press('f6')
                monitor = app.screen
                monitor.selector.highlighted = monitor.selector.get_option_index(other['id'])
                await pilot.pause()
                assert not monitor.check_action('stop_attempt', ())
                monitor.selector.highlighted = monitor.selector.get_option_index(own['id'])
                await pilot.press('x')
                for _ in range(40):
                    if not app._continued_tasks:
                        break
                    await pilot.pause(.05)
                assert stopped == [own['id']]
                await pilot.press('escape')
        assert stopped == [own['id'], own['id']]
        session.close()

    asyncio.run(exercise())
