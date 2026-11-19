import asyncio

from base_classes import Completed
from tui.app import MemexTUIApp
from ui.tui import TUIUI
from web.selftest import FakeSession


def test_textual_turn_approval_and_busy_input():
    async def exercise():
        session = FakeSession()
        session.ui = TUIUI(session)
        session.set_option('tool_mode', 'official')
        replies = iter(['', 'Approved answer'])
        calls = []
        answers = []

        def reply():
            calls.append(1)
            return next(replies)

        session.provider.stream_chat = lambda: iter([reply()])
        session.provider.get_tool_calls = lambda: (
            [{'id': 'a', 'name': 'probe', 'arguments': {}}] if len(calls) == 1 else [])
        session.set_user_data('__dynamic_tools__', {'probe': {
            'function': {'type': 'action', 'name': 'probe'}, 'args': [], 'description': 'Probe'}})

        class Probe:
            def run(self, args, content):
                answers.append(session.interactions.ask_bool('Approve?'))
                return Completed({'ok': True})

        session._actions['probe'] = Probe()
        session.set_option('stream', False)
        app = MemexTUIApp(session)
        async with app.run_test() as pilot:
            await pilot.press('ctrl+s', 'h', 'i', 'enter')
            await pilot.pause()
            assert app._stream_enabled
            assert app.traces[-1].phase == 'Input needed'
            app._submit_input_text('overlap')
            await pilot.click('#yes')
            await pilot.pause()
            assert answers == [True] and len(calls) == 2
            trace = app.traces[-1]
            assert (trace.phase, trace.calls, trace.tools) == ('Completed', 2, 1)
            assert [label for _, label, _ in trace.entries if label in ('INPUT', 'REPLY')] == [
                'INPUT', 'REPLY']
            assert [e.text for e in app.chat_view.entries if e.role == 'assistant'] == ['Approved answer']
            assert len([t for t in session.get_context('chat').get('all') if t['role'] == 'user']) == 1
        session.close()

    asyncio.run(exercise())
