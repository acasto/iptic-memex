from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.assistant_persona_review_tool_action import AssistantPersonaReviewToolAction


class FsMock:
    def __init__(self, existing_paths: set[str] | None = None, files: dict[str, str] | None = None):
        self._existing = existing_paths or set()
        self._files = files or {}
        self.resolve_calls: list[tuple[str, bool]] = []
        self.read_calls: list[str] = []

    def resolve_path(self, path: str, must_exist: bool = True):
        self.resolve_calls.append((path, must_exist))
        if must_exist and path not in self._existing:
            return None
        return path

    def read_file(self, path: str, binary=False, encoding='utf-8'):
        self.read_calls.append(path)
        return self._files.get(path)


class SessionMock:
    def __init__(self, fs_action):
        self._fs_action = fs_action

    def get_action(self, name):
        if name == 'assistant_fs_handler':
            return self._fs_action
        return None


def test_collect_personas_from_names_list_no_file_io():
    fs = FsMock()
    sess = SessionMock(fs)
    act = AssistantPersonaReviewToolAction(sess)

    personas = act._collect_personas(['Developer', 'Product Manager'])
    assert [p['name'] for p in personas] == ['Developer', 'Product Manager']
    # No file resolution/read attempted for multi-item
    assert fs.resolve_calls == []
    assert fs.read_calls == []


def test_collect_personas_from_single_file_when_exists():
    content = """
## Personas

### Developer
Builds stuff.

### Product Manager
Writes specs.
""".strip()
    fs = FsMock(existing_paths={'personas.md'}, files={'personas.md': content})
    sess = SessionMock(fs)
    act = AssistantPersonaReviewToolAction(sess)

    personas = act._collect_personas(['personas.md'])
    names = [p['name'] for p in personas]
    assert 'Developer' in names and 'Product Manager' in names
    # Ensure we resolved and read the file
    assert ('personas.md', True) in fs.resolve_calls
    assert 'personas.md' in fs.read_calls


def test_collect_personas_from_single_nonexistent_treated_as_name():
    fs = FsMock(existing_paths=set())
    sess = SessionMock(fs)
    act = AssistantPersonaReviewToolAction(sess)

    personas = act._collect_personas(['NotAFile'])
    assert [p['name'] for p in personas] == ['NotAFile']
    # Resolve attempted once, but no read
    assert ('NotAFile', True) in fs.resolve_calls
    assert fs.read_calls == []


def test_collect_personas_multi_item_with_path_like_values_treated_as_names():
    fs = FsMock(existing_paths={'personas.md'})
    sess = SessionMock(fs)
    act = AssistantPersonaReviewToolAction(sess)

    personas = act._collect_personas(['personas.md', 'Developer'])
    assert [p['name'] for p in personas] == ['personas.md', 'Developer']
    # No resolve/read in multi-item case
    assert fs.resolve_calls == []
    assert fs.read_calls == []

