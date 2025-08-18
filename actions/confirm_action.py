from __future__ import annotations

from typing import Any, Dict

from base_classes import StepwiseAction, Completed, Updates


class ConfirmAction(StepwiseAction):
    """A tiny canary action demonstrating stepwise prompts.

    - Asks the user to confirm a short message.
    - On confirm, emits a status update and returns a payload.
    - In CLI, this runs to completion in one go.
    - In Web/TUI, ask_* raises InteractionNeeded to be handled by the mode.
    """

    def __init__(self, session) -> None:
        self.session = session
        self._step = 0

    @classmethod
    def can_run(cls, session) -> bool:  # optional gating hook used elsewhere
        return True

    def start(self, args: Dict | None = None, content: Any | None = None) -> Completed | Updates:
        args = args or {}
        message = args.get('message') or content or "Proceed?"
        answer = self.session.ui.ask_bool(str(message), default=True)
        if answer:
            self.session.ui.emit('status', {'message': 'Confirmed by user.'})
            return Completed({'ok': True, 'confirmed': True})
        else:
            self.session.ui.emit('status', {'message': 'User declined.'})
            return Completed({'ok': True, 'confirmed': False})

    def resume(self, state_token: str, response: Any) -> Completed | Updates:
        # This simple action completes in start(); resume supports protocol completeness
        return Completed({'ok': True, 'resumed': True})

