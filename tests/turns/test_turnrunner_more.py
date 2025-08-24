from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions


class FakeOutput:
    def write(self, *args, **kwargs):
        pass
    def stop_spinner(self):
        pass
    def spinner(self, *args, **kwargs):
        class _N:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _N()


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()


class ChatContext:
    def __init__(self):
        self._msgs = []
    def add(self, content: str, role: str = "user", contexts: list | None = None, extra: dict | None = None):
        entry = {"role": role, "content": content, "contexts": contexts or []}
        if extra:
            entry.update(extra)
        self._msgs.append(entry)
    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None
    def remove_last_message(self):
        if self._msgs:
            self._msgs.pop()


class ProviderOnce:
    def __init__(self, text: str):
        self._text = text
    def chat(self) -> str:
        t = self._text
        self._text = ""
        return t
    def stream_chat(self):
        yield from []


class FakeProcessContexts:
    def get_contexts(self, session):
        return []
    def process_contexts_for_user(self, auto_submit: bool):
        return []


class FakeAssistantCommands:
    def __init__(self, session):
        self._session = session
    def parse_commands(self, text: str):
        # Simple: any non-empty text is a command
        return ["cmd:"] if text else []
    def run(self, text: str):
        # Would set auto_submit, but the test can disable via config
        self._session.set_flag('auto_submit', True)


class FakeSession:
    def __init__(self, provider, allow_auto_submit: bool = True):
        self.utils = FakeUtils()
        self._contexts = {"chat": ChatContext()}
        self._provider = provider
        self._flags = {}
        self._params = {"stream": False}
        self._actions = {
            "process_contexts": FakeProcessContexts(),
            "assistant_commands": FakeAssistantCommands(self),
        }
        self._allow_auto_submit = allow_auto_submit
    def get_params(self):
        return dict(self._params)
    def set_option(self, key, value):
        self._params[key] = value
    def get_option(self, section, key, fallback=None):
        if section == 'TOOLS' and key == 'allow_auto_submit':
            return bool(self._allow_auto_submit)
        return fallback
    def get_flag(self, name):
        return self._flags.get(name)
    def set_flag(self, name, value):
        self._flags[name] = value
    def get_context(self, name):
        return self._contexts.get(name)
    def add_context(self, name, value=None):
        if name == 'chat':
            ctx = self._contexts.get('chat') or ChatContext()
            self._contexts['chat'] = ctx
            return ctx
        self._contexts[name] = value
        return value
    def remove_context_type(self, name):
        self._contexts.pop(name, None)
    def get_action(self, name):
        return self._actions.get(name)
    def get_provider(self):
        return self._provider
    def in_agent_mode(self):
        return False


def test_final_mode_strips_sentinels_and_stops():
    provider = ProviderOnce("Work... %%DONE%% more ignored")
    sess = FakeSession(provider)
    runner = TurnRunner(sess)
    res = runner.run_agent_loop(steps=3, options=TurnOptions(agent_output_mode='final'))
    assert res.stopped_on_sentinel is True
    assert '%%DONE%%' not in (res.last_text or '')


def test_no_auto_submit_when_disallowed():
    provider = ProviderOnce("Please run tool")
    sess = FakeSession(provider, allow_auto_submit=False)
    runner = TurnRunner(sess)
    res = runner.run_user_turn("hi", options=TurnOptions(stream=False))
    # Should not follow up even though assistant_commands set auto_submit
    assert res.turns_executed == 1


def test_early_stop_no_tools_breaks_loop():
    provider = ProviderOnce("Plain text answer")
    # Session without assistant_commands so no tools ever run
    class SessNoTools(FakeSession):
        def __init__(self, provider):
            super().__init__(provider)
            self._actions = {"process_contexts": FakeProcessContexts()}
    sess = SessNoTools(provider)
    runner = TurnRunner(sess)
    res = runner.run_agent_loop(steps=3, options=TurnOptions(agent_output_mode='final', early_stop_no_tools=True))
    assert res.turns_executed == 1
    assert res.ran_tools is False
