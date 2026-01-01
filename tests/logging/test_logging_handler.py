from __future__ import annotations

import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.logging_utils import LoggingHandler


class FakeConfig:
    def __init__(self, opts: dict):
        self._opts = opts

    def get_option(self, section: str, key: str, fallback=None):
        if section != 'LOG':
            return fallback
        return self._opts.get(key, fallback)


class CaptureOutput:
    def __init__(self):
        self.lines = []

    def write(self, message, **kwargs):
        self.lines.append(str(message))

    def debug(self, message, **kwargs):
        # Used by json writer mirror path
        self.lines.append(str(message))


def test_json_logging_redaction_and_truncation(tmp_path):
    log_dir = str(tmp_path)
    cfg = FakeConfig({
        'active': True,
        'dir': log_dir,
        'per_run': True,
        'format': 'json',
        'mirror_to_console': False,
        'redact': True,
        'redact_keys': 'api_key,authorization,token,password,secret,key',
        'truncate_chars': 10,
        'log_settings': 'basic',
    })
    logger = LoggingHandler(cfg, output_handler=None)

    assert logger.active() is True
    # Emit a settings event with sensitive and long data
    logger.settings({'api_key': 'shhhhh', 'note': 'x' * 50})

    # Find the created log file and parse the last JSON line
    files = list(tmp_path.glob('*.log'))
    assert files, 'No log files created'
    with files[0].open('r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines, 'Log file empty'
    payload = json.loads(lines[-1])
    data = payload.get('data') or {}
    # Redaction and truncation
    assert data.get('api_key') == '***redacted***'
    assert data.get('note', '').endswith('…')
    assert len(data.get('note')) == 11  # 10 chars + ellipsis


def test_text_logging_and_console_mirror(tmp_path):
    cap = CaptureOutput()
    cfg = FakeConfig({
        'active': True,
        'dir': str(tmp_path),
        'per_run': True,
        'format': 'text',
        'mirror_to_console': True,
        'log_settings': 'basic',
    })
    logger = LoggingHandler(cfg, output_handler=cap)
    logger.settings({'model': 'm1'})

    # One text line mirrored to console
    assert any('settings' in line and 'model=m1' in line for line in cap.lines)


def test_span_allows_explicit_parent_span_id(tmp_path):
    log_dir = str(tmp_path)
    cfg = FakeConfig(
        {
            "active": True,
            "dir": log_dir,
            "per_run": True,
            "format": "json",
            "log_settings": "basic",
        }
    )
    logger = LoggingHandler(cfg, output_handler=None)
    # Explicit parent_span_id should not cause duplicate kwarg errors.
    with logger.span("child", trace_id="t1", parent_span_id="p1"):
        logger.settings({"k": "v"})

    files = list(tmp_path.glob("*.log"))
    assert files
    with files[0].open("r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines
    payload = json.loads(lines[-1])
    ctx = payload.get("ctx") or {}
    assert ctx.get("trace_id") == "t1"
    assert ctx.get("parent_span_id") == "p1"
