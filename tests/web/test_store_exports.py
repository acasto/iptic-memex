from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_store_exports_present():
    # This is a light-weight check that our front-end store API surface remains
    # intact (getState/setState/subscribe/addMessage/appendMessage).
    store_path = os.path.join(ROOT, 'web', 'static', 'js', 'store.js')
    with open(store_path, 'r', encoding='utf-8') as f:
        src = f.read()
    for name in ['getState', 'setState', 'subscribe', 'addMessage', 'appendMessage', 'clearMessages']:
        assert (f'export function {name}' in src) or (f'export {{ {name} }}' in src), f"missing export: {name}"

    # Ensure state keys exist (messages, stream, pendingInteraction, updates, status)
    for key in ['messages', 'stream', 'pendingInteraction', 'updates', 'status']:
        assert key in src, f"expected key in initial state: {key}"
