from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.tui import TUIUI
from base_classes import InteractionNeeded


class DummySession:
    def __init__(self):
        self.ui = None


def test_tui_ask_text_raises_interaction_needed():
    sess = DummySession()
    ui = TUIUI(sess)
    with pytest.raises(InteractionNeeded) as ei:
        ui.ask_text("Enter:")
    e = ei.value
    assert e.kind == "text"
    assert e.spec.get("prompt") == "Enter:"


def test_tui_ask_bool_and_choice_and_files():
    sess = DummySession()
    ui = TUIUI(sess)
    with pytest.raises(InteractionNeeded) as e1:
        ui.ask_bool("Continue?", default=True)
    assert e1.value.kind == "bool"
    assert e1.value.spec.get("default") is True

    with pytest.raises(InteractionNeeded) as e2:
        ui.ask_choice("Pick", ["a", "b"], default="a")
    assert e2.value.kind == "choice"
    assert e2.value.spec.get("options") == ["a", "b"]

    with pytest.raises(InteractionNeeded) as e3:
        ui.ask_files("Files", accept=[".txt"], multiple=False)
    assert e3.value.kind == "files"
    assert e3.value.spec.get("accept") == [".txt"]


def test_tui_emit_uses_handler():
    sess = DummySession()
    ui = TUIUI(sess)
    captured = []

    def handler(kind, data):
        captured.append((kind, data))

    ui.set_event_handler(handler)
    ui.emit('status', {'message': 'hello'})
    assert captured == [('status', {'message': 'hello'})]
