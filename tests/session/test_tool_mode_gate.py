from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import session as session_mod


class FakeUtils:
    class _Out:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def write(self, *a, **k): pass
    def __init__(self):
        self.output = self._Out()
        self.input = type('I', (), {'get_input': lambda *a, **k: 'y'})()


class FakeConfig:
    def __init__(self, params=None, defaults=None, model_opts=None, provider_opts=None):
        self._params = params or {}
        self._defaults = defaults or {}
        self._model_opts = model_opts or {}
        self._provider_opts = provider_opts or {}
        self.overrides = {}
    def get_params(self):
        return dict(self._params)
    def get_option(self, section, option, fallback=None):
        if section == 'DEFAULT' and option == 'enable_tools':
            return self._defaults.get('enable_tools', True)
        if section == 'TOOLS' and option == 'tool_mode':
            return self._defaults.get('tool_mode', 'official')
        return fallback
    def get_option_from_model(self, option, model):
        return (self._model_opts.get(model) or {}).get(option)
    def get_option_from_provider(self, option, provider):
        return (self._provider_opts.get(provider) or {}).get(option)


class FakeRegistry:
    def __init__(self):
        self.utils = FakeUtils()


def make_session(params, defaults=None, model_opts=None, provider_opts=None):
    cfg = FakeConfig(params=params, defaults=defaults, model_opts=model_opts, provider_opts=provider_opts)
    reg = FakeRegistry()
    return session_mod.Session(cfg, reg)


def test_model_tools_false_hard_gates_to_none():
    sess = make_session(params={'model': 'm1', 'provider': 'p1', 'tools': False}, defaults={'enable_tools': True})
    assert sess.get_effective_tool_mode() == 'none'


def test_tool_mode_precedence_and_global_disable():
    # Global disable wins
    sess = make_session(params={'model': 'm1', 'provider': 'p1'}, defaults={'enable_tools': False})
    assert sess.get_effective_tool_mode() == 'none'

    # Params override wins
    sess = make_session(params={'model': 'm1', 'provider': 'p1', 'tool_mode': 'pseudo'}, defaults={'enable_tools': True, 'tool_mode': 'official'})
    assert sess.get_effective_tool_mode() == 'pseudo'

    # Model override beats provider and TOOLS
    sess = make_session(
        params={'model': 'm1', 'provider': 'p1'},
        defaults={'enable_tools': True, 'tool_mode': 'official'},
        model_opts={'m1': {'tool_mode': 'none'}},
        provider_opts={'p1': {'tool_mode': 'pseudo'}},
    )
    assert sess.get_effective_tool_mode() == 'none'

    # Provider override beats TOOLS when model has no value
    sess = make_session(
        params={'model': 'm1', 'provider': 'p1'},
        defaults={'enable_tools': True, 'tool_mode': 'official'},
        model_opts={},
        provider_opts={'p1': {'tool_mode': 'pseudo'}},
    )
    assert sess.get_effective_tool_mode() == 'pseudo'

