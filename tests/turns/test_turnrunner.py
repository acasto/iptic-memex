from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from turns import TurnRunner, TurnOptions


class FakeOutput:
    def write(self, *args, **kwargs):
        pass


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()

    def replace_output(self, out):
        self.output = out


class ChatContext:
    def __init__(self):
        self._msgs = []

    def add(self, content: str, role: str, contexts: list | None = None):
        self._msgs.append({"role": role, "content": content, "contexts": contexts or []})

    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None


class ProviderSeq:
    def __init__(self, responses: list[str]):
        self._seq = list(responses)
        self._usage = {}
        self._cost = 0

    def chat(self) -> str:
        return self._seq.pop(0) if self._seq else ""

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


class FakeProcessContexts:
    def get_contexts(self, session):
        return []

    def process_contexts_for_user(self, auto_submit: bool):
        return []


class FakeAssistantCommands:
    def __init__(self, session):
        self._session = session
        self.calls = 0

    def parse_commands(self, text: str):
        # Consider any non-empty text a command on first pass
        return ["tool:"] if text and self.calls == 0 else []

    def run(self, text: str):
        # Set auto_submit once to trigger follow-up assistant turn
        self.calls += 1
        if self.calls == 1:
            self._session.set_flag("auto_submit", True)


class FakeSession:
    def __init__(self, provider):
        self.utils = FakeUtils()
        self._contexts = {"chat": ChatContext()}
        self._actions = {
            "process_contexts": FakeProcessContexts(),
            "assistant_commands": FakeAssistantCommands(self),
        }
        self._flags = {}
        self._params = {"stream": False}
        self._provider = provider

    def get_params(self):
        return dict(self._params)

    def set_option(self, key, value):
        self._params[key] = value

    def get_option(self, section, key, fallback=None):
        if section == "TOOLS" and key == "allow_auto_submit":
            return True
        return fallback

    def get_flag(self, name):
        return self._flags.get(name)

    def set_flag(self, name, value):
        self._flags[name] = value

    def get_context(self, name):
        return self._contexts.get(name)

    def add_context(self, name, value=None):
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        self._contexts[name] = value
        return value

    def remove_context_type(self, name):
        self._contexts.pop(name, None)

    def get_action(self, name):
        return self._actions.get(name)

    def get_provider(self):
        return self._provider


def test_run_user_turn_with_auto_submit_triggers_two_turns():
    provider = ProviderSeq(["Please run tool", "Final answer"])
    sess = FakeSession(provider)
    runner = TurnRunner(sess)
    result = runner.run_user_turn("hi", options=TurnOptions(stream=False))
    assert result.turns_executed == 2  # one initial + one auto-submitted
    assert result.last_text.endswith("Final answer")


def test_run_agent_loop_stops_on_sentinel():
    provider = ProviderSeq(["Work... %%DONE%% More text ignored"])
    sess = FakeSession(provider)
    runner = TurnRunner(sess)
    result = runner.run_agent_loop(steps=5, options=TurnOptions(agent_output_mode="final"))
    assert result.stopped_on_sentinel is True
    assert result.turns_executed == 1

