from __future__ import annotations

from typing import Any, Dict, List, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse


async def handle_api_action_start(app, request: Request):
    from base_classes import Completed, Updates, InteractionNeeded
    from web.output_sink import WebOutput
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return PlainTextResponse('Invalid JSON', status_code=400)

    action_name = (payload.get('action') or '').strip()
    if not action_name:
        return PlainTextResponse('Missing "action"', status_code=400)
    args = payload.get('args') or {}
    content = payload.get('content')

    action = app.session.get_action(action_name)
    if not action:
        return PlainTextResponse(f"Unknown action '{action_name}'", status_code=404)

    # Drive the action until a boundary (Completed or InteractionNeeded)
    def drive_until_boundary(res: Any, emitted: List[Dict[str, Any]]):
        aggregated: List[Dict[str, Any]] = list(emitted)
        while isinstance(res, Updates):
            aggregated.extend(res.events or [])
            try:
                res = action.resume("__implicit__", {"continue": True})
            except InteractionNeeded as need2:
                token2 = app._issue_token(action_name, 1, need2.kind, {"args": args, "content": content})
                spec2 = dict(need2.spec)
                spec2['state_token'] = token2
                return 'needs', {"needs_interaction": {"kind": need2.kind, "spec": spec2}, "updates": aggregated, "state_token": token2}

        if isinstance(res, Completed):
            return 'done', {"payload": res.payload, "updates": aggregated}
        return 'error', {"message": "Unknown result"}

    # Suppress stdout/spinners and capture ui.emit events during web action calls
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
        # Prefer stepwise start() if available; otherwise, fall back to run()
        if hasattr(action, 'start') and callable(getattr(action, 'start')):
            try:
                res = action.start(args, content)
            except InteractionNeeded as need:
                token = app._issue_token(action_name, 1, need.kind, {"args": args, "content": content})
                spec = dict(need.spec)
                spec['state_token'] = token
                return JSONResponse({"ok": True, "done": False, "needs_interaction": {"kind": need.kind, "spec": spec}, "state_token": token, "updates": emitted})
            except Exception as e:
                return JSONResponse({"ok": False, "error": {"recoverable": False, "message": str(e)}}, status_code=500)
        else:
            # Compatibility: call run() directly for legacy actions
            try:
                result = action.run(args)
                # Normalize payload: return the raw result under 'result' and best-effort ok flag
                payload = {'result': result}
                if isinstance(result, dict) and 'ok' in result:
                    # Preserve explicit ok when provided
                    payload = result
                status = True if result is None else bool(result)
                return JSONResponse({"ok": status, "done": True, "payload": payload, "updates": emitted})
            except Exception as e:
                return JSONResponse({"ok": False, "error": {"recoverable": False, "message": str(e)}}, status_code=500)
    finally:
        # restore ui.emit
        try:
            if original_emit:
                original_ui.emit = original_emit  # type: ignore[attr-defined]
        except Exception:
            pass
        utils.replace_output(original_output)

    status, data = drive_until_boundary(res, emitted)
    if status == 'done':
        # Optionally chain an auto-submitted assistant turn after resume
        try:
            if app.session.get_flag('auto_submit'):
                app.session.set_flag('auto_submit', False)
                # Re-process contexts and add synthetic user message
                pc = app.session.get_action('process_contexts')
                contexts2 = []
                if pc and hasattr(pc, 'process_contexts_for_user'):
                    try:
                        contexts2 = pc.process_contexts_for_user(auto_submit=True) or []
                    except Exception:
                        contexts2 = []
                try:
                    chat = app.session.get_context('chat')
                    chat.add("", 'user', contexts2)
                except Exception:
                    pass
                # Run assistant turn
                provider = app.session.get_provider()
                from actions.assistant_output_action import AssistantOutputAction
                utils = app.session.utils
                original_output = utils.output
                utils.replace_output(WebOutput())
                try:
                    raw_text = provider.chat() if provider else ''
                except Exception as e:
                    raw_text = f"[auto-submit error] {e}"
                finally:
                    utils.replace_output(original_output)
                if raw_text is None:
                    raw_text = ''
                visible = AssistantOutputAction.filter_full_text(raw_text, app.session)
                try:
                    chat.add(raw_text, 'assistant')
                except Exception:
                    pass
                # Attach a 'text' field for UI rendering
                data = {**data, 'text': visible}
        except Exception:
            pass
        return JSONResponse({"ok": True, "done": True, **data})
    elif status == 'needs':
        return JSONResponse({"ok": True, "done": False, **data})
    else:
        return JSONResponse({"ok": False, "error": {"recoverable": True, "message": data.get('message', 'Unexpected result')}}, status_code=500)


