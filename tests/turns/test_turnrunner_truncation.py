from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions
from core.null_ui import NullUI
from ui.base import UI


class ChatContext:
    def __init__(self):
        self._msgs = []

    def add(self, content: str, role: str, extra=None, contexts: list | None = None):
        self._msgs.append({"role": role, "content": content, "extra": extra or {}, "contexts": contexts or []})

    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None

    def remove_last_message(self):
        if self._msgs:
            self._msgs.pop()


class AssistantCommandsStub:
    def __init__(self):
        self.commands = {
            "cmd": {
                "function": {"type": "action", "name": "fake_tool"},
                "auto_submit": True,
            }
        }


class FakeToolAction:
    calls = []

    def __init__(self, session):
        self.session = session

    def run(self, args=None, content=None):
        FakeToolAction.calls.append(args)


class FakeOutput:
    """Real-ish output handler: writes to a stream so we can detect prints."""

    def __init__(self):
        self.written = []

    def write(self, *args, **kwargs):
        self.written.append("".join(str(a) for a in args))

    def warning(self, message, **kwargs):
        self.written.append(str(message))

    def stop_spinner(self):
        pass

    def spinner(self, *a, **k):
        from contextlib import nullcontext
        return nullcontext()


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()


class TruncatedProvider:
    """finish_reason 'length' with a malformed first call and a healthy second."""

    def __init__(self):
        self._usage = {}
        self._cost = 0

    def chat(self) -> str:
        return "partial reply"

    def stream_chat(self):
        yield from []

    def get_messages(self):
        return []

    def get_full_response(self):
        return None

    def get_usage(self):
        return self._usage

    def reset_usage(self):
        pass

    def get_cost(self):
        return self._cost

    def get_tool_calls(self):
        return [
            {"id": "tc_1", "name": "cmd", "arguments": {}, "truncated": True},
            {"id": "tc_2", "name": "cmd", "arguments": {"x": 1}, "truncated": False},
        ]

    def get_finish_reason(self):
        return 'length'


class CleanProvider(TruncatedProvider):
    def get_finish_reason(self):
        return 'stop'

    def get_tool_calls(self):
        return [{"id": "tc_1", "name": "cmd", "arguments": {"x": 1}, "truncated": False}]


class SessionBase:
    def __init__(self, provider):
        self.utils = FakeUtils()
        self.ui = NullUI()
        self._contexts = {"chat": ChatContext()}
        self._flags = {}
        self._params = {"stream": False}
        self._provider = provider
        self._actions = {'assistant_commands': AssistantCommandsStub()}
        self._user_data = {}

    def get_params(self):
        return dict(self._params)

    def set_option(self, key, value):
        self._params[key] = value

    def get_option(self, section, key, fallback=None):
        return fallback

    def get_flag(self, name):
        return self._flags.get(name)

    def set_flag(self, name, value):
        self._flags[name] = value

    def get_user_data(self, key, default=None):
        return self._user_data.get(key, default)

    def set_user_data(self, key, value):
        self._user_data[key] = value

    def get_context(self, name):
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        return self._contexts.get(name)

    def add_context(self, name, value=None):
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        self._contexts[name] = value
        return value

    def get_contexts(self, kind=None):
        if kind == 'assistant':
            items = []
            for k, v in self._contexts.items():
                if k == 'assistant':
                    if isinstance(v, list):
                        for item in v:
                            items.append({'type': 'assistant', 'context': item})
                    else:
                        items.append({'type': 'assistant', 'context': v})
            return items
        return []

    def remove_context_type(self, name):
        self._contexts.pop(name, None)

    def get_action(self, name):
        if name == 'fake_tool':
            return FakeToolAction(self)
        return self._actions.get(name)

    def get_provider(self):
        return self._provider

    def get_effective_tool_mode(self):
        return 'official'


def test_exactly_one_result_per_call_and_no_execution():
    """tc_1: one 'skipped' result. tc_2: one 'Cancelled'. Neither executes."""
    FakeToolAction.calls = []
    sess = SessionBase(TruncatedProvider())
    runner = TurnRunner(sess)
    result = runner.run_user_turn("go", options=TurnOptions(stream=False))

    msgs = sess._contexts['chat'].get('all')
    tool_msgs = [m for m in msgs if m.get('role') == 'tool']
    # Exactly one result per call
    ids = [m.get('extra', {}).get('tool_call_id') for m in tool_msgs]
    assert sorted(ids) == ['tc_1', 'tc_2'], f"expected one result per call, got {ids}"
    # tc_1's result is the skip notice; tc_2's is Cancelled
    tc1 = next(m for m in tool_msgs if m.get('extra', {}).get('tool_call_id') == 'tc_1')
    tc2 = next(m for m in tool_msgs if m.get('extra', {}).get('tool_call_id') == 'tc_2')
    assert 'skipped' in (tc1.get('content') or '')
    assert tc2.get('content') == 'Cancelled'
    # Neither call reached the action
    assert FakeToolAction.calls == []
    assert result.truncated is True


def test_warning_goes_to_ui_events_not_stdout():
    """Internal session (NullUI): warning lands in events, nothing prints."""
    sess = SessionBase(TruncatedProvider())
    runner = TurnRunner(sess)
    runner.run_user_turn("go", options=TurnOptions(stream=False))

    assert any(
        e.get('type') == 'warning' and 'token limit' in e.get('message', '')
        for e in sess.ui.events
    ), f"no warning event in NullUI.events: {sess.ui.events}"
    assert any(
        e.get('type') == 'warning' and 'invalid tool arguments' in e.get('message', '')
        for e in sess.ui.events
    ), f"no skipped-call event: {sess.ui.events}"
    # Nothing written to stdout via the output handler
    assert sess.utils.output.written == [], f"printed to output: {sess.utils.output.written}"


def test_clean_turn_quiet_and_executes():
    FakeToolAction.calls = []
    sess = SessionBase(CleanProvider())
    runner = TurnRunner(sess)
    result = runner.run_user_turn("go", options=TurnOptions(stream=False))
    assert result.truncated is False
    assert sess.ui.events == [] or not any(
        e.get('type') == 'warning' for e in sess.ui.events
    )
    # The healthy call executed
    assert FakeToolAction.calls == [{'x': 1}]
