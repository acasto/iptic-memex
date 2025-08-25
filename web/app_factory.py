from __future__ import annotations

from typing import Any, Dict, Callable
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from web.server.state import WebState


class AppCtx:
    """Lightweight adapter exposing the attributes route handlers expect.

    - session: the core session
    - _issue_token/_verify_token: token helpers backed by WebState
    """

    def __init__(self, session, webstate: WebState) -> None:
        self.session = session
        self._webstate = webstate

    # Token helpers expected by existing route handlers
    def _issue_token(self, action_name: str, step: int, phase: str, data: Dict[str, Any]) -> str:
        return self._webstate.issue_token(action_name, step, phase, data)

    def _verify_token(self, token: str, *, ttl_seconds: int = 900):
        return self._webstate.verify_token(token, ttl_seconds=ttl_seconds)


def _index_handler_factory(index_html_path: str) -> Callable[[Request], Any]:
    async def index(request: Request):
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        }
        try:
            with open(index_html_path, 'r', encoding='utf-8') as f:
                html = f.read()
            return HTMLResponse(html, headers=headers)
        except Exception:
            return HTMLResponse("<h1>Iptic Memex - Web</h1>", headers=headers)
    return index


def create_app(session, webstate: WebState | None = None) -> Starlette:
    """Create a Starlette app using split route handlers and shared WebState."""
    webstate = webstate or WebState(session)
    ctx = AppCtx(session, webstate)

    # Route wrappers that pass our ctx to handlers
    async def api_status(request: Request):
        from web.routes.meta import handle_api_status
        return await handle_api_status(ctx, request)

    async def api_params(request: Request):
        from web.routes.meta import handle_api_params
        return await handle_api_params(ctx, request)

    async def api_models(request: Request):
        from web.routes.meta import handle_api_models
        return await handle_api_models(ctx, request)

    async def api_chat(request: Request):
        from web.routes.chat import handle_api_chat
        return await handle_api_chat(ctx, request)

    async def api_stream_start(request: Request):
        from web.routes.stream import handle_api_stream_start
        return await handle_api_stream_start(ctx, request)

    async def api_stream(request: Request):
        from web.routes.stream import handle_api_stream
        return await handle_api_stream(ctx, request)

    async def api_action_start(request: Request):
        from web.routes.actions import handle_api_action_start
        return await handle_api_action_start(ctx, request)

    async def api_action_resume(request: Request):
        from web.routes.actions import handle_api_action_resume
        return await handle_api_action_resume(ctx, request)

    async def api_action_cancel(request: Request):
        from web.routes.actions import handle_api_action_cancel
        return await handle_api_action_cancel(ctx, request)

    routes = [
        Route('/', _index_handler_factory(os.path.join(os.path.dirname(__file__), 'static', 'index.html'))),
        Route('/api/status', api_status, methods=['GET']),
        Route('/api/params', api_params, methods=['GET']),
        Route('/api/models', api_models, methods=['GET']),
        Route('/api/chat', api_chat, methods=['POST']),
        Route('/api/stream/start', api_stream_start, methods=['POST']),
        Route('/api/stream', api_stream, methods=['GET']),
        Route('/api/action/start', api_action_start, methods=['POST']),
        Route('/api/action/resume', api_action_resume, methods=['POST']),
        Route('/api/action/cancel', api_action_cancel, methods=['POST']),
        # Upload route is implemented as a free function already
        Route('/api/upload', __import__('web.routes.upload', fromlist=['api_upload']).api_upload, methods=['POST']),
    ]

    app = Starlette(routes=routes)
    # Mount static dir
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    os.makedirs(static_dir, exist_ok=True)
    app.mount('/static', StaticFiles(directory=static_dir), name='static')

    # Attach shared state
    app.state.session = session
    app.state.webstate = webstate
    return app

