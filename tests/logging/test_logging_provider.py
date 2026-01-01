from __future__ import annotations

import os
import sys
import json
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions
from utils.logging_utils import LoggingHandler


class FakeProvider:
    def chat(self) -> str:
        return "ok"
    def stream_chat(self):
        yield from []
    def get_messages(self):
        return []
    def get_full_response(self):
        return None
    def get_usage(self):
        return {}
    def reset_usage(self):
        pass
    def get_cost(self):
        return 0.0


class FakeOutput:
    def write(self, *args, **kwargs):
        pass


class FakeUtils:
    def __init__(self, logger):
        self.output = FakeOutput()
        self.logger = logger


class FakeChat:
    def __init__(self):
        self._msgs = []
    def add(self, content: str, role: str, contexts=None):
        self._msgs.append({'role': role, 'content': content})
    def get(self, kind=None):
        return list(self._msgs)


class FakeProcessContexts:
    def get_contexts(self, session):
        return []
    def process_contexts_for_user(self, auto_submit: bool):
        return []


class FakeSession:
    def __init__(self, provider, logger):
        self._provider = provider
        self.utils = FakeUtils(logger)
        self._contexts = {'chat': FakeChat()}
        self._actions = {'process_contexts': FakeProcessContexts()}
        self._params = {'model': 'fake-model', 'stream': False}
        self._flags = {}

    def get_params(self):
        return dict(self._params)
    def set_option(self, k, v):
        self._params[k] = v
    def get_option(self, *a, **k):
        return False
    def set_flag(self, k, v):
        self._flags[k] = v
    def get_flag(self, k, default=False):
        return self._flags.get(k, default)
    def get_context(self, name):
        return self._contexts.get(name)
    def add_context(self, name, value=None):
        if name == 'chat':
            return self._contexts['chat']
        self._contexts[name] = value
        return value
    def remove_context_type(self, name):
        self._contexts.pop(name, None)
    def get_action(self, name):
        return self._actions.get(name)
    def get_provider(self):
        return self._provider


class FakeConfig:
    def __init__(self, opts: dict):
        self._opts = opts
    def get_option(self, section: str, key: str, fallback=None):
        if section != 'LOG':
            return fallback
        return self._opts.get(key, fallback)


def _read_jsonl(path: Path):
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def test_turnrunner_logs_provider_begin_and_done(tmp_path: Path):
    cfg = FakeConfig({'active': True, 'dir': str(tmp_path), 'file': 'memex.log', 'format': 'json', 'log_provider': 'basic'})
    logger = LoggingHandler(cfg)
    sess = FakeSession(FakeProvider(), logger)
    runner = TurnRunner(sess)
    res = runner.run_user_turn('hello', options=TurnOptions(stream=False))
    assert res.turns_executed == 1

    files = list(tmp_path.glob('*.log'))
    assert files
    evts = list(_read_jsonl(files[0]))
    names = [e.get('event') for e in evts]
    assert 'provider_start' in names
    assert 'provider_done' in names
    # Correlation context is always present and should include a per-turn trace id.
    start_evt = [e for e in evts if e.get('event') == 'provider_start'][-1]
    ctx = start_evt.get('ctx') or {}
    assert isinstance(ctx.get('trace_id'), str) and len(ctx.get('trace_id')) > 0
    # provider_done should retain basic identifying fields for correlation.
    done_evt = [e for e in evts if e.get('event') == 'provider_done'][-1]
    data = done_evt.get('data') or {}
    assert data.get('model') == 'fake-model'
    assert data.get('provider') == 'FakeProvider'
