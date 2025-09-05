from __future__ import annotations


class DummySession:
    class DummyFS:
        def resolve_file_path(self, f):
            return None

    def __init__(self):
        self.utils = type('U', (), {'fs': DummySession.DummyFS()})()


def test_file_context_accepts_dict_and_returns_verbatim():
    from contexts.file_context import FileContext
    sess = DummySession()
    data = {'name': 'sample.txt', 'content': 'hello world', 'metadata': {'k': 'v'}}
    ctx = FileContext(sess, data)
    out = ctx.get()
    assert out['name'] == 'sample.txt'
    assert out['content'] == 'hello world'
    assert out.get('metadata', {}).get('k') == 'v'

