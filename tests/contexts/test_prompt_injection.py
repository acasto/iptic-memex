from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contexts.prompt_context import PromptContext
from component_registry import PromptResolver


class FakeConfig:
    def get_default_prompt_source(self):
        return None

    def get_option(self, section: str, option: str, fallback=None):
        # Keep templating disabled via DEFAULT.template_handler return
        if section == 'DEFAULT' and option == 'template_handler':
            return 'none'
        return fallback


class FakeSession:
    def __init__(self):
        self._mode = 'pseudo'
        self.config = FakeConfig()
        class _Out:
            def write(self, *a, **k):
                pass
            def warning(self, *a, **k):
                pass
        class _Utils:
            def __init__(self):
                self.output = _Out()
        self._utils = _Utils()

    @property
    def utils(self):
        return self._utils

    def get_action(self, name: str):
        if name == 'build_system_addenda':
            # Simple shim that returns a fixed addenda string
            class _A:
                def run(self_inner, content=None):
                    return "ADDENDA"
            return _A()
        # Return None for other handlers (templating disabled anyway)
        return None

    def get_option(self, section: str, option: str, fallback=None):
        # Ensure templating is disabled in this test
        if section == 'DEFAULT' and option == 'template_handler':
            return 'none'
        return fallback


def test_prompt_context_appends_addenda():
    sess = FakeSession()
    resolver = PromptResolver(sess.config)
    pc = PromptContext(sess, content='BASE', prompt_resolver=resolver)
    data = pc.get()
    assert data['content'] == 'BASE\n\nADDENDA'
