import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session

from actions.prompt_template_file_action import PromptTemplateFileAction


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_prompt_template_file_includes_content(tmp_path):
    sess = _make_session()
    p = tmp_path / "included.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")

    handler = PromptTemplateFileAction(sess)
    out = handler.run(f"X {{{{file:{str(p)}}}}} Y")
    assert "hello" in out
    assert "world" in out
    assert out.startswith("X ")


def test_prompt_template_file_missing_is_empty():
    sess = _make_session()
    handler = PromptTemplateFileAction(sess)
    out = handler.run("A {{file:does-not-exist.txt}} B")
    assert out == "A  B"


def test_prompt_template_file_truncates_max_chars(tmp_path):
    sess = _make_session()
    p = tmp_path / "long.txt"
    p.write_text("a" * 50, encoding="utf-8")

    handler = PromptTemplateFileAction(sess)
    out = handler.run(f"{{{{file:{str(p)};max_chars=10}}}}")
    assert "… (truncated" in out
