import pytest

from config_manager import ConfigManager
from session import SessionBuilder
from modes.completion_mode import CompletionMode


def test_completion_disables_tools_and_mcp():
    cfg = ConfigManager()
    builder = SessionBuilder(cfg)
    sess = builder.build(mode='completion')

    # Instantiate completion to apply one-shot settings
    CompletionMode(sess)

    # Tools are disabled for one-shot completion
    assert sess.get_effective_tool_mode() == 'none'

    # MCP should not autoload in completion
    assert sess.get_user_data('__mcp_client__') is None
    assert sess.get_user_data('__mcp_autoload__') is None

