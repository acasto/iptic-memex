from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from click.testing import CliRunner

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import cli


def test_logs_show_filters_by_trace_id(tmp_path: Path):
    # Write a minimal config that points logging at tmp_path.
    cfg_path = tmp_path / "cfg.ini"
    cfg_path.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "default_model = invalid_model_for_test",
                "",
                "[LOG]",
                f"dir = {tmp_path}",
                "file = memex.log",
                "format = json",
                "active = false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    log_path = tmp_path / "memex.log"
    lines = [
        {
            "ts": "2026-01-01T00:00:00.000000Z",
            "run_id": "r1",
            "event": "provider_start",
            "component": "core.turns",
            "aspect": "provider",
            "severity": "info",
            "ctx": {"trace_id": "t1"},
            "data": {"model": "m1"},
        },
        {
            "ts": "2026-01-01T00:00:01.000000Z",
            "run_id": "r1",
            "event": "provider_done",
            "component": "core.turns",
            "aspect": "provider",
            "severity": "info",
            "ctx": {"trace_id": "t2"},
            "data": {"model": "m2"},
        },
    ]
    log_path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["-c", str(cfg_path), "logs", "show", "--trace", "t1", "--json"])
    assert res.exit_code == 0
    out_lines = [l for l in res.output.splitlines() if l.strip()]
    assert len(out_lines) == 1
    assert json.loads(out_lines[0]).get("ctx", {}).get("trace_id") == "t1"

