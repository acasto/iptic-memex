import os
import sys
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions
from contexts.chat_context import ChatContext


class DummyChatContext:
    def __init__(self):
        self.conversation = []

    def add(self, message, role="user", context=None, extra=None):
        turn = {"role": role, "message": message}
        if context is not None:
            turn["context"] = context
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in ("role", "message", "context"):
                    turn[k] = v
        self.conversation.append(turn)

    def get(self, args=None):
        if args == "all":
            return list(self.conversation)
        return list(self.conversation)


class DummySessionForTurnPrompt:
    def __init__(self):
        from types import SimpleNamespace

        self._contexts = {}
        self._actions = {}
        self.user_data = {}
        self.flags = {}
        self._params = {"model": "dummy", "provider": "DummyProvider"}
        self.utils = SimpleNamespace(output=SimpleNamespace(write=lambda *a, **k: None))

    # Contexts API
    def get_context(self, kind):
        return self._contexts.get(kind)

    def add_context(self, kind, data=None):
        if kind == "chat":
            ctx = self._contexts.get("chat") or ChatContext(self, None)
            self._contexts["chat"] = ctx
            return ctx
        # Simple context wrapper with get()
        class Ctx:
            def __init__(self, payload):
                self._payload = payload or {}

            def get(self):
                return self._payload

        ctx = Ctx(data)
        self._contexts[kind] = ctx
        return ctx

    def remove_context_type(self, name):
        self._contexts.pop(name, None)

    # Actions and providers
    def get_action(self, name):
        return self._actions.get(name)

    def register_action(self, name, action):
        self._actions[name] = action

    def get_provider(self):
        return None

    # Config/params helpers
    @property
    def params(self):
        return dict(self._params)

    def get_params(self):
        return dict(self._params)

    def get_option(self, section, option, fallback=None):
        if section == "DEFAULT" and option == "template_handler":
            return "prompt_template"
        if section == "DEFAULT" and option == "turn_prompt":
            return None
        if section == "TOOLS" and option == "allow_auto_submit":
            return False
        if section == "TOOLS" and option == "auto_submit_max_turns":
            return 1
        return fallback

    def get_option_from_model(self, option, model=None):
        return None

    def get_option_from_provider(self, option, provider=None):
        return None

    # Flags/user data
    def set_flag(self, name, value):
        self.flags[name] = value

    def get_flag(self, name, default=False):
        return self.flags.get(name, default)

    def set_user_data(self, key, value):
        self.user_data[key] = value

    def get_user_data(self, key, default=None):
        return self.user_data.get(key, default)


class DummyBuildTurnPromptAction:
    """Minimal fake to test that TurnRunner wires meta into the template."""

    def __init__(self, session):
        self.session = session

    def run(self, meta=None):
        # Echo back a deterministic status line using the provided meta.
        if not isinstance(meta, dict):
            return ""
        mid = meta.get("id", "unknown")
        kind = meta.get("kind", "unknown")
        return f"[turn_status id={mid} kind={kind}]"


def test_run_user_turn_attaches_meta_and_turn_prompt():
    sess = DummySessionForTurnPrompt()
    # Register the build_turn_prompt action
    sess.register_action("build_turn_prompt", DummyBuildTurnPromptAction(sess))

    runner = TurnRunner(sess)
    res = runner.run_user_turn("hello", options=TurnOptions(stream=False))

    # One assistant turn may or may not exist depending on provider; we care
    # about the chat history for the user turn.
    chat_ctx = sess.get_context("chat")
    assert chat_ctx is not None
    history = chat_ctx.get("all")
    assert history
    user_turn = history[0]
    assert user_turn["role"] == "user"
    meta = user_turn.get("meta")
    assert isinstance(meta, dict)
    # Stable id/index present
    assert "id" in meta
    assert "index" in meta
    # Our dummy action should have recorded a turn_prompt_text
    assert "turn_prompt_text" in meta
    assert meta["turn_prompt_text"].startswith("[turn_status id=")
