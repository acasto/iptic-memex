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

    ttl = 900

    def __init__(self, session) -> None:
        from app.chat import configure_chat
        from app.session_host import SessionHost
        configure_chat(session)
        self.host = SessionHost(session, owns_session=False)
        self.active = None
        self.command_token = None
        self.session = session
        self._secret = secrets.token_bytes(32)
        self._states: Dict[str, ActionState] = {}

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue_token(
        self, action_name: str, step: int, phase: str, data: Dict[str, Any]
    ) -> str:
        issued_at = time.time()
        for key in [key for key, value in self._states.items()
                    if issued_at - value.issued_at > self.ttl]:
            expired = self._states.pop(key)
            if expired.phase == 'upload' and expired.data.get('batch'):
                expired.data['batch'].cleanup()
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

    def take_upload(self, token):
        """Claim a batch once; the caller owns its temporary files until loading ends."""
        st, reason = self.verify_token(token)
        if not st or st.phase != 'upload':
            raise ValueError(reason or 'Invalid upload token')
        st.used = True
        return st.data.pop('batch'), st.data.pop('paths')

    def busy(self):
        if self.command_token and not self.verify_token(self.command_token)[0]:
            self.command_token = self.active = None
        handle = getattr(self.active, 'handle', None)
        if handle and handle.done():
            self.active = None
        return self.active is not None

    def close(self):
        if getattr(self.active, 'handle', None):
            self.active.cancel()
        self.host.close()
        self.active = None
        for state in self._states.values():
            if state.phase == 'upload' and state.data.get('batch'):
                state.data['batch'].cleanup()
        self._states.clear()
