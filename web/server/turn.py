"""Transport a live chat run across SSE and JSON interaction responses."""

import asyncio
import json
from queue import Empty, Queue
from threading import Timer

from starlette.responses import JSONResponse, StreamingResponse

from app.chat import submit_chat_turn
from core.runtime import InteractionService


class WebTurn:
    def __init__(self, state, message, stream, stream_token=None, upload=None, live_input=False):
        self.state = state
        self.stream_token = stream_token
        self.live_input = live_input
        self.events = Queue()
        self.timeout = None
        self.pending = None
        self.tool_scope = {}
        self.handle = submit_chat_turn(
            state.host, message, stream=stream, interactions=InteractionService(wait=True),
            on_event=self.observe, before_run=upload.load if upload else None,
        )
        def finished(handle):
            if upload:
                upload.close()
            self.events.put(('finished', handle))

        self.handle.add_done_callback(finished)

    def observe(self, event):
        kind, data = event.type, event.data
        if kind == 'assistant_delta':
            self.events.put(('token', {'text': data['text']}))
        elif kind == 'assistant_message':
            self.events.put(('message', {'text': data['text'], 'intermediate': data['intermediate']}))
        elif kind == 'interaction_requested':
            self.events.put(('input', data))
        elif kind in ('status', 'warning', 'error'):
            self.events.put(('update', {**self.tool_scope, 'type': kind, 'message': data.get('message', '')}))
        elif kind in ('tool_started', 'tool_finished'):
            self.tool_scope = {'origin': 'tool', 'tool': data['name'],
                               'tool_call_id': data['tool_call_id']}
            detail = data.get('args', {}).get('desc') or data.get('status', 'started')
            self.events.put(('update', {**self.tool_scope, 'type': 'status',
                                        'message': f"{data['name']}: {detail}"}))
            if kind == 'tool_finished':
                self.tool_scope = {}

    def cancel(self):
        if self.timeout:
            self.timeout.cancel()
        self.handle.cancel('Web turn cancelled')

    def respond(self, request_id, value):
        self.handle.respond(request_id, value)
        self.pending = None
        if self.timeout:
            self.timeout.cancel()
            self.timeout = None

    async def packets(self, request):
        updates = []
        completed = False
        handed_off = False
        try:
            while True:
                try:
                    kind, data = await asyncio.to_thread(self.events.get, True, 0.25)
                except Empty:
                    if await request.is_disconnected():
                        return
                    continue
                if kind == 'finished':
                    completed = True
                    self.pending = None
                    outcome = data.result()
                    yield 'done', dict(ok=outcome.status != 'failed', done=True,
                                       status=outcome.status, text=outcome.last_text or '',
                                       error=outcome.error, cancelled=outcome.status == 'cancelled',
                                       stop_reason=outcome.stop_reason, updates=updates)
                    return
                if kind == 'input':
                    token = self.state.issue_token('chat', 1, 'input', {'id': data['id']})
                    # An abandoned approval must eventually release the worker.
                    self.timeout = Timer(self.state.ttl, self.cancel)
                    self.timeout.daemon = True
                    self.timeout.start()
                    spec = {k: v for k, v in data['spec'].items() if not k.startswith('__')}
                    self.pending = dict(needs_interaction={'kind': data['kind'], 'spec': spec},
                                        state_token=token)
                    if self.live_input:
                        yield 'interaction', self.pending
                        continue
                    handed_off = True
                    yield 'done', dict(ok=True, done=False, handled=True, text='',
                                       updates=updates, **self.pending)
                    return
                if kind == 'update':
                    updates.append(data)
                yield kind, data
        finally:
            if not handed_off and not completed:
                self.cancel()

    async def response(self, request, stream=False):
        if stream:
            async def generate():
                async for kind, data in self.packets(request):
                    yield sse(kind, data)
            return StreamingResponse(generate(), media_type='text/event-stream',
                                     headers={'Cache-Control': 'no-cache'})
        async for kind, data in self.packets(request):
            if kind == 'done':
                return JSONResponse(data, status_code=200 if data['ok'] else 502)
        return JSONResponse({'ok': False, 'error': 'Disconnected'}, status_code=409)


def sse(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data)}\n\n'
