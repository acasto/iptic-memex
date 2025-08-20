from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from actions.prompt_template_tools_action import PromptTemplateToolsAction


class FakeConfig:
    def get_option(self, *a, **k):
        return None


class FakeSession:
    def __init__(self, mode: str, pseudo_key: str | None):
        self._mode = mode
        self._pseudo = pseudo_key
        self.config = FakeConfig()

    def get_effective_tool_mode(self):
        return self._mode

    def get_option(self, section: str, option: str, fallback=None):
        if section == 'TOOLS' and option == 'pseudo_tool_prompt':
            return self._pseudo if self._pseudo is not None else fallback
        return fallback


def test_placeholder_removed_when_not_pseudo():
    sess = FakeSession('official', 'any_key')
    act = PromptTemplateToolsAction(sess)
    content = "Hello\n{{pseudo_tool_prompt}}\nWorld"
    out = act.run(content)
    assert out == "Hello\n\nWorld"


def test_placeholder_injected_when_pseudo_with_key():
    sess = FakeSession('pseudo', 'my_inline_text')
    act = PromptTemplateToolsAction(sess)
    content = "A\n{{pseudo_tool_prompt}}\nZ"
    out = act.run(content)
    # When the resolver cannot find a prompt file, it returns the key itself.
    assert out == "A\nmy_inline_text\nZ"


def test_placeholder_empty_when_pseudo_without_key():
    sess = FakeSession('pseudo', None)
    act = PromptTemplateToolsAction(sess)
    content = "Start {{pseudo_tool_prompt}} End"
    out = act.run(content)
    assert out == "Start  End"

