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

from typing import Any, Dict
import os

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
    <div style="margin-top:1rem;">
      <textarea id="msg" placeholder="Type a message..."></textarea>
      <div>
        <label><input id="stream" type="checkbox" /> Stream</label>
      </div>
      <button id="send">Send</button>
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

        routes = [
            Route('/', self.index, methods=['GET']),
            Route('/api/status', self.api_status, methods=['GET']),
            Route('/api/chat', self.api_chat, methods=['POST']),
            Route('/api/stream', self.api_stream, methods=['GET']),
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

        # Process contexts (non-interactive path) similar to CompletionMode
        process_contexts = self.session.get_action('process_contexts')
        contexts = process_contexts.get_contexts(self.session) if process_contexts else []

        # Add chat context and message
        chat = self.session.get_context('chat')
        chat.add(message, 'user', contexts)

        # Non-stream MVP: call provider.chat()
        provider = self.session.get_provider()
        if not provider:
            return PlainTextResponse('No provider available', status_code=500)

        # Ensure streaming disabled for this call
        self.session.set_option('stream', False)

        # Swap output sink to suppress spinners/stdout noise during web requests
        from web.output_sink import WebOutput
        utils = self.session.utils
        original_output = utils.output
        utils.replace_output(WebOutput())
        try:
            raw_text = provider.chat()
        except Exception as e:
            return PlainTextResponse(f'Provider error: {e}', status_code=500)
        finally:
            # Restore original output handler
            utils.replace_output(original_output)

        if raw_text is None:
            raw_text = ''

        # Apply display-side filters for parity with CLI
        from actions.assistant_output_action import AssistantOutputAction
        visible = AssistantOutputAction.filter_full_text(raw_text, self.session)
        sanitized = AssistantOutputAction.filter_full_text_for_return(raw_text, self.session)

        # Record assistant message
        chat.add(raw_text, 'assistant')

        # Run assistant commands server-side (non-interactive)
        try:
            assistant_cmds = self.session.get_action('assistant_commands')
            if assistant_cmds:
                # Suppress spinners/stdout during tool execution as well
                utils = self.session.utils
                original_output = utils.output
                utils.replace_output(WebOutput())
                try:
                    assistant_cmds.run(sanitized)
                finally:
                    utils.replace_output(original_output)
        except Exception:
            # Swallow tool errors; they will be added as contexts by the action when applicable
            pass

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

        # Prepare contexts similar to /api/chat
        process_contexts = self.session.get_action('process_contexts')
        contexts = process_contexts.get_contexts(self.session) if process_contexts else []
        chat = self.session.get_context('chat')
        chat.add(message, 'user', contexts)

        provider = self.session.get_provider()
        if not provider:
            return PlainTextResponse('No provider available', status_code=500)

        self.session.set_option('stream', True)

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        web_output = WebOutput(loop=loop, queue=queue)

        def run_streaming_turn():
            # Runs in a background thread; must not use async APIs directly
            original_output = self.session.utils.output
            self.session.utils.replace_output(web_output)
            try:
                # Produce stream
                try:
                    stream = provider.stream_chat()
                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
                    return

                from actions.assistant_output_action import AssistantOutputAction
                out_action = AssistantOutputAction(self.session)
                try:
                    raw_text = out_action.run(stream, spinner_message="")
                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
                    raw_text = ""

                # Record assistant message
                try:
                    chat.add(raw_text, 'assistant')
                except Exception:
                    pass

                # Tool execution
                sanitized = out_action.get_sanitized_output() or raw_text
                try:
                    assistant_cmds = self.session.get_action('assistant_commands')
                    if assistant_cmds:
                        assistant_cmds.run(sanitized)
                except Exception as e:
                    # Surface tool error as a system message event; non-fatal
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})

                # Gather usage/cost
                usage = None
                cost = None
                try:
                    if hasattr(provider, 'get_usage'):
                        usage = provider.get_usage()
                    if hasattr(provider, 'get_cost'):
                        cost = provider.get_cost()
                except Exception:
                    pass

                # Finalize visible text from display buffer for parity
                visible_full = out_action.get_display_output() or raw_text
                # Send done event
                loop.call_soon_threadsafe(queue.put_nowait, {
                    "type": "done",
                    "text": visible_full,
                    "sanitized": sanitized,
                    "usage": usage,
                    "cost": cost,
                })
            finally:
                # Ensure we restore output sink
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
                            "sanitized": event.get('sanitized', ''),
                            "usage": event.get('usage'),
                            "cost": event.get('cost'),
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
