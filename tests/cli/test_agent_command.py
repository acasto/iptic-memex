from __future__ import annotations

import configparser
import os
import sys

from click.testing import CliRunner

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main


class FakeConfigManager:
    def __init__(self, _conf=None):
        cfg = configparser.ConfigParser()
        cfg["DEFAULT"] = {}
        cfg.add_section("AGENT")
        cfg.set("AGENT", "default_steps", "1")
        cfg.set("AGENT", "writes_policy", "dry-run")
        self.base_config = cfg


class FakeSession:
    def set_flag(self, *_args, **_kwargs):
        pass

    def add_context(self, *_args, **_kwargs):
        pass


class FakeSessionBuilder:
    def __init__(self, config_manager):
        self.config_manager = config_manager

    def build(self, mode=None, **options):
        self.mode = mode
        self.options = dict(options)
        return FakeSession()


class CapturingAgentRun:
    last_kwargs = None

    @staticmethod
    def run(**kwargs):
        from core.runtime import RunOutcome
        CapturingAgentRun.last_kwargs = kwargs
        return RunOutcome(run_id='fixture', last_text='Answer')


def test_agent_command_uses_configured_write_policy(monkeypatch):
    import app.runs as runs

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(runs, "run_agent", CapturingAgentRun.run)
    CapturingAgentRun.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["agent"])

    assert result.exit_code == 0
    assert CapturingAgentRun.last_kwargs is not None
    assert CapturingAgentRun.last_kwargs["writes_policy"] == "dry-run"


def test_agent_command_write_policy_cli_override_wins(monkeypatch):
    import app.runs as runs

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(runs, "run_agent", CapturingAgentRun.run)
    CapturingAgentRun.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["--agent-writes", "allow", "agent"])

    assert result.exit_code == 0
    assert CapturingAgentRun.last_kwargs is not None
    assert CapturingAgentRun.last_kwargs["writes_policy"] == "allow"


def test_agent_command_no_status_tags_flag_reaches_agent_mode(monkeypatch):
    import app.runs as runs

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(runs, "run_agent", CapturingAgentRun.run)
    CapturingAgentRun.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["--no-agent-status-tags", "agent"])

    assert result.exit_code == 0
    assert CapturingAgentRun.last_kwargs is not None
    assert CapturingAgentRun.last_kwargs["status_tags"] is False
