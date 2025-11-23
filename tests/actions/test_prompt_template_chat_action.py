import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.prompt_template_chat_action import PromptTemplateChatAction
from contexts.chat_context import ChatContext
from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_prompt_template_chat_renders_last_and_last_n():
    sess = _make_session()
    chat = sess.add_context('chat')
    # Populate conversation
    chat.add("Hi", role="user")
    chat.add("Hello", role="assistant")
    chat.add("How are you?", role="user")

    handler = PromptTemplateChatAction(sess)

    out_last = handler.run("X {{chat:last}} Y")
    assert "How are you?" in out_last
    assert "Hi" not in out_last

    out_last2 = handler.run("X {{chat:last_2}} Y")
    assert "How are you?" in out_last2
    assert "Hello" in out_last2
