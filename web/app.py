from __future__ import annotations

"""
Thin WebApp shim that wraps the Starlette app created by web.app_factory.

Tests and CLI code can continue importing WebApp and using `app._app`.
State tokens are handled by WebState; `_states` is exposed for back-compat
in tests that manipulate token expiry.
"""

import os

try:
    import uvicorn
except Exception as e:  # pragma: no cover - surfaced by modes/web_mode.py
    raise


INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), 'static', 'index.html')


class WebApp:
    def __init__(self, session, builder=None) -> None:
        from web.app_factory import create_app
        from web.server.state import WebState
        self.session = session
        self.builder = builder
        if not self.session.get_context('chat'):
            self.session.add_context('chat')
        self._webstate = WebState(self.session)
        # Back-compat: expose the internal state map for tests
        self._states = self._webstate._states  # type: ignore[attr-defined]
        self._app = create_app(self.session, self._webstate)

    def start(self, host: str | None = None, port: int | None = None) -> None:
        cfg_host = self.session.get_option('WEB', 'host', fallback='127.0.0.1')
        cfg_port = self.session.get_option('WEB', 'port', fallback=8765)
        try:
            cfg_port = int(cfg_port)
        except Exception:
            cfg_port = 8765
        bind_host = host or cfg_host
        bind_port = int(port or cfg_port)
        uvicorn.run(self._app, host=str(bind_host), port=int(bind_port), log_level='info')
