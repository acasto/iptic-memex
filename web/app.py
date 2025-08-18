from __future__ import annotations

"""
Starlette-based local web app for iptic-memex.

Endpoints:
- GET /            -> Minimal HTML page
- POST /api/chat   -> One-shot chat (non-stream MVP)

Notes:
- Streaming (SSE/WebSocket) can be added next; this MVP returns the full
  assistant message and uses the same filtering as CLI non-stream path.
- If Starlette/Uvicorn are not installed, WebMode will surface the ImportError.
"""

from typing import Any, Dict, Optional, Tuple, List
import os
import hmac
import hashlib
import time
import secrets
from dataclasses import dataclass

try:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, PlainTextResponse, HTMLResponse, StreamingResponse
    from starlette.requests import Request
    from starlette.routing import Route
    from starlette.staticfiles import StaticFiles
    import uvicorn
except Exception as e:  # pragma: no cover - surfaced by modes/web_mode.py
    # Re-raise so WebMode can print guidance
    raise


INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Iptic Memex - Web</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; }
      #log { white-space: pre-wrap; border: 1px solid #ddd; padding: 1rem; border-radius: 6px; min-height: 200px; }
      .msg { margin: .5rem 0; }
      .role { font-weight: 600; margin-right: .5rem; }
      textarea { width: 100%; height: 90px; }
      button { padding: .5rem 1rem; }
      #status { margin-bottom: 0.75rem; color: #555; font-size: 0.95rem; }
    </style>
  </head>
  <body>
    <h1>Iptic Memex - Web</h1>
    <div id="status">Loading status...</div>
    <div id="log"></div>
    <div id="panel" style="border:1px solid #ddd;border-radius:6px;padding:0.75rem;margin:0.75rem 0;display:none;"></div>
    <div style="margin-top:1rem;">
      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem;">
        <button id="attach" title="Load file" style="padding:.25rem .5rem;">📎 Attach</button>
        <label><input id="stream" type="checkbox" /> Stream</label>
      </div>
      <textarea id="msg" placeholder="Type a message..."></textarea>
      <div><button id="send">Send</button></div>
    </div>

    <script src="/static/app.js"></script>
  </body>
