from starlette.responses import JSONResponse

from web.routes.chat import error, submit


async def handle_api_stream_start(app, request):
    try:
        payload = await request.json()
        message = payload['message'].strip()
        stream = payload.get('stream', True)
        if not isinstance(stream, bool):
            return error('Expected a boolean stream setting', 400)
    except (ValueError, KeyError, AttributeError, TypeError):
        return error('Expected a message', 400)
    if not message:
        return error('Missing message', 400)
    if app._webstate.busy():
        return error('Finish or cancel the current operation first.', 409)
    token = app._issue_token('stream', 1, 'stream', {
        'message': message, 'stream': stream, 'upload': payload.get('upload_token'),
        'live_input': payload.get('live_input') is True,
    })
    return JSONResponse({'ok': True, 'token': token})


async def handle_api_stream(app, request):
    token = request.query_params.get('token')
    live_input = False
    upload = None
    provider_stream = True
    if token:
        state, reason = app._verify_token(token)
        if not state or state.phase != 'stream':
            return error(reason or 'Invalid stream token', 400)
        state.used = True
        message = state.data.pop('message')
        provider_stream = state.data.pop('stream')
        upload = state.data.pop('upload')
        live_input = state.data.pop('live_input')
    else:
        message = request.query_params.get('message', '').strip()
    if not message:
        return error('Missing message', 400)
    return await submit(app, request, message, stream=True, token=token, provider_stream=provider_stream,
                        upload_token=upload, live_input=live_input)


async def handle_api_stream_cancel(app, request):
    try:
        token = (await request.json())['token']
    except (ValueError, KeyError, TypeError):
        return error('Missing token', 400)
    turn = app._webstate.active if app._webstate.busy() else None
    cancelled = bool(token and getattr(turn, 'stream_token', None) == token)
    if cancelled:
        turn.cancel()
    elif token:
        state, _ = app._verify_token(token)
        if state and state.phase == 'stream':
            state.used = True
            state.data.clear()
            cancelled = True
    return JSONResponse({'ok': True, 'cancelled': cancelled})
