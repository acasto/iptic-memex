"""Exercise real session, tool and interaction machinery without a network provider."""

import pytest

from base_classes import Completed
from web.selftest import FakeSession


@pytest.fixture
def tool_session():
    def build(*, ask=False, pseudo=False):
        session = FakeSession()
        session.set_option('tool_mode', 'pseudo' if pseudo else 'official')
        seen = []

        class Provider:
            calls = 0

            def chat(self):
                self.calls += 1
                if self.calls == 1:
                    return '%%probe%%\n%%END%%' if pseudo else ''
                return 'Follow-up answer'

            def stream_chat(self):
                yield self.chat()

            def get_tool_calls(self):
                if self.calls == 1 and not pseudo:
                    return [{'id': 'probe-1', 'name': 'probe', 'arguments': {}}]
                return []

        class Probe:
            def run(self, args, content):
                seen.append('start')
                if ask:
                    seen.append(session.interactions.ask_bool('Approve offline probe?'))
                session.ui.emit('status', {'message': 'tool ran'})
                return Completed({'ok': True})

        session.provider = Provider()
        session._actions['probe_action'] = Probe()
        session.set_user_data('__dynamic_tools__', {'probe': {
            'function': {'type': 'action', 'name': 'probe_action'},
            'description': 'Offline probe', 'args': [], 'required': [],
        }})
        return session, seen

    return build
