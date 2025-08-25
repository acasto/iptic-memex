from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class ActionState:
    action_name: str
    step: int
    phase: str
    data: Dict[str, Any]
    issued_at: float
    used: bool = False


class WebState:
    """Shared web server state and HMAC-signed token helpers.

    This mirrors the logic that was embedded in `WebApp` in web/app.py but as a
    reusable helper that route handlers (or a lightweight app builder) can use.
    """

    def __init__(self, session) -> None:
        self.session = session
        self._secret = secrets.token_bytes(32)
        self._states: Dict[str, ActionState] = {}

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue_token(
        self, action_name: str, step: int, phase: str, data: Dict[str, Any]
    ) -> str:
        issued_at = time.time()
        payload = f"{action_name}|{step}|{phase}|{issued_at}".encode()
        sig = self.sign(payload)
        token = f"{sig}:{issued_at}:{action_name}:{step}:{phase}"
        self._states[token] = ActionState(
            action_name=action_name,
            step=step,
            phase=phase,
            data=data,
            issued_at=issued_at,
            used=False,
        )
        return token

    def verify_token(
        self, token: str, *, ttl_seconds: int = 900
    ) -> Tuple[Optional[ActionState], Optional[str]]:
        try:
            sig, ts, action_name, step, phase = token.split(":", 4)
            step_i = int(step)
            ts_f = float(ts)
        except Exception:
            return None, "Malformed token"
        st = self._states.get(token)
        if not st:
            return None, "Unknown token"
        if st.used:
            return None, "Token already used"
        # Verify signature against original issued timestamp embedded in token
        payload = f"{action_name}|{step_i}|{phase}|{ts_f}".encode()
        if not hmac.compare_digest(sig, self.sign(payload)):
            return None, "Invalid signature"
        # TTL: use stored issued_at to allow test manipulation and single source of truth
        if time.time() - st.issued_at > ttl_seconds:
            return None, "Token expired"
        return st, None
