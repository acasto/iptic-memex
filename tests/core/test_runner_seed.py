import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session

from core.runner_seed import apply_chat_seed, build_chat_seed, build_runner_snapshot
import core.mode_runner as mode_runner


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_build_chat_seed_uses_full_history():
    sess = _make_session()
    chat = sess.add_context('chat')
    chat.add("one", role="user")
    chat.add("two", role="assistant")
    chat.add("three", role="user")

    # Even if context_sent is windowed, seed should reflect full history.
    sess.set_option('context_sent', 'last_1')

    seed = build_chat_seed(sess)
    assert len(seed) == 3
    assert seed[-1].get('message') == 'three'


def test_build_runner_snapshot_includes_chat_seed():
    sess = _make_session()
    chat = sess.add_context('chat')
    chat.add("alpha", role="user")
    chat.add("beta", role="assistant")

    snap = build_runner_snapshot(sess, overrides={'model': 'm1'})
    seed = snap.get('chat_seed') or []
    assert len(seed) == 2
    assert seed[0].get('message') == 'alpha'


def test_apply_chat_seed_sets_flag():
    sess = _make_session()
    apply_chat_seed(sess, [{"role": "user", "message": "seed"}])
    assert sess.get_flag("use_chat_seed_for_templates", False) is True
    assert isinstance(sess.get_user_data("__chat_seed__"), list)
