import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session

from core.session_persistence import apply_session_data, load_session_data, save_session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_save_and_resume_roundtrip():
    sess = _make_session()
    chat = sess.add_context('chat')
    ctx = sess.create_context('file', {'name': 'notes.txt', 'content': 'hello'})
    chat.add("hi", role="user", context=[{'type': 'file', 'context': ctx}])

    with tempfile.TemporaryDirectory() as tmpdir:
        path = save_session(sess, kind='session', directory=tmpdir)
        data = load_session_data(path)

    new_sess = _make_session()
    apply_session_data(new_sess, data, fork=False)

    new_chat = new_sess.get_context('chat')
    turns = new_chat.get('all') if new_chat else []
    assert len(turns) == 1
    turn = turns[0]
    assert turn.get('message') == 'hi'
    ctx_items = turn.get('context') or []
    assert ctx_items and ctx_items[0].get('type') == 'file'
    ctx_obj = ctx_items[0].get('context')
    ctx_data = ctx_obj.get() if ctx_obj else {}
    assert ctx_data.get('name') == 'notes.txt'
