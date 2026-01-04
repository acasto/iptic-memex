from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.build_system_addenda_action import BuildSystemAddendaAction
from component_registry import PromptResolver


class FakeConfig:
    def get_default_prompt_source(self):
        return None

    def get_option(self, section: str, option: str, fallback=None):
        # Do not resolve any aliases or paths in tests; let PromptResolver
        # fall through to literal text for non-existent files
        return fallback


class FakeSession:
    def __init__(self):
        self._mode = 'pseudo'
        self._provider = 'OpenAI'
        self._model = 'gpt-foo'
        self._opts = {
            ('TOOLS', 'pseudo_tool_prompt'): 'PSEUDO',
            ('DEFAULT', 'supplemental_prompt'): 'DEF',
        }
        self._prov_opts = {
            ('OpenAI', 'supplemental_prompt'): 'PROV',
        }
        self._model_opts = {
            ('gpt-foo', 'supplemental_prompt'): 'MODEL',
        }
        self.config = FakeConfig()

    @property
    def params(self):
        return {'provider': self._provider, 'model': self._model}

    def get_effective_tool_mode(self):
        return self._mode

    def get_option(self, section: str, option: str, fallback=None):
        return self._opts.get((section, option), fallback)

    def get_option_from_provider(self, option: str, provider: str = None):
        key = (provider or self._provider, option)
        return self._prov_opts.get(key, None)

    def get_option_from_model(self, option: str, model: str = None):
        key = (model or self._model, option)
        return self._model_opts.get(key, None)


def test_addenda_order_and_content():
    sess = FakeSession()
    act = BuildSystemAddendaAction(sess)
    out = act.run()
    # Expected order: PSEUDO -> DEF -> PROV -> MODEL
    assert out == "PSEUDO\n\nDEF\n\nPROV\n\nMODEL"


def test_addenda_deduplicates_same_text():
    sess = FakeSession()
    # Make provider/model return duplicates of DEFAULT
    sess._prov_opts[(sess._provider, 'supplemental_prompt')] = 'DEF'
    sess._model_opts[(sess._model, 'supplemental_prompt')] = 'DEF'
    act = BuildSystemAddendaAction(sess)
    out = act.run()
    # Duplicates should be removed while preserving order
    assert out == "PSEUDO\n\nDEF"


def test_addenda_includes_skills_when_enabled(tmp_path):
    skill_root = tmp_path / "skills"
    (skill_root / "pdf-processing").mkdir(parents=True)
    skill_md = skill_root / "pdf-processing" / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: pdf-processing\n"
        "description: Extracts text from PDFs. Use when working with PDF files.\n"
        "---\n"
        "\n"
        "# PDF Processing\n",
        encoding="utf-8",
    )

    sess = FakeSession()
    sess._opts[("SKILLS", "active")] = True
    sess._opts[("SKILLS", "directories")] = str(skill_root)

    act = BuildSystemAddendaAction(sess)
    out = act.run()
    assert "<available_skills>" in out
    assert "pdf-processing" in out
    assert str(skill_md) in out
