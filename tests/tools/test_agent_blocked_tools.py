from config_manager import ConfigManager
from session import SessionBuilder
from actions.assistant_commands_action import AssistantCommandsAction


def test_noninteractive_blocked_tools_enforced():
    cfg = ConfigManager()
    builder = SessionBuilder(cfg)
    sess = builder.build(mode='internal')

    # Enter agent (non-interactive) and set policy: allow file,cmd but block cmd
    sess.enter_agent_mode('deny')
    sess.config.set_option('active_tools', 'file,cmd')  # [AGENT].active_tools via overrides
    sess.config.set_option('blocked_tools', 'cmd')      # [AGENT].blocked_tools via overrides

    ac = AssistantCommandsAction(sess)
    names = set((ac.commands or {}).keys())

    # 'file' should be present, 'cmd' must be blocked
    assert 'file' in names
    assert 'cmd' not in names

