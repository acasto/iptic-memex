"""Textual application for the iptic-memex TUI mode."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Dict, List, Optional, Tuple

try:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.suggester import SuggestFromList
    from textual.widgets import Footer, Input, Static
    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - surfaced when textual missing
    TEXTUAL_AVAILABLE = False

from base_classes import Completed, InteractionNeeded
from core.turns import TurnOptions, TurnRunner
from tui.models import CommandItem
from tui.output_sink import OutputEvent, TuiOutput

from utils.output_utils import OutputHandler


if TEXTUAL_AVAILABLE:
    from tui.screens.command_palette import CommandPalette
    from tui.screens.compose_modal import ComposeModal
    from tui.screens.interaction_modal import InteractionModal
    from tui.widgets.chat_transcript import ChatTranscript
    from tui.widgets.command_hint import CommandHint
    from tui.widgets.context_summary import ContextSummary
    from tui.widgets.status_panel import StatusPanel
    from tui.screens.status_modal import StatusModal

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
            self.input: Optional[Input] = None

            self.command_registry = self.session.get_action('user_commands_registry')
            self._command_items: List[CommandItem] = []
            self._command_specs: Dict[str, Dict[str, Any]] = {}
            self._command_lookup: Dict[Tuple[str, str], CommandItem] = {}
            self._top_level_commands: List[CommandItem] = []
            self._subcommand_map: Dict[str, List[CommandItem]] = {}
            self.command_hint: Optional[CommandHint] = None
            self._status_history: List[tuple[str, str]] = []
            self._all_suggestion_strings: List[str] = []
            self._tab_cycle_state: Dict[str, Any] = {
                'key': '',
                'index': 0,
                'suggestions': [],
            }
            self._suppress_input_changed_reset: bool = False

            self._orig_output: Optional[OutputHandler] = self.session.utils.output
            self._tui_output = TuiOutput(self._handle_output_event)
            self.session.utils.replace_output(self._tui_output)

            self._stream_enabled: bool = bool((self.session.get_params() or {}).get('stream'))
            self._pending_tasks: set[asyncio.Task[Any]] = set()
            self._active_message_id: Optional[str] = None
            self._spinner_messages: Dict[str, str] = {}

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

        # ----- layout --------------------------------------------------
        def compose(self) -> ComposeResult:
            params = self.session.get_params() or {}
            model_name = params.get('model', 'unknown')
            provider = params.get('provider', 'unknown')
            status = Static(
                f"iptic-memex TUI · Model: {model_name} · Provider: {provider} · Stream: {'on' if self._stream_enabled else 'off'}",
                id="status_bar",
            )
            yield status

            self.chat_view = ChatTranscript(id="chat_transcript")
            yield self.chat_view

            # Sidebar removed; dedicate full width to chat. Status is shown via F8 modal.

            with Vertical(id="input_row"):
                self.command_hint = CommandHint(id="command_hint")
                yield self.command_hint
                self.input = Input(placeholder="Type a message or '/' for commands", id="input")
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
            if self.command_hint:
                self.command_hint.display = False
            if self.status_log:
                self.status_log.log_status("Welcome to iptic-memex TUI. Press Ctrl+S to toggle streaming.", "info")
            try:
                params = self.session.get_params() or {}
                self.session.utils.logger.tui_event('start', {'model': params.get('model'), 'provider': params.get('provider')})
            except Exception:
                pass
            self._load_commands()
            self._refresh_input_suggester()
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
            self.call_from_thread(self._dispatch_output_event, event)

        def _dispatch_output_event(self, event: OutputEvent) -> None:
            if not hasattr(self, '_status_history'):
                self._status_history = []
            if event.type == 'write':
                text = event.text or ''
                if event.is_stream and self._active_message_id and self.chat_view:
                    self.chat_view.append_text(self._active_message_id, text)
                else:
                    stripped = text.rstrip('\n')
                    if stripped:
                        level = event.level or 'info'
                        self._record_status(stripped, level)
                        self._display_status_message(stripped, level)
                        if self.status_log:
                            self.status_log.log_status(stripped, level)
            elif event.type == 'spinner':
                label = event.text or 'Working...'
                self._record_status(label, 'info')
                if self.status_log:
                    self.status_log.log_status(label, 'info')
                if event.spinner_id:
                    self._spinner_messages[event.spinner_id] = label
            elif event.type == 'spinner_done':
                label = self._spinner_messages.pop(event.spinner_id, None)
                if label:
                    self._record_status(f"{label} – done", 'debug')
                    if self.status_log:
                        self.status_log.log_status(f"{label} – done", 'debug')

        def _handle_ui_event(self, event_type: str, data: Dict[str, Any]) -> None:
            if not hasattr(self, '_status_history'):
                self._status_history: List[tuple[str, str]] = []
            message = str(data.get('message') or data.get('text') or '')
            if event_type == 'progress':
                prog = data.get('progress')
                if prog is not None:
                    pct = int(float(prog) * 100)
                    if message:
                        message = f"{message} ({pct}%)"
                    else:
                        message = f"Progress {pct}%"
            if not message:
                message = str(data)
            level = 'info'
            if event_type in ('warning', 'error', 'critical'):
                level = event_type
            self._record_status(message, level)
            self._display_status_message(message, level)
            if self.status_log:
                self.status_log.log_status(message, level)

        # ----- input handling -----------------------------------------
        async def on_input_submitted(self, event: Input.Submitted) -> None:
            message = (event.value or '').strip()
            event.input.value = ''
            self._clear_command_hint()
            if not message:
                return
            self._handle_user_input(message)

        def on_input_changed(self, event: Input.Changed) -> None:  # type: ignore[override]
            if getattr(event.input, 'id', None) != 'input':
                return
            value = event.value or ''
            if self._suppress_input_changed_reset:
                self._suppress_input_changed_reset = False
            else:
                self._reset_tab_cycle()
            if not value.startswith('/'):
                self._clear_command_hint()
                return
            suggestions, highlight = self._find_command_suggestions(value)
            if self.command_hint:
                self.command_hint.update_suggestions(suggestions, prefix=highlight)
            self._update_input_suggestions(value, suggestions)

        def _handle_user_input(self, message: str) -> None:
            if message.startswith('/'):
                self._schedule_task(self._dispatch_slash_command(message))
                return
            if self.chat_view:
                self.chat_view.add_message('user', message)
            self._schedule_task(self._run_turn(message))

        async def _dispatch_slash_command(self, text: str) -> None:
            registry = self.command_registry
            if not registry:
                if self.status_log:
                    self.status_log.log_status('Command registry unavailable.', 'error')
                self._display_status_message('Command registry unavailable.', 'error')
                return
            try:
                chat_commands = self.session.get_action('chat_commands')
            except Exception:
                chat_commands = None
            if not chat_commands:
                if self.status_log:
                    self.status_log.log_status('Commands action unavailable.', 'error')
                self._display_status_message('Commands action unavailable.', 'error')
                return
            try:
                parsed = chat_commands.match(text)
            except Exception as exc:
                if self.status_log:
                    self.status_log.log_status(f'Command error: {exc}', 'error')
                self._display_status_message(f'Command error: {exc}', 'error')
                return
            if not parsed:
                return
            if parsed.get('kind') == 'error':
                if self.status_log:
                    self.status_log.log_status(parsed.get('message', 'Invalid command'), 'warning')
                self._display_status_message(parsed.get('message', 'Invalid command'), 'warning')
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
            registry = self.command_registry
            if not registry:
                return
            try:
                specs = registry.get_specs('tui')
            except Exception:
                specs = {}
            commands: List[CommandItem] = []
            lookup: Dict[Tuple[str, str], CommandItem] = {}
            sub_map: Dict[str, List[CommandItem]] = {}
            top_level: List[CommandItem] = []
            for item in specs.get('commands', []):
                cmd_name = item.get('command')
                help_text = item.get('help', '')
                has_default = False
                for sub in item.get('subs', []):
                    sub_name = sub.get('sub') or ''
                    title = f"/{cmd_name}" + (f" {sub_name}" if sub_name else '')
                    cmd_help = help_text
                    sub_help = sub.get('ui', {}).get('help') if isinstance(sub.get('ui'), dict) else ''
                    entry = CommandItem(
                        title=title,
                        path=[cmd_name, sub_name],
                        help=sub_help or cmd_help,
                        handler=sub,
                    )
                    commands.append(entry)
                    lookup[(cmd_name, sub_name)] = entry
                    if sub_name:
                        sub_map.setdefault(cmd_name, []).append(entry)
                    else:
                        has_default = True
                        top_level.append(entry)
                if not has_default:
                    top_level.append(
                        CommandItem(
                            title=f"/{cmd_name}",
                            path=[cmd_name, ''],
                            help=help_text,
                            handler={},
                        )
                    )
            self._command_items = commands
            self._command_lookup = lookup
            self._command_specs = {item.get('command'): item for item in specs.get('commands', [])}
            self._top_level_commands = sorted(top_level, key=lambda c: c.title.lower())
            for key, entries in sub_map.items():
                entries.sort(key=lambda c: c.title.lower())
            self._subcommand_map = sub_map
            self._refresh_input_suggester()

        def action_open_commands(self) -> None:
            if self.command_hint:
                self.command_hint.display = False
            if not self._command_items:
                if self.status_log:
                    self.status_log.log_status('No commands available.', 'warning')
                return
            palette = CommandPalette(self._command_items)
            self.push_screen(palette, self._on_command_selected)

        def _on_command_selected(self, command: Optional[CommandItem]) -> None:
            if not command:
                return
            self._schedule_task(self._execute_command_item(command))
            if self.input:
                self.input.value = ''
                self.set_focus(self.input)
            self._clear_command_hint()

        async def _execute_command_item(self, command: CommandItem) -> None:
            handler = command.handler or {}
            await self._execute_command_handler(handler, command.path, [])

        async def _execute_command_handler(self, handler: Dict[str, Any], path: List[str], extra_argv: Optional[List[str]]) -> None:
            if not handler:
                if self.status_log:
                    self.status_log.log_status('Invalid command handler.', 'error')
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
                if not ok and self.status_log:
                    self.status_log.log_status(err or 'Command failed.', 'error')
                try:
                    self.session.utils.logger.tui_event('command_builtin', {'path': ' '.join(path or []), 'ok': bool(ok)})
                except Exception:
                    pass
                if not ok:
                    self._display_status_message(err or 'Command failed.', 'error')
                self._refresh_context_panel()
                self._check_auto_submit()
                self._clear_command_hint()
                return

            action_name = handler.get('action') or handler.get('name')
            if not action_name:
                if self.status_log:
                    self.status_log.log_status('Command missing action mapping.', 'error')
                return
            action = self.session.get_action(action_name)
            if not action:
                if self.status_log:
                    self.status_log.log_status(f"Unknown action '{action_name}'.", 'error')
                return

            fixed_args = handler.get('args') or []
            if isinstance(fixed_args, str):
                fixed_args = [fixed_args]
            argv = list(str(a) for a in fixed_args)

            # Intercept bare /clear to behave like Ctrl+L and just clear the view.
            if path and path[0] == 'clear' and (len(path) < 2 or not path[1]):
                if self.chat_view:
                    self.chat_view.clear_messages()
                self._display_status_message('Screen cleared.', 'info')
                if self.status_log:
                    self.status_log.log_status('Screen cleared.', 'info')
                self._clear_command_hint()
                return

            if action_name == 'manage_chats':
                handled = await self._handle_manage_chats_command(action, path, argv, extra_argv)
                if handled:
                    if self.chat_view:
                        self.chat_view.clear_messages()
                        self._render_existing_history()
                    self._refresh_context_panel()
                    self._check_auto_submit()
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
                        if self.status_log:
                            self.status_log.log_status(f"Handler method '{method_name}' not found.", 'error')
                        return
                if self.status_log:
                    self.status_log.log_status('Command executed.', 'info')
                try:
                    self.session.utils.logger.tui_event('command_action', {'path': ' '.join(path or []), 'action': action_name, 'method': method_name, 'argv': argv})
                except Exception:
                    pass
                self._handle_command_result(path, result)
                self._refresh_context_panel()
                self._check_auto_submit()
                self._clear_command_hint()
                return

            result = await self._drive_action(action, argv)
            try:
                self.session.utils.logger.tui_event('command_action', {'path': ' '.join(path or []), 'action': action_name, 'method': None, 'argv': argv})
            except Exception:
                pass
            self._handle_command_result(path, result)
            if path and path[0] in {'reprint', 'load', 'load chat'} and self.chat_view:
                # After reprint or load, rebuild the transcript from chat context
                self.chat_view.clear_messages()
                self._render_existing_history()
            self._refresh_context_panel()
            self._check_auto_submit()
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
                    if self.status_log:
                        self.status_log.log_status('Command completed.', 'info')
                    return result
                except InteractionNeeded as need:
                    response = await self._prompt_for_interaction(need)
                    if response is None:
                        if self.status_log:
                            self.status_log.log_status('Command cancelled.', 'warning')
                        return
                    pending = ('resume', (need.state_token, response))
                except Exception as exc:
                    if self.status_log:
                        self.status_log.log_status(f'Command error: {exc}', 'error')
                    return

        async def _prompt_for_interaction(self, need: InteractionNeeded) -> Any:
            return await self.push_screen_wait(InteractionModal(need.kind, dict(need.spec)))

        def _find_command_suggestions(self, line: str) -> Tuple[List[CommandItem], str]:
            if not line.startswith('/'):
                return [], ''
            if not self._command_specs:
                return [], ''
            raw = line[1:]
            if not raw:
                return self._top_level_commands, ''
            has_space = raw.endswith(' ')
            try:
                import shlex
                tokens = shlex.split(raw, posix=True)
            except Exception:
                tokens = raw.strip().split()
            if not tokens:
                return self._top_level_commands, ''
            first_token = tokens[0]
            first_lower = first_token.lower()
            top_matches = [item for item in self._top_level_commands if item.title.lower().startswith(f'/{first_lower}')]
            if len(tokens) == 1 and not has_space:
                if top_matches:
                    return top_matches, first_token
                return self._top_level_commands, first_token

            command_name = first_token if first_token in self._command_specs else None
            if not command_name:
                candidates = [name for name in self._command_specs if name.startswith(first_token)]
                if len(candidates) == 1:
                    command_name = candidates[0]
            if not command_name:
                highlight = first_token
                suggestions = top_matches or self._top_level_commands
                return suggestions, highlight

            if len(tokens) == 1 and not has_space:
                matches = [item for item in self._top_level_commands if item.path[0] == command_name]
                return matches, first_token

            sub_items = self._subcommand_map.get(command_name, [])
            if not sub_items:
                matches = [item for item in self._top_level_commands if item.path[0] == command_name]
                return matches, first_token

            sub_fragment = ''
            if len(tokens) >= 2:
                sub_fragment = tokens[1]
            highlight = sub_fragment
            if sub_fragment:
                frag_lower = sub_fragment.lower()
                sub_matches = [
                    item for item in sub_items
                    if item.title.lower().startswith(f'/{command_name.lower()} {frag_lower}')
                ]
                if not sub_matches:
                    highlight = ''
                    sub_matches = sub_items
            else:
                sub_matches = sub_items
            return sub_matches, highlight

        def _clear_command_hint(self) -> None:
            if self.command_hint:
                self.command_hint.update_suggestions([], prefix='')
            self._reset_tab_cycle()

        def _update_input_suggestions(self, line: str, items: List[CommandItem]) -> None:
            """Update Input.suggestions if supported by Textual.

            Converts CommandItem suggestions to string suggestions suitable for
            the Textual Input suggestion API. Appends a trailing space to aid
            continued typing after accepting a suggestion.
            """
            try:
                if not hasattr(self.input, 'suggestions'):
                    return
                if not line.startswith('/'):
                    self.input.suggestions = []  # type: ignore[attr-defined]
                    return
                # Build suggestion strings
                suggs: List[str] = []
                for it in items:
                    s = self._format_suggestion(it.title)
                    suggs.append(s)
                # Deduplicate while preserving order
                seen = set()
                uniq = []
                for s in suggs:
                    if s not in seen:
                        seen.add(s)
                        uniq.append(s)
                self.input.suggestions = uniq  # type: ignore[attr-defined]
                # Ensure suggestions appear while typing
                if hasattr(self.input, 'suggest_on'):
                    self.input.suggest_on = 'typing'  # type: ignore[attr-defined]
            except Exception:
                pass

        def _handle_tab_completion(self) -> None:
            if not self.input:
                return
            current = self.input.value or ''
            cycle = self._tab_cycle_state or {}

            suggestions_list: List[str]
            suggestions_items: List[CommandItem] = []

            if cycle.get('applied') == current and cycle.get('suggestions'):
                suggestions_list = list(cycle.get('suggestions') or [])
                if not suggestions_list:
                    return
                index = (int(cycle.get('index', 0)) + 1) % len(suggestions_list)
                chosen = suggestions_list[index]
                cycle.update({'index': index})
            else:
                suggestions_items, _ = self._find_command_suggestions(current)
                if suggestions_items:
                    suggestions_list = [self._format_suggestion(item.title) for item in suggestions_items]
                else:
                    suggestions_list = list(self._all_suggestion_strings)
                if not suggestions_list:
                    return
                chosen = suggestions_list[0]
                cycle = {
                    'prefix': current,
                    'index': 0,
                    'suggestions': suggestions_list,
                }

            self._tab_cycle_state = {**cycle, 'applied': chosen}
            try:
                self._suppress_input_changed_reset = True
                self.input.value = chosen
                self.input.cursor_position = len(chosen)
            except Exception:
                pass
            finally:
                # Ensure focus stays on the input regardless of the keypress
                try:
                    self.set_focus(self.input)
                except Exception:
                    pass

        def _open_compose_modal(self, prefill: str = '') -> None:
            def _on_close(result: Optional[str]) -> None:
                if result is None:
                    return
                self._submit_composed_message(result)

            self.push_screen(ComposeModal(prefill), _on_close)

        def _submit_composed_message(self, text: str) -> None:
            message = text.rstrip()
            if not message:
                return
            if self.input:
                self.input.value = ''
            self._clear_command_hint()
            self._handle_user_input(message)

        async def _wait_for_screen(self, screen) -> Any:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Any] = loop.create_future()

            def _on_close(result: Any) -> None:
                if not future.done():
                    future.set_result(result)

            self.push_screen(screen, _on_close)
            return await future

        def _refresh_input_suggester(self) -> None:
            if not self.input:
                return
            entries: List[str] = []
            for item in self._top_level_commands:
                entries.append(self._format_suggestion(item.title))
            for items in self._subcommand_map.values():
                for entry in items:
                    entries.append(self._format_suggestion(entry.title))
            # Remove duplicates preserving order
            deduped: List[str] = []
            seen = set()
            for value in entries:
                if value not in seen:
                    seen.add(value)
                    deduped.append(value)
            self._all_suggestion_strings = deduped
            try:
                if deduped:
                    self.input.suggester = SuggestFromList(deduped, case_sensitive=False)
                else:
                    self.input.suggester = None
            except Exception:
                pass

        def _format_suggestion(self, title: str) -> str:
            value = title.strip()
            if not value.startswith('/'):
                value = '/' + value
            if not value.endswith(' '):
                value = value + ' '
            return value

        def _reset_tab_cycle(self) -> None:
            self._tab_cycle_state = {'key': '', 'index': 0, 'suggestions': []}

        def _record_status(self, text: str, level: str) -> None:
            try:
                history_limit = getattr(self, '_status_max_lines', 200)
            except Exception:
                history_limit = 200
            self._status_history.append((text, level))
            if len(self._status_history) > history_limit:
                self._status_history = self._status_history[-history_limit:]

        def _display_status_message(self, text: str, level: str) -> None:
            if not self.chat_view:
                return
            if level == 'debug':
                return
            if text and text.count('\n') > 4 and ('User:' in text or 'Assistant:' in text):
                # Avoid dumping full conversation as a single system message
                return
            prefix = {
                'info': 'ⓘ',
                'warning': '⚠',
                'error': '❌',
                'critical': '❌',
            }.get(level, 'ⓘ')
            message = f"{prefix} {text}" if text else prefix
            self.chat_view.add_message('system', message)

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
                self._display_status_message(message, 'error')
                return

            mode = payload.get('mode')
            if mode == 'list' and 'chats' in payload:
                chats = payload.get('chats') or []
                if not chats:
                    self._display_status_message('No chat files found.', 'info')
                else:
                    lines = [f"Chats ({len(chats)}):"]
                    for item in chats[:20]:
                        name = item.get('name') if isinstance(item, dict) else str(item)
                        lines.append(f" • {name}")
                    if len(chats) > 20:
                        lines.append(f"… and {len(chats) - 20} more")
                    self._display_status_message('\n'.join(lines), 'info')
                return

            if mode == 'load' and payload.get('loaded'):
                filename = payload.get('filename') or payload.get('path')
                self._display_status_message(f"Loaded chat from {filename}", 'info')
                if self.chat_view:
                    self.chat_view.clear_messages()
                    self._render_existing_history()
                return

            if mode == 'save' and payload.get('saved'):
                filename = payload.get('filename') or payload.get('path')
                self._display_status_message(f"Saved chat to {filename}", 'info')
                return

            # Generic fallback: surface minimal confirmation
            if payload.get('ok'):
                label = ' '.join(path or []) if path else 'command'
                self._display_status_message(f"/{label} completed.", 'info')

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
                            self._display_status_message('No chat files found.', 'warning')
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
                            self._display_status_message('Load chat cancelled.', 'warning')
                            return True
                        filename = str(selection)
                    result = await asyncio.to_thread(action._handle_headless, {'mode': 'load', 'file': filename})
                    self._handle_command_result(path, result)
                    return True
            except Exception as exc:
                self._display_status_message(f'Manage chats error: {exc}', 'error')
                if self.status_log:
                    self.status_log.log_status(f'Manage chats error: {exc}', 'error')
                return True
            return False

        # ----- chat execution ----------------------------------------
        async def _run_turn(self, message: str) -> None:
            if self.chat_view:
                self._active_message_id = self.chat_view.add_message(
                    'assistant',
                    '',
                    streaming=bool(self._stream_enabled),
                )
            else:
                self._active_message_id = None

            def _run() -> Any:
                options = TurnOptions(stream=self._stream_enabled, suppress_context_print=True)
                return self.turn_runner.run_user_turn(message, options=options)

            try:
                result = await asyncio.to_thread(_run)
            except Exception as exc:
                self.call_from_thread(self._handle_turn_error, str(exc))
                return

            self.call_from_thread(self._finish_turn, self._active_message_id, result)

        def _finish_turn(self, msg_id: Optional[str], result: Any) -> None:
            if msg_id and self.chat_view:
                text = (result.last_text or '').strip() if hasattr(result, 'last_text') else ''
                self.chat_view.update_message(msg_id, text or '(no response)', streaming=False)
            self._active_message_id = None
            if self.status_log:
                self.status_log.log_status('Assistant ready.', 'debug')
            self._refresh_context_panel()
            self._check_auto_submit()

        def _handle_turn_error(self, error_text: str) -> None:
            if self.chat_view and self._active_message_id:
                self.chat_view.update_message(self._active_message_id, f"Error: {error_text}", streaming=False)
            if self.status_log:
                self.status_log.log_status(f"Error: {error_text}", 'error')
            self._active_message_id = None

        def _check_auto_submit(self) -> None:
            try:
                if self.session.get_flag('auto_submit'):
                    self._schedule_task(self._run_turn(''))
            except Exception:
                pass

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
            status = f"Streaming {'enabled' if self._stream_enabled else 'disabled'}"
            if self.status_log:
                self.status_log.log_status(status, 'info')
            bar = self.query_one('#status_bar', Static)
            params = self.session.get_params() or {}
            model_name = params.get('model', 'unknown')
            provider = params.get('provider', 'unknown')
            bar.update(f"iptic-memex TUI · Model: {model_name} · Provider: {provider} · Stream: {'on' if self._stream_enabled else 'off'}")

        def action_refresh_contexts(self) -> None:
            self._refresh_context_panel()
            self._record_status('Contexts refreshed.', 'debug')
            self._display_status_message('Contexts refreshed.', 'debug')
            if self.status_log:
                self.status_log.log_status('Contexts refreshed.', 'debug')

        def action_quit(self) -> None:
            self._cleanup()
            self.exit()

        def action_focus_input(self) -> None:
            if self.input:
                self.set_focus(self.input)

        def action_show_status(self) -> None:
            history = getattr(self, '_status_history', [])
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
                self._display_status_message('Turn cancelled.', 'warning')
                if self.status_log:
                    self.status_log.log_status('Turn cancelled.', 'warning')
            else:
                self._display_status_message('No running turn to cancel.', 'info')

        def on_key(self, event: events.Key) -> None:
            if not self.input:
                return
            focused_input = self.focused is self.input
            if event.key == 'tab' and focused_input:
                event.prevent_default()
                event.stop()
                self._handle_tab_completion()
            elif ('shift+enter' in event.aliases or event.key == 'shift+enter') and focused_input:
                event.prevent_default()
                event.stop()
                self._open_compose_modal(prefill=self.input.value or '')
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

else:  # pragma: no cover - textual missing fallback

    class MemexTUIApp:
        def __init__(self, session, builder=None) -> None:  # noqa: D401
            self.session = session
            self.builder = builder

        def run(self) -> None:  # noqa: D401
            print("Error: Textual library not installed.")
            print("Install with: pip install textual")
