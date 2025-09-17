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
    from textual.widgets import Button, Footer, Static
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

        BINDINGS = [
            Binding("ctrl+q", "quit", "Quit"),
            Binding("ctrl+s", "toggle_stream", "Toggle stream"),
            Binding("ctrl+k", "open_commands", "Commands"),
            Binding("f8", "show_status", "Status", priority=True),
            Binding("ctrl+c", "cancel_turn", "Cancel turn", priority=True),
            Binding("alt+up", "scroll_chat_up", show=False, priority=True),
            Binding("alt+down", "scroll_chat_down", show=False, priority=True),
            Binding("pageup", "scroll_chat_page_up", show=False, priority=True),
            Binding("pagedown", "scroll_chat_page_down", show=False, priority=True),
            Binding("ctrl+u", "scroll_chat_page_up", show=False, priority=True),
            Binding("ctrl+d", "scroll_chat_page_down", show=False, priority=True),
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
            self.send_button: Optional[Button] = None

            self.command_registry = self.session.get_action('user_commands_registry')
            self.command_hint: Optional[CommandHint] = None
            self._command_controller = CommandController()
            self._input_completion = InputCompletionManager()

            self._chat_role_titles: Dict[str, str] = {}
            self._chat_role_styles: Dict[str, str] = {}
            params = self.session.get_params() or {}
            self._apply_response_label(params)

            self._stream_enabled: bool = bool((self.session.get_params() or {}).get('stream'))
            self._pending_tasks: set[asyncio.Task[Any]] = set()

            self._ui_adapter = getattr(self.session, 'ui', None)
            if hasattr(self._ui_adapter, 'set_event_handler'):
                try:
                    self._ui_adapter.set_event_handler(self._handle_ui_event)
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

            with Vertical(id="input_row"):
                self.command_hint = CommandHint(id="command_hint")
                self._input_completion.set_command_hint(self.command_hint)
                yield self.command_hint
                with Horizontal(id="input_controls"):
                    self.input = ChatInput(placeholder="Type a message or '/' for commands")
                    self._input_completion.set_input(self.input)
                    yield self.input
                    self.send_button = Button("Send", id="send_button")
                    yield self.send_button

            yield Footer()

        async def on_mount(self) -> None:
            if self.input:
                self.set_focus(self.input)
            if self.chat_view:
                try:
                    self.chat_view.can_focus = False
                except Exception:
                    pass
            if self.command_hint:
                self.command_hint.display = False
            self._emit_status("Welcome to iptic-memex TUI. Press Ctrl+S to toggle streaming.", 'info')
            try:
                params = self.session.get_params() or {}
                self.session.utils.logger.tui_event('start', {'model': params.get('model'), 'provider': params.get('provider')})
            except Exception:
                pass
            self._load_commands()
            self._refresh_context_panel()
            self._render_existing_history()
            # Configure input suggestions (Textual 0.27+)
            try:
                if hasattr(self.input, 'suggest_on'):
                    self.input.suggest_on = 'typing'  # type: ignore[attr-defined]
            except Exception:
                pass

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
            self.call_from_thread(self._output_bridge.handle_ui_event, event_type, data)

        # ----- input handling -----------------------------------------
        async def on_chat_input_send_requested(self, message: "ChatInput.SendRequested") -> None:
            message.stop()
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
            self._schedule_task(self.turn_executor.run(message))

        def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
            if event.button.id == "send_button":
                self._submit_input_text(self.input.text if self.input else '')

        async def _dispatch_slash_command(self, text: str) -> None:
            registry = self.command_registry
            if not registry:
                self._emit_status('Command registry unavailable.', 'error')
                return
            try:
                chat_commands = self.session.get_action('chat_commands')
            except Exception:
                chat_commands = None
            if not chat_commands:
                self._emit_status('Commands action unavailable.', 'error')
                return
            try:
                parsed = chat_commands.match(text)
            except Exception as exc:
                self._emit_status(f'Command error: {exc}', 'error')
                return
            if not parsed:
                return
            if parsed.get('kind') == 'error':
                self._emit_status(parsed.get('message', 'Invalid command'), 'warning')
                return
            handler = {
                'type': parsed.get('kind'),
                'action': parsed.get('action'),
                'method': parsed.get('method'),
                'args': parsed.get('args') or [],
            }
            path = parsed.get('path') or []
            argv = parsed.get('argv') or []
            await self._execute_command_handler(handler, path, argv)

        # ----- commands ------------------------------------------------
        def _load_commands(self) -> None:
            self._command_controller.load(self.command_registry)
            self._input_completion.update_catalog(
                self._command_controller.top_level_commands,
                self._command_controller.subcommand_map,
            )

        def action_open_commands(self) -> None:
            if self.command_hint:
                self.command_hint.display = False
            if not self._command_controller.has_commands:
                self._emit_status('No commands available.', 'warning')
                return
            palette = CommandPalette(self._command_controller.command_items)
            self.push_screen(palette, self._on_command_selected)

        def _on_command_selected(self, command: Optional[CommandItem]) -> None:
            if not command:
                return
            self._schedule_task(self._execute_command_item(command))
            if self.input:
                self._input_completion.mark_programmatic_update()
                self.input.value = ''
                self.set_focus(self.input)
            self._clear_command_hint()

        async def _execute_command_item(self, command: CommandItem) -> None:
            handler = command.handler or {}
            await self._execute_command_handler(handler, command.path, [])

        async def _execute_command_handler(self, handler: Dict[str, Any], path: List[str], extra_argv: Optional[List[str]]) -> None:
            if not handler:
                self._emit_status('Invalid command handler.', 'error', display=False)
                return
            if handler.get('type') == 'builtin':
                if path and path[0] == 'quit':
                    self.action_quit()
                    return
                try:
                    ok, err = await asyncio.to_thread(self.command_registry.execute, path, {'argv': list(extra_argv or [])})
                except SystemExit:
                    self.action_quit()
                    return
                try:
                    self.session.utils.logger.tui_event('command_builtin', {'path': ' '.join(path or []), 'ok': bool(ok)})
                except Exception:
                    pass
                if not ok:
                    self._emit_status(err or 'Command failed.', 'error')
                self._refresh_context_panel()
                self.turn_executor.check_auto_submit()
                self._clear_command_hint()
                return

            action_name = handler.get('action') or handler.get('name')
            if not action_name:
                self._emit_status('Command missing action mapping.', 'error', display=False)
                return
            action = self.session.get_action(action_name)
            if not action:
                self._emit_status(f"Unknown action '{action_name}'.", 'error', display=False)
                return

            fixed_args = list(handler.get('args') or [])
            if isinstance(fixed_args, str):
                fixed_args = [fixed_args]
            argv = list(str(a) for a in fixed_args)

            if action_name == 'set_model' and not argv and not (extra_argv and any(str(a).strip() for a in extra_argv)):
                chosen = await self._prompt_model_choice()
                if not chosen:
                    self._emit_status('Model selection cancelled.', 'warning', display=False)
                    self._clear_command_hint()
                    return
                argv.append(chosen)

            # Intercept bare /clear to behave like Ctrl+L and just clear the view.
            if path and path[0] == 'clear' and (len(path) < 2 or not path[1]):
                if self.chat_view:
                    self.chat_view.clear_messages()
                self._emit_status('Screen cleared.', 'info')
                self._clear_command_hint()
                return

            if action_name == 'manage_chats':
                handled = await self._handle_manage_chats_command(action, path, argv, extra_argv)
                if handled:
                    if self.chat_view:
                        self.chat_view.clear_messages()
                        self._render_existing_history()
                    self._refresh_context_panel()
                    self.turn_executor.check_auto_submit()
                    self._clear_command_hint()
                    return

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
                        return
                self._emit_status('Command executed.', 'info', display=False)
                try:
                    self.session.utils.logger.tui_event('command_action', {'path': ' '.join(path or []), 'action': action_name, 'method': method_name, 'argv': argv})
                except Exception:
                    pass
                self._handle_command_result(path, result)
                self._refresh_context_panel()
                self.turn_executor.check_auto_submit()
                self._clear_command_hint()
                return

            if action_name == 'load_file':
                has_path_arg = any((str(arg).strip()) for arg in argv)
                if not has_path_arg:
                    self._emit_status('Provide a file path before loading.', 'warning', display=False)
                    self._emit_status('Add a file path or use Tab to complete one, then press Enter.', 'warning')
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
                    return

            prev_model = (self.session.get_params() or {}).get('model') if action_name == 'set_model' else None
            result = await self._drive_action(action, argv)
            try:
                self.session.utils.logger.tui_event('command_action', {'path': ' '.join(path or []), 'action': action_name, 'method': None, 'argv': argv})
            except Exception:
                pass
            self._handle_command_result(path, result)
            if action_name == 'set_model':
                new_model = (self.session.get_params() or {}).get('model')
                if new_model and new_model != prev_model:
                    self._on_model_changed(str(new_model))
            should_refresh_chat = False
            if path:
                primary = path[0] or ''
                secondary = path[1] if len(path) > 1 else ''
                if primary in {'reprint', 'load', 'load chat'}:
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

        async def _drive_action(self, action: Any, argv: List[str]) -> None:
            pending = ('run', argv)
            while True:
                try:
                    if pending[0] == 'run':
                        result = await asyncio.to_thread(action.run, argv)
                    else:
                        token, response = pending[1]
                        result = await asyncio.to_thread(action.resume, token, response)
                    self._emit_status('Command completed.', 'info', display=False)
                    return result
                except InteractionNeeded as need:
                    response = await self._prompt_for_interaction(need)
                    if response is None:
                        self._emit_status('Command cancelled.', 'warning', display=False)
                        return
                    pending = ('resume', (need.state_token, response))
                except Exception as exc:
                    self._emit_status(f'Command error: {exc}', 'error', display=False)
                    return

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


        def _scroll_chat(self, mode: str) -> None:
            if not self.chat_view:
                return
            try:
                if hasattr(self.chat_view, 'auto_scroll'):
                    self.chat_view.auto_scroll = False
            except Exception:
                pass
            try:
                if mode == 'up':
                    self.chat_view.scroll_up(animate=False, immediate=True)
                elif mode == 'down':
                    self.chat_view.scroll_down(animate=False, immediate=True)
                elif mode == 'page_up':
                    self.chat_view.scroll_page_up(animate=False)
                elif mode == 'page_down':
                    self.chat_view.scroll_page_down(animate=False)
            except Exception:
                pass

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

            if not payload.get('ok', True):
                message = payload.get('error') or 'Command failed.'
                self._emit_status(message, 'error')
                return

            mode = payload.get('mode')
            if mode == 'list' and 'chats' in payload:
                chats = payload.get('chats') or []
                if not chats:
                    self._emit_status('No chat files found.', 'info')
                else:
                    lines = [f"Chats ({len(chats)}):"]
                    for item in chats[:20]:
                        name = item.get('name') if isinstance(item, dict) else str(item)
                        lines.append(f" • {name}")
                    if len(chats) > 20:
                        lines.append(f"… and {len(chats) - 20} more")
                    self._emit_status('\n'.join(lines), 'info')
                return

            if mode == 'load' and payload.get('loaded'):
                filename = payload.get('filename') or payload.get('path')
                self._emit_status(f"Loaded chat from {filename}", 'info')
                if self.chat_view:
                    self.chat_view.clear_messages()
                    self._render_existing_history()
                return

            if mode == 'save' and payload.get('saved'):
                filename = payload.get('filename') or payload.get('path')
                self._emit_status(f"Saved chat to {filename}", 'info')
                return

            # Generic fallback: surface minimal confirmation
            if payload.get('ok'):
                label = ' '.join(path or []) if path else 'command'
                self._emit_status(f"/{label} completed.", 'info')
                if payload.get('model'):
                    self._on_model_changed(str(payload.get('model')))

        def _emit_status(self, text: str, level: str, *, display: bool = True) -> None:
            self._output_bridge.record_status(text, level)
            if display:
                self._output_bridge.display_status_message(text, level)
            self._output_bridge.log_status(text, level)

        def _status_bar_text(self, model: str, provider: str, stream_enabled: bool) -> str:
            return (
                f"iptic-memex TUI · Model: {model or 'unknown'} · Provider: {provider or 'unknown'} · "
                f"Stream: {'on' if stream_enabled else 'off'}"
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

        def _apply_response_label(self, params: Optional[Dict[str, Any]] = None) -> None:
            params = params or (self.session.get_params() or {})
            response_label = str(params.get('response_label') or '').strip()
            response_label_color = str(params.get('response_label_color') or '').strip()

            titles: Dict[str, str] = {}
            styles: Dict[str, str] = {}
            if response_label:
                titles['assistant'] = response_label
            if response_label_color:
                color_tokens = {token.lower() for token in response_label_color.replace('-', ' ').split()}
                color_style = response_label_color
                if 'bold' not in color_tokens:
                    color_style = f"bold {response_label_color}"
                styles['assistant'] = color_style

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
            self._apply_response_label(params)
            self._update_status_bar()

        async def _prompt_model_choice(self) -> Optional[str]:
            models_raw = self.session.list_models() or {}
            if isinstance(models_raw, dict):
                model_map = dict(models_raw)
            else:
                model_map = {str(name): {} for name in list(models_raw)}

            if not model_map:
                self._emit_status('No models available to select.', 'error')
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
                            self._emit_status('No chat files found.', 'warning')
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
                            self._emit_status('Load chat cancelled.', 'warning')
                            return True
                        filename = str(selection)
                    result = await asyncio.to_thread(action._handle_headless, {'mode': 'load', 'file': filename})
                    self._handle_command_result(path, result)
                    return True
            except Exception as exc:
                self._emit_status(f'Manage chats error: {exc}', 'error')
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
                    self._emit_status(f'Invalid filename: {exc}', 'error')
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
            history = self._output_bridge.status_history
            self.push_screen(StatusModal(history), lambda _: None)

        def action_scroll_chat_up(self) -> None:
            self._scroll_chat('up')

        def action_scroll_chat_down(self) -> None:
            self._scroll_chat('down')

        def action_scroll_chat_page_up(self) -> None:
            self._scroll_chat('page_up')

        def action_scroll_chat_page_down(self) -> None:
            self._scroll_chat('page_down')

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
