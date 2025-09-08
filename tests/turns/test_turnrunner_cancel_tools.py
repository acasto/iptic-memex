from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions


class ChatContext:
    def __init__(self):
        self._msgs = []

    def add(self, content: str, role: str, extra=None, contexts: list | None = None):
        self._msgs.append({"role": role, "content": content, "extra": extra or {}, "contexts": contexts or []})

    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None


class ProviderWithTools:
    def __init__(self):
        self._usage = {}
        self._cost = 0

    def chat(self) -> str:
        # Return anything; tool calls are returned from get_tool_calls
        return "running tools"

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
        # Two fake calls with ids
        return [
            {"id": "tc_1", "name": "cmd", "arguments": {}},
            {"id": "tc_2", "name": "cmd", "arguments": {}},
        ]


class FakeOutput:
    def write(self, *args, **kwargs):
        pass

    def stop_spinner(self):
        pass

    def spinner(self, *a, **k):
        from contextlib import nullcontext
        return nullcontext()


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()


class AssistantCommandsStub:
    def __init__(self):
        # Minimal commands map for tool resolution
        self.commands = {
            "cmd": {
                "function": {"type": "action", "name": "fake_tool"},
                "auto_submit": True,
            }
        }


class FakeToolAction:
    def __init__(self, session):
        self.session = session
        self.calls = 0

    def run(self, args=None, content=None):
        self.calls += 1
        # Simulate user Ctrl+C on first tool
        if self.calls == 1:
            raise KeyboardInterrupt()
        # Second tool would normally run, but our loop should be cancelled before reaching here
        self.session.add_context('assistant', {'name': 'tool_output', 'content': 'OK'})


class SessionToolsCancel:
    def __init__(self):
        self.utils = FakeUtils()
        self._contexts = {"chat": ChatContext()}
        self._flags = {}
        self._params = {"stream": False}
        self._provider = ProviderWithTools()
        self._actions = {
            'assistant_commands': AssistantCommandsStub(),
        }
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
        return self._contexts.get(name)

    def add_context(self, name, value=None):
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        self._contexts[name] = value
        return value

    def get_contexts(self, kind=None):
        # Return list of assistant contexts for tool output mapping
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


def test_cancelled_tools_emit_tool_results_for_all_calls():
    sess = SessionToolsCancel()
    runner = TurnRunner(sess)
    # Run a single turn; the first tool raises KeyboardInterrupt which should cancel the loop
    runner.run_user_turn("please do tools", options=TurnOptions(stream=False))

    # Inspect chat messages for tool role messages with 'Cancelled'
    msgs = sess._contexts['chat'].get('all')
    tool_msgs = [m for m in msgs if m.get('role') == 'tool']
    # Expect 2 tool results, one for each call id
    assert len(tool_msgs) == 2
    # Content should be 'Cancelled'
    assert all((m.get('content') == 'Cancelled') for m in tool_msgs)

