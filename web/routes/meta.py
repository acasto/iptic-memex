from __future__ import annotations

from typing import Any, Dict

from starlette.requests import Request
from starlette.responses import JSONResponse


async def handle_api_status(app, request: Request):
    params = app.session.get_params() or {}
    model = params.get("model")
    provider = params.get("provider")
    return JSONResponse({"ok": True, "model": model, "provider": provider})


async def handle_api_params(app, request: Request):
    try:
        params = app.session.get_params() or {}
    except Exception:
        params = {}
    return JSONResponse({"ok": True, "params": params})


async def handle_api_models(app, request: Request):
    try:
        models = list((app.session.list_models(showall=False) or {}).keys())
    except Exception:
        models = []
    try:
        providers = list((app.session.list_providers(showall=False) or {}).keys())
    except Exception:
        providers = []
    return JSONResponse({"ok": True, "models": models, "providers": providers})

