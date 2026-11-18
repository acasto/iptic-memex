from starlette.responses import JSONResponse, PlainTextResponse, Response

from web.server.turn import WebTurn, sse


def error(message, status):
    return JSONResponse({'ok': False, 'error': {'message': message}}, status_code=status)


async def submit(app, request, message, *, stream=False, token=None, provider_stream=False,
                 upload_token=None, live_input=False):
    state = app._webstate
    if state.busy():
        return error('Finish or cancel the current operation first.', 409)
    upload = None
    if upload_token:
        from web.routes.upload import Upload
        try:
            upload = Upload(state, upload_token)
        except ValueError as exc:
            return error(str(exc), 400)
    if message.startswith('/'):
        from web.routes.actions import command
        try:
            data, status = await command(app, request, message, upload)
        finally:
            if upload:
                upload.close()
        if stream:
            if status >= 400:
                failure = data.get('error') or {}
                reason = failure.get('message') if isinstance(failure, dict) else failure
                data.update(done=True, status='failed', text=reason or 'Command failed',
                            error=reason)
            return Response(sse('done', data), media_type='text/event-stream')
        return JSONResponse(data, status_code=status)
    from app.chat import attach_startup_files
    app.session.pick_session_on_start = False
    attach_startup_files(app.session)
    try:
        turn = WebTurn(state, message, provider_stream, token, upload, live_input)
    except BaseException:
        if upload:
            upload.close()
        raise
    state.active = turn
    return await turn.response(request, stream)


async def handle_api_chat(app, request):
    try:
        payload = await request.json()
        message = payload['message'].strip()
    except (ValueError, KeyError, AttributeError, TypeError):
        return PlainTextResponse('Expected a message', status_code=400)
    if not message:
        return PlainTextResponse('Missing message', status_code=400)
    return await submit(app, request, message, upload_token=payload.get('upload_token'))
