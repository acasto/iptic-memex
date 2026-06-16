from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.build_turn_prompt_action import BuildTurnPromptAction


class FakeConfig:
    def get_default_prompt_source(self):
        return "default.txt"

    def get_option(self, section, option, fallback=None):
        return fallback


class FakeSession:
    def __init__(self, *, model_value=None, provider_value=None, default_value=None):
        self.config = FakeConfig()
        self._model_value = model_value
        self._provider_value = provider_value
        self._default_value = default_value
        self.user_data = {}
        self.params = {"model": "small-model", "provider": "LocalProvider"}

    def get_option_from_model(self, option, model_name=None):
        if option == "turn_prompt":
            return self._model_value
        return None

    def get_option_from_provider(self, option, provider_name=None):
        if option == "turn_prompt":
            return self._provider_value
        return None

    def get_option(self, section, option, fallback=None):
        if section == "DEFAULT" and option == "template_handler":
            return "none"
        if section == "DEFAULT" and option == "turn_prompt":
            return self._default_value
        return fallback

    def set_user_data(self, key, value):
        self.user_data[key] = value

    def get_action(self, name):
        return None


def test_model_false_disables_default_turn_prompt():
    action = BuildTurnPromptAction(
        FakeSession(model_value=False, default_value="default turn prompt")
    )

    assert action.run({"id": "t1"}) == ""


def test_model_false_disables_provider_turn_prompt():
    action = BuildTurnPromptAction(
        FakeSession(model_value=False, provider_value="provider turn prompt")
    )

    assert action.run({"id": "t1"}) == ""


def test_provider_false_disables_default_turn_prompt():
    action = BuildTurnPromptAction(
        FakeSession(provider_value="off", default_value="default turn prompt")
    )

    assert action.run({"id": "t1"}) == ""


def test_model_turn_prompt_still_overrides_provider_and_default():
    action = BuildTurnPromptAction(
        FakeSession(
            model_value="model turn prompt",
            provider_value="provider turn prompt",
            default_value="default turn prompt",
        )
    )

    assert action.run({"id": "t1"}) == "model turn prompt"
