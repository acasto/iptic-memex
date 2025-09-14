from __future__ import annotations

import os
import io
import tempfile

from typing import Any, Dict


class DummyTC:
    def set_session(self, s): pass
    def run(self, *a, **k): pass


class DummyFS:
    def delete_file(self, p: str) -> bool:
        try:
            os.remove(p)
            return True
        except Exception:
            return False


class DummyOutput:
    def write(self, *a, **k): pass
    def error(self, *a, **k): pass


class DummyUtils:
    def __init__(self):
        self.tab_completion = DummyTC()
        self.fs = DummyFS()
        self.output = DummyOutput()


class FileCtx:
    def __init__(self, path: str):
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.file: Dict[str, Any] = {'name': path, 'content': content}
    def get(self):
        return self.file


class FakeSession:
    def __init__(self):
        self.utils = DummyUtils()
        self._contexts: Dict[str, Any] = {}
    def add_context(self, kind: str, value: Any):
        if kind == 'file':
            ctx = FileCtx(value)
        else:
            ctx = value
        self._contexts[kind] = ctx
        return ctx


def test_load_file_deletes_upload_and_sets_metadata():
    # Arrange: create a fake uploaded file under the repo's web/uploads
    import actions.load_file_action as lfa
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(lfa.__file__)))
    uploads_dir = os.path.join(project_root, 'web', 'uploads')
    os.makedirs(uploads_dir, exist_ok=True)
    up = os.path.join(uploads_dir, 'example_test_cleanup.txt')
    with open(up, 'w', encoding='utf-8') as f:
        f.write('hello upload')
    sess = FakeSession()
    action = lfa.LoadFileAction(sess)

    # Act
    res = action.run({'files': [str(up)]})

    # Assert: uploaded file removed on disk
    assert not os.path.exists(up), 'uploaded file should be deleted after loading'
    # Context metadata adjusted
    ctx = sess._contexts.get('file')
    assert isinstance(ctx, FileCtx)
    assert ctx.file.get('origin') == 'upload'
    assert ctx.file.get('server_path') == str(up)
    assert ctx.file.get('name') == os.path.basename(up)