async def handle_api_action_resume(app, request: Request):
    from base_classes import Completed, Updates, InteractionNeeded
    from web.output_sink import WebOutput
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return PlainTextResponse('Invalid JSON', status_code=400)

    token = payload.get('state_token') or ''
    response = payload.get('response')
    if not token:
        return PlainTextResponse('Missing "state_token"', status_code=400)

    st, err = app._verify_token(token)
    if not st:
        return JSONResponse({"ok": False, "error": {"recoverable": True, "message": err or 'Invalid token'}}, status_code=400)

    action = app.session.get_action(st.action_name)
    if not action:
        return JSONResponse({"ok": False, "error": {"recoverable": False, "message": f"Unknown action '{st.action_name}'"}}, status_code=404)

    # Mark token used
    st.used = True

    # Helper to drive updates internally
    def drive_until_boundary(res: Any, current_step: int):
        aggregated: List[Dict[str, Any]] = []
        while isinstance(res, Updates):
            aggregated.extend(res.events or [])
            try:
                res = action.resume("__implicit__", {"continue": True})
            except InteractionNeeded as need2:
                next_step = current_step + 1
                token2 = app._issue_token(st.action_name, next_step, need2.kind, st.data)
                spec2 = dict(need2.spec)
                spec2['state_token'] = token2
                return 'needs', {"needs_interaction": {"kind": need2.kind, "spec": spec2}, "updates": aggregated, "state_token": token2}

        if isinstance(res, Completed):
            return 'done', {"payload": res.payload, "updates": aggregated}
        return 'error', {"message": "Unknown result"}

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
        try:
            # Pass stored args/content alongside user response for stateless actions
            res = action.resume(token, {"response": response, "state": st.data})
        except InteractionNeeded as need:
            next_step = st.step + 1
            token2 = app._issue_token(st.action_name, next_step, need.kind, st.data)
            spec = dict(need.spec)
            spec['state_token'] = token2
            return JSONResponse({"ok": True, "done": False, "needs_interaction": {"kind": need.kind, "spec": spec}, "state_token": token2, "updates": emitted})
        except Exception as e:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": str(e)}}, status_code=500)
    finally:
        # restore ui.emit
        try:
            if original_emit:
                original_ui.emit = original_emit  # type: ignore[attr-defined]
        except Exception:
            pass
        utils.replace_output(original_output)

    status, data = drive_until_boundary(res, st.step)
    if status == 'done':
        return JSONResponse({"ok": True, "done": True, **data})
    elif status == 'needs':
        return JSONResponse({"ok": True, "done": False, **data})
    else:
        return JSONResponse({"ok": False, "error": {"recoverable": True, "message": data.get('message', 'Unexpected result')}}, status_code=500)


async def handle_api_action_cancel(app, request: Request):
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return PlainTextResponse('Invalid JSON', status_code=400)
    token = payload.get('state_token') or ''
    if not token:
        return PlainTextResponse('Missing "state_token"', status_code=400)
    st, err = app._verify_token(token)
    if not st:
        return JSONResponse({"ok": False, "error": {"recoverable": True, "message": err or 'Invalid token'}}, status_code=400)
    st.used = True
    return JSONResponse({"ok": True, "done": True, "cancelled": True})
