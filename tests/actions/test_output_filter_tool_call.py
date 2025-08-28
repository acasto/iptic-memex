import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.assistant_output_action import AssistantOutputAction
from actions.output_filter_tool_call_action import OutputFilterToolCallAction


class FakeOutput:
    def __init__(self):
        self.lines = []

    def write(self, *args, **kwargs):
        # Sink writes during non-streaming filter path
        self.lines.append(''.join(str(a) for a in args))

    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def stop_spinner(self):
        pass

    def spinner(self, *args, **kwargs):
        from contextlib import nullcontext
        return nullcontext()


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()


class FakeSession:
    def __init__(self, params=None):
        self._params = params or {}
        self.utils = FakeUtils()

    def get_params(self):
        return self._params

    def get_action(self, name):
        # Return a new instance of the tool_call filter when requested
        if name == 'output_filter_tool_call':
            return OutputFilterToolCallAction(self)
        return None


def filter_text(text: str, params: dict):
    sess = FakeSession(params=params)
    return AssistantOutputAction.filter_full_text(text, sess)


def test_tool_block_hidden_when_unfenced_with_placeholder():
    text = """before\n%%TEST%%\nargs=\"x\"\n%%END%%\nafter\n"""
    params = {
        'output_filters': 'tool_call',
        'tool_placeholder': '[HIDDEN:{name}]',
    }
    out = filter_text(text, params)
    assert out == "before\n[HIDDEN:TEST]\nafter\n"


def test_tool_block_not_hidden_inside_code_fence():
    text = """```\n%%TEST%%\nargs=\"testing\"\n%%END%%\n```\n"""
    params = {
        'output_filters': 'tool_call',
        'tool_placeholder': '[HIDDEN:{name}]',
    }
    out = filter_text(text, params)
    # Inside a fenced block, content must pass through unchanged
    assert out == text


def test_quoted_opener_line_is_not_treated_as_block():
    text = '"%%TEST%%"\nargs=\"testing\"\n%%END%%\n'
    params = {
        'output_filters': 'tool_call',
        'tool_placeholder': '[HIDDEN:{name}]',
    }
    out = filter_text(text, params)
    # The quoted opener should remain; the stray %%END%% (outside any block) is dropped
    assert '"%%TEST%%"' in out
    assert 'args="testing"' in out
    assert '%%END%%' not in out
