from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse


async def handle_api_status(app, request: Request):
    params = app.session.get_params() or {}
    model = params.get("model")
    provider = params.get("provider")
    return JSONResponse({"ok": True, "model": model, "provider": provider,
                         "private": app.session.private})


async def handle_api_params(app, request: Request):
    try:
        params = app.session.get_params() or {}
    except Exception:
        params = {}
    visible = ('model', 'provider', 'stream', 'temperature', 'max_tokens', 'context_sent',
               'enable_tools', 'tool_mode', 'top_p', 'chat_format')
    return JSONResponse({"ok": True, "params": {k: params[k] for k in visible if k in params}})


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


async def handle_api_history(app, request):
    from app.chat import visible_history
    state = app._webstate
    pending = None
    if state.busy():
        if state.command_token:
            need = state.active.need
            spec = {k: v for k, v in need.spec.items() if not k.startswith('__')}
            pending = dict(needs_interaction={'kind': need.kind, 'spec': spec},
                           state_token=state.command_token)
        else:
            pending = getattr(state.active, 'pending', None)
    return JSONResponse({'messages': visible_history(app.session), 'pending': pending,
                         'pick_session': getattr(app.session, 'pick_session_on_start', False)},
                        headers={'Cache-Control': 'no-store'})
