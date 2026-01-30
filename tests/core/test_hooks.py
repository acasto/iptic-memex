import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions
from core.hooks import run_hooks
from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_run_hooks_no_config_is_noop():
    sess = _make_session()
    # Should not raise even when no [HOOKS] section exists
    run_hooks(sess, phase="pre_turn", extras={"input_text": "hello"})


def test_pre_and_post_hooks_inject_assistant_context(monkeypatch):
    sess = _make_session()
    # Configure a simple hook via overrides
    sess.config.set_option('pre_turn', 'test_hook')
    # Ensure the hook is enabled via base config (default) and has a section
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'true')
    sess.config.base_config.set('HOOK.test_hook', 'when_message_contains', '')
    # For HOOK.test_hook, we only need a prompt; reuse a small literal via PROMPTS
    # Using a literal prompt path ensures PromptResolver can resolve it.
    sess.config.set_option('prompt', 'default.txt')

    # Monkeypatch run_internal_agent so we don't require a real provider
    calls = {}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls['steps'] = steps
        calls['overrides'] = overrides or {}
        calls['contexts'] = contexts
        # Pretend the internal run produced a short summary
        class _R:
            last_text = "HOOK_SUMMARY"
        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    res = runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))
    assert res.last_text is not None

    # The hook should have injected an assistant context with the summary text
    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    injected = False
    for t in turns:
        ctx_items = t.get('context') or []
        for c in ctx_items:
            ctx_obj = c.get('context')
            data = ctx_obj.get() if ctx_obj else None
            if isinstance(data, dict):
                if 'HOOK_SUMMARY' in (data.get('content') or ''):
                    injected = True
    assert injected


def test_disabled_hook_is_skipped(monkeypatch):
    sess = _make_session()
    # List the hook in [HOOKS].pre_turn
    sess.config.set_option('pre_turn', 'test_hook')
    # Ensure no post_turn hooks run (isolate this test from user config)
    sess.config.set_option('post_turn', '')
    # Mark HOOK.test_hook as disabled in base config
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'false')

    calls = {'count': 0}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls['count'] += 1
        class _R:
            last_text = "SHOULD_NOT_RUN"
        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))
    assert calls['count'] == 0


def test_hook_label_and_prefix_are_applied(monkeypatch):
    sess = _make_session()
    sess.config.set_option('pre_turn', 'test_hook')
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'true')
    sess.config.base_config.set('HOOK.test_hook', 'label', 'memory_items')
    sess.config.base_config.set('HOOK.test_hook', 'prefix', 'Pref: ')
    sess.config.base_config.set('HOOK.test_hook', 'when_message_contains', '')
    sess.config.set_option('prompt', 'default.txt')

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        class _R:
            last_text = "BODY"
        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))

    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    found = False
    for t in turns:
        ctx_items = t.get('context') or []
        for c in ctx_items:
            ctx_obj = c.get('context')
            data = ctx_obj.get() if ctx_obj else None
            if isinstance(data, dict) and data.get('name') == 'memory_items':
                if data.get('content') == 'Pref:\nBODY':
                    found = True
    assert found


def test_hook_runs_every_n_user_turns(monkeypatch):
    sess = _make_session()
    sess.config.set_option('pre_turn', 'test_hook')
    sess.config.set_option('post_turn', '')
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'true')
    sess.config.base_config.set('HOOK.test_hook', 'when_every_n_turns', '2')
    sess.config.base_config.set('HOOK.test_hook', 'when_message_contains', '')
    sess.config.set_option('prompt', 'default.txt')

    calls = {'count': 0}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls['count'] += 1
        class _R:
            last_text = "RUN"
        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("first", options=TurnOptions(stream=False, suppress_context_print=True))
    runner.run_user_turn("second", options=TurnOptions(stream=False, suppress_context_print=True))

    assert calls['count'] == 1  # should only run on every 2nd user turn


def test_hook_message_contains_filters(monkeypatch):
    sess = _make_session()
    sess.config.set_option('pre_turn', 'test_hook')
    sess.config.set_option('post_turn', '')
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'true')
    sess.config.base_config.set('HOOK.test_hook', 'when_message_contains', 'magic,xyz')
    sess.config.set_option('prompt', 'default.txt')

    calls = {'count': 0}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls['count'] += 1
        class _R:
            last_text = "RUN"
        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello there", options=TurnOptions(stream=False, suppress_context_print=True))
    runner.run_user_turn("contains magic word", options=TurnOptions(stream=False, suppress_context_print=True))

    assert calls['count'] == 1  # only second message matches


