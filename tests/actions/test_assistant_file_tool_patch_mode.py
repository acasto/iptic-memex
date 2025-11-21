from __future__ import annotations

import os
import tempfile


class DummyFsHandler:
    def __init__(self, base: str):
        self.base = base

    def resolve_path(self, p: str, must_exist: bool = True):
        if not os.path.isabs(p):
            p = os.path.join(self.base, p)
        return p if (not must_exist or os.path.exists(p)) else None

    def read_file(self, p: str, binary: bool = False, encoding: str = 'utf-8'):
        rp = self.resolve_path(p)
        if rp is None:
            return None
        mode = 'rb' if binary else 'r'
        with open(rp, mode, encoding=None if binary else encoding) as f:
            return f.read()

    def write_file(self, p: str, content: str, create_dirs: bool = False, append: bool = False, force: bool = False):
        rp = self.resolve_path(p, must_exist=False)
        if rp is None:
            return False
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        mode = 'a' if append else 'w'
        with open(rp, mode, encoding='utf-8') as f:
            f.write(content)
        return True


class DummyUtils:
    class Out:
        def write(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def stop_spinner(self, *a, **k):
            pass

    def __init__(self):
        self.output = DummyUtils.Out()
        self.fs = self

    # Fallback read/write used nowhere in these tests
    def read_file(self, *a, **k):
        return None

    def write_file(self, *a, **k):
        return False


class DummyUI:
    class Caps:
        blocking = True

    def __init__(self):
        self.capabilities = DummyUI.Caps()

    def emit(self, *a, **k):
        pass

    def ask_bool(self, *a, **k):
        # For these tests we disable confirmation at the tool level,
        # so this should not be called.
        raise AssertionError("ask_bool should not be called in patch tests")


class DummySession:
    def __init__(self, base: str):
        self._base = base
        self._added = []
        self._tools = {'write_confirm': False}
        self.utils = DummyUtils()

    def get_action(self, name: str):
        if name == 'assistant_fs_handler':
            return DummyFsHandler(self._base)
        if name == 'count_tokens':
            return None
        return None

    def add_context(self, kind: str, data=None):
        self._added.append((kind, data))
        return data

    def get_agent_write_policy(self):
        return None

    def get_tools(self):
        return self._tools

    def get_params(self):
        return {'model': None}

    def get_option_from_model(self, *a, **k):
        return None

    @property
    def ui(self):
        return DummyUI()

    # Minimal stubs used by assistant_file_tool_action
    def get_context(self, *a, **k):
        return None

    def get_contexts(self, *a, **k):
        return []

    def get_user_data(self, *a, **k):
        return None

    def set_user_data(self, *a, **k):
        pass


def _make_file(tmpdir: str, name: str, content: str) -> str:
    path = os.path.join(tmpdir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_patch_single_block_replaces_unique_match():
    with tempfile.TemporaryDirectory() as td:
        path = _make_file(td, "file.txt", "line1\nline2\nline3\n")
        from actions.assistant_file_tool_action import AssistantFileToolAction

        sess = DummySession(td)
        act = AssistantFileToolAction(sess)
        patch_content = "<<<<\nline2\n====\nLINE_TWO\n>>>>"

        res = act.run({'mode': 'patch', 'file': path}, patch_content)
        assert res.payload.get('ok') is True

        new_text = _read(path)
        assert new_text == "line1\nLINE_TWO\nline3\n"


def test_patch_multi_block_applies_in_sequence():
    with tempfile.TemporaryDirectory() as td:
        path = _make_file(td, "file.txt", "a\nb\nc\n")
        from actions.assistant_file_tool_action import AssistantFileToolAction

        sess = DummySession(td)
        act = AssistantFileToolAction(sess)
        patch_content = (
            "<<<<\n"
            "a\n"
            "====\n"
            "A\n"
            ">>>>"
            "<<<<\n"
            "c\n"
            "====\n"
            "C\n"
            ">>>>"
        )

        res = act.run({'mode': 'patch', 'file': path}, patch_content)
        assert res.payload.get('ok') is True
        new_text = _read(path)
        assert new_text == "A\nb\nC\n"


def test_patch_fails_when_search_not_found():
    with tempfile.TemporaryDirectory() as td:
        path = _make_file(td, "file.txt", "only\nthis\ntext\n")
        from actions.assistant_file_tool_action import AssistantFileToolAction

        sess = DummySession(td)
        act = AssistantFileToolAction(sess)
        patch_content = "<<<<\nmissing\n====\nX\n>>>>"

        res = act.run({'mode': 'patch', 'file': path}, patch_content)
        # Operation itself is reported ok=False via file_tool_error context
        # The file content must be unchanged.
        assert _read(path) == "only\nthis\ntext\n"
