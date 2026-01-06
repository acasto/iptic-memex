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
        open_browser = self.session.get_option('WEB', 'open_browser', fallback=False)
        try:
            cfg_port = int(cfg_port)
        except Exception:
            cfg_port = 8765
        bind_host = host or cfg_host
        bind_port = int(port or cfg_port)
        if open_browser:
            # Normalize host for browser; the actual open is handled by the
            # Starlette lifespan startup in app_factory, which will read this URL
            # and open it after the app signals readiness.
            browser_host = 'localhost' if str(bind_host) in ('0.0.0.0', '::') else str(bind_host)
            try:
                # Pass URL through app.state for the lifespan hook to read
                self._app.state.open_browser_url = f'http://{browser_host}:{bind_port}/'
            except Exception:
                # Fallback: best-effort immediate open if app.state is unavailable
                try:
                    self._open_browser(browser_host, bind_port)
                except Exception:
                    pass
        try:
            uvicorn.run(self._app, host=str(bind_host), port=int(bind_port), log_level='info')
        finally:
            try:
                self.session.handle_exit(confirm=False)
            except Exception:
                pass

    @staticmethod
    def _open_browser(host: str, port: int) -> None:
        import platform
        import subprocess
        """Get the appropriate system command for opening URLs"""
        system = platform.system().lower()
        open_cmd = None

        if system == 'darwin':  # macOS
            open_cmd = ['open']
        elif system == 'linux':
            open_cmd = ['xdg-open']
        elif system == 'windows':
            open_cmd = ['cmd', '/c', 'start', '']
        else:
            # Fallback - try common commands
            for cmd in ['xdg-open', 'open']:
                try:
                    subprocess.run(['which', cmd], check=True, capture_output=True)
                    open_cmd = [cmd]
                    break
                except subprocess.CalledProcessError:
                    continue
            if open_cmd is None:
                return None

        if open_cmd:
            url = f'http://{host}:{port}/'
            try:
                subprocess.Popen(open_cmd + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        return None
