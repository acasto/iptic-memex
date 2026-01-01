from __future__ import annotations

import os
import sys
import textwrap

import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import importlib
import pytest

from config_manager import ConfigManager
from core.session_builder import SessionBuilder


class FakeLoggingHandlerInactive:
    def __init__(self, config, output_handler=None):
        pass
    def active(self) -> bool:
        return False


def test_warn_when_logging_enabled_but_inactive(tmp_path, capsys, monkeypatch):
    # Create a minimal config overriding DEFAULT and enabling logging
    cfg_text = textwrap.dedent(
        f"""
        [DEFAULT]
        default_model = invalid_model_for_test

        [LOG]
        active = true
        dir = {tmp_path}
        format = json
        file = memex.log
        rotation = size
        max_bytes = 1000000
        backup_count = 5
        """
    ).strip()
    cfg_path = tmp_path / 'cfg.ini'
    cfg_path.write_text(cfg_text, encoding='utf-8')

    cm = ConfigManager(str(cfg_path))
    sb = SessionBuilder(cm)

    # Monkeypatch the logger class used by core.utils to simulate failure
    import core.utils as core_utils
    monkeypatch.setattr(core_utils, 'LoggingHandler', FakeLoggingHandlerInactive)

    # Build a session with an invalid model so no provider is created
    _ = sb.build(mode='chat', model='invalid_model_for_test')

    out = capsys.readouterr().out
    assert 'Logging is enabled but the log file could not be opened' in out or 'failed to initialize' in out
