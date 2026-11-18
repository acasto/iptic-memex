"""Dispatch Web commands and retain the exact action across input boundaries."""

import asyncio
import re
from dataclasses import dataclass
from functools import partial

from starlette.responses import JSONResponse

from app.chat import attach_startup_files
from base_classes import Completed, InteractionNeeded, Updates
from core.runtime import InteractionService
from core.runtime.contracts import InteractionRequest
from web.routes.chat import error
from web.server.turn import WebTurn


@dataclass
class Command:
    name: str
    action: object
    operation: object
    need: InteractionNeeded | None = None
    upload: object = None

    def step(self, session, updates):
        def emit(kind, data):
            updates.append({**data, 'type': kind})

        with session._execution_lock, session.runtime_events.bind(emit):
            if self.upload:
                self.upload.load(session)
                self.upload = None
            result = self.operation()
            while isinstance(result, Updates):
                updates.extend(result.events)
                result = self.action.resume('__implicit__', {'continue': True})
            return result


async def run_command(app, request, cmd):
    state = app._webstate
    state.active = cmd
    updates = []
    try:
        result = await asyncio.to_thread(cmd.step, app.session, updates)
    except InteractionNeeded as need:
        cmd.need = need
        token = state.issue_token(cmd.name, 1, 'command', {})
        state.command_token = token
        spec = {k: v for k, v in need.spec.items() if not k.startswith('__')}
        return dict(ok=True, done=False, command=cmd.name, handled=True, updates=updates,
                    needs_interaction={'kind': need.kind, 'spec': spec}, state_token=token), 200
    except Exception as exc:
        return {'ok': False, 'error': {'message': str(exc)}, 'updates': updates}, 500
    finally:
        if state.command_token is None:
            state.active = None
            attach_startup_files(app.session)
    payload = result.payload if isinstance(result, Completed) else result
    if not isinstance(payload, dict):
        payload = {'result': payload}
    data = dict(ok=payload.get('ok', True), done=True, handled=True, command=cmd.name,
                updates=updates, payload=payload,
                text='\n'.join(str(u['message']) for u in updates if u.get('message')))
    if app.session.get_flag('auto_submit'):
        app.session.set_flag('auto_submit', False)
        if app.session.get_option('TOOLS', 'allow_auto_submit', fallback=True):
            turn = state.active = WebTurn(state, '', False)
            async for kind, reply in turn.packets(request):
                if kind == 'done':
                    data.update(reply, updates=updates + reply['updates'])
    return data, 502 if data.get('status') == 'failed' else 200


async def command(app, request, message, upload=None):
    app.session.pick_session_on_start = False
    parser = app.session.get_action('chat_commands')
    parsed = parser.match(message) if parser else None
    if not parsed or parsed['kind'] == 'error':
        return {'ok': False, 'error': {'message': (parsed or {}).get('message', 'Unknown command')}}, 400
    if parsed['kind'] == 'builtin':
        name = parsed['method']
        if name == 'handle_quit':
            return {'ok': True, 'done': True, 'handled': True, 'text': 'Close this tab to leave chat.'}, 200
        action = parser
        operation = partial(getattr(parser, name), *parsed['argv'])
    else:
        name = parsed['action']
        action = app.session.get_action(name)
        if not action:
            return {'ok': False, 'error': {'message': 'Command unavailable'}}, 404
        argv = [*parsed['args'], *parsed['argv']]
        method = parsed.get('method')
        operation = partial(getattr(action, method), *argv) if method else partial(action.run, argv)
    return await run_command(app, request, Command(name, action, operation, upload=upload))


async def handle_api_action_start(app, request):
    try:
        payload = await request.json()
        name = payload['action'].strip()
    except (ValueError, KeyError, AttributeError, TypeError):
        return error('Expected an action name', 400)
    if app._webstate.busy():
        return error('Finish or cancel the current operation first.', 409)
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        return error('Invalid action name', 400)
    action = app.session.get_action(name)
    if not action:
        return error('Unknown action', 404)
    if name == 'load_file' and payload.get('upload_token'):
        from web.routes.upload import Upload
        try:
            upload = Upload(app._webstate, payload['upload_token'])
        except ValueError as exc:
            return error(str(exc), 400)
        data, status = await run_command(app, request, Command(name, action, partial(upload.load, app.session)))
        return JSONResponse(data, status_code=status)
    args = payload.get('args') or {}
    operation = partial(action.run, args)
    if payload.get('content') is not None:
        operation = partial(action.run, args, payload['content'])
    data, status = await run_command(app, request, Command(name, action, operation))
    return JSONResponse(data, status_code=status)


async def interaction(app, request, *, cancel):
    try:
        payload = await request.json()
        token = payload['state_token']
    except (ValueError, KeyError, TypeError):
        return error('Expected an interaction token', 400)
    state = app._webstate
    st, reason = state.verify_token(token)
    if not st or st.phase not in ('command', 'input'):
        return error(reason or 'Invalid interaction token', 400)
    active = state.active
    value = payload.get('response')
    if st.phase == 'input' and isinstance(active, WebTurn):
        pending = active.handle.snapshot()['pending_input']
        if not pending or pending['id'] != st.data['id']:
            return error('Stale interaction', 400)
        if cancel:
            active.cancel()
        else:
            try:
                active.respond(st.data['id'], value)
            except ValueError as exc:
                return error(str(exc), 400)
        st.used = True
        if active.live_input:
            return JSONResponse({'ok': True, 'accepted': True})
        return await active.response(request)
    if not isinstance(active, Command) or token != state.command_token:
        return error('Stale interaction', 400)
    need = active.need
    if not cancel:
        try:
            InteractionService.validate(InteractionRequest(token, need.kind, need.spec), value)
        except ValueError as exc:
            return error(str(exc), 400)
    st.used = True
    state.command_token = None
    if cancel:
        state.active = None
        attach_startup_files(app.session)
        return JSONResponse({'ok': True, 'done': True, 'cancelled': True})
    active.operation = partial(active.action.resume, need.state_token, {'response': value})
    data, status = await run_command(app, request, active)
    return JSONResponse(data, status_code=status)


async def handle_api_action_resume(app, request):
    return await interaction(app, request, cancel=False)


async def handle_api_action_cancel(app, request):
    return await interaction(app, request, cancel=True)
