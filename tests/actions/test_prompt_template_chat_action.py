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

    out_last2 = handler.run("X {{chat:last=2}} Y")
    assert "How are you?" in out_last2
    assert "Hello" in out_last2

    # Legacy underscore syntax still works
    out_legacy = handler.run("X {{chat:last_2}} Y")
    assert "How are you?" in out_legacy
    assert "Hello" in out_legacy


def test_prompt_template_chat_role_and_limits():
    sess = _make_session()
    chat = sess.add_context('chat')
    chat.add("u1", role="user")
    chat.add("a1", role="assistant")
    chat.add("u2", role="user")
    chat.add("a2", role="assistant")

    handler = PromptTemplateChatAction(sess)

    # only=user should drop assistant turns
    out_only_user = handler.run("{{chat:last_4;only=user}}")
    assert "User: u1" in out_only_user
    assert "User: u2" in out_only_user
    assert "Assistant" not in out_only_user

    # max_chars should truncate and include ellipsis marker
    out_trunc = handler.run("{{chat:last_4;max_chars=10}}")
    assert "… (truncated" in out_trunc

    # max_tokens (very small) should truncate to a short prefix
    out_tok = handler.run("{{chat:last_4;max_tokens=2}}")
    assert len(out_tok.split()) <= 2


def test_prompt_template_chat_uses_seed_when_flag_set():
    sess = _make_session()
    chat = sess.add_context('chat')
    chat.add("local", role="user")

    sess.set_user_data("__chat_seed__", [
        {"role": "user", "message": "seed1"},
        {"role": "assistant", "message": "seed2"},
    ])
    sess.set_flag("use_chat_seed_for_templates", True)

    handler = PromptTemplateChatAction(sess)
    out_last = handler.run("{{chat:last}}")
    assert "seed2" in out_last
    assert "local" not in out_last