def test_hook_external_runner_injects(monkeypatch):
    sess = _make_session()
    sess.config.set_option('pre_turn', 'test_hook')
    sess.config.set_option('post_turn', '')
    if not sess.config.base_config.has_section('HOOK.test_hook'):
        sess.config.base_config.add_section('HOOK.test_hook')
    sess.config.base_config.set('HOOK.test_hook', 'enable', 'true')
    sess.config.base_config.set('HOOK.test_hook', 'runner', 'external')
    sess.config.base_config.set('HOOK.test_hook', 'when_message_contains', '')
    sess.config.set_option('prompt', 'default.txt')

    def fake_run_external_agent(steps, overrides=None, contexts=None, cmd=None, timeout=None):
        class _R:
            last_text = "EXTERNAL"
        return _R()

    sess.run_external_agent = fake_run_external_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))

    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    injected = False
    for t in turns:
        ctx_items = t.get('context') or []
        for c in ctx_items:
            ctx_obj = c.get('context')
            data = ctx_obj.get() if ctx_obj else None
            if isinstance(data, dict) and 'EXTERNAL' in (data.get('content') or ''):
                injected = True
    assert injected


def test_hook_model_is_not_shadowed_by_session_model_override(monkeypatch):
    sess = _make_session()
    # Outer session is "currently" on kimi (or any other), but hook explicitly sets a model.
    sess.config.set_option("model", "kimi")
    sess.config.set_option("pre_turn", "test_hook")
    sess.config.set_option("post_turn", "")
    if not sess.config.base_config.has_section("HOOK.test_hook"):
        sess.config.base_config.add_section("HOOK.test_hook")
    sess.config.base_config.set("HOOK.test_hook", "enable", "true")
    sess.config.base_config.set("HOOK.test_hook", "model", "gpt-5-mini")
    sess.config.base_config.set("HOOK.test_hook", "when_message_contains", "")

    calls = {}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls["overrides"] = overrides or {}

        class _R:
            last_text = "OK"

        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))

    assert calls.get("overrides", {}).get("model") == "gpt-5-mini"


def test_session_start_hook_runs_once(monkeypatch):
    sess = _make_session()
    sess.config.set_option("session_start", "test_hook")
    sess.config.set_option("pre_turn", "")
    sess.config.set_option("post_turn", "")
    if not sess.config.base_config.has_section("HOOK.test_hook"):
        sess.config.base_config.add_section("HOOK.test_hook")
    sess.config.base_config.set("HOOK.test_hook", "enable", "true")
    sess.config.base_config.set("HOOK.test_hook", "when_message_contains", "")

    calls = {"count": 0}

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        calls["count"] += 1

        class _R:
            last_text = "SESSION_START"

        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("first", options=TurnOptions(stream=False, suppress_context_print=True))
    runner.run_user_turn("second", options=TurnOptions(stream=False, suppress_context_print=True))

    assert calls["count"] == 1

    chat = sess.get_context("chat")
    turns = chat.get("all") if chat else []
    found = False
    for t in turns:
        if str(t.get("role", "")).lower() != "user":
            continue
        if (t.get("message") or "") != "first":
            continue
        ctx_items = t.get("context") or []
        for c in ctx_items:
            ctx_obj = c.get("context")
            data = ctx_obj.get() if ctx_obj else None
            if isinstance(data, dict) and "SESSION_START" in (data.get("content") or ""):
                found = True
    assert found


def test_session_end_hook_persists_assistant_context(monkeypatch):
    sess = _make_session()
    sess.config.set_option("session_start", "")
    sess.config.set_option("pre_turn", "")
    sess.config.set_option("post_turn", "")
    sess.config.set_option("session_end", "end_hook")
    if not sess.config.base_config.has_section("HOOK.end_hook"):
        sess.config.base_config.add_section("HOOK.end_hook")
    sess.config.base_config.set("HOOK.end_hook", "enable", "true")
    sess.config.base_config.set("HOOK.end_hook", "when_message_contains", "")

    def fake_run_internal_agent(steps, overrides=None, contexts=None, output=None, verbose_dump=False):
        class _R:
            last_text = "SESSION_END"

        return _R()

    sess.run_internal_agent = fake_run_internal_agent  # type: ignore[assignment]

    runner = TurnRunner(sess)
    runner.run_user_turn("hello", options=TurnOptions(stream=False, suppress_context_print=True))

    sess.handle_exit(confirm=False)

    items = (sess.context or {}).get("assistant") or []
    assert any(
        isinstance(ctx.get(), dict)
        and "SESSION_END" in (ctx.get().get("content") or "")
        for ctx in items
    )