</html>
"""


class WebApp:
    def __init__(self, session, builder=None) -> None:
        self.session = session
        self.builder = builder
        # Ensure chat context exists
        if not self.session.get_context('chat'):
            self.session.add_context('chat')

        # Simple per-process secret for HMAC tokens
        self._secret = secrets.token_bytes(32)
        self._states: Dict[str, 'ActionState'] = {}

        routes = [
            Route('/', self.index, methods=['GET']),
            Route('/api/status', self.api_status, methods=['GET']),
            Route('/api/chat', self.api_chat, methods=['POST']),
            Route('/api/stream', self.api_stream, methods=['GET']),
            Route('/api/action/start', self.api_action_start, methods=['POST']),
            Route('/api/action/resume', self.api_action_resume, methods=['POST']),
            Route('/api/action/cancel', self.api_action_cancel, methods=['POST']),
            Route('/api/upload', self.api_upload, methods=['POST']),
        ]
        self._app = Starlette(routes=routes)
        # Static files (app.js)
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        if not os.path.isdir(static_dir):
            os.makedirs(static_dir, exist_ok=True)
        self._app.mount('/static', StaticFiles(directory=static_dir), name='static')

    # ----- Handlers -----
    async def index(self, request: Request):
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        }
        return HTMLResponse(INDEX_HTML, headers=headers)

    async def api_status(self, request: Request):
        params = self.session.get_params() or {}
        model = params.get('model')
        provider = params.get('provider')
        return JSONResponse({'ok': True, 'model': model, 'provider': provider})

    async def api_chat(self, request: Request):
        try:
            payload: Dict[str, Any] = await request.json()
        except Exception:
            return PlainTextResponse('Invalid JSON', status_code=400)

        message = (payload.get('message') or '').strip()
        if not message:
            return PlainTextResponse('Missing "message"', status_code=400)

        # Check for user command first (CLI parity: handle before adding to chat/LLM)
        try:
            user_cmds = self.session.get_action('user_commands')
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
                        # prepare args the same way (predefined + user tail)
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
            utils = self.session.utils
            original_output = utils.output
            web_output = WebOutput()
            utils.replace_output(web_output)
            # Capture UI emits
            emitted: List[Dict[str, Any]] = []
            original_ui = self.session.ui
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
                        action = self.session.get_action(matched_action)
                        if not action:
                            return JSONResponse({'ok': False, 'error': {'recoverable': False, 'message': f"Unknown action '{matched_action}'"}}, status_code=404)
                        try:
                            # Allow special method-on-action if declared
                            fn = matched_info.get('function', {}) if matched_info else {}
                            if 'method' in fn:
                                meth = getattr(action.__class__, fn['method'])
                                meth(self.session, *matched_args if matched_args else [])
                            else:
                                action.run(matched_args)
                        except Exception as need_exc:
                            # If a stepwise action requested interaction, return needs_interaction
                            from base_classes import InteractionNeeded
                            if isinstance(need_exc, InteractionNeeded):
                                token = self._issue_token(matched_action, 1, need_exc.kind, {"args": {"argv": matched_args}, "content": None})
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
        provider = self.session.get_provider()
        if not provider:
            return PlainTextResponse('No provider available', status_code=500)

        # Ensure streaming disabled for this call
        self.session.set_option('stream', False)

        # Use TurnRunner and suppress stdout via WebOutput
        from web.output_sink import WebOutput
        from turns import TurnRunner, TurnOptions
        utils = self.session.utils
        original_output = utils.output
        web_output = WebOutput()
        utils.replace_output(web_output)
        # Capture UI emits during this call
        emitted: List[Dict[str, Any]] = []
        original_ui = self.session.ui
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
            from base_classes import InteractionNeeded
            runner = TurnRunner(self.session)
            try:
                result = runner.run_user_turn(message, options=TurnOptions(stream=False, suppress_context_print=True))
            except InteractionNeeded as need:
                spec = dict(getattr(need, 'spec', {}) or {})
                action_name = spec.pop('__action__', None) or 'assistant_file_tool'
                args_for_action = spec.pop('__args__', None)
                content_for_action = spec.pop('__content__', None)
                token = self._issue_token(action_name, 1, need.kind, {"args": args_for_action, "content": content_for_action})
                spec['state_token'] = token
                return JSONResponse({
                    "ok": True,
                    "done": False,
                    "needs_interaction": {"kind": need.kind, "spec": spec},
                    "state_token": token,
                    "updates": emitted,
                    "text": str(spec.get('prompt') or spec.get('message') or ''),
                })
        finally:
            try:
                if original_emit:
                    original_ui.emit = original_emit  # type: ignore[attr-defined]
            except Exception:
                pass
            utils.replace_output(original_output)

        visible = result.last_text or ''

        # Optional: usage and cost
        usage = None
        cost = None
        try:
            if hasattr(provider, 'get_usage'):
                usage = provider.get_usage()
            if hasattr(provider, 'get_cost'):
                cost = provider.get_cost()
        except Exception:
            pass

        return JSONResponse({
            'ok': True,
            'text': visible,
            'usage': usage,
            'cost': cost,
            'updates': emitted,
        })

    async def api_stream(self, request: Request):
        """SSE stream: streams assistant tokens for a single message."""
        from web.output_sink import WebOutput
        import asyncio
        import json
        import threading

        message = (request.query_params.get('message') or '').strip()
        if not message:
            return PlainTextResponse('Missing "message"', status_code=400)

        # Early: command handling parity with /api/chat (do not open stream when handled)
        try:
            user_cmds = self.session.get_action('user_commands')
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
            # Execute synchronously and return a single 'done' event with rendered updates
            utils = self.session.utils
            original_output = utils.output
            web_output = WebOutput()
            utils.replace_output(web_output)
            emitted: List[Dict[str, Any]] = []
            original_ui = self.session.ui
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
                        action = self.session.get_action(matched_action)
                        if action:
                            fn = matched_info.get('function', {}) if matched_info else {}
                            if 'method' in fn:
                                meth = getattr(action.__class__, fn['method'])
                                meth(self.session, *matched_args if matched_args else [])
                            else:
                                try:
                                    action.run(matched_args)
                                except Exception as need_exc:
                                    from base_classes import InteractionNeeded
                                    if isinstance(need_exc, InteractionNeeded):
                                        need_kind = need_exc.kind
                                        token = self._issue_token(matched_action, 1, need_kind, {"args": {"argv": matched_args}, "content": None})
                                        spec = dict(need_exc.spec)
                                        spec['state_token'] = token
                                        # Render an instructional text
                                        emitted.append({'type': 'status', 'message': 'Action needs interaction; respond via /api/action/resume.'})
                                        text_lines = ['Action needs interaction; use /api/action/resume with provided token.']
                                        for ev in emitted:
                                            if ev.get('type') in ('status','warning','error') and ev.get('message'):
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
        provider = self.session.get_provider()
        if not provider:
            return PlainTextResponse('No provider available', status_code=500)

        self.session.set_option('stream', True)

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        web_output = WebOutput(loop=loop, queue=queue)

        def run_streaming_turn():
            original_output = self.session.utils.output
            self.session.utils.replace_output(web_output)
            # Capture UI emits during turn to surface in final 'done' event
            emitted: List[Dict[str, Any]] = []
            original_ui = self.session.ui
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
                from turns import TurnRunner, TurnOptions
                from base_classes import InteractionNeeded
                runner = TurnRunner(self.session)
                try:
                    result = runner.run_user_turn(message, options=TurnOptions(stream=True, suppress_context_print=True))
                except InteractionNeeded as need:
                    spec = dict(getattr(need, 'spec', {}) or {})
                    action_name = spec.pop('__action__', None) or 'assistant_file_tool'
                    args_for_action = spec.pop('__args__', None)
                    content_for_action = spec.pop('__content__', None)
                    token = self._issue_token(action_name, 1, need.kind, {"args": args_for_action, "content": content_for_action})
                    spec['state_token'] = token
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "done",
                        "text": str(spec.get('prompt') or spec.get('message') or ''),
                        "needs_interaction": {"kind": need.kind, "spec": spec},
                        "state_token": token,
                        "handled": True,
                        "updates": emitted,
                    })
                    return

                usage = None
                cost = None
                try:
                    if hasattr(provider, 'get_usage'):
                        usage = provider.get_usage()
                    if hasattr(provider, 'get_cost'):
                        cost = provider.get_cost()
                except Exception:
                    pass

                loop.call_soon_threadsafe(queue.put_nowait, {
                    "type": "done",
                    "text": result.last_text or '',
                    "usage": usage,
                    "cost": cost,
                    "updates": emitted,
                })
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
            finally:
                # Restore UI emit and output
                try:
                    if original_emit:
                        original_ui.emit = original_emit  # type: ignore[attr-defined]
                except Exception:
                    pass
                self.session.utils.replace_output(original_output)

        # Start worker thread
        t = threading.Thread(target=run_streaming_turn, daemon=True)
        t.start()

        async def event_generator():
            try:
                while True:
                    event = await queue.get()
                    if not event:
                        continue
                    if event.get('type') == 'token':
                        data = json.dumps({"text": event.get('text', '')})
                        yield f"event: token\ndata: {data}\n\n"
                    elif event.get('type') == 'error':
                        data = json.dumps({"message": event.get('message', '')})
                        yield f"event: error\ndata: {data}\n\n"
                    elif event.get('type') == 'done':
                        data = json.dumps({
                            "text": event.get('text', ''),
                            "usage": event.get('usage'),
                            "cost": event.get('cost'),
                            "needs_interaction": event.get('needs_interaction'),
                            "state_token": event.get('state_token'),
                            "handled": event.get('handled'),
                            "updates": event.get('updates') or [],
                        })
                        yield f"event: done\ndata: {data}\n\n"
                        break
            except Exception:
                return

        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

    # ----- Runner -----
    def start(self, host: str | None = None, port: int | None = None) -> None:
        # Derive defaults from config or fallback
        cfg_host = self.session.get_option('WEB', 'host', fallback='127.0.0.1')
        cfg_port = self.session.get_option('WEB', 'port', fallback=8765)
        try:
            cfg_port = int(cfg_port)
        except Exception:
            cfg_port = 8765
        bind_host = host or cfg_host
        bind_port = int(port or cfg_port)

        uvicorn.run(self._app, host=str(bind_host), port=int(bind_port), log_level='info')

    # ----- Stepwise actions (MVP) --------------------------------------
    @dataclass
    class ActionState:
        session_id: str
        action_name: str
        step: int
        phase: str
        data: Dict[str, Any]
        issued_at: float
        issued_at_str: str
        nonce: str
        used: bool = False
        version: int = 1

    def _session_id(self) -> str:
        # MVP: single user/session
        return 'default'

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def _issue_token(self, action_name: str, step: int, phase: str, data: Dict[str, Any]) -> str:
        issued_at = time.time()
        issued_at_str = f"{issued_at}"
        nonce = secrets.token_hex(8)
        parts = f"{self._session_id()}|{action_name}|{step}|{issued_at_str}|{nonce}".encode('utf-8')
        sig = self._sign(parts)
        token = f"{sig}.{nonce}"
        # Persist state for this token; verification will recompute signature from stored fields
        self._states[token] = WebApp.ActionState(
            session_id=self._session_id(),
            action_name=action_name,
            step=step,
            phase=phase,
            data=data,
            issued_at=issued_at,
            issued_at_str=issued_at_str,
            nonce=nonce,
            used=False,
            version=1,
        )
        return token

    def _verify_token(self, token: str, *, ttl_seconds: int = 900) -> Tuple[Optional['ActionState'], Optional[str]]:
        st = self._states.get(token)
        if not st:
            return None, "Invalid token"
        if st.used:
            return None, "Token already used"
        if st.session_id != self._session_id():
            return None, "Session mismatch"
        if time.time() - st.issued_at > ttl_seconds:
            return None, "Token expired"
        # Integrity check: verify HMAC signature embedded in token matches stored attributes
        try:
            sig_prefix, nonce_from_token = token.split('.', 1)
            if nonce_from_token != st.nonce:
                return None, "Nonce mismatch"
            parts = f"{st.session_id}|{st.action_name}|{st.step}|{st.issued_at_str}|{st.nonce}".encode('utf-8')
            expected = self._sign(parts)
            if not hmac.compare_digest(sig_prefix, expected):
                return None, "Invalid token signature"
        except Exception:
            return None, "Malformed token"
        return st, None

    async def api_action_start(self, request: Request):
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

        action = self.session.get_action(action_name)
        if not action:
            return PlainTextResponse(f"Unknown action '{action_name}'", status_code=404)

        # Drive the action until a boundary (Completed or InteractionNeeded)
        def drive_until_boundary(res: Any, emitted: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
            aggregated: List[Dict[str, Any]] = list(emitted)
            while isinstance(res, Updates):
                aggregated.extend(res.events or [])
                try:
                    res = action.resume("__implicit__", {"continue": True})
                except InteractionNeeded as need2:
                    token2 = self._issue_token(action_name, 1, need2.kind, {"args": args, "content": content})
                    spec2 = dict(need2.spec)
                    spec2['state_token'] = token2
                    return 'needs', {"needs_interaction": {"kind": need2.kind, "spec": spec2}, "updates": aggregated, "state_token": token2}

            if isinstance(res, Completed):
                return 'done', {"payload": res.payload, "updates": aggregated}
            return 'error', {"message": "Unknown result"}

        # Suppress stdout/spinners and capture ui.emit events during web action calls
        utils = self.session.utils
        original_output = utils.output
        web_output = WebOutput()
        utils.replace_output(web_output)
        # Capture UI emits
        emitted: List[Dict[str, Any]] = []
        original_ui = self.session.ui
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
                res = action.start(args, content)
            except InteractionNeeded as need:
                token = self._issue_token(action_name, 1, need.kind, {"args": args, "content": content})
                spec = dict(need.spec)
                spec['state_token'] = token
                return JSONResponse({"ok": True, "done": False, "needs_interaction": {"kind": need.kind, "spec": spec}, "state_token": token, "updates": emitted})
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
                if self.session.get_flag('auto_submit'):
                    self.session.set_flag('auto_submit', False)
                    # Re-process contexts and add synthetic user message
                    pc = self.session.get_action('process_contexts')
                    contexts2 = []
                    if pc and hasattr(pc, 'process_contexts_for_user'):
                        try:
                            contexts2 = pc.process_contexts_for_user(auto_submit=True) or []
                        except Exception:
                            contexts2 = []
                    try:
                        chat = self.session.get_context('chat')
                        chat.add("", 'user', contexts2)
                    except Exception:
                        pass
                    # Run assistant turn
                    provider = self.session.get_provider()
                    from actions.assistant_output_action import AssistantOutputAction
                    utils = self.session.utils
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
                    visible = AssistantOutputAction.filter_full_text(raw_text, self.session)
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

    async def api_action_resume(self, request: Request):
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

        st, err = self._verify_token(token)
        if not st:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": err or 'Invalid token'}}, status_code=400)

        action = self.session.get_action(st.action_name)
        if not action:
            return JSONResponse({"ok": False, "error": {"recoverable": False, "message": f"Unknown action '{st.action_name}'"}}, status_code=404)

        # Mark token used
        st.used = True
        # Helper to drive updates internally
        def drive_until_boundary(res: Any, current_step: int) -> Tuple[str, Dict[str, Any]]:
            aggregated: List[Dict[str, Any]] = []
            while isinstance(res, Updates):
                aggregated.extend(res.events or [])
                try:
                    res = action.resume("__implicit__", {"continue": True})
                except InteractionNeeded as need2:
                    next_step = current_step + 1
                    token2 = self._issue_token(st.action_name, next_step, need2.kind, st.data)
                    spec2 = dict(need2.spec)
                    spec2['state_token'] = token2
                    return 'needs', {"needs_interaction": {"kind": need2.kind, "spec": spec2}, "updates": aggregated, "state_token": token2}

            if isinstance(res, Completed):
                return 'done', {"payload": res.payload, "updates": aggregated}
            return 'error', {"message": "Unknown result"}

        utils = self.session.utils
        original_output = utils.output
        web_output = WebOutput()
        utils.replace_output(web_output)
        # Capture UI emits
        emitted: List[Dict[str, Any]] = []
        original_ui = self.session.ui
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
                token2 = self._issue_token(st.action_name, next_step, need.kind, st.data)
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

    async def api_action_cancel(self, request: Request):
        try:
            payload: Dict[str, Any] = await request.json()
        except Exception:
            return PlainTextResponse('Invalid JSON', status_code=400)
        token = payload.get('state_token') or ''
        if not token:
            return PlainTextResponse('Missing "state_token"', status_code=400)
        st, err = self._verify_token(token)
        if not st:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": err or 'Invalid token'}}, status_code=400)
        st.used = True
        return JSONResponse({"ok": True, "done": True, "cancelled": True})

    async def api_upload(self, request: Request):
        try:
            form = await request.form()
        except Exception as e:
            msg = str(e) or 'Invalid form'
            if 'python-multipart' in msg.lower():
                return JSONResponse({"ok": False, "error": {"recoverable": True, "message": "Missing dependency 'python-multipart' for file uploads."}}, status_code=400)
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": msg}}, status_code=400)
        files = form.getlist('files') if hasattr(form, 'getlist') else []
        if not files:
            return JSONResponse({"ok": False, "error": {"recoverable": True, "message": 'No files in form'}}, status_code=400)
        # Save uploads to a temp dir under web/uploads
        upload_dir = os.path.join(os.path.dirname(__file__), 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        saved = []
        for up in files:
            try:
                filename = getattr(up, 'filename', 'upload.bin')
                # Basic sanitization
                safe = ''.join(ch for ch in filename if ch.isalnum() or ch in ('-', '_', '.', ' ')) or 'upload.bin'
                # Ensure unique name
                base = os.path.splitext(safe)[0]
                ext = os.path.splitext(safe)[1]
                path = os.path.join(upload_dir, safe)
                i = 1
                while os.path.exists(path):
                    path = os.path.join(upload_dir, f"{base}_{i}{ext}")
                    i += 1
                # Write content
                content = await up.read()  # type: ignore[attr-defined]
                with open(path, 'wb') as f:
                    f.write(content)
                saved.append({"name": filename, "path": path, "size": len(content)})
            except Exception as e:
                return JSONResponse({"ok": False, "error": {"recoverable": True, "message": str(e)}}, status_code=500)
        return JSONResponse({"ok": True, "files": saved})
