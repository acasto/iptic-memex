from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from io import StringIO
from utils.output_utils import OutputHandler, OutputLevel
from config_manager import SessionConfig, ConfigManager


class DummyConfig(SessionConfig):
    def __init__(self):
        # Build a minimal SessionConfig via ConfigManager to satisfy OutputHandler
        cm = ConfigManager()
        # Provide a minimal base_config with defaults
        self.base_config = cm.base_config
        self.models = cm.models
        self.overrides = {}

    def get_option(self, section: str, option: str, fallback=None):
        # Default: colors off to avoid ANSI in tests; INFO level
        if section == 'DEFAULT' and option == 'colors':
            return False
        if section == 'DEFAULT' and option == 'output_level':
            return 'INFO'
        return fallback


def test_suppress_below_drops_info_and_keeps_warning():
    cfg = DummyConfig()
    out = OutputHandler(cfg)
    buf = StringIO()
    out.set_stream(buf)

    out.write('A', level=OutputLevel.INFO)
    out.write('B', level=OutputLevel.WARNING)
    assert 'A' in buf.getvalue() and 'B' in buf.getvalue()

    buf.truncate(0); buf.seek(0)
    with out.suppress_below(OutputLevel.WARNING):
        out.write('X', level=OutputLevel.INFO)
        out.write('Y', level=OutputLevel.DEBUG)
        out.write('Z', level=OutputLevel.WARNING)
    s = buf.getvalue()
    assert 'Z' in s and 'X' not in s and 'Y' not in s

