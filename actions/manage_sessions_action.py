from __future__ import annotations

import os
from typing import Any, Dict

from base_classes import Completed, StepwiseAction
from core.session_persistence import (
    apply_session_data,
    format_session_description,
    list_sessions,
    load_session_data,
    resolve_session_path,
    save_session,
)


class ManageSessionsAction(StepwiseAction):
    """List, resume, and checkpoint persistent sessions."""

    def __init__(self, session):
        self.session = session

    def start(self, args=None, content=None) -> Completed:
        """List sessions, load a supplied target, or prompt for a saved session."""
        args = args or []
        if isinstance(args, str):
            args = [args]
        if not args:
            return Completed({'ok': False, 'error': 'missing_mode'})
        mode = str(args[0]).strip().lower()
        if mode == 'list':
            return Completed(self._list_sessions())
        if mode == 'resume':
            target = args[1] if len(args) > 1 else ''
            if not target:
                return self._pick_session()
            return Completed(self._resume_session(target))
        if mode == 'checkpoint':
            title = args[1] if len(args) > 1 else ''
            return Completed(self._checkpoint_session(title))
        return Completed({'ok': False, 'error': 'invalid_mode'})

    def _pick_session(self) -> Completed:
        items = list_sessions(self.session)
        if not items:
            self.session.ui.emit('warning', {'message': 'No saved sessions found.'})
            return Completed({'ok': False, 'error': 'not_found'})
        choices: Dict[str, str] = {}
        for item in items:
            label = format_session_description(item, short_id=True)
            if label in choices:
                label += f"\n  ID: {item['id']}"
            choices[label] = item['path']
        # Store the displayed snapshot: non-blocking UIs can recreate the action on resume.
        self.session.set_user_data('__session_picker_choices__', choices)
        options = list(choices) + ['Cancel']
        selected = self.session.ui.ask_choice(
            'Choose a saved session (newest first):', options, default='Cancel',
        )
        return self.resume('session_picker', selected)

    def resume(self, state_token: str, response: Any) -> Completed:
        """Load the selected session from the snapshot shown in the picker."""
        if isinstance(response, dict):
            response = response.get('response')
        choices = self.session.get_user_data('__session_picker_choices__', {})
        self.session.set_user_data('__session_picker_choices__', {})
        if response is None or response == 'Cancel':
            return Completed({'ok': True, 'cancelled': True})
        path = choices.get(response) if isinstance(response, str) else None
        if not path:
            self.session.ui.emit('warning', {'message': 'Invalid session selection.'})
            return Completed({'ok': False, 'error': 'invalid_selection'})
        return Completed(self._resume_session(path))

    def _list_sessions(self) -> Dict[str, Any]:
        items = list_sessions(self.session)
        try:
            self.session.ui.emit('status', {'message': 'Sessions:'})
            if not items:
                self.session.ui.emit('status', {'message': '(none)'})
            for it in items:
                msg = '- ' + format_session_description(it)
                self.session.ui.emit('status', {'message': msg})
        except Exception:
            pass
        return {'ok': True, 'sessions': items}

    def _resume_session(self, target: str) -> Dict[str, Any]:
        if not target:
            return {'ok': False, 'error': 'missing_target'}
        path = resolve_session_path(self.session, target)
        if not path or not os.path.isfile(path):
            self.session.ui.emit('warning', {'message': 'Saved session not found.'})
            return {'ok': False, 'error': 'not_found'}
        try:
            data = load_session_data(path)
            if not isinstance(data, dict):
                raise ValueError('Invalid session data')
        except (OSError, ValueError):
            self.session.ui.emit('error', {'message': 'Failed to load session data.'})
            return {'ok': False, 'error': 'invalid_data'}
        kind = (data.get('kind') or 'session').lower()
        fork = (kind == 'checkpoint')
        apply_session_data(self.session, data, fork=fork)
        try:
            msg = f"Resumed session from {path}"
            if fork:
                msg += " (forked from checkpoint)"
            self.session.ui.emit('status', {'message': msg})
        except Exception:
            pass
        return {'ok': True, 'path': path, 'forked': fork}

    def _checkpoint_session(self, title: str) -> Dict[str, Any]:
        has_user_turn = False
        try:
            chat = self.session.get_context('chat')
            turns = chat.get('all') if chat else []
        except Exception:
            turns = []
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            if turn.get('role') != 'user':
                continue
            msg = turn.get('message')
            if isinstance(msg, str) and msg.strip():
                has_user_turn = True
                break
            ctx = turn.get('context')
            if ctx:
                has_user_turn = True
                break
        if not has_user_turn:
            try:
                self.session.ui.emit('warning', {'message': 'No user messages to save yet.'})
            except Exception:
                pass
            return {'ok': False, 'error': 'empty_session'}

        path = save_session(self.session, kind='checkpoint', title=title or None)
        try:
            from core.session_persistence import prune_sessions
            prune_sessions(self.session, kind='checkpoint')
        except Exception:
            pass
        try:
            self.session.ui.emit('status', {'message': f"Checkpoint saved to {path}"})
        except Exception:
            pass
        return {'ok': True, 'path': path}
