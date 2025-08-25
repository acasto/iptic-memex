from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse


async def handle_api_chat(app, request: Request):
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return PlainTextResponse('Invalid JSON', status_code=400)

    message = (payload.get('message') or '').strip()
    if not message:
        return PlainTextResponse('Missing "message"', status_code=400)

    # Check for user command first (CLI parity: handle before adding to chat/LLM)
    try:
        user_cmds = app.session.get_action('user_commands')
    except Exception:
        user_cmds = None

    matched_action: Optional[str] = None
    matched_kind: Optional[str] = None  # 'action' or 'method'
    matched_info: Optional[Dict[str, Any]] = None
    matched_args: List[str] = []
    if user_cmds and hasattr(user_cmds, 'commands') and len(message.split()) <= 4:
        try:
            # Sort by length (longest first) as in CLI
            sorted_commands = sorted(user_cmds.commands.keys(), key=len, reverse=True)  # type: ignore[attr-defined]
            for cmd in sorted_commands:
                if message.lower() == cmd or message.lower().startswith(cmd + ' '):
                    info = user_cmds.commands[cmd]  # type: ignore[attr-defined]
                    fn = info.get('function', {})
                    matched_kind = fn.get('type')
                    matched_info = info
                    if matched_kind == 'action':
                        matched_action = fn['name']
                    # prepare args the same way (predefined and user tail)
                    user_tail = message[len(cmd):].strip().split()
                    predefined = fn.get('args', [])
                    if isinstance(predefined, str):
                        predefined = [predefined]
                    matched_args = list(predefined) + user_tail
                    break
        except Exception:
            matched_action = None
            matched_kind = None

    if matched_kind in ('action', 'method'):
        # Execute command (action or method) with event capture and no LLM call
        from web.output_sink import WebOutput
        from contextlib import redirect_stdout
        from io import StringIO
        utils = app.session.utils
        original_output = utils.output
        web_output = WebOutput()
        utils.replace_output(web_output)
        # Capture UI emits
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
        try:
            if original_emit:
                original_ui.emit = _capture_emit  # type: ignore[attr-defined]
            # Capture print() output as additional status updates
            stdout_buf = StringIO()
            with redirect_stdout(stdout_buf):
                if matched_kind == 'action' and matched_action:
                    action = app.session.get_action(matched_action)
                    if not action:
                        return JSONResponse({'ok': False, 'error': {'recoverable': False, 'message': f"Unknown action '{matched_action}'"}}, status_code=404)
                    try:
                        # Allow special method-on-action if declared
                        fn = matched_info.get('function', {}) if matched_info else {}
                        if 'method' in fn:
                            meth = getattr(action.__class__, fn['method'])
                            meth(app.session, *matched_args if matched_args else [])
                        else:
                            action.run(matched_args)
                    except Exception as need_exc:
                        # If a stepwise action requested interaction, return needs_interaction
                        from base_classes import InteractionNeeded
                        if isinstance(need_exc, InteractionNeeded):
                            token = app._issue_token(matched_action, 1, need_exc.kind, {"args": {"argv": matched_args}, "content": None})
                            spec = dict(need_exc.spec)
                            spec['state_token'] = token
                            # Include any printed output as updates too
                            printed = stdout_buf.getvalue()
                            if printed:
                                emitted.append({'type': 'status', 'message': printed})
                            # Compose a minimal text to display in UI
                            prompt_text = ''
                            try:
                                prompt_text = str(spec.get('prompt') or spec.get('message') or '')
                            except Exception:
                                prompt_text = ''
                            return JSONResponse({
                                "ok": True,
                                "done": False,
                                "needs_interaction": {"kind": need_exc.kind, "spec": spec},
                                "state_token": token,
                                "updates": emitted,
                                "text": prompt_text
                            })
                        # otherwise, real error
                        return JSONResponse({'ok': False, 'error': {'recoverable': True, 'message': str(need_exc)}}, status_code=500)
                elif matched_kind == 'method':
                    # Call method on the user commands action instance
                    try:
                        fn = matched_info.get('function', {}) if matched_info else {}
                        method_name = fn.get('name')
                        if not method_name:
                            return JSONResponse({'ok': False, 'error': {'recoverable': False, 'message': 'Invalid command mapping'}}, status_code=500)
                        method = getattr(user_cmds, method_name)
                        method(*matched_args)
                    except Exception as e:
                        return JSONResponse({'ok': False, 'error': {'recoverable': True, 'message': str(e)}}, status_code=500)
            # Flush captured prints into updates
            printed = stdout_buf.getvalue()
            if printed:
                emitted.append({'type': 'status', 'message': printed})
        finally:
            try:
                if original_emit:
                    original_ui.emit = original_emit  # type: ignore[attr-defined]
            except Exception:
                pass
            utils.replace_output(original_output)

        # Compose a simple text from status updates and prints for current UI
        text_lines = []
        for ev in emitted:
            if ev.get('type') in ('status', 'warning', 'error') and ev.get('message'):
                text_lines.append(str(ev.get('message')))
        render_text = '\n'.join(text_lines) if text_lines else ''
        return JSONResponse({'ok': True, 'done': True, 'handled': True, 'updates': emitted, 'command': matched_action or (matched_info.get('function', {}).get('name') if matched_info else None), 'text': render_text})

    # Non-stream path via TurnRunner
    provider = app.session.get_provider()
    if not provider:
        return PlainTextResponse('No provider available', status_code=500)

    # Ensure streaming disabled for this call
    app.session.set_option('stream', False)

    # Use TurnRunner and suppress stdout via WebOutput
    from web.output_sink import WebOutput
    from core.turns import TurnRunner, TurnOptions
    utils = app.session.utils
    original_output = utils.output
    web_output = WebOutput()
    utils.replace_output(web_output)
    # Capture UI emits during this call
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
        try:
            runner = TurnRunner(app.session)
            result = runner.run_user_turn(message, options=TurnOptions(stream=False, suppress_context_print=True))
        finally:
            # restore ui.emit
            try:
                if original_emit:
                    original_ui.emit = original_emit  # type: ignore[attr-defined]
            except Exception:
                pass
            utils.replace_output(original_output)
        if not result:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": "No result"}}, status_code=500)
        # Compose visible assistant text from TurnResult
        text = result.last_text or ''
        return JSONResponse({'ok': True, 'text': text, 'updates': emitted})
    except Exception as e:
        return JSONResponse({'ok': False, 'error': {'recoverable': True, 'message': str(e)}}, status_code=500)
