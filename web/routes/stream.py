from __future__ import annotations

from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse


async def handle_api_stream_start(app, request: Request):
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return PlainTextResponse('Invalid JSON', status_code=400)
    message = (payload.get('message') or '').strip()
    if not message:
        return PlainTextResponse('Missing "message"', status_code=400)
    # Issue a short-lived token carrying the message; client opens SSE with this token
    token = app._issue_token('stream', 1, 'stream', {"message": message})
    return JSONResponse({"ok": True, "token": token})


async def handle_api_stream(app, request: Request):
    from web.output_sink import WebOutput
    import asyncio
    import json
    import threading

    # Prefer token-based message handoff to avoid logging sensitive content in query strings
    token = (request.query_params.get('token') or '').strip()
    if token:
        st, err = app._verify_token(token)
        if not st:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": err or 'Invalid token'}}, status_code=400)
        if st.phase != 'stream':
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": 'Invalid stream token'}}, status_code=400)
        message = (st.data or {}).get('message') or ''
        # Mark token used (single-use)
        st.used = True
    else:
        # Backward compatibility: allow message in query param (discouraged)
        message = (request.query_params.get('message') or '').strip()
        if not message:
            return PlainTextResponse('Missing "message"', status_code=400)

    # Early: command handling parity with /api/chat (do not open stream when handled)
    try:
        user_cmds = app.session.get_action('user_commands')
    except Exception:
        user_cmds = None
    matched_kind = None
    matched_action = None
    matched_info = None
    matched_args: List[str] = []
    if user_cmds and hasattr(user_cmds, 'commands') and len(message.split()) <= 4:
        try:
            sorted_commands = sorted(user_cmds.commands.keys(), key=len, reverse=True)  # type: ignore[attr-defined]
            for cmd in sorted_commands:
                if message.lower() == cmd or message.lower().startswith(cmd + ' '):
                    info = user_cmds.commands[cmd]  # type: ignore[attr-defined]
                    fn = info.get('function', {})
                    matched_kind = fn.get('type')
                    matched_info = info
                    if matched_kind == 'action':
                        matched_action = fn.get('name')
                    user_tail = message[len(cmd):].strip().split()
                    predefined = fn.get('args', [])
                    if isinstance(predefined, str):
                        predefined = [predefined]
                    matched_args = list(predefined) + user_tail
                    break
        except Exception:
            matched_kind = None

    if matched_kind in ('action', 'method'):
        utils = app.session.utils
        original_output = utils.output
        web_output = WebOutput()
        utils.replace_output(web_output)
        emitted: List[Dict[str, Any]] = []
        original_ui = app.session.ui
        original_emit = getattr(original_ui, 'emit', None)

        def _capture_emit(event_type: str, data: Dict[str, Any]) -> None:
            try:
                item = dict(data)
                item['type'] = event_type
                emitted.append(item)
            except Exception:
                pass
        from contextlib import redirect_stdout
        from io import StringIO
        stdout_buf = StringIO()
        try:
            if original_emit:
                original_ui.emit = _capture_emit  # type: ignore[attr-defined]
            with redirect_stdout(stdout_buf):
                if matched_kind == 'action' and matched_action:
                    action = app.session.get_action(matched_action)
                    if action:
                        fn = matched_info.get('function', {}) if matched_info else {}
                        if 'method' in fn:
                            meth = getattr(action.__class__, fn['method'])
                            meth(app.session, *matched_args if matched_args else [])
                        else:
                            try:
                                action.run(matched_args)
                            except Exception as need_exc:
                                from base_classes import InteractionNeeded
                                if isinstance(need_exc, InteractionNeeded):
                                    need_kind = need_exc.kind
                                    token = app._issue_token(matched_action, 1, need_kind, {"args": {"argv": matched_args}, "content": None})
                                    spec = dict(need_exc.spec)
                                    spec['state_token'] = token
                                    # Render an instructional text
                                    emitted.append({'type': 'status', 'message': 'Action needs interaction; respond via /api/action/resume.'})
                                    text_lines = ['Action needs interaction; use /api/action/resume with provided token.']
                                    for ev in emitted:
                                        if ev.get('type') in ('status', 'warning', 'error') and ev.get('message'):
                                            text_lines.append(str(ev.get('message')))
                                    printed = stdout_buf.getvalue()
                                    if printed:
                                        text_lines.append(printed)
                                    render_text = '\n'.join([ln for ln in text_lines if ln])

                                    async def one_shot_need():
                                        yield f"event: done\ndata: {json.dumps({'text': render_text, 'handled': True, 'needs_interaction': {'kind': need_kind, 'spec': spec}, 'state_token': token, 'updates': emitted, 'command': matched_action})}\n\n"
                                    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
                                    return StreamingResponse(one_shot_need(), media_type="text/event-stream", headers=headers)
                                else:
                                    # Re-raise to be handled by outer finally
                                    raise
                elif matched_kind == 'method':
                    fn = matched_info.get('function', {}) if matched_info else {}
                    method_name = fn.get('name')
                    if method_name:
                        method = getattr(user_cmds, method_name)
                        method(*matched_args)
        finally:
            try:
                if original_emit:
                    original_ui.emit = original_emit  # type: ignore[attr-defined]
            except Exception:
                pass
            utils.replace_output(original_output)

        # Render text from updates and prints
        text_lines = []
        for ev in emitted:
            if ev.get('type') in ('status', 'warning', 'error') and ev.get('message'):
                text_lines.append(str(ev.get('message')))
        printed = stdout_buf.getvalue()
        if printed:
            text_lines.append(printed)
        render_text = '\n'.join([ln for ln in text_lines if ln])

        async def one_shot():
            yield f"event: done\ndata: {json.dumps({'text': render_text, 'handled': True, 'updates': emitted, 'command': (matched_action or (matched_info.get('function', {}).get('name') if matched_info else None))})}\n\n"
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(one_shot(), media_type="text/event-stream", headers=headers)

    # Use TurnRunner for streaming with SSE
    provider = app.session.get_provider()
    if not provider:
        return PlainTextResponse('No provider available', status_code=500)

    app.session.set_option('stream', True)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    web_output = WebOutput(loop=loop, queue=queue)

    def run_streaming_turn():
        original_output = app.session.utils.output
        app.session.utils.replace_output(web_output)
        # Capture UI emits during turn to surface in final 'done' event
        emitted: List[Dict[str, Any]] = []
        original_ui = app.session.ui
        original_emit = getattr(original_ui, 'emit', None)

        def _capture_emit(event_type: str, data: Dict[str, Any]) -> None:
            try:
                item = dict(data)
                item['type'] = event_type
                emitted.append(item)
            except Exception:
                pass
        if original_emit:
            try:
                original_ui.emit = _capture_emit  # type: ignore[attr-defined]
            except Exception:
                pass
        try:
            from core.turns import TurnRunner, TurnOptions
            from base_classes import InteractionNeeded
            runner = TurnRunner(app.session)
            try:
                result = runner.run_user_turn(message, options=TurnOptions(stream=True, suppress_context_print=True))
            except InteractionNeeded as need:
                spec = dict(getattr(need, 'spec', {}) or {})
                action_name = spec.pop('__action__', None) or 'assistant_file_tool'
                args_for_action = spec.pop('__args__', {}) if isinstance(spec.get('__args__'), dict) else {}
                content_for_action = spec.pop('__content__', None)
                tok = app._issue_token(action_name, 1, need.kind, {"args": args_for_action, "content": content_for_action})
                spec['state_token'] = tok
                # Render one-shot done event with needs_interaction
                data = {"text": "", "handled": True, "needs_interaction": {"kind": need.kind, "spec": spec}, "state_token": tok, "updates": emitted, "command": action_name}
                loop.call_soon_threadsafe(queue.put_nowait, ("done", data))
                return
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"message": str(e)}))
                return

            # If streaming completed without InteractionNeeded, finalize
            text = getattr(result, 'last_text', None) or ""
            # If no token events were emitted by WebOutput, emit a single token with the full text
            try:
                if not getattr(web_output, '_emitted', False) and text:
                    loop.call_soon_threadsafe(queue.put_nowait, ("token", {"text": text}))
            except Exception:
                pass
            # Post-process assistant_commands in case provider output triggers a handoff
            try:
                ac = app.session.get_action('assistant_commands')
                if ac and hasattr(ac, 'parse_commands') and callable(getattr(ac, 'parse_commands')):
                    cmds = []
                    try:
                        cmds = ac.parse_commands(text or '')
                    except Exception:
                        cmds = []
                    if cmds:
                        try:
                            ac.run(text or '')
                        except Exception as need_exc:
                            try:
                                from base_classes import InteractionNeeded
                                if isinstance(need_exc, InteractionNeeded):
                                    spec = dict(getattr(need_exc, 'spec', {}) or {})
                                    action_name = spec.pop('__action__', None) or 'assistant_file_tool'
                                    args_for_action = spec.pop('__args__', {}) if isinstance(spec.get('__args__'), dict) else {}
                                    content_for_action = spec.pop('__content__', None)
                                    tok = app._issue_token(action_name, 1, need_exc.kind, {"args": args_for_action, "content": content_for_action})
                                    spec['state_token'] = tok
                                    data = {"text": "", "handled": True, "needs_interaction": {"kind": need_exc.kind, "spec": spec}, "state_token": tok, "updates": emitted, "command": action_name}
                                    loop.call_soon_threadsafe(queue.put_nowait, ("done", data))
                                    return
                            except Exception:
                                pass
            except Exception:
                pass
            data = {"text": text, "updates": emitted}
            loop.call_soon_threadsafe(queue.put_nowait, ("done", data))
        finally:
            try:
                if original_emit:
                    original_ui.emit = original_emit  # type: ignore[attr-defined]
            except Exception:
                pass
            app.session.utils.replace_output(original_output)

    # Run the turn in a thread; consume queue into SSE
    t = threading.Thread(target=run_streaming_turn, daemon=True)
    t.start()

    async def event_generator():
        import json
        while True:
            item = await queue.get()
            # Support both dict tokens from WebOutput and (typ, data) tuples
            if isinstance(item, dict) and item.get('type') == 'token':
                yield f"event: token\ndata: {json.dumps({'text': item.get('text', '')})}\n\n"
                continue
            try:
                typ, data = item
            except Exception:
                continue
            if typ == 'token':
                yield f"event: token\ndata: {json.dumps(data)}\n\n"
            elif typ == 'done':
                yield f"event: done\ndata: {json.dumps(data)}\n\n"
                break
            elif typ == 'error':
                yield f"event: error\ndata: {json.dumps(data)}\n\n"
                break

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
