from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.stream_utils import StreamHandler


class Cfg:
    def get_option(self, section, key, fallback=None):
        return fallback


class DummyOutput:
    def write(self, *a, **k):
        pass

    def spinner(self, *a, **k):
        from contextlib import nullcontext
        return nullcontext()


def test_stream_handler_cooperative_cancel_stops_early_and_calls_on_cancel():
    cfg = Cfg()
    out = DummyOutput()
    sh = StreamHandler(cfg, out)

    produced = []
    cancelled = {'v': False}

    def gen():
        yield 'Hello '
        yield 'World'

    def on_token(t):
        produced.append(t)

    def on_cancel():
        cancelled['v'] = True

    # Cancel before processing the next token after the first
    def cancel_check():
        return True

    acc = sh.process_stream(gen(), on_token=on_token, on_complete=lambda _: None, cancel_check=cancel_check, on_cancel=on_cancel)
    # Should have captured only the first token and set cancelled flag
    assert ''.join(produced) == 'Hello '
    assert cancelled['v'] is True
    # Accumulated text should also reflect partial output
    assert acc.startswith('Hello') and not acc.endswith('World')
