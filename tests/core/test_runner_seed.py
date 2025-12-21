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


def test_internal_runner_uses_chat_seed_not_live_chat(monkeypatch):
    outer = _make_session()
    chat = outer.add_context('chat')
    chat.add("outer1", role="user")
    chat.add("outer2", role="assistant")

    inner = _make_session()

    # Force run_agent to use our inner session without provider setup.
    monkeypatch.setattr(mode_runner, "_build_subsession", lambda builder, overrides=None: inner)

    # Avoid external side effects.
    monkeypatch.setattr(mode_runner, "compute_context_tokens", lambda *a, **k: 0)
    monkeypatch.setattr(mode_runner, "check_noninteractive_gate", lambda *a, **k: {"ok": True})

    try:
        import memex_mcp.bootstrap as mcp_boot
        monkeypatch.setattr(mcp_boot, "autoload_mcp", lambda *a, **k: None)
    except Exception:
        pass

    seen = {}
    import core.context_transfer as ct

    def fake_copy(src, dest, *, types=None, include_chat=False):
        seen["include_chat"] = include_chat

    monkeypatch.setattr(ct, "copy_contexts", fake_copy)

    class _FakeRunner:
        def __init__(self, sess):
            self.sess = sess

        def run_agent_loop(self, *a, **k):
            class _R:
                last_text = "ok"
                turns_executed = 1
            return _R()

    monkeypatch.setattr(mode_runner, "TurnRunner", _FakeRunner)

    res = mode_runner.run_agent(builder=None, steps=1, outer_session=outer)
    assert res.last_text == "ok"
    assert seen.get("include_chat") is False

    seed = inner.get_user_data("__chat_seed__")
    assert isinstance(seed, list) and len(seed) == 2
    assert seed[0].get("message") == "outer1"

    inner_chat = inner.get_context("chat")
    assert inner_chat is not None
    assert inner_chat.get("all") == []
