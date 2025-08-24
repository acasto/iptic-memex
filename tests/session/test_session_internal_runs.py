from __future__ import annotations

import os
import sys
import types
import builtins

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import session as session_mod


class FakeUtils:
    class _Out:
        def write(self, *a, **k):
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
    def __init__(self):
        self.output = self._Out()


class FakeConfig:
    def get_params(self, model: str | None = None):
        return {}


class FakeRegistry:
    def __init__(self):
        self.utils = FakeUtils()


def make_session():
    # Construct a Session with minimal fake dependencies
    return session_mod.Session(FakeConfig(), FakeRegistry())


def test_run_internal_completion_delegates_and_returns_result(monkeypatch):
    sess = make_session()
    # Attach a fake builder marker so helper doesn't error
    setattr(sess, '_builder', object())

    calls = {}
    def fake_run_completion(builder, overrides=None, contexts=None, message='', capture='text'):
        calls['builder'] = builder
        calls['overrides'] = overrides
        calls['contexts'] = contexts
        calls['message'] = message
        calls['capture'] = capture
        return {'ok': True, 'msg': message, 'ovr': overrides}

    import core.mode_runner as mr
    monkeypatch.setattr(mr, 'run_completion', fake_run_completion)

    res = sess.run_internal_completion(message='hello', overrides={'model': 'm1'}, contexts=[('x', 1)], capture='text')
    assert isinstance(res, dict) and res.get('ok') is True and res.get('msg') == 'hello'
    assert calls.get('overrides') == {'model': 'm1'} and calls.get('contexts') == [('x', 1)]


def test_run_internal_agent_delegates_and_returns_result(monkeypatch):
    sess = make_session()
    setattr(sess, '_builder', object())

    calls = {}
    def fake_run_agent(builder, steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls['builder'] = builder
        calls['steps'] = steps
        calls['overrides'] = overrides
        calls['contexts'] = contexts
        calls['output'] = output
        calls['verbose_dump'] = verbose_dump
        return {'ok': True, 'steps': steps, 'out': output}

    import core.mode_runner as mr
    monkeypatch.setattr(mr, 'run_agent', fake_run_agent)

    res = sess.run_internal_agent(steps=2, overrides={'model': 'm2'}, contexts=[('y', 2)], output='final', verbose_dump=True)
    assert isinstance(res, dict) and res.get('ok') is True and res.get('steps') == 2
    assert calls.get('overrides') == {'model': 'm2'} and calls.get('output') == 'final' and calls.get('verbose_dump') is True

