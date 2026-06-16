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


class CapturingAgentMode:
    last_kwargs = None

    def __init__(self, session, **kwargs):
        self.session = session
        CapturingAgentMode.last_kwargs = kwargs

    def start(self):
        pass


def test_agent_command_uses_configured_write_policy(monkeypatch):
    import modes.agent_mode as agent_mode

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(agent_mode, "AgentMode", CapturingAgentMode)
    CapturingAgentMode.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["agent"])

    assert result.exit_code == 0
    assert CapturingAgentMode.last_kwargs is not None
    assert CapturingAgentMode.last_kwargs["writes_policy"] == "dry-run"


def test_agent_command_write_policy_cli_override_wins(monkeypatch):
    import modes.agent_mode as agent_mode

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(agent_mode, "AgentMode", CapturingAgentMode)
    CapturingAgentMode.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["--agent-writes", "allow", "agent"])

    assert result.exit_code == 0
    assert CapturingAgentMode.last_kwargs is not None
    assert CapturingAgentMode.last_kwargs["writes_policy"] == "allow"


def test_agent_command_no_status_tags_flag_reaches_agent_mode(monkeypatch):
    import modes.agent_mode as agent_mode

    monkeypatch.setattr(main, "ConfigManager", FakeConfigManager)
    monkeypatch.setattr(main, "SessionBuilder", FakeSessionBuilder)
    monkeypatch.setattr(agent_mode, "AgentMode", CapturingAgentMode)
    CapturingAgentMode.last_kwargs = None

    result = CliRunner().invoke(main.cli, ["--no-agent-status-tags", "agent"])

    assert result.exit_code == 0
    assert CapturingAgentMode.last_kwargs is not None
    assert CapturingAgentMode.last_kwargs["use_status_tags"] is False
