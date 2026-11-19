"""Render shared chat runs on Textual's event loop."""

import asyncio
from contextvars import copy_context

from app.chat import submit_chat_turn
from core.runtime import InteractionService
from tui.screens.interaction_modal import InteractionModal


class TurnExecutor:
    def __init__(self, host, emit_status, stream_enabled, observe):
        self.observe = observe
        self.host = host
        self._emit_status = emit_status
        self._stream_enabled = stream_enabled
        self._chat_view = None
        self.active_message_id = None
        self.handle = None
        self.modal = None
        self.tool_message = None

    def set_chat_view(self, view):
        self._chat_view = view

    def set_stream_enabled(self, enabled):
        self._stream_enabled = enabled

    def cancel(self):
        if self.handle is None:
            return False
        self.handle.cancel('Cancelled by user')
        return True

    async def run(self, message):
        loop = asyncio.get_running_loop()
        ui_context = copy_context()
        try:
            self.handle = submit_chat_turn(
                self.host, message, stream=self._stream_enabled,
                on_event=lambda event: loop.call_soon_threadsafe(self._render, event, message, context=ui_context),
                interactions=InteractionService(wait=True),
            )
            outcome = await asyncio.to_thread(self.handle.result)
            if outcome.status != 'completed':
                self._emit_status(outcome.error or outcome.stop_reason or outcome.status, 'warning')
            return outcome
        except Exception as exc:
            self._emit_status(str(exc), 'error')
        finally:
            self.handle = None
            self.active_message_id = None
            self.tool_message = None
            if (self.modal and self._chat_view and self.modal.is_mounted
                    and self.modal in self._chat_view.app.screen_stack[1:]):
                self.modal.dismiss(None)
            self.modal = None

    def _render(self, event, message):
        view = self._chat_view
        if not view or not self.handle:
            return
        if event.type != 'assistant_delta':
            self.observe(event, message)
        data = event.data
        if event.type == 'interaction_requested':
            self._ask(data)
        elif event.type in ('assistant_delta', 'assistant_message'):
            text = data.get('text') or ''
            if text and self.active_message_id is None:
                self.active_message_id = view.add_message('assistant', '', streaming=True)
            if self.active_message_id:
                if event.type == 'assistant_delta':
                    view.append_text(self.active_message_id, text)
                else:
                    view.update_message(self.active_message_id, text, streaming=False)
                    self.active_message_id = None
        elif event.type == 'tool_started':
            title = data['args'].get('desc') or data['name']
            self.tool_message = view.add_message('tool', title)
        elif event.type == 'tool_finished':
            if self.tool_message:
                detail = (data.get('result') or {}).get('error') or data['status']
                view.append_text(self.tool_message, f'\n{detail}')
                view.stop_streaming(self.tool_message)
            self.tool_message = None
        elif event.type in ('warning', 'error', 'status') and data.get('message'):
            if self.tool_message:
                view.append_text(self.tool_message, '\n' + data['message'])
            else:
                self._emit_status(data['message'], event.type if event.type != 'status' else 'info')

    def _ask(self, request):
        handle = self.handle

        def answer(value):
            self.modal = None
            if handle is not self.handle:
                return
            if value is None:
                handle.cancel('Input cancelled')
                return
            try:
                handle.respond(request['id'], value)
            except ValueError as exc:
                self._emit_status(str(exc), 'warning')
                if handle.snapshot()['pending_input']:
                    self._ask(request)

        self.modal = InteractionModal(request['kind'], request['spec'])
        self._chat_view.app.push_screen(self.modal, answer)
