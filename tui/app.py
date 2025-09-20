"""Textual application for the iptic-memex TUI mode."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Awaitable, Dict, List, Optional, Tuple

try:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Static
    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - surfaced when textual missing
    TEXTUAL_AVAILABLE = False

from base_classes import Completed, InteractionNeeded
from core.turns import TurnRunner
from tui.commands import CommandController
from tui.input_completion import InputCompletionManager
from tui.models import CommandItem
from tui.output_bridge import OutputBridge
from tui.output_sink import OutputEvent, TuiOutput
from tui.turn_executor import TurnExecutor

from utils.output_utils import OutputHandler


if TEXTUAL_AVAILABLE:
    from tui.screens.command_palette import CommandPalette
    from tui.screens.interaction_modal import InteractionModal
    from tui.screens.reader_overlay import ReaderOverlay
    from tui.utils.clipboard import ClipboardHelper, ClipboardOutcome
    from tui.utils.ui_bridge import UIEventProxy
    from tui.widgets.chat_transcript import ChatTranscript
    from tui.widgets.command_hint import CommandHint
    from tui.widgets.context_summary import ContextSummary
    from tui.widgets.status_panel import StatusPanel
    from tui.widgets.chat_input import ChatInput
    from tui.screens.status_modal import StatusModal
    from textual.widgets import TextArea

    class MemexTUIApp(App):
        """Main Textual app providing a richer chat experience."""

        CSS_PATH = "styles/app.tcss"

        LAYERS = ("main", "modal")

        BINDINGS = [
            Binding("ctrl+q", "quit", "Quit"),
            Binding("ctrl+s", "toggle_stream", "Toggle stream"),
            Binding("ctrl+k", "open_commands", "Commands"),
            Binding("f8", "show_status", "Status", priority=True),
            Binding("ctrl+s", "cancel_turn", "Cancel turn", priority=True),
            Binding("meta+c", "cancel_turn", "Cancel turn", show=False, priority=True),
            Binding("f7", "open_reader", "Reader", priority=True),
            Binding("ctrl+shift+c", "copy_current_message", "Copy message", show=False, priority=True),
        ]

        def __init__(self, session, builder=None) -> None:
            super().__init__()
            self.session = session
            self.builder = builder
            self.turn_runner = TurnRunner(self.session)

            self.chat_view: Optional[ChatTranscript] = None
            self.status_log: Optional[StatusPanel] = None
            self.context_panel: Optional[ContextSummary] = None
            self.input: Optional[ChatInput] = None
            self.command_registry = self.session.get_action('user_commands_registry')
            self.command_hint: Optional[CommandHint] = None
            self._command_controller = CommandController()
            self._input_completion = InputCompletionManager()

            self._chat_role_titles: Dict[str, str] = {}
            self._chat_role_styles: Dict[str, str] = {}
            params = self.session.get_params() or {}
            self._apply_role_labels(params)

            self._stream_enabled: bool = bool((self.session.get_params() or {}).get('stream'))
            self._pending_tasks: set[asyncio.Task[Any]] = set()

            self._ui_adapter = getattr(self.session, 'ui', None)
            # Ensure we can receive UI.emit events even if the adapter lacks a hook
            if hasattr(self._ui_adapter, 'set_event_handler'):
                try:
                    self._ui_adapter.set_event_handler(self._handle_ui_event)
                except Exception:
                    pass
            else:
                try:
                    proxy = UIEventProxy(self._ui_adapter, self._handle_ui_event)
                    self.session.ui = proxy  # route future emits through the proxy
                    self._ui_adapter = proxy
                except Exception:
                    pass
            try:
                self._status_max_lines = int(self.session.get_option('TUI', 'status_max_lines', fallback=200))
            except Exception:
                self._status_max_lines = 200

            self._output_bridge = OutputBridge(status_limit=self._status_max_lines)
            self.turn_executor = TurnExecutor(
                self.session,
                self.turn_runner,
                schedule_task=self._schedule_task,
                emit_status=self._emit_status,
                refresh_contexts=self._refresh_context_panel,
                call_in_app_thread=self.call_from_thread,
                stream_enabled=self._stream_enabled,
            )

            self._orig_output: Optional[OutputHandler] = self.session.utils.output
            self._tui_output = TuiOutput(self._handle_output_event)
            self.session.utils.replace_output(self._tui_output)

            self._reader_overlay: Optional[ReaderOverlay] = None
            self._clipboard_helper = ClipboardHelper()
            self._last_clipboard_outcome: Optional[ClipboardOutcome] = None
            self._copy_warning_threshold = 100_000
            self._reader_open = False
            self._status_modal: Optional[StatusModal] = None
            self._status_open = False
            self._base_screen = None
            # Header spinner removed: simplify status bar (no animated glyph)

        # ----- layout --------------------------------------------------
        def compose(self) -> ComposeResult:
            params = self.session.get_params() or {}
            model_name = params.get('model', 'unknown')
            provider = params.get('provider', 'unknown')
            status = Static(
                self._status_bar_text(model_name, provider, self._stream_enabled),
                id="status_bar",
            )
            yield status

            with Vertical(id="main_content"):
                self.chat_view = ChatTranscript(
                    id="chat_transcript",
                    role_titles=self._chat_role_titles,
                    role_styles=self._chat_role_styles,
                )
                self.chat_view.styles.height = "1fr"
                self._output_bridge.set_chat_view(self.chat_view)
                self.turn_executor.set_chat_view(self.chat_view)
                yield self.chat_view

            self.command_hint = CommandHint(id="command_hint")
            self._input_completion.set_command_hint(self.command_hint)
            yield self.command_hint

            with Vertical(id="input_row"):
                with Horizontal(id="input_controls"):
                    self.input = ChatInput(placeholder="Type a message or '/' for commands")
                    self._input_completion.set_input(self.input)
                    yield self.input

            yield Footer()

        async def on_mount(self) -> None:
            if self.input:
                self.set_focus(self.input)
            if self.chat_view:
                try:
                    self.chat_view.can_focus = False
                except Exception:
                    pass

            self._emit_status("Welcome to iptic-memex TUI. Press Ctrl+S to toggle streaming.", 'info')
            try:
                params = self.session.get_params() or {}
                self.session.utils.logger.tui_event('start', {'model': params.get('model'), 'provider': params.get('provider')})
            except Exception:
                pass
            self._load_commands()
            self._refresh_context_panel()
            self._render_existing_history()
            self._base_screen = self.screen
            # No header spinner timer needed
            # Inline suggester disabled; CommandHint drives suggestions

        # ----- scheduling helpers -------------------------------------
        def _schedule_task(self, coro: Awaitable[Any]) -> None:
            task = asyncio.create_task(coro)
            self._pending_tasks.add(task)

            def _cleanup_task(t: asyncio.Task[Any]) -> None:
                self._pending_tasks.discard(t)

            task.add_done_callback(_cleanup_task)

        # ----- output handling ----------------------------------------
        def _handle_output_event(self, event: OutputEvent) -> None:
            active_id = getattr(self.turn_executor, "active_message_id", None)
            self.call_from_thread(self._output_bridge.handle_output_event, event, active_id)

        def _handle_ui_event(self, event_type: str, data: Dict[str, Any]) -> None:
            scope_meta = None
            try:
                cur_scope = getattr(self._tui_output, 'current_scope', None)
                if callable(cur_scope):
                    scope_meta = cur_scope()
            except Exception:
                scope_meta = None
            if event_type in {'status', 'warning', 'error', 'critical'}:
                message = str(data.get('message') or data.get('text') or '').strip()
                if message:
                    level = event_type if event_type in {'warning', 'error', 'critical'} else 'info'
                    text = message if message.endswith('\n') else message + '\n'
                    # Optional explicit scope from actions: 'command'|'system'|'tool'
                    explicit = str(data.get('scope') or '').strip().lower()
                    origin = 'system'
                    if explicit in {'tool', 'command', 'system'}:
                        origin = explicit
                    elif scope_meta:
                        origin = scope_meta.get('origin') or 'system'
                    evt = OutputEvent(
                        type='write',
                        text=text,
                        level=level,
                        origin=origin,
                        tool_name=scope_meta.get('tool_name'),
                        tool_call_id=scope_meta.get('tool_call_id'),
                        tool_title=(data.get('title') or data.get('label') or scope_meta.get('title') or scope_meta.get('tool_title')),
                    )
                    active_id = getattr(self.turn_executor, "active_message_id", None)
                    self.call_from_thread(self._output_bridge.handle_output_event, evt, active_id)
                    return
            self.call_from_thread(self._output_bridge.handle_ui_event, event_type, data)

        # ----- input handling -----------------------------------------
        async def on_chat_input_send_requested(self, message: "ChatInput.SendRequested") -> None:
            # Detail log: record that Enter was pressed and whether a suggestion is being accepted
            try:
                text_preview = (message.text or '')[:64]
                self.session.utils.logger.tui_detail('input_send', {
                    'has_slash': bool((message.text or '').lstrip().startswith('/')),
                    'len': len(message.text or ''),
                    'preview': text_preview,
                }, component='tui.input')
            except Exception:
                pass
            def _focus_input():
                if self.input:
                    self.set_focus(self.input)

            if self._input_completion.accept_suggestion(
                _focus_input, self._command_controller.find_command_suggestions
            ):
                try:
                    self.session.utils.logger.tui_detail('accept_suggestion', {
                        'accepted': True,
                    }, component='tui.input')
                except Exception:
                    pass
                message.stop()
                return
            message.stop()
            try:
                self.session.utils.logger.tui_detail('accept_suggestion', {
                    'accepted': False,
                }, component='tui.input')
            except Exception:
                pass
            self._submit_input_text(message.text)

        def on_text_area_changed(self, message: TextArea.Changed) -> None:
            if message.control is not self.input:
                return
            value = message.control.text or ''
            self._input_completion.handle_input_changed(
                value,
                find_command_suggestions=self._command_controller.find_command_suggestions,
            )

        def _submit_input_text(self, raw_text: str) -> None:
            text = (raw_text or '').strip()
            if not text:
                return
            if self.input:
                self._input_completion.mark_programmatic_update()
                self.input.clear_and_focus()
            self._clear_command_hint()
            self._handle_user_input(text)

        def _handle_user_input(self, message: str) -> None:
            if message.startswith('/'):
                self._schedule_task(self._dispatch_slash_command(message))
                return
            if self.chat_view:
                self.chat_view.add_message('user', message)

            async def _run_and_handle_turn() -> None:
                result = await self.turn_executor.run(message)
                self._handle_turn_result(result)

            self._schedule_task(_run_and_handle_turn())

        def _handle_turn_result(self, result: Any) -> None:
            if result:
                self._handle_context_events(result)
            self._refresh_context_panel()
            self.turn_executor.check_auto_submit()
            # After the user turn (and summaries) have been handled, close the
            # current command group so future statuses start a fresh bubble.
            try:
                self._output_bridge.end_command_group()
            except Exception:
                pass

        def _handle_context_events(self, result: Any) -> None:
            events = getattr(result, 'context_events', None)
            if not events:
                return
            for event in events:
                message = event.get('message')
                if message:
                    self._emit_status(message, 'info')

        async def _dispatch_slash_command(self, text: str) -> None:
            registry = self.command_registry
            if not registry:
                self._emit_status('Command registry unavailable.', 'error', role='command')
                return
            try:
                chat_commands = self.session.get_action('chat_commands')
            except Exception:
                chat_commands = None
            if not chat_commands:
                self._emit_status('Commands action unavailable.', 'error', role='command')
                return
            try:
                self.session.utils.logger.tui_detail('command_parse_start', {
                    'text_len': len(text or ''),
                    'text_preview': (text or '')[:64],
                }, component='tui.commands')
                parsed = chat_commands.match(text)
            except Exception as exc:
                self._emit_status(f'Command error: {exc}', 'error', role='command')
                try:
                    self.session.utils.logger.tui_event('command_parse_error', {
                        'error': str(exc),
                    }, component='tui.commands')
                except Exception:
                    pass
                return
            if not parsed:
                return
            if parsed.get('kind') == 'error':
                try:
                    self._output_bridge.end_command_group()
                except Exception:
                    pass
                self._emit_status(parsed.get('message', 'Invalid command'), 'warning', role='system')
                try:
                    self.session.utils.logger.tui_event('command_parse_invalid', {
                        'message': parsed.get('message'),
                    }, component='tui.commands')
                except Exception:
                    pass
                return
            handler = {
                'type': parsed.get('kind'),
                'action': parsed.get('action'),
                'method': parsed.get('method'),
                'args': parsed.get('args') or [],
            }
            path = parsed.get('path') or []
            argv = parsed.get('argv') or []
            try:
                self.session.utils.logger.tui_event('command_parsed', {
                    'path': ' '.join([p for p in path if p]),
                    'argv_count': len(argv or []),
                }, component='tui.commands')
            except Exception:
                pass
            result = await self._execute_command_handler(handler, path, argv)
            self._handle_turn_result(result)

        # ----- commands ------------------------------------------------
        def _load_commands(self) -> None:
            self._command_controller.load(self.command_registry)
            self._input_completion.update_catalog(
                self._command_controller.top_level_commands,
                self._command_controller.subcommand_map,
            )

        def _reload_commands(self) -> None:
            """Rebuild command specs from the registry to reflect gating changes.

            Useful after toggles like `/mcp on|off` which alter can_run gates.
            """
            try:
                # Recreate the registry action to allow it to re-evaluate gates
                self.command_registry = self.session.get_action('user_commands_registry')
            except Exception:
                pass
            self._load_commands()

        def action_open_commands(self) -> None:
            if self.command_hint:
                self.command_hint.styles.visibility = "hidden"
            if not self._command_controller.has_commands:
                self._emit_status('No commands available.', 'warning', role='command')
                return
            palette = CommandPalette(self._command_controller.command_items)
            self.push_screen(palette, self._on_command_selected)

        def _on_command_selected(self, command: Optional[CommandItem]) -> None:
            if not command:
                return

            async def _run_and_handle_command() -> None:
                result = await self._execute_command_item(command)
                self._handle_turn_result(result)

            self._schedule_task(_run_and_handle_command())
            if self.input:
                self._input_completion.mark_programmatic_update()
                self.input.value = ''
                self.set_focus(self.input)
            self._clear_command_hint()

        async def _execute_command_item(self, command: CommandItem) -> Any:
            handler = command.handler or {}
            return await self._execute_command_handler(handler, command.path, [])

        async def _execute_command_handler(self, handler: Dict[str, Any], path: List[str], extra_argv: Optional[List[str]]) -> Any:
            if not handler:
                self._emit_status('Invalid command handler.', 'error', display=False)
                return None
            # Decide scope: system vs command
            def _is_system_scoped(h: Dict[str, Any], p: List[str]) -> bool:
                ui = (h or {}).get('ui') or {}
                scope = (ui.get('status_scope') or '').strip().lower() if isinstance(ui, dict) else ''
                if scope == 'system':
                    return True
                # Fallback static set
                primary = (p[0] if p else '') or ''
                secondary = (p[1] if len(p) > 1 else '') or ''
                sys_pairs = {
                    ('show', 'usage'), ('show', 'cost'), ('show', 'models'), ('show', 'messages'), ('show', 'contexts'),
                    ('rag', 'status'), ('mcp', 'status'), ('mcp', 'doctor'), ('reprint', ''),
                }
                return (primary, secondary) in sys_pairs

            def _command_label(p: List[str]) -> str:
                bits = [seg for seg in (p or []) if seg]
                return '/' + ' '.join(bits) if bits else '/command'
            kind = handler.get('type')
            if kind == 'builtin' or kind == 'builtin-inner':
                use_system = _is_system_scoped(handler, path)
                scope_cm = self._tui_output.command_scope(_command_label(path)) if (not use_system and kind == 'builtin') else None
                # Do not end an existing command group; we want multiple
                # commands in the same user phase to share one bubble.
                if scope_cm:
                    with scope_cm:
                        return await self._execute_command_handler({**handler, 'type': 'builtin-inner'}, path, extra_argv)
                if path and path[0] == 'quit':
                    self.action_quit()
                    return None
                try:
                    self.session.utils.logger.tui_event('command_builtin_start', {
                        'path': ' '.join([p for p in path if p]),
                        'argv_count': len(list(extra_argv or [])),
                    }, component='tui.commands')
                    ok, err = await asyncio.to_thread(self.command_registry.execute, path, {'argv': list(extra_argv or [])})
                except SystemExit:
                    self.action_quit()
                    return None
                try:
                    self.session.utils.logger.tui_event('command_builtin_done', {
                        'path': ' '.join(path or []),
                        'ok': bool(ok),
                        'error': err or None,
                    }, component='tui.commands')
                except Exception:
                    pass
                if not ok:
                    self._emit_status(err or 'Command failed.', 'error', role='command')
                # Command gating may have changed (e.g., /mcp on|off)
                self._reload_commands()
                self._refresh_context_panel()
                self.turn_executor.check_auto_submit()
                self._clear_command_hint()
                return None

            action_name = handler.get('action') or handler.get('name')
            if not action_name:
                self._emit_status('Command missing action mapping.', 'error', display=False)
                return None
            action = self.session.get_action(action_name)
            if not action:
                self._emit_status(f"Unknown action '{action_name}'.", 'error', display=False)
                return None

            fixed_args = list(handler.get('args') or [])
            if isinstance(fixed_args, str):
                fixed_args = [fixed_args]
            argv = list(str(a) for a in fixed_args)

            if action_name == 'set_model' and not argv and not (extra_argv and any(str(a).strip() for a in extra_argv)):
                chosen = await self._prompt_model_choice()
                if not chosen:
                    self._emit_status('Model selection cancelled.', 'warning', display=False)
                    self._clear_command_hint()
                    return None
                argv.append(chosen)

            # Intercept bare /clear to behave like Ctrl+L and just clear the view.
            if path and path[0] == 'clear' and (len(path) < 2 or not path[1]):
                if self.chat_view:
                    self.chat_view.clear_messages()
                self._emit_status('Screen cleared.', 'info', role='command')
                self._clear_command_hint()
                return None

            if action_name == 'manage_chats':
                handled = await self._handle_manage_chats_command(action, path, argv, extra_argv)
                if handled:
                    if self.chat_view:
                        self.chat_view.clear_messages()
                        self._render_existing_history()
                    self._refresh_context_panel()
                    self.turn_executor.check_auto_submit()
                    self._clear_command_hint()
                    return None

            if extra_argv:
                argv.extend(str(a) for a in extra_argv)

            method_name = handler.get('method')
            if method_name:
                fn = getattr(action, method_name, None)
                if callable(fn):
                    result = await asyncio.to_thread(fn, *argv)
                else:
                    cls_fn = getattr(action.__class__, method_name, None)
                    if callable(cls_fn):
                        result = await asyncio.to_thread(cls_fn, self.session, *argv)
                    else:
                        self._emit_status(f"Handler method '{method_name}' not found.", 'error', display=False)
                        return None
                self._emit_status('Command executed.', 'info', display=False)
                try:
                    self.session.utils.logger.tui_event('command_action', {'path': ' '.join(path or []), 'action': action_name, 'method': method_name, 'argv': argv})
                except Exception:
                    pass
                self._handle_command_result(path, result)
                self._refresh_context_panel()
                self.turn_executor.check_auto_submit()
                self._clear_command_hint()
                return result

            if action_name == 'load_file':
                has_path_arg = any((str(arg).strip()) for arg in argv)
                if not has_path_arg:
                    self._emit_status('Provide a file path before loading.', 'warning', display=False)
                    self._emit_status('Add a file path or use Tab to complete one, then press Enter.', 'warning', role='command')
                    command_bits = [segment for segment in (path or []) if segment]
                    if command_bits:
                        base = '/' + ' '.join(command_bits)
                    else:
                        base = '/load file'
                    if not base.endswith(' '):
                        base = base + ' '
                    if self.input:
                        try:
                            self._input_completion.mark_programmatic_update()
                            self.input.value = base
                            if hasattr(self.input, 'cursor_position'):
                                self.input.cursor_position = len(base)
                        except Exception:
                            pass
                    return None

            prev_model = (self.session.get_params() or {}).get('model') if action_name == 'set_model' else None
            # Wrap action in command scope if not system-scoped
            use_system = _is_system_scoped(handler, path)
            scope_cm = self._tui_output.command_scope(_command_label(path)) if not use_system else None
            # Do not end existing command group here; we keep aggregating
            try:
                self.session.utils.logger.tui_event('command_action_start', {
                    'path': ' '.join(path or []),
                    'action': action_name,
                    'argv_count': len(argv),
                }, component='tui.commands')
            except Exception:
                pass
            # Detail: log argv
            try:
                self.session.utils.logger.tui_detail('command_action_args', {
                    'path': ' '.join(path or []),
                    'argv': list(argv),
                }, component='tui.commands')
            except Exception:
                pass
            label_for_scope = _command_label(path)
            if scope_cm:
                # Expose command scope in session for nested actions (e.g., MarkItDown)
                try:
                    self.session.set_user_data('__last_command_scope__', {'title': label_for_scope})
                except Exception:
                    pass
                with scope_cm:
                    result = await self._drive_action(action, argv)
                try:
                    # Clear after command completes
                    self.session.set_user_data('__last_command_scope__', None)
                except Exception:
                    pass
            else:
                result = await self._drive_action(action, argv)
            try:
                self.session.utils.logger.tui_event('command_action_done', {
                    'path': ' '.join(path or []),
                    'action': action_name,
                    'ok': bool(result is not None),
                }, component='tui.commands')
            except Exception:
                pass
            self._handle_command_result(path, result)
            # Rebuild command list in case the action changed gates
            self._reload_commands()
            if action_name == 'set_model':
                new_model = (self.session.get_params() or {}).get('model')
                if new_model and new_model != prev_model:
                    self._on_model_changed(str(new_model))
            should_refresh_chat = False
            if path:
                primary = (path[0] or '').strip()
                secondary = (path[1] if len(path) > 1 else '').strip()
                # Only refresh transcript when it meaningfully changes chat history
                # (e.g., reprint or loading a saved chat file). Do NOT refresh for
                # '/load file', '/load raw', etc., or we’ll clear status bubbles.
                if primary == 'reprint':
                    should_refresh_chat = True
                elif primary == 'load' and secondary == 'chat':
                    should_refresh_chat = True
                elif primary == 'clear' and secondary == 'chat':
                    should_refresh_chat = True
            if should_refresh_chat and self.chat_view:
                # After commands that reset chat state, rebuild the transcript from context
                self.chat_view.clear_messages()
                self._render_existing_history()
            self._refresh_context_panel()
            self.turn_executor.check_auto_submit()
            self._clear_command_hint()
            return result

        async def _drive_action(self, action: Any, argv: List[str]) -> Any:
            import time
            pending = ('run', argv)
            start_ts = time.monotonic()
            while True:
                try:
                    if pending[0] == 'run':
                        try:
                            self.session.utils.logger.tui_detail('action_run', {
                                'action': action.__class__.__name__,
                                'phase': 'start',
                                'argv_count': len(argv),
                            }, component='tui.commands')
                        except Exception:
                            pass
                        result = await asyncio.to_thread(action.run, argv)
                    else:
                        token, response = pending[1]
                        try:
                            self.session.utils.logger.tui_detail('action_resume', {
                                'action': action.__class__.__name__,
                                'phase': 'resume',
                            }, component='tui.commands')
                        except Exception:
                            pass
                        result = await asyncio.to_thread(action.resume, token, response)
                    self._emit_status('Command completed.', 'info', display=False)
                    try:
                        dur = time.monotonic() - start_ts
                        self.session.utils.logger.tui_event('action_done', {
                            'action': action.__class__.__name__,
                            'duration_ms': int(dur * 1000),
                            'ok': True,
                        }, component='tui.commands')
                    except Exception:
                        pass
                    return result
                except InteractionNeeded as need:
                    try:
                        self.session.utils.logger.tui_detail('action_interaction_needed', {
                            'action': action.__class__.__name__,
                            'kind': need.kind,
                        }, component='tui.commands')
                    except Exception:
                        pass
                    response = await self._prompt_for_interaction(need)
                    if response is None:
                        self._emit_status('Command cancelled.', 'warning', display=False)
                        try:
                            dur = time.monotonic() - start_ts
                            self.session.utils.logger.tui_event('action_done', {
                                'action': action.__class__.__name__,
                                'duration_ms': int(dur * 1000),
                                'ok': False,
                                'cancelled': True,
                            }, component='tui.commands')
                        except Exception:
                            pass
                        return None
                    pending = ('resume', (need.state_token, response))
                except Exception as exc:
                    # Surface command errors in the transcript so users see what went wrong
                    self._emit_status(f'Command error: {exc}', 'error', display=True, role='command')
                    try:
                        dur = time.monotonic() - start_ts
                        self.session.utils.logger.tui_event('action_done', {
                            'action': action.__class__.__name__,
                            'duration_ms': int(dur * 1000),
                            'ok': False,
                            'error': str(exc),
                        }, component='tui.commands')
                    except Exception:
                        pass
                    return None

        async def _prompt_for_interaction(self, need: InteractionNeeded) -> Any:
            return await self.push_screen_wait(InteractionModal(need.kind, dict(need.spec)))

        def _clear_command_hint(self) -> None:
            self._input_completion.clear_command_hint()

        def _handle_tab_completion(self) -> None:
            def _focus() -> None:
                if self.input:
                    try:
                        self.set_focus(self.input)
                    except Exception:
                        pass

            self._input_completion.handle_tab_completion(
                find_command_suggestions=self._command_controller.find_command_suggestions,
                focus_input=_focus,
            )

        async def _wait_for_screen(self, screen) -> Any:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Any] = loop.create_future()

            def _on_close(result: Any) -> None:
                if not future.done():
                    future.set_result(result)

            self.push_screen(screen, _on_close)
            return await future


        def _get_reader_overlay(self) -> ReaderOverlay:
            if self._reader_overlay is None:
                self._reader_overlay = ReaderOverlay()
            return self._reader_overlay

        def _copy_text_to_clipboard(self, text: str) -> ClipboardOutcome:
            def primary(value: str) -> None:
                super(MemexTUIApp, self).copy_to_clipboard(value)

            outcome = self._clipboard_helper.copy(text, primary)
            self._last_clipboard_outcome = outcome
            return outcome

        def copy_to_clipboard(self, text: str) -> None:  # type: ignore[override]
            outcome = self._copy_text_to_clipboard(text)
            if not outcome.success:
                raise RuntimeError(outcome.error or "clipboard unavailable")

        def _scroll_chat(self, mode: str) -> None:
            if not self.chat_view:
                return
            if self._base_screen is not None and self.screen is not self._base_screen:
                return
            if mode == 'up':
                self.chat_view.move_cursor(-1)
            elif mode == 'down':
                self.chat_view.move_cursor(1)
            elif mode == 'page_up':
                self.chat_view.page_cursor(-1)
            elif mode == 'page_down':
                self.chat_view.page_cursor(1)
            elif mode == 'home':
                self.chat_view.jump_home()
            elif mode == 'end':
                self.chat_view.jump_end()

        def _mouse_in_chat(self, event: events.MouseEvent) -> bool:
            if not self.chat_view:
                return False
            try:
                region = self.chat_view.screen_region
                sx = event.screen_x if event.screen_x is not None else getattr(event, 'pointer_screen_x', None)
                sy = event.screen_y if event.screen_y is not None else getattr(event, 'pointer_screen_y', None)
                if sx is None or sy is None:
                    offset = getattr(event, 'offset', None)
                    if offset is not None:
                        sx = offset.x
                        sy = offset.y
                    else:
                        return False
                return region.contains_point(int(sx), int(sy))
            except Exception:
                return False

        def _handle_command_result(self, path: List[str], result: Any) -> None:
            payload: Dict[str, Any]
            if isinstance(result, Completed):
                payload = result.payload or {}
            elif isinstance(result, dict):
                payload = result
            else:
                payload = {}

            if not payload:
                return

            # Detail log: summarize command outcome without dumping full payload
            try:
                self.session.utils.logger.tui_detail('command_result', {
                    'path': ' '.join(path or []),
                    'ok': bool(payload.get('ok', True)),
                    'keys': sorted(list(payload.keys())),
                    'loaded_count': len(payload.get('loaded') or []) if isinstance(payload.get('loaded'), list) else None,
                    'mode': payload.get('mode'),
                }, component='tui.commands')
            except Exception:
                pass

            # If this command loaded contexts, remember its label so context summaries
            # attach to this command bubble on the next user turn.
            try:
                loaded = payload.get('loaded') if isinstance(payload, dict) else None
                if isinstance(loaded, list) and loaded:
                    label = '/' + ' '.join([seg for seg in (path or []) if seg])
                    self.session.set_user_data('__last_command_scope__', {'title': label})
            except Exception:
                pass

            if not payload.get('ok', True):
                message = payload.get('error') or 'Command failed.'
                self._emit_status(message, 'error', role='command')
                return

            mode = payload.get('mode')
            if mode == 'list' and 'chats' in payload:
                chats = payload.get('chats') or []
                if not chats:
                    self._emit_status('No chat files found.', 'info', role='command')
                else:
                    lines = [f"Chats ({len(chats)}):"]
                    for item in chats[:20]:
                        name = item.get('name') if isinstance(item, dict) else str(item)
                        lines.append(f" • {name}")
                    if len(chats) > 20:
                        lines.append(f"… and {len(chats) - 20} more")
                    self._emit_status('\n'.join(lines), 'info', role='command')
                return

            if mode == 'load' and payload.get('loaded'):
                filename = payload.get('filename') or payload.get('path')
                self._emit_status(f"Loaded chat from {filename}", 'info', role='command')
                if self.chat_view:
                    self.chat_view.clear_messages()
                    self._render_existing_history()
                return

            if mode == 'save' and payload.get('saved'):
                filename = payload.get('filename') or payload.get('path')
                self._emit_status(f"Saved chat to {filename}", 'info', role='command')
                return

            # Generic fallback: surface minimal confirmation for non-context-modifying commands
            if payload.get('ok'):
                primary = (path[0] if path else '') or ''
                secondary = (path[1] if len(path) > 1 else '')
                context_modifying = (
                    (primary == 'load' and secondary in {'file', 'raw', 'multiline'}) or
                    (primary == 'file') or
                    (primary == 'clear' and secondary in {'context', 'chat'})
                )
                if not context_modifying:
                    label = '/' + ' '.join([seg for seg in (path or []) if seg]) if path else '/command'
                    self._emit_status(f"{label} completed.", 'info', role='command')
                if payload.get('model'):
                    self._on_model_changed(str(payload.get('model')))

        def _emit_status(
            self,
            text: str,
            level: str,
            *,
            display: bool = True,
            role: str = "system",
        ) -> None:
            self._output_bridge.record_status(text, level)
            if display:
                self._output_bridge.display_status_message(text, level, role=role)
            self._output_bridge.log_status(text, level)

        def _status_bar_text(self, model: str, provider: str, stream_enabled: bool) -> str:
            stream_label = 'on' if stream_enabled else 'off'
            return (
                f"iptic-memex TUI · Model: {model or 'unknown'} · Provider: {provider or 'unknown'} · "
                f"Stream: {stream_label}"
            )

        def _update_status_bar(self) -> None:
            try:
                bar = self.query_one('#status_bar', Static)
            except Exception:
                return
            params = self.session.get_params() or {}
            model_name = params.get('model', 'unknown')
            provider = params.get('provider', 'unknown')
            bar.update(self._status_bar_text(model_name, provider, self._stream_enabled))

        def _apply_role_labels(self, params: Optional[Dict[str, Any]] = None) -> None:
            params = params or (self.session.get_params() or {})

            titles: Dict[str, str] = {}
            styles: Dict[str, str] = {}

            def normalize_color(value: str) -> str:
                collapsed = value.replace('_', '').replace('-', '').replace(' ', '').lower()
                aliases = {
                    # Map gray variants to Rich-safe equivalents.
                    # Use dim white for generic gray for broad theme compatibility.
                    'gray': 'dim white',
                    'grey': 'dim white',
                    'darkgray': 'grey30',
                    'darkgrey': 'grey30',
                    'lightgray': 'grey70',
                    'lightgrey': 'grey70',
                }
                return aliases.get(collapsed, value)

            def apply(role: str, label_key: str, color_key: str, *, bold_default: bool) -> None:
                label_raw = str(params.get(label_key) or '').strip()
                color_raw = str(params.get(color_key) or '').strip()
                if color_raw:
                    color_raw = normalize_color(color_raw)
                if label_raw:
                    titles[role] = label_raw
                if color_raw:
                    color_tokens = {token.lower() for token in color_raw.replace('-', ' ').split()}
                    color_style = color_raw
                    if bold_default and 'bold' not in color_tokens and 'dim' not in color_tokens:
                        color_style = f"bold {color_raw}"
                    styles[role] = color_style

            apply('assistant', 'response_label', 'response_label_color', bold_default=True)
            apply('user', 'user_label', 'user_label_color', bold_default=False)

            self._chat_role_titles = titles
            self._chat_role_styles = styles
            if self.chat_view:
                self.chat_view.set_role_customization(titles, styles)

        def _on_model_changed(self, model_name: str) -> None:
            params = self.session.get_params() or {}
            new_stream = bool(params.get('stream'))
            if new_stream != self._stream_enabled:
                self._stream_enabled = new_stream
                self.turn_executor.set_stream_enabled(new_stream)
            self._apply_role_labels(params)
            self._update_status_bar()

        async def _prompt_model_choice(self) -> Optional[str]:
            models_raw = self.session.list_models() or {}
            if isinstance(models_raw, dict):
                model_map = dict(models_raw)
            else:
                model_map = {str(name): {} for name in list(models_raw)}

            if not model_map:
                self._emit_status('No models available to select.', 'error', role='command')
                return None

            current_model = str((self.session.get_params() or {}).get('model') or '').strip()

            entries: List[tuple[str, str]] = []
            for name in sorted(model_map.keys()):
                meta = model_map.get(name) or {}
                provider = str(meta.get('provider') or '').strip()
                is_default = str(meta.get('default') or '').lower() in {'true', '1', 'yes'}
                parts = [name]
                if provider:
                    parts.append(f"[{provider}]")
                if is_default:
                    parts.append('(default)')
                if name == current_model:
                    parts.append('(current)')
                label = ' '.join(parts)
                entries.append((name, label))

            # Move current model to the top of the list for quick confirmation
            if current_model:
                entries.sort(key=lambda item: (0 if item[0] == current_model else 1, item[1].lower()))
            else:
                entries.sort(key=lambda item: item[1].lower())

            choices = [{'label': label, 'value': name} for name, label in entries]
            selection = await self._wait_for_screen(
                InteractionModal('choice', {'prompt': 'Select a model', 'choices': choices})
            )
            if not selection:
                return None
            return str(selection)

        async def _handle_manage_chats_command(
            self,
            action: Any,
            path: List[str],
            fixed_args: List[str],
            extra_argv: Optional[List[str]],
        ) -> bool:
            if not fixed_args:
                return False
            mode = str(fixed_args[0] or '').lower()
            try:
                if mode == 'save':
                    handled, payload = await self._handle_manage_chats_save(action, extra_argv)
                    if handled:
                        if payload is not None:
                            self._handle_command_result(path, payload)
                        return True
                if mode == 'list':
                    result = await asyncio.to_thread(action._handle_headless, {'mode': 'list'})
                    self._handle_command_result(path, result)
                    return True
                if mode == 'load':
                    filename: Optional[str] = None
                    if extra_argv:
                        filename = str(extra_argv[0]) if extra_argv else None
                    if not filename:
                        chats = await asyncio.to_thread(action._list_chats)
                        if not chats:
                            self._emit_status('No chat files found.', 'warning', role='command')
                            return True
                        choices = []
                        for item in chats:
                            name = item.get('name') if isinstance(item, dict) else str(item)
                            value = item.get('filename') if isinstance(item, dict) else str(item)
                            if not value:
                                value = name
                            choices.append({'label': name, 'value': value})
                        selection = await self._wait_for_screen(
                            InteractionModal('choice', {'prompt': 'Select chat to load', 'choices': choices})
                        )
                        if not selection:
                            self._emit_status('Load chat cancelled.', 'warning', role='command')
                            return True
                        filename = str(selection)
                    result = await asyncio.to_thread(action._handle_headless, {'mode': 'load', 'file': filename})
                    self._handle_command_result(path, result)
                    return True
            except Exception as exc:
                self._emit_status(f'Manage chats error: {exc}', 'error', role='command')
                return True
            return False

        async def _handle_manage_chats_save(
            self,
            action: Any,
            extra_argv: Optional[List[str]],
        ) -> Tuple[bool, Optional[Dict[str, Any]]]:
            params = self.session.get_params() or {}
            chat_format = params.get('chat_format', 'md') or 'md'
            chats_directory = params.get('chats_directory', 'chats')

            default_name = None
            default_fn = getattr(action, '_default_filename', None)
            if callable(default_fn):
                try:
                    default_name = default_fn(chat_format)
                except Exception:
                    default_name = None
            if not default_name:
                ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                default_name = f"chat_{ts}.{chat_format}"

            candidate = None
            if extra_argv:
                candidate = str(extra_argv[0] or '').strip() or None

            while True:
                if not candidate:
                    value = await self._wait_for_screen(
                        InteractionModal('text', {'prompt': 'Filename to save:', 'default': default_name})
                    )
                    if not value:
                        self._emit_status('Chat save cancelled.', 'warning', display=False)
                        return True, None
                    candidate = str(value).strip()

                try:
                    normalize_fn = getattr(action, '_normalize_filename', None)
                    if callable(normalize_fn):
                        basename, _, full_path = normalize_fn(candidate)
                    else:
                        basename = candidate
                        full_path = os.path.join(os.path.expanduser(chats_directory), basename)
                except Exception as exc:
                    self._emit_status(f'Invalid filename: {exc}', 'error', role='command')
                    candidate = None
                    continue

                expanded_input = os.path.expanduser(candidate)
                default_name = full_path if os.path.isabs(expanded_input) else basename

                include_ctx = await self._wait_for_screen(
                    InteractionModal('bool', {'prompt': 'Include context in save?', 'default': False})
                )
                if include_ctx is None:
                    self._emit_status('Chat save cancelled.', 'warning', display=False)
                    return True, None

                overwrite = False
                exists = await asyncio.to_thread(os.path.exists, full_path)
                if exists:
                    overwrite_resp = await self._wait_for_screen(
                        InteractionModal(
                            'bool',
                            {
                                'prompt': f"File '{basename}' exists. Overwrite?",
                                'default': False,
                            },
                        )
                    )
                    if overwrite_resp is None:
                        self._emit_status('Chat save cancelled.', 'warning', display=False)
                        return True, None
                    if not overwrite_resp:
                        candidate = None
                        continue
                    overwrite = True

                save_target = full_path if os.path.isabs(expanded_input) else basename

                payload = await asyncio.to_thread(
                    action._handle_headless,
                    {
                        'mode': 'save',
                        'filename': save_target,
                        'include_context': bool(include_ctx),
                        'overwrite': overwrite,
                    },
                )
                return True, payload

        # ----- status/context helpers ---------------------------------
        def _refresh_context_panel(self) -> None:
            if not self.context_panel:
                return
            contexts: List[str] = []
            for kind, entries in self.session.context.items():
                if kind == 'chat':
                    continue
                contexts.append(f"{kind}: {len(entries)}")
            self.context_panel.update_contexts(contexts)

        def _render_existing_history(self) -> None:
            chat_ctx = self.session.get_context('chat')
            if not chat_ctx or not self.chat_view:
                return
            try:
                history = chat_ctx.get('all') if hasattr(chat_ctx, 'get') else []
            except Exception:
                history = []
            for item in history or []:
                self._add_chat_entry(item)

        def _render_messages(self, messages: List[Dict[str, Any]]) -> None:
            if not self.chat_view:
                return
            for msg in messages:
                self._add_chat_entry(msg)

        def _add_chat_entry(self, item: Dict[str, Any]) -> None:
            if not self.chat_view:
                return
            role = item.get('role', 'assistant')
            text = item.get('message') or item.get('text') or item.get('content') or ''
            self.chat_view.add_message(role, text)

        # ----- bindings/actions ---------------------------------------
        def action_toggle_stream(self) -> None:
            self._stream_enabled = not self._stream_enabled
            self.session.set_option('stream', bool(self._stream_enabled))
            self.turn_executor.set_stream_enabled(self._stream_enabled)
            status = f"Streaming {'enabled' if self._stream_enabled else 'disabled'}"
            self._emit_status(status, 'info', display=False)
            self._update_status_bar()

        def action_refresh_contexts(self) -> None:
            self._refresh_context_panel()
            self._emit_status('Contexts refreshed.', 'debug', display=False)

        def action_quit(self) -> None:
            self._cleanup()
            self.exit()

        def action_focus_input(self) -> None:
            if self.input:
                self.set_focus(self.input)

        def action_show_status(self) -> None:
            if self._status_open and self._status_modal:
                self._status_modal.dismiss(None)
                return
            history = self._output_bridge.status_history
            self._status_modal = StatusModal(history)
            self._status_open = True
            self.push_screen(self._status_modal, lambda _: self._on_status_close())

        def action_open_reader(self) -> None:
            if not self.chat_view:
                return
            if self._reader_open and self._reader_overlay:
                self._reader_overlay.dismiss(None)
                return
            message = self.chat_view.current_message()
            if not message:
                self._emit_status('No message selected.', 'info', display=False)
                return
            overlay = self._get_reader_overlay()
            overlay.load_message(message)
            self._reader_open = True
            self.push_screen(overlay, lambda _: self._on_reader_close())

        def action_copy_current_message(self) -> None:
            if not self.chat_view:
                return
            message = self.chat_view.current_message()
            if not message:
                self._emit_status('No message selected to copy.', 'info', display=False)
                return
            if getattr(self, '_reader_open', False) and self._reader_overlay:
                selected = self._reader_overlay.get_selected_text() or ""
                if selected:
                    text = selected
                else:
                    text = message.text or ""
            else:
                text = message.text or ""
            outcome = self._copy_text_to_clipboard(text)
            if outcome.success:
                method = 'OSC-52' if outcome.method == 'osc52' else outcome.method
                self._emit_status(f'Copied message via {method}.', 'info', role='system')
                if len(text) > self._copy_warning_threshold:
                    self._emit_status(
                        'Copied large selection; some terminals may truncate clipboard data.',
                        'warning',
                        role='system',
                    )
            else:
                detail = outcome.error or 'clipboard unavailable'
                self._emit_status(f'Clipboard copy failed: {detail}', 'error')

        def _on_reader_close(self) -> None:
            self._reader_open = False

        def _on_status_close(self) -> None:
            self._status_open = False
            self._status_modal = None

        def action_cancel_turn(self) -> None:
            cancelled = False
            try:
                self.session.set_flag('turn_cancelled', True)
                cancelled = True
            except Exception:
                pass
            try:
                token = getattr(self.session, 'get_cancellation_token', None)
                if callable(token):
                    tok = token()
                else:
                    tok = self.session.get_user_data('__turn_cancel__')
                if tok:
                    tok.cancel('user')
                    cancelled = True
            except Exception:
                pass
            if cancelled:
                self._emit_status('Turn cancelled.', 'warning')
            else:
                self._emit_status('No running turn to cancel.', 'info')

        def on_key(self, event: events.Key) -> None:
            if not self.input:
                return

            if event.key == 'escape':
                in_base_screen = self._base_screen is None or self.screen is self._base_screen
                if in_base_screen and (self.focused is self.input):
                    # Only consume ESC to clear hints when the base view is active and input has focus.
                    self._input_completion.clear_command_hint()
                    try:
                        self.set_focus(self.input)
                    except Exception:
                        pass
                    event.prevent_default()
                    event.stop()
                    return
                # Otherwise let ESC bubble to modals / other screens.

            in_base_screen = self._base_screen is None or self.screen is self._base_screen
            if in_base_screen:
                nav_map = {
                    'up': 'up',
                    'down': 'down',
                    'pageup': 'page_up',
                    'pagedown': 'page_down',
                    'home': 'home',
                    'end': 'end',
                }
                mode = nav_map.get(event.key)
                if mode:
                    event.prevent_default()
                    event.stop()
                    self._scroll_chat(mode)
                    return

            focused_input = self.focused is self.input
            if event.key == 'tab' and focused_input:
                event.prevent_default()
                event.stop()
                self._handle_tab_completion()
            elif event.key == 'tab' or event.key == 'shift+tab':
                event.prevent_default()
                event.stop()
                try:
                    self.set_focus(self.input)
                except Exception:
                    pass

        def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
            if self._mouse_in_chat(event):
                event.prevent_default()
                event.stop()
                self._scroll_chat('up')

        def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
            if self._mouse_in_chat(event):
                event.prevent_default()
                event.stop()
                self._scroll_chat('down')

        async def on_unmount(self) -> None:
            self._cleanup()

        def _cleanup(self) -> None:
            for task in list(self._pending_tasks):
                task.cancel()
            # No spinner timer to stop
            try:
                if hasattr(self._ui_adapter, 'set_event_handler'):
                    self._ui_adapter.set_event_handler(lambda *_: None)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                params = self.session.get_params() or {}
                self.session.utils.logger.tui_event('stop', {'model': params.get('model'), 'provider': params.get('provider')})
            except Exception:
                pass
            try:
                if self._orig_output:
                    self.session.utils.replace_output(self._orig_output)
                    self._orig_output = None
            except Exception:
                pass
            try:
                self.turn_executor.set_chat_view(None)
            except Exception:
                pass

else:  # pragma: no cover - textual missing fallback

    class MemexTUIApp:
        def __init__(self, session, builder=None) -> None:  # noqa: D401
            self.session = session
            self.builder = builder

        def run(self) -> None:  # noqa: D401
            print("Error: Textual library not installed.")
            print("Install with: pip install textual")
