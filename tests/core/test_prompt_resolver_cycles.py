from __future__ import annotations

import os
import sys
from configparser import ConfigParser

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.prompt_resolver import PromptResolver


class _FakeConfig:
    def __init__(self, base_config: ConfigParser):
        self.base_config = base_config

    def get_default_prompt_source(self):
        return self.base_config.get('PROMPTS', 'default', fallback=None)

    def get_option(self, section: str, option: str, fallback=None):
        try:
            return self.base_config.get(section, option, fallback=fallback)
        except Exception:
            return fallback


def test_prompt_resolver_allows_default_prompt_file_when_prompts_default_is_default(tmp_path):
    (tmp_path / "default.txt").write_text("HELLO", encoding="utf-8")

    base = ConfigParser()
    base["DEFAULT"] = {"prompt_directory": str(tmp_path)}
    base["PROMPTS"] = {"default": "default"}

    resolver = PromptResolver(_FakeConfig(base))
    assert resolver.resolve(None) == "HELLO"


def test_prompt_resolver_detects_circular_prompt_mapping():
    base = ConfigParser()
    base["PROMPTS"] = {"a": "b", "b": "a"}

    resolver = PromptResolver(_FakeConfig(base))
    with pytest.raises(ValueError, match=r"Circular prompt reference detected"):
        resolver.resolve("a")

